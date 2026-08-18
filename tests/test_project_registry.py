"""프로젝트 메타 레지스트리 테스트 — 파일 조회, 역할 제한, 파이프라인 연결"""

import json

import pytest

from services import project_registry
from services.analysis_service import analyze_pr_pipeline
from services.context_filter import filter_contexts
from services.project_registry import get_project_info
from services.prompt_builder import ALLOWED_ROLES
from services.retrieval import build_outcome

REGISTRY_YAML = """
default:
  language: ko
  roles: [프론트엔드, 백엔드]

projects:
  "org/contextory":
    name: Contextory
    description: PR 기록 서비스
    purpose: 변경 이유를 나중에 검색해 이해하게 한다
    features: [PR 분석, 기록 승인]
    roles: [프론트엔드, 백엔드, AI, 개발자]   # "개발자"는 허용 목록 밖 → 제거되어야 함
"""


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "project.yml"
    path.write_text(REGISTRY_YAML, encoding="utf-8")
    project_registry.clear_cache()
    yield path
    project_registry.clear_cache()


def test_lookup_by_full_repo_name(registry):
    info = get_project_info("org/contextory", path=registry)

    assert info.name == "Contextory"
    assert info.description == "PR 기록 서비스"
    assert info.features == ["PR 분석", "기록 승인"]
    assert info.language == "ko"


def test_lookup_by_repo_part_and_case_insensitive(registry):
    assert get_project_info("Contextory", path=registry).name == "Contextory"
    assert get_project_info("ORG/CONTEXTORY", path=registry).name == "Contextory"


def test_roles_are_limited_to_allowed_values(registry):
    """허용 목록 밖 역할이 프롬프트에 들어가면 응답 매핑에서 잘려 혼란이 생기므로 로드 시 제거한다."""
    info = get_project_info("org/contextory", path=registry)

    assert info.roles == ["프론트엔드", "백엔드", "AI"]
    assert all(role in ALLOWED_ROLES for role in info.roles)


def test_unknown_repo_falls_back_to_default(registry):
    info = get_project_info("someone/other-repo", path=registry)

    assert info.name == ""                       # default에 name이 없으므로 비어 있다
    assert info.roles == ["프론트엔드", "백엔드"]  # default의 역할은 적용된다


def test_missing_file_returns_none(tmp_path):
    project_registry.clear_cache()

    assert get_project_info("org/contextory", path=tmp_path / "없는파일.yml") is None


def test_empty_registry_returns_none(tmp_path):
    path = tmp_path / "project.yml"
    path.write_text("", encoding="utf-8")
    project_registry.clear_cache()

    assert get_project_info("org/contextory", path=path) is None


def test_cache_refreshes_when_file_changes(registry):
    assert get_project_info("org/contextory", path=registry).name == "Contextory"

    registry.write_text(REGISTRY_YAML.replace("name: Contextory", "name: 바뀐이름"), encoding="utf-8")

    assert get_project_info("org/contextory", path=registry).name == "바뀐이름"


def test_repository_project_yml_is_valid():
    """저장소에 커밋된 project.yml 이 실제로 로드되는지 (오타·문법 오류 조기 발견)"""
    project_registry.clear_cache()
    info = get_project_info("kau-likelion-14th-hackathon/Contextory_AI")

    assert info is not None
    assert info.name == "Contextory"
    assert "AI" in info.roles
    assert all(role in ALLOWED_ROLES for role in info.roles)


# ==========================================
# 파이프라인 연결
# ==========================================

CHUNKS = [{"chunk_id": "cr-1", "id": "1", "similarity_score": 0.9, "text": "ctx", "file_path": None}]

LLM_OUTPUT = {
    "summary": "s", "purpose": "p", "changeReason": "c", "before": "b", "after": "a",
    "relatedFeatures": [], "affectedRoles": [], "roleImpacts": [], "followUpTasks": [],
    "needsConfirmation": [], "evidence": [], "riskScore": 10, "reviews": [],
}


def test_pipeline_injects_project_meta_into_prompt(monkeypatch):
    """요청에 프로젝트 정보가 없어도 레지스트리에서 찾아 프롬프트에 넣는다."""
    from models.schemas import PRAnalysisRequest
    from services.prompt_builder import ProjectInfo

    monkeypatch.setattr(
        "services.project_registry.get_project_info",
        lambda repo_name: ProjectInfo(name="Contextory", roles=["프론트엔드", "AI"], language="ko"),
    )
    prompts = []

    analyze_pr_pipeline(
        PRAnalysisRequest(pr_id=1, repo_name="org/contextory", title="t", description="d",
                          diff_content="@@ -1 +1 @@\n+x", author="dev"),
        retrieve_fn=lambda **kw: build_outcome(CHUNKS, sim_threshold=0.5, min_evidence_count=1),
        filter_fn=lambda c: filter_contexts(c, sim_threshold=0.5, filter_mode="on"),
        generate_fn=lambda system, user: prompts.append(user) or json.dumps(LLM_OUTPUT),
        translate_fn=lambda t, b: "q",
    )

    assert "프로젝트 이름: Contextory" in prompts[0]
    assert "팀 역할: 프론트엔드, AI" in prompts[0]


def test_registry_failure_does_not_break_analysis(monkeypatch):
    """레지스트리 파일이 깨져도 분석 자체는 진행된다."""
    from models.schemas import PRAnalysisRequest

    def broken(repo_name):
        raise ValueError("깨진 YAML")

    monkeypatch.setattr("services.project_registry.get_project_info", broken)

    response = analyze_pr_pipeline(
        PRAnalysisRequest(pr_id=1, repo_name="org/contextory", title="t", description="d",
                          diff_content="@@ -1 +1 @@\n+x", author="dev"),
        retrieve_fn=lambda **kw: build_outcome(CHUNKS, sim_threshold=0.5, min_evidence_count=1),
        filter_fn=lambda c: filter_contexts(c, sim_threshold=0.5, filter_mode="on"),
        generate_fn=lambda system, user: json.dumps(LLM_OUTPUT),
        translate_fn=lambda t, b: "q",
    )

    assert response.summary == "s"
