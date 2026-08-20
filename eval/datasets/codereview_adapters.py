"""
datasets/codereview_adapters.py — 외부 코드리뷰 데이터셋 → Contextory 평가 포맷 변환

지원 소스
    code_review_gh (data/code_review_gh/raw/*.jsonl)
        {"repo_name", "dataset", "lang", "pr_id", "owner", "reviewer",
         "diff_hunk", "code_review_comment"}

변환 규칙
    - query            : diff_hunk (실제 파이프라인의 검색 쿼리도 PR diff 기반이므로 동일하게 맞춘다)
    - gold_chunks      : 해당 레코드가 적재된 벡터 chunk id. id 매핑 함수를 주입해 결정한다.
                         (기본은 인덱스 기반 "cr-{idx}" — 실제 DB id 규칙과 다르면 chunk_id_fn을 주입)
    - reference_answer : code_review_comment (리뷰어가 실제로 남긴 지적)
    - reference_keywords: 리뷰 코멘트에서 추출한 식별자/기술 키워드 (Offline Keyword Judge용)

이 모듈은 services/ 를 import하지 않으며 DB에도 접근하지 않는다.
"""

import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from eval.datasets.ground_truth import GroundTruthCase

# 리뷰 코멘트에서 뽑을 "판단 근거가 되는" 토큰: 식별자/파일명/API 경로/숫자 포함 토큰
_KEYWORD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_./#-]{2,}")
_KEYWORD_STOPWORDS = {
    "the", "this", "that", "with", "from", "have", "should", "would", "could", "please",
    "there", "here", "what", "when", "which", "your", "you", "not", "and", "for", "but",
    "can", "add", "use", "using", "just", "need", "needs", "think", "maybe", "also",
}
MIN_COMMENT_LENGTH = 15


def extract_keywords(comment: str, limit: int = 8) -> List[str]:
    """리뷰 코멘트에서 Offline Keyword Judge가 쓸 키워드를 추출한다(등장 순서 유지, 중복 제거)."""
    keywords: List[str] = []
    for token in _KEYWORD_PATTERN.findall(comment or ""):
        lowered = token.lower()
        if lowered in _KEYWORD_STOPWORDS or lowered in {k.lower() for k in keywords}:
            continue
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def _default_chunk_id(record: Dict[str, Any], idx: int) -> str:
    return f"cr-{idx}"


def adapt_code_review_gh(
    records: Iterable[Dict[str, Any]],
    chunk_id_fn: Optional[Callable[[Dict[str, Any], int], str]] = None,
    min_comment_length: int = MIN_COMMENT_LENGTH,
    limit: Optional[int] = None,
) -> List[GroundTruthCase]:
    """
    code_review_gh 레코드를 GroundTruthCase 목록으로 변환한다.

    chunk_id_fn을 주입하면 gold chunk id를 실제 벡터 DB의 id 규칙에 맞출 수 있다.
    (DB 적재 id 규칙이 확정되기 전까지는 기본 "cr-{idx}"를 쓴다 — 보고서의 '확인 필요' 참고)
    """
    make_id = chunk_id_fn or _default_chunk_id
    cases: List[GroundTruthCase] = []

    for idx, record in enumerate(records):
        comment = str(record.get("code_review_comment") or "").strip()
        diff = str(record.get("diff_hunk") or "").strip()
        # 'done', 'fixed' 류 단답형과 diff 없는 레코드는 평가 신호가 되지 못하므로 제외한다.
        if len(comment) < min_comment_length or not diff:
            continue

        chunk_id = make_id(record, idx)
        cases.append(
            GroundTruthCase(
                case_id=f"{record.get('repo_name', 'unknown')}#{record.get('pr_id', idx)}-{idx}",
                query=diff,
                gold_chunks=[chunk_id],
                reference_answer=comment,
                reference_keywords=extract_keywords(comment),
                metadata={
                    "repo_name": record.get("repo_name"),
                    "owner": record.get("owner"),
                    "reviewer": record.get("reviewer"),
                    "lang": record.get("lang"),
                    "dataset": record.get("dataset"),
                    "pr_id": record.get("pr_id"),
                    "source": "code_review_gh",
                },
            )
        )
        if limit is not None and len(cases) >= limit:
            break

    return cases


ADAPTERS: Dict[str, Callable[..., List[GroundTruthCase]]] = {
    "code_review_gh": adapt_code_review_gh,
}


def adapt(source: str, records: Iterable[Dict[str, Any]], **kwargs: Any) -> List[GroundTruthCase]:
    """소스 이름으로 어댑터를 골라 실행한다."""
    if source not in ADAPTERS:
        raise ValueError(f"지원하지 않는 데이터셋 소스입니다: {source} (지원: {sorted(ADAPTERS)})")
    return ADAPTERS[source](records, **kwargs)
