#!/usr/bin/env python3
"""원본 데이터셋을 건드리지 않고 약 5GB 이하의 축소 데이터셋을 별도로 생성한다.

지원 형식
    - JSONL (라인 단위 스트리밍)
    - JSON  (레코드 배열 또는 배열을 담은 객체)
    - CSV / TSV (chunk 스트리밍)
    - Parquet (PyArrow row group 단위)
    - Hugging Face Dataset / DatasetDict (load_from_disk)
    - 이미지 / 오디오 디렉터리 (+ 연결된 메타데이터 파일 동반 필터링)
    - 위 형식이 섞인 디렉터리

원본은 읽기 전용으로만 다룬다. 결과는 --output-path 아래에만 기록한다.

사용 예
    python scripts/create_5gb_subset.py \\
        --input-path ./data --output-path ./data/subset_5gb \\
        --target-size-gb 4.8 --max-size-gb 5.0 --seed 42 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import random
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

GB = 1_000_000_000
GIB = 1_073_741_824

SPLIT_TOKENS: tuple[str, ...] = (
    "train",
    "training",
    "validation",
    "valid",
    "val",
    "dev",
    "test",
    "eval",
)

# split 이름 정규화. 원본 비율을 유지할 때 같은 split 으로 묶기 위해 쓴다.
SPLIT_ALIASES: dict[str, str] = {
    "training": "train",
    "valid": "validation",
    "val": "validation",
    "dev": "validation",
    "eval": "test",
}

IMAGE_EXT: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ppm"}
)
AUDIO_EXT: frozenset[str] = frozenset(
    {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus", ".aac", ".aiff"}
)
MEDIA_EXT: frozenset[str] = IMAGE_EXT | AUDIO_EXT

RECORD_EXT: frozenset[str] = frozenset({".jsonl", ".ndjson", ".json", ".csv", ".tsv", ".parquet"})

# 데이터가 아니라 부속 설명 파일로 보는 확장자. 작으면 그대로 복사한다.
AUX_EXT: frozenset[str] = frozenset(
    {".md", ".txt", ".yaml", ".yml", ".cfg", ".ini", ".license", ".rst", ".toml"}
)

# 제외할 임시/캐시 경로 조각.
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {"__pycache__", ".git", ".ipynb_checkpoints", ".DS_Store", ".venv", "node_modules"}
)
SKIP_SUFFIXES: tuple[str, ...] = (".tmp", ".part", ".lock", ".swp", ".pyc")

# 라벨/클래스로 쓸 만한 컬럼 후보. 앞쪽이 우선순위가 높다.
LABEL_CANDIDATES: tuple[str, ...] = (
    "label",
    "labels",
    "class",
    "class_name",
    "category",
    "classification",
    "comment_type",
    "target",
    "intent",
    "sentiment",
    "topic",
    "tag",
    "type",
    "kind",
)

# 라벨은 아니지만 분포를 함께 유지하고 싶은 메타데이터 컬럼 후보.
META_CANDIDATES: tuple[str, ...] = (
    "language",
    "lang",
    "source",
    "domain",
    "repo_language",
    "dataset",
    "origin",
    "country",
    "speaker",
)

# 미디어 파일 경로가 담길 만한 컬럼 후보.
PATH_CANDIDATES: tuple[str, ...] = (
    "file_name",
    "filename",
    "file",
    "path",
    "filepath",
    "file_path",
    "image",
    "image_path",
    "audio",
    "audio_path",
    "wav",
    "url",
    "relative_path",
)

# 개인정보로 의심되는 컬럼명 조각. 보고서에 경고만 남긴다(값은 그대로 둔다).
PII_COLUMN_HINTS: tuple[str, ...] = (
    "email",
    "e_mail",
    "phone",
    "mobile",
    "tel",
    "address",
    "addr",
    "ssn",
    "passport",
    "birth",
    "dob",
    "credit",
    "card",
    "ip_address",
    "ipaddr",
    "user_name",
    "username",
    "real_name",
    "full_name",
    "author_email",
)

PII_VALUE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "이메일 주소 형태"),
    (r"\b(?:\+?\d{1,3}[ \-]?)?0?1[0-9][ \-]?\d{3,4}[ \-]?\d{4}\b", "휴대전화 번호 형태"),
    (r"\b\d{6}[ \-]\d{7}\b", "주민등록번호 형태"),
    (r"\b(?:\d{4}[ \-]){3}\d{4}\b", "카드번호 형태"),
)

PROGRESS_INTERVAL_SEC = 2.0

# 출력 인코딩/구분자 차이로 생기는 오차를 흡수할 여유분(비율).
SIZE_SAFETY_MARGIN = 0.02


# ---------------------------------------------------------------------------
# 자료 구조
# ---------------------------------------------------------------------------


@dataclass
class RecordRef:
    """원본 레코드 1건에 대한 참조. 본문은 담지 않는다(메모리 절약)."""

    index: int
    size: int
    stratum: str
    label: str
    meta: str
    identifier: str
    path_value: str | None = None
    # 내용 기반 중복 검사용 지문. --no-dedup 이면 None 으로 남는다.
    fingerprint: str | None = None


@dataclass
class SourceFile:
    """원본 파일 1개와 그 안에서 찾은 레코드 색인."""

    path: Path
    rel: str
    size: int
    kind: str  # record | media | aux | other
    fmt: str  # jsonl | json | csv | parquet | media | aux | other
    split: str
    records: list[RecordRef] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    invalid_rows: int = 0
    json_container_key: str | None = None
    csv_dialect: dict[str, Any] = field(default_factory=dict)
    encoding: str = "utf-8"
    label_column: str | None = None
    path_column: str | None = None
    error: str | None = None


@dataclass
class Inventory:
    """원본 전체 스캔 결과."""

    root: Path
    mode: str  # hf_dataset | media | record | empty
    files: list[SourceFile] = field(default_factory=list)
    total_bytes: int = 0
    record_bytes: int = 0
    media_bytes: int = 0
    aux_bytes: int = 0
    other_bytes: int = 0
    total_records: int = 0
    total_media: int = 0
    invalid_rows: int = 0
    duplicate_records: int = 0
    duplicate_sources: list[str] = field(default_factory=list)
    formats: Counter = field(default_factory=Counter)
    split_records: Counter = field(default_factory=Counter)
    split_bytes: Counter = field(default_factory=Counter)
    label_dist: Counter = field(default_factory=Counter)
    meta_dist: Counter = field(default_factory=Counter)
    media_label_dist: Counter = field(default_factory=Counter)
    stratum_bytes: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    pii_warnings: list[str] = field(default_factory=list)
    broken_files: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """축소 계획. 파일별로 어떤 레코드를 남길지 담는다."""

    selected: dict[str, list[RecordRef]] = field(default_factory=dict)
    media_files: list[SourceFile] = field(default_factory=list)
    aux_files: list[SourceFile] = field(default_factory=list)
    estimated_bytes: int = 0
    selected_records: int = 0
    selected_media: int = 0
    label_dist: Counter = field(default_factory=Counter)
    meta_dist: Counter = field(default_factory=Counter)
    media_label_dist: Counter = field(default_factory=Counter)
    split_records: Counter = field(default_factory=Counter)
    kept_media_rel: set[str] = field(default_factory=set)
    output_paths: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------


def human_bytes(num: int | float) -> str:
    """바이트 수를 사람이 읽기 쉬운 문자열로 바꾼다."""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1000.0:
            return f"{value:,.2f} {unit}"
        value /= 1000.0
    return f"{value:,.2f} PB"


def log(message: str) -> None:
    """진행 상황을 표준 출력으로 알린다."""
    print(message, flush=True)


def should_skip(path: Path) -> bool:
    """임시/캐시 파일이면 True.

    숨김 디렉터리 안의 파일도 건너뛴다. Hugging Face 스냅샷의
    `.cache/huggingface/` 처럼 파일 이름 자체는 숨김이 아니지만 내용은
    다운로드 캐시인 경우가 있어서다.
    """
    if any(part in SKIP_DIR_NAMES or part.startswith(".") for part in path.parts):
        return True
    return path.name.endswith(SKIP_SUFFIXES)


def dir_size(path: Path) -> tuple[int, int]:
    """디렉터리의 실제 파일 용량 합계와 파일 개수를 돌려준다(임시 파일 제외)."""
    total = 0
    count = 0
    for entry in path.rglob("*"):
        if entry.is_symlink() or not entry.is_file():
            continue
        if should_skip(entry.relative_to(path)):
            continue
        total += entry.stat().st_size
        count += 1
    return total, count


def normalize_split(token: str) -> str:
    """split 이름을 표준 이름으로 정규화한다."""
    low = token.lower()
    return SPLIT_ALIASES.get(low, low)


def detect_split(rel_path: str) -> str:
    """상대 경로에서 split 이름을 추론한다. 못 찾으면 'all'."""
    parts = re.split(r"[/\\]", rel_path.lower())
    for part in parts[:-1]:
        if part in SPLIT_TOKENS:
            return normalize_split(part)
    stem = Path(parts[-1]).stem
    for token in re.split(r"[^a-z0-9]+", stem):
        if token in SPLIT_TOKENS:
            return normalize_split(token)
    return "all"


def stringify(value: Any) -> str:
    """라벨 값을 문자열 키로 정규화한다.

    중첩 객체(`{"category": "security", ...}`)면 안쪽에서 라벨다운 키를 찾아 쓴다.
    그렇게 하지 않으면 서로 다른 클래스가 전부 하나로 뭉쳐 계층 샘플링이 무너진다.
    """
    if value is None:
        return "<none>"
    if isinstance(value, (list, tuple)):
        return "|".join(stringify(v) for v in value[:4])
    if isinstance(value, dict):
        inner = pick_column(list(value.keys()), LABEL_CANDIDATES + META_CANDIDATES)
        if inner is not None and not isinstance(value[inner], (dict, list, tuple)):
            return stringify(value[inner])
        for key in sorted(value):
            if isinstance(value[key], (str, int, bool)):
                return stringify(value[key])
        return "<dict>"
    text = str(value).strip()
    return text if text else "<empty>"


# 수집 시각처럼 같은 레코드라도 실행마다 달라지는 키는 지문에서 뺀다.
VOLATILE_KEYS: frozenset[str] = frozenset(
    {"_retrieved_at", "retrieved_at", "_ingested_at", "downloaded_at"}
)


def record_fingerprint(obj: dict[str, Any]) -> str:
    """레코드 내용에서 중복 판정용 지문을 만든다.

    키 순서와 수집 시각의 영향을 받지 않도록 정렬하고 휘발성 키를 제외한다.
    직렬화할 수 없는 값은 문자열로 바꿔 비교한다.
    """
    payload = {k: v for k, v in obj.items() if k not in VOLATILE_KEYS}
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError):
        canonical = repr(sorted((str(k), str(v)) for k, v in payload.items()))
    return hashlib.blake2b(canonical.encode("utf-8", "replace"), digest_size=16).hexdigest()


def get_nested(obj: dict[str, Any], column: str) -> Any:
    """`a.b.c` 형태의 점 표기 컬럼도 읽을 수 있게 한다."""
    if column in obj:
        return obj[column]
    current: Any = obj
    for part in column.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def pick_column(columns: Sequence[str], candidates: Sequence[str]) -> str | None:
    """후보 목록 우선순위대로 컬럼을 고른다(대소문자 무시)."""
    lowered = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    for cand in candidates:
        for low, original in lowered.items():
            if cand in low:
                return original
    return None


class Reservoir:
    """스트리밍 중 전체 구간에서 고르게 표본을 모은다.

    앞부분만 보면 라벨순으로 정렬된 원본에서 라벨 컬럼을 놓치므로 필요하다.
    """

    def __init__(self, limit: int, seed: int = 0) -> None:
        self.limit = limit
        self.items: list[dict[str, Any]] = []
        self.seen = 0
        self._rng = random.Random(seed)

    def add(self, obj: dict[str, Any]) -> None:
        """표본 후보를 하나 넣는다."""
        if len(self.items) < self.limit:
            self.items.append(obj)
        else:
            pos = self._rng.randrange(self.seen + 1)
            if pos < self.limit:
                self.items[pos] = obj
        self.seen += 1

    def columns(self) -> list[str]:
        """모인 표본에서 컬럼 순서를 복원한다."""
        out: list[str] = []
        for obj in self.items:
            for key in obj:
                if key not in out:
                    out.append(key)
        return out


def choose_label_column(
    columns: Sequence[str], samples: Sequence[dict[str, Any]]
) -> str | None:
    """카디널리티가 라벨답게 낮은 컬럼을 고른다."""
    if not samples:
        return None
    total = len(samples)
    fallback: str | None = None
    for cand in LABEL_CANDIDATES:
        col = pick_column(columns, (cand,))
        if not col:
            continue
        if fallback is None:
            fallback = col
        values = {stringify(get_nested(row, col)) for row in samples}
        # 값이 전부 같거나(구분 불가) 거의 전부 다르면(사실상 식별자) 라벨로 쓰지 않는다.
        if len(values) < 2:
            continue
        if len(values) <= max(2, min(200, total // 2 or 1)):
            return col
    # 표본이 한쪽으로 치우쳐 판정에 실패해도, 이름이 라벨다운 컬럼이 있으면 그것을 쓴다.
    return fallback


def detect_pii(columns: Sequence[str], samples: Sequence[dict[str, Any]], rel: str) -> list[str]:
    """개인정보로 의심되는 컬럼/값을 찾아 경고 문구 목록으로 돌려준다."""
    found: list[str] = []
    for col in columns:
        low = col.lower()
        if any(hint in low for hint in PII_COLUMN_HINTS):
            found.append(f"{rel}: 컬럼명 '{col}' 이 개인정보 관련으로 보입니다.")
    joined = "\n".join(
        stringify(v) for row in samples[:50] for v in row.values() if isinstance(v, str)
    )[:200_000]
    for pattern, desc in PII_VALUE_PATTERNS:
        if re.search(pattern, joined):
            found.append(f"{rel}: 값에서 {desc} 가 발견되었습니다.")
    return found


def iter_source_files(root: Path, exclude: Path | None = None) -> Iterator[Path]:
    """원본 아래의 실제 파일을 순회한다. exclude 하위는 건너뛴다."""
    if root.is_file():
        yield root
        return
    for entry in sorted(root.rglob("*")):
        if entry.is_symlink() or not entry.is_file():
            continue
        if should_skip(entry.relative_to(root)):
            continue
        if exclude is not None and (entry == exclude or exclude in entry.parents):
            continue
        yield entry


def classify_file(path: Path) -> tuple[str, str]:
    """파일 하나의 (kind, fmt) 를 판정한다."""
    ext = path.suffix.lower()
    if ext in {".jsonl", ".ndjson"}:
        return "record", "jsonl"
    if ext == ".json":
        return "record", "json"
    if ext in {".csv", ".tsv"}:
        return "record", "csv"
    if ext == ".parquet":
        return "record", "parquet"
    if ext in MEDIA_EXT:
        return "media", "media"
    if ext in AUX_EXT:
        return "aux", "aux"
    return "other", "other"


# ---------------------------------------------------------------------------
# 형식 감지
# ---------------------------------------------------------------------------


def is_hf_dataset_dir(root: Path) -> bool:
    """load_from_disk 로 열 수 있는 디렉터리인지 확인한다."""
    if not root.is_dir():
        return False
    if (root / "dataset_dict.json").exists():
        return True
    if (root / "dataset_info.json").exists() and (root / "state.json").exists():
        return True
    return False


# ---------------------------------------------------------------------------
# 파일별 인덱싱
# ---------------------------------------------------------------------------


def index_jsonl(
    src: SourceFile,
    label_hint: str | None,
    sample_limit: int = 200,
    fingerprints: bool = False,
) -> list[str]:
    """JSONL 파일을 한 줄씩 읽어 레코드 색인을 만든다. 본문은 저장하지 않는다."""
    reservoir = Reservoir(sample_limit)
    idx = 0
    invalid = 0
    with src.path.open("r", encoding=src.encoding, errors="replace") as handle:
        for line in handle:
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            size = len(line.encode(src.encoding, errors="replace"))
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                invalid += 1
                idx += 1
                continue
            if not isinstance(obj, dict):
                obj = {"value": obj}
            reservoir.add(obj)
            src.records.append(
                RecordRef(index=idx, size=size, stratum="", label="", meta="", identifier="")
            )
            idx += 1
    src.invalid_rows = invalid
    src.columns = reservoir.columns()
    _attach_record_attrs(
        src,
        reservoir.items,
        label_hint,
        reader=lambda: _reread_jsonl(src),
        fingerprints=fingerprints,
    )
    return src.columns


def _reread_jsonl(src: SourceFile) -> Iterator[tuple[int, dict[str, Any]]]:
    """JSONL 을 다시 순회하며 (인덱스, 객체) 를 내보낸다."""
    idx = 0
    with src.path.open("r", encoding=src.encoding, errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                idx += 1
                continue
            if not isinstance(obj, dict):
                obj = {"value": obj}
            yield idx, obj
            idx += 1


def index_json(
    src: SourceFile,
    label_hint: str | None,
    max_load_bytes: int,
    fingerprints: bool = False,
) -> list[str]:
    """JSON 파일에서 레코드 배열을 찾아 색인한다."""
    if src.size > max_load_bytes:
        src.error = (
            f"JSON 파일이 {human_bytes(src.size)} 로 커서 전체 적재를 건너뛰었습니다"
            " (--max-json-load-mb 로 상향 가능)."
        )
        return []
    try:
        with src.path.open("r", encoding=src.encoding, errors="replace") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        src.error = f"JSON 파싱 실패: {exc}"
        return []

    records: list[Any]
    if isinstance(data, list):
        records = data
        src.json_container_key = None
    elif isinstance(data, dict):
        best_key, best_len = None, -1
        for key, value in data.items():
            if isinstance(value, list) and len(value) > best_len:
                best_key, best_len = key, len(value)
        if best_key is None:
            src.error = "JSON 안에서 레코드 배열을 찾지 못했습니다."
            return []
        src.json_container_key = best_key
        records = data[best_key]
    else:
        src.error = "지원하지 않는 JSON 최상위 타입입니다."
        return []

    reservoir = Reservoir(200)
    for i, item in enumerate(records):
        obj = item if isinstance(item, dict) else {"value": item}
        size = len(json.dumps(obj, ensure_ascii=False).encode("utf-8")) + 2
        reservoir.add(obj)
        src.records.append(
            RecordRef(index=i, size=size, stratum="", label="", meta="", identifier="")
        )
    src.columns = reservoir.columns()
    _attach_record_attrs(
        src,
        reservoir.items,
        label_hint,
        reader=lambda: (
            (i, item if isinstance(item, dict) else {"value": item})
            for i, item in enumerate(records)
        ),
        fingerprints=fingerprints,
    )
    return src.columns


def _sniff_csv(path: Path, encoding: str) -> tuple[str, list[str]]:
    """CSV 구분자와 헤더를 추정한다."""
    with path.open("r", encoding=encoding, errors="replace", newline="") as handle:
        head = handle.read(65536)
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        delimiter = csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    reader = csv.reader(io.StringIO(head), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        header = []
    return delimiter, header


def index_csv(
    src: SourceFile, label_hint: str | None, fingerprints: bool = False
) -> list[str]:
    """CSV/TSV 를 스트리밍으로 읽어 레코드 색인을 만든다."""
    delimiter, header = _sniff_csv(src.path, src.encoding)
    src.csv_dialect = {"delimiter": delimiter, "header": header}
    if not header:
        src.error = "CSV 헤더를 읽지 못했습니다."
        return []
    reservoir = Reservoir(200)
    idx = 0
    invalid = 0
    with src.path.open("r", encoding=src.encoding, errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for row in reader:
            if row is None:
                invalid += 1
                continue
            approx = sum(len(str(v)) for v in row.values() if v is not None) + len(row) + 1
            reservoir.add(dict(row))
            src.records.append(
                RecordRef(index=idx, size=approx, stratum="", label="", meta="", identifier="")
            )
            idx += 1
    src.invalid_rows = invalid
    src.columns = list(header)
    _attach_record_attrs(
        src,
        reservoir.items,
        label_hint,
        reader=lambda: _reread_csv(src),
        fingerprints=fingerprints,
    )
    return src.columns


def _reread_csv(src: SourceFile) -> Iterator[tuple[int, dict[str, Any]]]:
    """CSV 를 다시 순회하며 (인덱스, 행) 을 내보낸다."""
    delimiter = src.csv_dialect.get("delimiter", ",")
    with src.path.open("r", encoding=src.encoding, errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for idx, row in enumerate(reader):
            yield idx, dict(row)


def index_parquet(
    src: SourceFile, label_hint: str | None, fingerprints: bool = False
) -> list[str]:
    """Parquet 을 row group 단위로 읽어 색인한다."""
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        src.error = "pyarrow 가 없어 Parquet 을 처리하지 못했습니다."
        return []
    try:
        pf = pq.ParquetFile(str(src.path))
    except Exception as exc:  # noqa: BLE001 - 손상 파일 보고용
        src.error = f"Parquet 열기 실패: {exc}"
        return []

    columns = list(pf.schema_arrow.names)
    src.columns = columns
    total_rows = pf.metadata.num_rows if pf.metadata else 0
    avg = int(src.size / total_rows) if total_rows else 0

    label_col = label_hint if label_hint in columns else pick_column(columns, LABEL_CANDIDATES)
    meta_col = pick_column(columns, META_CANDIDATES)
    id_col = pick_column(columns, ("id", "uid", "sample_id", "key"))
    wanted = [c for c in {label_col, meta_col, id_col} if c]

    src.label_column = label_col
    idx = 0
    if wanted:
        for batch in pf.iter_batches(batch_size=8192, columns=wanted):
            table = batch.to_pydict()
            rows = len(next(iter(table.values()))) if table else 0
            for i in range(rows):
                label = stringify(table[label_col][i]) if label_col else "<all>"
                meta = stringify(table[meta_col][i]) if meta_col else "<all>"
                ident = stringify(table[id_col][i]) if id_col else f"{src.rel}#{idx}"
                src.records.append(
                    RecordRef(
                        index=idx,
                        size=avg,
                        stratum=f"{src.split}||{label}",
                        label=label,
                        meta=meta,
                        identifier=ident,
                    )
                )
                idx += 1
    else:
        for i in range(total_rows):
            src.records.append(
                RecordRef(
                    index=i,
                    size=avg,
                    stratum=f"{src.split}||<all>",
                    label="<all>",
                    meta="<all>",
                    identifier=f"{src.rel}#{i}",
                )
            )

    if fingerprints and src.records:
        # 중복 검사를 위해서만 전체 컬럼을 한 번 더 스트리밍한다(배치 단위, 전량 적재 없음).
        try:
            pos = 0
            for batch in pf.iter_batches(batch_size=2048):
                for row in batch.to_pylist():
                    if pos < len(src.records):
                        src.records[pos].fingerprint = record_fingerprint(row)
                    pos += 1
        except Exception as exc:  # noqa: BLE001 - 중복 검사 실패는 치명적이지 않다
            src.error = src.error or f"중복 검사용 재읽기 실패: {exc}"

    return columns


def _attach_record_attrs(
    src: SourceFile,
    samples: Sequence[dict[str, Any]],
    label_hint: str | None,
    reader: Callable[[], Iterable[tuple[int, dict[str, Any]]]],
    fingerprints: bool = False,
) -> None:
    """색인된 레코드에 라벨/메타/식별자/경로값을 채운다."""
    columns = src.columns
    label_col: str | None = None
    if label_hint:
        # 점 표기(`classification.category`)로 지정했을 수도 있으므로 최상위 키까지 본다.
        if label_hint in columns or label_hint.split(".")[0] in columns:
            label_col = label_hint
    if label_col is None:
        label_col = choose_label_column(columns, samples)
    meta_col = pick_column(columns, META_CANDIDATES)
    id_col = pick_column(columns, ("id", "uid", "sample_id", "key", "_source_row_index"))
    path_col = pick_column(columns, PATH_CANDIDATES)

    src.label_column = label_col
    src.path_column = path_col

    by_index = {ref.index: ref for ref in src.records}
    for idx, obj in reader():
        ref = by_index.get(idx)
        if ref is None:
            continue
        ref.label = stringify(get_nested(obj, label_col)) if label_col else "<all>"
        ref.meta = stringify(get_nested(obj, meta_col)) if meta_col else "<all>"
        ref.identifier = stringify(get_nested(obj, id_col)) if id_col else f"{src.rel}#{idx}"
        ref.stratum = f"{src.split}||{ref.label}"
        if path_col:
            value = get_nested(obj, path_col)
            ref.path_value = stringify(value) if value is not None else None
        if fingerprints:
            ref.fingerprint = record_fingerprint(obj)


# ---------------------------------------------------------------------------
# 스캔
# ---------------------------------------------------------------------------


def scan_source(
    root: Path, args: argparse.Namespace, exclude: Path | None = None
) -> Inventory:
    """원본을 훑어 구조/용량/분포를 담은 Inventory 를 만든다."""
    inv = Inventory(root=root, mode="record")

    if is_hf_dataset_dir(root):
        inv.mode = "hf_dataset"
        return scan_hf_dataset(root, inv, args)

    files = list(iter_source_files(root, exclude=exclude))
    if not files:
        inv.mode = "empty"
        return inv

    log(f"[스캔] 파일 {len(files):,}개 확인 중 ...")
    last_report = time.monotonic()
    max_json_bytes = int(args.max_json_load_mb * 1_000_000)

    for pos, path in enumerate(files, start=1):
        rel = str(path.relative_to(root)) if root.is_dir() else path.name
        kind, fmt = classify_file(path)
        size = path.stat().st_size
        src = SourceFile(
            path=path,
            rel=rel,
            size=size,
            kind=kind,
            fmt=fmt,
            split=detect_split(rel),
            encoding=args.encoding,
        )
        inv.total_bytes += size
        inv.formats[fmt] += 1

        if kind == "record":
            try:
                fp = bool(getattr(args, "dedup", True))
                if fmt == "jsonl":
                    index_jsonl(src, args.label_column, fingerprints=fp)
                elif fmt == "json":
                    index_json(src, args.label_column, max_json_bytes, fingerprints=fp)
                elif fmt == "csv":
                    index_csv(src, args.label_column, fingerprints=fp)
                elif fmt == "parquet":
                    index_parquet(src, args.label_column, fingerprints=fp)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                src.error = f"읽기 실패: {exc}"

            if src.error or not src.records:
                # 레코드로 못 읽은 파일은 부속 파일로 강등해 그대로 보존한다.
                if src.error:
                    inv.broken_files.append(f"{rel}: {src.error}")
                src.kind = "aux"
                src.fmt = "aux"
                inv.aux_bytes += size
            else:
                inv.record_bytes += size
                inv.total_records += len(src.records)
                inv.invalid_rows += src.invalid_rows
                for ref in src.records:
                    inv.label_dist[ref.label] += 1
                    inv.meta_dist[ref.meta] += 1
                    inv.split_records[src.split] += 1
                    inv.stratum_bytes[ref.stratum] += ref.size
                inv.split_bytes[src.split] += size
        elif kind == "media":
            inv.media_bytes += size
            inv.total_media += 1
            inv.split_bytes[src.split] += size
            # 미디어는 상위 디렉터리 이름을 클래스로 본다. 레코드 라벨과는 따로 센다.
            inv.media_label_dist[path.parent.name] += 1
        elif kind == "aux":
            inv.aux_bytes += size
        else:
            inv.other_bytes += size

        inv.files.append(src)

        now = time.monotonic()
        if now - last_report >= PROGRESS_INTERVAL_SEC or pos == len(files):
            log(
                f"  [스캔] {pos:,}/{len(files):,} 파일 | "
                f"레코드 {inv.total_records:,}건 | 미디어 {inv.total_media:,}개"
            )
            last_report = now

    # 개인정보 경고는 레코드 파일의 컬럼과 앞부분 값에서만 살핀다.
    for src in inv.files:
        if src.kind != "record" or not src.columns:
            continue
        head_samples = _head_samples(src, limit=50)
        inv.pii_warnings.extend(detect_pii(src.columns, head_samples, src.rel))

    if inv.total_media > 0 and inv.total_media >= max(1, inv.total_records):
        inv.mode = "media"
    elif inv.total_records == 0 and inv.total_media > 0:
        inv.mode = "media"

    return inv


def dedup_inventory(inv: Inventory) -> int:
    """내용 지문이 같은 레코드를 파일 간에도 한 번만 남긴다.

    같은 데이터셋 안에 서로 다른 샤딩본이 함께 들어 있는 경우(예: `*-of-00003` 과
    `*-of-00004` 가 공존)를 잡아낸다. 파일 경로를 정렬해 순회하므로 어떤 사본이
    남는지는 실행마다 같다. 지문이 없는 레코드(중복 검사 비활성 또는 재읽기 실패)는
    건드리지 않는다.
    """
    seen: set[str] = set()
    removed_total = 0

    for src in sorted(
        (f for f in inv.files if f.kind == "record" and f.records), key=lambda f: f.rel
    ):
        kept: list[RecordRef] = []
        removed: list[RecordRef] = []
        for ref in src.records:
            if ref.fingerprint is None:
                kept.append(ref)
                continue
            if ref.fingerprint in seen:
                removed.append(ref)
                continue
            seen.add(ref.fingerprint)
            kept.append(ref)

        if not removed:
            continue

        src.records = kept
        removed_total += len(removed)
        inv.duplicate_sources.append(f"{src.rel}: 중복 {len(removed):,}건 제외")
        inv.total_records -= len(removed)
        for ref in removed:
            inv.label_dist[ref.label] -= 1
            inv.meta_dist[ref.meta] -= 1
            inv.split_records[src.split] -= 1
            inv.stratum_bytes[ref.stratum] -= ref.size
        if not kept:
            # 파일 전체가 중복이면 용량 집계에서도 뺀다(출력에도 쓰이지 않는다).
            inv.record_bytes -= src.size
            inv.split_bytes[src.split] -= src.size

    for counter in (
        inv.label_dist,
        inv.meta_dist,
        inv.split_records,
        inv.stratum_bytes,
        inv.split_bytes,
    ):
        for key in [k for k, v in counter.items() if v <= 0]:
            del counter[key]

    inv.duplicate_records = removed_total
    return removed_total


def _head_samples(src: SourceFile, limit: int) -> list[dict[str, Any]]:
    """개인정보 점검용으로 앞쪽 레코드 몇 건만 읽는다."""
    out: list[dict[str, Any]] = []
    try:
        if src.fmt == "jsonl":
            for _, obj in _reread_jsonl(src):
                out.append(obj)
                if len(out) >= limit:
                    break
        elif src.fmt == "csv":
            for _, obj in _reread_csv(src):
                out.append(obj)
                if len(out) >= limit:
                    break
        elif src.fmt == "json" and src.size < 50_000_000:
            with src.path.open("r", encoding=src.encoding, errors="replace") as handle:
                data = json.load(handle)
            items = data if isinstance(data, list) else data.get(src.json_container_key or "", [])
            out = [i for i in items[:limit] if isinstance(i, dict)]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return out
    return out


def scan_hf_dataset(root: Path, inv: Inventory, args: argparse.Namespace) -> Inventory:
    """load_from_disk 로 열리는 HF Dataset 을 스캔한다."""
    try:
        from datasets import DatasetDict, load_from_disk  # type: ignore
    except ImportError:
        inv.warnings.append(
            "datasets 라이브러리가 없어 HF Dataset 을 읽지 못했습니다. "
            "`pip install datasets` 후 다시 실행하세요."
        )
        inv.mode = "empty"
        return inv

    dataset = load_from_disk(str(root))
    splits = dataset if isinstance(dataset, dict) else {"train": dataset}
    inv.total_bytes, _ = dir_size(root)

    for split_name, ds in splits.items():
        split = normalize_split(split_name)
        nbytes = int(getattr(ds, "dataset_size", 0) or 0) or inv.total_bytes // max(1, len(splits))
        rows = ds.num_rows
        avg = int(nbytes / rows) if rows else 0
        columns = list(ds.column_names)
        label_col = (
            args.label_column
            if args.label_column in columns
            else pick_column(columns, LABEL_CANDIDATES)
        )
        meta_col = pick_column(columns, META_CANDIDATES)

        src = SourceFile(
            path=root,
            rel=f"<hf:{split_name}>",
            size=nbytes,
            kind="record",
            fmt="hf_dataset",
            split=split,
            columns=columns,
            label_column=label_col,
        )
        labels = ds[label_col] if label_col else None
        metas = ds[meta_col] if meta_col else None
        for i in range(rows):
            label = stringify(labels[i]) if labels is not None else "<all>"
            meta = stringify(metas[i]) if metas is not None else "<all>"
            ref = RecordRef(
                index=i,
                size=avg,
                stratum=f"{split}||{label}",
                label=label,
                meta=meta,
                identifier=f"{split}#{i}",
            )
            src.records.append(ref)
            inv.label_dist[label] += 1
            inv.meta_dist[meta] += 1
            inv.stratum_bytes[ref.stratum] += avg

        inv.files.append(src)
        inv.record_bytes += nbytes
        inv.total_records += rows
        inv.split_records[split] += rows
        inv.split_bytes[split] += nbytes
        inv.formats["hf_dataset"] += 1
        inv.pii_warnings.extend(
            detect_pii(columns, [ds[i] for i in range(min(50, rows))], f"<hf:{split_name}>")
        )
    return inv


# ---------------------------------------------------------------------------
# 샘플링 계획
# ---------------------------------------------------------------------------


def allocate_stratified(
    groups: dict[str, list[tuple[str, RecordRef]]],
    budget: int,
    rng: random.Random,
    min_per_stratum: int,
) -> dict[str, list[tuple[str, RecordRef]]]:
    """stratum 별로 예산 바이트를 배분해 레코드를 고른다.

    1) 희소 stratum 보존: 각 stratum 에서 최소 개수를 먼저 확보한다.
    2) 남은 예산을 원본 바이트 비중대로 비례 배분한다.
    3) 그래도 예산이 남으면 라운드로빈으로 채운다.
    """
    picked: dict[str, list[tuple[str, RecordRef]]] = {k: [] for k in groups}
    taken: dict[str, int] = {k: 0 for k in groups}
    shuffled: dict[str, list[tuple[str, RecordRef]]] = {}
    for key in sorted(groups):
        items = list(groups[key])
        rng.shuffle(items)
        shuffled[key] = items

    remaining = budget

    # 1) 희소 클래스 최소 보존
    for key in sorted(shuffled):
        items = shuffled[key]
        quota = min(min_per_stratum, len(items))
        for i in range(quota):
            size = items[i][1].size
            if size > remaining:
                break
            picked[key].append(items[i])
            remaining -= size
            taken[key] += 1

    # 2) 원본 바이트 비중대로 비례 배분
    total_bytes = sum(ref.size for items in shuffled.values() for _, ref in items)
    if total_bytes > 0 and remaining > 0:
        share_budget = remaining
        for key in sorted(shuffled):
            items = shuffled[key]
            group_bytes = sum(ref.size for _, ref in items)
            quota_bytes = int(share_budget * (group_bytes / total_bytes))
            used = 0
            for entry in items[taken[key] :]:
                size = entry[1].size
                if used + size > quota_bytes or size > remaining:
                    break
                picked[key].append(entry)
                used += size
                remaining -= size
                taken[key] += 1

    # 3) 남은 예산 라운드로빈
    if remaining > 0:
        active = [k for k in sorted(shuffled) if taken[k] < len(shuffled[k])]
        while active and remaining > 0:
            still: list[str] = []
            for key in active:
                pos = taken[key]
                if pos >= len(shuffled[key]):
                    continue
                size = shuffled[key][pos][1].size
                if size <= remaining:
                    picked[key].append(shuffled[key][pos])
                    remaining -= size
                    taken[key] += 1
                    if taken[key] < len(shuffled[key]):
                        still.append(key)
            if not still:
                break
            active = still

    return picked


def build_plan(inv: Inventory, args: argparse.Namespace) -> Plan:
    """Inventory 를 바탕으로 축소 계획을 세운다."""
    rng = random.Random(args.seed)
    plan = Plan()

    target_bytes = resolve_target_bytes(args)
    max_bytes = resolve_max_bytes(args)

    aux_files = [f for f in inv.files if f.kind in {"aux", "other"}]
    aux_bytes = sum(f.size for f in aux_files)
    plan.aux_files = aux_files

    media_files = [f for f in inv.files if f.kind == "media"]
    record_files = [f for f in inv.files if f.kind == "record" and f.records]

    budget = int(target_bytes * (1 - SIZE_SAFETY_MARGIN)) - aux_bytes
    if budget <= 0:
        plan.notes.append(
            "부속 파일만으로도 목표 용량을 넘습니다. 부속 파일 복사를 건너뜁니다."
        )
        plan.aux_files = []
        aux_bytes = 0
        budget = int(target_bytes * (1 - SIZE_SAFETY_MARGIN))

    # 미디어가 주된 데이터면 미디어부터 고르고 메타데이터를 거기에 맞춘다.
    if inv.mode == "media" and media_files:
        media_budget = int(budget * args.media_budget_ratio) if record_files else budget
        groups: dict[str, list[tuple[str, RecordRef]]] = defaultdict(list)
        for f in media_files:
            label = f.path.parent.name if f.path.parent != inv.root else "<root>"
            ref = RecordRef(
                index=0,
                size=f.size,
                stratum=f"{f.split}||{label}",
                label=label,
                meta="<all>",
                identifier=f.rel,
            )
            groups[ref.stratum].append((f.rel, ref))
        chosen = allocate_stratified(groups, media_budget, rng, args.min_per_class)
        keep = {rel for entries in chosen.values() for rel, _ in entries}
        plan.media_files = [f for f in media_files if f.rel in keep]
        plan.kept_media_rel = keep
        budget -= sum(f.size for f in plan.media_files)
    elif media_files:
        # 미디어가 부수적이면 예산의 일부만 배정한다.
        media_budget = int(budget * args.media_budget_ratio)
        groups = defaultdict(list)
        for f in media_files:
            label = f.path.parent.name
            ref = RecordRef(
                index=0,
                size=f.size,
                stratum=f"{f.split}||{label}",
                label=label,
                meta="<all>",
                identifier=f.rel,
            )
            groups[ref.stratum].append((f.rel, ref))
        chosen = allocate_stratified(groups, media_budget, rng, args.min_per_class)
        keep = {rel for entries in chosen.values() for rel, _ in entries}
        plan.media_files = [f for f in media_files if f.rel in keep]
        plan.kept_media_rel = keep
        budget -= sum(f.size for f in plan.media_files)

    # 레코드 선택
    if record_files and budget > 0:
        groups = defaultdict(list)
        for f in record_files:
            for ref in f.records:
                groups[ref.stratum].append((f.rel, ref))

        record_total = sum(ref.size for entries in groups.values() for _, ref in entries)
        if record_total <= budget:
            chosen = {k: list(v) for k, v in groups.items()}
            plan.notes.append(
                "원본 레코드 전체가 목표 용량 안에 들어가 전량을 포함했습니다."
            )
        else:
            chosen = allocate_stratified(groups, budget, rng, args.min_per_class)

        for entries in chosen.values():
            for rel, ref in entries:
                plan.selected.setdefault(rel, []).append(ref)
        for refs in plan.selected.values():
            refs.sort(key=lambda r: r.index)

    # 미디어-메타데이터 참조 정합성: 선택된 미디어만 남기도록 레코드를 한 번 더 거른다.
    if plan.kept_media_rel:
        drop_total = 0
        for rel, refs in list(plan.selected.items()):
            src = next((f for f in record_files if f.rel == rel), None)
            if src is None or not src.path_column:
                continue
            kept: list[RecordRef] = []
            for ref in refs:
                if ref.path_value is None:
                    kept.append(ref)
                    continue
                if _media_matches(ref.path_value, plan.kept_media_rel):
                    kept.append(ref)
                else:
                    drop_total += 1
            plan.selected[rel] = kept
        if drop_total:
            plan.notes.append(
                f"선택되지 않은 미디어를 가리키는 메타데이터 레코드 {drop_total:,}건을 제외했습니다."
            )

    # 분포 집계는 모든 필터를 거친 뒤 한 번만 한다(중간 집계는 참조 필터와 어긋난다).
    recompute_plan_stats(inv, plan)

    plan.estimated_bytes = (
        sum(f.size for f in plan.aux_files)
        + sum(f.size for f in plan.media_files)
        + sum(ref.size for refs in plan.selected.values() for ref in refs)
    )
    if plan.estimated_bytes > max_bytes:
        plan.notes.append(
            "예상 용량이 상한을 넘어 보입니다. 생성 후 자동 축소 후처리가 동작합니다."
        )
    return plan


def recompute_plan_stats(inv: Inventory, plan: Plan) -> None:
    """선택 결과로부터 개수와 분포를 다시 계산한다."""
    plan.label_dist = Counter()
    plan.meta_dist = Counter()
    plan.media_label_dist = Counter()
    plan.split_records = Counter()

    by_rel = {f.rel: f for f in inv.files}
    for rel, refs in plan.selected.items():
        split = by_rel[rel].split if rel in by_rel else "all"
        for ref in refs:
            plan.label_dist[ref.label] += 1
            plan.meta_dist[ref.meta] += 1
            plan.split_records[split] += 1
    for f in plan.media_files:
        plan.media_label_dist[f.path.parent.name] += 1

    plan.selected_records = sum(len(v) for v in plan.selected.values())
    plan.selected_media = len(plan.media_files)


def _media_matches(path_value: str, kept: set[str]) -> bool:
    """메타데이터의 경로 값이 선택된 미디어 중 하나를 가리키는지 확인한다."""
    norm = path_value.replace("\\", "/").lstrip("./")
    if norm in kept:
        return True
    base = os.path.basename(norm)
    return any(k.endswith("/" + base) or k == base for k in kept)


def resolve_target_bytes(args: argparse.Namespace) -> int:
    """목표 용량을 바이트로 환산한다."""
    if args.target_size_gib is not None:
        return int(args.target_size_gib * GIB)
    return int(args.target_size_gb * GB)


def resolve_max_bytes(args: argparse.Namespace) -> int:
    """상한 용량을 바이트로 환산한다."""
    if args.max_size_gib is not None:
        return int(args.max_size_gib * GIB)
    return int(args.max_size_gb * GB)


# ---------------------------------------------------------------------------
# 출력 생성
# ---------------------------------------------------------------------------


def write_subset(inv: Inventory, plan: Plan, out_root: Path, args: argparse.Namespace) -> None:
    """계획대로 축소 데이터셋을 출력 디렉터리에 쓴다."""
    out_root.mkdir(parents=True, exist_ok=True)

    if inv.mode == "hf_dataset":
        write_hf_subset(inv, plan, out_root, args)
    else:
        record_files = [f for f in inv.files if f.kind == "record" and f.records]
        for src in record_files:
            refs = plan.selected.get(src.rel, [])
            if not refs:
                continue
            dest = out_root / src.rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.fmt == "jsonl":
                _write_jsonl_subset(src, refs, dest)
                plan.output_paths[src.rel] = [str(dest)]
            elif src.fmt == "json":
                _write_json_subset(src, refs, dest)
                plan.output_paths[src.rel] = [str(dest)]
            elif src.fmt == "csv":
                _write_csv_subset(src, refs, dest)
                plan.output_paths[src.rel] = [str(dest)]
            elif src.fmt == "parquet":
                plan.output_paths[src.rel] = _write_parquet_subset(src, refs, dest, args)
            log(f"  [쓰기] {src.rel}: {len(refs):,}건")

    for f in plan.media_files:
        dest = out_root / f.rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f.path, dest)
    if plan.media_files:
        log(f"  [쓰기] 미디어 {len(plan.media_files):,}개 복사")

    for f in plan.aux_files:
        dest = out_root / f.rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f.path, dest)
    if plan.aux_files:
        log(f"  [쓰기] 부속 파일 {len(plan.aux_files):,}개 복사")


def _write_jsonl_subset(src: SourceFile, refs: Sequence[RecordRef], dest: Path) -> None:
    """선택된 인덱스의 JSONL 줄만 다시 쓴다."""
    wanted = {ref.index for ref in refs}
    idx = 0
    written = 0
    with src.path.open("r", encoding=src.encoding, errors="replace") as fin, dest.open(
        "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            if not line.strip():
                continue
            if idx in wanted:
                fout.write(line if line.endswith("\n") else line + "\n")
                written += 1
            idx += 1
            if written == len(wanted):
                break


def _write_json_subset(src: SourceFile, refs: Sequence[RecordRef], dest: Path) -> None:
    """JSON 컨테이너 구조를 유지한 채 선택된 레코드만 남긴다."""
    with src.path.open("r", encoding=src.encoding, errors="replace") as handle:
        data = json.load(handle)
    wanted = {ref.index for ref in refs}
    if isinstance(data, list):
        out: Any = [item for i, item in enumerate(data) if i in wanted]
    else:
        key = src.json_container_key or ""
        records = data.get(key, [])
        kept = [item for i, item in enumerate(records) if i in wanted]
        out = dict(data)
        out[key] = kept
        # 개수를 담은 필드가 있으면 함께 맞춘다.
        for count_key in ("total_samples", "total_records", "num_records", "count", "n"):
            if count_key in out and isinstance(out[count_key], int):
                out[count_key] = len(kept)
    with dest.open("w", encoding="utf-8") as fout:
        json.dump(out, fout, ensure_ascii=False, indent=2)
        fout.write("\n")


def _write_csv_subset(src: SourceFile, refs: Sequence[RecordRef], dest: Path) -> None:
    """헤더와 컬럼 순서를 유지한 채 선택된 행만 쓴다."""
    wanted = {ref.index for ref in refs}
    delimiter = src.csv_dialect.get("delimiter", ",")
    header = src.csv_dialect.get("header") or src.columns
    with src.path.open("r", encoding=src.encoding, errors="replace", newline="") as fin, dest.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.DictReader(fin, delimiter=delimiter)
        writer = csv.DictWriter(fout, fieldnames=header, delimiter=delimiter)
        writer.writeheader()
        for idx, row in enumerate(reader):
            if idx in wanted:
                writer.writerow({k: row.get(k, "") for k in header})


def _write_parquet_subset(
    src: SourceFile, refs: Sequence[RecordRef], dest: Path, args: argparse.Namespace
) -> list[str]:
    """스키마를 유지한 채 선택된 행만 Parquet 으로 쓰고, 쓴 파일 경로를 돌려준다."""
    import pyarrow as pa  # type: ignore
    import pyarrow.parquet as pq  # type: ignore

    wanted = sorted(ref.index for ref in refs)
    pf = pq.ParquetFile(str(src.path))
    writer: pq.ParquetWriter | None = None
    # 선택된 행이 shard 하나에 다 들어가면 원본 파일명을 그대로 쓴다.
    shard_rows = args.parquet_shard_rows if len(wanted) > args.parquet_shard_rows else 0
    shard_idx = 0
    written: list[str] = []
    buffered: list[pa.Table] = []
    buffered_rows = 0
    offset = 0
    pos = 0

    def flush() -> None:
        nonlocal buffered, buffered_rows, shard_idx, writer
        if not buffered:
            return
        table = pa.concat_tables(buffered)
        if shard_rows > 0:
            shard_path = dest.with_name(f"{dest.stem}-{shard_idx:05d}{dest.suffix}")
            pq.write_table(table, str(shard_path))
            written.append(str(shard_path))
            shard_idx += 1
        else:
            if writer is None:
                writer = pq.ParquetWriter(str(dest), table.schema)
                written.append(str(dest))
            writer.write_table(table)
        buffered = []
        buffered_rows = 0

    for batch in pf.iter_batches(batch_size=8192):
        rows = batch.num_rows
        picks: list[int] = []
        while pos < len(wanted) and wanted[pos] < offset + rows:
            picks.append(wanted[pos] - offset)
            pos += 1
        if picks:
            table = pa.Table.from_batches([batch]).take(pa.array(picks))
            buffered.append(table)
            buffered_rows += len(picks)
            if shard_rows > 0 and buffered_rows >= shard_rows:
                flush()
        offset += rows
        if pos >= len(wanted):
            break
    flush()
    if writer is not None:
        writer.close()
    return written


def write_hf_subset(inv: Inventory, plan: Plan, out_root: Path, args: argparse.Namespace) -> None:
    """HF DatasetDict 를 split 구조 그대로 축소 저장한다."""
    from datasets import DatasetDict, load_from_disk  # type: ignore

    dataset = load_from_disk(str(inv.root))
    splits = dataset if isinstance(dataset, dict) else {"train": dataset}
    out: dict[str, Any] = {}
    for split_name, ds in splits.items():
        rel = f"<hf:{split_name}>"
        refs = plan.selected.get(rel, [])
        indices = sorted(ref.index for ref in refs)
        out[split_name] = ds.select(indices)
        log(f"  [쓰기] {split_name}: {len(indices):,}건")
    if args.hf_output_format == "parquet":
        for split_name, ds in out.items():
            dest = out_root / f"{split_name}.parquet"
            ds.to_parquet(str(dest))
    else:
        DatasetDict(out).save_to_disk(str(out_root))


# ---------------------------------------------------------------------------
# 상한 강제 및 검증
# ---------------------------------------------------------------------------


def enforce_max_size(
    inv: Inventory, plan: Plan, out_root: Path, args: argparse.Namespace, max_bytes: int
) -> tuple[int, int]:
    """출력 용량이 상한을 넘으면 계층 비율을 유지한 채 추가로 줄인다."""
    rounds = 0
    actual, _ = dir_size(out_root)
    while actual > max_bytes and rounds < args.max_shrink_rounds:
        rounds += 1
        ratio = (max_bytes * (1 - SIZE_SAFETY_MARGIN)) / actual
        log(
            f"[축소] 출력 {human_bytes(actual)} 이 상한 {human_bytes(max_bytes)} 를 넘어"
            f" {ratio:.3f} 배로 재축소합니다 (라운드 {rounds})."
        )
        # 미디어와 레코드를 같은 비율로 줄인다. 희소 클래스는 최소 1건씩 남긴다.
        rng = random.Random(args.seed + rounds)
        for rel, refs in list(plan.selected.items()):
            by_label: dict[str, list[RecordRef]] = defaultdict(list)
            for ref in refs:
                by_label[ref.label].append(ref)
            kept: list[RecordRef] = []
            for label in sorted(by_label):
                items = by_label[label]
                keep_n = max(1, int(len(items) * ratio))
                rng.shuffle(items)
                kept.extend(items[:keep_n])
            kept.sort(key=lambda r: r.index)
            plan.selected[rel] = kept
        if plan.media_files:
            by_label_media: dict[str, list[SourceFile]] = defaultdict(list)
            for f in plan.media_files:
                by_label_media[f.path.parent.name].append(f)
            kept_media: list[SourceFile] = []
            for label in sorted(by_label_media):
                items = by_label_media[label]
                keep_n = max(1, int(len(items) * ratio))
                rng.shuffle(items)
                kept_media.extend(items[:keep_n])
            plan.media_files = kept_media
            plan.kept_media_rel = {f.rel for f in kept_media}

        recompute_plan_stats(inv, plan)

        shutil.rmtree(out_root)
        write_subset(inv, plan, out_root, args)
        actual, _ = dir_size(out_root)

    return actual, rounds


def verify_output(inv: Inventory, out_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """출력물을 다시 읽어 재로딩 가능 여부와 스키마 일치를 확인한다."""
    result: dict[str, Any] = {
        "reloadable": True,
        "schema_match": True,
        "checked_files": 0,
        "issues": [],
        "output_counts": {},
    }
    src_columns = {f.rel: list(f.columns) for f in inv.files if f.kind == "record"}

    if inv.mode == "hf_dataset":
        try:
            from datasets import load_from_disk  # type: ignore

            if args.hf_output_format == "parquet":
                import pyarrow.parquet as pq  # type: ignore

                for p in sorted(out_root.glob("*.parquet")):
                    table = pq.read_table(str(p))
                    result["output_counts"][p.name] = table.num_rows
                    result["checked_files"] += 1
            else:
                loaded = load_from_disk(str(out_root))
                splits = loaded if isinstance(loaded, dict) else {"train": loaded}
                for name, ds in splits.items():
                    result["output_counts"][name] = ds.num_rows
                    result["checked_files"] += 1
                    expected = src_columns.get(f"<hf:{name}>")
                    if expected and set(expected) != set(ds.column_names):
                        result["schema_match"] = False
                        result["issues"].append(f"{name}: 컬럼 구성이 원본과 다릅니다.")
        except Exception as exc:  # noqa: BLE001 - 검증 실패는 보고 대상
            result["reloadable"] = False
            result["issues"].append(f"HF Dataset 재로딩 실패: {exc}")
        return result

    for path in sorted(out_root.rglob("*")):
        if not path.is_file() or should_skip(path.relative_to(out_root)):
            continue
        rel = str(path.relative_to(out_root))
        ext = path.suffix.lower()
        try:
            if ext in {".jsonl", ".ndjson"}:
                count = 0
                cols: set[str] = set()
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if line.strip():
                            obj = json.loads(line)
                            if isinstance(obj, dict):
                                cols |= set(obj.keys())
                            count += 1
                result["output_counts"][rel] = count
                result["checked_files"] += 1
                expected = src_columns.get(rel)
                if expected and not cols.issubset(set(expected)):
                    result["schema_match"] = False
                    result["issues"].append(f"{rel}: 원본에 없는 컬럼이 있습니다.")
            elif ext == ".json":
                with path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, list):
                    result["output_counts"][rel] = len(data)
                elif isinstance(data, dict):
                    biggest = max(
                        (len(v) for v in data.values() if isinstance(v, list)), default=0
                    )
                    result["output_counts"][rel] = biggest
                result["checked_files"] += 1
            elif ext in {".csv", ".tsv"}:
                delimiter, header = _sniff_csv(path, "utf-8")
                with path.open("r", encoding="utf-8", newline="") as handle:
                    rows = sum(1 for _ in csv.DictReader(handle, delimiter=delimiter))
                result["output_counts"][rel] = rows
                result["checked_files"] += 1
                expected = src_columns.get(rel)
                if expected and set(header) != set(expected):
                    result["schema_match"] = False
                    result["issues"].append(f"{rel}: CSV 헤더가 원본과 다릅니다.")
            elif ext == ".parquet":
                import pyarrow.parquet as pq  # type: ignore

                table = pq.read_table(str(path))
                result["output_counts"][rel] = table.num_rows
                result["checked_files"] += 1
        except Exception as exc:  # noqa: BLE001 - 검증 실패는 보고 대상
            result["reloadable"] = False
            result["issues"].append(f"{rel}: 재로딩 실패 - {exc}")

    return result


def check_media_references(out_root: Path, plan: Plan) -> list[str]:
    """출력 메타데이터가 가리키는 미디어가 실제로 존재하는지 확인한다."""
    issues: list[str] = []
    if not plan.kept_media_rel:
        return issues
    present = {
        str(p.relative_to(out_root)).replace("\\", "/")
        for p in out_root.rglob("*")
        if p.is_file()
    }
    basenames = {os.path.basename(p) for p in present}
    missing = 0
    for refs in plan.selected.values():
        for ref in refs:
            if ref.path_value is None:
                continue
            norm = ref.path_value.replace("\\", "/").lstrip("./")
            if norm not in present and os.path.basename(norm) not in basenames:
                missing += 1
    if missing:
        issues.append(f"출력 메타데이터에서 참조가 끊긴 미디어 {missing:,}건이 있습니다.")
    return issues


# ---------------------------------------------------------------------------
# 보고서 / manifest
# ---------------------------------------------------------------------------


def build_manifest(
    inv: Inventory,
    plan: Plan,
    out_root: Path,
    args: argparse.Namespace,
    actual_bytes: int,
    verification: dict[str, Any],
) -> dict[str, Any]:
    """선택된 파일/샘플 정보를 담은 manifest 를 만든다."""
    entries: list[dict[str, Any]] = []
    truncated = False
    by_rel = {f.rel: f for f in inv.files}

    for rel in sorted(plan.selected):
        src = by_rel.get(rel)
        if src is None:
            continue
        for ref in plan.selected[rel]:
            if len(entries) >= args.manifest_max_records:
                truncated = True
                break
            outputs = plan.output_paths.get(rel) or [str(out_root / rel)]
            entries.append(
                {
                    "identifier": ref.identifier,
                    "source_path": str(src.path),
                    "source_rel": rel,
                    "output_path": outputs[0] if len(outputs) == 1 else outputs,
                    "record_index": ref.index,
                    "approx_bytes": ref.size,
                    "split": src.split,
                    "label": ref.label,
                }
            )
        if truncated:
            break

    media_entries: list[dict[str, Any]] = []
    for f in plan.media_files[: args.manifest_max_records]:
        media_entries.append(
            {
                "identifier": f.rel,
                "source_path": str(f.path),
                "output_path": str(out_root / f.rel),
                "bytes": f.size,
                "split": f.split,
                "label": f.path.parent.name,
            }
        )

    file_summary = [
        {
            "source_rel": rel,
            "format": by_rel[rel].fmt if rel in by_rel else "unknown",
            "split": by_rel[rel].split if rel in by_rel else "all",
            "source_records": len(by_rel[rel].records) if rel in by_rel else None,
            "selected_records": len(refs),
            "output_paths": plan.output_paths.get(rel) or [str(out_root / rel)],
        }
        for rel, refs in sorted(plan.selected.items())
    ]

    return {
        "generated_by": "scripts/create_5gb_subset.py",
        "input_path": str(inv.root),
        "output_path": str(out_root),
        "seed": args.seed,
        "detected_mode": inv.mode,
        "formats": dict(inv.formats),
        "target_bytes": resolve_target_bytes(args),
        "max_bytes": resolve_max_bytes(args),
        "actual_output_bytes": actual_bytes,
        "dataset_id": args.dataset_id,
        "dedup_enabled": bool(getattr(args, "dedup", True)),
        "duplicate_records_excluded": inv.duplicate_records,
        "duplicate_sources": inv.duplicate_sources,
        "source_total_bytes": inv.total_bytes,
        "source_records": inv.total_records,
        "selected_records": plan.selected_records,
        "source_media_files": inv.total_media,
        "selected_media_files": plan.selected_media,
        "aux_files_copied": len(plan.aux_files),
        "split_records_source": dict(inv.split_records),
        "split_records_subset": dict(plan.split_records),
        "label_distribution_source": dict(inv.label_dist),
        "label_distribution_subset": dict(plan.label_dist),
        "media_label_distribution_source": dict(inv.media_label_dist),
        "media_label_distribution_subset": dict(plan.media_label_dist),
        "verification": verification,
        "files": file_summary,
        "records_truncated": truncated,
        "manifest_max_records": args.manifest_max_records,
        "records": entries,
        "media": media_entries,
    }


def _dist_table(source: Counter, subset: Counter, limit: int = 40) -> str:
    """원본/축소 분포 비교 표를 마크다운으로 만든다."""
    keys = [k for k, _ in source.most_common(limit)]
    for k in subset:
        if k not in keys and len(keys) < limit:
            keys.append(k)
    total_s = sum(source.values()) or 1
    total_t = sum(subset.values()) or 1
    lines = [
        "| 값 | 원본 건수 | 원본 비율 | 축소 건수 | 축소 비율 | 비율 차이 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key in keys:
        s = source.get(key, 0)
        t = subset.get(key, 0)
        ps = s / total_s * 100
        pt = t / total_t * 100
        lines.append(
            f"| `{key}` | {s:,} | {ps:.2f}% | {t:,} | {pt:.2f}% | {pt - ps:+.2f}pp |"
        )
    if len(source) > limit:
        lines.append(f"| … | 그 외 {len(source) - limit:,}종 생략 | | | | |")
    return "\n".join(lines)


def build_report(
    inv: Inventory,
    plan: Plan,
    out_root: Path,
    args: argparse.Namespace,
    actual_bytes: int,
    shrink_rounds: int,
    verification: dict[str, Any],
    reference_issues: Sequence[str],
    command: str,
) -> str:
    """축소 전후 비교 보고서를 마크다운으로 만든다."""
    max_bytes = resolve_max_bytes(args)
    ratio = (actual_bytes / inv.total_bytes * 100) if inv.total_bytes else 0.0
    rec_ratio = (
        plan.selected_records / inv.total_records * 100 if inv.total_records else 0.0
    )

    split_lines = [
        "| split | 원본 건수 | 축소 건수 | 원본 비율 | 축소 비율 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    total_s = sum(inv.split_records.values()) or 1
    total_t = sum(plan.split_records.values()) or 1
    for split in sorted(set(inv.split_records) | set(plan.split_records)):
        s = inv.split_records.get(split, 0)
        t = plan.split_records.get(split, 0)
        split_lines.append(
            f"| `{split}` | {s:,} | {t:,} | {s / total_s * 100:.2f}% | {t / total_t * 100:.2f}% |"
        )

    file_lines = [
        "| 원본 파일 | 형식 | split | 원본 레코드 | 선택 레코드 |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    by_rel = {f.rel: f for f in inv.files}
    for rel in sorted(plan.selected):
        src = by_rel.get(rel)
        if src is None:
            continue
        file_lines.append(
            f"| `{rel}` | {src.fmt} | `{src.split}` | {len(src.records):,} | "
            f"{len(plan.selected[rel]):,} |"
        )

    pii_block = (
        "\n".join(f"- {w}" for w in dict.fromkeys(inv.pii_warnings))
        if inv.pii_warnings
        else "- 개인정보로 의심되는 컬럼/값은 발견되지 않았습니다."
    )
    dedup_method = (
        "레코드 내용 지문(blake2b-128, 키 정렬·수집시각 제외)으로 파일 간 중복까지 검사"
        if getattr(args, "dedup", True)
        else "비활성(--no-dedup) — 파일 안에서 인덱스 단위 중복만 없음"
    )

    problem_items: list[str] = []
    problem_items.extend(plan.notes)
    problem_items.extend(inv.warnings)
    problem_items.extend(inv.duplicate_sources)
    problem_items.extend(f"손상/미해석 파일 — {b}" for b in inv.broken_files)
    problem_items.extend(verification.get("issues", []))
    problem_items.extend(reference_issues)
    if inv.invalid_rows:
        problem_items.append(f"파싱 실패로 제외한 행 {inv.invalid_rows:,}건")
    if shrink_rounds:
        problem_items.append(f"상한 초과로 추가 축소를 {shrink_rounds}회 수행")
    problems = (
        "\n".join(f"- {p}" for p in problem_items) if problem_items else "- 특이사항 없음"
    )

    within = actual_bytes <= max_bytes

    return f"""# 5GB 축소 데이터셋 생성 보고서

