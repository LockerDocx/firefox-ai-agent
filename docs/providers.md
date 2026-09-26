# Model providers

> Per-model parameter surfaces (which controls exist for Kimi vs GLM vs
> gpt-oss, and how reasoning reaches the wire) live in
> [model-parameters.md](model-parameters.md).

Three independent model roles drive the agent, and each one can use a different provider at the same time:

- **Planner** — decomposes the mission into a short checklist of browser steps (optional). `PLANNER_*` variables.
- **Policy** — the fast executor: decides the operation and target on every step. TypeSafe Jev (the original) or any provider below. `POLICY_*` variables.
- **Text helper** — writes the value whenever the policy chooses `TYPE_TEXT`. `TEXT_MODEL_*` variables.

If `TYPESAFE_API_KEY` is set, the policy uses Jev and `POLICY_*` is ignored. Otherwise the policy resolves from `POLICY_PROVIDER` / `POLICY_API_KEY` / `POLICY_BASE_URL` / `POLICY_MODEL`. The text helper always resolves from `TEXT_MODEL_*` variables (its legacy `TEXT_MODEL_BASE_URL` + `TEXT_MODEL` + `TEXT_MODEL_API_KEY` form keeps working).

## Zero configuration: one key is enough (the default)

`providers.selection_for(role)` resolves in one order: **whatever you wrote first, then the best
model of whatever free key is present**. You only need to set the key:

| Keys present | Planner | Executor | Text helper |
| --- | --- | --- | --- |
| `NVIDIA_API_KEY` only (recommended) | `nvidia:z-ai/glm-5.3-flash` | `nvidia:z-ai/glm-5.3-flash` | `nvidia:z-ai/glm-5.3-flash` |
| `GROQ_API_KEY` only | `groq:openai/gpt-oss-120b` | `groq:openai/gpt-oss-20b` | `groq:openai/gpt-oss-20b` |
| Both keys, or `DEEPSEEK_API_KEY` alone | `nvidia:z-ai/glm-5.3-flash` | `nvidia:z-ai/glm-5.3-flash` | `nvidia:z-ai/glm-5.3-flash` |

With one key that provider runs all three roles — the planner included, on the stronger model of
that provider rather than the executor's. **With both keys present nothing is mixed**: the first
provider in `DERIVATION_ORDER` that has a key for *every* role runs the whole mission.

That rule has a measured reason. The split this page used to recommend as the default — NVIDIA
planning, Groq executing — is the fastest arrangement on paper and the one that died in practice:
Groq's free tier is **8 000 tokens per minute**, the executor burns that in a couple of steps, and
the run ends with `HTTP 429` and the provider's "upgrade to Dev Tier" message. A key that expires
mid-mission is worse than a slower key that lasts. The split is still available, it is just opt-in:
write the `POLICY_*` / `TEXT_MODEL_*` constants and you get exactly it (see below). Any explicit `*_PROVIDER` / `*_MODEL` still wins over the derivation,
and choosing a provider without naming a model picks that provider's documented model (so a
half-written config is never a dead end). The sidebar, the planner gate and the *Test setup* check
all read this same resolution, so the panel can never disagree with what actually runs.

## The split, if you want it (opt-in, with its cost)

A slow, reasoning-heavy model plans once per task; a fast model executes every step. Two providers at
once — **and one warning**: the fast half is Groq's free tier, which gives you 8 000 tokens per
minute. On a mission long enough to spend that, this configuration stops mid-run with `HTTP 429`;
when it does, the message now says it is capacity, not your key. If you would rather not think about
it, the defaults above are the version that finishes:

