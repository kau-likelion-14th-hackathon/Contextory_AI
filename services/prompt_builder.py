"""
prompt_builder.py — Grounded Prompt 생성 (RAG Pipeline ④)

프론트가 표시하는 10개 항목(작업 요약 ~ 분석 근거)을 구조화 JSON으로 받기 위한 프롬프트를 만든다.

설계 원칙
- 프롬프트 문구를 코드 여기저기에 f-string으로 흩뿌리지 않는다. 이 파일의 템플릿 상수
  (SYSTEM_PROMPT_TEMPLATE / USER_PROMPT_TEMPLATE / OUTPUT_SCHEMA_SECTION)가 유일한 관리 지점이다.
- Context 섹션에는 Context Filter를 '통과한' chunk만 넣는다. 제거된 chunk는 절대 넣지 않는다.
- 근거 없는 생성은 프롬프트 문구가 아니라 (1) 필터 통과 Context만 주입 (2) 구조화 출력 강제
  (3) 근거 부족 → needsConfirmation 항목, 세 가지 구조로 막는다.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

# 프롬프트에 실을 chunk 본문 최대 길이 (Context Window 보호)
CONTEXT_SNIPPET_MAX_CHARS = 1200

# 판단 근거가 없을 때 문자열 필드에 채우는 값 (명세: 판단 불가한 문자열 필드는 "확인 필요")
UNKNOWN_VALUE = "확인 필요"

# affectedRoles 허용 값 (명세 고정). 이 목록에 없는 역할은 매핑 단계에서 버린다.
ALLOWED_ROLES = ("프론트엔드", "백엔드", "AI", "기획", "디자인", "QA", "프로젝트 관리자")

# roleImpacts[].basis 허용 값 (사실 / 예상 구분)
BASIS_CONFIRMED = "확인된 사실"
BASIS_EXPECTED = "변경 기반 예상"
ALLOWED_BASIS = (BASIS_CONFIRMED, BASIS_EXPECTED)

# evidence[].source 허용 값
EVIDENCE_SOURCE_DIFF = "pr_diff"
EVIDENCE_SOURCE_CONTEXT = "context"

NO_CONTEXT_TEXT = "관련 기존 컨텍스트 없음"


@dataclass
class ProjectInfo:
    """프로젝트 기록 초안 작성에 필요한 프로젝트 메타 정보 (분석의 배경 지식)"""

    name: str = ""
    description: str = ""          # 한 줄 설명
    purpose: str = ""
    features: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    language: str = "ko"


@dataclass
class PRInput:
    """분석 대상 PR — '실제 변경의 판단 기준'이 되는 1차 자료"""

    title: str = ""
    body: str = ""
    changed_files: List[str] = field(default_factory=list)
    diff: str = ""
    commits: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)

    @property
    def has_diff(self) -> bool:
        """diff가 비어 있으면 분석을 진행하지 않는다(확인 필요 경로)."""
        return bool(str(self.diff or "").strip())


# ==========================================
# 템플릿 (단일 관리 지점)
# ==========================================

SYSTEM_PROMPT_TEMPLATE = """당신은 Contextory의 PR 분석가다.

당신의 역할은 PR을 단순 요약하는 것이 아니라, 현재 PR의 변경 내용을 분석하고
기존 프로젝트 맥락과 연결하여 팀원들이 검토할 "프로젝트 기록 초안"을 만드는 것이다.
이 초안은 프론트엔드·백엔드·AI·기획·디자인·QA·프로젝트 관리자가 각자 자신의 역할에
필요한 정보를 빠르게 이해하는 데 사용된다.

[자료 사용 규칙]
1. 판단 기준은 오직 아래 제공되는 자료다: 프로젝트 정보, 현재 PR(제목·본문·변경 파일·
   코드 diff·커밋·연결된 Issue), 검색된 기존 프로젝트 컨텍스트.
2. "현재 PR / diff"는 실제로 무엇이 변경됐는지 판단하는 유일한 기준이다.
   "검색된 기존 프로젝트 컨텍스트"는 변경을 이해하기 위한 배경일 뿐,
   현재 변경 사실의 근거로 사용하지 않는다. 두 자료를 같은 수준으로 섞지 않는다.
3. 제공된 자료에서 확인할 수 없는 사실은 절대 작성하지 않는다.
   diff에 없는 변경을 만들어내지 않고, PR 본문에 없는 목적을 확정적으로 쓰지 않는다.

[모르는 것 처리 규칙]
4. 변경 목적·변경 이유 등을 판단할 근거가 없으면 추측으로 채우지 말고,
   해당 필드에는 "{unknown}"라고 쓰고 needsConfirmation 배열에 무엇을 누구에게
   확인해야 하는지 구체적으로 적는다.
   (예: "변경 이유가 PR 본문에 없음 — PR 작성자에게 확인 필요")
5. 자료끼리 내용이 충돌하면(기존 컨텍스트 vs 현재 PR, 컨텍스트끼리) 임의로 하나를
   선택하지 않는다. 충돌 사실 자체를 서술하고 needsConfirmation에 추가한다.
   (예: "기존 문서에는 message 유지로 되어 있으나 현재 PR에서는 삭제됨 — 정책 확인 필요")

