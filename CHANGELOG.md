# Changelog

Notable changes per version. Versions 0.5.0 to 0.8.0 shipped inside the code line and are listed here even
though they never had a GitHub release; everything else has a tag.

**On the name:** until v0.11.0 the project and the add-on were called *Jev Ultrafast* — borrowed from
**TypeSafe AI**'s Jev model, because the upstream project this fork started from demonstrates it. It is not
ours, and it is gone from anything a user sees: the name is now *AI Agent for Firefox*. Internal
identifiers (`jev_ultrafast`, the `jev-*` console scripts, the `JEV_*` variables, the extension id and the
native-messaging host name) were kept on purpose, so installations and Firefox registrations that already
exist keep working.

**On the measurements:** the performance figures in `docs/` were recorded by the upstream project, on
Chrome, with TypeSafe's hosted policy and the Mercury text model. They are labelled as such wherever they
appear. This build drives Firefox with Groq/NVIDIA and has not been measured yet.


## [0.13.2] — 2026-09-30

The change v0.13.1 should have carried. Its two JavaScript tests ran `node -e <script>` with
a script containing an accent and the mask character «•••»; under a legacy code page an
argv string must be ASCII-encodable, so `posix_spawn` raised `UnicodeEncodeError` before
node started, and `text=True` had the same problem decoding stdout. CI caught it on the
v0.13.1 commit, in all three Python jobs. The script now travels as a UTF-8 file and stdout
is decoded as UTF-8.

The defect was in the tests, not in the shipped code: the mask, the action value, the page
key and the guard are exactly as v0.13.1 published them, and those artifacts were correct.
v0.13.1 is left untouched on purpose. This release exists so the tag and `main` agree and
CI is green on what the tag points at.

661 tests pass under both UTF-8 and `LC_ALL=C PYTHONUTF8=0`; ruff clean.

## [0.13.1] — 2026-09-30

The published v0.13.0 tag could not complete a single real action: a JavaScript SyntaxError on
every click, fill and select, and a bridge that accepted any local process. Both are fixed here,
with a regression test for each. Six findings from a two-phase review of the tag (585 tests, a
static pass, then eleven live missions with a real browser and a real key) are closed; the rest
stay open and are written down rather than hidden.

### Fixed

- **No action could be executed at all.** `target_expression` wrapped the action JSON in quotes,
  so the literal closed on its first inner quote and the browser answered
  `SyntaxError: missing ) after argument list` — every click, fill and select became a
  `StalePage`. Verified with `node --check`, and on a real example.com click. It broke both
  paths: the Chrome one and the sandbox one. Nothing covered it: 585 unit tests replace the CDP
  layer, and the broken half of `browser.py` is the half the suite does not touch.
- **A slow navigation ended the mission.** The stale-page recovery re-observed once, immediately.
  Any real navigation slower than that raised an uncaught `StalePage` and killed the run in
  transit — it killed the Wikipedia mission on its first run. It now waits out the page for up
  to ten seconds before giving the decision back, and still gives up after that.
- **Password fields were excluded from the action space.** `safe = e =>
  !['password','file','hidden'].includes(e.type)` meant the field never reached the element
  table, so no choice could reach it and every login was impossible: the agent typed the
  username and clicked Submit until the budget ran out. That is the failure the review
  attributed to the model choosing the wrong thing; it was a hard block, and no prompt change
  would have reached it. The field is now offered as a fill target, and its contents are masked
  in all three places a value travels: the action, the page key and the guard. An empty password
  still reads differently from a filled one, so a field is not typed into twice.
- **The bridge accepted handshakes with no `Origin`.** Firefox always sends one; no local
  process does, so its absence identifies an attacker rather than a client. A handshake without
  it now gets `403`, and the check is pinned by a regression test that replays raw sockets.
- **The README described a model the code stopped sending, and a test count that was a year of
  releases out of date.** Both now match the code, across the README, the Spanish guide, the
  `docs/` set and the three workflows that pinned the retired model — the rule this repository
  set itself in v0.12.5.
