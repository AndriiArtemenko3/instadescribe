import { createHmac, timingSafeEqual } from "node:crypto";
import { WebhookVerificationError } from "./errors.js";

export interface VerifyWebhookOptions {
  rawBody: string | Uint8Array;
  secret: string;
  eventId: string;
  signature: string;
  timestamp: string | number;
  toleranceSeconds?: number;
  now?: Date | number;
}

export interface VerifiedWebhook {
  eventId: string;
  timestamp: number;
  body: Uint8Array;
}

function signatures(header: string): Buffer[] {
  const values: Buffer[] = [];
  for (const part of header.split(",")) {
    const [version, value, ...rest] = part.trim().split("=");
    if (version !== "v1" || value === undefined || rest.length > 0 || !/^[0-9a-f]{64}$/i.test(value)) continue;
    values.push(Buffer.from(value, "hex"));
  }
  return values;
}

/**
 * Verify the client-side webhook fixture contract:
 * HMAC-SHA256(secret, `${eventId}.${timestamp}.${rawBody}`), using the exact
 * `Webhook-Id`, `Webhook-Timestamp` and `Webhook-Signature` header values.
 */
export function verifyWebhook(options: VerifyWebhookOptions): VerifiedWebhook {
  if (Buffer.byteLength(options.secret, "utf8") < 32) {
    throw new WebhookVerificationError("Webhook secret must be at least 32 bytes");
  }
  if (!/^[\x21-\x7e]{1,128}$/.test(options.eventId)) {
    throw new WebhookVerificationError("Webhook event ID is invalid", "invalid_webhook_id");
  }
  const timestamp = typeof options.timestamp === "number" ? options.timestamp : Number(options.timestamp);
  if (!Number.isSafeInteger(timestamp) || timestamp < 0) {
    throw new WebhookVerificationError("Webhook timestamp is invalid", "invalid_webhook_timestamp");
  }
  const tolerance = options.toleranceSeconds ?? 300;
  if (!Number.isFinite(tolerance) || tolerance < 0) {
    throw new WebhookVerificationError("Webhook tolerance is invalid", "invalid_webhook_tolerance");
  }
  const now = options.now instanceof Date
    ? Math.floor(options.now.getTime() / 1000)
    : Math.floor((options.now ?? Date.now()) / (options.now === undefined ? 1000 : 1));
  if (Math.abs(now - timestamp) > tolerance) {
    throw new WebhookVerificationError("Webhook timestamp is outside the tolerance window", "stale_webhook");
  }
  const body = Buffer.from(options.rawBody);
  const expected = createHmac("sha256", options.secret)
    .update(Buffer.from(`${options.eventId}.${timestamp}.`, "utf8"))
    .update(body)
    .digest();
  const valid = signatures(options.signature).some((candidate) =>
    candidate.byteLength === expected.byteLength && timingSafeEqual(candidate, expected),
  );
  if (!valid) throw new WebhookVerificationError("Webhook signature is invalid");
  return { eventId: options.eventId, timestamp, body: new Uint8Array(body) };
}

export function verifyWebhookJson<T = unknown>(options: VerifyWebhookOptions): T {
  const verified = verifyWebhook(options);
  try {
    return JSON.parse(Buffer.from(verified.body).toString("utf8")) as T;
  } catch (cause) {
    throw new WebhookVerificationError("Verified webhook body is not valid JSON", "invalid_webhook_json", cause);
  }
}
