import { openAsBlob } from "node:fs";
import { basename } from "node:path";
import { InstaDescribeError } from "./errors.js";
import type { PresignedUpload } from "./types.js";

export interface UploadFileOptions {
  fileName?: string;
  contentType?: string;
  signal?: AbortSignal | undefined;
}

function signedUploadUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch (cause) {
    throw new InstaDescribeError("Upload contract contains an invalid URL", {
      kind: "contract",
      code: "invalid_upload_url",
      cause,
    });
  }
  const loopback = url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) || url.username || url.password) {
    throw new InstaDescribeError("Upload contract contains an unsafe URL", {
      kind: "contract",
      code: "unsafe_upload_url",
    });
  }
  return url;
}

/** Upload a file directly to the signed storage endpoint; the API key is never attached. */
export async function uploadFile(
  fetchImpl: typeof globalThis.fetch,
  contract: PresignedUpload,
  filePath: string,
  options: UploadFileOptions = {},
): Promise<void> {
  if (contract.method !== "POST") {
    throw new InstaDescribeError("Upload contract requires an unsupported method", {
      kind: "contract",
      code: "unsupported_upload_method",
    });
  }
  let blob: Blob;
  try {
    blob = await openAsBlob(filePath, options.contentType ? { type: options.contentType } : undefined);
  } catch (cause) {
    throw new InstaDescribeError("Could not open the source file", {
      kind: "filesystem",
      code: "source_file_error",
      cause,
    });
  }
  const form = new FormData();
  for (const [key, value] of Object.entries(contract.fields)) form.append(key, value);
  form.append("file", blob, options.fileName ?? basename(filePath));
  let response: Response;
  try {
    response = await fetchImpl(signedUploadUrl(contract.url), {
      method: "POST",
      body: form,
      headers: { Accept: "application/json, text/plain, */*" },
      credentials: "omit",
      redirect: "error",
      ...(options.signal ? { signal: options.signal } : {}),
    });
  } catch (cause) {
    throw new InstaDescribeError("Source upload failed", {
      kind: options.signal?.aborted ? "aborted" : "network",
      code: options.signal?.aborted ? "aborted" : "upload_network_error",
      retryable: !options.signal?.aborted,
      cause,
    });
  }
  if (!response.ok) {
    throw new InstaDescribeError(`Source upload failed with HTTP ${response.status}`, {
      kind: response.status >= 500 ? "service" : "conflict",
      code: "upload_http_error",
      status: response.status,
      retryable: response.status >= 500,
    });
  }
}
