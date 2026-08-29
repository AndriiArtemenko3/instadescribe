import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repository = resolve(fileURLToPath(new URL("..", import.meta.url)));

function read(path) {
  return readFileSync(resolve(repository, path), "utf8");
}

function packageJson(path) {
  return JSON.parse(read(path));
}

function requireText(haystack, needle, label) {
  if (!haystack.includes(needle)) {
    throw new Error(`${label} is missing ${JSON.stringify(needle)}`);
  }
}

const coreLicense = read("LICENSE");
for (const marker of [
  "Business Source License 1.1",
  "Licensor: Andrii Artemenko",
  "Licensed Work: InstaDescribe Core, Version 1.0.0-beta.1",
  "Additional Use Grant: None",
  "Change Date: 2030-08-29",
  "Change License: Apache License, Version 2.0",
]) {
  requireText(coreLicense, marker, "root BUSL-1.1 license");
}

for (const [path, label] of [
  ["package.json", "root workspace"],
  ["App/package.json", "Web App"],
]) {
  const manifest = packageJson(path);
  if (manifest.license !== "BUSL-1.1") {
    throw new Error(`${label} must declare BUSL-1.1`);
  }
}

for (const packageName of ["sdk", "cli"]) {
  const base = `packages/${packageName}`;
  const manifest = packageJson(`${base}/package.json`);
  const license = read(`${base}/LICENSE`);
  if (manifest.license !== "MIT") {
    throw new Error(`${base} must declare MIT`);
  }
  if (!license.startsWith("MIT License") || !license.includes("Andrii Artemenko")) {
    throw new Error(`${base}/LICENSE must contain the approved MIT license`);
  }
}

const boundary = read("LICENSING.md").replace(/\s+/g, " ");
for (const marker of [
  "`packages/sdk/**`",
  "`packages/cli/**`",
  "2030-08-29",
  "Versions that were already distributed under MIT remain available under the MIT terms",
  "does not withdraw rights previously granted for earlier versions",
]) {
  requireText(boundary, marker, "LICENSING.md");
}

process.stdout.write("license-boundaries-ok\n");
