#!/usr/bin/env python3
"""평가 기준 데이터셋의 각 PR 을 RAG 로 분석해 예측을 남긴다.

과제는 PR-분석형이다. 입력은 PR 제목/본문/파일 경로/diff 이고, 출력은
`prompts/korean_code_review_output_schema.json` 형태의 구조화 리뷰 JSON 이다.

절차
    1. pgvector 에서 같은 범위(프로젝트/저장소)의 관련 기록을 top-k 로 검색한다.
    2. 검색 결과를 "참고 문맥"으로 주입해 LLM 을 호출한다.
    3. 결과를 정답과 나란히 비교할 수 있는 JSONL 로 저장한다.

환각 방지
    - 정답의 근거인 `review_comment_*` 와 `expected` 는 프롬프트에 넣지 않는다.
    - 검색 문맥은 참고용일 뿐, diff/PR 본문에 없는 사실을 확정하는 근거로 쓰지
      않는다는 규칙을 프롬프트에 명시한다.

사용 예
    # smoke (20건)
    python scripts/run_evaluation.py --eval-data ./data/test_samples.json \\
        --output ./results/predictions.jsonl --top-k 5 --max-samples 20

    # 검색 없이 LLM 만 (pgvector 미가동 환경)
    python scripts/run_evaluation.py --eval-data ./data/test_samples.json \\
        --output ./results/predictions.jsonl --no-retrieval
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llamaindex.vector_store import (  # noqa: E402
    DEFAULT_TABLE_NAME,
    describe_target,
    get_vector_store,
    healthcheck,
)

DEFAULT_PROMPT_FILE = "prompts/korean_code_review_v3_contextory.md"
SYSTEM_PROMPT_BLOCK_RE = re.compile(r"## System Prompt\s*```text\n(.*?)\n```", re.DOTALL)

# 프롬프트 버전. 프롬프트 파일이 바뀌면 이 값도 함께 올린다.
PROMPT_VERSION = "v3_contextory"

# 모든 샘플에 같은 조건을 주어야 비교가 성립한다.
FIXED_CONDITIONS: dict[str, Any] = {
    "temperature": 0.0,
    "top_p": 1.0,
    "max_output_tokens": 4096,
    "response_format": "json_object",
}

# 실험 조건과 같은 프로젝트 정보를 주입한다.
PROJECT_INFO: dict[str, str] = {
    "이름": "Contextory",
    "한 줄 설명": "GitHub PR 변경 사항을 팀 전체가 이해할 수 있게 정리해 주는 서비스입니다.",
    "목적": "코드 변경의 목적과 영향을 역할별로 전달해, 코드를 직접 읽지 않는 팀원도 맥락을 파악하게 합니다.",
    "주요 기능": "PR 분석, 변경 요약과 전후 비교, 역할별 영향 정리, 후속 작업 제안, RAG 기반 관련 문맥 검색",
    "역할": "AI 분석 파이프라인 (FastAPI, LlamaIndex, PostgreSQL(pgvector))",
    "기본 언어": "한국어",
}

RETRIEVAL_RULE = """[검색 문맥 사용 규칙]
아래 '참고 문맥'은 프로젝트의 과거 기록에서 유사도로 찾아온 것입니다. 참고만 하고 다음을 지킵니다.
- diff 나 PR 본문에 없는 사실을 참고 문맥만 근거로 확정하지 않습니다.
- 참고 문맥에서 본 코드/파일을 이번 변경에 있는 것처럼 서술하지 않습니다.
- 참고 문맥이 판단에 필요하지만 확정할 수 없으면 needs_confirmation 에 적습니다.
- issues 의 evidence 는 반드시 이번 diff 에서 그대로 인용합니다."""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------


def load_system_prompt(path: Path) -> str:
    """마크다운에서 ```text 로 감싼 System Prompt 본문만 뽑아낸다."""
    if not path.exists():
        raise SystemExit(f"프롬프트 파일을 찾을 수 없습니다: {path}")
    text = path.read_text(encoding="utf-8")
    match = SYSTEM_PROMPT_BLOCK_RE.search(text)
    if not match:
        raise SystemExit(f"{path} 에서 System Prompt 블록을 찾지 못했습니다.")
    return match.group(1).strip()


def build_user_message(sample: dict[str, Any], contexts: list[dict[str, Any]]) -> str:
    """모델 입력을 만든다. 정답 근거(review_comment_*, expected)는 넣지 않는다."""
    inp = sample["input"]
    language = sample["classification"].get("programming_language", "(없음)")
    project = "\n".join(f"- {k}: {v}" for k, v in PROJECT_INFO.items())

    if contexts:
        blocks = []
        for i, ctx in enumerate(contexts, start=1):
            meta = ctx.get("metadata") or {}
            label = meta.get("file_path") or meta.get("repo_name") or ctx.get("record_id") or "?"
            blocks.append(f"--- 참고 {i} (출처: {label}, 유사도 {ctx.get('score')}) ---\n{ctx.get('text', '')}")
        context_block = "\n\n".join(blocks)
    else:
        context_block = "(검색된 관련 기록 없음)"

    return (
        f"## 프로젝트 정보\n{project}\n\n"
        f"## PR 제목\n{inp.get('pr_title_original') or '(없음)'}\n\n"
        f"## PR 설명\n{inp.get('pr_description_original') or '(없음)'}\n\n"
        f"## 변경 파일 경로\n{inp.get('file_path', '(없음)')}\n\n"
        f"## 프로그래밍 언어\n{language}\n\n"
        f"## 코드 Diff\n{inp.get('diff', '')}\n\n"
        f"## 관련 리뷰 댓글\n(없음)\n\n"
        f"## 추가 문맥\n{inp.get('additional_context') or '(없음)'}\n\n"
        f"{RETRIEVAL_RULE}\n\n"
        f"## 참고 문맥 (pgvector 검색 결과)\n{context_block}\n"
    )


def build_query(sample: dict[str, Any], max_chars: int) -> str:
    """검색 질의를 만든다. diff 앞부분과 제목/경로를 함께 쓴다."""
    inp = sample["input"]
    parts = [
        inp.get("pr_title_original") or "",
        inp.get("file_path") or "",
        sample["classification"].get("programming_language") or "",
        (inp.get("diff") or "")[:max_chars],
    ]
    return "\n".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# 검색
# ---------------------------------------------------------------------------


@dataclass
class Retriever:
    """pgvector 검색기. 접속이 안 되면 비활성 상태로 남는다."""

    vector_store: Any = None
    embed_model: Any = None
    scope_key: str | None = None
    enabled: bool = False
    reason: str = ""

    def retrieve(self, query: str, top_k: int, scope_value: str | None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        from llama_index.core.vector_stores.types import (
            MetadataFilter,
            MetadataFilters,
            VectorStoreQuery,
        )

        embedding = self.embed_model.get_text_embedding(query)
        filters = None
        if self.scope_key and scope_value:
            filters = MetadataFilters(
                filters=[MetadataFilter(key=self.scope_key, value=scope_value)]
            )
        result = self.vector_store.query(
            VectorStoreQuery(
                query_embedding=embedding, similarity_top_k=top_k, filters=filters
            )
        )

        contexts: list[dict[str, Any]] = []
        nodes = result.nodes or []
        similarities = result.similarities or [None] * len(nodes)
        for node, score in zip(nodes, similarities):
            meta = dict(node.metadata or {})
            contexts.append(
                {
                    "record_id": meta.get("doc_id") or meta.get("source_file") or node.node_id,
                    "chunk_id": node.node_id,
                    "score": round(float(score), 6) if score is not None else None,
                    "metadata": meta,
                    "text": node.get_content(),
                }
            )
        return contexts


def build_retriever(args: argparse.Namespace) -> Retriever:
    """검색기를 준비한다. 준비되지 않으면 사유를 담아 비활성으로 돌려준다."""
    if args.no_retrieval:
        return Retriever(reason="--no-retrieval 이 지정되어 검색을 하지 않습니다.")
    if not os.environ.get("OPENAI_API_KEY"):
        return Retriever(reason="OPENAI_API_KEY 가 없어 질의 임베딩을 만들 수 없습니다.")

    ok, note = healthcheck()
    if not ok:
        return Retriever(reason=f"pgvector 에 접속하지 못했습니다: {note}")

    try:
        from llama_index.embeddings.openai import OpenAIEmbedding
    except ImportError:
        return Retriever(reason="llama-index-embeddings-openai 가 설치되어 있지 않습니다.")

    target = describe_target(args.table_name)
    store = get_vector_store(table_name=args.table_name, embed_dim=int(target["embed_dim"]))
    return Retriever(
        vector_store=store,
        embed_model=OpenAIEmbedding(model=args.embedding_model),
        scope_key=args.scope_key,
        enabled=True,
        reason=note,
    )


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


def call_llm(
    client: Any, model: str, system_prompt: str, user_message: str, max_retries: int
) -> tuple[str | None, str | None]:
    """모델을 호출해 (본문, 오류) 를 돌려준다. 키는 오류 메시지에 담지 않는다."""
    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=FIXED_CONDITIONS["temperature"],
                top_p=FIXED_CONDITIONS["top_p"],
                max_tokens=FIXED_CONDITIONS["max_output_tokens"],
                response_format={"type": FIXED_CONDITIONS["response_format"]},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content, None
        except Exception as exc:  # SDK 예외 계층에 의존하지 않는다
            last_error = type(exc).__name__
            if attempt < max_retries:
                time.sleep(2.0 * (2**attempt))
    return None, last_error


def normalize_prediction(parsed: dict[str, Any]) -> dict[str, Any]:
    """예측을 평가가 기대하는 형태로 맞춘다. 없는 필드는 빈 값으로 채운다."""
    issues = []
    for issue in parsed.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        issues.append(
            {
                "category": issue.get("category", ""),
                "subtype": issue.get("subtype", ""),
                "severity": issue.get("severity", ""),
                "file_path": issue.get("file_path", ""),
                "line_reference": issue.get("line_reference", ""),
                "evidence": issue.get("evidence", ""),
                "problem_ko": issue.get("problem_ko", issue.get("problem", "")),
                "impact_ko": issue.get("impact_ko", issue.get("impact", "")),
                "recommendation_ko": issue.get(
                    "recommendation_ko", issue.get("recommendation", "")
                ),
                "confidence": issue.get("confidence", 0.0),
            }
        )

    return {
        "change_summary_ko": parsed.get("change_summary_ko", ""),
        "change_purpose_ko": parsed.get("change_purpose_ko", ""),
        "change_reason_ko": parsed.get("change_reason_ko", ""),
        "before_ko": parsed.get("before_ko", ""),
        "after_ko": parsed.get("after_ko", ""),
        "review_result": parsed.get("review_result", ""),
        "issues": issues,
        "affected_roles": parsed.get("affected_roles") or [],
        "role_impacts": parsed.get("role_impacts") or [],
        "follow_up_tasks": parsed.get("follow_up_tasks") or [],
        "needs_confirmation": parsed.get("needs_confirmation") or [],
    }


EMPTY_PREDICTION: dict[str, Any] = {
    "change_summary_ko": "",
    "change_purpose_ko": "",
    "change_reason_ko": "",
    "before_ko": "",
    "after_ko": "",
    "review_result": "",
    "issues": [],
    "affected_roles": [],
    "role_impacts": [],
    "follow_up_tasks": [],
    "needs_confirmation": [],
}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    eval_path = Path(args.eval_data).expanduser()
    if not eval_path.exists():
        print(f"평가 데이터가 없습니다: {eval_path}", file=sys.stderr)
        return 1

    dataset = json.loads(eval_path.read_text(encoding="utf-8"))
    samples = dataset.get("samples") or []
    if args.max_samples:
        samples = samples[: args.max_samples]
    if not samples:
        print("평가할 샘플이 없습니다.", file=sys.stderr)
        return 1

    system_prompt = load_system_prompt(Path(args.prompt_file))
    retriever = build_retriever(args)

    client = None
    llm_note = ""
    if args.dry_run:
        llm_note = "dry-run 이라 LLM 을 호출하지 않습니다."
    elif not os.environ.get("OPENAI_API_KEY"):
        llm_note = "OPENAI_API_KEY 가 없어 LLM 을 호출할 수 없습니다."
    else:
        try:
            from openai import OpenAI

            client = OpenAI()
            llm_note = f"OpenAI {args.llm_model} 을 호출합니다."
        except ImportError:
            llm_note = "openai 패키지가 설치되어 있지 않습니다."

    target = describe_target(args.table_name)
    print("=" * 70)
    print(f"평가 데이터   : {eval_path} (샘플 {len(samples)}건)")
    print(f"프롬프트      : {args.prompt_file} ({PROMPT_VERSION})")
    print(f"LLM           : {args.llm_model} — {llm_note}")
    print(f"임베딩        : {args.embedding_model}")
    print(f"벡터 저장소   : pgvector {target['target']} / 테이블 {target['table_name']}")
    print(f"검색          : top_k={args.top_k} / {'사용' if retriever.enabled else '미사용'} — {retriever.reason}")
    print("=" * 70)

    if client is None and not args.dry_run:
        print(
            "LLM 을 호출할 수 없어 중단합니다. 입력 구성만 확인하려면 --dry-run 을 쓰세요.",
            file=sys.stderr,
        )
        return 2

    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    successful = 0
    failed = 0
    empty = 0
    started = time.monotonic()

    with out_path.open("w", encoding="utf-8") as handle:
        for pos, sample in enumerate(samples, start=1):
            sample_id = sample.get("id", f"sample-{pos}")
            scope_value = (sample.get("source") or {}).get(args.scope_source_field)
            query = build_query(sample, args.query_max_chars)

            t0 = time.perf_counter()
            try:
                contexts = retriever.retrieve(query, args.top_k, scope_value)
            except Exception as exc:  # noqa: BLE001 - 검색 실패가 평가를 멈추면 안 된다
                contexts = []
                print(f"  [{sample_id}] 검색 실패: {type(exc).__name__}", file=sys.stderr)

            user_message = build_user_message(sample, contexts)

            record: dict[str, Any] = {
                "id": sample_id,
                "predicted": dict(EMPTY_PREDICTION),
                "retrieved_contexts": [
                    {
                        "record_id": c.get("record_id"),
                        "chunk_id": c.get("chunk_id"),
                        "score": c.get("score"),
                    }
                    for c in contexts
                ],
                "latency_ms": 0,
                "error": None,
                "meta": {
                    "prompt_version": PROMPT_VERSION,
                    "llm_model": args.llm_model,
                    "embedding_model": args.embedding_model,
                    "top_k": args.top_k,
                    "retrieval_enabled": retriever.enabled,
                    "input_chars": len(user_message),
                    "requested_at": now_utc(),
                },
            }

            if args.dry_run:
                record["error"] = "dry_run"
                record["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"  [{pos}/{len(samples)}] {sample_id}: dry_run")
                continue

            raw, error = call_llm(
                client, args.llm_model, system_prompt, user_message, args.max_retries
            )
            record["latency_ms"] = int((time.perf_counter() - t0) * 1000)

            if raw is None:
                record["error"] = f"llm_call_failed: {error}"
                failed += 1
                status = "실패"
            else:
                try:
                    parsed = json.loads(raw)
                    record["predicted"] = normalize_prediction(parsed)
                    successful += 1
                    if not record["predicted"]["change_summary_ko"] and not record["predicted"]["issues"]:
                        empty += 1
                        status = "빈답"
                    else:
                        status = "성공"
                except json.JSONDecodeError as exc:
                    record["error"] = f"json_decode_error: {exc}"
                    failed += 1
                    status = "JSON 파싱 실패"

            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(
                f"  [{pos}/{len(samples)}] {sample_id}: {status} "
                f"({record['latency_ms']}ms, 검색 {len(contexts)}건)"
            )

    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "generated_at": now_utc(),
                "eval_data": str(eval_path),
                "dataset_version": dataset.get("dataset_version"),
                "samples": len(samples),
                "prompt_file": args.prompt_file,
                "prompt_version": PROMPT_VERSION,
                "llm_model": args.llm_model,
                "embedding_model": args.embedding_model,
                "vector_store": "pgvector",
                "table_name": target["table_name"],
                "top_k": args.top_k,
                "retrieval_enabled": retriever.enabled,
                "retrieval_note": retriever.reason,
                "llm_note": llm_note,
                "fixed_conditions": FIXED_CONDITIONS,
                "dry_run": args.dry_run,
                "successful": successful,
                "failed": failed,
                "empty_answers": empty,
                "elapsed_seconds": round(time.monotonic() - started, 2),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print("=" * 70)
    print(f"성공 {successful} / 실패 {failed} / 빈답 {empty}")
    print(f"예측 저장: {out_path}")
    print(f"실행 정보: {meta_path}")
    print("=" * 70)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="평가 데이터셋으로 RAG PR-분석을 실행해 예측을 남긴다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--eval-data", default="./data/test_samples.json", help="평가 정답 데이터")
    parser.add_argument("--output", default="./results/predictions.jsonl", help="예측 저장 경로")
    parser.add_argument("--top-k", type=int, default=5, help="검색할 문서 수")
    parser.add_argument("--max-samples", type=int, default=0, help="앞에서 N건만 실행 (0=전체)")
    parser.add_argument(
        "--prompt-file", default=DEFAULT_PROMPT_FILE, help="System Prompt 마크다운 경로"
    )
    parser.add_argument(
        "--llm-model", default=os.getenv("LLM_MODEL", "gpt-4o"), help="분석에 쓸 LLM"
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        help="질의 임베딩 모델",
    )
    parser.add_argument("--table-name", default=DEFAULT_TABLE_NAME, help="pgvector 테이블 이름")
    parser.add_argument(
        "--scope-key",
        default="repo_name",
        help="검색 범위를 한정할 메타데이터 키 (빈 값이면 전체 검색)",
    )
    parser.add_argument(
        "--scope-source-field",
        default="repository",
        help="샘플의 source 블록에서 범위 값으로 쓸 필드",
    )
    parser.add_argument(
        "--query-max-chars", type=int, default=1500, help="검색 질의에 쓸 diff 최대 길이"
    )
    parser.add_argument("--max-retries", type=int, default=2, help="LLM 호출 재시도 횟수")
    parser.add_argument(
        "--no-retrieval", action="store_true", help="검색 없이 LLM 만 호출한다"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="LLM 을 호출하지 않고 입력 구성만 확인"
    )
    args = parser.parse_args(argv)
    if not args.scope_key:
        args.scope_key = None
    return args


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
