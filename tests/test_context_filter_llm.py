"""LLM Context Filter Agent 테스트 — 실제 OpenAI 호출 없이 가짜 client로 검증"""

import json

from services.context_filter import build_llm_relevance_map, filter_contexts_with_llm


class _FakeCompletions:
    def __init__(self, content=None, raise_exc=None):
        self._content = content
        self._raise_exc = raise_exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_exc:
            raise self._raise_exc
        return type(
            "Resp", (), {"choices": [type("C", (), {"message": type("M", (), {"content": self._content})()})()]}
        )()


class FakeClient:
    def __init__(self, content=None, raise_exc=None):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(content=content, raise_exc=raise_exc)


CHUNKS = [
    {"chunk_id": "c1", "similarity_score": 0.90, "text": "JWT 필터 등록 관련 리뷰"},
    {"chunk_id": "c2", "similarity_score": 0.70, "text": "README 오타 수정 요청"},
    {"chunk_id": "c3", "similarity_score": 0.65, "text": "AuthService 재발급 로직 리뷰"},
]


def _client(decisions):
    return FakeClient(content=json.dumps({"decisions": decisions}, ensure_ascii=False))


def test_llm_agent_removes_irrelevant_chunk_even_above_threshold():
    """유사도는 임계값을 넘어도 LLM이 무관하다고 하면 제거된다."""
    client = _client([
        {"chunk_id": "c1", "keep": True, "reason": "직접 관련"},
        {"chunk_id": "c2", "keep": False, "reason": "문서 오타로 무관"},
        {"chunk_id": "c3", "keep": True, "reason": "관련"},
    ])

    outcome = filter_contexts_with_llm(CHUNKS, query_text="JWT 로그인 도입", sim_threshold=0.5, client=client)

    assert [c["chunk_id"] for c in outcome.kept] == ["c1", "c3"]
    assert [c["chunk_id"] for c in outcome.removed] == ["c2"]
    assert outcome.mode == "llm"
    assert outcome.filter_ratio == round(1 / 3, 4)


def test_llm_agent_cannot_delete_top1():
    """LLM이 전부 무관하다고 해도 Top-1은 보존된다."""
    client = _client([{"chunk_id": cid, "keep": False, "reason": "무관"} for cid in ("c1", "c2", "c3")])

    outcome = filter_contexts_with_llm(CHUNKS, query_text="질의", sim_threshold=0.5, client=client)

    assert [c["chunk_id"] for c in outcome.kept] == ["c1"]
    assert outcome.top1_preserved is True


def test_undecided_chunks_fall_back_to_threshold():
    """LLM이 판단하지 않은 chunk는 임계값 기준으로 처리하고 그 사실을 남긴다."""
    client = _client([{"chunk_id": "c2", "keep": False, "reason": "무관"}])

    outcome = filter_contexts_with_llm(CHUNKS, query_text="질의", sim_threshold=0.8, client=client)

    # c1(0.90)은 Top-1이라 보존, c3(0.65)는 임계값 0.8 미달로 제거
    assert [c["chunk_id"] for c in outcome.kept] == ["c1"]
    assert any("판단하지 않은" in note for note in outcome.notes)


def test_llm_failure_falls_back_to_threshold_filter_and_records_note():
    """LLM 호출 실패를 조용히 전량 통과로 처리하지 않는다."""
    client = FakeClient(raise_exc=RuntimeError("rate limit exceeded"))

    outcome = filter_contexts_with_llm(CHUNKS, query_text="질의", sim_threshold=0.8, client=client)

    assert outcome.mode == "llm_fallback"
    assert [c["chunk_id"] for c in outcome.kept] == ["c1"]
    assert any("폴백" in note for note in outcome.notes)


def test_broken_json_response_falls_back():
    client = FakeClient(content="JSON 아님")

    outcome = filter_contexts_with_llm(CHUNKS, query_text="질의", sim_threshold=0.5, client=client)

    assert outcome.mode == "llm_fallback"
    assert outcome.notes


def test_empty_contexts_short_circuit_without_llm_call():
    client = FakeClient(raise_exc=RuntimeError("호출되면 안 됨"))

    outcome = filter_contexts_with_llm([], query_text="질의", client=client)

    assert outcome.kept == [] and outcome.removed == []
    assert client.chat.completions.calls == []


def test_relevance_map_sends_query_and_chunks_to_llm():
    client = _client([{"chunk_id": "c1", "keep": True}])

    decisions = build_llm_relevance_map(CHUNKS, query_text="JWT 로그인 도입", client=client)

    assert decisions == {"c1": True}
    prompt = client.chat.completions.calls[0]["messages"][1]["content"]
    assert "JWT 로그인 도입" in prompt
    assert "c1" in prompt and "c3" in prompt
    assert client.chat.completions.calls[0]["response_format"] == {"type": "json_object"}
