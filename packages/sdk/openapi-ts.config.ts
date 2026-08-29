import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "./openapi/instadescribe-integration-v1.contract.json",
  output: {
    path: "./src/generated/openapi",
    clean: true,
  },
  plugins: [
    "@hey-api/client-fetch",
    "@hey-api/typescript",
    "@hey-api/sdk",
    "zod",
  ],
});
