"""
datasets/loader.py — JSONL 평가 데이터셋 로더/세이버

포맷은 한 줄 = 한 케이스(JSON 객체). ground_truth.GroundTruthCase 필드를 따른다.
(포맷 자체는 팀 합의 전 잠정안이다 — README/보고서의 '확인 필요' 참고)
"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Union

from eval.datasets.ground_truth import GroundTruthCase, as_case

PathLike = Union[str, Path]

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_GROUND_TRUTH_PATH = DEFAULT_DATA_DIR / "sample_ground_truth.jsonl"


def iter_jsonl(path: PathLike) -> Iterator[Dict[str, Any]]:
    """JSONL을 한 줄씩 읽는다. 빈 줄은 건너뛰고, 깨진 줄은 줄 번호와 함께 실패시킨다."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"데이터셋 파일이 없습니다: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as e:
                raise ValueError(f"{file_path}:{line_no} JSONL 파싱 실패: {e}") from e


def load_cases(path: PathLike = DEFAULT_GROUND_TRUTH_PATH) -> List[GroundTruthCase]:
    """JSONL 파일을 GroundTruthCase 리스트로 로드한다."""
    return [as_case(raw) for raw in iter_jsonl(path)]


def save_cases(cases: Iterable[Union[GroundTruthCase, Dict[str, Any]]], path: PathLike) -> int:
    """GroundTruthCase(또는 dict) 목록을 JSONL로 저장한다. 저장한 건수를 반환한다."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with file_path.open("w", encoding="utf-8") as f:
        for case in cases:
            payload = case.to_dict() if isinstance(case, GroundTruthCase) else dict(case)
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            count += 1
    return count
