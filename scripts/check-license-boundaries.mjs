import { readdirSync, readFileSync } from "node:fs";
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

const investigationBase = "packages/investigation-core";
const investigationManifest = read(`${investigationBase}/pyproject.toml`);
const investigationLicense = read(`${investigationBase}/LICENSE`);
for (const marker of [
  'name = "instadescribe-investigation-core"',
  'requires-python = ">=3.12"',
  'license = "Apache-2.0"',
  "dependencies = []",
]) {
  requireText(investigationManifest, marker, `${investigationBase}/pyproject.toml`);
}
for (const marker of [
  "Apache License",
  "Version 2.0, January 2004",
  "Copyright 2026 Andrii Artemenko",
]) {
  requireText(investigationLicense, marker, `${investigationBase}/LICENSE`);
}

function pythonSources(directory) {
  return readdirSync(resolve(repository, directory), { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) {
      return pythonSources(path);
    }
    return entry.isFile() && entry.name.endsWith(".py") ? [path] : [];
  });
}

const prohibitedCoreRoots = [
  "app",
  "instadescribe_contracts",
  "instadescribe_worker",
  "modular_pipeline",
  "services",
];
const prohibitedCoreImport = new RegExp(
  `(?:from|import)\\s+(?:${prohibitedCoreRoots.join("|")})(?:\\.|\\s|$)`,
  "m",
);
for (const path of pythonSources(`${investigationBase}/src`)) {
  if (prohibitedCoreImport.test(read(path))) {
    throw new Error(`${path} must not import a BUSL-licensed core module`);
  }
}

const workerDockerfile = read("services/worker/Dockerfile");
requireText(
  workerDockerfile,
  "packages/investigation-core/LICENSE /app/licenses/instadescribe-investigation-core/LICENSE",
  "production worker Dockerfile",
);
requireText(
  workerDockerfile,
  'grep -q "Apache License" /app/licenses/instadescribe-investigation-core/LICENSE',
  "production worker Dockerfile",
);

const boundary = read("LICENSING.md").replace(/\s+/g, " ");
for (const marker of [
  "`packages/sdk/**`",
  "`packages/cli/**`",
  "`packages/investigation-core/**`",
  "Apache License 2.0 governs the autonomous investigation baseline",
  "2030-08-29",
  "Versions that were already distributed under MIT remain available under the MIT terms",
  "does not withdraw rights previously granted for earlier versions",
]) {
  requireText(boundary, marker, "LICENSING.md");
}

process.stdout.write("license-boundaries-ok\n");
