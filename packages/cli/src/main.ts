import { mkdir, stat } from "node:fs/promises";
import { basename, extname, join, resolve } from "node:path";
import type {
  CreateJobInput,
  CreateJobResult,
  Deliverable,
  Deliverables,
  DownloadDeliverableResult,
  JobSummary,
  Page,
  PresignedUpload,
  PublicJobState,
} from "@instadescribe/sdk";
import {
  booleanOption,
  extractGlobals,
  numberOption,
  onePositional,
  parseOptions,
  stringOption,
  UsageError,
  type GlobalOptions,
} from "./args.js";
import {
  configPaths,
  credentialsPresent,
  readPublicConfig,
  removeCredentials,
  resolveRuntime,
  writeCredentials,
  writePublicConfig,
  type CliEnvironment,
  type ConfigPaths,
} from "./config.js";
import { Reporter, type WritableText } from "./output.js";

export const VERSION = "1.0.0-beta.1";

export const ExitCode = {
  success: 0,
  unexpected: 1,
  usage: 2,
  auth: 3,
  notFound: 4,
  conflict: 5,
  timeout: 6,
  service: 7,
  integrity: 8,
  job: 9,
  unsupported: 10,
  aborted: 130,
} as const;

interface ClientLike {
  capabilities: { get(): Promise<unknown> };
  jobs: {
    create(input: CreateJobInput): Promise<CreateJobResult>;
    list(options?: { limit?: number; cursor?: string; projectId?: string }): Promise<Page<JobSummary>>;
    get(jobId: string): Promise<JobSummary>;
    completeUpload(jobId: string): Promise<JobSummary>;
    wait(jobId: string, options?: {
      until?: PublicJobState | readonly PublicJobState[];
      timeoutMs?: number;
      pollIntervalMs?: number;
      onProgress?: (job: JobSummary) => void | Promise<void>;
    }): Promise<JobSummary>;
    cancel(jobId: string): Promise<JobSummary>;
  };
  deliverables: {
    list(jobId: string): Promise<Deliverables>;
    download(jobId: string, selector: string | Deliverable, destination: string, options?: { overwrite?: boolean }): Promise<DownloadDeliverableResult>;
  };
  uploads: {
    uploadFile(contract: PresignedUpload, filePath: string, options?: { fileName?: string; contentType?: string }): Promise<void>;
  };
  reviewUrl(job: JobSummary): URL;
}

interface SdkModule {
  InstaDescribe: new (options: { baseUrl: string; appUrl: string; apiKey: string }) => ClientLike;
}

export interface CliDependencies {
  environment?: CliEnvironment | undefined;
  stdout?: WritableText | undefined;
  stderr?: WritableText | undefined;
  readStdin?: (() => Promise<string>) | undefined;
  openUrl?: ((url: URL) => Promise<void>) | undefined;
  createClient?: ((options: { baseUrl: string; appUrl: string; apiKey: string }) => ClientLike | Promise<ClientLike>) | undefined;
  paths?: ConfigPaths | undefined;
}

const mediaTypes: Readonly<Record<string, string>> = {
  ".mp4": "video/mp4",
  ".mov": "video/quicktime",
  ".webm": "video/webm",
};
const HELP = `instadescribe ${VERSION}

Usage:
  instadescribe auth login --key-stdin [--api-url URL]
  instadescribe auth status | logout
  instadescribe config set <api-url|app-url> <URL>
  instadescribe config get <api-url|app-url>
  instadescribe config unset <api-url|app-url>
  instadescribe config list
  instadescribe create <video> --project NAME [--transcript FILE] [--external-id ID] [--wait]
  instadescribe list [--limit N]
  instadescribe status <job-id>
  instadescribe wait <job-id> [--until needs_review|completed]
  instadescribe cancel <job-id>
  instadescribe confirm-upload <job-id>
  instadescribe review <job-id> [--wait] [--open]
  instadescribe download <job-id> [--output-dir DIR] [--overwrite]

Advanced aliases:
  instadescribe jobs create|list|get|wait|cancel|complete-upload ...
  instadescribe deliverables list <job-id>
  instadescribe deliverables download <job-id> <id-or-kind> --destination PATH [--overwrite]

Global options (accepted anywhere):
  --api-url URL    Override INSTADESCRIBE_API_URL/config
  --app-url URL    Override INSTADESCRIBE_APP_URL/config
  --output MODE    human, json or jsonl
  --json           Alias for --output json
  --ndjson         Alias for --output jsonl
  --quiet          Suppress human success/progress output

Secrets are read from INSTADESCRIBE_API_KEY or stdin, never a command-line flag.`;

