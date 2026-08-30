export type InstaDescribeErrorKind =
  | "auth"
  | "validation"
  | "not_found"
  | "capacity"
  | "conflict"
  | "service"
  | "network"
  | "timeout"
  | "aborted"
  | "job_failed"
  | "job_cancelled"
  | "integrity"
  | "unsupported"
  | "contract"
  | "filesystem";

export interface InstaDescribeErrorOptions {
  kind: InstaDescribeErrorKind;
  code?: string;
  status?: number;
  retryable?: boolean;
  cause?: unknown;
  requestId?: string | undefined;
}

export class InstaDescribeError extends Error {
  readonly kind: InstaDescribeErrorKind;
  readonly code: string | undefined;
  readonly status: number | undefined;
  readonly retryable: boolean;
  readonly requestId: string | undefined;

  constructor(message: string, options: InstaDescribeErrorOptions) {
    super(message, { cause: options.cause });
    this.name = "InstaDescribeError";
    this.kind = options.kind;
    this.code = options.code;
    this.status = options.status;
    this.retryable = options.retryable ?? false;
    this.requestId = options.requestId;
  }
}

export class UnsupportedOperationError extends InstaDescribeError {
  constructor(message: string, cause?: unknown) {
    super(message, { kind: "unsupported", code: "unsupported_operation", cause });
    this.name = "UnsupportedOperationError";
  }
}

export class IntegrityError extends InstaDescribeError {
  readonly expected: string;
  readonly actual: string;

  constructor(message: string, expected: string, actual: string) {
    super(message, { kind: "integrity", code: "checksum_mismatch" });
    this.name = "IntegrityError";
    this.expected = expected;
    this.actual = actual;
  }
}

export class WebhookVerificationError extends InstaDescribeError {
  constructor(message: string, code = "invalid_webhook_signature", cause?: unknown) {
    super(message, { kind: "auth", code, cause });
    this.name = "WebhookVerificationError";
  }
}
