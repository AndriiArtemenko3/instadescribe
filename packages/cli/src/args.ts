export type OutputMode = "human" | "json" | "ndjson";

export interface GlobalOptions {
  output: OutputMode;
  quiet: boolean;
  apiUrl?: string | undefined;
  appUrl?: string | undefined;
}

export class UsageError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UsageError";
  }
}

export function extractGlobals(argv: readonly string[]): { args: string[]; globals: GlobalOptions } {
  const args: string[] = [];
  let output: OutputMode = "human";
  let quiet = false;
  let apiUrl: string | undefined;
  let appUrl: string | undefined;
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index]!;
    if (value === "--json" || value === "--ndjson") {
      const next = value === "--json" ? "json" : "ndjson";
      if (output !== "human" && output !== next) throw new UsageError("--json and --ndjson are mutually exclusive");
      output = next;
    } else if (value === "--output") {
      const requested = argv[index + 1];
      if (requested === undefined || requested.startsWith("--")) throw new UsageError("--output requires a value");
      const next = requested === "jsonl" ? "ndjson" : requested;
      if (next !== "human" && next !== "json" && next !== "ndjson") {
        throw new UsageError("--output must be human, json or jsonl");
      }
      if (output !== "human" && output !== next) throw new UsageError("Output modes are mutually exclusive");
      output = next;
      index += 1;
    } else if (value.startsWith("--output=")) {
      const requested = value.slice("--output=".length);
      const next = requested === "jsonl" ? "ndjson" : requested;
      if (next !== "human" && next !== "json" && next !== "ndjson") {
        throw new UsageError("--output must be human, json or jsonl");
      }
      if (output !== "human" && output !== next) throw new UsageError("Output modes are mutually exclusive");
      output = next;
    } else if (value === "--quiet") {
      quiet = true;
    } else if (value === "--api-url" || value === "--app-url") {
      const next = argv[index + 1];
      if (next === undefined || next.startsWith("--")) throw new UsageError(`${value} requires a value`);
      if (value === "--api-url") apiUrl = next;
      else appUrl = next;
      index += 1;
    } else if (value.startsWith("--api-url=")) {
      apiUrl = value.slice("--api-url=".length);
    } else if (value.startsWith("--app-url=")) {
      appUrl = value.slice("--app-url=".length);
    } else {
      args.push(value);
    }
  }
  return { args, globals: { output, quiet, apiUrl, appUrl } };
}

export type OptionKind = "boolean" | "string";

export interface ParsedOptions {
  positionals: string[];
  values: Record<string, string | boolean>;
}

export function parseOptions(args: readonly string[], schema: Readonly<Record<string, OptionKind>>): ParsedOptions {
  const positionals: string[] = [];
  const values: Record<string, string | boolean> = {};
  for (let index = 0; index < args.length; index += 1) {
    const token = args[index]!;
    if (token === "--") {
      positionals.push(...args.slice(index + 1));
      break;
    }
    if (!token.startsWith("--")) {
      positionals.push(token);
      continue;
    }
    const separator = token.indexOf("=");
    const name = separator === -1 ? token.slice(2) : token.slice(2, separator);
    const kind = schema[name];
    if (kind === undefined) throw new UsageError(`Unknown option --${name}`);
    if (Object.hasOwn(values, name)) throw new UsageError(`Option --${name} was provided more than once`);
    if (kind === "boolean") {
      if (separator !== -1) throw new UsageError(`Option --${name} does not take a value`);
      values[name] = true;
      continue;
    }
    const value = separator === -1 ? args[index + 1] : token.slice(separator + 1);
    if (value === undefined || (separator === -1 && value.startsWith("--"))) {
      throw new UsageError(`Option --${name} requires a value`);
    }
    values[name] = value;
    if (separator === -1) index += 1;
  }
  return { positionals, values };
}

export function stringOption(options: ParsedOptions, name: string): string | undefined {
  const value = options.values[name];
  return typeof value === "string" ? value : undefined;
}

export function booleanOption(options: ParsedOptions, name: string): boolean {
  return options.values[name] === true;
}

export function numberOption(options: ParsedOptions, name: string, required = false): number | undefined {
  const raw = stringOption(options, name);
  if (raw === undefined) {
    if (required) throw new UsageError(`--${name} is required`);
    return undefined;
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new UsageError(`--${name} must be a number`);
  return value;
}

export function onePositional(options: ParsedOptions, usage: string): string {
  if (options.positionals.length !== 1) throw new UsageError(`Usage: ${usage}`);
  return options.positionals[0]!;
}