async function defaultReadStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  return Buffer.concat(chunks).toString("utf8");
}

async function defaultOpenUrl(url: URL): Promise<void> {
  const { spawn } = await import("node:child_process");
  const command = process.platform === "darwin" ? "open" : process.platform === "win32" ? "rundll32" : "xdg-open";
  const args = process.platform === "win32" ? ["url.dll,FileProtocolHandler", url.href] : [url.href];
  await new Promise<void>((resolveOpen, rejectOpen) => {
    const child = spawn(command, args, { detached: true, stdio: "ignore" });
    child.once("spawn", () => {
      child.unref();
      resolveOpen();
    });
    child.once("error", rejectOpen);
  });
}

function durationOptionMs(raw: string | undefined, name: string): number | undefined {
  if (raw === undefined) return undefined;
  const match = /^(\d+(?:\.\d+)?)(ms|s|m|h)?$/.exec(raw);
  if (match === null) throw new UsageError(`--${name} must be a positive duration such as 3s or 30m`);
  const value = Number(match[1]);
  const multiplier = match[2] === "ms" ? 1 : match[2] === "m" ? 60_000 : match[2] === "h" ? 3_600_000 : 1_000;
  if (!Number.isFinite(value) || value <= 0) throw new UsageError(`--${name} must be greater than zero`);
  return value * multiplier;
}

async function clientFor(
  globals: GlobalOptions,
  environment: CliEnvironment,
  paths: ConfigPaths,
  dependencies: CliDependencies,
): Promise<{ client: ClientLike; runtime: Awaited<ReturnType<typeof resolveRuntime>> }> {
  const runtime = await resolveRuntime(environment, paths, { apiUrl: globals.apiUrl, appUrl: globals.appUrl });
  const options = { baseUrl: runtime.apiUrl, appUrl: runtime.appUrl, apiKey: runtime.apiKey };
  if (dependencies.createClient) return { client: await dependencies.createClient(options), runtime };
  const sdk = await import("@instadescribe/sdk") as SdkModule;
  return { client: new sdk.InstaDescribe(options), runtime };
}

function validateConfigOrigin(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new UsageError("URL must be an absolute HTTPS origin");
  }
  const loopback = url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  if (
    (url.protocol !== "https:" && !(url.protocol === "http:" && loopback))
    || url.username || url.password || (url.pathname !== "" && url.pathname !== "/") || url.search || url.hash
  ) {
    throw new UsageError("URL must be an HTTPS origin (HTTP is allowed only for loopback development)");
  }
  url.pathname = "/";
  return url.origin;
}

function requireNoOptions(positionals: readonly string[], usage: string): void {
  if (positionals.length !== 0) throw new UsageError(`Usage: ${usage}`);
}

