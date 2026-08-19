"""
scripts/index_repo_code.py — 로컬 레포지토리 소스코드를 pgvector에 인덱싱하는 CLI

배경
    프로젝트 코드 컨텍스트(data_repo_code_vectors)가 비어 있으면 검색 근거가 없어
    대부분의 PR이 "근거 부족" 경로로 빠진다. 백엔드가 POST /api/v1/repos/index 를
    호출하도록 만드는 대신, AI 파트가 이 스크립트로 직접 적재해 백엔드 작업을 0으로 둔다.

동작
    - git 저장소면 `git ls-files` 결과를 쓴다 → .gitignore가 자동으로 반영된다.
    - git이 아니면 디렉터리를 순회하되 흔한 산출물 디렉터리를 제외한다.
    - 확장자/파일 크기로 한 번 더 거른 뒤 llamaindex/pipeline.index_repository_files()에 넘긴다.
      (청킹·임베딩·Upsert는 그 함수가 담당한다 — 로직을 중복 구현하지 않는다)

사용 예
    python -m scripts.index_repo_code --path . --repo-name kau-likelion-14th-hackathon/Contextory_AI --dry-run
    python -m scripts.index_repo_code --path . --repo-name kau-likelion-14th-hackathon/Contextory_AI
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings  # noqa: E402

# 인덱싱 대상 확장자 (소스코드·설정·문서). CLI로 덮어쓸 수 있다.
DEFAULT_EXTENSIONS = (
    ".py", ".java", ".kt", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb", ".rs",
    ".sql", ".yml", ".yaml", ".toml", ".gradle", ".md",
)
# 산출물·의존성 디렉터리 (git 저장소가 아닐 때 사용)
DEFAULT_EXCLUDE_DIRS = (
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "build", "dist", "target", ".idea", ".vscode", "data", "notebooks",
)
# 임베딩 입력 한도(8192 토큰)와 비용을 감안한 파일 크기 상한
DEFAULT_MAX_BYTES = 40_000
EMBED_PRICE_PER_1M_TOKENS = 0.02  # text-embedding-3-small (2026-08 기준, 추정치 표시용)
CHARS_PER_TOKEN = 4  # 대략치


def _git_tracked_files(root: Path) -> Optional[List[Path]]:
    """git 저장소면 추적 중인 파일 목록을 돌려준다(.gitignore 자동 반영). 아니면 None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return [root / line for line in out.splitlines() if line.strip()]


def collect_source_files(
    root: Path,
    extensions: Iterable[str] = DEFAULT_EXTENSIONS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS,
    candidates: Optional[List[Path]] = None,
) -> List[Path]:
    """
    인덱싱 대상 파일을 고른다. (순수 함수 — candidates를 주입하면 파일시스템 없이 테스트 가능)
    제외 기준: 확장자 불일치 / 크기 초과 / 빈 파일 / 산출물 디렉터리 포함.
    """
    exts = {e.lower() for e in extensions}
    excluded = set(exclude_dirs)
    picked: List[Path] = []

    for path in candidates if candidates is not None else sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            relative_parts = path.parts
        if any(part in excluded for part in relative_parts):
            continue
        if path.suffix.lower() not in exts:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0 or size > max_bytes:
            continue
        picked.append(path)

    return picked


def to_file_chunks(paths: List[Path], root: Path):
    """선택된 파일을 인덱싱 요청 DTO(CodeFileChunk)로 변환한다. 읽기 실패 파일은 건너뛴다."""
    from models.schemas import CodeFileChunk

    chunks = []
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not content.strip():
            continue
        chunks.append(
            CodeFileChunk(file_path=str(path.relative_to(root)), chunk_idx=0, content=content)
        )
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 레포 소스코드를 pgvector에 인덱싱")
    parser.add_argument("--path", default=".", help="인덱싱할 레포지토리 루트 경로")
    parser.add_argument("--repo-name", required=True, help="검색 시 격리 키로 쓰는 저장소 이름 (owner/repo)")
    parser.add_argument("--extensions", default=",".join(DEFAULT_EXTENSIONS), help="쉼표로 구분한 확장자 목록")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="파일 크기 상한(바이트)")
    parser.add_argument("--limit", type=int, default=None, help="상위 N개만 인덱싱(시험용)")
    parser.add_argument("--dry-run", action="store_true", help="적재 없이 대상 파일·비용 추정만 출력")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.is_dir():
        raise SystemExit(f"경로를 찾을 수 없습니다: {root}")

    tracked = _git_tracked_files(root)
    source = "git ls-files (.gitignore 반영)" if tracked is not None else "디렉터리 순회"
    paths = collect_source_files(
        root,
        extensions=[e.strip() for e in args.extensions.split(",") if e.strip()],
        max_bytes=args.max_bytes,
        candidates=tracked,
    )
    if args.limit:
        paths = paths[: args.limit]

    chunks = to_file_chunks(paths, root)
    total_chars = sum(len(c.content) for c in chunks)
    est_tokens = total_chars // CHARS_PER_TOKEN

    print(f"레포          : {root}")
    print(f"대상 수집 방식: {source}")
    print(f"인덱싱 대상   : {len(chunks)}개 파일 ({total_chars:,}자)")
    print(f"임베딩 추정   : 약 {est_tokens:,} 토큰 → 약 ${est_tokens / 1_000_000 * EMBED_PRICE_PER_1M_TOKENS:.4f}")
    print(f"저장 테이블   : data_{settings.REPO_CODE_TABLE_NAME} (repo_name={args.repo_name})")

    if args.dry_run:
        print("\n[dry-run] 실제 적재는 --dry-run 없이 실행하세요. 상위 10개 예시:")
        for c in chunks[:10]:
            print(f"  - {c.file_path} ({len(c.content):,}자)")
        return 0

    if not chunks:
        print("인덱싱할 파일이 없습니다.")
        return 0

    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY가 설정되지 않았습니다. .env를 확인하세요.")

    from llamaindex.pipeline import index_repository_files

    indexed, deleted = index_repository_files(repo_name=args.repo_name, files=chunks, deleted_files=[])
    print(f"\n✅ 인덱싱 완료: {indexed}개 파일 (삭제 정리 {deleted}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
