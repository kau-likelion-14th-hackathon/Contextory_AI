from pydantic import BaseModel, Field, ConfigDict
from pydantic.alias_generators import to_camel
from typing import List, Literal, Optional


# ==========================================
# 1. 공통 및 세부 요소를 위한 DTO
# ==========================================

class Evidence(BaseModel):
    """
    Context Filter를 통과해 LLM 분석 근거로 사용된 pgvector 검색 결과 DTO

    하위 호환 원칙: 기존 필드(id/source_code/pr_diff/review_comment/similarity_score)는
    이름/의미를 그대로 유지하고, 근거 추적용 필드만 Optional로 추가한다.
    """
    id: str = Field(..., description="검색된 벡터 데이터 고유 ID", example="12345")
    source_code: Optional[str] = Field(None, description="참조 원본 소스코드")
    pr_diff: Optional[str] = Field(None, description="참조 PR Diff 조각")
    review_comment: Optional[str] = Field(None, description="참조 과거 리뷰 코멘트")
    similarity_score: float = Field(..., description="코사인 유사도 점수 (0.0~1.0)", example=0.87)
    # 🚀 [추가] 근거 추적(evidence linking)용 필드
    chunk_id: Optional[str] = Field(None, description="프롬프트에 노출된 chunk 식별자 (LLM이 인용하는 값)", example="ctx-3")
    source_type: Optional[str] = Field(None, description="근거 출처 종류 (code_review / repo_code)", example="repo_code")
    file_path: Optional[str] = Field(None, description="근거가 된 프로젝트 파일 경로", example="src/main/java/auth/AuthService.java")


class CodeReviewComment(BaseModel):
    """
    파일별 세부 코드 리뷰 피드백 DTO
    """
    file_path: Optional[str] = Field(None, description="리뷰 대상 파일 경로", example="src/main/java/com/contextory/service/UserService.java")
    line_number: Optional[int] = Field(None, description="코드 줄 번호 (전체 파일 리뷰 시 None)", example=42)
    comment: str = Field(..., description="AI 코드 리뷰 피드백 내용", example="N+1 쿼리 문제가 발생할 수 있으므로 Fetch Join 사용을 권장합니다.")


class RoleImpact(BaseModel):
    """
    프로젝트 기록 초안의 역할별 영향 DTO (실제 변경과 연결되는 역할만 생성)

    허용 역할: 프론트엔드 / 백엔드 / AI / 기획 / 디자인 / QA / 프로젝트 관리자
    basis: "확인된 사실"(diff에서 직접 확인) 또는 "변경 기반 예상"(사실로부터 추론)
    """
    role: str = Field(..., description="영향을 받는 팀 역할", example="프론트엔드")
    impact: str = Field(..., description="그 역할이 실제로 확인·수정해야 하는 내용", example="message 기반 오류 분기를 errorCode 기반으로 수정해야 한다")
    basis: Optional[str] = Field(None, description="확인된 사실 | 변경 기반 예상", example="변경 기반 예상")
    evidence_refs: List[str] = Field(default_factory=list, description="근거 evidence[].id 참조", example=["e1"])


class FollowUpTask(BaseModel):
    """
    역할별 후속 작업 DTO.

    `needs_confirmation`("물어봐야 할 것")과 달리 "누군가 실제로 해야 할 일"이다.
    role은 허용 역할 7종 중 하나이며, 담당을 특정할 수 없으면 None으로 둔다(지어내지 않는다).
    """
    role: Optional[str] = Field(None, description="작업을 수행할 역할 (특정 불가 시 None)", example="프론트엔드")
    task: str = Field(..., description="처리해야 할 작업", example="message 기반 오류 분기를 errorCode 기반으로 수정")
    evidence_refs: List[str] = Field(default_factory=list, description="근거 evidence[].id 참조", example=["e1"])


class CodeFileChunk(BaseModel):
    """
    레포지토리 인덱싱을 위한 단일 코드 파일/조각 DTO
    """
    file_path: str = Field(..., description="파일 경로", example="src/main/java/com/contextory/service/UserService.java")
    chunk_idx: int = Field(default=0, description="파일 내 청크 인덱스 (단일 파일 시 0)", example=0)
    content: str = Field(..., description="파일의 소스코드 내용", example="package com.contextory.service;\n\npublic class UserService { ... }")


# ==========================================
# 2. [API 1] PR 분석 (POST /api/v1/analyze/pr) DTO
# ==========================================

