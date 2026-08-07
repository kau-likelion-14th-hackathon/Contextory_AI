import time
import os
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
    print("✅ pgvector 확장 및 DB 테이블(code_review_vectors) 준비 완료!")


# ==========================================
# 3. 안전한 토큰 자르기 유틸리티 함수
# ==========================================
def truncate_by_tokens(text_str, max_tokens=6000):
    """
    OpenAI 8,192 토큰 한도를 넘지 않도록 실제 토큰 수 기준으로 안전하게 자릅니다.
    disallowed_special=() 옵션을 추가하여 <|endoftext|> 등 특수 문자열을 일반 텍스트로 처리합니다.
    """
    s = str(text_str) if pd.notnull(text_str) else ""
    tokens = tokenizer.encode(s, disallowed_special=())  # ✅ 해결 완료!
    if len(tokens) > max_tokens:
        return tokenizer.decode(tokens[:max_tokens])
    return s


# ==========================================
# 4. 임베딩 및 DB Bulk Insert 함수
# ==========================================
def embed_and_insert(df: pd.DataFrame, engine, batch_size=10, delay_seconds=2.5):
    """
    OpenAI text-embedding-3-small 모델을 이용해 임베딩을 생성하고 pgvector DB에 저장합니다.
    - batch_size=10, delay_seconds=2.5: 429 Rate Limit (TPM 40,000) 방지
    - truncate_by_tokens: 8,192 토큰 초과 (400 에러) 방지
    """
    init_db(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # PR Diff는 최대 5,000 토큰, Review Comment는 최대 1,500 토큰으로 자름 (합계 6,500 토큰 내외로 안전)
    texts_to_embed = [
        f"PR Diff:\n{truncate_by_tokens(row['pr_diff'], 5000)}\nReview Comment:\n{truncate_by_tokens(row['review_comment'], 1500)}"
        for _, row in df.iterrows()
    ]

    total_records = len(df)
    print(f"🚀 총 {total_records:,}건 데이터 임베딩 및 DB 저장 시작 (Batch Size: {batch_size}, Interval: {delay_seconds}s)...")

    for i in range(0, total_records, batch_size):
        batch_texts = texts_to_embed[i:i + batch_size]
        batch_df = df.iloc[i:i + batch_size]
        
        embeddings = None
        for attempt in range(5):
            try:
                response = client.embeddings.create(
                    model="text-embedding-3-small",
                    input=batch_texts
                )
                embeddings = [data.embedding for data in response.data]
                break  # 성공 시 재시도 루프 탈출
                
            except RateLimitError as e:
                wait_time = (attempt + 1) * 5
                print(f"\n⚠️ Rate Limit(429) 감지 (시도 {attempt+1}/5): {wait_time}초 대기...")
                time.sleep(wait_time)
                
            except Exception as e:
                print(f"\n❌ API 호출 중 에러 발생 (시도 {attempt+1}/5): {e}")
                time.sleep(3)
        
        if embeddings is None:
            print(f"❌ [{i}~{i+batch_size}] 구간 임베딩 실패. 해당 배치는 건너뜁니다.")
            continue

        # DB 객체 생성 및 Bulk Insert
        records = []
        for idx, (_, row) in enumerate(batch_df.iterrows()):
            record = CodeReviewVector(
                dataset_source=row['dataset_source'],
                source_code=row['source_code'],
                pr_diff=row['pr_diff'],
                review_comment=row['review_comment'],
                has_issue=row['has_issue'],
                embedding=embeddings[idx]
            )
            records.append(record)
        
        session.bulk_save_objects(records)
        session.commit()
        print(f"  - [{min(i + batch_size, total_records):,}/{total_records:,}] 건 저장 완료")

        time.sleep(delay_seconds)

    session.close()
    print("🎉 모든 데이터 저장 완료!")