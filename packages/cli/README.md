# `instadescribe`

ESM-only CLI for the public InstaDescribe Integration API. Requires Node.js 22.19 or newer.

```sh
instadescribe config set api-url https://api.instadescribe.example
instadescribe config set app-url https://app.instadescribe.example
printf '%s' "$INSTADESCRIBE_API_KEY" | instadescribe auth login --key-stdin

instadescribe create ./launch.mp4 --project "Agency launch" --external-id crm-42 --wait
instadescribe create ./episode.mp4 --project-id PROJECT_ID --transcript ./episode.vtt
instadescribe list --output json
instadescribe wait JOB_ID --until completed --output jsonl
instadescribe review JOB_ID --open
instadescribe download JOB_ID --output-dir ./accessible

# Advanced single-deliverable operations remain available.
instadescribe deliverables list JOB_ID
instadescribe deliverables download JOB_ID mp4 --destination ./described.mp4
```

Configuration precedence is command-line URL override, environment, then config file. The API key comes from `INSTADESCRIBE_API_KEY` or the credentials file. Credentials are written separately with mode `0600` on POSIX. At the default Windows location they rely on the current user's inherited `%APPDATA%` ACL; callers overriding `INSTADESCRIBE_CONFIG_DIR` must provide a private directory. The CLI intentionally has no `--api-key` option that would leak into shell history. Login validates the key through the public capabilities endpoint.

`--output human|json|jsonl` selects the output contract; `--json` and `--ndjson` remain aliases. Progress is written to stderr in human mode. Diagnostics and machine-readable errors go to stderr. Stable exit codes are exported as `ExitCode`: `2` usage, `3` auth/scope, `4` not found, `5` conflict/capacity, `6` timeout, `7` service/network/contract, `8` integrity/filesystem, `9` failed/cancelled job, `10` unsupported operation, and `130` aborted.

Review remains in the web UI. The CLI opens only a server-provided URL on the configured app origin and never edits review state itself.
