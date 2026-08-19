# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Contextory AI Service — the AI/RAG worker for the Contextory project. A FastAPI service that receives PR data and repository source from a Spring Boot backend, retrieves relevant context from PostgreSQL (`pgvector`), and produces LLM-generated code review analysis. Not a standalone product; it's a backend-for-a-backend, callback-driven service.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the dev server (requires .env configured, see below)
uvicorn main:app --reload

# Run tests (PostgreSQL tests auto-skip if no DB connection is available)
pytest
pytest tests/test_job_store_pg.py -v          # single file
pytest tests/test_job_store_pg.py::test_create_job_persists_processing_status  # single test
```

There is no configured linter/formatter in this repo (no pyproject.toml/setup.cfg) — match existing code style.

### Environment setup

Copy `.env.example` to `.env`. Key vars: `POSTGRES_*` (DB connection), `OPENAI_API_KEY` (embeddings + GPT-4o), `INTERNAL_API_KEY` (shared secret for Backend<->AI internal API), `JOB_TIMEOUT_MINUTES`/`JOB_RETENTION_DAYS` (async job cleanup policy).

The DB requires the `pgvector` Postgres extension (`CREATE EXTENSION IF NOT EXISTS vector;`) — see README.md for Windows-specific install steps. Tests that need a live PostgreSQL connection skip themselves (`_db_available()` check) rather than failing when no DB is reachable.

## Architecture

### Auth boundary

Every route in `routers/analysis.py`, `routers/indexing.py`, and `routers/internal_analysis.py` requires the `X-Internal-Api-Key` header (checked via `core/security.py:verify_internal_api_key`, wired as a router-level `dependencies=[Depends(...)]`). There is no other auth layer (no JWT, no IP allowlist) — this is the only thing standing between the public internet and endpoints that trigger paid OpenAI calls or write to the vector DB. This matters more than it looks: this service is meant to be reachable by a separate Spring Boot backend (`com.kbj.contextory`) over the network (in practice, potentially tunneled to a public domain), and both `/api/v1/analyze/pr` and `/api/v1/repos/index` used to ship with zero auth before this was added — treat any new router the same way rather than assuming "internal-sounding" routes are safe by default.

### Two parallel API surfaces

The service exposes both a legacy synchronous API and a newer async/callback API — both are live and share the same retrieval/prompt internals, but have **different response contracts**:

- `POST /api/v1/analyze/pr` (`routers/analysis.py`) — synchronous, snake_case JSON, returns `PRAnalysisResponse` directly (summary/risk_score/reviews/evidences/confidence). This is the original contract; kept for backward compatibility.
- `POST /internal/v1/analyses` (`routers/internal_analysis.py`) — the current path used by the Spring Boot backend. Returns `202 Accepted` + `job_id` immediately, runs analysis via `BackgroundTasks`, and delivers the result asynchronously to `callback_url`. Requires `X-Internal-Api-Key` header auth. JSON is camelCase (via `CamelModel` in `models/schemas.py`, using pydantic's `to_camel` alias generator — define fields in snake_case, they serialize as camelCase automatically).
  - `GET /internal/v1/analyses/{job_id}` — poll job status.
  - `POST /internal/v1/analyses/maintenance` — reaps zombie jobs (PROCESSING past `JOB_TIMEOUT_MINUTES`) and purges old terminal rows (past `JOB_RETENTION_DAYS`, disabled by default). Nothing inside this service calls it on a timer — it's meant to be invoked periodically by a Spring Boot–side scheduler, but as of the Spring Boot repo's own docs, no such scheduler is wired up yet. Until it is, zombie jobs are only ever resolved lazily (when someone happens to `GET` that specific job).

`services/analysis_service.py` holds `_gather_grounded_context()`, the shared retrieval+prompt-building step both pipelines call before diverging into their own LLM system prompts and response shapes. When changing retrieval/prompt logic, change it there so both APIs stay consistent; when changing response shape, know which endpoint's contract you're touching.

### RAG pipeline flow

1. **Query translation** (`services/translation_service.py`) — PR title/description translated to an English search query (embeddings model performs best in English).
2. **Multi-source retrieval** (`services/retrieval.py`):
   - `retrieve_contexts()` — raw SQL cosine-similarity search against `code_review_vectors` (a global, cross-project code-review knowledge base — not repo-scoped, no `repo_name` filter).
   - `retrieve_repo_contexts()` — LlamaIndex `PGVectorStore` query against `repo_code_vectors` (physical table `data_repo_code_vectors`), filtered by `repo_name` metadata to isolate the current project's own indexed source from other repos.
3. **Context filtering** (`services/context_filter.py`) — drops low-similarity/irrelevant contexts below `SIM_THRESHOLD`.
4. **Prompt construction** (`services/prompt_builder.py`) — builds a grounded prompt from the diff + filtered contexts.
5. **LLM call** — OpenAI `chat.completions.create` with `response_format={"type": "json_object"}`, `LLM_MODEL` (default `gpt-4o`). The two pipelines use different system prompts/output schemas (see above).
6. **Confidence scoring** (`services/confidence.py`) — only used by the sync `/api/v1/analyze/pr` path.

### Repository indexing (separate from PR analysis)

`POST /api/v1/repos/index` (`routers/indexing.py` → `llamaindex/pipeline.py`) ingests source files sent by the backend, chunks them (LlamaIndex `SimpleNodeParser`, 512/50 overlap), embeds, and upserts into `repo_code_vectors`. It also handles deletions (`deleted_files`) via a **direct psycopg2 connection** against the physical `data_repo_code_vectors` table — this bypasses SQLAlchemy/LlamaIndex because psycopg2 can't parse the `postgresql+psycopg2://` DSN, so it connects with individual params instead. When touching indexing/deletion, keep the physical table name (`data_<REPO_CODE_TABLE_NAME>`) and the insert/delete paths in sync — they must target the same table.

