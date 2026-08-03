#!/usr/bin/env python3
"""축소 데이터셋을 pgvector(PostgreSQL)에 배치 인덱싱한다.

벡터 저장소는 pgvector 하나뿐이다. 파일 기반 저장소(Chroma/FAISS 등)나
`--persist-directory` 는 쓰지 않는다. 접속 정보는 `llamaindex/vector_store.py`
가 해석하며 `DATABASE_URL` 을 1순위, `POSTGRES_*` 를 2순위로 본다.

설계 요점
    - 전체 메모리 적재를 하지 않는다. 파일을 스트리밍으로 읽고 배치 단위로
      임베딩·저장한다.
    - 노드 ID 는 (원본 문서 id + 파일 경로 + chunk index + 본문 hash) 로 만든다.
      같은 입력을 다시 인덱싱해도 같은 ID 가 나오므로 chunk 가 중복되지 않는다.
    - `--resume` 을 주면 체크포인트에 기록된 문서를 건너뛰고 이어서 진행한다.
    - 임베딩 호출은 지수 백오프로 재시도한다. 토큰/키는 로그에 남기지 않는다.

사용 예
    # smoke (모델 호출 없음)
    python scripts/batch_index.py --data-path ./data/subset_5gb \\
        --batch-size 16 --max-documents 100 --dry-run

    # 실제 (DATABASE_URL / OPENAI_API_KEY 환경 변수 필요)
    python scripts/batch_index.py --data-path ./data/subset_5gb \\
        --batch-size 128 --chunk-size 1000 --chunk-overlap 150 --resume
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llamaindex.vector_store import (  # noqa: E402 - sys.path 설정 뒤에 import 해야 한다
    DEFAULT_TABLE_NAME,
    count_vectors,
    describe_target,
    get_vector_store,
    healthcheck,
)

LOGGER = logging.getLogger("batch_index")

# 인덱싱 본문으로 쓸 컬럼 후보. 앞에 있는 것부터 우선한다.
TEXT_COLUMNS: tuple[str, ...] = (
    "diff_context",
    "diff",
    "content",
    "text",
    "body",
    "after_code",
    "before_code",
)

# 문서 식별자 후보.
ID_COLUMNS: tuple[str, ...] = ("id", "sample_id", "uid", "_source_row_index", "record_id")

# 검색·필터에 쓰려고 함께 저장하는 메타데이터 컬럼.
METADATA_COLUMNS: tuple[str, ...] = (
    "repo_name",
    "repo_language",
    "language",
    "file_path",
    "pr_number",
    "pr_title",
    "comment_type",
    "comment_line",
    "quality_score",
    "category",
    "subtype",
    "severity",
    "split",
)

# 정답 누출 방지. 리뷰 댓글은 평가 정답의 근거이므로 인덱싱하지 않는다.
ANSWER_COLUMNS: frozenset[str] = frozenset(
    {"reviewer_comment", "review_comment_original", "review_comment_ko", "expected"}
)

RECORD_SUFFIXES = {".jsonl": "jsonl", ".json": "json", ".csv": "csv", ".tsv": "csv", ".parquet": "parquet"}
SKIP_DIR_NAMES = {"__pycache__", ".git", ".cache", ".huggingface"}


# ---------------------------------------------------------------------------
# 자료 구조
# ---------------------------------------------------------------------------


@dataclass
class SourceDocument:
    """인덱싱 대상 문서 1건."""

    doc_id: str
    file_rel: str
    text: str
    metadata: dict[str, Any]


@dataclass
class Stats:
    """진행/결과 집계."""

    documents_seen: int = 0
    documents_indexed: int = 0
    documents_skipped_short: int = 0
    documents_skipped_empty: int = 0
    documents_skipped_resume: int = 0
    chunks_created: int = 0
    chunks_indexed: int = 0
    chunks_duplicate: int = 0
    failed_items: int = 0
    batches: int = 0
    errors: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self.started


# ---------------------------------------------------------------------------
# 로깅
# ---------------------------------------------------------------------------


def setup_logging(log_path: Path, verbose: bool) -> None:
    """콘솔과 파일에 동시에 기록한다. 시크릿은 남기지 않는다."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)

    LOGGER.handlers = [stream, file_handler]
    LOGGER.setLevel(logging.DEBUG if verbose else logging.INFO)
    LOGGER.propagate = False


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 문서 읽기 (스트리밍)
# ---------------------------------------------------------------------------