async function commandAuth(
  args: readonly string[],
  globals: GlobalOptions,
  environment: CliEnvironment,
  paths: ConfigPaths,
  dependencies: CliDependencies,
  reporter: Reporter,
): Promise<void> {
  const action = args[0];
  if (action === "login") {
    const options = parseOptions(args.slice(1), { "key-stdin": "boolean", "api-key-stdin": "boolean" });
    requireNoOptions(options.positionals, "instadescribe auth login --key-stdin");
    if (booleanOption(options, "key-stdin") && booleanOption(options, "api-key-stdin")) {
      throw new UsageError("Use only --key-stdin");
    }
    let apiKey: string | undefined;
    if (booleanOption(options, "key-stdin") || booleanOption(options, "api-key-stdin")) {
      apiKey = (await (dependencies.readStdin ?? defaultReadStdin)()).trim();
    }
    else apiKey = environment.INSTADESCRIBE_API_KEY;
    if (!apiKey) throw new UsageError("Use --key-stdin or set INSTADESCRIBE_API_KEY");
    const publicConfig = await readPublicConfig(paths);
    const apiUrl = globals.apiUrl ?? environment.INSTADESCRIBE_API_URL ?? publicConfig.apiUrl;
    if (!apiUrl) throw new UsageError("API URL is not configured; pass --api-url or use config set api-url");
    const appUrl = globals.appUrl ?? environment.INSTADESCRIBE_APP_URL ?? publicConfig.appUrl ?? apiUrl;
    const client = dependencies.createClient
      ? await dependencies.createClient({ baseUrl: apiUrl, appUrl, apiKey })
      : new (await import("@instadescribe/sdk") as SdkModule).InstaDescribe({ baseUrl: apiUrl, appUrl, apiKey });
    await client.capabilities.get();
    await writeCredentials(paths, apiKey);
    reporter.result({ authenticated: true, stored: true }, "Authentication verified; credentials stored securely.");
    return;
  }
  if (action === "status") {
    const options = parseOptions(args.slice(1), {});
    requireNoOptions(options.positionals, "instadescribe auth status");
    const fromEnvironment = Boolean(environment.INSTADESCRIBE_API_KEY);
    const stored = await credentialsPresent(paths);
    reporter.result(
      { authenticated: fromEnvironment || stored, source: fromEnvironment ? "environment" : stored ? "credentials" : null },
      fromEnvironment ? "API key configured from environment." : stored ? "Stored credentials are present." : "No API key configured.",
    );
    return;
  }
  if (action === "logout") {
    const options = parseOptions(args.slice(1), {});
    requireNoOptions(options.positionals, "instadescribe auth logout");
    const removed = await removeCredentials(paths);
    reporter.result({ authenticated: Boolean(environment.INSTADESCRIBE_API_KEY), removed },
      environment.INSTADESCRIBE_API_KEY
        ? "Stored credentials removed; INSTADESCRIBE_API_KEY remains active."
        : removed ? "Stored credentials removed." : "No stored credentials were present.");
    return;
  }
  throw new UsageError("Usage: instadescribe auth <login|status|logout>");
}

async function commandConfig(
  args: readonly string[],
  environment: CliEnvironment,
  paths: ConfigPaths,
  reporter: Reporter,
): Promise<void> {
  const action = args[0];
  const config = await readPublicConfig(paths);
  if (action === "show" || action === "list") {
    if (args.length !== 1) throw new UsageError("Usage: instadescribe config list");
    reporter.result({
      apiUrl: environment.INSTADESCRIBE_API_URL ?? config.apiUrl ?? null,
      appUrl: environment.INSTADESCRIBE_APP_URL ?? config.appUrl ?? null,
      apiKeyConfigured: Boolean(environment.INSTADESCRIBE_API_KEY) || await credentialsPresent(paths),
    });
    return;
  }
  if (action === "get") {
    if (args.length !== 2) throw new UsageError("Usage: instadescribe config get <api-url|app-url>");
    const key = args[1];
    if (key !== "api-url" && key !== "app-url") throw new UsageError("Configuration key must be api-url or app-url");
    const value = key === "api-url"
      ? environment.INSTADESCRIBE_API_URL ?? config.apiUrl ?? null
      : environment.INSTADESCRIBE_APP_URL ?? config.appUrl ?? null;
    reporter.result({ key, value }, value ?? "Not configured");
    return;
  }
  if (action === "set") {
    if (args.length !== 3) throw new UsageError("Usage: instadescribe config set <api-url|app-url> <URL>");
    const key = args[1];
    if (key !== "api-url" && key !== "app-url") throw new UsageError("Configuration key must be api-url or app-url");
    const value = validateConfigOrigin(args[2]!);
    if (key === "api-url") config.apiUrl = value;
    else config.appUrl = value;
    await writePublicConfig(paths, config);
    reporter.result({ key, value }, `${key} set to ${value}`);
    return;
  }
  if (action === "unset") {
    if (args.length !== 2) throw new UsageError("Usage: instadescribe config unset <api-url|app-url>");
    const key = args[1];
    if (key === "api-url") delete config.apiUrl;
    else if (key === "app-url") delete config.appUrl;
    else throw new UsageError("Configuration key must be api-url or app-url");
    await writePublicConfig(paths, config);
    reporter.result({ key, removed: true }, `${key} removed.`);
    return;
  }
  throw new UsageError("Usage: instadescribe config <set|get|unset|list>");
}

