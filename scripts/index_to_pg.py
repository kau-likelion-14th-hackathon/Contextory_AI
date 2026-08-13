import os
import time
import pandas as pd
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, Boolean, text
from pgvector.sqlalchemy import Vector
from openai import OpenAI, RateLimitError
import tiktoken

# 1. OpenAI 클라이언트 초기화 (환경변수 OPENAI_API_KEY 사용)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 2. OpenAI 토큰 계산기 (cl100k_base: text-embedding-3-small 용)
tokenizer = tiktoken.get_encoding("cl100k_base")

# 3. SQLAlchemy Base
Base = declarative_base()


# ==========================================
# 1. DB 테이블 ORM 모델 정의
# ==========================================
class CodeReviewVector(Base):
    __tablename__ = "code_review_vectors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    orig_idx = Column(Integer, nullable=True, index=True)  # ✅ 원본 DataFrame 인덱스 체크포인트용
    dataset_source = Column(String(50), nullable=False)
    source_code = Column(Text, nullable=True)
    pr_diff = Column(Text, nullable=True)
    review_comment = Column(Text, nullable=True)
    has_issue = Column(Boolean, default=False)
    # text-embedding-3-small 모델은 1536 차원 벡터
    embedding = Column(Vector(1536))


# ==========================================
# 2. DB 초기화 함수 (pgvector 확장 및 테이블 생성)
# ==========================================
def init_db(engine):
    """
    pgvector 확장을 활성화하고, 테이블을 생성합니다.
    """
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()

    # 정의된 ORM 모델(CodeReviewVector) 기반으로 테이블 자동 생성
    Base.metadata.create_all(bind=engine)


# ==========================================
# 3. 안전한 토큰 자르기 유틸리티 함수 및 NUL 문자 정제
# ==========================================
def sanitize_batch_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    PostgreSQL NUL (0x00) 문자 제거 전처리
    """
    df_sanitized = df.copy()
    text_cols = ['pr_diff', 'review_comment', 'source_code']
    for col in text_cols:
        if col in df_sanitized.columns:
            df_sanitized[col] = (
                df_sanitized[col]
                .astype(str)
                .str.replace('\x00', '', regex=False)
            )
    return df_sanitized


def truncate_by_tokens(text_str, max_tokens=6000):
    """
    OpenAI 8,192 토큰 한도를 넘지 않도록 실제 토큰 수 기준으로 안전하게 자릅니다.
    disallowed_special=() 옵션을 추가하여 <|endoftext|> 등 특수 문자열을 일반 텍스트로 처리합니다.
    """
    s = str(text_str) if pd.notnull(text_str) else ""
    tokens = tokenizer.encode(s, disallowed_special=())
    if len(tokens) > max_tokens:
        return tokenizer.decode(tokens[:max_tokens])
    return s


# ==========================================
# 4. 안전 체크포인트 기반 임베딩 및 DB Bulk Insert 함수
# ==========================================
def embed_and_insert_safe(df: pd.DataFrame, engine, batch_size=10, delay_seconds=2.5):
    init_db(engine)

    # 1. DB에서 이미 처리 완료된 orig_idx 집합 조회
    print("🔍 DB에서 이미 완료된 레코드 목록을 조회 중입니다...")
    with engine.connect() as conn:
        saved_indices = conn.execute(
            text("SELECT orig_idx FROM code_review_vectors WHERE orig_idx IS NOT NULL")
        ).scalars().all()

    saved_set = set(saved_indices)
    print(f"✅ DB에 이미 저장된 레코드: {len(saved_set):,}건")

    # 2. 원본 DataFrame의 index를 orig_idx 컬럼으로 명시적 생성 후 필터링
    df_copy = df.copy()
    df_copy['orig_idx'] = df_copy.index
    df_target = df_copy[~df_copy['orig_idx'].isin(saved_set)].copy()

    total_target = len(df_target)
    print(f"🚀 처리해야 할 남은 레코드: {total_target:,}건")

    if total_target == 0:
        print("🎉 모든 데이터가 이미 DB에 성공적으로 저장되어 있습니다!")
        return

    Session = sessionmaker(bind=engine)

    for i in range(0, total_target, batch_size):
        # DataFrame iloc로 배치를 명확히 슬라이싱 및 NUL(0x00) 문자 정제 적용
        raw_batch = df_target.iloc[i:i + batch_size]
        batch_df = sanitize_batch_df(raw_batch)  # 🧹 [수정] DB/API 전송 전 NUL 제거 전처리

        batch_texts = [
            f"PR Diff:\n{truncate_by_tokens(row['pr_diff'], 1500)}\nReview Comment:\n{truncate_by_tokens(row['review_comment'], 500)}"
            for _, row in batch_df.iterrows()
        ]

        embeddings = None
        for attempt in range(5):
            try:
                response = client.embeddings.create(
                    model="text-embedding-3-small",
                    input=batch_texts
                )
                embeddings = [data.embedding for data in response.data]
                break

            except RateLimitError as e:
                wait_time = (attempt + 1) * 5
                print(f"\n⚠️ Rate Limit(429) 감지 (시도 {attempt+1}/5): {wait_time}초 대기...")
                time.sleep(wait_time)

            except Exception as e:
                print(f"\n❌ API 호출 중 에러 발생 (시도 {attempt+1}/5): {e}")
                time.sleep(3)

        if embeddings is None:
            print(f"❌ [{i}~{i+len(batch_df)}] 구간 임베딩 실패. 다음 실행 때 다시 시도됩니다.")
            continue

        # DB 객체 생성 (batch_df.iterrows()를 사용해 각 행의 orig_idx를 정확히 주입)
        db_objects = []
        for idx, (_, row) in enumerate(batch_df.iterrows()):
            record = CodeReviewVector(
                orig_idx=int(row['orig_idx']),  # ✅ 각 행 고유의 orig_idx 명확히 주입
                dataset_source=row['dataset_source'],
                source_code=row['source_code'],
                pr_diff=row['pr_diff'],
                review_comment=row['review_comment'],
                has_issue=row['has_issue'],
                embedding=embeddings[idx]
            )
            db_objects.append(record)

        session = Session()
        try:
            session.bulk_save_objects(db_objects)
            session.commit()
            
            # 🚀 전체 데이터셋(len(df)) 기준 직관적 출력 계산
            current_total = len(saved_set) + i + len(batch_df)
            total_all = len(df)
            progress_pct = (current_total / total_all) * 100

            print(f"  - [{current_total:,}/{total_all:,}] 건 저장 완료 ({progress_pct:.2f}%)")
        except Exception as e:
            session.rollback()
            print(f"❌ DB 저장 중 에러 발생: {e}")
        finally:
            session.close()

        time.sleep(delay_seconds)

    print("🎉 모든 데이터 저장 완료!")