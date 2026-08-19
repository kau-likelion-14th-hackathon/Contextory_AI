"""레포 코드 인덱싱 스크립트 테스트 — 파일 선별·변환 로직 (DB·OpenAI 호출 없음)"""

from pathlib import Path

from scripts.index_repo_code import (
    DEFAULT_EXTENSIONS, DEFAULT_MAX_BYTES, collect_source_files, to_file_chunks,
)


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AuthService.java").write_text("class AuthService {}", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# doc", encoding="utf-8")

    # 제외 대상들
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")               # 확장자 불일치
    (tmp_path / "empty.py").write_text("", encoding="utf-8")            # 빈 파일
    (tmp_path / "huge.py").write_text("x" * (DEFAULT_MAX_BYTES + 1), encoding="utf-8")  # 크기 초과
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("module.exports={}", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("cache", encoding="utf-8")
    return tmp_path


def test_collect_filters_by_extension_size_and_directory(tmp_path):
    root = _make_repo(tmp_path)

    picked = {p.name for p in collect_source_files(root)}

    assert picked == {"AuthService.java", "main.py", "README.md"}


def test_collect_respects_custom_extensions(tmp_path):
    root = _make_repo(tmp_path)

    picked = {p.name for p in collect_source_files(root, extensions=[".py"])}

    assert picked == {"main.py"}


def test_collect_respects_max_bytes(tmp_path):
    root = _make_repo(tmp_path)

    picked = {p.name for p in collect_source_files(root, max_bytes=15)}

    assert "AuthService.java" not in picked      # 20자라 상한 초과
    assert "main.py" in picked                   # 11자


def test_collect_accepts_injected_candidates(tmp_path):
    """git ls-files 결과를 주입해도 동일한 필터가 적용된다(파일시스템 순회 없이)."""
    root = _make_repo(tmp_path)
    candidates = [root / "main.py", root / "image.png", root / "node_modules" / "lib.js"]

    picked = {p.name for p in collect_source_files(root, candidates=candidates)}

    assert picked == {"main.py"}


def test_to_file_chunks_uses_relative_paths(tmp_path):
    root = _make_repo(tmp_path)

    chunks = to_file_chunks(collect_source_files(root), root)

    paths = {c.file_path for c in chunks}
    assert "src/AuthService.java" in paths       # 저장소 기준 상대 경로
    assert all(not Path(c.file_path).is_absolute() for c in chunks)
    assert all(c.chunk_idx == 0 for c in chunks)
    assert all(c.content.strip() for c in chunks)


def test_to_file_chunks_skips_undecodable_files(tmp_path):
    (tmp_path / "binary.py").write_bytes(b"\xff\xfe\x00\x01")

    chunks = to_file_chunks([tmp_path / "binary.py"], tmp_path)

    assert chunks == []


def test_default_extensions_cover_project_stack():
    """Contextory 스택(Python/Java/TS)과 설정·문서 파일이 기본 대상에 포함된다."""
    for ext in (".py", ".java", ".ts", ".yml", ".md", ".sql"):
        assert ext in DEFAULT_EXTENSIONS