## 1. 경로와 형식

| 항목 | 값 |
| --- | --- |
| 원본 경로 | `{inv.root}` |
| 출력 경로 | `{out_root}` |
| 감지된 구조 | `{inv.mode}` |
| 형식 구성 | {", ".join(f"{k} x{v}" for k, v in inv.formats.most_common()) or "없음"} |
| 사용 시드 | `{args.seed}` |

## 2. 용량 비교

| 항목 | 값 |
| --- | --- |
| 원본 전체 용량 | {human_bytes(inv.total_bytes)} ({inv.total_bytes:,} bytes) |
| 축소 전체 용량 | {human_bytes(actual_bytes)} ({actual_bytes:,} bytes) |
| 축소 비율 | {ratio:.2f}% (원본 대비) |
| 목표 용량 | {human_bytes(resolve_target_bytes(args))} |
| 상한 용량 | {human_bytes(max_bytes)} ({max_bytes:,} bytes) |
| 상한 충족 | {"충족 (<= 상한)" if within else "미충족 — 추가 축소 필요"} |
| 추가 축소 라운드 | {shrink_rounds} |

원본 내역: 레코드 파일 {human_bytes(inv.record_bytes)} / 미디어 {human_bytes(inv.media_bytes)}
/ 부속 파일 {human_bytes(inv.aux_bytes)} / 기타 {human_bytes(inv.other_bytes)}

