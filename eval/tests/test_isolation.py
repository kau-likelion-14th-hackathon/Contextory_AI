"""
구조 제약 자동 검증

    - eval/ 은 services/ 를 import하지 않는다.
    - services/ 는 eval/ 을 import하지 않는다.

이 규칙이 깨지면 "LLM·DB 없이 평가가 돈다"는 전제가 무너지므로 테스트로 고정한다.
"""

import ast
from pathlib import Path
from typing import List, Set

ROOT = Path(__file__).resolve().parent.parent.parent
EVAL_DIR = ROOT / "eval"
SERVICES_DIR = ROOT / "services"


def _imported_top_level_modules(path: Path) -> Set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])

    return modules


def _python_files(directory: Path) -> List[Path]:
    return [p for p in directory.rglob("*.py") if "__pycache__" not in p.parts]


def test_eval_never_imports_services():
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files(EVAL_DIR)
        if "services" in _imported_top_level_modules(path)
    ]

    assert offenders == [], f"eval/ 에서 services/ 를 import한 파일: {offenders}"


def test_services_never_imports_eval():
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files(SERVICES_DIR)
        if "eval" in _imported_top_level_modules(path)
    ]

    assert offenders == [], f"services/ 에서 eval/ 을 import한 파일: {offenders}"


def test_eval_files_exist():
    """평가 시스템 구성 파일이 실제로 존재하는지 확인 (구조 회귀 방지)"""
    expected = [
        "judge.py", "runner.py", "report.py", "error_analysis.py", "fakes.py",
        "metrics/retrieval.py", "metrics/filtering.py", "metrics/generation.py", "metrics/retrieval_signals.py",
        "datasets/ground_truth.py", "datasets/loader.py", "datasets/codereview_adapters.py", "datasets/silver_builder.py",
        "data/sample_ground_truth.jsonl",
    ]

    missing = [name for name in expected if not (EVAL_DIR / name).exists()]

    assert missing == [], f"누락된 평가 모듈: {missing}"