- **`ruff check .` was red on `main`.** The last commit before this one shipped a publish script
  with five lines over the limit, and CI runs the linter over the whole repository.

### Added

- **A budget for the executor prompt (H6).** Measured, not guessed: `scripts/bench_prompt_size.py`
  reproduces the dense page that cost 30.053 characters per decision and prints the whole
  trade-off curve. The element table was 88 % of it, and the real amplifier was not the 250
  elements but the options of native dropdowns — one `<select>` is one element that renders a
  line per option. Options now carry their own budget, and an option line no longer repeats its
  element's label, which was pure waste. On a dense page the prompt went from **21.233 to
  10.111 characters (~5.308 to ~2.528 tokens), −52.4 %**, and it stopped growing with the page.
  The cut is safe by construction: whole elements go or stay, a dropdown never loses an option,
  what the run just did is never dropped, and the omission is stated in the prompt instead of
  hidden.
- **Routing by task (H12).** A deterministic, network-free classifier decides whether a mission
  is worth a planner call: one is 402 ms, the other 21.045 ms, and the battery showed the
  planner inventing domains the user never wrote. `JEV_ROUTING=auto|always|never`; a caller
  that passes no goal keeps exactly the behaviour it had. The route and its reason are shown in
  the sidebar, because a cost decision the user cannot see is one they have to trust blindly.
  Measured honestly: it saves 1 of the 11 missions, which is why H9 (anchoring the plan to a
  domain whitelist) is the real fix.
- **`scripts/measure_routing.py`** replays the eleven live goals through the router and reports
  the calls avoided — and says in its own output that this is not the mission result, which needs
  the live battery and a real key.

### Changed

- A credential the goal does not supply is now an answer, not a failure. The text helper's
  `{"text": null}` is accepted for a secret field, and the run stops with a reason the user can
  act on instead of clicking Submit on an empty field. The sidebar shows it as a warning, not an
  error: a login without a password is not a malfunction. The agent does not invent, guess or
  reuse a credential, and what it types into a secret field is masked in the run history and in
  the panel — the browser still receives it, or nothing would be fixed.

### Not measured, and still open

- **The mission success rate.** The acceptance criteria for the routing work (≥ 60 % across the
  eleven missions) need the live battery with a real browser and a real key. Nothing in this
  release claims it.
- **H1/H2 in the sidebar's own copy, H4** (coverage: `demo.py` 0 %, `browser.py` 47 %),
  **H5** (two interpolations without `escape()` in `sidebar.js`), **H7** (`jev --help` hangs,
  no `argparse`), **H9** (the planner invents domains) and **H12**'s success target.
- **A `moz-extension://` origin from another extension is still accepted.** With a temporary
  add-on loaded through `about:debugging` the internal id changes on every load, so pinning it
  would break the documented flow. The right medium-term fix is the bridge token on by default,
  which the extension already sends and the host already compares.

## [0.13.0] — 2026-09-28

### Added

- **FastPath Architecture v1.0 (Local-First Action-Verifiable Agent).** Complete redesign replacing heavy HTML prompting with actionable AXTree candidate indexing, GBNF grammar decoding, 4-level self-healing cascades, and zero-LLM procedural replay.
- **Sistema 0: Caché Procedimental (`<1 ms`).** Deterministic SHA-256 fingerprint matching (`procedural_cache.py`) for verified action sequences, replaying frequent tasks in <1 ms with automatic postcondition invalidation.
- **Sistema 1: GBNF Grammar-Constrained Decoding.** Context-free grammar module (`gbnf_grammar.py`) constraining local model token sampling to guarantee 100% valid JSON payload structure.
- **Sistema 2: Selectores Auto-Reparables (Self-Healing Cascade).** 4-level fallback resolver (`self_healing.py` / `self_healing.js`) matching elements via ARIA accessibility, semantic text, AXTree relative position, and BBox visual coordinates.
- **Sistema 3: Extractor Ligero AXTree & MutationObserver.** Ultra-compact content script (`ax_tree.js` / `axtree.py`) extracting 3-6 KB payloads of actionable candidates and tracking DOM mutations incrementally.