class PRAnalysisRequest(BaseModel):
    """
    Spring Boot -> FastAPI: PR 분석 요청 DTO
    """
    pr_id: int = Field(..., description="PR 식별자 ID", example=101)
    repo_name: str = Field(..., description="리포지토리 이름", example="Contextory/Backend")
    title: str = Field(..., description="PR 제목", example="feat: 사용자 로그인 및 JWT 토큰 검증 로직 구현")
    description: Optional[str] = Field(None, description="PR 본문 설명", example="Spring Security 및 JWT 필터를 추가했습니다.")
    diff_content: str = Field(..., description="Git Diff 문맥 (코드 변경점)", example="@@ -10,3 +10,12 @@\n+ public void login() { ... }")
    author: str = Field(..., description="PR 작성자 닉네임", example="Jong-gang-man")


class PRAnalysisResponse(BaseModel):
    """
    FastAPI -> Spring Boot: PR 분석 결과 응답 DTO
    """
    pr_id: int = Field(..., description="요청받았던 PR ID", example=101)
    summary: str = Field(..., description="AI가 요약한 PR 핵심 변경사항 및 영향도", example="사용자 인증을 위한 JWT 필터 및 Spring Security 설정이 추가되었습니다.")
    risk_score: int = Field(..., description="코드 변경 위험도 점수 (1~100)", example=25)
    reviews: List[CodeReviewComment] = Field(default_factory=list, description="파일별 코드 리뷰 목록")
    evidences: List[Evidence] = Field(default_factory=list, description="RAG 분석 근거 Context 목록")
    confidence: float = Field(0.0, description="RAG 답변 신뢰도 점수 (0.0~1.0)", example=0.85)
    needs_confirmation: bool = Field(False, description="개발자 추가 확인 필요 여부", example=False)
    filter_ratio: float = Field(0.0, description="Context Filter 필터링 비율 (0.0~1.0)", example=0.2)

    # 🚀 [추가] 프로젝트 기록 초안(Project Record Draft) 필드
    # 기존 필드는 유지한 채 추가만 한다(하위 호환). 이 응답 모델은 기존 계약이 snake_case이므로
    # 기획서의 camelCase 필드명을 snake_case로 맞춰 넣었다. (백엔드 합의 필요)
    purpose: Optional[str] = Field(None, description="변경의 목적", example="외부 로그인 사용자도 서비스에 접근할 수 있게 하기 위함")
    change_reason: Optional[str] = Field(None, description="변경이 필요했던 이유/배경", example="기존 세션 인증이 모바일 클라이언트에서 유지되지 않는 문제가 있었음")
    before: Optional[str] = Field(None, description="변경 전 상태", example="세션 기반 인증만 지원")
    after: Optional[str] = Field(None, description="변경 후 상태", example="JWT 기반 인증 추가")
    related_features: List[str] = Field(default_factory=list, description="이번 변경과 연결된 기능 목록")
    affected_roles: List[str] = Field(default_factory=list, description="영향을 받는 팀 역할 목록", example=["Backend", "Frontend"])
    role_impacts: List["RoleImpact"] = Field(default_factory=list, description="역할별 영향 상세")
    follow_up_tasks: List["FollowUpTask"] = Field(default_factory=list, description="역할별 후속 작업 목록")
    confirmation_items: List[str] = Field(default_factory=list, description="근거 부족·충돌로 사람 확인이 필요한 항목")
    retrieval_quality_warning: bool = Field(False, description="filter_ratio 경고 기준 초과 등 검색 품질 확인 필요 신호", example=False)
    grounding_sufficient: bool = Field(True, description="분석에 필요한 근거 Context가 확보되었는지 여부", example=True)


# ==========================================
# 3. [API 2] 레포지토리 코드 인덱싱 (POST /api/v1/repos/index) DTO
# ==========================================

class RepoIndexingRequest(BaseModel):
    """
    Spring Boot -> FastAPI: 레포지토리 전체 코드 인덱싱(임베딩/Upsert/Delete) 요청 DTO
    """
    repo_name: str = Field(..., description="리포지토리 이름", example="Contextory/Backend")
    branch: str = Field(default="main", description="대상 브랜치명", example="main")
    commit_sha: Optional[str] = Field(None, description="인덱싱 대상 커밋 SHA", example="a1b2c3d4e5")
    files: List[CodeFileChunk] = Field(default_factory=list, description="인덱싱/Upsert 처리할 소스코드 목록")
    deleted_files: Optional[List[str]] = Field(default_factory=list, description="Rebase/삭제로 인해 DB에서 제거할 파일 경로 목록", example=["src/main/java/com/contextory/OldService.java"])


