from __future__ import annotations

from models.schemas import AnalysisResultDTO


def parse_analysis_json(raw: str) -> AnalysisResultDTO:
    """LLM 출력 문자열을 AnalysisResultDTO 로 파싱합니다.

    파싱하지 못하면 core.exceptions.LlmResponseError 를 던집니다.
    """
    raise NotImplementedError


def validate_evidence_grounded(result: AnalysisResultDTO, diff: str) -> list[str]:
    """각 issue 의 evidence 를 diff 에서 찾을 수 있는지 확인합니다.

    찾지 못한 issue 의 식별 정보를 돌려줍니다(환각 탐지용).
    """
    raise NotImplementedError
