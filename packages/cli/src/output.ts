import type { OutputMode } from "./args.js";

export interface WritableText {
  write(chunk: string): unknown;
}

export interface ReporterOptions {
  mode: OutputMode;
  quiet: boolean;
  stdout: WritableText;
  stderr: WritableText;
}

export class Reporter {
  readonly #options: ReporterOptions;

  constructor(options: ReporterOptions) {
    this.#options = options;
  }

  result(value: unknown, human?: string): void {
    if (this.#options.mode === "human") {
      if (!this.#options.quiet) this.#options.stdout.write(`${human ?? formatHuman(value)}\n`);
      return;
    }
    this.#options.stdout.write(`${JSON.stringify(value)}\n`);
  }

  event(value: unknown, human?: string): void {
    if (this.#options.mode === "ndjson") {
      this.#options.stdout.write(`${JSON.stringify(value)}\n`);
    } else if (this.#options.mode === "human" && !this.#options.quiet && human) {
      this.#options.stderr.write(`${human}\n`);
    }
  }

  error(value: unknown, human: string): void {
    if (this.#options.mode === "human") this.#options.stderr.write(`${human}\n`);
    else this.#options.stderr.write(`${JSON.stringify(value)}\n`);
  }
}

function formatHuman(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
