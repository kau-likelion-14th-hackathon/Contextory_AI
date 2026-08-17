"""
judge.py — 생성 품질 판단기 두 구현 (동일 인터페이스)

    1. LLMJudge            : GPT를 호출해 채점 (client 주입 가능 → 테스트에서 가짜 client 사용)
    2. OfflineKeywordJudge : LLM 없이 동작. 정답/컨텍스트 키워드 포함 여부로 채점

공통 인터페이스
    judge(question=..., context=..., answer=..., criterion=..., reference=None) -> dict
        {"criterion": str, "score": float|None, "reason": str, "error": str|None, "source": str}

점수 방향
    - hallucination 만 "높을수록 나쁨"(환각 정도)이고, 나머지 criterion은 전부 "높을수록 좋음"이다.

주의
    이 모듈은 services/ 를 import하지 않는다. 런타임 파이프라인과 완전히 분리되어 있다.
"""

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.config import settings

CRITERIA: Dict[str, str] = {
    "faithfulness": "answer가 context에 실제로 있는 내용만 근거로 작성됐는지, context와 모순되는 내용은 없는지 평가한다.",
    "groundedness": "answer의 각 주장이 context 또는 PR 변경 내용의 특정 부분으로 뒷받침되는지 평가한다.",
    "answer_relevance": "answer의 역할별 영향·후속 작업이 실제 이번 PR 변경과 연결되는지 평가한다.",
    "context_relevance": "context가 question(이번 PR 분석)에 실제로 필요한 정보인지 평가한다.",
    "hallucination": "answer에 context로 뒷받침되지 않는 지어낸 내용이 있는지 평가한다. (높을수록 나쁨)",
    "completeness": "answer가 PR의 중요한 변경을 빠뜨리지 않았는지 평가한다.",
}

# 키워드 매칭에서 제외할 흔한 토큰 (점수 부풀림 방지)
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "have", "has",
    "was", "were", "are", "not", "but", "you", "your", "our", "its",
    "그리고", "하지만", "그러나", "이번", "위해", "대한", "관련", "있습니다", "합니다", "때문",
}

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_./#-]{1,}|[가-힣]{2,}|\d+")


def _tokens(text: Optional[str]) -> List[str]:
    return [t.lower() for t in _TOKEN_PATTERN.findall(text or "") if t.lower() not in _STOPWORDS]


def _result(criterion: str, score: Optional[float], reason: str, source: str, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "criterion": criterion,
        "score": score,
        "reason": reason,
        "error": error,
        "source": source,
    }


def _reference_keywords(reference: Any) -> List[str]:
    """
    reference로 올 수 있는 형태를 모두 키워드 리스트로 정규화한다.
      - {"keywords": [...]} / {"reference_keywords": [...]} / {"reference_answer": "..."}
      - ["kw1", "kw2"]
      - "정답 문장"
    """
    if reference is None:
        return []
    if isinstance(reference, dict):
        for key in ("reference_keywords", "keywords", "gold_keywords"):
            if reference.get(key):
                return [str(k) for k in reference[key]]
        if reference.get("reference_answer"):
            return _tokens(reference["reference_answer"])
        return []
    if isinstance(reference, (list, tuple, set)):
        return [str(k) for k in reference]
    return _tokens(str(reference))


def _coverage(needles: Sequence[str], haystack_text: str) -> float:
    """needles(키워드/토큰) 중 haystack에 등장하는 비율"""
    needle_list = [str(n).strip().lower() for n in needles if str(n).strip()]
    if not needle_list:
        return 0.0
    hay = (haystack_text or "").lower()
    hay_tokens = set(_tokens(haystack_text))
    hits = 0
    for needle in needle_list:
        # 여러 단어로 된 키워드는 부분 문자열로, 단일 토큰은 토큰 일치로 판단한다.
        if (" " in needle and needle in hay) or (needle in hay_tokens) or (needle in hay):
            hits += 1
    return hits / len(needle_list)


# ==========================================
# 1. Offline Keyword Judge (LLM 없이 동작)
# ==========================================