[사실과 예상 구분 규칙]
6. diff에서 직접 확인되는 사실(예: errorCode 필드 추가)과, 그 사실로부터 예상한
   영향(예: 프론트엔드 오류 분기 수정 필요)을 구분해서 작성한다.
   roleImpacts의 각 항목에 basis 필드로 "{basis_confirmed}"인지 "{basis_expected}"인지 표시한다.

[역할별 영향 규칙]
7. 모든 역할에 억지로 영향을 만들지 않는다. 실제 이번 변경과 연결되는 역할만
   affectedRoles에 넣는다. 역할별 영향은 일반론이 아니라 "그 역할이 실제로
   확인하거나 수정해야 하는 것"을 쓴다. 근거가 약하면 만들지 말고 확인 필요로 남긴다.

[근거 연결 규칙]
8. 모든 주요 판단(요약·전후·역할별 영향)은 evidence 배열의 항목과 연결한다.
   evidence에는 근거가 된 파일 경로, diff 내용 요약, 또는 컨텍스트 chunk id를 적는다.
   근거를 연결할 수 없는 판단은 쓰지 않거나 확인 필요로 처리한다.

[표기 규칙]
9. 파일명, 변수명, 클래스명, 함수명, API 경로, 브랜치명, 커밋 해시는 절대 번역하거나
   변형하지 않고 원본 그대로 백틱 없이 쓴다. (예: /api/auth/login, errorCode, LoginResponse)
10. 설명 문장은 {language}로 작성한다.

[출력 규칙]
11. 반드시 아래 "출력 스키마"의 JSON 형식으로만 출력한다. JSON 외의 텍스트를 붙이지 않는다.
12. 해당 없는 배열 필드는 빈 배열 []로, 판단 불가한 문자열 필드는 "{unknown}"로 채운다."""

USER_PROMPT_TEMPLATE = """[프로젝트 정보 — 분석의 배경 지식]
- 프로젝트 이름: {project_name}
- 한 줄 설명: {project_description}
- 프로젝트 목적: {project_purpose}
- 주요 기능: {project_features}
- 팀 역할: {project_roles}
- 기본 언어: {default_language}

[현재 PR — 실제 변경의 판단 기준]
- PR 제목: {pr_title}
- PR 본문:
{pr_body}
- 변경 파일 목록:
{changed_files}
- 코드 diff:
{diff}
- 커밋:
{commits}
- 연결된 Issue:
{linked_issues}

[검색된 기존 프로젝트 컨텍스트 — 배경/맥락으로만 사용]
※ 아래 내용은 과거 시점의 프로젝트 내용이므로 현재 변경 사실의 근거가 아니다.
{retrieved_contexts}

{output_schema_section}

위 자료를 분석해 시스템 프롬프트의 규칙에 따라 프로젝트 기록 초안을 JSON으로 작성하라."""

OUTPUT_SCHEMA_SECTION = """[출력 스키마 — 아래 JSON 객체 하나만 출력]
{
  "summary": "이번 PR에서 무엇을 작업했는지 2~3문장 요약",
  "purpose": "작업 목적. 근거 없으면 '확인 필요'",
  "changeReason": "왜 변경했는지. PR 본문·Issue에 근거가 없으면 '확인 필요'",
  "before": "변경 전 동작·구조 (diff의 삭제부와 컨텍스트로 확인되는 범위만)",
  "after": "변경 후 동작·구조 (diff의 추가부로 확인되는 범위만)",
  "relatedFeatures": ["관련 기능명"],
  "affectedRoles": ["프론트엔드", "QA"],
  "roleImpacts": [
    {
      "role": "프론트엔드",
      "impact": "그 역할이 실제로 확인·수정해야 하는 내용",
      "basis": "확인된 사실 | 변경 기반 예상",
      "evidenceRefs": ["e1"]
    }
  ],
  "followUpTasks": ["근거가 있는 후속 작업만. 없으면 빈 배열"],
  "needsConfirmation": [
    "확인이 필요한 내용 — 무엇을, 왜, 가능하면 누구에게"
  ],
  "evidence": [
    {
      "id": "e1",
      "source": "pr_diff | context",
      "location": "파일 경로 또는 chunk_id",
      "description": "이 근거에서 확인되는 내용"
    }
  ]
}

- affectedRoles 허용 값: 프론트엔드, 백엔드, AI, 기획, 디자인, QA, 프로젝트 관리자
  (이 목록에 없는 역할은 쓰지 않는다)