function jobHuman(job: JobSummary): string {
  return `${job.jobId}  ${job.state}  ${job.progress}%  project=${job.projectId}`;
}

async function commandJobs(
  args: readonly string[],
  globals: GlobalOptions,
  environment: CliEnvironment,
  paths: ConfigPaths,
  dependencies: CliDependencies,
  reporter: Reporter,
): Promise<void> {
  const action = args[0];
  const { client } = await clientFor(globals, environment, paths, dependencies);
  if (action === "create") {
    const options = parseOptions(args.slice(1), {
      project: "string", "project-id": "string", "project-name": "string", "external-id": "string", "client-reference": "string",
      transcript: "string", duration: "string", "content-type": "string", wait: "boolean",
      preset: "string", style: "string", detail: "string", language: "string", instructions: "string", voice: "string",
    });
    const filePath = resolve(onePositional(options, "instadescribe create <video> <--project NAME|--project-id ID>"));
    const projectId = stringOption(options, "project-id");
    const projectAlias = stringOption(options, "project");
    const legacyProjectName = stringOption(options, "project-name");
    if (projectAlias !== undefined && legacyProjectName !== undefined) {
      throw new UsageError("Use only --project (the --project-name alias is deprecated)");
    }
    const projectName = projectAlias ?? legacyProjectName;
    if (Boolean(projectId) === Boolean(projectName)) throw new UsageError("Provide exactly one of --project-id or --project");
    const externalId = stringOption(options, "external-id");
    const project: CreateJobInput["project"] = projectId
      ? { id: projectId, ...(externalId === undefined ? {} : { externalId }) }
      : { name: projectName!, ...(externalId === undefined ? {} : { externalId }) };
    const durationSeconds = numberOption(options, "duration");
    if (durationSeconds !== undefined && durationSeconds <= 0) throw new UsageError("--duration must be greater than zero");
    let videoInfo;
    try {
      videoInfo = await stat(filePath);
    } catch {
      throw new UsageError("Source file does not exist or is not readable");
    }
    if (!videoInfo.isFile() || videoInfo.size < 1) throw new UsageError("Source must be a non-empty regular file");
    const extension = extname(filePath).toLowerCase();
    const contentType = stringOption(options, "content-type") ?? mediaTypes[extension];
    if (!contentType) throw new UsageError("Cannot infer content type; supported extensions are .mp4, .mov and .webm");
    const preset = stringOption(options, "preset") ?? "standard";
    if (preset !== "economy" && preset !== "standard") throw new UsageError("--preset must be economy or standard");
    const detail = numberOption(options, "detail") ?? 3;
    if (!Number.isInteger(detail) || detail < 1 || detail > 5) throw new UsageError("--detail must be an integer from 1 to 5");
    const language = stringOption(options, "language");
    const instructions = stringOption(options, "instructions");
    const voice = stringOption(options, "voice");
    const transcriptPathRaw = stringOption(options, "transcript");
    let transcriptPath: string | undefined;
    let transcript: CreateJobInput["transcript"];
    if (transcriptPathRaw !== undefined) {
      transcriptPath = resolve(transcriptPathRaw);
      let transcriptInfo;
      try {
        transcriptInfo = await stat(transcriptPath);
      } catch {
        throw new UsageError("Transcript file does not exist or is not readable");
      }
      if (!transcriptInfo.isFile() || transcriptInfo.size < 1) throw new UsageError("Transcript must be a non-empty regular file");
      const transcriptExtension = extname(transcriptPath).toLowerCase();
      if (transcriptExtension !== ".vtt" && transcriptExtension !== ".srt") throw new UsageError("Transcript must be .vtt or .srt");
      transcript = {
        fileName: basename(transcriptPath),
        format: transcriptExtension === ".vtt" ? "vtt" : "srt",
        contentType: transcriptExtension === ".vtt" ? "text/vtt" : "application/x-subrip",
        sizeBytes: transcriptInfo.size,
      };
    }
    const input: CreateJobInput = {
      project,
      ...(stringOption(options, "client-reference") === undefined ? {} : { clientReference: stringOption(options, "client-reference")! }),
      video: {
        fileName: basename(filePath),
        contentType,
        sizeBytes: videoInfo.size,
        ...(durationSeconds === undefined ? {} : { durationSeconds }),
      },
      ...(transcript === undefined ? {} : { transcript }),
      settings: {
        preset,
        style: stringOption(options, "style") ?? "documentary",
        detail,
        ...(language === undefined ? {} : { language }),
        ...(instructions === undefined ? {} : { instructions }),
        ...(voice === undefined ? {} : { voice }),
      },
    };
    const created = await client.jobs.create(input);
    const jobId = created.job.jobId;
    reporter.event({ type: "job.created", jobId, projectId: created.job.projectId }, `Reserved job ${jobId}`);
    try {
      await client.uploads.uploadFile(created.uploads.video, filePath, { fileName: input.video.fileName, contentType });
      if (transcriptPath !== undefined) {
        if (created.uploads.transcript === undefined) {
          throw new CommandError(`Transcript upload contract missing for reserved job ${jobId}`, "missing_transcript_upload", { jobId });
        }
        await client.uploads.uploadFile(created.uploads.transcript, transcriptPath, {
          fileName: input.transcript!.fileName,
          contentType: input.transcript!.contentType,
        });
      }
    } catch (cause) {
      if (cause instanceof CommandError) throw cause;
      throw new CommandError(`Source upload failed for reserved job ${jobId}`, "upload_failed", { jobId }, cause);
    }
    reporter.event({ type: "upload.completed", jobId }, "Source uploads completed; asking InstaDescribe to verify them.");
    const accepted = await client.jobs.completeUpload(jobId);
    let finalJob: JobSummary | undefined;
    if (booleanOption(options, "wait")) {
      finalJob = await client.jobs.wait(jobId, {
        onProgress: (job) => reporter.event(
          { type: "job.progress", jobId: job.jobId, state: job.state, progress: job.progress, stage: job.stage },
          `${job.state} ${job.progress}%${job.stage ? ` — ${job.stage}` : ""}`,
        ),
      });
    }
    const resultingJob = finalJob ?? accepted;
    const reviewUrl = resultingJob.reviewUrl === null ? null : client.reviewUrl(resultingJob).href;
    const result = {
      projectId: resultingJob.projectId,
      jobId: resultingJob.jobId,
      state: resultingJob.state,
      reviewUrl,
    };
    reporter.result(
      result,
      reviewUrl === null
        ? `${jobId} accepted. Run instadescribe review ${jobId} when it reaches needs_review.`
        : `${jobId} accepted. Review: ${reviewUrl}`,
    );
    return;
  }
  if (action === "list") {
    const options = parseOptions(args.slice(1), { limit: "string", cursor: "string", "project-id": "string" });
    requireNoOptions(options.positionals, "instadescribe jobs list [--limit N]");
    const limit = numberOption(options, "limit");
    if (limit !== undefined && !Number.isInteger(limit)) throw new UsageError("--limit must be an integer");
    const page = await client.jobs.list({
      ...(limit === undefined ? {} : { limit }),
      ...(stringOption(options, "cursor") === undefined ? {} : { cursor: stringOption(options, "cursor")! }),
      ...(stringOption(options, "project-id") === undefined ? {} : { projectId: stringOption(options, "project-id")! }),
    });
    reporter.result(page, page.items.length === 0 ? "No jobs." : page.items.map(jobHuman).join("\n"));
    return;
  }
  if (action === "get") {
    const options = parseOptions(args.slice(1), {});
    const jobId = onePositional(options, "instadescribe jobs get <job-id>");
    const job = await client.jobs.get(jobId);
    reporter.result(job, jobHuman(job));
    return;
  }
  if (action === "wait") {
    const options = parseOptions(args.slice(1), { timeout: "string", "poll-interval": "string", until: "string" });
    const jobId = onePositional(options, "instadescribe wait <job-id> [--until needs_review|completed]");
    const timeout = durationOptionMs(stringOption(options, "timeout"), "timeout");
    const pollInterval = durationOptionMs(stringOption(options, "poll-interval"), "poll-interval");
    const until = stringOption(options, "until");
    if (until !== undefined && until !== "needs_review" && until !== "completed") {
      throw new UsageError("--until must be needs_review or completed");
    }
    const job = await client.jobs.wait(jobId, {
      ...(until === undefined ? {} : { until }),
      ...(timeout === undefined ? {} : { timeoutMs: timeout }),
      ...(pollInterval === undefined ? {} : { pollIntervalMs: pollInterval }),
      onProgress: (current) => reporter.event(
        { type: "job.progress", jobId: current.jobId, state: current.state, progress: current.progress, stage: current.stage },
        `${current.state} ${current.progress}%${current.stage ? ` — ${current.stage}` : ""}`,
      ),
    });
    const reviewUrl = job.reviewUrl === null ? null : client.reviewUrl(job).href;
    reporter.result(
      { job, reviewUrl },
      reviewUrl === null ? jobHuman(job) : `${jobHuman(job)}\nReview: ${reviewUrl}`,
    );
    return;
  }
  if (action === "cancel") {
    const options = parseOptions(args.slice(1), {});
    const jobId = onePositional(options, "instadescribe jobs cancel <job-id>");
    const job = await client.jobs.cancel(jobId);
    reporter.result(job, jobHuman(job));
    return;
  }
  if (action === "complete-upload") {
    const options = parseOptions(args.slice(1), {});
    const jobId = onePositional(options, "instadescribe jobs complete-upload <job-id>");
    const result = await client.jobs.completeUpload(jobId);
    reporter.result(result, jobHuman(result));
    return;
  }
  throw new UsageError("Usage: instadescribe jobs <create|list|get|wait|cancel|complete-upload>");
}

