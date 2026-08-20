"""
scripts/create_vector_indexes.py — pgvector ANN 인덱스 생성/점검

왜 필요한가
    services/retrieval.py 의 검색은 `ORDER BY embedding <=> :query_vector`(코사인 거리)이다.
    ANN 인덱스가 없으면 매 질의가 전체 스캔이므로, 적재량이 늘수록 검색 지연이 선형으로 커진다.

인덱스 선택
    hnsw + vector_cosine_ops (pgvector 0.5.0+).
    런타임이 코사인 거리 연산자(<=>)를 쓰므로 opclass도 cosine으로 맞춰야 인덱스가 실제로 쓰인다.

안전장치
    - 기본은 dry-run: 실행할 SQL만 출력한다. 실제 적용은 --apply 필요.
    - CREATE INDEX IF NOT EXISTS 라서 중복 실행이 안전하고, DROP INDEX로 되돌릴 수 있다.
    - 대량 적재는 "적재 후 인덱스 생성"이 더 빠르다. 이미 적재된 상태라면 생성에 시간이 걸린다.

사용 예
    python -m scripts.create_vector_indexes            # 계획만 출력
    python -m scripts.create_vector_indexes --apply    # 실제 생성
    python -m scripts.create_vector_indexes --show     # 현재 인덱스 목록만 확인
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text                     # noqa: E402

from core.config import settings                # noqa: E402
from core.db import engine                      # noqa: E402

# (테이블명, 임베딩 컬럼, 인덱스명)
TARGETS: List[Tuple[str, str, str]] = [
    (settings.CODE_REVIEW_TABLE_NAME, "embedding", "idx_code_review_vectors_embedding_hnsw"),
    (f"data_{settings.REPO_CODE_TABLE_NAME}", "embedding", "idx_data_repo_code_vectors_embedding_hnsw"),
]


def build_index_sql(table: str, column: str, index_name: str) -> str:
    return (
        f"CREATE INDEX IF NOT EXISTS {index_name} "
        f"ON {table} USING hnsw ({column} vector_cosine_ops);"
    )


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            text("select 1 from pg_tables where schemaname='public' and tablename=:t"), {"t": table}
        ).scalar()
    )


def _row_count(conn, table: str) -> int:
    return int(conn.execute(text(f"select count(*) from {table}")).scalar() or 0)


def show_indexes(conn) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for table, _, _ in TARGETS:
        if not _table_exists(conn, table):
            result[table] = ["(테이블 없음)"]
            continue
        rows = conn.execute(
            text("select indexname, indexdef from pg_indexes where tablename=:t order by indexname"), {"t": table}
        ).all()
        result[table] = [f"{name} | {definition}" for name, definition in rows]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="pgvector ANN(hnsw) 인덱스 생성/점검")
    parser.add_argument("--apply", action="store_true", help="실제로 인덱스를 생성한다 (기본은 dry-run)")
    parser.add_argument("--show", action="store_true", help="현재 인덱스 목록만 출력하고 종료")
    args = parser.parse_args()

    with engine.connect() as conn:
        print("=== 현재 인덱스 ===")
        for table, indexes in show_indexes(conn).items():
            print(f"[{table}]")
            for line in indexes:
                print(f"  - {line}")

        if args.show:
            return 0

        print("\n=== 대상 ===")
        plan: List[Tuple[str, str]] = []
        for table, column, index_name in TARGETS:
            if not _table_exists(conn, table):
                print(f"  · {table}: 테이블이 없어 건너뜁니다.")
                continue
            count = _row_count(conn, table)
            sql = build_index_sql(table, column, index_name)
            print(f"  · {table} ({count} rows)\n      {sql}")
            plan.append((table, sql))

        if not plan:
            print("생성할 대상이 없습니다.")
            return 0

        if not args.apply:
            print("\n[dry-run] 실제로 적용하려면 --apply 를 붙여 다시 실행하세요.")
            return 0

        print("\n=== 적용 ===")
        for table, sql in plan:
            conn.execute(text(sql))
            conn.commit()
            print(f"  ✓ {table} 인덱스 생성 완료")

        print("\n=== 적용 후 인덱스 ===")
        for table, indexes in show_indexes(conn).items():
            print(f"[{table}]")
            for line in indexes:
                print(f"  - {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