## [0.12.9] — 2026-09-27

### Added

- **Full Text Selection & Copy Support in Extension Sidebar.** Added explicit `user-select: text` styling and high-visibility text selection highlighting across conversation messages, step logs, history, and status outputs.
- **`📋 Copy text` / Copiar texto Button.** Added a one-click copy button in the Conversation and Log tabs that copies the full view text directly to the system clipboard with instant visual feedback (`✓ Copied!`).

## [0.12.8] — 2026-09-27

### Fixed

- **Planner Navigation Rule.** Explicitly instructs the planning prompt that missions requesting a specific website, domain or URL (e.g., `"go, and open any game of friv.com"`) must make step 1 `"Navigate to <url>"`. This fixes the bug where the planner generated on-page search steps (like `"Click 'Explore' in the header"`) on DuckDuckGo and got stuck in DuckDuckGo link loops.

## [0.12.7] — 2026-09-27

### Changed

- **Default NVIDIA model switched to `meta/llama-3.2-11b-vision-instruct` (~450 ms response time).**
  Extensive live benchmarking across all 82 models in the NVIDIA NIM catalogue revealed that `z-ai/glm-5.3-flash` and `z-ai/glm-5.3` suffered from severe API queuing / timeouts (>300 seconds per call).
  In contrast, `meta/llama-3.2-11b-vision-instruct` on NVIDIA NIM delivers sub-second latency (464 ms average routing, 540 ms average text extraction) with **100% accuracy** on the project's routing battery and field filling contracts.
- **Automated migration for existing setups.** Starters (`start-host.sh`, `.command`, `.bat`) and `parameters.migrate_old_default_model()` automatically rewrite `.env` files and saved selections pinned to `z-ai/glm-5.3` or `z-ai/glm-5.3-flash` to `meta/llama-3.2-11b-vision-instruct`.

## [0.12.6] — 2026-09-27

The double-click now **checks the installation instead of assuming it**. Until now the starters asked one
question — "does `.venv` exist?" — and if the answer was yes they never looked again, so a machine stayed
frozen on the first version it installed, an environment built by an older Python kept being used, and a
half-finished install looked exactly like a finished one.

### Added

- **`scripts/install.py`, the smart installer.** It reads what is really there (version in the
  environment, the Python it was built by, whether the interpreter still starts, whether the command
  files exist, whether Laya is in it, what Firefox was told) and then either reuses it — printing
  `nothing downloaded` — or **removes it and installs this copy**. No patching on top of an old install.
  `--check` reports without changing anything, `--dry-run` prints the plan, `--reinstall` forces a clean
  install, `--quiet` only speaks when there is work to do.
- **Your data is never part of "the installation".** `.env` (keys), `artifacts/` (run history and
  recordings), `workspace/` (files the agent made), `.git/` and the extension are named in a
  `NEVER_REMOVE` guard that refuses to delete them even if a plan asks for it, and anything outside the
  project folder is refused outright. The final line of every run says it: *"Your keys (.env), your run
  history (artifacts/) and your workspace files were not touched."*
- **Laya is never installed twice.** If the environment being replaced had Laya, the package goes back
  into the new one — its 644 MB of weights are already in the shared cache, so nothing large is
  downloaded — and the install is recorded in `laya-install.json`, which is the file the agent itself
  reads before installing Laya on the first start. If Laya was not there, the installer stays out of it
  and says who will take care of it.
- **A broken environment is repaired instead of crashing.** A previous install can leave `.venv` behind
  with an interpreter that no longer starts (Python removed, folder moved, half-deleted install). The
  installer runs it to find out; if it cannot start, the environment is rebuilt. Found by running the
  installer for real, not by reading it.
- **Firefox is registered once, not on every start.** The check reads the native-messaging manifest file
  and the installed package, so a machine that is already registered is left alone, and one that points
  at another copy of the agent is taken over with the reason printed.

