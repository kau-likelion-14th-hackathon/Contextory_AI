"""LLM 호출 클라이언트.

출력은 자유 텍스트가 아니라 AnalysisResultDTO 로 파싱합니다.
파싱 실패 시 LlmResponseError 를 던지고, 호출한 Service 가 PR 상태를 failed 로 기록합니다.
API 키는 core/config.py 가 환경 변수에서만 읽습니다.
"""

from __future__ import annotations

from models.schemas import AnalysisResultDTO


class LlmClient:
    def __init__(self, model: str | None = None) -> None:
        self.model = model

    def analyze_pr(self, prompt_input: dict) -> AnalysisResultDTO:
        """PR 분석 프롬프트를 호출하고 결과를 스키마로 파싱합니다.

        환각 방지 규칙(프롬프트에 포함):
          - diff 에 없는 변경을 만들지 않습니다.
          - PR 본문에 없는 목적을 확정하지 않습니다.
          - 근거가 약하면 needs_confirmation 으로 분리합니다.

        System Prompt 는 prompts/korean_code_review_v*.md 를 사용합니다.
        """
        raise NotImplementedError

    def answer_with_context(self, question: str, context: list[str]) -> str:
        """RAG 검색 결과를 문맥으로 질의에 답합니다."""
        raise NotImplementedError
