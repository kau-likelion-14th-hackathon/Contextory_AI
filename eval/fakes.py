"""
fakes.py — LLM·Vector DB 없이 평가 파이프라인을 돌리기 위한 가짜 callback 모음

runner는 retrieve_fn / filter_fn / generate_fn / confidence_fn / judge 를 주입받으므로,
여기 있는 가짜 구현만으로 전체 평가와 리포트가 끝까지 돌아간다.
(단위 테스트와 오프라인 리포트 실행 스크립트가 공용으로 쓴다)

주의: 이 모듈은 검색기·생성기를 '흉내'낼 뿐 품질을 대표하지 않는다.
      절대 성능 수치의 근거로 쓰지 말 것.
"""

import re
from typing import Any, Callable, Dict, List, Optional, Sequence

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_./#+-]{1,}|[가-힣]{2,}|\d+")

# 최소 샘플 코퍼스 — eval/data/sample_ground_truth.jsonl 의 gold chunk id와 맞춰 둔다.
SAMPLE_CORPUS: List[Dict[str, Any]] = [
    {
        "chunk_id": "cr-101",
        "source": "code_review_vectors",
        "text": "JWT 토큰 검증 필터를 SecurityConfig에 등록할 때 필터 순서를 확인하세요. AuthService의 재발급 로직은 refresh token 만료 검증이 필요합니다.",
        "keywords": ["JWT", "SecurityConfig", "AuthService", "재발급", "토큰"],
    },
    {
        "chunk_id": "repo-auth01",
        "source": "src/main/java/auth/AuthService.java",
        "text": "public class AuthService { public TokenResponse login(...) {...} public TokenResponse reissue(...) {...} } // 기존에는 세션 기반 인증만 지원했다",
        "keywords": ["AuthService", "login", "JWT", "세션", "재발급"],
    },
    {
        "chunk_id": "cr-201",
        "source": "code_review_vectors",
        "text": "목록 조회에서 연관 엔티티를 지연 로딩하면 N+1 쿼리가 발생합니다. ProjectRepository에 fetch join을 적용하세요.",
        "keywords": ["N+1", "fetch join", "ProjectRepository", "조회", "쿼리"],
    },
    {
        "chunk_id": "cr-301",
        "source": "code_review_vectors",
        "text": "외부 콜백 전송은 일시적 네트워크 오류에 대비해 지수 백오프 재시도를 두는 것이 좋습니다.",
        "keywords": ["callback", "재시도", "백오프", "콜백", "전송"],
    },
    {
        "chunk_id": "repo-cb01",
        "source": "services/callback_service.py",
        "text": "def send_analysis_callback(callback_url, payload): # 분석 완료/실패 결과를 Backend 콜백 URL로 전송한다",
        "keywords": ["callback", "콜백", "전송", "분석", "백오프"],
    },
    {
        "chunk_id": "cr-901",
        "source": "code_review_vectors",
        "text": "README의 오타를 수정해 주세요.",
        "keywords": ["README", "오타", "문서"],
    },
    {
        "chunk_id": "cr-902",
        "source": "code_review_vectors",
        "text": "CSS 클래스 이름을 케밥 케이스로 통일하는 편이 좋겠습니다.",
        "keywords": ["CSS", "클래스", "케밥"],
    },
]


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_PATTERN.findall(text or "")]


def keyword_similarity(query: str, chunk: Dict[str, Any]) -> float:
    """쿼리 토큰과 chunk 키워드의 겹침 비율 (0.0~1.0). 결정적이라 테스트에 재현 가능하다."""
    keywords = [str(k).lower() for k in chunk.get("keywords", [])]
    if not keywords:
        return 0.0
    query_text = " ".join(_tokens(query))
    hits = len([k for k in keywords if k in query_text])
    return round(hits / len(keywords), 4)


def make_keyword_retriever(
    corpus: Sequence[Dict[str, Any]] = SAMPLE_CORPUS,
    top_k: int = 5,
) -> Callable[[str], List[Dict[str, Any]]]:
    """키워드 겹침으로 유사도를 매기는 가짜 retrieve_fn"""

    def retrieve(query: str) -> List[Dict[str, Any]]:
        scored = [
            {**chunk, "similarity_score": keyword_similarity(query, chunk)}
            for chunk in corpus
        ]
        scored.sort(key=lambda c: c["similarity_score"], reverse=True)
        return scored[:top_k]

    return retrieve


def make_threshold_filter(
    sim_threshold: float,
    preserve_top1: bool = True,
) -> Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """
    임계값 기반 가짜 filter_fn.
    preserve_top1=False 로 두면 Top-1 보존 규칙을 일부러 깨뜨릴 수 있어
    false_deletion / recall_delta 가 실제로 잡히는지 검증할 수 있다.
    """

    def filter_fn(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ordered = sorted(chunks, key=lambda c: float(c.get("similarity_score", 0.0) or 0.0), reverse=True)
        kept = []
        for idx, chunk in enumerate(ordered):
            if (preserve_top1 and idx == 0) or float(chunk.get("similarity_score", 0.0) or 0.0) >= sim_threshold:
                kept.append(chunk)
        return kept

    return filter_fn


def make_template_generator(max_contexts: int = 3) -> Callable[[str, List[Dict[str, Any]]], str]:
    """
    LLM 없이 '주어진 context만 사용해' 답을 만드는 가짜 generate_fn.
    context 밖의 내용을 만들어내지 않으므로 groundedness는 높고, 근거가 없으면 그대로 빈약해진다.
    """

    def generate(query: str, contexts: List[Dict[str, Any]]) -> str:
        if not contexts:
            return "근거를 찾지 못해 분석 초안을 만들 수 없습니다."
        body = " ".join(str(c.get("text", "")) for c in contexts[:max_contexts])
        return f"[요약] {query}\n[근거] {body}"

    return generate


def make_fake_confidence_fn(weight_top: float = 0.7, weight_retention: float = 0.3) -> Callable[..., float]:
    """
    런타임 confidence를 주입할 수 없는 환경(순수 eval 단위 테스트)에서 쓰는 가짜 confidence_fn.
    실제 리포트에는 services/confidence.py 를 주입해 쓰는 것이 원칙이다.
    """

    def confidence_fn(contexts: List[Dict[str, Any]], filter_ratio: float) -> float:
        scores = [float(c.get("similarity_score", 0.0) or 0.0) for c in contexts]
        if not scores:
            return 0.0
        top = max(scores)
        return round(weight_top * top + weight_retention * max(0.0, 1.0 - float(filter_ratio)), 4)

    return confidence_fn


def make_mean_score_confidence_fn() -> Callable[..., float]:
    """
    '평균 유사도' 기반의 취약한 confidence 공식 시뮬레이터.

    필터가 정답 Context를 지우면 남은 chunk의 평균 유사도가 올라 Confidence가 상승하는,
    Fake Confidence의 전형적인 실패 모드를 재현한다. 리포트의 판정 규칙이 이 상황을
    실제로 잡아내는지 검증하는 용도이며, 런타임에서 쓰면 안 된다.
    """

    def confidence_fn(contexts: List[Dict[str, Any]], filter_ratio: float) -> float:
        scores = [float(c.get("similarity_score", 0.0) or 0.0) for c in contexts]
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 4)

    return confidence_fn


def make_failing_retriever(error: Optional[Exception] = None) -> Callable[[str], List[Dict[str, Any]]]:
    """검색 단계 장애를 흉내내는 retrieve_fn (실패가 조용히 삼켜지지 않는지 확인용)"""

    def retrieve(query: str) -> List[Dict[str, Any]]:
        raise error or RuntimeError("vector db down")

    return retrieve
