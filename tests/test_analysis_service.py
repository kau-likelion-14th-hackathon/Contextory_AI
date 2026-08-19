"""
PR diff가 OpenAI TPM 한도를 넘겨 매번 동일하게 429로 실패하던 인시던트(REFACTOR_BRIEF.md)에 대한
회귀 테스트. build_diff_content()의 저가치 파일 제외/파일별 patch 상한과,
run_pipeline()이 최종 프롬프트를 LLM_MAX_PROMPT_TOKENS 예산에 맞추는 트리밍/실패 처리를 검증한다.
"""

import pytest

from core.config import settings
from models.schemas import AsyncAnalysisRequest, PullRequestFile, PullRequestInfo
from services.analysis_service import (
    PromptTooLargeError,
    analyze_pr_for_callback,
    build_diff_content,
    run_pipeline,
)
from services.context_filter import FilterOutcome
from services.prompt_builder import PRInput
from services.retrieval import RetrievalOutcome
from services.token_utils import count_tokens


def _file(path, change_type="MODIFIED", patch="@@ -1,1 +1,1 @@\n-old\n+new") -> PullRequestFile:
    return PullRequestFile(filePath=path, changeType=change_type, patch=patch)


def test_build_diff_content_includes_normal_files():
    files = [_file("src/main.py"), _file("src/util.py")]

    diff_content = build_diff_content(files)

    assert "### src/main.py (MODIFIED)" in diff_content
    assert "### src/util.py (MODIFIED)" in diff_content


def test_build_diff_content_excludes_low_value_files_and_caps_large_patch(monkeypatch):
    monkeypatch.setattr(settings, "MAX_PATCH_CHARS_PER_FILE", 100)

    huge_patch = "@@ -1,1 +1,1 @@\n" + ("+line\n" * 100)  # well over 100 chars
    files = [
        _file("package-lock.json", patch="@@ -1,1 +1,1 @@\n-a\n+b"),  # lockfile
        _file("dist/bundle.min.js", patch="@@ -1,1 +1,1 @@\n-a\n+b"),  # generated/vendored
        _file("src/renamed.py", change_type="RENAMED", patch="rename from a.py\nrename to renamed.py"),  # rename-only
        _file("src/big.py", patch=huge_patch),
    ]

    diff_content = build_diff_content(files)

    assert "package-lock.json" not in diff_content
    assert "bundle.min.js" not in diff_content
    assert "renamed.py" not in diff_content
    assert "### src/big.py (MODIFIED)" in diff_content
    assert "... (patch truncated)" in diff_content
    assert "truncated, 3 files omitted" in diff_content


def _stub_pipeline_fns(repo_contexts=None):
    """검색/필터/번역을 DB·OpenAI 없이 고정 결과로 대체하는 run_pipeline 주입 함수 묶음.
    근거는 항상 충분한 것으로 취급하고, 넘겨받은 chunk를 필터 없이 그대로 통과시킨다."""
    chunks = repo_contexts or []

    def retrieve_fn(query_text, repo_name, top_k):
        return RetrievalOutcome(chunks=chunks, grounding_sufficient=True)

    def filter_fn(chunks_in):
        return FilterOutcome(
            kept=list(chunks_in), removed=[], filter_ratio=0.0, top1_preserved=True, mode="off",
        )

    def translate_fn(title, description):
        return title

    def generate_fn(system_prompt, user_prompt):
        return '{"summary": "ok"}'

    return retrieve_fn, filter_fn, translate_fn, generate_fn


def _prompt_token_total(ctx) -> int:
    return count_tokens(ctx.system_prompt, settings.LLM_MODEL) + count_tokens(ctx.prompt, settings.LLM_MODEL)


