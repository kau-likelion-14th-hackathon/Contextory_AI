# Refactor brief: PR analysis stuck in PENDING (OpenAI TPM limit)

## Context for whoever picks this up

This is a task brief, not a spec to implement literally — read it, form your own
plan, and check it against the current code before changing anything (the exact
line numbers below may have drifted). It documents a real incident reproduced
against this repo on 2026-08-18, with root cause identified. Spring Boot side
(`Contextory_BackEnd`) was checked via Swagger and is not implicated — the
`POST /internal/v1/analyses` call succeeds (`202 Accepted`), the bug is entirely
on this side.

## Symptom

An `AiAnalysis` row on the Spring Boot side never leaves `PENDING`. FastAPI
accepted the job (202 + `job_id`), but the analysis never completes and no
callback ever arrives.

## What actually happened (from FastAPI server logs)

```
INFO:     52.78.137.106:0 - "POST /internal/v1/analyses HTTP/1.1" 202 Accepted
[Analysis Job Failed] job_id=rag-job-031f5f3c292f analysis_id=1
...
openai.RateLimitError: Error code: 429 - {'error': {'message': 'Request too large
for gpt-4o in organization org-QiZml4Na5szQSqhuoKGxvzKS on tokens per min (TPM):
Limit 30000, Requested 100791. The input or output tokens must be reduced in
order to run successfully. ...', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}

[Analysis Callback Unreachable] job_id=rag-job-031f5f3c292f analysis_id=1
callback_url=http://localhost:8080/internal/v1/analyses/1/callback
...
httpx.ConnectError: [WinError 10061] 대상 컴퓨터에서 연결을 거부했으므로 연결하지 못했습니다
```

Two distinct failures happened in sequence on the *same* job:

### 1. Primary root cause — LLM request exceeds the org's TPM limit

`analyze_pr_for_callback()` in `services/analysis_service.py` builds a prompt via
`_gather_grounded_context()` → `build_grounded_prompt()`
(`services/prompt_builder.py`) and sends it to `client.chat.completions.create()`
with **no token budgeting anywhere in the path**:

- `build_diff_content()` (`services/analysis_service.py`) concatenates every
  changed file's full `patch` string with no size cap, no per-file cap, and no
  filtering of large/generated/lockfile diffs.
- `build_grounded_prompt()` (`services/prompt_builder.py`) truncates retrieved
  *context* snippets (`pr_diff[:300]`, `source_code[:500]`) but embeds `pr_diff`
  (the actual PR diff being analyzed) **verbatim, uncapped** — this is the
  dominant contributor when a PR touches a lot of code or includes a large
  generated/vendored file.
- Nothing in the pipeline counts tokens before calling OpenAI. The observed
  request was ~100,791 tokens against a 30,000 TPM org limit — over 3x the
  limit in a single call.

This is a **request-shape bug**, not transient rate-limiting: retrying the same
request will fail identically every time. The job is caught by the generic
`except Exception` in `_run_analysis_job` (`routers/internal_analysis.py:67`),
which correctly reports it as `FAILED` via callback — but see #2.

### 2. Secondary failure — the FAILED callback itself couldn't be delivered

After the job was marked `FAILED`, `_run_analysis_job` still tried to notify
Spring Boot via `send_analysis_callback()` (`services/callback_service.py`), which
POSTs to `request.callback_url`. In this run that URL was
`http://localhost:8080/internal/v1/analyses/1/callback`, and the FastAPI process
got `ConnectError: connection refused` after 3 retries with backoff.

Per this repo's own `CLAUDE.md` ("Cross-service deployment reachability"), this
is a **known, already-documented network-topology issue**, not a code bug: if
this FastAPI process isn't running on the same host/network as `localhost:8080`
resolves to for Spring Boot, the callback can never land, by design of how
`callback_url` is used. `_run_analysis_job` already treats this as best-effort
(logs and swallows `httpx.ConnectError`/`ConnectTimeout` with an explicit hint)
and the job's terminal status is still persisted in `ai_analysis_jobs`, retrievable
via `GET /internal/v1/analyses/{job_id}`.

