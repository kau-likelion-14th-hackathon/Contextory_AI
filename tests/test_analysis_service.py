"""
PR diff가 OpenAI TPM 한도를 넘겨 매번 동일하게 429로 실패하던 인시던트(REFACTOR_BRIEF.md)에 대한
회귀 테스트. build_diff_content()의 저가치 파일 제외/파일별 patch 상한과,
_gather_grounded_context()의 토큰 예산 트리밍/실패 처리를 검증한다.
"""

import pytest

from core.config import settings
from models.schemas import AsyncAnalysisRequest, PullRequestFile, PullRequestInfo
from services import analysis_service
from services.analysis_service import (
    PromptTooLargeError,
    analyze_pr_for_callback,
    build_diff_content,
)
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


def _patch_retrieval(monkeypatch, repo_contexts=None):
    monkeypatch.setattr(analysis_service, "translate_pr_to_en_query", lambda title, desc: title)
    monkeypatch.setattr(analysis_service, "retrieve_contexts", lambda query_text, top_k: [])
    monkeypatch.setattr(
        analysis_service, "retrieve_repo_contexts",
        lambda query_text, repo_name, top_k: repo_contexts or [],
    )
    monkeypatch.setattr(analysis_service, "filter_contexts", lambda contexts, sim_threshold: ([], 0.0))


def test_gather_grounded_context_returns_prompt_unchanged_when_within_budget(monkeypatch):
    _patch_retrieval(monkeypatch)

    prompt, _contexts, _ratio = analysis_service._gather_grounded_context(
        "Add login endpoint", "", "small diff content", "org/repo"
    )

    assert "small diff content" in prompt
    assert count_tokens(prompt, settings.LLM_MODEL) <= settings.LLM_MAX_PROMPT_TOKENS


def test_gather_grounded_context_trims_oversized_diff(monkeypatch):
    _patch_retrieval(monkeypatch)
    monkeypatch.setattr(settings, "LLM_MAX_PROMPT_TOKENS", 200)

    # Comfortably larger than a 200-token budget once wrapped in the prompt template.
    oversized_diff = "changed_line = 1\n" * 2000

    prompt, _contexts, _ratio = analysis_service._gather_grounded_context(
        "Big refactor", "", oversized_diff, "org/repo"
    )

    assert "diff truncated to fit token budget" in prompt
    assert count_tokens(prompt, settings.LLM_MODEL) <= settings.LLM_MAX_PROMPT_TOKENS


def test_gather_grounded_context_raises_when_still_too_large_after_trim(monkeypatch):
    """Context/overhead alone already exceeds an unreasonably tight budget -> fail fast,
    with no diff left to trim."""
    huge_repo_contexts = [
        {"file_path": f"f{i}.py", "similarity_score": 0.9, "source_code": "x" * 2000}
        for i in range(20)
    ]
    _patch_retrieval(monkeypatch, repo_contexts=huge_repo_contexts)
    monkeypatch.setattr(settings, "LLM_MAX_PROMPT_TOKENS", 5)

    with pytest.raises(PromptTooLargeError) as exc_info:
        analysis_service._gather_grounded_context("Big refactor", "", "some diff", "org/repo")

    assert "PR diff too large to analyze" in str(exc_info.value)
    assert "limit 5" in str(exc_info.value)


def test_analyze_pr_for_callback_fails_clean_without_calling_openai(monkeypatch):
    """When the request can't be brought under budget, OpenAI must never be called —
    the 429 should never happen in the first place."""
    _patch_retrieval(monkeypatch)
    monkeypatch.setattr(settings, "LLM_MAX_PROMPT_TOKENS", 5)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("OpenAI should not be called when the prompt is too large")

    monkeypatch.setattr(analysis_service.client.chat.completions, "create", _fail_if_called)

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
        analyze_pr_for_callback(request)