```bash
# Fast executor · GPT-OSS-20B on Groq (free tier, very low latency)
POLICY_PROVIDER=groq
GROQ_API_KEY=gsk_...
POLICY_MODEL=openai/gpt-oss-20b
POLICY_REASONING=low

# Planner · GLM-5.3 on NVIDIA NIM (free endpoint)
PLANNER_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...
PLANNER_MODEL=z-ai/glm-5.3

# Text helper on Groq too: measured ~300 ms per field vs ~22 s with
# z-ai/glm-5.3-flash (which reasons by default).
TEXT_MODEL_PROVIDER=groq
TEXT_MODEL=openai/gpt-oss-20b
TEXT_MODEL_REASONING=low
```

Without `PLANNER_*` configuration the agent keeps the original single-goal loop, so nothing changes for existing setups.

### Where the latency actually is (measured, not guessed)

Measured on 25 September 2026 with `scripts/bench_profiles.py` against the live endpoints, from the
prompts and token budgets the agent itself uses, each candidate on its own CI job
(`.github/workflows/model-profiles.yml`, artifact per profile):

| Candidate (one free key) | Planner | Executor | Text writer | Quality checks |
| --- | --- | --- | --- | --- |
| **Groq only** (the fast anchor) | 1.4 s | **0.4 s** | **0.26 s** | plans 3/3 · routing 12/12 · values 4/4 |
| NVIDIA only, old defaults (`glm-5.3` plans, `gpt-oss-20b` executes) | 30 s | 36 s | 9 s | plans 2-3/3 · routing 12/12 · values 4/4 |
| NVIDIA only, `glm-5.3` with thinking off (the faster arrangement, one line away) | 37 s | **2-5 s** | 1.6-98 s | plans 2/3 · routing 12/12 · values 4/4 |
| NVIDIA only, `z-ai/glm-5.3-flash` with thinking off (**current defaults**) | 85 s | 43 s | 42 s | plans 2/2 · routing 12/12 · values 4/4 |

Read it as medians of a handful of calls, and read the spread: NVIDIA NIM's free tier answered the same
prompt in 1.6 s once and in 98 s on another run, and once did not answer for 60 s at all (the retry then
reports it honestly instead of blaming the key). **Smaller model and thinking off did not make NVIDIA
faster — the queue is the cost, not the parameters.** If you want a snappy agent and you are on one free
key, the Groq key is the lever — provided you stay under its 8 000 tokens per minute; the current
defaults instead keep one provider (NVIDIA) for the whole mission, which is slower per step and
finishes.

The defaults are `z-ai/glm-5.3-flash` with thinking off in all three roles, which was an explicit
product decision on 26 September 2026. On 26-27 September, with the same key, that arrangement measured
**37.4 s** median for the executor role (12 calls), **56.1 s** for the text helper (4) and **75.7 s** for
the planner (2 valid of 9, four of them timing out at 60 s), against **1.9 s**, **1.4 s** and **25.7 s**
for `z-ai/glm-5.3` with thinking off in the same run — flash is the smaller model of the same family and
waits in the same free-tier queue, which is where the seconds are. Both arrangements are one line apart:
`POLICY_MODEL` / `PLANNER_MODEL` / `TEXT_MODEL`, or the **Models & parameters** panel.

Reproduce it: `python scripts/bench_profiles.py --list`, then
`python scripts/bench_profiles.py --profile nvidia-flash-none` with your key in `.env`. `--floor 0.8` makes
it exit non-zero below that routing accuracy.

### Pinning everything to one provider (`nvidia`, alias `nim`)

The derivation above already does this for you when only that key is present; to pin it explicitly:

```bash
PLANNER_PROVIDER=nvidia
PLANNER_MODEL=z-ai/glm-5.3
POLICY_PROVIDER=nvidia
POLICY_MODEL=openai/gpt-oss-20b
POLICY_REASONING=low
TEXT_MODEL_PROVIDER=nvidia
TEXT_MODEL=openai/gpt-oss-20b
NVIDIA_API_KEY=nvapi-...
```

The reverse works too (one Groq key for everything: `POLICY_MODEL=openai/gpt-oss-20b`,
`PLANNER_MODEL=openai/gpt-oss-120b`), and a **local model is never required** — see the decision
table at the top of [laya.md](laya.md).

