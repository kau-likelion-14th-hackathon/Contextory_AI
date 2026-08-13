from pydantic import BaseModel, Field
from typing import List, Optional


# ==========================================
# 1. 공통 및 세부 요소를 위한 DTO
# ==========================================

class Evidence(BaseModel):
    """
    Context Filter를 통과해 LLM 분석 근거로 사용된 pgvector 검색 결과 DTO
    """
    id: str = Field(..., description="검색된 벡터 데이터 고유 ID", example="12345")
    source_code: Optional[str] = Field(None, description="참조 원본 소스코드")
    pr_diff: Optional[str] = Field(None, description="참조 PR Diff 조각")
    review_comment: Optional[str] = Field(None, description="참조 과거 리뷰 코멘트")
    similarity_score: float = Field(..., description="코사인 유사도 점수 (0.0~1.0)", example=0.87)


class CodeReviewComment(BaseModel):
    """
    파일별 세부 코드 리뷰 피드백 DTO
    """
    file_path: Optional[str] = Field(None, description="리뷰 대상 파일 경로", example="src/main/java/com/contextory/service/UserService.java")
    line_number: Optional[int] = Field(None, description="코드 줄 번호 (전체 파일 리뷰 시 None)", example=42)
    comment: str = Field(..., description="AI 코드 리뷰 피드백 내용", example="N+1 쿼리 문제가 발생할 수 있으므로 Fetch Join 사용을 권장합니다.")


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