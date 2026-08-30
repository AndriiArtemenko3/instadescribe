import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExitCode, runCli, VERSION, type CliDependencies } from "../src/index.js";
import { configPaths } from "../src/config.js";

const JOB_ID = "11111111-1111-4111-8111-111111111111";
const PROJECT_ID = "22222222-2222-4222-8222-222222222222";
const DELIVERABLE_ID = "33333333-3333-4333-8333-333333333333";
const API_KEY = `idsb_live_0123456789ab.${"x".repeat(43)}`;
const REVIEW_URL = `https://app.example/orgs/agency/projects/${PROJECT_ID}/jobs/${JOB_ID}/review`;

function job(state = "needs_review", progress = 100) {
  return {
    jobId: JOB_ID,
    projectId: PROJECT_ID,
    clientReference: null,
    state: state as "needs_review",
    progress,
    stage: state,
    pipelineRevision: "test",
    source: { status: "uploaded" as const, contentType: "video/mp4", sizeBytes: 5, durationSeconds: 90 },
    reviewUrl: REVIEW_URL,
    error: null,
    createdAt: "2026-08-28T10:00:00Z",
    updatedAt: "2026-08-28T10:01:00Z",
  };
}

function fakeClient() {
  const current = job();
  return {
    capabilities: { get: vi.fn(async () => ({ brand: "InstaDescribe", apiVersion: "v1-beta" })) },
    jobs: {
      create: vi.fn(async () => ({
        job: job("awaiting_upload", 0),
        uploads: {
          video: {
            method: "POST" as const,
            url: "https://storage.example/upload?signature=secret",
            fields: { policy: "secret-policy" },
            expiresAt: "2026-08-28T11:00:00Z",
          },
          transcript: {
            method: "POST" as const,
            url: "https://storage.example/transcript?signature=secret-two",
            fields: { policy: "secret-transcript-policy" },
            expiresAt: "2026-08-28T11:00:00Z",
          },
        },
      })),
      list: vi.fn(async () => ({ items: [current], nextCursor: null })),
      get: vi.fn(async () => current),
      completeUpload: vi.fn(async () => job("queued", 0)),
      wait: vi.fn(async (_jobId: string, options?: { onProgress?: (value: ReturnType<typeof job>) => void | Promise<void> }) => {
        await options?.onProgress?.(current);
        return current;
      }),
      cancel: vi.fn(async () => job("cancelled", 0)),
    },
    deliverables: {
      list: vi.fn(async () => ({
        items: [{
          deliverableId: DELIVERABLE_ID,
          jobId: JOB_ID,
          kind: "srt" as const,
          fileName: "audio_description.srt",
          contentType: "application/x-subrip",
          byteSize: 12,
          sha256: "a".repeat(64),
          createdAt: "2026-08-28T10:02:00Z",
        }],
        completedSet: true as const,
      })),
      download: vi.fn(async (_jobId: string, _selector: string, destination: string) => ({
        deliverable: {
          deliverableId: DELIVERABLE_ID, jobId: JOB_ID, kind: "srt" as const, fileName: "audio_description.srt",
          contentType: "application/x-subrip", byteSize: 12, sha256: "a".repeat(64), createdAt: "2026-08-28T10:02:00Z",
        },
        path: destination,
        bytes: 12,
        sha256: "a".repeat(64),
        reusedExisting: false,
      })),
    },
    uploads: { uploadFile: vi.fn(async () => undefined) },
    reviewUrl: vi.fn((value: ReturnType<typeof job>) => new URL(value.reviewUrl)),
  };
}

function sink() {
  let value = "";
  return {
    stream: { write(chunk: string) { value += chunk; } },
    read: () => value,
  };
}