### Changed

- **The three starters (`start-host.sh`, `.command`, `.bat`) hand the whole job to the installer.** No
  second copy of the logic, no `pip` calls in the shell scripts, no separate registration step: one
  place decides and one place reports. The interpreter they found is still the one that builds `.venv`.
- **The docs stop promising things the scripts do not do.** The walkthrough now shows the message the
  starter really prints, the update section says the previous installation is removed rather than
  patched, and both mention that a double-click on an up-to-date folder downloads nothing.

### Tests

- `tests/test_install.py`: 46 tests over the decisions (create / reuse / rebuild, with the reason),
  the never-remove guard, Laya remembered, `--check` and `--dry-run` writing nothing, the CPU-only torch
  index, the registration rules, and the starters no longer installing anything themselves.

## [0.12.5] — 2026-09-27

Nothing that used `z-ai/glm-5.3` still does — including installs that already exist.

### Changed

- **The whole repository was grepped, not the obvious file.** What still pointed at the old model:
  the *no key* help text, the CI job that runs a live mission (`e2e-flights.yml`), a comment in the
  provider check, the price-table example, and the docs.
- **An install that already exists is moved too, in both places a model can be pinned.** The
  starter now moves the line in `.env` (the same step that fixed the wrong `zai/` id) to
  `z-ai/glm-5.3-flash`, keeps `.env.bak`, and `JEV_KEEP_MODEL=1` opts out. A selection saved by the
  panel is migrated once, the reason is recorded in the config file (`models_migrated_from`), and
  only the exact old pair on NVIDIA is touched — any other model the user chose is left alone.
  Twelve tests pin all of it (`tests/test_flash_everywhere.py`).

- **The live end-to-end job runs the shipped model too** (`e2e-flights.yml` plans on
  `z-ai/glm-5.3-flash`), so the workflow that drives a real browser with a real key cannot quietly
  keep testing another model.
- The *no key* help text, the provider-check comment, the price-table example and the docs name the
  model that actually runs.

### Kept on purpose

Historical measurements and the comparison profiles still mention `z-ai/glm-5.3` — a measurement
that names the model it measured is not a residue, it is the evidence. `scripts/bench_profiles.py`
keeps `nvidia-big-none` and `nvidia-hybrid` for exactly that comparison.

## [0.12.4] — 2026-09-27

Every cloud role moves to `z-ai/glm-5.3-flash` with thinking off, on the user's explicit instruction.

### Changed

- **The derived default on an NVIDIA key is `z-ai/glm-5.3-flash` with `reasoning=none`, in all three
  roles.** Thinking was already off by default; the model is the change. It is one line per role to
  move back (`PLANNER_MODEL` / `POLICY_MODEL` / `TEXT_MODEL`, or the **Models & parameters** panel),
  and `scripts/bench_profiles.py` now carries both arrangements as profiles — `nvidia-current` is
  whatever ships, `nvidia-big-none` is the same setup on the full-size model — with a test that fails
  if the profile and the shipped table drift apart.

### Measured cost of that choice (same key, same day, `scripts/bench_profiles.py`)

| Role | `z-ai/glm-5.3` (thinking off) | `z-ai/glm-5.3-flash` (thinking off) |
| --- | --- | --- |
| Planner | 25.7 s median, worst 159.6 s, 6/9 valid plans | 75.7 s median, worst 104.7 s, 2/2 valid |
| Executor (every step) | **1.9 s median** (n=12), worst 9.8 s | **37.4 s median** (n=12), worst 107.7 s |
| Text writer | **1.4 s median** (n=4), worst 1.8 s | **56.1 s median** (n=4), worst 172.8 s |

Flash is the smaller model of the same family and waits in the same free-tier queue, so what changes is
the queue's mood, not the parameter count: on 25 September the same comparison came out 85 s / 43 s / 42 s
for flash. Both tables are in `docs/providers.md`, and neither is a guess.

## [0.12.3] — 2026-09-26

The version that stops a run from dying of capacity, and stops every decision from costing a
cloud call.

