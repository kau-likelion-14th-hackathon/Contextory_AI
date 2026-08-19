"""
scripts/measure_grounding.py — 실제 PR로 "근거 부족" 판정 분포를 측정한다.

왜 필요한가
    SIM_THRESHOLD 미만이면 LLM을 아예 호출하지 않고 "근거 부족" 경로로 빠진다.
    이 임계값을 감으로 정하면 두 방향 모두 나쁘다.
      - 너무 높다 → 멀쩡한 PR도 초안 없이 "사람이 직접 확인하세요"만 나온다
      - 너무 낮다 → 무관한 컨텍스트를 근거라고 붙여 잘못된 초안을 만든다
    실제 저장소의 실제 PR이 어떤 점수 분포를 갖는지 재고 나서 정한다.

동작
    1) GitHub REST API로 병합된 PR 목록을 가져온다 (제목·본문만 — 운영도 이 둘로 쿼리를 만든다)
    2) 운영 파이프라인과 같은 방식으로 영문 검색 쿼리를 만든다 (translate_pr_to_en_query)
    3) retrieve_with_signals 로 검색해 최고 유사도와 임계값 통과 개수를 기록한다
    4) 임계값 후보별로 "몇 %의 PR이 초안 없이 빠지는가"를 표로 출력한다

    LLM 생성 단계는 호출하지 않는다 — 검색 신호만 보면 되는 측정이라 비용이 거의 들지 않는다.

사용 예
    python -m scripts.measure_grounding --limit 10
    python -m scripts.measure_grounding --repo kau-likelion-14th-hackathon/OffCourse_FrontEnd --limit 20
    python -m scripts.measure_grounding --limit 10 --json out.json
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings  # noqa: E402

# project.yml 에 등록된 세 파트 저장소 (인덱싱 완료된 것들)
DEFAULT_REPOS = (
    "kau-likelion-14th-hackathon/Contextory_AI",
    "kau-likelion-14th-hackathon/Contextory_BackEnd",
    "kau-likelion-14th-hackathon/OffCourse_FrontEnd",
)
# 임계값을 이 값들로 바꿨을 때 각각 몇 건이 통과하는지 본다
THRESHOLD_CANDIDATES = (0.35, 0.40, 0.45, 0.50, 0.55, 0.60)


def fetch_merged_prs(repo: str, limit: int) -> List[Dict[str, Any]]:
    """
    병합된 PR의 번호·제목·본문을 가져온다.

    공개 저장소라 인증 없이 GitHub REST API로 읽는다(미인증 60req/h — 저장소당 1회면 충분).
    GITHUB_TOKEN 이 환경에 있으면 붙여 쓴다.
    """
    import httpx

    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = httpx.get(
            f"https://api.github.com/repos/{repo}/pulls",
            # sort=updated 를 주면 PR이 많은 저장소에서 GitHub이 504를 낸다(실측) → 기본 정렬을 쓴다
            params={"state": "closed", "per_page": min(limit * 3, 100)},
            headers=headers, timeout=20.0,
        )
        response.raise_for_status()
    except Exception as e:
        print(f"  [건너뜀] {repo}: PR 목록을 가져오지 못했습니다 ({type(e).__name__}: {e})")
        return []

    merged = [pr for pr in response.json() if pr.get("merged_at")]
    return [
        {"number": pr.get("number"), "title": pr.get("title"), "body": pr.get("body")}
        for pr in merged[:limit]
    ]


def measure_pr(repo: str, pr: Dict[str, Any], repeat: int = 1) -> Dict[str, Any]:
    """
    PR 하나를 운영과 같은 경로로 검색해 근거 신호를 기록한다.

    repeat > 1 이면 같은 PR을 여러 번 측정한다. 검색 쿼리를 LLM이 만들기 때문에
    실행마다 결과가 흔들릴 수 있고, 그 흔들림이 임계값을 넘나들면 같은 PR의
    "근거 부족" 판정이 실행마다 뒤집힌다. 그 폭을 재기 위한 옵션이다.
    """
    from services.retrieval import retrieve_with_signals
    from services.translation_service import translate_pr_to_en_query

    title = pr.get("title") or ""
    body = pr.get("body") or ""

    top_scores: List[float] = []
    queries: List[str] = []
    sufficient: List[bool] = []
    for _ in range(max(1, repeat)):
        query = translate_pr_to_en_query(title, body)
        outcome = retrieve_with_signals(query_text=query, repo_name=repo)
        scores = [float(c.get("similarity_score") or 0.0) for c in outcome.chunks]
        top_scores.append(max(scores) if scores else 0.0)
        queries.append(query)
        sufficient.append(outcome.grounding_sufficient)

    ordered = sorted(top_scores)
    return {
        "repo": repo,
        "number": pr.get("number"),
        "title": title,
        "top_score": ordered[len(ordered) // 2],       # 대푯값은 중앙값
        "top_scores": top_scores,
        "spread": ordered[-1] - ordered[0],            # 실행 간 최대 변동 폭
        "flipped": len(set(sufficient)) > 1,           # 실행마다 판정이 뒤집혔는가
        "queries": queries,
        "grounding_sufficient": sufficient[0],
    }


def summarize(rows: List[Dict[str, Any]]) -> None:
    """측정 결과를 사람이 판단할 수 있는 형태로 출력한다."""
    if not rows:
        print("측정된 PR이 없습니다.")
        return

    print("\n" + "=" * 78)
    print("PR별 최고 유사도")
    print("=" * 78)
    for row in sorted(rows, key=lambda r: r["top_score"]):
        mark = "  " if row["grounding_sufficient"] else "✗ "
        title = row["title"][:44]
        print(f"{mark}{row['top_score']:.4f}  #{row['number']:<5} {row['repo'].split('/')[-1]:22s} {title}")

    tops = sorted(r["top_score"] for r in rows)
    n = len(tops)
    print("\n" + "=" * 78)
    print(f"최고 유사도 분포 (n={n})")
    print("=" * 78)
    print(f"  최소 {tops[0]:.4f} / 중앙값 {tops[n // 2]:.4f} / 최대 {tops[-1]:.4f}")

    print("\n" + "=" * 78)
    print(f"임계값별 '근거 부족'으로 빠지는 비율  (현재 SIM_THRESHOLD={settings.SIM_THRESHOLD})")
    print("=" * 78)
    print(f"  {'임계값':>8}  {'초안 생성':>10}  {'근거 부족':>10}   빠지는 비율")
    for threshold in THRESHOLD_CANDIDATES:
        passed = sum(1 for t in tops if t >= threshold)
        skipped = n - passed
        current = " ← 현재" if abs(threshold - settings.SIM_THRESHOLD) < 1e-9 else ""
        print(f"  {threshold:>8.2f}  {passed:>10}  {skipped:>10}   {skipped / n:>6.1%}{current}")

    # 반복 측정했다면 실행 간 흔들림을 본다 — 판정 안정성 문제는 임계값 조정으로 못 고친다
    repeats = max(len(r.get("top_scores") or []) for r in rows)
    if repeats > 1:
        spreads = sorted(r["spread"] for r in rows)
        flipped = [r for r in rows if r["flipped"]]
        print("\n" + "=" * 78)
        print(f"실행 간 흔들림 ({repeats}회 반복 — 검색 쿼리를 LLM이 만들기 때문)")
        print("=" * 78)
        print(f"  최고 유사도 변동 폭: 중앙값 {spreads[len(spreads) // 2]:.4f} / 최대 {spreads[-1]:.4f}")
        print(f"  판정이 뒤집힌 PR   : {len(flipped)}/{len(rows)} ({len(flipped) / len(rows):.1%})")
        for row in flipped:
            scores = " / ".join(f"{s:.4f}" for s in row["top_scores"])
            print(f"    ✗ #{row['number']:<5} {row['repo'].split('/')[-1]:22s} {scores}")

    # 저장소별로 나눠 본다 — 인덱싱 품질이 파트마다 다를 수 있다
    print("\n" + "=" * 78)
    print("저장소별 평균/중앙값")
    print("=" * 78)
    for repo in sorted({r["repo"] for r in rows}):
        part = sorted(r["top_score"] for r in rows if r["repo"] == repo)
        skipped = sum(1 for t in part if t < settings.SIM_THRESHOLD)
        print(
            f"  {repo.split('/')[-1]:24s} n={len(part):<3} "
            f"중앙값 {part[len(part) // 2]:.4f}  최대 {part[-1]:.4f}  "
            f"근거부족 {skipped}/{len(part)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 PR로 근거 부족 판정 분포를 측정한다")
    parser.add_argument("--repo", action="append", default=None,
                        help="측정할 저장소 (owner/repo). 여러 번 줄 수 있다. 기본은 등록된 3개")
    parser.add_argument("--limit", type=int, default=10, help="저장소당 가져올 병합 PR 수")
    parser.add_argument("--repeat", type=int, default=1,
                        help="같은 PR을 N회 측정해 실행 간 흔들림을 잰다 (기본 1)")
    parser.add_argument("--json", dest="json_path", default=None, help="측정 원본을 JSON으로 저장할 경로")
    args = parser.parse_args()

    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY가 설정되지 않았습니다. .env를 확인하세요.")

    repos = args.repo or list(DEFAULT_REPOS)
    rows: List[Dict[str, Any]] = []

    for repo in repos:
        prs = fetch_merged_prs(repo, args.limit)
        print(f"{repo}: 병합 PR {len(prs)}건 측정")
        for pr in prs:
            try:
                rows.append(measure_pr(repo, pr, repeat=args.repeat))
            except Exception as e:  # 한 건 실패가 전체 측정을 막지 않게 한다
                print(f"  [실패] #{pr.get('number')}: {type(e).__name__}: {e}")

    summarize(rows)

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n원본 저장: {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
