"""
scripts/run_eval.py — Filter OFF vs ON 비교 리포트 실행 스크립트

배치 원칙
    eval/ 은 services/ 를 import하지 않는다(그 반대도 마찬가지). 둘을 실제로 연결하는 '조립'은
    런타임도 평가도 아닌 이 스크립트 계층의 책임이며, 어댑터는 scripts/eval_adapters.py 에 있다.

실행 모드
    offline(기본) : fixture 검색기 + 템플릿 생성기 + Offline Keyword Judge
                    → LLM·DB 없이 동작. 필터·Confidence는 실제 services 로직을 주입한다.
    live          : 실제 pgvector 검색 + GPT 생성 + LLM Judge
                    → PostgreSQL과 OPENAI_API_KEY가 필요하다.

사용 예
    python -m scripts.run_eval
    python -m scripts.run_eval --strict-threshold 0.9
    python -m scripts.run_eval --strict-threshold 0.9 --vulnerable-confidence
    python -m scripts.run_eval --live --repo-name org/contextory
    python -m scripts.run_eval --json
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

# 저장소 루트를 import 경로에 추가 (python scripts/run_eval.py 로 직접 실행하는 경우 대비)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings                                       # noqa: E402
from eval.datasets.loader import DEFAULT_GROUND_TRUTH_PATH, load_cases  # noqa: E402
from eval.fakes import (                                                # noqa: E402
    SAMPLE_CORPUS, make_keyword_retriever, make_mean_score_confidence_fn, make_template_generator,
)
from eval.judge import LLMJudge, OfflineKeywordJudge                    # noqa: E402
from eval.report import build_report, to_json_report, to_text_report    # noqa: E402
from eval.metrics.generation import GENERATION_CRITERIA               # noqa: E402
from eval.runner import MODE_FILTER_ON, run_comparison, run_evaluation  # noqa: E402
from scripts.eval_adapters import (                                     # noqa: E402
    make_live_generate_fn, make_live_retrieve_fn, make_runtime_filter_fn, runtime_confidence_fn,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter OFF vs ON 비교 평가 리포트")
    parser.add_argument("--dataset", default=str(DEFAULT_GROUND_TRUTH_PATH), help="Ground Truth JSONL 경로")
    parser.add_argument("--threshold", type=float, default=settings.SIM_THRESHOLD, help="Context Filter 임계값")
    parser.add_argument("--k", type=int, default=settings.EVAL_DEFAULT_K, help="평가 K")
    parser.add_argument("--json", action="store_true", help="JSON 리포트로 출력")
    parser.add_argument(
        "--strict-threshold", type=float, default=None,
        help="추가로 비교할 높은 임계값 (예: 0.9 — 필터가 gold를 지우는 상황 확인용)",
    )
    parser.add_argument(
        "--vulnerable-confidence", action="store_true",
        help="Confidence를 '평균 유사도' 취약 공식으로 대체해 Fake Confidence 판정이 실제로 잡히는지 확인",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="실제 pgvector 검색 + GPT 생성 + LLM Judge 사용 (PostgreSQL / OPENAI_API_KEY 필요)",
    )
    parser.add_argument("--repo-name", default=None, help="--live 에서 repo_code_vectors를 격리 검색할 repo 이름")
    parser.add_argument(
        "--no-generation", action="store_true",
        help="생성·judge 없이 검색/필터 지표만 계산 (--live에서 GPT 비용 없이 검색 경로만 검증할 때 사용)",
    )
    parser.add_argument(
        "--criteria", default=None,
        help=f"평가할 생성 지표를 콤마로 지정 (기본: {','.join(GENERATION_CRITERIA)})",
    )
    return parser.parse_args()


def _build_callbacks(args: argparse.Namespace) -> Dict[str, Any]:
    """실행 모드에 따라 주입할 callback 묶음을 만든다."""
    if args.live:
        if not settings.OPENAI_API_KEY:
            raise SystemExit("--live 실행에는 OPENAI_API_KEY가 필요합니다. .env를 확인하세요.")
        callbacks: Dict[str, Any] = {
            "retrieve_fn": make_live_retrieve_fn(repo_name=args.repo_name, top_k=args.k),
        }
        if not args.no_generation:
            callbacks["generate_fn"] = make_live_generate_fn()
            callbacks["judge"] = LLMJudge()
        return callbacks

    callbacks = {"retrieve_fn": make_keyword_retriever(top_k=args.k)}
    if not args.no_generation:
        callbacks["generate_fn"] = make_template_generator()
        callbacks["judge"] = OfflineKeywordJudge()
    return callbacks


def _warn_if_dataset_does_not_match_fixture(args: argparse.Namespace, cases: List[Any]) -> None:
    """
    offline 모드는 fixture 코퍼스로 검색한다. 데이터셋의 gold chunk가 그 코퍼스에 하나도 없으면
    모든 검색 지표가 0으로 나오는데, 이는 품질 문제가 아니라 데이터셋/검색기 불일치다.
    수치를 오해하지 않도록 먼저 경고한다.
    """
    if args.live:
        return

    corpus_ids = {chunk["chunk_id"] for chunk in SAMPLE_CORPUS}
    gold_ids = {gold for case in cases for gold in case.gold_chunks}
    if gold_ids and not (gold_ids & corpus_ids):
        print(
            "⚠ 경고: 데이터셋의 gold chunk가 offline fixture 코퍼스에 하나도 없습니다.\n"
            "  검색 지표가 0으로 나오는 것은 검색 품질이 아니라 데이터셋/검색기 불일치 때문입니다.\n"
            "  실제 벡터 DB로 평가하려면 --live 를 사용하세요.\n",
            file=sys.stderr,
        )


def main() -> int:
    args = _parse_args()
    cases = load_cases(args.dataset)
    _warn_if_dataset_does_not_match_fixture(args, cases)
    callbacks = _build_callbacks(args)
    confidence_fn = make_mean_score_confidence_fn() if args.vulnerable_confidence else runtime_confidence_fn
    if args.criteria:
        callbacks["criteria"] = tuple(c.strip() for c in args.criteria.split(",") if c.strip())

    comparison = run_comparison(
        cases=cases,
        filter_fn=make_runtime_filter_fn(args.threshold),
        confidence_fn=confidence_fn,
        k=args.k,
        sim_threshold=args.threshold,
        **callbacks,
    )

    if args.strict_threshold is not None:
        comparison[f"filter_on_strict_{args.strict_threshold}"] = run_evaluation(
            cases=cases,
            filter_fn=make_runtime_filter_fn(args.strict_threshold),
            confidence_fn=confidence_fn,
            k=args.k,
            sim_threshold=args.threshold,
            mode=MODE_FILTER_ON,
            **callbacks,
        )

    if args.live:
        mode_label = "live(pgvector 검색만, 생성·judge 없음)" if args.no_generation else "live(pgvector+GPT+LLMJudge)"
    else:
        mode_label = "offline(fixture 검색만)" if args.no_generation else "offline(fixture+OfflineKeywordJudge)"
    confidence_label = "mean-score(취약 공식 시뮬레이션)" if args.vulnerable_confidence else "services/confidence.py"
    report = build_report(
        comparison,
        title=(
            f"Contextory RAG Evaluation (mode={mode_label}, "
            f"threshold={args.threshold}, confidence={confidence_label})"
        ),
    )
    print(to_json_report(report) if args.json else to_text_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