### Added

- **The key card asks for one key: NVIDIA.** Groq is not offered any more (its free tier is
  8 000 tokens/minute and answers `429` mid-mission), and a Groq or DeepSeek key that is already in
  the environment is *named* in the card — "Also on this machine: a **Groq** key (`GROQ_API_KEY`).
  Not used — its free tier is 8 000 tokens/minute and answers 429 mid-mission." — instead of being
  left invisible and inert. Both stay fully supported when they are the key you have, or when a role
  names them.
- **Laya is installed by default, in the background, on the first start.** It answers the two
  decisions the agent makes before any model call — which loop a mission needs, and which
  procedural package should guide it — in any language, locally, with no key. Be precise about
  what that is worth: without Laya those decisions are **already local** (a keyword router), so
  this is quality, not speed. The CI battery with the real weights measures it: **Laya decides on
  its own in 6 of 14 missions**, abstains on the rest (its calibrated confidence stays below the
  gate), and the keyword fallback answers those — **14/14 correct together, 195 ms per decision
  on a 2-core CPU**. What it adds over keywords is language, and what it costs the mission is
  nothing: the install runs in a thread while the agent is already usable, reports what it
  is doing in the sidebar (its own line under the model row — never the readiness dot: the agent
  runs without it, only slower), and records the outcome next to the key file so a machine that
  is offline does not pay for the attempt at every launch. `JEV_LAYA=off` means "do not use it",
  `JEV_LAYA_AUTO=off|retry|force` controls the install itself, and **nothing is downloaded in CI
  or inside the test suite** — that is a gate in the code, not a promise in a document (a
  background install during a test run took the machine's memory down here, which is how the
  gate got written).
- **`install-laya.sh` / `.command` / `.bat` now run the same code as the automatic path**, so
  the CPU-torch choice on Linux and Windows (the CUDA build on PyPI is ~2.5 GB) and the wording
  of every message are identical by construction.

### Fixed

- **A rate limit no longer kills a run: the agent waits for as long as the provider asked.**
  Groq answers a spent free tier with `429` and the sentence "Please try again in 1.319999999s";
  the retries were 0.5 s and 1 s apart, so the agent gave up a third of a second before the
  provider would have answered. It now reads `Retry-After` first and the provider's own words
  second, waits that long (plus 0.25 s of slack, capped at 30 s), gets one more attempt than a
  wire failure, and if it still runs out says so: *this is capacity, not a key problem*, how long
  it waited in total, and what to do. `tests/test_rate_limits.py` pins the wording and the timing.
- **The local engine refuses to load where it would be killed.** The multilingual checkpoint is
  644 MB of weights plus torch, and loading it on a 2 GB machine gets the process OOM-killed —
  not a failure Python can catch. The loader now asks for 1.6 GB of free memory first (on Linux
  what the kernel reports as available) and, if there is not enough, says exactly that in the
  sidebar instead of dying.
- **One key now means one provider.** With both keys present the agent no longer plans on NVIDIA
  and executes on Groq: that mix is the one that failed mid-mission with `429` and the
  provider's "upgrade to Dev Tier" message. NVIDIA runs all three roles (measured: executor 2.3 s
  median against 36 s), Groq is still first-class when it is the key you have, and any mix stays
  available by setting `POLICY_*` / `TEXT_MODEL_*` by hand.
- **The HTTP client's timeout is no longer frozen by whoever used it first.** Setting an
  attribute on the shared client left a bound method pointing at the client of that moment, so
  `JEV_HTTP_TIMEOUT` stopped being read and a test inherited a 17 s patience it never asked for.
  Tests now replace the client whole, which is what the module always documented.

## [0.12.2] — 2026-09-25

The version that answered a speed question with a measurement, and fixed what the measurement found.

### Added

- **The planner asks twice when its first reply is unusable.** Measured: one planner call in ten came
  back **empty** from NVIDIA's GLM endpoint, at the same rate with 1024 and with 2048 tokens (so it is
  not a truncated answer, and a bigger budget does not fix it). The planner runs once per mission, so a
  second question is cheap and keeps the checklist; a connection failure is not asked again, because the
  HTTP layer already spends its three attempts on the wire.
