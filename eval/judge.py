"""
judge.py — RAG 평가용 두 종류의 Judge를 제공한다.

1. LLM Judge (llm_judge)
   Generation Metric(Faithfulness, Groundedness, Answer Relevance, Context Relevance,
   Hallucination, Completeness) 평가에 재사용하는 범용 LLM 판단기.
   eval/metrics/generation.py 등에서 criterion만 바꿔가며 주입(inject)해서 쓴다.

2. Offline Keyword Judge (offline_keyword_judge / llm_keyword_judge / keyword_judge)
   PR에서 추출된 keyword 하나가 Contextory가 검색·보존할 가치가 있는 "중요한 맥락 키워드"인지
   판단한다. 문자열 패턴으로 명백한 것만 즉시 걸러내고(오프라인, 비용 0), 애매한 것만
   LLM Judge로 재확인해서 비용을 아낀다.
   판단 결과는 report.py의 build_report()에 모아서 여러 PR에 걸쳐 집계한다.
"""

import json
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from core.config import settings


def _client(client: Optional[OpenAI] = None) -> OpenAI:
    return client or OpenAI(api_key=settings.OPENAI_API_KEY)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


# ============================================================
# 1. LLM Judge — Generation Metric 평가용 범용 판단기
# ============================================================

_GENERATION_CRITERIA: Dict[str, str] = {
    "faithfulness": "answer가 context에 실제로 있는 내용만 근거로 작성됐는지, context와 모순되는 내용은 없는지 평가한다.",
    "groundedness": "answer의 각 주장이 context의 특정 부분으로 뒷받침되는지 평가한다.",
    "answer_relevance": "answer가 question에서 실제로 묻는 것에 답하고 있는지 평가한다.",
    "context_relevance": "context가 question에 답하는 데 실제로 필요한 정보인지 평가한다.",
    "hallucination": "answer에 context나 일반 상식으로 뒷받침되지 않는, 지어낸 내용이 있는지 평가한다.",
    "completeness": "answer가 question에 답하는 데 필요한 내용을 빠짐없이 담고 있는지 평가한다.",
}

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

<thinking>
1. context 안에서 answer의 각 주장을 뒷받침하는 근거를 찾는다.
2. criterion 기준에 비추어 부족하거나 어긋나는 부분이 있는지 확인한다.
</thinking>

