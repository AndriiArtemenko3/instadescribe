import { constants } from "node:fs";
import { access, chmod, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { UsageError } from "./args.js";

export interface CliEnvironment {
  INSTADESCRIBE_API_KEY?: string | undefined;
  INSTADESCRIBE_API_URL?: string | undefined;
  INSTADESCRIBE_APP_URL?: string | undefined;
  INSTADESCRIBE_CONFIG_DIR?: string | undefined;
  XDG_CONFIG_HOME?: string | undefined;
  APPDATA?: string | undefined;
}

export interface PublicConfig {
  apiUrl?: string | undefined;
  appUrl?: string | undefined;
}

interface Credentials {
  apiKey: string;
}

export interface RuntimeConfig {
  apiUrl: string;
  appUrl: string;
  apiKey: string;
  apiKeySource: "environment" | "credentials";
}

export interface ConfigPaths {
  directory: string;
  config: string;
  credentials: string;
}

export function configPaths(environment: CliEnvironment, platform = process.platform): ConfigPaths {
  const directory = environment.INSTADESCRIBE_CONFIG_DIR
    ?? (platform === "win32"
      ? join(environment.APPDATA ?? homedir(), "instadescribe")
      : join(environment.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "instadescribe"));
  return {
    directory,
    config: join(directory, "config.json"),
    credentials: join(directory, "credentials.json"),
  };
}

async function readJson(path: string): Promise<unknown | undefined> {
  try {
    return JSON.parse(await readFile(path, "utf8")) as unknown;
  } catch (cause) {
    if ((cause as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    if (cause instanceof SyntaxError) throw new UsageError(`Invalid JSON in ${path}`);
    throw cause;
  }
}

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new UsageError("Configuration must be a JSON object");
  return value as Record<string, unknown>;
}

export async function readPublicConfig(paths: ConfigPaths): Promise<PublicConfig> {
  const value = await readJson(paths.config);
  if (value === undefined) return {};
  const entry = object(value);
  for (const key of Object.keys(entry)) {
    if (key !== "apiUrl" && key !== "appUrl") throw new UsageError(`Unknown configuration key ${key}`);
  }
  if (entry.apiUrl !== undefined && typeof entry.apiUrl !== "string") throw new UsageError("apiUrl must be a string");
  if (entry.appUrl !== undefined && typeof entry.appUrl !== "string") throw new UsageError("appUrl must be a string");
  return {
    ...(typeof entry.apiUrl === "string" ? { apiUrl: entry.apiUrl } : {}),
    ...(typeof entry.appUrl === "string" ? { appUrl: entry.appUrl } : {}),
  };
}

async function readCredentials(paths: ConfigPaths): Promise<Credentials | undefined> {
  const value = await readJson(paths.credentials);
  if (value === undefined) return undefined;
  const entry = object(value);
  if (Object.keys(entry).some((key) => key !== "apiKey") || typeof entry.apiKey !== "string" || entry.apiKey.length === 0) {
    throw new UsageError(`Invalid credentials in ${paths.credentials}`);
  }
  return { apiKey: entry.apiKey };
}

async function atomicJson(path: string, value: unknown, mode: number): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  await chmod(dirname(path), 0o700);
  const temporary = `${path}.tmp-${process.pid}-${Date.now()}`;
  try {
    await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", flag: "wx", mode });
    await rename(temporary, path);
    await chmod(path, mode);
  } finally {
    await rm(temporary, { force: true }).catch(() => undefined);
  }
}

export async function writePublicConfig(paths: ConfigPaths, config: PublicConfig): Promise<void> {
  await atomicJson(paths.config, config, 0o600);
}

export async function writeCredentials(paths: ConfigPaths, apiKey: string): Promise<void> {
  if (apiKey.length === 0) throw new UsageError("API key is empty");
  await atomicJson(paths.credentials, { apiKey }, 0o600);
}

export async function removeCredentials(paths: ConfigPaths): Promise<boolean> {
  try {
    await access(paths.credentials, constants.F_OK);
  } catch (cause) {
    if ((cause as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw cause;
  }
  await rm(paths.credentials);
  return true;
}

export async function credentialsPresent(paths: ConfigPaths): Promise<boolean> {
  try {
    await access(paths.credentials, constants.F_OK);
    return true;
  } catch (cause) {
    if ((cause as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw cause;
  }
}

export async function resolveRuntime(
  environment: CliEnvironment,
  paths: ConfigPaths,
  overrides: { apiUrl?: string | undefined; appUrl?: string | undefined } = {},
): Promise<RuntimeConfig> {
  const config = await readPublicConfig(paths);
  const credentials = await readCredentials(paths);
  const apiUrl = overrides.apiUrl ?? environment.INSTADESCRIBE_API_URL ?? config.apiUrl;
  if (!apiUrl) throw new UsageError("API URL is not configured; use config set api-url or INSTADESCRIBE_API_URL");
  const apiKey = environment.INSTADESCRIBE_API_KEY ?? credentials?.apiKey;
  if (!apiKey) throw new UsageError("API key is not configured; use auth login --key-stdin or INSTADESCRIBE_API_KEY");
  return {
    apiUrl,
    appUrl: overrides.appUrl ?? environment.INSTADESCRIBE_APP_URL ?? config.appUrl ?? apiUrl,
    apiKey,
    apiKeySource: environment.INSTADESCRIBE_API_KEY ? "environment" : "credentials",
  };
}
