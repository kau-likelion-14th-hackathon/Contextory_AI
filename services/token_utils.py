from typing import Optional

import tiktoken

from core.config import settings

_ENCODING_CACHE: dict[str, "tiktoken.Encoding"] = {}


def get_encoding(model: Optional[str] = None) -> "tiktoken.Encoding":
    """LLM_MODEL에 맞는 tiktoken encoding을 반환한다.

    tiktoken이 아직 모르는(신규) 모델명이면 KeyError가 나므로, gpt-4o 계열이 쓰는
    cl100k_base로 폴백한다 — 프롬프트가 실제 한도를 살짝 넘거나 못 미칠 수는 있지만
    토큰 카운팅 자체가 죽는 것보다 낫다.
    """
    model = model or settings.LLM_MODEL
    if model not in _ENCODING_CACHE:
        try:
            _ENCODING_CACHE[model] = tiktoken.encoding_for_model(model)
        except KeyError:
            _ENCODING_CACHE[model] = tiktoken.get_encoding("cl100k_base")
    return _ENCODING_CACHE[model]


def count_tokens(text: str, model: Optional[str] = None) -> int:
    return len(get_encoding(model).encode(text or ""))