**Do not "fix" #2 by making callback delivery block or crash the background
task** — that behavior is intentional (see the docstring on `_run_analysis_job`
and the "Cross-service deployment reachability" section of `CLAUDE.md`). The
actual gap this incident exposes is that **Spring Boot has no fallback poller**:
since it relies solely on the callback and never calls
`GET /internal/v1/analyses/{job_id}`, a lost callback strands the row in
`PENDING` forever with no self-healing path. That reconciliation gap is a
`Contextory_BackEnd`-side follow-up, not something to build into this repo —
flag it back if you want, but out of scope here.

## Scope of this refactor: fix #1

The job is to make sure a single analysis request **never exceeds the model's
practical token budget**, and that when a PR is genuinely too large to analyze
in one shot, the system produces a clean, informative `FAILED` result (or a
degraded-but-useful one) instead of relying on OpenAI to reject it.

### Relevant files
- `services/analysis_service.py` — `build_diff_content()`, `analyze_pr_for_callback()`, `analyze_pr_pipeline()` (both pipelines share diff-building; keep both consistent)
- `services/prompt_builder.py` — `build_grounded_prompt()`
- `core/config.py` — `Settings` (add any new tunables here, following the existing pattern)
- `routers/internal_analysis.py` — `_run_analysis_job()` (only if the failure-classification/error-message logic needs to change)

### Requirements / acceptance criteria

1. **Count tokens before calling OpenAI**, not after catching the 429. Use a
   tokenizer appropriate for the configured `LLM_MODEL` (e.g. `tiktoken`) rather
   than assuming a fixed org TPM number — the actual limit is account/tier-specific
   and can change, so the guard should target a configurable budget
   (new `Settings` field, e.g. `LLM_MAX_PROMPT_TOKENS`) rather than a hardcoded
   30000.
2. **Reduce oversized input before it reaches the LLM**, don't just detect and
   fail. At minimum, prefer some combination of:
   - Capping each file's `patch` contribution (similar to how context snippets
     are already truncated to 300/500 chars in `prompt_builder.py`) rather than
     including full patches unconditionally.
   - Excluding or summarizing low-value diff content — lockfiles, generated
     code, minified/vendored files, pure whitespace/rename-only changes — from
     `build_diff_content()`. `PullRequestFile.change_type` and `file_path` are
     available to make this decision.
   - If the diff still doesn't fit after trimming, either truncate with a clear
     `"... (truncated, N files omitted)"` marker in the prompt, or degrade to
     analyzing only the highest-signal files — whichever keeps the LLM output
     honest about what it actually saw. Don't silently drop content and let the
     LLM report on a partial diff as if it were complete.
3. **If a request still can't be brought under budget, fail fast with a specific,
   actionable error** — don't let it reach `client.chat.completions.create()`
   and bubble up as a raw `openai.RateLimitError`. The `FAILED` callback's
   `error_message` (capped at `MAX_CALLBACK_ERROR_LENGTH` in
   `routers/internal_analysis.py`) should say something like "PR diff too large
   to analyze (N tokens, limit M)" instead of surfacing the raw OpenAI error
   string.
4. Apply the same treatment to **both** `analyze_pr_pipeline()` (sync API) and
   `analyze_pr_for_callback()` (async/callback API) — they share
   `build_diff_content()`/`_gather_grounded_context()`/`build_grounded_prompt()`,
   so fix it at the shared layer where possible rather than duplicating logic
   per-pipeline, consistent with how this repo already keeps retrieval/prompt
   logic unified (see `CLAUDE.md` § "Two parallel API surfaces").
5. Add/update tests. Check `tests/` for existing coverage of
   `build_diff_content`/`build_grounded_prompt`/`analyze_pr_for_callback` and add
   cases for: a diff that fits, a diff that needs trimming, and a diff that's
   still too large after trimming (should fail cleanly, not call OpenAI).

### Explicit non-goals (don't touch)

- Callback delivery/retry logic in `services/callback_service.py` and the
  connection-refused handling in `_run_analysis_job` — already correct and
  intentional per this repo's `CLAUDE.md`.
- `job_store.py` status-transition rules (terminal-state handling, zombie
  reaping) — unrelated to this bug.
- Cross-service network reachability (tunnels/public domains for `callback_url`)
  — that's a deployment/config concern for whoever operates both services
  together, not something this repo's code can fix.