- **A CI job that proves the one-key promise.** `.github/workflows/single-key-check.yml` runs the self-test twice —
  NVIDIA key only, Groq key only — with every role and model override removed, so the derivation has to configure
  all three roles on its own; a role that cannot run fails the job, and a role that is merely rate-limited is
  reported as capacity. `scripts/single_key_check.sh` is the script behind it, runnable locally too.
- **A bench that measures candidate model profiles instead of guessing.** `scripts/bench_profiles.py`
  (`.github/workflows/model-profiles.yml`, one job per candidate) reports median and worst latency per role
  from the prompts and token budgets the agent itself uses, plus three objective quality checks: a valid plan,
  the routing decision on the project's own 241-mission battery, and the exact value the goal supplied for a
  field. It was written to answer one question — *"what if we limit the AI: glm-5.3 flash without reasoning?"* —
  and the answer stops the guesswork: flash with thinking off was the **slowest** NVIDIA candidate measured
  (85 s / 43 s / 42 s medians for planner, executor and text against 30 s / 36 s / 9 s for the shipped
  defaults), because what costs time on that free tier is the queue, not the parameters. `docs/providers.md`
  carries the table.

## [0.12.1] — 2026-09-25

### Fixed

- **"Model connection failed" no longer means "your key is wrong", and it stops happening so often.** A user whose
  single NVIDIA NIM key was working saw the planner and the executor red with that sentence while the text role, on
  the same key, answered after 37.8 s — three 25 s attempts had gone by without one byte, which is a free endpoint
  waking up. The wait is now 60 s per attempt (`JEV_HTTP_TIMEOUT` overrides it), the retries stay, and the message
  says which failure it was: nothing answered in time — *"the endpoint is slow or busy, not a rejected key"* — or
  the connection could not be established (network, proxy, VPN). A key the provider really rejects is still answered
  with its own HTTP 401 and named as a key problem.
- **The panel no longer calls a configured model "missing".** With a model that had not answered, the verdict read
  *"Not ready: Planner, Executor have no model"* — with `Planner · nvidia:z-ai/glm-5.3` printed right above it. That
  sentence sends you looking for a key you have already pasted. "No model" is now reserved for a role that has
  none; a role that has one and stayed silent says *"did not answer — the model is configured, the endpoint is slow
  or unreachable. Press Test setup to try again."*
- **A self-test is no longer a frozen panel.** The wait is shown while it happens: the box reads *"Testing every
  model connection… 12 s"* with the seconds ticking, the button is disabled so a second press is not dropped
  silently, and every role is announced as it starts and finishes (`Testing Executor (nvidia:openai/gpt-oss-20b)…`,
  *answered in 1 282 ms*, or *FAILED — no answer within 60 s*). The roles are asked one after another, which is the
  honest reason a slow free tier takes minutes.
- **`check_providers` takes an optional `on_event` callback**, so any caller (the host today, a script tomorrow) can
  report progress; a caller that passes nothing gets exactly the report it always got.
- **The live-provider check no longer goes red when an endpoint simply stalls.** The run of 25 Sep was red on
  NVIDIA NIM leaving `openai/gpt-oss-20b` unanswered for 76 s, while the same provider answered for
  `z-ai/glm-5.3` in the same run and re-running it unchanged went green. A probe that comes back with no verdict
  at all is now asked again — up to three attempts, and the number of attempts is printed — while a real verdict
  (parameters verified or refused) is reported as it came, first time. The parameter check stays a hard one.
- **A panel test that could fail on a slow machine is now decided by handshakes.** The test for a verdict
  arriving from before the key slept 0.4 s hoping the key would be saved inside that window; on the Windows
  runner it was not, so the check already in flight answered first and the test read that as a stale verdict
  being published. The key save and the check's answer are now ordered with events, and the test additionally
  asserts the discarded verdict was never broadcast to the sidebar at all.