def test_run_pipeline_prompt_unchanged_when_within_budget():
    retrieve_fn, filter_fn, translate_fn, generate_fn = _stub_pipeline_fns()
    pr = PRInput(title="Add login endpoint", body="", diff="small diff content")

    ctx = run_pipeline(
        pr=pr,
        repo_name="org/repo",
        retrieve_fn=retrieve_fn,
        filter_fn=filter_fn,
        translate_fn=translate_fn,
        generate_fn=generate_fn,
    )

    assert "small diff content" in ctx.prompt
    assert _prompt_token_total(ctx) <= settings.LLM_MAX_PROMPT_TOKENS


def test_run_pipeline_trims_oversized_diff(monkeypatch):
    # Large enough to fit the (sizeable, Korean-instruction-heavy) system + user prompt overhead
    # on its own, but well short of the ~14k-token untrimmed prompt below.
    monkeypatch.setattr(settings, "LLM_MAX_PROMPT_TOKENS", 3000)
    retrieve_fn, filter_fn, translate_fn, generate_fn = _stub_pipeline_fns()

    # Comfortably larger than a 200-token budget once wrapped in the prompt template.
    oversized_diff = "changed_line = 1\n" * 2000
    pr = PRInput(title="Big refactor", body="", diff=oversized_diff)

    ctx = run_pipeline(
        pr=pr,
        repo_name="org/repo",
        retrieve_fn=retrieve_fn,
        filter_fn=filter_fn,
        translate_fn=translate_fn,
        generate_fn=generate_fn,
    )

    assert "diff truncated to fit token budget" in ctx.prompt
    assert _prompt_token_total(ctx) <= settings.LLM_MAX_PROMPT_TOKENS


def test_run_pipeline_raises_when_still_too_large_after_trim(monkeypatch):
    """Context/overhead alone already exceeds an unreasonably tight budget -> fail fast,
    with no diff left to trim."""
    huge_repo_contexts = [
        {
            "chunk_id": f"repo-f{i}", "file_path": f"f{i}.py", "source": f"f{i}.py",
            "similarity_score": 0.9, "source_code": "x" * 2000,
        }
        for i in range(20)
    ]
    retrieve_fn, filter_fn, translate_fn, generate_fn = _stub_pipeline_fns(repo_contexts=huge_repo_contexts)
    monkeypatch.setattr(settings, "LLM_MAX_PROMPT_TOKENS", 5)
    pr = PRInput(title="Big refactor", body="", diff="some diff")

    with pytest.raises(PromptTooLargeError) as exc_info:
        run_pipeline(
            pr=pr,
            repo_name="org/repo",
            retrieve_fn=retrieve_fn,
            filter_fn=filter_fn,
            translate_fn=translate_fn,
            generate_fn=generate_fn,
        )

    assert "PR diff too large to analyze" in str(exc_info.value)
    assert "limit 5" in str(exc_info.value)


def test_analyze_pr_for_callback_fails_clean_without_calling_openai(monkeypatch):
    """When the request can't be brought under budget, OpenAI must never be called —
    the 429 should never happen in the first place."""
    monkeypatch.setattr(settings, "LLM_MAX_PROMPT_TOKENS", 5)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("OpenAI should not be called when the prompt is too large")

    retrieve_fn, filter_fn, translate_fn, _ = _stub_pipeline_fns()

    request = AsyncAnalysisRequest(
        analysisId=1,
        projectId=1,
        repositoryId=1,
        repositoryFullName="org/repo",
        pullRequest=PullRequestInfo(
            githubPrId=1,
            prNumber=1,
            title="Big refactor",
            body="",
            headSha="deadbeef",
            sourceBranch="feature/x",
            targetBranch="main",
            files=[_file("src/big.py")],
        ),
        language="en",
        callbackUrl="https://example.com/callback",
    )

    with pytest.raises(PromptTooLargeError):
        analyze_pr_for_callback(
            request,
            retrieve_fn=retrieve_fn,
            filter_fn=filter_fn,
            translate_fn=translate_fn,
            generate_fn=_fail_if_called,
        )