<output>
반드시 아래 JSON 형식으로만 답해:
{{"score": 0.0에서 1.0 사이 숫자, "reason": "판단 근거 한 문장"}}
</output>
"""


def llm_judge(
    question: str,
    context: str,
    answer: str,
    criterion: str,
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    """
    Generation Metric(Faithfulness/Groundedness/Answer Relevance/Context Relevance/
    Hallucination/Completeness) 평가에 공통으로 쓰는 범용 LLM Judge.
    criterion은 _GENERATION_CRITERIA의 key 중 하나여야 한다.
    """
    if criterion not in _GENERATION_CRITERIA:
        raise ValueError(f"지원하지 않는 criterion입니다: {criterion}")

    prompt = _LLM_JUDGE_PROMPT.format(
        criterion_instruction=_GENERATION_CRITERIA[criterion],
        question=question,
        context=context,
        answer=answer,
    )

    try:
        response = _client(client).chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        result = json.loads(response.choices[0].message.content)
    except Exception as e:
        return {
            "criterion": criterion,
            "score": None,
            "reason": f"LLM judge 호출 실패: {e}",
            "error": str(e),
        }

    return {
        "criterion": criterion,
        "score": result.get("score", 0.0),
        "reason": result.get("reason", ""),
        "error": None,
    }


# ============================================================
# 2. Offline Keyword Judge — 키워드 중요도 판단
# ============================================================

# 명백히 안 중요한 것들은 LLM 호출 없이 즉시 걸러낸다 (비용 0).
_STOPWORD_PHRASES = {"수정함", "업데이트", "테스트 완료", "코드 정리", "버그 수정", "리팩토링"}
_MOCK_ID_PATTERN = re.compile(r"^(record-\d+|test-.*|mock_.*|dummy@.*|foo|bar)$", re.IGNORECASE)
_BARE_TECH_NAMES = {"python", "javascript", "typescript", "react", "fastapi", "github", "java"}


def offline_keyword_judge(keyword: str) -> Optional[Dict[str, Any]]:
    """
    문자열 패턴만으로 명백한 not_important를 즉시 판단한다 (LLM 호출 없음).
    - 확실히 안 중요하면 결과 dict를 바로 반환한다.
    - 애매하면 None을 반환해서 llm_keyword_judge로 넘긴다.

    주의: 이 함수는 "중요하다"는 판정은 절대 내리지 않는다.
    important 여부는 반드시 LLM Judge(A~F 기준)를 거쳐야 한다.
    """
    norm = _normalize(keyword)

    if not norm:
        return {
            "label": "not_important",
            "matched_criteria": [],
            "reason": "빈 키워드",
            "inferred_from_background_knowledge": False,
            "relevant_roles": [],
        }

    if norm in _STOPWORD_PHRASES:
        return {
            "label": "not_important",
            "matched_criteria": [],
            "reason": "형식적 문구",
            "inferred_from_background_knowledge": False,
            "relevant_roles": [],
        }

    if _MOCK_ID_PATTERN.match(norm):
        return {
            "label": "not_important",
            "matched_criteria": [],
            "reason": "의미 없는 예시/mock 식별자 패턴",
            "inferred_from_background_knowledge": False,
            "relevant_roles": [],
        }

    if norm in _BARE_TECH_NAMES:
        return {
            "label": "not_important",
            "matched_criteria": [],
            "reason": "PR의 핵심 결정과 연결되지 않은 일반 기술명 단독 언급",
            "inferred_from_background_knowledge": False,
            "relevant_roles": [],
        }

    return None


# Keyword Importance Judge 프롬프트 v5 (v4 + thought_process 노출: 선배 메모의 "사고 과정을 보이게"
# 요구사항 반영. thought_process를 label보다 먼저 오는 첫 필드로 둬서, LLM이 판정을 내리기 전에
# 먼저 생각하도록 강제한다. 비용은 조금 늘지만 판정 근거를 사람이 감사(audit)하기 쉬워진다.
_KEYWORD_JUDGE_PROMPT = """<role>
너는 GitHub PR(코드 변경)의 내용을 분석해서, 주어진 keyword가 Contextory의 검색·지식 저장 시스템에서
보존할 가치가 있는 "중요한 맥락 키워드"인지 판단하는 평가자(judge)야.
</role>

<context>
Contextory는 PR의 변경 이유와 영향을 팀원들이 나중에 검색해서 이해할 수 있도록 돕는 RAG 기반 서비스야.
그래서 "중요한 키워드"란 이 PR의 의사결정, 변경 이유, 제약, 영향을 설명하는 개념을 말해.
</context>

<core_principle>
가장 먼저 이 질문을 생각해:
"이 keyword로 나중에 이 PR을 검색한다면, 팀원이 변경의 이유나 맥락을 이해하는 데 도움이 되는가?"
keyword가 기술적으로 유명하거나 전문적으로 보인다는 이유만으로 important로 판단하지 마.
keyword가 PR 안에 등장한다는 사실 하나만으로도 important로 판단하지 마.
</core_principle>

<keyword_rule>
너는 keyword를 새로 만들거나 다른 표현으로 바꾸지 않는다. 오직 주어진 keyword가
현재 그 형태로 중요한 맥락을 나타내는지만 판단한다.
숫자·파라미터 자체(예: "60초")가 keyword로 주어졌다면, 그 값이 PR 맥락에서 실제 정책/결정과
연결되는지를 판단하되, "60초"를 다른 문구로 바꿔서 답하지는 않는다.
</keyword_rule>