## [0.12.0] — 2026-09-25

You can now see what the agent is doing instead of guessing.

### Added

- **A verdict before you run.** The sidebar answers "can it run?" in one line: green with the model each role will
  use, or red naming the roles that have no model and how to fix it. It used to be three red dots and a paragraph to
  interpret.
- **A progress bar that does not invent numbers.** It is a real percentage when the host measured one (the plan
  position in browser mode: *step 2 of 3*); otherwise it moves while the agent works and the meta line reports what is
  actually known — step count, the cap (`step_budget`, 25), elapsed time, tokens.
- **A conversation view.** What you asked, what the agent said it would do, each tool call with the result it returned,
  the answer and the closing line, in the order it happened. The model's streaming output grows in place instead of
  appearing and vanishing whenever a step arrived.
- **A log view.** Every event with its timestamp and level (`SYSTEM` / `TOOL` / `OK` / `WARN` / `ERROR`), one line
  each, including the failures that used to leave no trace.
- **A headless harness for the panel.** `tests/sidebar_harness.mjs` runs `sidebar.js` against a stub DOM and
  `tests/test_sidebar_activity.py` asserts on what a user would see: the order of the conversation, the indeterminate
  bar when there is no total, a determinate one when there is, the log contents, the verdict. The panel's behaviour is
  tested now instead of assumed (it skips where Node is missing).

### Fixed