def iter_record_files(root: Path) -> list[Path]:
    """레코드 파일을 경로 순으로 돌려준다. 순서가 고정돼야 재현이 된다."""
    if root.is_file():
        return [root] if root.suffix.lower() in RECORD_SUFFIXES else []
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in SKIP_DIR_NAMES or part.startswith(".") for part in path.parts):
            continue
        if path.suffix.lower() in RECORD_SUFFIXES:
            out.append(path)
    return out


def pick_column(columns: Sequence[str], candidates: Sequence[str]) -> str | None:
    lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def build_text(row: dict[str, Any], text_col: str | None) -> str:
    """인덱싱할 본문을 만든다. 정답 컬럼은 절대 포함하지 않는다."""
    if text_col and row.get(text_col):
        primary = str(row[text_col])
    else:
        # 본문 컬럼을 못 찾으면 정답이 아닌 문자열 필드를 이어 붙인다.
        parts = [
            f"{k}: {v}"
            for k, v in row.items()
            if k not in ANSWER_COLUMNS and isinstance(v, (str, int, float)) and str(v).strip()
        ]
        primary = "\n".join(parts)

    header_keys = ("repo_name", "file_path", "language", "pr_title")
    header = [f"[{k}] {row[k]}" for k in header_keys if row.get(k)]
    return ("\n".join(header) + "\n\n" + primary).strip() if header else primary.strip()


def build_metadata(row: dict[str, Any], file_rel: str) -> dict[str, Any]:
    meta: dict[str, Any] = {"source_file": file_rel}
    for col in METADATA_COLUMNS:
        value = row.get(col)
        if value is None or value == "":
            continue
        meta[col] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return meta


def stable_doc_id(file_rel: str, index: int, row: dict[str, Any], id_col: str | None) -> str:
    """원본 문서 id 가 있으면 쓰고, 없으면 파일 경로+행 번호로 만든다."""
    if id_col and row.get(id_col) not in (None, ""):
        return f"{file_rel}::{row[id_col]}"
    return f"{file_rel}::{index}"


def iter_documents(root: Path, files: Sequence[Path]) -> Iterator[SourceDocument]:
    """레코드 파일을 스트리밍으로 읽어 문서를 하나씩 내보낸다."""
    for path in files:
        rel = str(path.relative_to(root)) if root.is_dir() else path.name
        fmt = RECORD_SUFFIXES[path.suffix.lower()]
        try:
            if fmt == "jsonl":
                yield from _iter_jsonl(path, rel)
            elif fmt == "json":
                yield from _iter_json(path, rel)
            elif fmt == "csv":
                yield from _iter_csv(path, rel)
            elif fmt == "parquet":
                yield from _iter_parquet(path, rel)
        except (OSError, ValueError) as exc:
            LOGGER.warning("파일을 건너뜁니다 (%s): %s", rel, exc)


def _emit(rows: Iterator[tuple[int, dict[str, Any]]], rel: str, columns: Sequence[str]) -> Iterator[SourceDocument]:
    text_col = pick_column(columns, TEXT_COLUMNS)
    id_col = pick_column(columns, ID_COLUMNS)
    for index, row in rows:
        yield SourceDocument(
            doc_id=stable_doc_id(rel, index, row, id_col),
            file_rel=rel,
            text=build_text(row, text_col),
            metadata=build_metadata(row, rel),
        )


def _iter_jsonl(path: Path, rel: str) -> Iterator[SourceDocument]:
    def rows() -> Iterator[tuple[int, dict[str, Any]]]:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield index, obj

    first: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    first = candidate
                    break
    if first is None:
        return
    yield from _emit(rows(), rel, list(first.keys()))


