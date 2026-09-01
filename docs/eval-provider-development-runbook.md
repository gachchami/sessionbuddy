# Evaluation provider development runbook

Purpose: define how to add or change an evaluation model provider without
breaking launcher flags, step-level failover, retries, token accounting, or the
live dashboard.

Lifecycle: in-progress.

Authority: `scripts/run_sbek.sh` owns operator-facing provider selection and
secret loading. The eval kit's `src/model_client.ts` owns transport,
normalization, retries, fallback, and telemetry. `src/agent.ts` owns explicit
specification-step events. The evaluation dashboard consumes only the durable
artifacts documented below.

## Provider contract

A provider adapter must expose the eval kit's `ModelClient` interface and
normalize both agent and judge responses to Anthropic message blocks. It must
not change scenario semantics or consume launcher flags. Every argument after
the launcher command is forwarded unchanged to the eval CLI, including area,
scenario, configuration, turn-limit, dry-run, and resume flags.

Providers operate per model step:

1. Try the configured primary provider.
2. Retry only transient failures within the configured retry budget.
3. Record every attempt and scheduled retry before waiting.
4. After the primary budget is exhausted, retry that same model step through
   the configured fallback.
5. Continue later steps with the primary unless a deterministic conversation-
   level incompatibility disables it, such as repeated empty completions or a
   context-size rejection.
6. Never silently switch providers or discard a usable response.

Featherless, NVIDIA NIM, and B.AI use one shared OpenAI-compatible primary
transport and the authenticated Claude CLI fallback. Claude remains a CLI
integration; do not convert it to an API-key transport.

## Launcher configuration

Select the chain with:

```sh
SBEK_PROVIDER=featherless-claude scripts/run_sbek.sh [eval CLI flags]
SBEK_PROVIDER=nvidia-claude scripts/run_sbek.sh [eval CLI flags]
SBEK_PROVIDER=bai-claude scripts/run_sbek.sh [eval CLI flags]
```

Supported environment controls:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SBEK_FEATHERLESS_KEY_FILE` | `.local/featherless_api_key` | Ignored primary-provider credential file |
| `SBEK_FEATHERLESS_BASE_URL` | `https://api.featherless.ai/v1` | OpenAI-compatible API base |
| `SBEK_FEATHERLESS_AGENT_MODEL` | `zai-org/GLM-5.3-Flash` | Multimodal browser-agent model |
| `SBEK_FEATHERLESS_JUDGE_MODEL` | `zai-org/GLM-5.3-Flash` | Structured judge model |
| `SBEK_FEATHERLESS_TIMEOUT_MS` | `240000` | Timeout for one primary request |
| `SBEK_FEATHERLESS_MAX_ATTEMPTS` | `3` | Total primary attempts per model step |
| `SBEK_FEATHERLESS_RETRY_BASE_MS` | `1500` | Exponential retry base delay, capped at 30 seconds |
| `SBEK_AGENT_IMAGE_BUDGET` | `2` | Recent screenshots sent to an agent request |
| `SBEK_JUDGE_IMAGE_BUDGET` | `8` | Screenshots sent to a judge request |
| `SBEK_CLAUDE_TIMEOUT_MS` | `180000` | Claude CLI fallback timeout |

NVIDIA uses `.local/nvidia_api_key`, `https://integrate.api.nvidia.com/v1`,
and `minimaxai/minimax-m3` by default. Its equivalent controls use the
`SBEK_NVIDIA_*` prefix. B.AI uses `.local/bai_api_key`,
`https://api.b.ai/v1`, and `glm-5.3-flash`; its controls use the
`SBEK_BAI_*` prefix. Both inherit the same image budgets and Claude CLI
fallback. Override agent and judge models independently with
`SBEK_<PROVIDER>_AGENT_MODEL` and `SBEK_<PROVIDER>_JUDGE_MODEL` only after the
credential-specific `/models` catalogue confirms the identifier.

Secrets must never appear in command output, provider artifacts, dashboard
responses, or committed configuration.

## Retry classification

Retry connection failures, timeouts, empty completions, HTTP 408, 409, 425,
429, and 5xx responses. Do not retry deterministic authentication,
authorization, unsupported-model, schema, or ordinary 4xx failures. A retry
wait must be visible in `retry-status.json` and must be cleared after success.

Fallback is step-level: the exact agent or judge request that exhausted the
primary retry budget is sent to Claude CLI. It is not scenario-level replay.

## Mandatory dashboard artifacts

The dashboard must never infer script progress from provider usage. The eval
runner writes explicit `step-events.jsonl` records with timestamp, scenario,
step, and turn. The model must call `set_spec_step` before starting every
numbered scenario step.

Screenshot images are nested inside tool results. Provider converters must
traverse that structure recursively, must never serialize image base64 as tool
text, and must retain no more than the configured newest-image budget in model
context. Older screenshots remain evidence files on disk.

Every provider must also write sanitized artifacts under the run directory:

- `provider-state.json`: active provider, phase, health/retry/fallback status,
  and next retry time when applicable.
- `provider-events.jsonl`: request start, success, failure, retry, and fallback
  routing events. Store counts and safe diagnostics, never prompts or secrets.
- `provider-attempts.jsonl`: one success or failure record per network/CLI
  attempt.
- `token-usage.jsonl`: provider-reported input/output tokens, latency, model,
  scenario, step, turn, and phase.
- `token-usage-summary.json`: incrementally rebuilt totals by run, scenario,
  and specification step.
- `retry-status.json`: active per-scenario retry waits used by the dashboard.

If a provider does not report tokens, record zero with provider identity and
latency; the dashboard must say telemetry is unavailable instead of inventing
an estimate.

## Adding another provider

1. Add a transport adapter in `src/model_client.ts`; reuse the shared message,
   tool, image-budget, telemetry, retry, and fallback boundaries.
2. Add one explicit `SBEK_PROVIDER` branch to `scripts/run_sbek.sh`. Load its
   credential from an ignored file or environment variable and forward every
   eval CLI argument unchanged.
3. Validate the requested model against the provider's own model catalogue.
   Do not treat an OpenRouter listing as proof that a direct provider supports
   the same identifier.
4. Add deterministic tests for response normalization, retryable and
   non-retryable failures, retry exhaustion, fallback, token aggregation,
   explicit step attribution, and secret redaction.
5. Run a mocked small-packet and large-packet smoke before any paid call.
6. Run one bounded live canary scenario, watch the 30-second monitor, and stop
   on the first systemic failure.
7. Confirm the dashboard advances steps and displays provider status, retries,
   fallback, latency, and honest token availability.

## Dashboard launch

The dashboard is a separate local project and takes the eval run directory,
port, and specs directory explicitly:

```sh
node server.mjs /absolute/path/to/eval-checkout/runs 4317 \
  /absolute/path/to/eval-checkout/specs
```

If port 4317 is already listening and `/api/health` reports the intended run
and specs directories, use the existing dashboard. Starting a second instance
will correctly fail with `EADDRINUSE`.
