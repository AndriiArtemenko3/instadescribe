import { InstaDescribeError } from "./errors.js";

const API_PREFIX = "/v1/";
const retryableCodes = new Set([
  "authentication_unavailable",
  "capacity_conflict",
  "source_not_visible",
  "storage_unavailable",
  "enqueue_unavailable",
  "persistence_unavailable",
  "deliverables_unavailable",
  "service_unavailable",
  "upload_service_unavailable",
  "idempotency_in_progress",
]);

function isLoopback(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

export function normalizeOrigin(input: string | URL, label: string): URL {
  let url: URL;
  try {
    url = new URL(input.toString());
  } catch {
    throw new InstaDescribeError(`${label} must be an absolute URL`, {
      kind: "validation",
      code: `invalid_${label}`,
    });
  }
  const safeProtocol = url.protocol === "https:" || (url.protocol === "http:" && isLoopback(url.hostname));
  if (
    !safeProtocol || url.username !== "" || url.password !== ""
    || (url.pathname !== "" && url.pathname !== "/") || url.search !== "" || url.hash !== ""
  ) {
    throw new InstaDescribeError(
      `${label} must be an HTTPS origin (HTTP is allowed only for loopback development)`,
      { kind: "validation", code: `invalid_${label}` },
    );
  }
  url.pathname = "/";
  return url;
}

interface RequestOptions {
  body?: unknown;
  signal?: AbortSignal | undefined;
  acceptedStatuses?: readonly number[];
  idempotencyKey?: string | undefined;
  redirect?: RequestRedirect;
}

interface SafeApiProblem {
  code?: string;
  detail?: string;
  requestId?: string;
  retryable?: boolean;
}

async function safeApiProblem(response: Response): Promise<SafeApiProblem> {
  try {
    const value: unknown = await response.json();
    if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
    const entry = value as Record<string, unknown>;
    // Public integrations use RFC 9457. The nested fallback keeps error
    // handling safe during a rolling deployment without exposing raw bodies.
    const nested = typeof entry.detail === "object" && entry.detail !== null && !Array.isArray(entry.detail)
      ? entry.detail as Record<string, unknown>
      : undefined;
    const detail = typeof entry.detail === "string" ? entry.detail : nested?.message;
    const code = entry.code ?? nested?.code;
    return {
      ...(typeof code === "string" ? { code: code.slice(0, 80) } : {}),
      ...(typeof detail === "string" ? { detail: detail.slice(0, 300) } : {}),
      ...(typeof entry.requestId === "string" ? { requestId: entry.requestId.slice(0, 100) } : {}),
      ...(typeof entry.retryable === "boolean" ? { retryable: entry.retryable } : {}),
    };
  } catch {
    return {};
  }
}

function defaultMessage(status: number): string {
  if (status === 401) return "Authentication failed";
  if (status === 403) return "The API key does not grant the required scope";
  if (status === 404) return "Resource not found";
  if (status === 409) return "Request conflicts with the current resource state";
  if (status === 400 || status === 422) return "Request validation failed";
  if (status === 425 || status === 429) return "InstaDescribe is at capacity";
  if (status >= 500) return "InstaDescribe is temporarily unavailable";
  return `InstaDescribe request failed with HTTP ${status}`;
}

async function responseError(response: Response): Promise<InstaDescribeError> {
  const problem = await safeApiProblem(response);
  const code = problem.code ?? `http_${response.status}`;
  let kind: InstaDescribeError["kind"] = "service";
  if (response.status === 401 || response.status === 403) kind = "auth";
  else if (response.status === 400 || response.status === 422) kind = "validation";
  else if (response.status === 404) kind = "not_found";
  else if ((response.status === 409 || response.status === 425) && code === "capacity_conflict") kind = "capacity";
  else if (response.status === 409 || response.status === 425) kind = "conflict";
  else if (response.status === 429) kind = "capacity";
  return new InstaDescribeError(problem.detail ?? defaultMessage(response.status), {
    kind,
    code,
    status: response.status,
    retryable: problem.retryable ?? (response.status >= 500 || response.status === 429 || retryableCodes.has(code)),
    requestId: problem.requestId,
  });
}

export class ApiTransport {
  readonly #origin: URL;
  readonly #apiKey: string;
  readonly #fetch: typeof globalThis.fetch;
  readonly #timeoutMs: number;

  constructor(origin: URL, apiKey: string, fetchImpl: typeof globalThis.fetch, timeoutMs: number) {
    this.#origin = origin;
    this.#apiKey = apiKey;
    this.#fetch = fetchImpl;
    this.#timeoutMs = timeoutMs;
  }

  async #request(method: string, path: string, options: RequestOptions): Promise<Response> {
    if (!path.startsWith(API_PREFIX) || path.includes("\\") || path.includes("#")) {
      throw new InstaDescribeError("Refused a non-Integration-API path", {
        kind: "validation",
        code: "unsafe_api_path",
      });
    }
    const url = new URL(path, this.#origin);
    if (url.origin !== this.#origin.origin) {
      throw new InstaDescribeError("Refused a cross-origin API request", {
        kind: "validation",
        code: "unsafe_api_origin",
      });
    }
    const timeoutSignal = AbortSignal.timeout(this.#timeoutMs);
    const signal = options.signal ? AbortSignal.any([options.signal, timeoutSignal]) : timeoutSignal;
    const headers = new Headers({
      Accept: "application/json, application/problem+json",
      Authorization: `Bearer ${this.#apiKey}`,
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (options.idempotencyKey !== undefined) headers.set("Idempotency-Key", options.idempotencyKey);
    let response: Response;
    try {
      response = await this.#fetch(url, {
        method,
        headers,
        ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
        credentials: "omit",
        redirect: options.redirect ?? "error",
        signal,
      });
    } catch (cause) {
      if (options.signal?.aborted) {
        throw new InstaDescribeError("Request aborted", { kind: "aborted", code: "aborted", cause });
      }
      if (timeoutSignal.aborted) {
        throw new InstaDescribeError("Request timed out", {
          kind: "timeout", code: "request_timeout", retryable: true, cause,
        });
      }
      throw new InstaDescribeError("Could not reach InstaDescribe", {
        kind: "network", code: "network_error", retryable: true, cause,
      });
    }
    const accepted = options.acceptedStatuses ?? [200];
    if (!accepted.includes(response.status)) throw await responseError(response);
    return response;
  }

  async json(method: string, path: string, options: RequestOptions = {}): Promise<unknown> {
    const response = await this.#request(method, path, options);
    if (response.status === 204) return null;
    try {
      return await response.json();
    } catch (cause) {
      throw new InstaDescribeError("InstaDescribe returned invalid JSON", {
        kind: "contract", code: "invalid_json", cause,
      });
    }
  }

  async redirectLocation(path: string, signal?: AbortSignal): Promise<string> {
    const response = await this.#request("GET", path, {
      signal,
      acceptedStatuses: [303],
      redirect: "manual",
    });
    const location = response.headers.get("Location");
    if (!location) {
      throw new InstaDescribeError("Deliverable response omitted the signed download location", {
        kind: "contract", code: "missing_download_location",
      });
    }
    return location;
  }
}