- roleImpacts[].evidenceRefs 는 evidence[].id 를 참조한다
- roleImpacts[].basis 는 "확인된 사실" 또는 "변경 기반 예상" 중 하나만 쓴다
- evidence[].source 는 "pr_diff"(현재 PR diff) 또는 "context"(검색된 기존 컨텍스트) 중 하나다"""

# 컨텍스트 항목 형식: [chunk_id] (출처: 파일경로, similarity: 점수) 내용
_CONTEXT_ITEM_TEMPLATE = "[{chunk_id}] (출처: {source}, similarity: {score:.4f})\n{content}"


# ==========================================
# 구조화 출력 스키마 (OUTPUT_SCHEMA_SECTION과 1:1로 맞춘 Pydantic 모델)
# ==========================================
# LLM 호출 시 이 모델로 JSON 스키마를 강제하고 그대로 파싱한다(자유 텍스트 후처리 없음).
# 필드명을 프롬프트의 JSON 키와 동일하게(camelCase) 두어 프롬프트와 스키마가 어긋나지 않게 한다.

class RecordDraftEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str          # pr_diff | context
    location: str        # 파일 경로 또는 chunk_id
    description: str


class RecordDraftRoleImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str            # ALLOWED_ROLES 중 하나
    impact: str
    basis: str           # 확인된 사실 | 변경 기반 예상
    evidenceRefs: List[str]


class RecordDraftOutput(BaseModel):
    """프론트 표시 10개 항목 + followUpTasks"""

    model_config = ConfigDict(extra="forbid")

    summary: str
    purpose: str
    changeReason: str
    before: str
    after: str
    relatedFeatures: List[str]
    affectedRoles: List[str]
    roleImpacts: List[RecordDraftRoleImpact]
    followUpTasks: List[str]
    needsConfirmation: List[str]
    evidence: List[RecordDraftEvidence]


def build_system_prompt(project: Optional[ProjectInfo] = None) -> str:
    """시스템 프롬프트 생성 (설명 언어만 프로젝트 설정에 따라 바뀐다)"""
    language = (project.language if project and project.language else "ko")
    return SYSTEM_PROMPT_TEMPLATE.format(
        unknown=UNKNOWN_VALUE,
        basis_confirmed=BASIS_CONFIRMED,
        basis_expected=BASIS_EXPECTED,
        language="한국어" if str(language).lower() != "en" else "English",
    )


# 하위 호환: 기존 호출부(SYSTEM_INSTRUCTION)를 위한 기본 시스템 프롬프트
SYSTEM_INSTRUCTION = build_system_prompt()


# ==========================================
# 섹션 빌더
# ==========================================

def _fallback(value: Any, placeholder: str = "(정보 없음)") -> str:
    text = str(value).strip() if value is not None else ""
    return text or placeholder


def _bullet_list(items: Optional[List[str]], placeholder: str = "  - (없음)") -> str:
    if not items:
        return placeholder
    return "\n".join(f"  - {item}" for item in items)


def build_context_section(filtered_contexts: List[Dict[str, Any]]) -> str:
    """
    검색 컨텍스트 섹션. Context Filter를 통과한 chunk만 받는다.
    비어 있으면 "관련 기존 컨텍스트 없음"을 명시해 컨텍스트 기반 서술을 막는다.
    """
    if not filtered_contexts:
        return NO_CONTEXT_TEXT

    items = []
    for ctx in filtered_contexts:
        content = str(ctx.get("text") or ctx.get("source_code") or ctx.get("review_comment") or "")
        items.append(
            _CONTEXT_ITEM_TEMPLATE.format(
                chunk_id=ctx.get("chunk_id") or ctx.get("id") or "unknown",
                source=ctx.get("source") or ctx.get("file_path") or "unknown",
                score=float(ctx.get("similarity_score", 0.0) or 0.0),
                content=content[:CONTEXT_SNIPPET_MAX_CHARS],
            )
        )
    return "\n\n".join(items)


def build_user_prompt(
    pr: PRInput,
    filtered_contexts: List[Dict[str, Any]],
    project: Optional[ProjectInfo] = None,
) -> str:
    """유저 프롬프트 생성 (프로젝트 정보 + 현재 PR + 필터 통과 컨텍스트 + 출력 스키마)"""
    project = project or ProjectInfo()
    return USER_PROMPT_TEMPLATE.format(
        project_name=_fallback(project.name),
        project_description=_fallback(project.description),
        project_purpose=_fallback(project.purpose),
        project_features=", ".join(project.features) if project.features else "(정보 없음)",
        project_roles=", ".join(project.roles) if project.roles else "(정보 없음)",
        default_language=_fallback(project.language, "ko"),
        pr_title=_fallback(pr.title),
        pr_body=_fallback(pr.body),
        changed_files=_bullet_list(pr.changed_files),
        diff=_fallback(pr.diff, "(diff 없음)"),
        commits=_bullet_list(pr.commits),
        linked_issues=_bullet_list(pr.issues),
        retrieved_contexts=build_context_section(filtered_contexts),
        output_schema_section=OUTPUT_SCHEMA_SECTION,
    )


def build_grounded_prompt(
    pr: PRInput,
    filtered_contexts: List[Dict[str, Any]],
    project: Optional[ProjectInfo] = None,
) -> str:
    """
    하위 호환 진입점 — 시스템 프롬프트 + 유저 프롬프트를 한 문자열로 합쳐 반환한다.
    (LLM 호출부는 build_system_prompt / build_user_prompt를 나눠 쓰는 것을 권장)
    """
    return f"{build_system_prompt(project)}\n\n{build_user_prompt(pr, filtered_contexts, project)}"