async function commandReview(
  args: readonly string[], globals: GlobalOptions, environment: CliEnvironment, paths: ConfigPaths,
  dependencies: CliDependencies, reporter: Reporter,
): Promise<void> {
  const options = parseOptions(args, { wait: "boolean", open: "boolean" });
  const jobId = onePositional(options, "instadescribe review <job-id> [--wait] [--open]");
  const { client } = await clientFor(globals, environment, paths, dependencies);
  const job = booleanOption(options, "wait")
    ? await client.jobs.wait(jobId, {
      until: ["needs_review", "completed"],
      onProgress: (current) => reporter.event(
        { type: "job.progress", jobId: current.jobId, state: current.state, progress: current.progress, stage: current.stage },
        `${current.state} ${current.progress}%${current.stage ? ` — ${current.stage}` : ""}`,
      ),
    })
    : await client.jobs.get(jobId);
  const url = client.reviewUrl(job).href;
  if (booleanOption(options, "open")) await (dependencies.openUrl ?? defaultOpenUrl)(new URL(url));
  reporter.result({ jobId: job.jobId, projectId: job.projectId, reviewUrl: url, opened: booleanOption(options, "open") }, url);
}

async function commandDownload(
  args: readonly string[], globals: GlobalOptions, environment: CliEnvironment, paths: ConfigPaths,
  dependencies: CliDependencies, reporter: Reporter,
): Promise<void> {
  const options = parseOptions(args, { "output-dir": "string", overwrite: "boolean" });
  const jobId = onePositional(options, "instadescribe download <job-id> [--output-dir DIR] [--overwrite]");
  const directory = resolve(stringOption(options, "output-dir") ?? `instadescribe-${jobId}`);
  const { client } = await clientFor(globals, environment, paths, dependencies);
  const deliverables = await client.deliverables.list(jobId);
  await mkdir(directory, { recursive: true });
  const downloaded: DownloadDeliverableResult[] = [];
  for (const item of deliverables.items) {
    const fileName = basename(item.fileName);
    if (fileName !== item.fileName || fileName === "." || fileName === ".." || fileName.length === 0) {
      throw new CommandError("Server returned an unsafe deliverable file name", "invalid_deliverable_filename", {
        jobId,
        deliverableId: item.deliverableId,
      });
    }
    reporter.event(
      { type: "artifact.progress", jobId, deliverableId: item.deliverableId, state: "downloading" },
      `Downloading ${fileName}`,
    );
    downloaded.push(await client.deliverables.download(jobId, item, join(directory, fileName), {
      overwrite: booleanOption(options, "overwrite"),
    }));
  }
  reporter.result(
    { jobId, directory, items: downloaded },
    downloaded.length === 0 ? "No deliverables." : `Downloaded ${downloaded.length} deliverables to ${directory}`,
  );
}