### How planning works

1. The planner is called **once** at task start with the mission and the initial page; it returns `{"steps": [...]}` (1–12 concrete steps).
2. The executor receives the mission, the full checklist (completed steps marked ✓), and the current step — and advances one step per turn.
3. A `DONE` choice marks the current step complete and continues with the next; the last `DONE` ends the run.
4. `BLOCKED` or three consecutive no-change actions trigger a bounded **replan** (max 2 per run): the planner sees the completed steps, the failure reason, and the current page, and returns only the remaining work.
5. The plan is guidance text only — the executor still validates every choice against the observed action space, so no step can invent elements or selectors.

## Presets

| `POLICY_PROVIDER` | Endpoint | Key variable | Notes |
| --- | --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | JSON mode on |
| `anthropic` | `https://api.anthropic.com` | `ANTHROPIC_API_KEY` | Native Messages API (`x-api-key` + `anthropic-version`) |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | Any catalog model, e.g. `anthropic/claude-sonnet-4.5` |
| `nvidia` (alias `nim`) | `https://integrate.api.nvidia.com/v1` | `NVIDIA_API_KEY` | NVIDIA NIM hosted inference |
| `omniroute` | `http://localhost:20128/v1` | `OMNIROUTE_API_KEY` | Self-hosted OmniRoute gateway; override the base URL for remote deployments |
| `deepseek` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | Default for the text helper when nothing else is configured |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `openai/gpt-oss-20b` is a fast free executor; `reasoning_effort` is mapped automatically |
| `together` | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` | |
| `mistral` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` | |
| `xai` (alias `grok`) | `https://api.x.ai/v1` | `XAI_API_KEY` | |
| `gemini` (alias `google`) | `https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY` | Gemini's OpenAI-compatible endpoint |
| `custom` | yours, via `POLICY_BASE_URL` | `POLICY_API_KEY` | vLLM, LiteLLM, any OpenAI-compatible gateway (local LLM runtimes are not supported) |
| *(a full URL)* | e.g. `POLICY_PROVIDER=https://gw.internal/v1` | `POLICY_API_KEY` | Same as `custom` |

Aliases: `nim`/`nvidia-nim` → `nvidia`, `grok` → `xai`, `google` → `gemini`, `omni` → `omniroute`, `openai-compatible` → `custom`.

The same table applies to the text helper with `TEXT_MODEL_PROVIDER` and `TEXT_MODEL_*`. When a `*_PROVIDER` is not given, the provider and dialect are inferred from the base URL (`https://api.anthropic.com` selects the Anthropic dialect; anything else is treated as OpenAI-compatible).

## Copy-paste examples

**OpenRouter**

```bash
POLICY_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
POLICY_MODEL=anthropic/claude-sonnet-4.5
# Optional separate text helper:
TEXT_MODEL_PROVIDER=openrouter
TEXT_MODEL=inception/mercury-2.5
TEXT_MODEL_REASONING=none
```

**NVIDIA NIM**

```bash
POLICY_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...
POLICY_MODEL=meta/llama-3.3-70b-instruct
```

**OmniRoute** (self-hosted gateway, default port 20128)

```bash
POLICY_PROVIDER=omniroute
POLICY_BASE_URL=http://localhost:20128/v1   # only if not on the default port/host
OMNIROUTE_API_KEY=sk_omniroute
POLICY_MODEL=cc/claude-sonnet-4.5
```

**OpenAI**

```bash
POLICY_PROVIDER=openai
OPENAI_API_KEY=sk-...
POLICY_MODEL=gpt-4.1
```

**Anthropic**

```bash
POLICY_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
POLICY_MODEL=claude-sonnet-4-5
```

**Your own gateway** (vLLM, LiteLLM, LM Studio, …)

```bash
POLICY_PROVIDER=custom
POLICY_BASE_URL=http://localhost:8000/v1
POLICY_API_KEY=dummy
POLICY_MODEL=Qwen/Qwen2.5-72B-Instruct
```