def _iter_json(path: Path, rel: str) -> Iterator[SourceDocument]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        best_key, best_len = None, -1
        for key, value in data.items():
            if isinstance(value, list) and len(value) > best_len:
                best_key, best_len = key, len(value)
        if best_key is None:
            return
        items = data[best_key]
    elif isinstance(data, list):
        items = data
    else:
        return

    records = [(i, item) for i, item in enumerate(items) if isinstance(item, dict)]
    if not records:
        return
    # 중첩 구조(test_samples.json 처럼)는 평탄화해 컬럼을 인식할 수 있게 한다.
    flat = [(i, _flatten(obj)) for i, obj in records]
    yield from _emit(iter(flat), rel, list(flat[0][1].keys()))


def _flatten(obj: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """한 단계 중첩까지 평탄화한다. 리스트/깊은 구조는 문자열로 둔다."""
    out: dict[str, Any] = {}
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and depth < 2:
            out.update(_flatten(value, prefix="", depth=depth + 1))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = value
        else:
            out[name] = json.dumps(value, ensure_ascii=False)
    return out


def _iter_csv(path: Path, rel: str) -> Iterator[SourceDocument]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        columns = list(reader.fieldnames or [])
        if not columns:
            return
        rows = ((i, dict(row)) for i, row in enumerate(reader))
        yield from _emit(rows, rel, columns)


def _iter_parquet(path: Path, rel: str) -> Iterator[SourceDocument]:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        LOGGER.warning("pyarrow 가 없어 Parquet 을 건너뜁니다: %s", rel)
        return
    pf = pq.ParquetFile(str(path))
    columns = list(pf.schema_arrow.names)

    def rows() -> Iterator[tuple[int, dict[str, Any]]]:
        index = 0
        for batch in pf.iter_batches(batch_size=1024):
            for row in batch.to_pylist():
                yield index, row
                index += 1

    yield from _emit(rows(), rel, columns)


# ---------------------------------------------------------------------------
# chunk / node
# ---------------------------------------------------------------------------


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """문자 기준으로 자른다.

    코드 diff 는 문장 경계가 의미를 갖지 않고, LlamaIndex 기본 SentenceSplitter 는
    nltk punkt 를 불러오는데 이 저장소 환경에서 그 경로가 막혀 있다
    (`eval_local/run_llamaindex_test.py` 에 같은 내용이 기록돼 있다).
    """
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 은 chunk_size 보다 작아야 합니다.")
    if len(text) <= chunk_size:
        return [text]
    step = chunk_size - chunk_overlap
    return [text[i : i + chunk_size] for i in range(0, len(text), step) if text[i : i + chunk_size].strip()]


def node_id_for(doc_id: str, file_rel: str, chunk_index: int, body: str) -> str:
    """(원본 doc id + 파일 경로 + chunk index + 본문 hash) 로 안정적인 ID 를 만든다."""
    digest = hashlib.blake2b(body.encode("utf-8", "replace"), digest_size=12).hexdigest()
    key = f"{doc_id}|{file_rel}|{chunk_index}|{digest}"
    return hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()


# ---------------------------------------------------------------------------
# 체크포인트
# ---------------------------------------------------------------------------


class Checkpoint:
    """이미 인덱싱한 문서 ID 를 파일에 남겨 재실행 시 건너뛴다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.done: set[str] = set()
        self._handle = None

    def load(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                doc_id = line.strip()
                if doc_id:
                    self.done.add(doc_id)
        return len(self.done)

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def mark(self, doc_ids: Sequence[str]) -> None:
        if self._handle is None:
            return
        for doc_id in doc_ids:
            self._handle.write(doc_id + "\n")
            self.done.add(doc_id)
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# ---------------------------------------------------------------------------
# 임베딩
# ---------------------------------------------------------------------------


def resolve_embed_model(model_name: str, dry_run: bool) -> tuple[Any, str, str]:
    """임베딩 모델과 모드, 사유를 돌려준다."""
    if dry_run:
        return None, "none", "dry-run 이라 임베딩을 호출하지 않습니다."
    if not os.environ.get("OPENAI_API_KEY"):
        return (
            None,
            "unavailable",
            "OPENAI_API_KEY 가 없어 임베딩을 만들 수 없습니다.",
        )
    try:
        from llama_index.embeddings.openai import OpenAIEmbedding
    except ImportError:
        return (
            None,
            "unavailable",
            "llama-index-embeddings-openai 가 설치되어 있지 않습니다.",
        )
    return OpenAIEmbedding(model=model_name), "openai", f"OpenAI 임베딩({model_name})을 사용합니다."


def embed_with_retry(
    embed_model: Any, texts: list[str], max_retries: int, base_delay: float, rng: random.Random
) -> list[list[float]]:
    """지수 백오프로 임베딩을 재시도한다. 예외 메시지에 키를 담지 않는다."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return embed_model.get_text_embedding_batch(texts, show_progress=False)
        except Exception as exc:  # SDK 예외 계층에 의존하지 않는다
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = base_delay * (2**attempt) + rng.uniform(0, base_delay)
            LOGGER.warning(
                "임베딩 실패(%s). %.1f초 뒤 재시도 %d/%d",
                type(exc).__name__,
                delay,
                attempt + 1,
                max_retries,
            )
            time.sleep(delay)
    raise RuntimeError(f"임베딩 재시도 실패: {type(last_exc).__name__}") from last_exc


# ---------------------------------------------------------------------------
# 인덱싱 본체
# ---------------------------------------------------------------------------


def flush_batch(
    vector_store: Any,
    embed_model: Any,
    pending: list[tuple[str, str, dict[str, Any]]],
    stats: Stats,
    args: argparse.Namespace,
    rng: random.Random,
) -> None:
    """모아 둔 chunk 를 임베딩해 pgvector 에 저장한다."""
    if not pending:
        return

    from llama_index.core.schema import TextNode

    texts = [body for _, body, _ in pending]
    embeddings = embed_with_retry(
        embed_model, texts, args.max_retries, args.retry_base_delay, rng
    )

    nodes = []
    for (node_id, body, meta), vector in zip(pending, embeddings):
        node = TextNode(text=body, id_=node_id, metadata=meta)
        node.embedding = vector
        nodes.append(node)

    vector_store.add(nodes)
    stats.chunks_indexed += len(nodes)
    stats.batches += 1
    pending.clear()


def run(args: argparse.Namespace) -> int:
    log_path = Path(args.log_file).expanduser()
    setup_logging(log_path, args.verbose)

    data_path = Path(args.data_path).expanduser().resolve()
    if not data_path.exists():
        LOGGER.error("데이터 경로가 없습니다: %s", data_path)
        return 1

    target = describe_target(args.table_name)
    stats = Stats()
    rng = random.Random(args.seed)

    LOGGER.info("=" * 70)
    LOGGER.info("배치 인덱싱 시작 (%s)", now_utc())
    LOGGER.info("데이터 경로     : %s", data_path)
    LOGGER.info("벡터 저장소     : pgvector (%s, 설정 출처=%s)", target["target"], target["config_source"])
    LOGGER.info("테이블          : %s", target["table_name"])
    LOGGER.info("임베딩 모델     : %s (dim=%s)", args.embedding_model, target["embed_dim"])
    LOGGER.info("chunk           : size=%d overlap=%d", args.chunk_size, args.chunk_overlap)
    LOGGER.info("batch size      : %d", args.batch_size)
    LOGGER.info("최소 본문 길이  : %d자", args.min_chars)
    LOGGER.info("dry-run         : %s", args.dry_run)
    LOGGER.info("=" * 70)

    files = iter_record_files(data_path)
    if not files:
        LOGGER.error("인덱싱할 레코드 파일을 찾지 못했습니다: %s", data_path)
        return 1
    LOGGER.info("레코드 파일 %d개", len(files))

    checkpoint = Checkpoint(Path(args.checkpoint).expanduser())
    if args.resume:
        loaded = checkpoint.load()
        LOGGER.info("체크포인트에서 문서 %d건을 확인했습니다(건너뜁니다).", loaded)

    embed_model, embed_mode, embed_note = resolve_embed_model(args.embedding_model, args.dry_run)
    LOGGER.info("임베딩 모드: %s — %s", embed_mode, embed_note)

    vector_store = None
    db_ok, db_note = (False, "dry-run 이라 접속을 시도하지 않았습니다.")
    if not args.dry_run:
        db_ok, db_note = healthcheck()
        LOGGER.info("pgvector 상태: %s", db_note)
        if not db_ok:
            LOGGER.error("pgvector 에 접속하지 못해 중단합니다. DATABASE_URL 을 확인하세요.")
            return 2
        if embed_model is None:
            LOGGER.error("임베딩을 만들 수 없어 중단합니다. %s", embed_note)
            return 3
        vector_store = get_vector_store(
            table_name=args.table_name, embed_dim=int(target["embed_dim"])
        )

    seen_node_ids: set[str] = set()
    pending: list[tuple[str, str, dict[str, Any]]] = []
    pending_docs: list[str] = []
    last_report = time.monotonic()

    if not args.dry_run:
        checkpoint.open()

    try:
        for doc in iter_documents(data_path, files):
            if args.max_documents and stats.documents_seen >= args.max_documents:
                break
            stats.documents_seen += 1

            if args.resume and doc.doc_id in checkpoint.done:
                stats.documents_skipped_resume += 1
                continue

            body = doc.text.strip()
            if not body:
                stats.documents_skipped_empty += 1
                continue
            if len(body) < args.min_chars:
                stats.documents_skipped_short += 1
                continue

            chunks = chunk_text(body, args.chunk_size, args.chunk_overlap)
            stats.chunks_created += len(chunks)

            for chunk_index, chunk in enumerate(chunks):
                node_id = node_id_for(doc.doc_id, doc.file_rel, chunk_index, chunk)
                if node_id in seen_node_ids:
                    stats.chunks_duplicate += 1
                    continue
                seen_node_ids.add(node_id)
                meta = dict(doc.metadata)
                meta.update({"doc_id": doc.doc_id, "chunk_index": chunk_index})
                pending.append((node_id, chunk, meta))

            stats.documents_indexed += 1
            pending_docs.append(doc.doc_id)

            if len(pending) >= args.batch_size:
                if args.dry_run:
                    stats.batches += 1
                    pending.clear()
                else:
                    try:
                        flush_batch(vector_store, embed_model, pending, stats, args, rng)
                        checkpoint.mark(pending_docs)
                    except Exception as exc:  # noqa: BLE001 - 배치 실패는 기록하고 계속한다
                        stats.failed_items += len(pending)
                        stats.errors.append(f"{type(exc).__name__}: {exc}")
                        LOGGER.error("배치 저장 실패 (%d chunk): %s", len(pending), type(exc).__name__)
                        pending.clear()
                pending_docs = []

            now = time.monotonic()
            if now - last_report >= 10.0:
                rate = stats.documents_seen / max(stats.elapsed(), 1e-6)
                LOGGER.info(
                    "진행: 문서 %d건 / chunk %d개 / 저장 %d개 / %.1f docs/s",
                    stats.documents_seen,
                    stats.chunks_created,
                    stats.chunks_indexed,
                    rate,
                )
                last_report = now

        if pending:
            if args.dry_run:
                stats.batches += 1
                pending.clear()
            else:
                try:
                    flush_batch(vector_store, embed_model, pending, stats, args, rng)
                    checkpoint.mark(pending_docs)
                except Exception as exc:  # noqa: BLE001
                    stats.failed_items += len(pending)
                    stats.errors.append(f"{type(exc).__name__}: {exc}")
                    LOGGER.error("마지막 배치 저장 실패: %s", type(exc).__name__)
    finally:
        checkpoint.close()

    # 검증: 저장된 벡터 개수와 만든 chunk 개수를 맞춰 본다.
    vector_count = None if args.dry_run else count_vectors(args.table_name)
    unique_chunks = len(seen_node_ids)
    verification = {
        "unique_chunks_built": unique_chunks,
        "chunks_indexed_this_run": stats.chunks_indexed,
        "vector_rows_in_table": vector_count,
        "matches": (
            None
            if vector_count is None
            else vector_count >= stats.chunks_indexed and stats.failed_items == 0
        ),
        "note": (
            "dry-run 이라 pgvector 를 조회하지 않았습니다."
            if args.dry_run
            else "테이블에는 이전 실행분이 함께 들어 있을 수 있어 '이상'으로 비교합니다."
        ),
    }

    summary = {
        "generated_at": now_utc(),
        "dataset_path": str(data_path),
        "vector_store": "pgvector",
        "vector_store_target": target["target"],
        "vector_store_config_source": target["config_source"],
        "table_name": target["table_name"],
        "embedding_model": args.embedding_model,
        "embedding_mode": embed_mode,
        "embedding_note": embed_note,
        "embed_dim": target["embed_dim"],
        "chunk_size": args.chunk_size,
        "chunk_overlap": args.chunk_overlap,
        "batch_size": args.batch_size,
        "min_chars": args.min_chars,
        "seed": args.seed,
        "resume": args.resume,
        "dry_run": args.dry_run,
        "record_files": len(files),
        "documents_seen": stats.documents_seen,
        "documents_indexed": stats.documents_indexed,
        "documents_skipped": {
            "empty": stats.documents_skipped_empty,
            "too_short": stats.documents_skipped_short,
            "resume_checkpoint": stats.documents_skipped_resume,
        },
        "chunks_created": stats.chunks_created,
        "chunks_indexed": stats.chunks_indexed,
        "chunks_duplicate_skipped": stats.chunks_duplicate,
        "failed_items": stats.failed_items,
        "batches": stats.batches,
        "elapsed_seconds": round(stats.elapsed(), 2),
        "documents_per_second": round(stats.documents_seen / max(stats.elapsed(), 1e-6), 2),
        "database_status": db_note,
        "verification": verification,
        "errors": stats.errors[:20],
    }

    out_path = Path(args.summary_output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    LOGGER.info("=" * 70)
    LOGGER.info(
        "문서 %d건 확인 / %d건 인덱싱 / chunk %d개 생성 / %d개 저장 / 실패 %d",
        stats.documents_seen,
        stats.documents_indexed,
        stats.chunks_created,
        stats.chunks_indexed,
        stats.failed_items,
    )
    LOGGER.info("제외: 빈 문서 %d / 너무 짧음 %d / 체크포인트 %d",
                stats.documents_skipped_empty,
                stats.documents_skipped_short,
                stats.documents_skipped_resume)
    LOGGER.info("소요 %.1f초", stats.elapsed())
    LOGGER.info("요약 저장: %s", out_path)
    LOGGER.info("로그: %s", log_path)
    LOGGER.info("=" * 70)

    if stats.failed_items:
        return 4
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="축소 데이터셋을 pgvector 에 배치 인덱싱한다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-path", required=True, help="인덱싱할 데이터 경로")
    parser.add_argument("--batch-size", type=int, default=128, help="한 번에 임베딩할 chunk 수")
    parser.add_argument("--chunk-size", type=int, default=1000, help="chunk 문자 수")
    parser.add_argument("--chunk-overlap", type=int, default=150, help="chunk 겹침 문자 수")
    parser.add_argument(
        "--min-chars", type=int, default=40, help="이보다 짧은 문서는 인덱싱하지 않는다"
    )
    parser.add_argument("--max-documents", type=int, default=0, help="처리할 최대 문서 수 (0=전체)")
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        help="임베딩 모델 이름",
    )
    parser.add_argument(
        "--table-name", default=DEFAULT_TABLE_NAME, help="pgvector 테이블 이름"
    )
    parser.add_argument("--resume", action="store_true", help="체크포인트부터 이어서 진행")
    parser.add_argument(
        "--checkpoint",
        default="./results/batch_index.checkpoint",
        help="처리 완료 문서 ID 를 남길 파일",
    )
    parser.add_argument(
        "--summary-output", default="./results/indexing_summary.json", help="요약 저장 경로"
    )
    parser.add_argument("--log-file", default="./logs/batch_index.log", help="로그 파일 경로")
    parser.add_argument("--max-retries", type=int, default=5, help="임베딩 재시도 횟수")
    parser.add_argument(
        "--retry-base-delay", type=float, default=2.0, help="지수 백오프 기본 대기(초)"
    )
    parser.add_argument("--seed", type=int, default=42, help="재시도 지터용 시드")
    parser.add_argument(
        "--dry-run", action="store_true", help="임베딩/저장 없이 문서·chunk 수만 확인"
    )
    parser.add_argument("--verbose", action="store_true", help="상세 로그")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
