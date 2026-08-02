import json
import os
import sys

DATASET_ID, SPLIT, N = "ronantakizawa/github-codereview", "train", 3
OUT_DIR = "data/raw"
# 본 수집 원본(hf_github_codereview_raw.jsonl)을 덮어쓰지 않도록 별도 파일명을 씁니다.
OUT_NAME = "hf_demo_sample.jsonl"


def main():
    from datasets import load_dataset

    print(f"[1] connecting: load_dataset('{DATASET_ID}', split='{SPLIT}', streaming=True)")
    # 공개 데이터셋이라 토큰이 없어도 익명으로 동작합니다. 토큰 값을 출력하지 않습니다.
    ds = load_dataset(
        DATASET_ID, split=SPLIT, streaming=True, token=os.environ.get("HF_TOKEN")
    )
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    print(f"[2] streaming first usable {N} rows ...")
    for row in ds:
        if not (row.get("diff_context") or row.get("after_code")):
            continue
        if not row.get("reviewer_comment"):
            continue
        rows.append(row)
        if len(rows) >= N:
            break
    print(f"[3] fetched {len(rows)} rows\n")
    for i, r in enumerate(rows, 1):
        print(f"--- sample {i} ---")
        print("repo        :", r.get("repo_name"))
        print("language    :", r.get("repo_language") or r.get("language"))
        print("pr_title    :", (r.get("pr_title") or "")[:80])
        print("comment_type:", r.get("comment_type"))
        print("review      :", (r.get("reviewer_comment") or "")[:120].replace("\n", " "))
        print("diff head   :", (r.get("diff_context") or "")[:120].replace("\n", " "))
        print()
    out = os.path.join(OUT_DIR, OUT_NAME)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[4] saved -> {out}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", type(e).__name__, str(e)[:300], file=sys.stderr)
        sys.exit(1)
