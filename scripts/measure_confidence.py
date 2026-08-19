"""
scripts/measure_confidence.py — 실제 PR의 Confidence 분포를 재고, 검색 쿼리 구성을 비교한다.

왜 필요한가
    Confidence의 70%(top_score 0.5 + strong_evidence 0.2)는 검색 유사도에서 나온다.
    즉 점수를 올리는 정직한 방법은 기준값을 내리는 게 아니라 검색을 잘하는 것이다.
    어떤 쿼리 구성이 실제로 유사도를 올리는지 같은 PR로 비교한다.

쿼리 구성 (variants)
    prod        현재 운영 방식 — "PR Title/Summary: {번역문}\nPR Diff:\n{전체 diff}"
    prod+files  위 + 변경된 파일 경로
    files+diff  번역문 + 파일 경로 + diff 추가 줄의 식별자 (전체 diff 대신 식별자만)

    PR이 건드린 파일은 인덱스에 그 파일의 청크로 들어가 있다. 쿼리에 경로·식별자를
    넣으면 바로 그 청크와의 유사도가 오를 것이라는 가설을 검증한다.

사용 예
    python -m scripts.measure_confidence --limit 8
    python -m scripts.measure_confidence --limit 8 --variant prod --json out.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings  # noqa: E402

DEFAULT_REPOS = (
    "kau-likelion-14th-hackathon/Contextory_AI",
    "kau-likelion-14th-hackathon/Contextory_BackEnd",
    "kau-likelion-14th-hackathon/OffCourse_FrontEnd",
)
# prod = 현재 운영 코드가 만드는 쿼리 (analysis_service.py 의 query_text 구성과 동일)
VARIANTS = ("prod", "prod_capped", "files+diff")
# diff에서 뽑을 식별자: 클래스/함수/타입 이름처럼 검색에 쓸모 있는 토큰
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
MAX_IDENTIFIERS = 40
# text-embedding-3-small 입력 한도는 8192 토큰. 대략 4자/토큰으로 보고 여유를 둔다.
CAP_CHARS = 20_000


def _headers() -> Dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_prs(repo: str, limit: int) -> List[Dict[str, Any]]:
    """병합된 PR의 제목·본문과 변경 파일(경로·patch)을 가져온다."""
    import httpx

    out: List[Dict[str, Any]] = []
    try:
        with httpx.Client(timeout=30.0, headers=_headers()) as client:
            listing = client.get(
                f"https://api.github.com/repos/{repo}/pulls",
                params={"state": "closed", "per_page": min(limit * 3, 100)},
            )
            listing.raise_for_status()
            for pr in [p for p in listing.json() if p.get("merged_at")][:limit]:
                files = client.get(
                    f"https://api.github.com/repos/{repo}/pulls/{pr['number']}/files",
                    params={"per_page": 50},
                )
                files.raise_for_status()
                out.append({
                    "number": pr["number"],
                    "title": pr.get("title") or "",
                    "body": pr.get("body") or "",
                    "files": [
                        {"path": f.get("filename", ""), "patch": f.get("patch", "") or ""}
                        for f in files.json()
                    ],
                })
    except Exception as e:
        print(f"  [건너뜀] {repo}: {type(e).__name__}: {e}")
    return out


def build_query(pr: Dict[str, Any], variant: str) -> str:
    """쿼리 구성. prod 변형이 현재 운영 방식과 동일하다."""
    from services.translation_service import translate_pr_to_en_query

    base = translate_pr_to_en_query(pr["title"], pr["body"])
    paths = [f["path"] for f in pr["files"] if f["path"]]
    # 운영의 build_diff_content 와 같은 형식으로 diff를 합친다
    full_diff = "\n\n".join(
        f"### {f['path']}\n{f['patch']}" for f in pr["files"] if f["patch"]
    )

    if variant == "prod":
        return f"PR Title/Summary: {base}\nPR Diff:\n{full_diff}"
    if variant == "prod_capped":
        # 운영과 같은 구성이되 임베딩 입력 한도 안에 들어오도록 자른다.
        # 파일 경로는 앞에 두어 잘려나가지 않게 한다 (검색에 가장 도움이 되는 신호).
        head = f"PR Title/Summary: {base}\nChanged files: {' '.join(paths)}\nPR Diff:\n"
        return head + full_diff[: max(0, CAP_CHARS - len(head))]

    parts = [base, " ".join(paths)]

    if variant == "files+diff":
        # diff의 추가된 줄에서만 식별자를 뽑는다 (삭제된 코드는 현재 코드베이스에 없다)
        added = "\n".join(
            line[1:]
            for f in pr["files"]
            for line in f["patch"].splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        seen: List[str] = []
        for token in IDENTIFIER_RE.findall(added):
            if token not in seen:
                seen.append(token)
            if len(seen) >= MAX_IDENTIFIERS:
                break
        parts.append(" ".join(seen))

    return "\n".join(p for p in parts if p.strip())


def measure(repo: str, pr: Dict[str, Any], variant: str) -> Dict[str, Any]:
    """운영과 동일한 검색·필터·Confidence 경로로 한 건을 잰다 (LLM 생성은 하지 않는다)."""
    from services.confidence import calculate_confidence
    from services.context_filter import filter_contexts
    from services.retrieval import retrieve_with_signals

    query = build_query(pr, variant)
    outcome = retrieve_with_signals(query_text=query, repo_name=repo)
    filtered = filter_contexts(outcome.chunks)
    confidence = calculate_confidence(
        filtered.kept,
        filtered.filter_ratio,
        information_loss_ratio=filtered.information_loss_ratio,
    )

    signals = confidence.signals
    return {
        "repo": repo,
        "number": pr["number"],
        "title": pr["title"],
        "variant": variant,
        "confidence": confidence.score,
        "top_score": signals.get("top_score", 0.0),
        "strong_count": signals.get("strong_evidence_count", 0),
        "evidence_count": signals.get("evidence_count", 0),
        "needs_confirmation": confidence.needs_confirmation,
        "grounding_sufficient": outcome.grounding_sufficient,
        "query_chars": len(query),
    }


def summarize(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("측정된 PR이 없습니다.")
        return

    variants = [v for v in VARIANTS if any(r["variant"] == v for r in rows)]

    print("\n" + "=" * 82)
    print("PR별 Confidence (쿼리 구성 비교)")
    print("=" * 82)
    header = f"{'PR':>6} {'저장소':22s}" + "".join(f"{v:>13s}" for v in variants)
    print(header)
    keys = sorted({(r["repo"], r["number"]) for r in rows}, key=lambda k: (k[0], k[1]))
    for repo, number in keys:
        cells = ""
        for v in variants:
            match = [r for r in rows if r["repo"] == repo and r["number"] == number and r["variant"] == v]
            cells += f"{match[0]['confidence']:>13.4f}" if match else f"{'-':>13s}"
        print(f"{'#' + str(number):>6} {repo.split('/')[-1]:22s}{cells}")

    print("\n" + "=" * 82)
    print("쿼리 구성별 요약")
    print("=" * 82)
    print(f"  {'구성':12s} {'평균':>8s} {'중앙값':>8s} {'≥0.8':>7s} {'≥0.6':>7s} {'평균 top':>9s} {'강한근거':>8s}")
    for v in variants:
        part = sorted(r["confidence"] for r in rows if r["variant"] == v)
        tops = [r["top_score"] for r in rows if r["variant"] == v]
        strong = [r["strong_count"] for r in rows if r["variant"] == v]
        n = len(part)
        print(
            f"  {v:12s} {sum(part) / n:>8.4f} {part[n // 2]:>8.4f} "
            f"{sum(1 for x in part if x >= 0.8):>3}/{n:<3} "
            f"{sum(1 for x in part if x >= 0.6):>3}/{n:<3} "
            f"{sum(tops) / n:>9.4f} {sum(strong) / n:>8.1f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 PR의 Confidence 분포와 쿼리 구성을 비교한다")
    parser.add_argument("--repo", action="append", default=None)
    parser.add_argument("--limit", type=int, default=6, help="저장소당 PR 수")
    parser.add_argument("--variant", action="append", default=None,
                        help=f"쿼리 구성 (기본: 전부). {', '.join(VARIANTS)}")
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY가 설정되지 않았습니다. .env를 확인하세요.")

    variants = args.variant or list(VARIANTS)
    rows: List[Dict[str, Any]] = []

    for repo in (args.repo or list(DEFAULT_REPOS)):
        prs = fetch_prs(repo, args.limit)
        print(f"{repo}: PR {len(prs)}건 × 구성 {len(variants)}개")
        for pr in prs:
            for variant in variants:
                try:
                    rows.append(measure(repo, pr, variant))
                except Exception as e:
                    print(f"  [실패] #{pr['number']} ({variant}): {type(e).__name__}: {e}")

    summarize(rows)

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n원본 저장: {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
