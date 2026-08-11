# services/translation_service.py
from openai import OpenAI
from core.config import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY)


def translate_pr_to_en_query(title: str, description: str = "") -> str:
    """
    한글/영문 PR Title 및 Description을 gpt-4o-mini를 통해 
    RAG 검색 정밀도를 높이기 위한 영문 검색 쿼리로 변환
    """
    if not title and not description:
        return ""

    prompt = f"""You are a technical translator for a RAG search engine.
Convert the following PR title and description into a concise English search query for finding similar code reviews.
Keep technical terms, programming languages, and framework names exact.

PR Title: {title}
PR Description: {description or 'N/A'}

Output ONLY the translated English search query without any explanation."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return f"{title} {description}".strip()