"""
scripts/load_code_review_subset.py — code_review_vectors 서브셋 적재 CLI

배경
    scripts/index_to_pg.py 는 노트북에서 쓰는 라이브러리라 CLI 진입점이 없고, 원본 JSONL은
    747,555행이라 전량 임베딩은 비용·시간이 크다. 평가 파이프라인을 실제로 돌려보기 위한
    "작은 서브셋"만 적재하는 진입점을 따로 둔다.

재사용
    임베딩·적재 로직은 scripts/index_to_pg.py 의 embed_and_insert_safe() 를 그대로 쓴다.
    (체크포인트 로직 덕분에 중단 후 재실행하면 이미 저장된 orig_idx는 건너뛴다)
    전처리는 utils/adapter.adapt_code_review_gh() 를 재사용한다.

비용 안내
    text-embedding-3-small 기준 1행당 최대 2,000 토큰(diff 1,500 + comment 500)으로 잘라 보낸다.
    500행이면 최대 약 1M 토큰 → 대략 $0.02 수준. --dry-run 으로 먼저 추정치를 확인할 수 있다.

사용 예
    python -m scripts.load_code_review_subset --limit 300 --dry-run
    python -m scripts.load_code_review_subset --limit 300
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import settings  # noqa: E402

# index_to_pg 는 import 시점에 os.getenv("OPENAI_API_KEY")로 클라이언트를 만든다.
# .env는 pydantic-settings가 읽으므로, 그 값을 프로세스 환경변수로 넘겨줘야 import가 성공한다.
if settings.OPENAI_API_KEY and not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY

DEFAULT_INPUT = "data/code_review_gh/raw/code_review_gh_2023.jsonl"
# text-embedding-3-small 단가 (2026-08 기준, 1M 토큰당 USD). 변동 가능하므로 추정치 표시용으로만 쓴다.
EMBED_PRICE_PER_1M_TOKENS = 0.02
MAX_TOKENS_PER_ROW = 2000  # index_to_pg: diff 1,500 + comment 500


def read_records(path: str, limit: int) -> List[Dict[str, Any]]:
    """JSONL 앞에서부터 필요한 만큼만 읽는다 (747k행 전체를 메모리에 올리지 않는다)."""
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
            if len(records) >= limit:
                break
    return records


def to_vector_dataframe(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """원본 레코드 → code_review_vectors 스키마 DataFrame (전처리는 utils/adapter 재사용)"""
    from utils.adapter import adapt_code_review_gh

    raw_df = pd.DataFrame(records)
    adapted = adapt_code_review_gh(raw_df)
    adapted["dataset_source"] = [
        str(records[i].get("dataset", "github_2023")) for i in adapted.index
    ]
    # embed_and_insert_safe는 DataFrame index를 orig_idx로 쓴다(체크포인트 키).
    # 원본 파일에서의 위치를 그대로 유지해야 재실행 시 중복 적재를 피할 수 있다.
    return adapted


def main() -> int:
    parser = argparse.ArgumentParser(description="code_review_vectors 서브셋 적재")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--limit", type=int, default=300, help="원본에서 읽을 행 수 (전처리로 일부 제외됨)")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--delay", type=float, default=0.5, help="배치 간 대기 시간(초)")
    parser.add_argument("--dry-run", action="store_true", help="적재 없이 대상 건수·비용 추정만 출력")
    args = parser.parse_args()

    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY가 설정되지 않았습니다. .env를 확인하세요.")

    records = read_records(args.input, args.limit)
    df = to_vector_dataframe(records)

    max_tokens = len(df) * MAX_TOKENS_PER_ROW
    print(f"원본에서 읽은 행: {len(records):,}")
    print(f"전처리 통과(적재 대상): {len(df):,}  (15자 미만 코멘트·초장문 diff 제외)")
    print(f"임베딩 최대 토큰 추정: {max_tokens:,} → 최대 약 ${max_tokens / 1_000_000 * EMBED_PRICE_PER_1M_TOKENS:.4f}")
    print(f"대상 테이블: {settings.CODE_REVIEW_TABLE_NAME}")

    if args.dry_run:
        print("\n[dry-run] 실제 적재는 --dry-run 없이 실행하세요.")
        return 0

    if df.empty:
        print("적재할 행이 없습니다.")
        return 0

    from core.db import engine
    from scripts.index_to_pg import embed_and_insert_safe

    embed_and_insert_safe(df, engine, batch_size=args.batch_size, delay_seconds=args.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