async function commandDeliverables(
  args: readonly string[], globals: GlobalOptions, environment: CliEnvironment, paths: ConfigPaths,
  dependencies: CliDependencies, reporter: Reporter,
): Promise<void> {
  if (args[0] === "list") {
    const options = parseOptions(args.slice(1), {});
    const jobId = onePositional(options, "instadescribe deliverables list <job-id>");
    const { client } = await clientFor(globals, environment, paths, dependencies);
    const result = await client.deliverables.list(jobId);
    reporter.result(result, result.items.length === 0 ? "No deliverables." : result.items.map((item) => `${item.deliverableId}  ${item.kind}  ${item.byteSize}  ${item.fileName}`).join("\n"));
    return;
  }
  if (args[0] !== "download") throw new UsageError("Usage: instadescribe deliverables <list|download>");
  const options = parseOptions(args.slice(1), { destination: "string", overwrite: "boolean" });
  if (options.positionals.length !== 2) throw new UsageError("Usage: instadescribe deliverables download <job-id> <id-or-kind> --destination PATH");
  const [jobId, selector] = options.positionals as [string, string];
  const destination = stringOption(options, "destination");
  if (!destination) throw new UsageError("--destination is required");
  const { client } = await clientFor(globals, environment, paths, dependencies);
  const result = await client.deliverables.download(jobId, selector, resolve(destination), {
    overwrite: booleanOption(options, "overwrite"),
  });
  reporter.result(result, `${result.reusedExisting ? "Verified existing" : "Downloaded"} ${result.path} (${result.bytes} bytes)`);
}