describe("instadescribe CLI", () => {
  const directories: string[] = [];
  afterEach(async () => {
    await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
  });

  async function context(client = fakeClient()) {
    const directory = await mkdtemp(join(tmpdir(), "instadescribe-cli-"));
    directories.push(directory);
    const stdout = sink();
    const stderr = sink();
    const environment = {
      INSTADESCRIBE_API_URL: "https://api.example",
      INSTADESCRIBE_APP_URL: "https://app.example",
      INSTADESCRIBE_API_KEY: API_KEY,
      INSTADESCRIBE_CONFIG_DIR: directory,
    };
    const dependencies: CliDependencies = {
      environment,
      stdout: stdout.stream,
      stderr: stderr.stream,
      createClient: async () => client as never,
      paths: configPaths(environment),
    };
    return { directory, stdout, stderr, environment, dependencies, client };
  }

  it("provides machine-readable version output without loading the SDK", async () => {
    const stdout = sink();
    expect(await runCli(["--version", "--output=json"], { stdout: stdout.stream, stderr: sink().stream })).toBe(ExitCode.success);
    expect(JSON.parse(stdout.read())).toEqual({ version: VERSION });
  });

  it("validates auth through capabilities and stores stdin credentials with mode 0600 on POSIX", async () => {
    const { directory, stdout, dependencies, client } = await context();
    delete dependencies.environment!.INSTADESCRIBE_API_KEY;
    dependencies.readStdin = async () => `${API_KEY}\n`;
    const exit = await runCli(["auth", "login", "--key-stdin", "--json"], dependencies);
    expect(exit).toBe(ExitCode.success);
    expect(client.capabilities.get).toHaveBeenCalledOnce();
    const credentials = join(directory, "credentials.json");
    expect(JSON.parse(await readFile(credentials, "utf8"))).toEqual({ apiKey: API_KEY });
    if (process.platform !== "win32") {
      expect((await stat(credentials)).mode & 0o777).toBe(0o600);
    }
    expect(stdout.read()).not.toContain(API_KEY);
  });

  it("persists validated origin config without credentials", async () => {
    const { directory, stdout, dependencies } = await context();
    expect(await runCli(["config", "set", "api-url", "https://new-api.example", "--json"], dependencies)).toBe(0);
    expect(JSON.parse(stdout.read())).toEqual({ key: "api-url", value: "https://new-api.example" });
    expect(JSON.parse(await readFile(join(directory, "config.json"), "utf8"))).toEqual({ apiUrl: "https://new-api.example" });
  });

  it("creates inline project metadata, uploads video and transcript, then completes", async () => {
    const { directory, stdout, dependencies, client } = await context();
    const video = join(directory, "launch.mp4");
    const transcript = join(directory, "launch.vtt");
    await writeFile(video, Buffer.from("video"));
    await writeFile(transcript, Buffer.from("WEBVTT"));
    const exit = await runCli([
      "create", video,
      "--project", "Agency launch",
      "--external-id", "crm-42",
      "--client-reference", "run-9",
      "--transcript", transcript,
      "--duration", "92",
      "--json",
    ], dependencies);
    expect(exit).toBe(ExitCode.success);
    expect(client.jobs.create).toHaveBeenCalledWith(expect.objectContaining({
      project: { name: "Agency launch", externalId: "crm-42" },
      clientReference: "run-9",
      video: { fileName: "launch.mp4", contentType: "video/mp4", sizeBytes: 5, durationSeconds: 92 },
      transcript: { fileName: "launch.vtt", format: "vtt", contentType: "text/vtt", sizeBytes: 6 },
      settings: { preset: "standard", style: "documentary", detail: 3 },
    }));
    expect(client.uploads.uploadFile).toHaveBeenCalledTimes(2);
    expect(client.jobs.completeUpload).toHaveBeenCalledWith(JOB_ID);
    expect(JSON.parse(stdout.read())).toMatchObject({ projectId: PROJECT_ID, jobId: JOB_ID, state: "queued" });
    expect(stdout.read()).not.toContain("secret-policy");
    expect(stdout.read()).not.toContain("signature=secret");
  });

  it("emits progress plus one final result in ndjson mode", async () => {
    const { stdout, dependencies, client } = await context();
    expect(await runCli(["wait", JOB_ID, "--until", "completed", "--output", "jsonl"], dependencies)).toBe(0);
    const lines = stdout.read().trim().split("\n").map((line) => JSON.parse(line));
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatchObject({ type: "job.progress", jobId: JOB_ID, state: "needs_review" });
    expect(lines[1]).toMatchObject({ job: { jobId: JOB_ID }, reviewUrl: REVIEW_URL });
    expect(client.jobs.wait).toHaveBeenCalledWith(JOB_ID, expect.objectContaining({ until: "completed" }));
  });

  it("lists and downloads deliverables with an explicit destination", async () => {
    const { directory, stdout, dependencies, client } = await context();
    const destination = join(directory, "audio_description.srt");
    expect(await runCli(["deliverables", "download", JOB_ID, "srt", "--destination", destination, "--json"], dependencies)).toBe(0);
    expect(client.deliverables.download).toHaveBeenCalledWith(JOB_ID, "srt", destination, { overwrite: false });
    expect(JSON.parse(stdout.read())).toMatchObject({ path: destination, bytes: 12 });
  });

  it("downloads the completed deliverable set through the top-level command", async () => {
    const { directory, stdout, dependencies, client } = await context();
    const destination = join(directory, "delivery");
    expect(await runCli(["download", JOB_ID, "--output-dir", destination, "--output", "json"], dependencies)).toBe(0);
    expect(client.deliverables.download).toHaveBeenCalledWith(
      JOB_ID,
      expect.objectContaining({ deliverableId: DELIVERABLE_ID }),
      join(destination, "audio_description.srt"),
      { overwrite: false },
    );
    expect(JSON.parse(stdout.read())).toMatchObject({ jobId: JOB_ID, directory: destination, items: [{ bytes: 12 }] });
  });

  it("opens only the server-provided review URL when explicitly requested", async () => {
    const { stdout, dependencies } = await context();
    const openUrl = vi.fn(async () => undefined);
    dependencies.openUrl = openUrl;
    expect(await runCli(["review", JOB_ID, "--open", "--json"], dependencies)).toBe(0);
    expect(openUrl).toHaveBeenCalledWith(new URL(REVIEW_URL));
    expect(JSON.parse(stdout.read())).toMatchObject({ jobId: JOB_ID, opened: true });
  });

  it("maps RFC-style auth failures to stable exit 3 and one machine error", async () => {
    const client = fakeClient();
    client.jobs.get.mockRejectedValueOnce(Object.assign(new Error("The API key does not grant the required scope."), {
      kind: "auth", code: "insufficient_scope", status: 403, retryable: false,
    }));
    const { stderr, dependencies } = await context(client);
    expect(await runCli(["jobs", "get", JOB_ID, "--json"], dependencies)).toBe(ExitCode.auth);
    expect(JSON.parse(stderr.read())).toEqual({
      error: { kind: "auth", code: "insufficient_scope", message: "The API key does not grant the required scope.", retryable: false, status: 403 },
    });
  });

  it("returns usage exit 2 when configuration is incomplete", async () => {
    const directory = await mkdtemp(join(tmpdir(), "instadescribe-cli-"));
    directories.push(directory);
    const stderr = sink();
    const exit = await runCli(["jobs", "list", "--json"], {
      environment: { INSTADESCRIBE_CONFIG_DIR: directory }, stderr: stderr.stream, stdout: sink().stream,
    });
    expect(exit).toBe(ExitCode.usage);
    expect(JSON.parse(stderr.read()).error.code).toBe("usage_error");
  });
});