압축은 적용하지 않았습니다(압축 전 = 압축 후 = 위 표의 축소 전체 용량).

## 3. 샘플 수 비교

| 항목 | 원본 | 축소 | 비율 |
| --- | ---: | ---: | ---: |
| 레코드 | {inv.total_records:,} | {plan.selected_records:,} | {rec_ratio:.2f}% |
| 미디어 파일 | {inv.total_media:,} | {plan.selected_media:,} | {(plan.selected_media / inv.total_media * 100) if inv.total_media else 0:.2f}% |
| 부속 파일 | {sum(1 for f in inv.files if f.kind in {"aux", "other"}):,} | {len(plan.aux_files):,} | - |

## 4. split 별 비교

{chr(10).join(split_lines)}

## 5. 라벨/클래스 분포 비교

{_dist_table(inv.label_dist, plan.label_dist)}

## 6. 메타데이터 분포 비교

{_dist_table(inv.meta_dist, plan.meta_dist)}

## 6-1. 미디어 클래스 분포 비교

{_dist_table(inv.media_label_dist, plan.media_label_dist) if inv.media_label_dist else "미디어 파일이 없습니다."}

## 7. 파일별 선택 결과

{chr(10).join(file_lines) if len(file_lines) > 2 else "선택된 레코드 파일이 없습니다."}

