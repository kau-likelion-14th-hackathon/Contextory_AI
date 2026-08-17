# services/translation_service.py
"""
PR 제목/설명을 RAG 검색용 영문 쿼리로 변환한다.

- OpenAI 클라이언트를 import 시점에 만들지 않는다(API Key 없이도 모듈 import 가능해야 테스트가 돈다).
- client / model을 주입할 수 있어 실제 호출 없이 단위 테스트가 가능하다.
- 번역 실패는 파이프라인을 죽이지 않고 원문 fallback으로 처리한다(검색 품질만 떨어질 뿐 근거는 유지된다).
"""

from typing import Any, Optional

from core.config import settings

TRANSLATION_PROMPT_TEMPLATE = """You are a technical translator for a RAG search engine.
Convert the following PR title and description into a concise English search query for finding similar code reviews.
Keep technical terms, programming languages, and framework names exact.

PR Title: {title}
PR Description: {description}

Output ONLY the translated English search query without any explanation."""

_client: Any = None


def _get_client(client: Any = None) -> Any:
    global _client
    if client is not None:
        return client
    if _client is None:
        from openai import OpenAI  # 지연 import

        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def translate_pr_to_en_query(
    title: str,
    description: str = "",
    client: Any = None,
    model: Optional[str] = None,
) -> str:
    """한글/영문 PR 제목·설명을 영문 검색 쿼리로 변환한다. 실패 시 원문을 그대로 돌려준다."""
    if not title and not description:
        return ""

    prompt = TRANSLATION_PROMPT_TEMPLATE.format(title=title, description=description or "N/A")

    try:
        response = _get_client(client).chat.completions.create(
            model=model or settings.TRANSLATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=150,
        )
        translated = (response.choices[0].message.content or "").strip()
        return translated or f"{title} {description}".strip()
    except Exception:
        return f"{title} {description}".strip()
