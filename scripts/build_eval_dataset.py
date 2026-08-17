"""
scripts/build_eval_dataset.py — 평가 데이터셋 생성 스크립트

세 가지 소스를 지원한다.

  codereview : 외부 코드리뷰 데이터셋(JSONL)을 Contextory 평가 포맷으로 변환한다.
               리뷰어가 실제로 남긴 코멘트를 reference_answer로 쓰므로 사람이 만든 라벨에 가깝다.
               DB·LLM 없이 동작한다. 단, gold chunk id가 "cr-{index}"라 실제 DB id와 맞지 않는다.

  fromdb     : 이미 적재된 code_review_vectors 행을 샘플링해 self-retrieval 평가 케이스를 만든다.
               gold chunk id를 services.retrieval.make_review_chunk_id()로 만들기 때문에
               런타임 검색 결과의 chunk_id와 정확히 일치한다 → --live 평가에 바로 쓸 수 있다.
               DB 조회만 하며 임베딩·LLM 호출은 하지 않는다.

  silver     : 실제 검색기(pgvector)의 결과로부터 임시 정답(silver)을 만든다.
               검색기 편향이 들어가므로 절대 성능 근거가 아니라 회귀 비교용으로만 쓴다.
               PostgreSQL + OPENAI_API_KEY가 필요하다.

사용 예
    python -m scripts.build_eval_dataset codereview \
        --input data/code_review_gh/raw/code_review_gh_2023.jsonl \
        --output eval/data/codereview_cases.jsonl --limit 20

    python -m scripts.build_eval_dataset fromdb --limit 10 \
        --output eval/data/live_selfretrieval_cases.jsonl

    python -m scripts.build_eval_dataset silver \
        --input eval/data/codereview_cases.jsonl --output eval/data/silver_cases.jsonl
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings                                     # noqa: E402
from eval.datasets.codereview_adapters import adapt_code_review_gh, extract_keywords  # noqa: E402
from eval.datasets.ground_truth import GroundTruthCase               # noqa: E402
from eval.datasets.loader import iter_jsonl, load_cases, save_cases  # noqa: E402
from eval.datasets.silver_builder import build_silver_dataset        # noqa: E402
from services.retrieval import make_review_chunk_id                 # noqa: E402

DEFAULT_CODEREVIEW_INPUT = "data/code_review_gh/raw/code_review_gh_2023.jsonl"
DEFAULT_CODEREVIEW_OUTPUT = "eval/data/codereview_cases.jsonl"
DEFAULT_SILVER_OUTPUT = "eval/data/silver_cases.jsonl"
DEFAULT_FROMDB_OUTPUT = "eval/data/live_selfretrieval_cases.jsonl"
MIN_COMMENT_LENGTH = 15


def _build_codereview(args: argparse.Namespace) -> int:
    records = iter_jsonl(args.input)
    cases = adapt_code_review_gh(records, limit=args.limit)
    saved = save_cases(cases, args.output)
    print(f"[codereview] {saved}건 저장 → {args.output}")
    if saved:
        print(f"  예시 case_id: {cases[0].case_id}")
        print(f"  gold chunk id 규칙: {cases[0].gold_chunks} (실제 DB id 규칙 확정 시 chunk_id_fn 주입 필요)")
    return 0


def build_cases_from_rows(rows: List[Dict[str, Any]]) -> List[GroundTruthCase]:
    """
    code_review_vectors 행 목록 → self-retrieval 평가 케이스.

    "이 diff로 검색하면 이 diff가 들어 있는 chunk를 찾아오는가"를 보는 sanity/회귀 데이터셋이다.
    검색기가 이것조차 못 맞히면 임베딩·인덱싱 경로에 문제가 있다는 뜻이다.
    일반적인 품질(사람 기준 정답)을 재는 데이터셋은 아니므로 label_type=self_retrieval로 표시한다.

    순수 함수 — DB 없이 단위 테스트할 수 있다.
    """
    cases: List[GroundTruthCase] = []
    for row in rows:
        query = str(row.get("pr_diff") or row.get("source_code") or "").strip()
        comment = str(row.get("review_comment") or "").strip()
        if not query:
            continue

        cases.append(
            GroundTruthCase(
                case_id=f"db-{row.get('id')}",
                query=query,
                gold_chunks=[make_review_chunk_id(row.get("id"))],
                reference_answer=comment or None,
                reference_keywords=extract_keywords(comment) if comment else [],
                metadata={
                    "label_type": "self_retrieval",
                    "db_id": row.get("id"),
                    "orig_idx": row.get("orig_idx"),
                    "dataset_source": row.get("dataset_source"),
                    "source": "code_review_vectors",
                },
            )
        )
    return cases


def _fetch_review_rows(limit: int, min_comment_length: int = MIN_COMMENT_LENGTH) -> List[Dict[str, Any]]:
    """code_review_vectors에서 평가에 쓸 행을 결정적 순서로 샘플링한다 (재현 가능하도록 id 순)."""
    from sqlalchemy import text

    from core.db import engine

    sql = text(
        f"""
        SELECT id, orig_idx, dataset_source, source_code, pr_diff, review_comment
        FROM {settings.CODE_REVIEW_TABLE_NAME}
        WHERE pr_diff IS NOT NULL
          AND length(pr_diff) > 0
          AND length(coalesce(review_comment, '')) >= :min_len
        ORDER BY id
        LIMIT :limit;
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": limit, "min_len": min_comment_length}).mappings().all()
    return [dict(row) for row in rows]