class RepoIndexingResponse(BaseModel):
    """
    FastAPI -> Spring Boot: 레포지토리 인덱싱 결과 응답 DTO
    """
    repo_name: str = Field(..., description="인덱싱된 리포지토리 이름", example="Contextory/Backend")
    indexed_files_count: int = Field(..., description="pgvector에 성공적으로 임베딩/Upsert 처리된 파일(청크) 수", example=15)
    deleted_files_count: int = Field(0, description="Rebase/삭제로 인해 DB에서 제거된 파일 수", example=1)
    message: str = Field(..., description="처리 결과 메시지", example="성공적으로 pgvector 인덱싱 및 정리가 완료되었습니다.")


# ==========================================
# 4. [내부 API] 비동기 PR 분석 (POST /internal/v1/analyses) DTO
#    Backend <-> AI 내부 계약은 camelCase JSON을 사용하므로 CamelModel을 공통 Base로 둔다.
# ==========================================

class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PullRequestFile(CamelModel):
    """PR 변경 파일 단위 정보"""
    file_path: str = Field(..., example="src/main/java/auth/AuthService.java")
    change_type: str = Field(..., example="MODIFIED")
    patch: Optional[str] = Field(None, description="Unified diff 조각", example="@@ -21,7 +21,18 @@ ...")
    additions: int = Field(0, example=50)
    deletions: int = Field(0, example=10)


class PullRequestInfo(CamelModel):
    """분석 대상 PR 정보"""
    github_pr_id: int = Field(..., example=987654321)
    pr_number: int = Field(..., example=18)
    title: str = Field(..., example="JWT 로그인 기능 추가")
    body: Optional[str] = Field(None, example="JWT 기반 인증 기능을 구현했습니다.")
    head_sha: str = Field(..., example="a123bc456def789")
    source_branch: str = Field(..., example="feature/login")
    target_branch: str = Field(..., example="develop")
    files: List[PullRequestFile] = Field(default_factory=list)


class AsyncAnalysisRequest(CamelModel):
    """
    Spring Boot -> FastAPI: 비동기 PR 분석 요청 DTO (POST /internal/v1/analyses)
    """
    analysis_id: int = Field(..., description="Spring Boot 분석 ID", example=1)
    project_id: int = Field(..., example=1)
    repository_id: int = Field(..., example=1)
    repository_full_name: str = Field(..., description="owner/repository 형식", example="org/contextory")
    pull_request: PullRequestInfo
    language: str = Field("ko", description="분석 결과 언어 (ko/en)", example="ko")
    # 선택 필드 — 보내지 않아도 동작한다(미전달 시 AI 서버의 project.yml 을 폴백으로 쓴다).
    # 백엔드는 project_member.project_role 원본 값을 그대로 담으면 된다. 값 정규화
    # ("프론트"/"FE"/"Frontend" → "프론트엔드")는 AI 서버가 수행하기로 합의했다.
    project_roles: List[str] = Field(
        default_factory=list,
        description="프로젝트 멤버의 project_role 원본 값 목록 (선택). 정규화는 AI 서버가 수행",
        example=["프론트엔드", "BE", "AI"],
    )
    callback_url: str = Field(..., description="분석 완료 콜백 URL", example="https://api.contextory.com/api/v1/internal/ai/analyses/1/callback")


class AsyncAnalysisAcceptedResponse(CamelModel):
    """
    FastAPI -> Spring Boot: 비동기 분석 접수 응답 DTO (202 Accepted)
    """
    job_id: str = Field(..., description="FastAPI 작업 ID", example="rag-job-a12b34c56")
    status: str = Field("PROCESSING", example="PROCESSING")


class AnalysisChangeItem(CamelModel):
    file_path: str = Field(..., example="src/main/java/auth/AuthService.java")
    description: str = Field(..., example="로그인과 토큰 재발급 로직이 추가되었습니다.")


class EvidenceRef(CamelModel):
    """
    분석 근거 DTO (프론트 "분석 근거" 항목).
    roleImpacts[].evidenceRefs 가 이 항목의 id를 참조해 "이 영향의 근거 보기"로 연결된다.
    """
    id: str = Field(..., description="근거 식별자 (roleImpacts[].evidenceRefs가 참조)", example="e1")
    source: str = Field(..., description="pr_diff(현재 PR diff) | context(검색된 기존 컨텍스트)", example="pr_diff")
    location: Optional[str] = Field(None, description="파일 경로 또는 chunk_id", example="src/main/java/auth/LoginResponse.java")
    description: Optional[str] = Field(None, description="이 근거에서 확인되는 내용", example="errorCode 필드 추가 및 오류 응답 생성부 변경")
    # 검색 근거일 때만 채워지는 추적용 부가 정보
    chunk_id: Optional[str] = Field(None, description="검색 Context 식별자", example="cr-1")
    similarity_score: Optional[float] = Field(None, description="검색 유사도 점수", example=0.87)


