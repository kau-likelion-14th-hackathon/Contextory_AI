"""RAG 프롬프트.

"""

from __future__ import annotations

QA_SYSTEM_PROMPT = """당신은 Contextory 의 프로젝트 문맥 도우미입니다.
주어진 문맥에 있는 내용만 근거로 한국어(설명체)로 답합니다.
문맥에 없으면 모른다고 답하고 추측하지 않습니다.
코드, 식별자, 파일 경로, 명령어는 원문 그대로 인용합니다."""


def build_qa_prompt(question: str, context: list[str]) -> str:
    raise NotImplementedError