def _build_fromdb(args: argparse.Namespace) -> int:
    rows = _fetch_review_rows(limit=args.limit)
    if not rows:
        raise SystemExit(
            f"{settings.CODE_REVIEW_TABLE_NAME} 에 사용할 행이 없습니다. "
            "먼저 scripts/index_to_pg.py 로 데이터를 적재하세요."
        )

    cases = build_cases_from_rows(rows)
    saved = save_cases(cases, args.output)
    print(f"[fromdb] {saved}건 저장 → {args.output}")
    print(f"  gold chunk id 예시: {cases[0].gold_chunks} (런타임 chunk_id 규칙과 동일)")
    print("  label_type=self_retrieval — 검색 경로 sanity/회귀 확인용이며 사람 기준 정답이 아님")
    return 0


def _build_silver(args: argparse.Namespace) -> int:
    if not settings.OPENAI_API_KEY:
        raise SystemExit("silver 생성에는 실제 검색(임베딩)이 필요합니다. OPENAI_API_KEY를 설정하세요.")

    # 실제 검색기는 services에 있으므로 scripts 계층에서 주입한다 (eval → services 금지 규칙 유지).
    from scripts.eval_adapters import make_live_retrieve_fn

    source_cases = load_cases(args.input)
    if args.limit:
        source_cases = source_cases[: args.limit]
    queries = [
        {"case_id": f"silver-{case.case_id}", "query": case.query,
         "reference_keywords": case.reference_keywords, "metadata": case.metadata}
        for case in source_cases
    ]

    cases = build_silver_dataset(
        queries=queries,
        retrieve_fn=make_live_retrieve_fn(repo_name=args.repo_name, top_k=args.top_k),
        silver_threshold=args.silver_threshold,
        max_gold=args.max_gold,
    )
    saved = save_cases(cases, args.output)
    print(f"[silver] {saved}건 저장 → {args.output} (label_type=silver, 회귀 비교 전용)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="평가 데이터셋 생성")
    sub = parser.add_subparsers(dest="command", required=True)

    cr = sub.add_parser("codereview", help="외부 코드리뷰 JSONL → 평가 포맷 (DB·LLM 불필요)")
    cr.add_argument("--input", default=DEFAULT_CODEREVIEW_INPUT)
    cr.add_argument("--output", default=DEFAULT_CODEREVIEW_OUTPUT)
    cr.add_argument("--limit", type=int, default=20)
    cr.set_defaults(func=_build_codereview)

    fd = sub.add_parser("fromdb", help="적재된 code_review_vectors → self-retrieval 케이스 (DB 조회만)")
    fd.add_argument("--output", default=DEFAULT_FROMDB_OUTPUT)
    fd.add_argument("--limit", type=int, default=10)
    fd.set_defaults(func=_build_fromdb)

    sv = sub.add_parser("silver", help="실제 검색 결과로 silver 라벨 생성 (DB·OpenAI 필요)")
    sv.add_argument("--input", default=DEFAULT_CODEREVIEW_OUTPUT)
    sv.add_argument("--output", default=DEFAULT_SILVER_OUTPUT)
    sv.add_argument("--limit", type=int, default=20)
    sv.add_argument("--repo-name", default=None)
    sv.add_argument("--top-k", type=int, default=settings.RAG_TOP_K)
    sv.add_argument("--silver-threshold", type=float, default=settings.STRONG_EVIDENCE_THRESHOLD)
    sv.add_argument("--max-gold", type=int, default=3)
    sv.set_defaults(func=_build_silver)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
