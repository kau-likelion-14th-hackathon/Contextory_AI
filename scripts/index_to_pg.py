import time
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import pandas as pd

def embed_and_insert(df: pd.DataFrame, engine, batch_size=30, delay_seconds=1.5):
    init_db(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    texts_to_embed = (
        "PR Diff:\n" + df['pr_diff'].fillna('').astype(str) + 
        "\nReview Comment:\n" + df['review_comment'].fillna('').astype(str)
    ).tolist()

    total_records = len(df)
    print(f"🚀 총 {total_records:,}건 데이터 임베딩 및 DB 저장 시작 (Batch Size: {batch_size})...")

    for i in range(0, total_records, batch_size):
        batch_texts = texts_to_embed[i:i + batch_size]
        batch_df = df.iloc[i:i + batch_size]
        
        # Rate Limit 대비 재시도 루프 (최대 5회)
        embeddings = None
        for attempt in range(5):
            try:
                response = client.embeddings.create(
                    model="text-embedding-3-small",
                    input=batch_texts
                )
                embeddings = [data.embedding for data in response.data]
                break  # 성공 시 루프 탈출
            except Exception as e:
                wait_time = (attempt + 1) * 20  # 20초, 40초, 60초... 지수 증가
                print(f"\n⚠️ API 호출 중 에러 발생 (시도 {attempt+1}/5): {e}")
                print(f"⏳ Rate Limit 리셋을 위해 {wait_time}초 동안 대기합니다...")
                time.sleep(wait_time)
        
        if embeddings is None:
            print(f"❌ [{i}~{i+batch_size}] 구간 저장 실패. 다음 배치로 넘어갑니다.")
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