## 8. 데이터 품질

| 항목 | 값 |
| --- | --- |
| 파싱 실패로 제외한 행 | {inv.invalid_rows:,} |
| 손상/미해석 파일 | {len(inv.broken_files):,} |
| 중복 검사 방식 | {dedup_method} |
| 내용 중복으로 제외한 레코드 | {inv.duplicate_records:,} |
| 재로딩 가능 | {"예" if verification.get("reloadable") else "아니오"} |
| 원본과 스키마 일치 | {"예" if verification.get("schema_match") else "아니오"} |
| 검증한 출력 파일 수 | {verification.get("checked_files", 0):,} |

## 9. 개인정보 점검

{pii_block}

값 자체는 변형하지 않았습니다. 배포 전에 직접 확인하세요.

## 10. 축소 중 발견한 문제

{problems}

## 11. 재현 방법

실제 실행한 명령:

```bash
{command}
```

같은 원본과 같은 시드(`{args.seed}`)를 쓰면 동일한 샘플이 선택됩니다.
선택된 레코드 목록은 `reports/subset_5gb_manifest.json` 에 있습니다.
"""


# ---------------------------------------------------------------------------
# dry-run 출력
# ---------------------------------------------------------------------------


def print_summary(inv: Inventory, plan: Plan, args: argparse.Namespace) -> None:
    """dry-run 및 실행 전 요약을 출력한다."""
    log("")
    log("=" * 68)
    log("원본 요약")
    log("=" * 68)
    log(f"  경로            : {inv.root}")
    log(f"  감지된 구조     : {inv.mode}")
    log(f"  형식 구성       : {dict(inv.formats)}")
    log(f"  전체 용량       : {human_bytes(inv.total_bytes)} ({inv.total_bytes:,} bytes)")
    log(f"  전체 레코드 수  : {inv.total_records:,}")
    log(f"  미디어 파일 수  : {inv.total_media:,}")
    log(f"  부속 파일 수    : {sum(1 for f in inv.files if f.kind in {'aux', 'other'}):,}")
    log(f"  파싱 실패 행    : {inv.invalid_rows:,}")

    log("  split 구조      :")
    for split in sorted(set(inv.split_records) | set(inv.split_bytes)):
        log(
            f"    - {split:<12} 레코드 {inv.split_records.get(split, 0):>10,} | "
            f"{human_bytes(inv.split_bytes.get(split, 0))}"
        )

    log("  라벨/클래스 분포 (상위 15):")
    total = sum(inv.label_dist.values()) or 1
    for label, count in inv.label_dist.most_common(15):
        log(f"    - {label:<24} {count:>10,}  ({count / total * 100:5.2f}%)")
    if len(inv.label_dist) > 15:
        log(f"    - … 그 외 {len(inv.label_dist) - 15:,}종")

    if inv.media_label_dist:
        log("  미디어 클래스 분포 (상위 15):")
        mtotal = sum(inv.media_label_dist.values()) or 1
        for label, count in inv.media_label_dist.most_common(15):
            log(f"    - {label:<24} {count:>10,}  ({count / mtotal * 100:5.2f}%)")

    avg = inv.record_bytes / inv.total_records if inv.total_records else 0
    log(f"  평균 레코드 크기: {human_bytes(avg)}")

    log("")
    log("=" * 68)
    log("축소 계획")
    log("=" * 68)
    log(f"  목표 용량       : {human_bytes(resolve_target_bytes(args))}")
    log(f"  상한 용량       : {human_bytes(resolve_max_bytes(args))}")
    log(f"  예상 선택 레코드: {plan.selected_records:,}")
    log(f"  예상 선택 미디어: {plan.selected_media:,}")
    log(
        f"  예상 출력 용량  : {human_bytes(plan.estimated_bytes)} "
        f"({plan.estimated_bytes:,} bytes)"
    )
    log("  예상 라벨 분포 (상위 15):")
    ttotal = sum(plan.label_dist.values()) or 1
    for label, count in plan.label_dist.most_common(15):
        log(f"    - {label:<24} {count:>10,}  ({count / ttotal * 100:5.2f}%)")
    if plan.media_label_dist:
        log("  예상 미디어 클래스 분포 (상위 15):")
        mtotal = sum(plan.media_label_dist.values()) or 1
        for label, count in plan.media_label_dist.most_common(15):
            log(f"    - {label:<24} {count:>10,}  ({count / mtotal * 100:5.2f}%)")
    for note in plan.notes:
        log(f"  * {note}")
    if inv.pii_warnings:
        log("  개인정보 경고:")
        for warning in list(dict.fromkeys(inv.pii_warnings))[:10]:
            log(f"    ! {warning}")
    log("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """명령행 인자를 해석한다."""
    parser = argparse.ArgumentParser(
        description="원본 데이터셋을 보존한 채 5GB 이하 축소 데이터셋을 만든다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-path",
        default=None,
        help="원본 데이터셋 경로 (읽기 전용). --dataset-id 를 쓰면 생략할 수 있다.",
    )
    parser.add_argument(
        "--dataset-id",
        default=None,
        help="Hugging Face Hub 데이터셋 ID (예: user/name). 내려받은 뒤 원본으로 쓴다.",
    )
    parser.add_argument(
        "--dataset-revision", default=None, help="--dataset-id 사용 시 고정할 리비전"
    )
    parser.add_argument(
        "--dataset-cache-dir",
        default="./data/hf_source",
        help="--dataset-id 로 내려받은 원본을 둘 디렉터리",
    )
    parser.add_argument(
        "--no-dedup",
        dest="dedup",
        action="store_false",
        help="내용 기반 중복 레코드 검사를 끈다 (기본은 켜짐)",
    )
    parser.set_defaults(dedup=True)
    parser.add_argument("--output-path", default="./data/subset_5gb", help="축소 결과 저장 경로")
    parser.add_argument("--target-size-gb", type=float, default=4.8, help="목표 용량 (GB, 10^9)")
    parser.add_argument("--max-size-gb", type=float, default=5.0, help="상한 용량 (GB, 10^9)")
    parser.add_argument("--target-size-gib", type=float, default=None, help="목표 용량 (GiB, 2^30)")
    parser.add_argument("--max-size-gib", type=float, default=None, help="상한 용량 (GiB, 2^30)")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드")
    parser.add_argument("--dry-run", action="store_true", help="복사/변환 없이 예상만 출력")
    parser.add_argument(
        "--overwrite", action="store_true", help="기존 출력 디렉터리를 덮어쓴다"
    )
    parser.add_argument(
        "--label-column", default=None, help="계층 샘플링에 쓸 라벨 컬럼 (미지정 시 자동 감지)"
    )
    parser.add_argument(
        "--min-per-class",
        type=int,
        default=50,
        help="희소 클래스 보존을 위해 stratum 마다 우선 확보할 최소 개수",
    )
    parser.add_argument(
        "--media-budget-ratio",
        type=float,
        default=0.7,
        help="레코드와 미디어가 함께 있을 때 미디어에 배정할 예산 비율",
    )
    parser.add_argument(
        "--max-json-load-mb",
        type=float,
        default=512.0,
        help="단일 JSON 파일을 통째로 적재할 최대 크기 (MB)",
    )
    parser.add_argument(
        "--parquet-shard-rows",
        type=int,
        default=200_000,
        help="Parquet 출력 shard 당 행 수 (0 이면 단일 파일)",
    )
    parser.add_argument(
        "--hf-output-format",
        choices=("save_to_disk", "parquet"),
        default="save_to_disk",
        help="HF Dataset 결과 저장 형식",
    )
    parser.add_argument(
        "--manifest-max-records",
        type=int,
        default=200_000,
        help="manifest 에 개별 기록할 최대 레코드 수",
    )
    parser.add_argument(
        "--max-shrink-rounds", type=int, default=4, help="상한 초과 시 재축소 최대 반복 횟수"
    )
    parser.add_argument("--encoding", default="utf-8", help="텍스트 파일 읽기 인코딩")
    parser.add_argument(
        "--reports-dir", default="./reports", help="manifest/보고서를 저장할 디렉터리"
    )
    parser.add_argument(
        "--allow-nested-output",
        action="store_true",
        help="출력 경로가 원본 디렉터리 안에 있어도 허용한다 (출력 경로는 스캔에서 제외됨)",
    )
    return parser.parse_args(argv)


def resolve_dataset_id(args: argparse.Namespace) -> None:
    """--dataset-id 가 있으면 Hub 에서 내려받아 args.input_path 를 채운다.

    이미 받아 둔 파일은 huggingface_hub 가 재사용하므로 다시 내려받지 않는다.
    토큰은 환경 변수(HF_TOKEN)로만 읽고 출력하지 않는다.
    """
    if not args.dataset_id:
        if not args.input_path:
            raise SystemExit("[중단] --input-path 또는 --dataset-id 중 하나는 필요합니다.")
        return

    if args.input_path:
        log("[안내] --input-path 가 지정되어 --dataset-id 는 무시합니다.")
        return

    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except ImportError:
        raise SystemExit(
            "[중단] --dataset-id 를 쓰려면 huggingface_hub 가 필요합니다: pip install huggingface_hub"
        )

    local_name = args.dataset_id.split("/")[-1]
    target = Path(args.dataset_cache_dir).expanduser().resolve() / local_name
    log(f"[HF] 데이터셋 내려받기: {args.dataset_id} → {target}")
    try:
        path = snapshot_download(
            repo_id=args.dataset_id,
            repo_type="dataset",
            revision=args.dataset_revision,
            local_dir=str(target),
        )
    except Exception as exc:  # noqa: BLE001 - 원인을 그대로 알려야 한다
        raise SystemExit(f"[중단] 데이터셋을 내려받지 못했습니다: {type(exc).__name__}: {exc}")
    args.input_path = path
    log(f"[HF] 내려받기 완료: {path}")


def validate_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """입력/출력 경로를 검증하고 정규화한다."""
    in_path = Path(args.input_path).expanduser().resolve()
    out_path = Path(args.output_path).expanduser().resolve()

    if not in_path.exists():
        raise SystemExit(f"[중단] 원본 경로가 존재하지 않습니다: {in_path}")
    if not os.access(in_path, os.R_OK):
        raise SystemExit(f"[중단] 원본 경로를 읽을 수 없습니다: {in_path}")
    if out_path == in_path:
        raise SystemExit("[중단] 출력 경로가 원본 경로와 같습니다. 원본을 덮어쓸 수 없습니다.")
    if in_path in out_path.parents and not args.allow_nested_output:
        # 원본 하위에 출력하면 재귀 스캔에 섞이므로 기본적으로 막는다.
        raise SystemExit(
            f"[중단] 출력 경로가 원본 디렉터리 안에 있습니다: {out_path}\n"
            "        원본 밖 경로를 쓰거나 --allow-nested-output 을 지정하세요."
        )
    if out_path.exists() and any(out_path.iterdir()):
        if not args.overwrite:
            raise SystemExit(
                f"[중단] 출력 디렉터리가 비어 있지 않습니다: {out_path}\n"
                "        덮어쓰려면 --overwrite 를 지정하세요."
            )
        if args.dry_run:
            log(f"[안내] dry-run 이라 기존 출력 디렉터리를 지우지 않습니다: {out_path}")
    return in_path, out_path


def main(argv: Sequence[str] | None = None) -> int:
    """스크립트 진입점."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(raw_argv)

    command = "python scripts/create_5gb_subset.py " + " ".join(
        (f'"{a}"' if " " in a else a) for a in raw_argv
    )

    resolve_dataset_id(args)
    in_path, out_path = validate_paths(args)
    reports_dir = Path(args.reports_dir).expanduser().resolve()

    log(f"[1/6] 원본 스캔: {in_path}")
    # 출력 경로가 원본 안에 있으면 이전 결과물이 원본으로 잡히지 않도록 제외한다.
    inv = scan_source(in_path, args, exclude=out_path)
    if inv.mode == "empty":
        for warning in inv.warnings:
            log(f"  ! {warning}")
        log("[중단] 원본에서 처리할 데이터를 찾지 못했습니다.")
        return 2

    if args.dedup and inv.mode != "hf_dataset":
        removed = dedup_inventory(inv)
        if removed:
            log(f"  [중복] 내용이 같은 레코드 {removed:,}건을 원본 집계에서 제외했습니다.")
            for line in inv.duplicate_sources:
                log(f"    - {line}")
        else:
            log("  [중복] 내용이 같은 레코드는 없었습니다.")

    log(f"[2/6] 축소 계획 수립 (seed={args.seed})")
    plan = build_plan(inv, args)
    print_summary(inv, plan, args)

    if args.dry_run:
        log("[dry-run] 파일을 복사하거나 변환하지 않았습니다.")
        log(f"[dry-run] 실제 생성하려면 --dry-run 을 빼고 다시 실행하세요.")
        return 0

    log(f"[3/6] 축소 데이터셋 생성: {out_path}")
    if out_path.exists() and any(out_path.iterdir()):
        shutil.rmtree(out_path)
    try:
        write_subset(inv, plan, out_path, args)
    except Exception as exc:  # noqa: BLE001 - 불완전 산출물임을 알려야 한다
        log("")
        log("[실패] 축소 도중 오류가 발생했습니다. 출력은 불완전합니다(사용하지 마세요).")
        log(f"        오류: {exc}")
        log(f"        불완전 출력 위치: {out_path}")
        log("        원본은 수정되지 않았습니다.")
        return 1

    log("[4/6] 출력 용량 검증")
    max_bytes = resolve_max_bytes(args)
    actual_bytes, file_count = dir_size(out_path)
    log(f"  출력 파일 {file_count:,}개 / {human_bytes(actual_bytes)} ({actual_bytes:,} bytes)")
    actual_bytes, shrink_rounds = enforce_max_size(inv, plan, out_path, args, max_bytes, )
    if shrink_rounds:
        actual_bytes, file_count = dir_size(out_path)
        log(
            f"  재축소 후 출력 파일 {file_count:,}개 / {human_bytes(actual_bytes)} "
            f"({actual_bytes:,} bytes)"
        )

    log("[5/6] 재로딩·스키마 검증")
    verification = verify_output(inv, out_path, args)
    reference_issues = check_media_references(out_path, plan)
    for issue in verification.get("issues", []) + reference_issues:
        log(f"  ! {issue}")

    log("[6/6] manifest / 보고서 생성")
    reports_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(inv, plan, out_path, args, actual_bytes, verification)
    manifest_path = reports_dir / "subset_5gb_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    report_path = reports_dir / "subset_5gb_report.md"
    report_path.write_text(
        build_report(
            inv,
            plan,
            out_path,
            args,
            actual_bytes,
            shrink_rounds,
            verification,
            reference_issues,
            command,
        ),
        encoding="utf-8",
    )
    log(f"  manifest : {manifest_path}")
    log(f"  보고서   : {report_path}")

    within = actual_bytes <= max_bytes
    log("")
    log("=" * 68)
    log(
        f"결과: {human_bytes(actual_bytes)} ({actual_bytes:,} bytes) / "
        f"상한 {max_bytes:,} bytes"
    )
    log(f"레코드 {inv.total_records:,} → {plan.selected_records:,}")
    if inv.total_media:
        log(f"미디어 {inv.total_media:,} → {plan.selected_media:,}")
    log("상한 충족" if within else "상한 초과 — 성공으로 보지 마세요")
    log("=" * 68)

    if not within:
        return 3
    if not verification.get("reloadable", True):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