`POLICY_API_KEY` (or `TEXT_MODEL_API_KEY`) always wins over the preset's own key variable.

## How the generic policy works

The provider policy preserves the loop's core contract: **one request per decision cycle** and **only observed actions can execute**.

1. The same indexed element table, page excerpt, recent actions, and operation catalog that Jev receives are rendered into a single prompt (`POLICY_SYSTEM` in `questions.py`).
2. The model must reply with one JSON object: `{"operation": "CLICK", "target": "3", "confidence": 0.9}`. `SELECT` on a native dropdown uses an observed option index like `"5:2"`.
3. The reply is validated against the action space: an unknown operation, a target from another operation's head, or an invented index is rejected (after one corrective retry) and nothing executes. Model output never becomes selectors, coordinates, or code.
4. Unlike Jev, a generic LLM returns a choice, not a distribution — the inspector shows the chosen target and confidence instead of ranked probabilities.

## Reasoning and JSON mode

- `POLICY_REASONING` / `TEXT_MODEL_REASONING`: `none` (default), `low`, `medium`, `high`.
  - `none` disables thinking where the provider supports it (DeepSeek `thinking: disabled`, OpenRouter `reasoning: {enabled: false}`).
  - Effort levels map to `reasoning_effort` (OpenAI) and `reasoning.effort` (OpenRouter). Unverified combinations are omitted rather than sent.
- `POLICY_JSON_MODE` / `TEXT_MODEL_JSON_MODE`: `on`/`off` override. JSON mode (OpenAI `response_format`) is enabled only for presets that honor it across their catalog; everywhere else the reply is parsed robustly (fences and prose tolerated). Set it to `off` if your endpoint rejects the parameter.


## Laya — the open System-1 decision engine (optional, local)

