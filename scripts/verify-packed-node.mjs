import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repository = resolve(fileURLToPath(new URL("..", import.meta.url)));
const npmCli = process.env.npm_execpath;
if (!npmCli) {
  throw new Error("npm_execpath is unavailable; run this verifier through npm run package:smoke");
}
const scratch = mkdtempSync(join(tmpdir(), "instadescribe-packed-consumer-"));
const packs = join(scratch, "packs");
const consumer = join(scratch, "consumer");

function command(executable, args, options = {}) {
  return execFileSync(executable, args, {
    cwd: repository,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
    env: { ...process.env, npm_config_cache: join(scratch, "npm-cache") },
    ...options,
  }).trim();
}

function npmCommand(args, options = {}) {
  return command(process.execPath, [npmCli, ...args], options);
}

function packedArchive(args, label) {
  const result = JSON.parse(npmCommand(["pack", ...args, "--pack-destination", packs, "--json"]));
  if (!Array.isArray(result) || result.length !== 1 || typeof result[0]?.filename !== "string") {
    throw new Error(`npm pack returned an invalid result for ${label}`);
  }
  const allowed = label === "SDK"
    ? ["LICENSE", "README.md", "package.json", "dist/", "openapi/"]
    : label === "CLI"
      ? ["LICENSE", "README.md", "package.json", "dist/"]
      : null;
  if (allowed) {
    const paths = (result[0].files ?? []).map((entry) => entry.path);
    for (const required of ["LICENSE", "README.md", "package.json"]) {
      if (!paths.includes(required)) {
        throw new Error(`${label} archive is missing ${required}`);
      }
    }
    const unexpected = paths.filter(
      (path) => !allowed.some((prefix) => path === prefix || path.startsWith(prefix)),
    );
    if (unexpected.length > 0) {
      throw new Error(`${label} archive contains unexpected files: ${unexpected.join(", ")}`);
    }
  }
  return join(packs, result[0].filename);
}

try {
  mkdirSync(packs);
  mkdirSync(consumer);
  const sdkArchive = packedArchive(["--workspace", "@instadescribe/sdk"], "SDK");
  const cliArchive = packedArchive(["--workspace", "instadescribe"], "CLI");
  // Keep the consumer install deterministic and registry-free. Root `npm ci`
  // already installed the SDK's sole runtime dependency from the lockfile;
  // packing that exact copy proves the product archives resolve in a clean app.
  const zodArchive = packedArchive([join(repository, "node_modules", "zod")], "zod");
  writeFileSync(
    join(consumer, "package.json"),
    `${JSON.stringify({ name: "instadescribe-packed-consumer", private: true, type: "module" }, null, 2)}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
  npmCommand(
    [
      "install",
      "--ignore-scripts",
      "--offline",
      "--no-audit",
      "--no-fund",
      zodArchive,
      sdkArchive,
      cliArchive,
    ],
    { cwd: consumer },
  );

  const probe = `
    import { InstaDescribe, verifyWebhook } from "@instadescribe/sdk";
    import { verifyWebhookJson } from "@instadescribe/sdk/webhooks";
    if (typeof InstaDescribe !== "function" || typeof verifyWebhook !== "function" || typeof verifyWebhookJson !== "function") {
      throw new Error("packed SDK exports are incomplete");
    }
  `;
  command(process.execPath, ["--input-type=module", "--eval", probe], { cwd: consumer });

  const cliPackage = JSON.parse(
    readFileSync(join(consumer, "node_modules", "instadescribe", "package.json"), "utf8"),
  );
  const cliEntryRelative = cliPackage.bin?.instadescribe;
  if (typeof cliEntryRelative !== "string" || cliEntryRelative.length === 0) {
    throw new Error("packed CLI does not declare the instadescribe binary");
  }
  for (const packageDirectory of ["@instadescribe/sdk", "instadescribe"]) {
    const license = readFileSync(
      join(consumer, "node_modules", packageDirectory, "LICENSE"),
      "utf8",
    );
    if (!license.startsWith("MIT License") || !license.includes("Andrii Artemenko")) {
      throw new Error(`${packageDirectory} must ship the approved MIT license`);
    }
  }
  const installedEntry = join(consumer, "node_modules", "instadescribe", cliEntryRelative);
  if (process.platform !== "win32" && (statSync(installedEntry).mode & 0o111) === 0) {
    throw new Error("packed CLI entry point is not executable");
  }
  const installedBin = join(
    consumer,
    "node_modules",
    ".bin",
    process.platform === "win32" ? "instadescribe.cmd" : "instadescribe",
  );
  statSync(installedBin);
  const cliVersion = process.platform === "win32"
    ? command(process.execPath, [installedEntry, "--version"], { cwd: consumer })
    : command(installedBin, ["--version"], { cwd: consumer });
  if (cliVersion !== cliPackage.version) {
    throw new Error(`packed CLI version mismatch: expected ${cliPackage.version}, received ${cliVersion}`);
  }
  process.stdout.write(`packed-node-smoke-ok ${cliVersion}\n`);
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