<background_knowledge>
PR 텍스트에 이유가 명시되어 있지 않아도, 일반적인 기술/도메인 지식으로 배경을 추론할 수 있다면
근거로 사용해도 좋아. 이 경우 inferred_from_background_knowledge를 true로 설정해.
PR에 근거가 없는 회사 내부 정책이나 존재하지 않는 사실을 임의로 지어내지는 마.
</background_knowledge>

<criteria>
[필수 - 아래 A~D 중 최소 하나를 만족해야 important 후보가 될 수 있다]
A. WHY - 변경, 설계, 제약의 이유를 담고 있는가
B. SCOPE/LIMITATION - 명시적으로 제외한 범위, 알려진 한계, 추후 처리 예정(TODO)을 나타내는가
C. SEARCH VALUE - 나중에 팀원이 "이거 왜 이렇게 됐지?"라고 검색할 만한 개념인가
D. IMPACT - 변경으로 인한 결과, 영향(호환성 변화, 마이그레이션 필요, 성능 변화 등)을 설명하는가

[보조 - A~D 중 하나가 이미 성립된 상태에서만 확신도를 높이는 용도, 단독 사용 금지]
E. 특정 역할(프론트엔드/백엔드/기획/QA/SRE/CS)이 신경 써야 함을 시사하는가
   - 해당하면 relevant_roles에 구체적으로 어떤 역할인지 적는다 (예: ["프론트엔드", "QA"])
F. 재사용 가능한 의미를 가진 코드 식별자(함수명/API 경로/설정값)인가
   - 단순히 재사용된다는 이유만으로는 충분하지 않으며, PR의 중요한 결정/제약/영향과 연결되어야 한다
</criteria>

<not_important_examples>
- 단순 구현 세부사항: for-loop, if문, import, 임시 변수, 일반적 자료구조
- 의미 없는 테스트/mock 데이터: mock_user, record-128, dummy@example.com, foo, bar
- 형식적 문구: "수정함", "업데이트", "테스트 완료", "코드 정리"
- 일반적인 기술명 단독 언급: Python, React, FastAPI, GitHub - PR의 핵심 결정과 안 엮이면 제외
- 구현 위치만 나타내는 것: 파일명/클래스명이 단순 변경 대상이라는 이유만으로는 제외
- 설정값 자체: MAX_RETRY, TIMEOUT, PAGE_SIZE 등도 그 값이 나타내는 정책과 안 엮이면 제외
- A~D 중 아무것도 해당 안 하고 E 또는 F만 해당하는 경우
</not_important_examples>

<consistency_rule>
다음 이유만으로 important라고 판단하지 마:
keyword가 영어다 / keyword가 길다 / 전문적으로 보인다 / 코드에 여러 번 등장한다 /
PR 제목에 있다 / PR의 변경량이 많다
</consistency_rule>

<pr>
Title: {pr_title}
Description: {pr_description}
Diff: {pr_diff}
</pr>

<keyword>
{keyword}
</keyword>

<thinking>
1. keyword가 not_important_examples에 먼저 해당하는지 확인한다.
2. 해당하지 않으면 PR 맥락에서 이 keyword가 어떻게 쓰였는지 확인한다 (필요하면 배경지식 사용).
3. A~D 중 만족하는 게 있는지 확인한다 - 없으면 E/F와 무관하게 not_important다.
4. A~D 중 하나라도 있으면 E/F도 확인해서 matched_criteria를 정리한다.
</thinking>

<output_rule>
- important로 판단하려면 matched_criteria에 반드시 A, B, C, D 중 하나 이상이 포함되어야 한다.
- E와 F는 A~D와 함께 사용할 수 있지만, 단독으로 important 판정의 근거가 될 수 없다.
- not_important인 경우 실제로 충족하는 핵심 기준이 없다면 matched_criteria는 빈 배열([])로 작성한다.
- reason에는 keyword가 PR의 어떤 맥락과 연결되는지, 그리고 그 판정을 내린 이유를 함께 담는다.
- thought_process는 반드시 label보다 먼저 나오는 첫 번째 필드로 작성해서, label을 정하기 전에 먼저 생각한다.
- matched_criteria에 E가 포함되면 relevant_roles를 반드시 채운다 (프론트엔드/백엔드/기획/QA/SRE/CS 중에서).
  E가 없으면 relevant_roles는 빈 배열([])로 둔다.
