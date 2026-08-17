"""Judge 테스트 — Offline Keyword Judge와 LLM Judge(가짜 client)가 동일 인터페이스를 지킨다."""

import json

from eval.judge import CRITERIA, LLMJudge, OfflineKeywordJudge, offline_generation_judge
from eval.metrics.generation import evaluate_generation, exact_match, hallucination_rate, token_f1

QUESTION = "JWT 로그인 도입 PR: AuthService에 login 추가"
CONTEXT = "AuthService에 login 메서드를 추가하고 SecurityConfig에 JWT 필터를 등록했다."
GROUNDED_ANSWER = "AuthService에 login 메서드를 추가하고 SecurityConfig에 JWT 필터를 등록했다."
HALLUCINATED_ANSWER = "Redis 세션 클러스터를 도입하고 Kafka 이벤트 파이프라인을 새로 구축했다."


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content=None, raise_exc=None):
        self._content = content
        self._raise_exc = raise_exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_exc:
            raise self._raise_exc
        return _FakeResponse(self._content)


class FakeClient:
    """OpenAI 클라이언트 흉내 (네트워크 호출 없음)"""

    def __init__(self, content=None, raise_exc=None):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(content=content, raise_exc=raise_exc)


# ==========================================
# Offline Keyword Judge
# ==========================================

def test_offline_judge_scores_grounded_answer_high():
    result = OfflineKeywordJudge()(question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER, criterion="groundedness")

    assert result["score"] > 0.9
    assert result["source"] == "offline_keyword"
    assert result["error"] is None


def test_offline_judge_scores_hallucinated_answer_low():
    grounded = offline_generation_judge(question=QUESTION, context=CONTEXT, answer=HALLUCINATED_ANSWER, criterion="groundedness")
    hallucination = offline_generation_judge(question=QUESTION, context=CONTEXT, answer=HALLUCINATED_ANSWER, criterion="hallucination")

    assert grounded["score"] < 0.4
    assert hallucination["score"] > 0.6  # hallucination만 '높을수록 나쁨'


def test_offline_judge_completeness_uses_reference_keywords():
    full = offline_generation_judge(
        question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER,
        criterion="completeness", reference={"reference_keywords": ["AuthService", "JWT", "SecurityConfig"]},
    )
    partial = offline_generation_judge(
        question=QUESTION, context=CONTEXT, answer="AuthService만 수정했다.",
        criterion="completeness", reference={"reference_keywords": ["AuthService", "JWT", "SecurityConfig"]},
    )

    assert full["score"] == 1.0
    assert partial["score"] < 0.5


def test_offline_judge_without_reference_reports_error_not_fake_score():
    result = offline_generation_judge(question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER, criterion="completeness")

    assert result["score"] is None
    assert result["error"] == "no_reference"


def test_unsupported_criterion_is_rejected():
    result = offline_generation_judge(criterion="vibes")

    assert result["score"] is None
    assert result["error"] == "unsupported_criterion"


def test_offline_judge_supports_every_criterion():
    judge = OfflineKeywordJudge()
    for criterion in CRITERIA:
        result = judge(
            question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER,
            criterion=criterion, reference=["AuthService"],
        )
        assert result["criterion"] == criterion


# ==========================================
# LLM Judge (가짜 client)
# ==========================================

def test_llm_judge_parses_scores():
    client = FakeClient(content=json.dumps({"score": 0.75, "reason": "근거 있음"}))

    result = LLMJudge(client=client)(question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER, criterion="faithfulness")

    assert result["score"] == 0.75
    assert result["reason"] == "근거 있음"
    assert result["error"] is None


def test_llm_judge_handles_broken_json_without_crashing():
    client = FakeClient(content="이건 JSON이 아님")

    result = LLMJudge(client=client)(question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER, criterion="faithfulness")

    assert result["score"] is None
    assert result["error"]


def test_llm_judge_handles_api_failure():
    client = FakeClient(raise_exc=RuntimeError("rate limit exceeded"))

    result = LLMJudge(client=client)(question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER, criterion="faithfulness")

    assert result["score"] is None
    assert "rate limit" in result["error"]


def test_both_judges_share_the_same_interface():
    fake_client = FakeClient(content=json.dumps({"score": 1.0, "reason": "ok"}))
    for judge in (OfflineKeywordJudge(), LLMJudge(client=fake_client)):
        result = judge(question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER, criterion="groundedness", reference=None)
        assert set(result) == {"criterion", "score", "reason", "error", "source"}


# ==========================================
# generation 지표 (judge 주입)
# ==========================================

def test_evaluate_generation_with_injected_judge():
    result = evaluate_generation(
        question=QUESTION, context=CONTEXT, answer=GROUNDED_ANSWER,
        judge=OfflineKeywordJudge(), criteria=("groundedness", "hallucination"),
        reference=["AuthService"],
    )

    assert set(result["scores"]) == {"groundedness", "hallucination"}
    assert result["details"]["groundedness"]["source"] == "offline_keyword"


def test_hallucination_rate_counts_ungrounded_cases():
    assert hallucination_rate([{"groundedness": 0.9}, {"groundedness": 0.2}]) == 0.5
    assert hallucination_rate([{"hallucination": 0.9}, {"hallucination": 0.1}]) == 0.5
    assert hallucination_rate([]) == 0.0


def test_exact_match_and_f1():
    assert exact_match("JWT 인증 도입", " jwt 인증 도입 ") == 1.0
    assert exact_match("A", "B") == 0.0
    assert token_f1("JWT 인증 도입", "JWT 인증 적용") > 0.5
    assert token_f1("", "무언가") == 0.0