[Laya](https://github.com/NandhaKishorM/laya) (Convai Innovations, Apache 2.0) is the open alternative to TypeSafe's Jev: it answers typed questions with calibrated confidence in one forward pass, fully local. This agent uses it — when installed — for two decisions where a generative model is overkill and keywords only know Spanish/English:

- **Mission routing** (browser loop vs tool orchestrator) in any language
- **Skill picking** (which procedural package guides the mission)

Everything is fail-safe: if the package is missing, the weights cannot load, or the calibrated confidence is below 0.55, the keyword implementation decides exactly as before. It is deliberately **not** used as the browser executor yet — its zero-shot choice accuracy and 512/1024-token context are not ready for picking among dozens of page elements (see [laya.md](laya.md) for the numbers and the fine-tuning path).

| Variable | Effect |
|---|---|
| `JEV_LAYA=off` | disable Laya participation (keywords only) |
| `LAYA_CHECKPOINT` | `multilingual` (default, 322M, 100+ languages), `english` (421M), or `typed-decisions` |

It is **installed by default** on the first start (background, `JEV_LAYA_AUTO=off` to decline, nothing is downloaded in CI or inside the test suite), and `pip install -e ".[laya]"` — or double-clicking `install-laya.bat` / `.command` / `.sh` — does the same by hand. The «Laya check» CI workflow replays a multilingual battery with the real weights and reports the accuracy on the PR.

## The sidebar catalogue and parameters (MVP-1)

The Firefox sidebar's **⚙️ Models & parameters** panel replaces `.env` editing for everyday changes:

- **Catalogue**: `GET {base_url}/models` is fetched for every provider that has a key, filtered to chat models (embed/rerank/image/audio families excluded), and cached for 24 h in `artifacts/model-registry.json` (`JEV_MODEL_REGISTRY` moves it). `Refresh catalogue` forces a refetch. A rejected key (401/403) shows a message pointing at the provider's key page.
- **Pickers**: one per role; choosing a model updates the environment and the config file, then re-runs the setup self-test so a bad id is caught immediately. The executor picker also offers the built-in **TypeSafe Jev** policy when `TYPESAFE_API_KEY` is set; picking it back restores the built-in policy without a restart.
- **Presets**: *Fast / Balanced / Deep / Browser / Coding* — bundles of per-role parameters.
- **Advanced**: per-role reasoning effort and temperature.

Persistence: `artifacts/model-config.json` (`JEV_MODEL_CONFIG` moves it). At startup the saved selection is re-applied over `.env`, so the sidebar always wins once you have used it. Delete the file to fall back to `.env` only.

Related environment variables (all optional):

| Variable | Effect |
|---|---|
| `PLANNER_TEMPERATURE`, `POLICY_TEMPERATURE`, `TEXT_MODEL_TEMPERATURE` | per-role temperature, 0–2, sent only when set |
| `JEV_MODEL_CONFIG` | where the sidebar's model/parameter choices persist |
| `JEV_MODEL_REGISTRY` | where the model catalogue cache persists |
| `JEV_SKILLS_DIR` | override the `skills/` directory (default: next to the package) |
| `JEV_RUNS_LOG` | where the per-run history is appended (default `artifacts/runs.jsonl`) |

**Profiles.** The panel can also save the *current* models + parameters as a named profile
(`Save` button) and re-apply it later with one click — handy for switching between a
"research" setup (deep planner) and a "local" setup (offline executor).

**Streaming.** Orchestrated tasks stream the model's raw output to the sidebar live
(the dashed "thinking" box between steps), including reasoning tokens when the model
emits them; endpoints that reject streaming fall back to a single request automatically.

**Run history.** Every finished task (browser or orchestrated) appends one line to
`artifacts/runs.jsonl` (timestamp, mode, goal, status, steps, elapsed, tokens) — plain
JSONL, easy to inspect or reset by deleting the file.

## Credential lifecycle: configure → validate → rotate → revoke

Keys live in exactly one place: your local `.env` (the sidebar's model config stores model *names*, never keys). The whole lifecycle:

1. **Configure** — one key is enough. Paste it in the Firefox sidebar (the first-run card, or *🔑 API keys* in the models panel): the host validates it, exports it and writes `GROQ_API_KEY=...` into `.env` itself. Editing that line by hand works exactly the same; no quotes needed (the loader strips accidental quotes and BOMs).
2. **Validate** — press **Test setup** in the sidebar (or check the PR comments from the *Provider check* workflow). Each role shows 🟢 with latency, or the provider's exact error (401/403 = bad key, 404 = bad model id).
3. **Rotate** — generate the fresh key at the provider (links in the table above), replace the line in `.env`, restart the host, press **Test setup** again. Nothing else to clean: no other file ever stored the old key.
4. **Revoke** — delete the key at the provider's console, then remove (or comment) its line in `.env` and restart.

Guarantees: every error message, log line, and broadcast passes through a redaction layer that masks anything shaped like an API key (`gsk_…`, `nvapi-…`, `sk-…`, `KEY=value`, `Bearer …`) before it reaches the sidebar, `runs.jsonl`, or the audit trail — variable *names* survive so diagnostics stay useful. The browser extension only ever receives model names, never keys.

## Troubleshooting

- **`No API key for the policy role: set POLICY_API_KEY or OPENROUTER_API_KEY...`** — the message names the variable for each role; with a single free key the agent derives its own provider and this never appears. Provide the key in either form if you deliberately pinned a provider.
- **`POLICY_MODEL is not set`** — the policy role has no default model; name the exact id your provider expects.
- **HTTP 400 on `response_format`** — set `POLICY_JSON_MODE=off` (or the `TEXT_MODEL_JSON_MODE` equivalent).
- **HTTP 401/403** — the key does not match the provider/base URL combination.
- **OmniRoute 404s** — make sure the base URL includes `/v1` and the OmniRoute gateway is running.
- The text helper still refuses to guess: without a key, `TYPE_TEXT` stops with an error instead of typing an invented value.