</output_rule>

반드시 아래 JSON 형식으로만 답해. 다른 설명, 마크다운, 코드블록 텍스트는 출력하지 마.
thought_process를 반드시 첫 번째 필드로 작성해:
{{"thought_process": "A~D/consistency_rule에 따라 판단한 짧은 사고 과정", "label": "important 또는 not_important", "matched_criteria": ["해당 항목"], "reason": "keyword가 어떤 맥락과 연결되는지 + 판정 이유", "inferred_from_background_knowledge": true 또는 false, "relevant_roles": ["E가 해당할 때만 채움"]}}
"""


def llm_keyword_judge(
    pr_title: str,
    pr_description: str,
    pr_diff: str,
    keyword: str,
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    """offline_keyword_judge가 애매하다고 넘긴(None 반환한) 키워드를 LLM에게 재확인시킨다."""
    prompt = (
        _KEYWORD_JUDGE_PROMPT.replace("{pr_title}", pr_title or "")
        .replace("{pr_description}", pr_description or "")
        .replace("{pr_diff}", pr_diff or "")
        .replace("{keyword}", keyword)
    )

    try:
        response = _client(client).chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        result = json.loads(response.choices[0].message.content)
    except Exception as e:
        # API 호출 실패/timeout/깨진 JSON 등 - 판단 불가 상황에서는 important를 절대 단정하지 않고
        # offline_keyword_judge와 동일하게 안전한 쪽(not_important)으로 처리하되 error를 남겨서
        # report.py 집계에서 구분할 수 있게 한다.
        return {
            "keyword": keyword,
            "thought_process": "",
            "label": "not_important",
            "matched_criteria": [],
            "reason": f"LLM 호출 실패로 판단 불가(안전하게 not_important 처리): {e}",
            "inferred_from_background_knowledge": False,
            "relevant_roles": [],
            "error": str(e),
        }

    return {
        "keyword": keyword,
        "thought_process": result.get("thought_process", ""),
        "label": result.get("label", "not_important"),
        "matched_criteria": result.get("matched_criteria", []),
        "reason": result.get("reason", ""),
        "inferred_from_background_knowledge": result.get("inferred_from_background_knowledge", False),
        "relevant_roles": result.get("relevant_roles", []),
        "error": None,
    }


# important 판정에 반드시 있어야 하는 필수 기준(A~D). E/F만으로는 important가 될 수 없다.
_REQUIRED_CRITERIA = {"A", "B", "C", "D"}


def keyword_judge(
    pr_title: str,
    pr_description: str,
    pr_diff: str,
    keyword: str,
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    """
    Offline Keyword Judge -> (애매한 것만) LLM Judge 순서로 키워드 하나를 판단하는 오케스트레이터.
    eval/runner.py의 judge 콜백으로 이 함수를 주입해서 쓴다.
    """
    offline_result = offline_keyword_judge(keyword)
    if offline_result is not None:
        return {"keyword": keyword, "source": "offline", **offline_result}

    llm_result = llm_keyword_judge(pr_title, pr_description, pr_diff, keyword, client=client)

    # 프롬프트는 "important면 matched_criteria에 A~D 중 하나는 있어야 한다"고 지시하지만,
    # LLM이 그 지시를 어길 가능성은 코드에서 직접 막아야 한다.
    matched = set(llm_result.get("matched_criteria", []))
    if llm_result["label"] == "important" and not (matched & _REQUIRED_CRITERIA):
        llm_result = {
            **llm_result,
            "label": "not_important",
            "reason": f"[검증 실패로 다운그레이드: A~D 근거 없이 important로 응답함] {llm_result['reason']}",
        }

    return {"source": "llm", **llm_result}