`services/repo_index_service.py` exists but is **not wired to any router** — it's dead code from before `llamaindex/pipeline.py` took over this responsibility; don't assume it runs.

### Table name configuration

`core/config.py` centralizes the two pgvector table names as settings (`REPO_CODE_TABLE_NAME`, `CODE_REVIEW_TABLE_NAME`) rather than hardcoding them per call site. `repo_code_vectors` is LlamaIndex-managed (physical table gets a `data_` prefix automatically); `code_review_vectors` is raw-SQL managed (`scripts/index_to_pg.py`), table name used as-is. Keep this distinction in mind when writing new queries against either table.

### Async job persistence (`services/job_store.py`)

Job state for the async analysis API used to be an in-memory dict, which broke under multi-worker Uvicorn (workers don't share memory → 404s from the "wrong" worker) and lost data on restart. It's now persisted to a PostgreSQL table (`ai_analysis_jobs`), created on startup via `init_job_store_table()` (`main.py` lifespan) since there's no Alembic/migration tool in this repo. It uses `CREATE TABLE/INDEX IF NOT EXISTS` DDL directly rather than SQLAlchemy's `checkfirst=True` — `checkfirst` does a check-then-create in Python (not atomic), which races when multiple `uvicorn --workers N` processes call this at startup simultaneously and can crash a worker's boot with a `DuplicateTable` error. If you add another table that needs this bootstrap pattern, follow the `IF NOT EXISTS` approach, not `checkfirst`.

The module's DB access is synchronous SQLAlchemy (`SessionLocal`), wrapped in `async def` functions via `run_in_threadpool` so the event loop isn't blocked — there's no async DB driver in this codebase, so follow this wrap-sync-in-threadpool pattern rather than introducing one.

Status transitions are constrained: once a job reaches a terminal status (`COMPLETED`/`FAILED`), it cannot be reverted to `PROCESSING` — but terminal-to-terminal transitions are allowed (a job timed out to `FAILED` can still later be overwritten by a genuine `COMPLETED` if it finishes late). Zombie detection (`PROCESSING` past `JOB_TIMEOUT_MINUTES`) happens both lazily (on `GET` of a specific job) and via the batch `reap_stale_jobs()` maintenance call — a stale job can flip to `FAILED` just from being read.

### Response contract stability

Response field names/shapes for both the sync API and the internal camelCase API are contracts the Spring Boot backend depends on directly (no shared schema/codegen between the two services). Tests like `tests/test_internal_analysis_job_api.py` assert exact JSON shapes (`{"jobId": ..., "analysisId": ..., "status": ..., "startedAt": ..., "completedAt": ...}`) — treat these as a compatibility boundary, not incidental test detail.

### Cross-service deployment reachability

This service and the Spring Boot backend are two independently deployed processes, and each must be reachable from the *other's* network vantage point, not just its own:

- Spring Boot calls this service's `POST /internal/v1/analyses` with a `callbackUrl` pointing back at itself. `services/callback_service.py` later does an outbound `httpx.post(callback_url, ...)` **from wherever this FastAPI process is actually running**. If only this service is exposed via a public domain/tunnel (e.g. Cloudflare) while Spring Boot's callback URL is a `localhost`/private address that only makes sense from Spring Boot's own machine, the callback will fail with a connection-refused error every time — this is not a bug in either codebase, it's a network-topology mismatch. Both sides need to be mutually reachable, or Spring Boot's `callbackUrl` needs to resolve to wherever this service can actually reach it.
- `_run_analysis_job` (`routers/internal_analysis.py`) treats `update_job_status` and `send_analysis_callback` as independent best-effort steps — a callback delivery failure is logged (with an extra hint for `httpx.ConnectError`/`ConnectTimeout` specifically) but never crashes the request or the background task. `GET /internal/v1/analyses/{job_id}` is the fallback if a callback never arrives, so a "connection refused" in the logs from `send_analysis_callback` generally means the analysis itself succeeded and only the notification failed — check job status via `GET` before assuming the analysis failed.
