# services/translation_service.py
"""
PR 제목/설명을 RAG 검색용 영문 쿼리로 변환한다.

- OpenAI 클라이언트를 import 시점에 만들지 않는다(API Key 없이도 모듈 import 가능해야 테스트가 돈다).
- client / model을 주입할 수 있어 실제 호출 없이 단위 테스트가 가능하다.
- 번역 실패는 파이프라인을 죽이지 않고 원문 fallback으로 처리한다(검색 품질만 떨어질 뿐 근거는 유지된다).
"""

from typing import Any, Optional

from core.config import settings

# 이 쿼리는 임베딩 검색에 그대로 쓰인다. 너무 짧게 요약하면 유사도가 떨어져
# 멀쩡한 PR이 "근거 부족"으로 빠진다.
#   실측: 같은 PR을 "project domain CRUD API implementation" 으로 줄이면 0.4462,
#         엔티티·Enum 이름을 남기면 0.5458 (임계값 0.5를 사이에 두고 갈린다)
# 그래서 "간결하게"가 아니라 "식별자를 남기라"고 지시한다.
TRANSLATION_PROMPT_TEMPLATE = """You are a technical query builder for a code search engine (vector similarity).
Convert the following PR title and description into an English search query.

Rules:
- Keep every technical identifier verbatim: class/entity names, enum names, endpoints, file paths, table names, framework and library names.
- Keep domain nouns (project, member, invitation, analysis, repository, pull request...).
- Do NOT summarize into a short phrase. A keyword-rich query retrieves better than a terse one.
- Drop prose, checklists, PR templates, reviewer notes, and issue links.
- Output one line, no more than 60 words, no explanation.

PR Title: {title}
PR Description: {description}

Output ONLY the English search query."""

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

    # 같은 PR은 항상 같은 쿼리로 번역되어야 한다 — 쿼리가 흔들리면 검색 점수가 흔들리고,
    # 그 흔들림이 SIM_THRESHOLD를 넘나들면 "근거 부족" 판정이 실행마다 뒤집힌다.
    request: dict = {
        "model": model or settings.TRANSLATION_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": settings.TRANSLATION_TEMPERATURE,
        "max_tokens": 150,
    }
    if settings.TRANSLATION_SEED is not None:
        request["seed"] = settings.TRANSLATION_SEED

    try:
        response = _get_client(client).chat.completions.create(**request)
        translated = (response.choices[0].message.content or "").strip()
        return translated or f"{title} {description}".strip()
    except Exception:
        return f"{title} {description}".strip()