class CommandError extends Error {
  readonly kind = "service";
  readonly code: string;
  readonly retryable = true;
  readonly details: Record<string, unknown>;

  constructor(message: string, code: string, details: Record<string, unknown>, cause?: unknown) {
    super(message, { cause });
    this.name = "CommandError";
    this.code = code;
    this.details = details;
  }
}

function errorShape(cause: unknown): { value: unknown; human: string; exitCode: number } {
  if (cause instanceof UsageError) {
    return { value: { error: { kind: "usage", code: "usage_error", message: cause.message } }, human: cause.message, exitCode: ExitCode.usage };
  }
  const entry = typeof cause === "object" && cause !== null ? cause as Record<string, unknown> : {};
  const kind = typeof entry.kind === "string" ? entry.kind : "unexpected";
  const code = typeof entry.code === "string" ? entry.code : "unexpected_error";
  const message = cause instanceof Error && kind !== "unexpected" ? cause.message : "Unexpected CLI error";
  let exitCode: number = ExitCode.unexpected;
  if (kind === "validation") exitCode = ExitCode.usage;
  else if (kind === "auth") exitCode = ExitCode.auth;
  else if (kind === "not_found") exitCode = ExitCode.notFound;
  else if (kind === "capacity" || kind === "conflict") exitCode = ExitCode.conflict;
  else if (kind === "timeout") exitCode = ExitCode.timeout;
  else if (kind === "service" || kind === "network" || kind === "contract") exitCode = ExitCode.service;
  else if (kind === "integrity" || kind === "filesystem") exitCode = ExitCode.integrity;
  else if (kind === "job_failed" || kind === "job_cancelled") exitCode = ExitCode.job;
  else if (kind === "unsupported") exitCode = ExitCode.unsupported;
  else if (kind === "aborted") exitCode = ExitCode.aborted;
  return {
    value: {
      error: {
        kind,
        code,
        message,
        ...(typeof entry.retryable === "boolean" ? { retryable: entry.retryable } : {}),
        ...(typeof entry.status === "number" ? { status: entry.status } : {}),
        ...(typeof entry.requestId === "string" ? { requestId: entry.requestId } : {}),
        ...(typeof entry.details === "object" && entry.details !== null ? { details: entry.details } : {}),
      },
    },
    human: `${code}: ${message}`,
    exitCode,
  };
}

