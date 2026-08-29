import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { link, mkdir, open, rename, rm, stat, unlink } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { IntegrityError, InstaDescribeError } from "./errors.js";
import type { Deliverable, DownloadDeliverableOptions, DownloadDeliverableResult } from "./types.js";

async function digestFile(path: string): Promise<{ bytes: number; sha256: string }> {
  const hash = createHash("sha256");
  let bytes = 0;
  for await (const chunk of createReadStream(path)) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += buffer.byteLength;
    hash.update(buffer);
  }
  return { bytes, sha256: hash.digest("hex") };
}

function safeSignedUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch (cause) {
    throw new InstaDescribeError("Deliverable redirect contains an invalid URL", {
      kind: "contract", code: "invalid_download_url", cause,
    });
  }
  const loopback = url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) || url.username || url.password) {
    throw new InstaDescribeError("Deliverable redirect contains an unsafe URL", {
      kind: "contract", code: "unsafe_download_url",
    });
  }
  return url;
}

async function exists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch (cause) {
    if ((cause as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw cause;
  }
}

export async function downloadDeliverable(
  fetchImpl: typeof globalThis.fetch,
  deliverable: Deliverable,
  signedLocation: string,
  destination: string,
  options: DownloadDeliverableOptions = {},
): Promise<DownloadDeliverableResult> {
  const target = resolve(destination);
  const partial = `${target}.part`;
  try {
    if (await exists(target)) {
      const current = await digestFile(target);
      if (current.bytes === deliverable.byteSize && current.sha256 === deliverable.sha256) {
        return { deliverable, path: target, bytes: current.bytes, sha256: current.sha256, reusedExisting: true };
      }
      if (!options.overwrite) {
        throw new InstaDescribeError("Destination exists but does not match the deliverable", {
          kind: "conflict", code: "destination_exists",
        });
      }
    }
    await mkdir(dirname(target), { recursive: true });
    const file = await open(partial, "wx", 0o600).catch((cause: unknown) => {
      if ((cause as NodeJS.ErrnoException).code === "EEXIST") {
        throw new InstaDescribeError("A partial download already exists", {
          kind: "filesystem", code: "partial_exists", cause,
        });
      }
      throw cause;
    });
    let createdPartial = true;
    try {
      let response: Response;
      try {
        response = await fetchImpl(safeSignedUrl(signedLocation), {
          method: "GET",
          headers: { Accept: "*/*" },
          credentials: "omit",
          redirect: "error",
          ...(options.signal ? { signal: options.signal } : {}),
        });
      } catch (cause) {
        throw new InstaDescribeError("Deliverable download failed", {
          kind: options.signal?.aborted ? "aborted" : "network",
          code: options.signal?.aborted ? "aborted" : "download_network_error",
          retryable: !options.signal?.aborted,
          cause,
        });
      }
      if (!response.ok) {
        throw new InstaDescribeError(`Deliverable download failed with HTTP ${response.status}`, {
          kind: response.status >= 500 ? "service" : "conflict",
          code: "download_http_error", status: response.status, retryable: response.status >= 500,
        });
      }
      if (response.body === null) {
        throw new InstaDescribeError("Deliverable response had no body", {
          kind: "contract", code: "download_empty_body",
        });
      }
      const hash = createHash("sha256");
      let bytes = 0;
      const meter = new Transform({
        transform(chunk: Buffer, _encoding, callback) {
          bytes += chunk.byteLength;
          hash.update(chunk);
          callback(null, chunk);
        },
      });
      await pipeline(Readable.fromWeb(response.body as never), meter, file.createWriteStream());
      const sha256 = hash.digest("hex");
      if (bytes !== deliverable.byteSize) {
        throw new IntegrityError("Downloaded deliverable size does not match metadata", String(deliverable.byteSize), String(bytes));
      }
      if (sha256 !== deliverable.sha256) {
        throw new IntegrityError("Downloaded deliverable checksum does not match metadata", deliverable.sha256, sha256);
      }
      if (options.overwrite) {
        await rename(partial, target);
      } else {
        try {
          await link(partial, target);
        } catch (cause) {
          if ((cause as NodeJS.ErrnoException).code === "EEXIST") {
            throw new InstaDescribeError("Destination was created during download", {
              kind: "conflict", code: "destination_exists", cause,
            });
          }
          throw cause;
        }
        await unlink(partial);
      }
      createdPartial = false;
      return { deliverable, path: target, bytes, sha256, reusedExisting: false };
    } finally {
      await file.close().catch(() => undefined);
      if (createdPartial) await rm(partial, { force: true }).catch(() => undefined);
    }
  } catch (cause) {
    if (cause instanceof InstaDescribeError) throw cause;
    throw new InstaDescribeError("Could not write the deliverable", {
      kind: "filesystem", code: "filesystem_error", cause,
    });
  }
}