class FollowUpTaskItem(CamelModel):
    """콜백 계약(camelCase)의 역할별 후속 작업 DTO"""
    role: Optional[str] = Field(None, description="작업을 수행할 역할 (특정 불가 시 null)", example="프론트엔드")
    task: str = Field(..., example="message 기반 오류 분기를 errorCode 기반으로 수정")
    evidence_refs: List[str] = Field(default_factory=list, description="evidence[].id 참조", example=["e1"])


class RoleImpactItem(CamelModel):
    """콜백 계약(camelCase)의 역할별 영향 DTO"""
    role: str = Field(..., example="프론트엔드")
    impact: str = Field(..., example="message 기반 오류 분기를 errorCode 기반으로 수정해야 한다")
    basis: Optional[str] = Field(None, description="확인된 사실 | 변경 기반 예상", example="변경 기반 예상")
    evidence_refs: List[str] = Field(default_factory=list, description="evidence[].id 참조", example=["e1"])


class AnalysisResultPayload(CamelModel):
    """
    콜백 result 객체 (분석 완료 시에만 채워짐)

    하위 호환 원칙: 기존 5개 필드(summary/changes/impacts/risks/recommendations)는 그대로 두고,
    기획서 기준 프로젝트 기록 초안 필드만 추가한다. Backend가 아직 모르는 필드는 무시하면 된다.
    """
    summary: str
    changes: List[AnalysisChangeItem] = Field(default_factory=list)
    impacts: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)

    # 🚀 [추가] 프로젝트 기록 초안 필드 — 프론트 표시 10개 항목 (camelCase로 직렬화됨)
    #   작업 요약 summary / 작업 목적 purpose / 변경 이유 changeReason / 변경 전 before /
    #   변경 후 after / 관련 기능 relatedFeatures / 영향받는 역할 affectedRoles /
    #   역할별 영향 roleImpacts / 확인 필요 사항 needsConfirmation / 분석 근거 evidence
    purpose: Optional[str] = None
    change_reason: Optional[str] = None
    before: Optional[str] = None
    after: Optional[str] = None
    related_features: List[str] = Field(default_factory=list)
    affected_roles: List[str] = Field(default_factory=list)
    role_impacts: List[RoleImpactItem] = Field(default_factory=list)
    follow_up_tasks: List[FollowUpTaskItem] = Field(default_factory=list)
    # 확인 필요 사항 목록. 비어 있으면 사람이 추가로 확인할 항목이 없다는 뜻이다.
    needs_confirmation: List[str] = Field(default_factory=list)
    evidence: List[EvidenceRef] = Field(default_factory=list)
    confidence: float = 0.0
    retrieval_quality_warning: bool = False


class AsyncAnalysisStatusResponse(CamelModel):
    """
    FastAPI -> Spring Boot: 비동기 분석 작업 상태 조회 응답 DTO (GET /internal/v1/analyses/{jobId})
    """
    job_id: str = Field(..., example="rag-job-a12b34c56")
    analysis_id: int = Field(..., example=1)
    status: str = Field(..., example="PROCESSING")
    started_at: str = Field(..., example="2026-08-02T11:30:05Z")
    completed_at: Optional[str] = Field(None, example=None)


class JobMaintenanceResponse(CamelModel):
    """
    FastAPI -> Spring Boot: 좀비 작업 정리/보존기간 삭제 결과 응답 DTO
    (POST /internal/v1/analyses/maintenance)
    """
    reaped_count: int = Field(..., description="타임아웃으로 FAILED 처리된 좀비 작업 수", example=2)
    purged_count: int = Field(..., description="보존 기간 초과로 삭제된 작업 수", example=0)
    timeout_minutes: int = Field(..., description="좀비 판정 기준 시간(분)", example=30)


class AnalysisCallbackPayload(CamelModel):
    """
    FastAPI -> Spring Boot: 분석 완료/실패 콜백 DTO (POST {callbackUrl})
    """
    job_id: str = Field(..., example="rag-job-a12b34c56")
    status: Literal["COMPLETED", "FAILED"]
    model_name: Optional[str] = Field(None, example="contextory-rag-v1")
    result: Optional[AnalysisResultPayload] = None
    error_message: Optional[str] = Field(None, example="Vector DB 검색 중 오류가 발생했습니다.")
    completed_at: str = Field(..., description="ISO 8601 UTC 시각", example="2026-08-02T11:31:30Z")