export async function runCli(argv: readonly string[], dependencies: CliDependencies = {}): Promise<number> {
  const environment = dependencies.environment ?? process.env as CliEnvironment;
  const stdout = dependencies.stdout ?? process.stdout;
  const stderr = dependencies.stderr ?? process.stderr;
  let reporter = new Reporter({ mode: argv.includes("--json") ? "json" : argv.includes("--ndjson") ? "ndjson" : "human", quiet: false, stdout, stderr });
  try {
    const { args, globals } = extractGlobals(argv);
    reporter = new Reporter({ mode: globals.output, quiet: globals.quiet, stdout, stderr });
    const paths = dependencies.paths ?? configPaths(environment);
    const command = args[0];
    if (command === undefined || command === "help" || command === "--help" || command === "-h") {
      reporter.result({ version: VERSION, help: HELP }, HELP);
    } else if (command === "--version" || command === "-V") {
      reporter.result({ version: VERSION }, VERSION);
    } else if (command === "auth") {
      await commandAuth(args.slice(1), globals, environment, paths, dependencies, reporter);
    } else if (command === "config") {
      await commandConfig(args.slice(1), environment, paths, reporter);
    } else if (command === "jobs") {
      await commandJobs(args.slice(1), globals, environment, paths, dependencies, reporter);
    } else if (command === "create") {
      await commandJobs(["create", ...args.slice(1)], globals, environment, paths, dependencies, reporter);
    } else if (command === "list") {
      await commandJobs(["list", ...args.slice(1)], globals, environment, paths, dependencies, reporter);
    } else if (command === "status") {
      await commandJobs(["get", ...args.slice(1)], globals, environment, paths, dependencies, reporter);
    } else if (command === "wait") {
      await commandJobs(["wait", ...args.slice(1)], globals, environment, paths, dependencies, reporter);
    } else if (command === "cancel") {
      await commandJobs(["cancel", ...args.slice(1)], globals, environment, paths, dependencies, reporter);
    } else if (command === "confirm-upload") {
      await commandJobs(["complete-upload", ...args.slice(1)], globals, environment, paths, dependencies, reporter);
    } else if (command === "review") {
      await commandReview(args.slice(1), globals, environment, paths, dependencies, reporter);
    } else if (command === "download") {
      await commandDownload(args.slice(1), globals, environment, paths, dependencies, reporter);
    } else if (command === "deliverables") {
      await commandDeliverables(args.slice(1), globals, environment, paths, dependencies, reporter);
    } else {
      throw new UsageError(`Unknown command ${command}. Run instadescribe help.`);
    }
    return ExitCode.success;
  } catch (cause) {
    const shaped = errorShape(cause);
    reporter.error(shaped.value, shaped.human);
    return shaped.exitCode;
  }
}