- **A saved key can no longer hide in another file.** The key file was resolved relative to the process working
  directory, and Firefox chooses that directory when it launches the native host: a key pasted in the sidebar (or
  typed into the checkout's `.env`) was invisible the next time the agent started from elsewhere, which showed up as
  *"nothing is configured"* with a key already saved. Reads and writes now resolve one file per installation — the
  checkout's `.env`, `JEV_ENV_FILE` to move it, `~/.config/jev-ultrafast/.env` for a package install — and the panel
  and the startup line both name that exact path, so the answer to "where did my key go?" is on screen.

### Changed

- **A role is named the same everywhere.** The messages said "the policy role" while the panel said "Executor", which
  read like two different problems; both now use the panel's names (Planner, Executor, Text writer).
- The host publishes `step_budget` in its state so the panel can say "at most 25 steps" without hardcoding it.

## [0.11.0] — 2026-09-25

The version that took the project from "assumed macOS" to the systems people actually run, and that proves
it instead of claiming it.

### Added

- CI that runs the real thing: openSUSE **Leap 15.6** and **Tumbleweed** containers (zypper, the starter,
  install, full suite), a **Windows** job (native messaging over stdio, registry, key handling, starter)
  and Python **3.11 / 3.12 / 3.13** — plus the whole suite again under a non-UTF-8 locale.
- Platform tests that drive the actual starters against fake per-distro interpreters, and per-distro Python
  install hints inside the starter itself.
- Windows interpreter discovery through the `py` launcher, with a warning when `python` is the Microsoft
  Store stub.

### Changed

- `requires-python` lowered to **3.11** (what current distributions still ship as an option), and the
  starter looks for `python3.13` → `python3.12` → `python3.11` → `python3` instead of assuming the first
  `python3` it finds is new enough.
- The user-facing name is **AI Agent for Firefox**; release assets are named `ai-agent-for-firefox-*`;
  the repository is `firefox-ai-agent`.
- README rewritten to describe this project rather than the upstream one it started from.

### Fixed

- Every text file the agent reads or writes declares UTF-8. Without it, a Windows machine under a legacy
  code page could not even start the agent (`snapshot.js` contains non-ASCII characters).
- `.env` is read as `utf-8-sig`, so a key saved by Notepad with a BOM is no longer invisible to the agent.
- SUSE package names in the docs and in CI (`python312`, never `python3.12`).
- A connection-level failure now retries on the plain request path exactly as the streaming path already
  did — a model being woken up no longer kills a run on the first attempt.
- `[project.urls]` no longer swallows the `dependencies` line, which broke every fresh install.


## [0.10.0] — 2026-09-25 — Firefox starts the agent itself

The double-click became optional: Firefox launches the local host through native messaging, so opening the sidebar *is* starting the agent.

### Added

- Native messaging: a per-user manifest registered once (`jev-register-host`, with `--status` and `--unregister`), on Linux, macOS and Windows (registry, no admin rights).
- The host accepts only this add-on (`allowed_extensions`), and it stops when Firefox closes — nothing keeps running in the background.

### Changed

- The double-click starter stays as the documented fallback, and as the route on snap/Flatpak Firefox.

### Fixed

- Registration that a browser can actually launch, after CI proved the first version could not.

## [0.9.0] — 2026-09-25 — The setup happens in Firefox

Setup stopped being a terminal task: the sidebar asks for one key, checks it and configures everything, which is what makes a no-console install real.

Between v0.4.0 and this release, versions 0.5.0 to 0.8.0 shipped inside the code line without a GitHub release: the parameter surface became the model's, the isolated Neko browser, permission centre and audit log landed, local LLM runtimes were dropped on purpose, and one free key started configuring all three roles.

### Added

- Graphical setup in the sidebar: paste one free API key, it is validated against the provider, written to `.env` and applied immediately — no restart, no file editing.
- The panel reports what the key bought (`planner … · policy … · text …`) and reopens the card with the provider's exact error when a key is rejected.
- CI guard that fails if the double-click starters lose their executable bit.

### Fixed

- The executable bit itself, which the guard immediately caught.

## [0.4.0] — 2026-09-23 — Quality at scale: 241-mission battery, provider bench and a live E2E

The version that answered the question the previous ones left open: does any of this still hold at scale? It answered with real resources in CI, not with simulations.

### Added

- Routing battery: 241 labelled missions — 6 languages × 36 core missions plus 25 adversarial ones (mixed intent, multi-clause, bilingual, telegraphic, typos) — scoring raw Laya against the shipped stack and tracking *dangerous* confusion above all.
- Provider bench that probes live endpoints instead of trusting documentation.
- Live flights mission in CI: real providers, real browser, a token budget per provider and a report that survives even a failed run.

### Fixed

- Tool signals outrank a confident page answer; suggestions complete before the model is asked to choose; the demo's expired date no longer makes the mission impossible.

## [0.2.0] — 2026-09-23 — The whole fork merged: MVP-1 to MVP-6, Laya and the security pass

The first version containing all of the fork's work in one place (PR #1: 27 commits, about 9,100 lines).

### Added

- Model catalogue and provider layer (MVP-1/2): presets, key handling, per-role selection.
- Tools, skills and the orchestrator (MVP-3/4): web search, files, document parsing, terminal commands.
- Command approvals (MVP-4): side-effecting commands pause for an explicit yes, and a timeout means no.
- Streaming responses, saved profiles and run history (MVP-6).
- Laya integration with a measured confidence gate, published next to the vendor claims.

## [0.1.0] — 2026-09-22 — Pluggable providers, planner–executor loop, Firefox sidebar

First release of this fork: the reference browser loop, rebuilt around providers you choose and a Firefox sidebar that drives the tab you are looking at.

### Added

- Pluggable model providers (NVIDIA NIM, Groq, OpenRouter, OpenAI, Anthropic, DeepSeek, Together, Mistral, xAI, Gemini, or your own gateway) with a planner–executor split.
- Firefox WebExtension sidebar and the local bridge it talks to over loopback.
- Double-click starters for Windows, macOS and Linux, plus a Spanish guide written for people who do not use a console (EMPEZAR-AQUI.md).
- CI with automatic release artifacts (source archive, `.xpi`, wheel).
- Laya, the open System One decision engine (Convai Innovations, Apache 2.0), as the optional local router.
- Production security pass: secret redaction, an action audit log, and CPU/memory/file limits on approved commands.


---

*The pattern is simple: a version ships when its own claims can be checked — by tests in the repository, or
by CI on the systems it says it supports.*
