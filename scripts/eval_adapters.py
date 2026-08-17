"""
scripts/eval_adapters.py — services(런타임)를 eval(평가)의 callback 인터페이스로 감싸는 조립 계층

`eval/` 은 `services/` 를 import하지 않는다(그 반대도 마찬가지). 둘을 실제로 연결하는 책임은
런타임도 평가도 아닌 이 scripts 계층에 있다. run_eval.py가 이 어댑터들을 주입해서 평가를 돌린다.

제공하는 어댑터
    make_runtime_filter_fn(threshold)       : services.context_filter (임계값 모드)
    make_llm_filter_fn(threshold)           : services.context_filter (LLM Agent 모드, OpenAI 필요)
    runtime_confidence_fn                   : services.confidence 의 실제 계산식
    make_live_retrieve_fn(...)              : services.retrieval (pgvector 실제 검색, DB 필요)
    make_live_generate_fn(...)              : services.prompt_builder + GPT 구조화 출력 (OpenAI 필요)
"""

from typing import Any, Callable, Dict, List, Optional

from core.config import settings
from services.analysis_service import parse_llm_json
from services.confidence import calculate_confidence
from services.context_filter import filter_contexts, filter_contexts_with_llm
from services.prompt_builder import PRInput, ProjectInfo, SYSTEM_INSTRUCTION, build_grounded_prompt
from services.retrieval import retrieve_with_signals


# ==========================================
# 필터 / Confidence (DB·LLM 없이도 동작)
# ==========================================

def make_runtime_filter_fn(sim_threshold: Optional[float] = None) -> Callable:
    """임계값 기반 실제 필터를 eval의 filter_fn 인터페이스((kept, removed) 반환)로 감싼다."""
    threshold = settings.SIM_THRESHOLD if sim_threshold is None else sim_threshold

    def filter_fn(chunks: List[Dict[str, Any]]):
        outcome = filter_contexts(chunks, sim_threshold=threshold, filter_mode="on")
        return outcome.kept, outcome.removed

    return filter_fn


def make_llm_filter_fn(
    query_provider: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
    sim_threshold: Optional[float] = None,
    client: Any = None,
) -> Callable:
    """
    LLM Context Filter Agent를 eval의 filter_fn으로 감싼다. (OpenAI 호출 발생)
    query_provider가 없으면 chunk 목록만으로는 질의를 알 수 없으므로 빈 질의로 판단하게 되니,
    실행 스크립트에서 현재 케이스의 query를 넘겨주는 provider를 주입하는 것을 권장한다.
    """
    threshold = settings.SIM_THRESHOLD if sim_threshold is None else sim_threshold

    def filter_fn(chunks: List[Dict[str, Any]]):
        query_text = query_provider(chunks) if query_provider else ""
        outcome = filter_contexts_with_llm(
            chunks, query_text=query_text, sim_threshold=threshold, client=client
        )
        return outcome.kept, outcome.removed

    return filter_fn


def runtime_confidence_fn(contexts: List[Dict[str, Any]], filter_ratio: float) -> float:
    """services/confidence.py 의 실제 계산식을 그대로 주입한다."""
    return calculate_confidence(contexts, filter_ratio).score


# ==========================================
# Live 어댑터 (실제 pgvector / OpenAI 사용)
# ==========================================

def make_live_retrieve_fn(
    repo_name: Optional[str] = None,
    top_k: Optional[int] = None,
) -> Callable[[str], List[Dict[str, Any]]]:
    """
    실제 pgvector 검색을 eval의 retrieve_fn으로 감싼다. (PostgreSQL + OpenAI 임베딩 필요)
    검색 실패(RetrievalError)는 잡지 않고 그대로 올려 runner가 케이스 실패로 기록하게 둔다.
    """

    def retrieve_fn(query: str) -> List[Dict[str, Any]]:
        outcome = retrieve_with_signals(
            query_text=query,
            repo_name=repo_name,
            top_k=top_k if top_k is not None else settings.RAG_TOP_K,
        )
        return outcome.chunks

    return retrieve_fn


def make_live_generate_fn(
    project: Optional[ProjectInfo] = None,
    client: Any = None,
    model: Optional[str] = None,
) -> Callable[[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """
    실제 Grounded Prompt + GPT 구조화 출력을 eval의 generate_fn으로 감싼다. (OpenAI 필요)
    평가용 answer 텍스트는 생성된 기록 초안의 핵심 필드를 이어붙여 만든다.
    """

    def generate_fn(query: str, contexts: List[Dict[str, Any]]) -> Dict[str, Any]:
        prompt = build_grounded_prompt(
            pr=PRInput(title=query[:200], body="", diff=query),
            filtered_contexts=contexts,
            project=project,
        )

        nonlocal client
        if client is None:
            from openai import OpenAI  # 지연 import

            client = OpenAI(api_key=settings.OPENAI_API_KEY)

        response = client.chat.completions.create(
            model=model or settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        output = parse_llm_json(response.choices[0].message.content)

        parts = [
            str(output.get(key, ""))
            for key in ("summary", "purpose", "changeReason", "before", "after")
            if output.get(key)
        ]
        parts += [str(item) for item in output.get("followUpTasks", []) or []]
        return {"answer": "\n".join(parts), "raw": output}

    return generate_fn