class OfflineKeywordJudge:
    """
    LLM 호출 없이 '정답 키워드 포함 여부'로 채점하는 Judge.
    API Key·네트워크 없이 CI에서 전체 평가 파이프라인을 돌리기 위한 구현이며,
    의미 판단이 아닌 표면적 문자열 매칭이므로 근사치임을 전제로 쓴다.
    """

    source = "offline_keyword"

    def __call__(
        self,
        question: str = "",
        context: str = "",
        answer: str = "",
        criterion: str = "groundedness",
        reference: Any = None,
    ) -> Dict[str, Any]:
        if criterion not in CRITERIA:
            return _result(criterion, None, f"지원하지 않는 criterion: {criterion}", self.source, error="unsupported_criterion")

        keywords = _reference_keywords(reference)

        if criterion in ("groundedness", "faithfulness"):
            grounded = _coverage(_tokens(answer), f"{context}\n{question}")
            return _result(criterion, round(grounded, 4), "answer 토큰 중 context/PR에서 확인되는 비율", self.source)

        if criterion == "hallucination":
            grounded = _coverage(_tokens(answer), f"{context}\n{question}")
            return _result(criterion, round(1.0 - grounded, 4), "context에서 확인되지 않는 answer 토큰 비율(높을수록 나쁨)", self.source)

        if criterion == "context_relevance":
            score = _coverage(_tokens(question), context)
            return _result(criterion, round(score, 4), "question 토큰 중 context가 담고 있는 비율", self.source)

        if criterion == "completeness":
            if not keywords:
                return _result(criterion, None, "reference 키워드가 없어 판단 불가", self.source, error="no_reference")
            return _result(criterion, round(_coverage(keywords, answer), 4), "정답 키워드 중 answer에 포함된 비율", self.source)

        # answer_relevance
        target = keywords or _tokens(question)
        if not target:
            return _result(criterion, None, "비교할 기준이 없어 판단 불가", self.source, error="no_reference")
        return _result(criterion, round(_coverage(target, answer), 4), "질문/정답 키워드 중 answer가 다루는 비율", self.source)


# ==========================================
# 2. LLM Judge (client 주입식)
# ==========================================

_LLM_JUDGE_PROMPT = """<role>
너는 RAG 시스템이 생성한 answer의 품질을 채점하는 평가자야.
</role>

<criterion>
{criterion_instruction}
</criterion>

<question>
{question}
</question>

<context>
{context}
</context>

<answer>
{answer}
</answer>

<reference>
{reference}
</reference>

<output>
반드시 아래 JSON 형식으로만 답해:
{{"score": 0.0에서 1.0 사이 숫자, "reason": "판단 근거 한 문장"}}
</output>
"""


class LLMJudge:
    """
    GPT로 채점하는 Judge. client를 주입할 수 있어 테스트에서 가짜 client로 대체 가능하다.
    LLM 호출 실패는 예외로 터뜨리지 않고 score=None + error로 표시해, 평가 집계에서 제외되게 한다.
    """

    source = "llm"

    def __init__(self, client: Any = None, model: Optional[str] = None):
        self._client = client
        self.model = model or settings.LLM_MODEL

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI  # 지연 import

            self._client = OpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    def __call__(
        self,
        question: str = "",
        context: str = "",
        answer: str = "",
        criterion: str = "groundedness",
        reference: Any = None,
    ) -> Dict[str, Any]:
        if criterion not in CRITERIA:
            return _result(criterion, None, f"지원하지 않는 criterion: {criterion}", self.source, error="unsupported_criterion")

        prompt = _LLM_JUDGE_PROMPT.format(
            criterion_instruction=CRITERIA[criterion],
            question=question,
            context=context,
            answer=answer,
            reference=reference if reference is not None else "(없음)",
        )

        try:
            response = self._get_client().chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            parsed = json.loads(response.choices[0].message.content)
        except Exception as e:
            return _result(criterion, None, f"LLM judge 호출/파싱 실패: {e}", self.source, error=str(e))

        score = parsed.get("score")
        try:
            score = None if score is None else float(score)
        except (TypeError, ValueError):
            return _result(criterion, None, f"score 파싱 실패: {parsed}", self.source, error="invalid_score")

        return _result(criterion, score, str(parsed.get("reason", "")), self.source)


def llm_judge(
    question: str,
    context: str,
    answer: str,
    criterion: str,
    client: Any = None,
    reference: Any = None,
) -> Dict[str, Any]:
    """함수 형태 진입점 (LLMJudge와 동일 동작)"""
    return LLMJudge(client=client)(
        question=question, context=context, answer=answer, criterion=criterion, reference=reference
    )


def offline_keyword_judge(
    question: str = "",
    context: str = "",
    answer: str = "",
    criterion: str = "groundedness",
    reference: Any = None,
) -> Dict[str, Any]:
    """함수 형태 진입점 (OfflineKeywordJudge와 동일 동작)"""
    return OfflineKeywordJudge()(
        question=question, context=context, answer=answer, criterion=criterion, reference=reference
    )


Judge = Callable[..., Dict[str, Any]]
