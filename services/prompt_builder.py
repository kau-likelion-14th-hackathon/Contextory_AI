"""
prompt_builder.py — Grounded Prompt 생성 (RAG Pipeline ④)

설계 원칙
- 프롬프트 문구를 코드 여기저기에 f-string으로 흩뿌리지 않는다. 이 파일의 템플릿 상수
  (SYSTEM_INSTRUCTION / GROUNDED_PROMPT_TEMPLATE / 섹션 템플릿)가 유일한 관리 지점이다.
- Context 섹션에는 Context Filter를 '통과한' chunk만 넣는다. 제거된 chunk는 절대 넣지 않는다.
- 근거 없는 생성은 프롬프트 문구가 아니라 (1) 필터 통과 Context만 주입 (2) 구조화 출력 강제
  (3) 근거 부족 → needsConfirmation 규칙, 세 가지 구조로 막는다.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 프롬프트에 실을 chunk 본문 최대 길이 (Context Window 보호)
CONTEXT_SNIPPET_MAX_CHARS = 1200


@dataclass
class ProjectInfo:
    """프로젝트 기록 초안 작성에 필요한 프로젝트 메타 정보"""

    name: str = ""
    one_liner: str = ""
    purpose: str = ""
    key_features: List[str] = field(default_factory=list)
    team_roles: List[str] = field(default_factory=list)
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


# ==========================================
# 템플릿 (단일 관리 지점)
# ==========================================

SYSTEM_INSTRUCTION = """당신은 Contextory 프로젝트 기록 초안을 작성하는 분석가다.
반드시 사용자 메시지의 [출력 스키마]에 정의된 JSON 객체 하나만 출력한다.
스키마 밖의 설명 문장, 마크다운 코드펜스, 주석을 출력하지 않는다."""

_PROJECT_SECTION_TEMPLATE = """[프로젝트 정보]
- 이름: {name}
- 한 줄 설명: {one_liner}
- 목적: {purpose}
- 주요 기능: {key_features}
- 팀 역할: {team_roles}
- 기본 언어: {language}"""

_PR_SECTION_TEMPLATE = """[현재 PR — 실제 변경의 판단 기준]
- PR 제목: {title}
- PR 본문: {body}
- 변경 파일 목록:
{changed_files}
- 코드 diff:
{diff}
- 커밋: {commits}
- 연결된 Issue: {issues}"""

_CONTEXT_ITEM_TEMPLATE = """- chunk_id: {chunk_id} | 출처: {source} | 유사도: {score:.4f}
  내용: {content}"""

_CONTEXT_SECTION_TEMPLATE = """[검색된 기존 프로젝트 컨텍스트 — 배경·맥락으로만 사용, PR과 동급 정보 아님]
{context_items}"""

_NO_CONTEXT_TEXT = """[검색된 기존 프로젝트 컨텍스트 — 배경·맥락으로만 사용, PR과 동급 정보 아님]
- (필터를 통과한 컨텍스트 없음. 이번 분석은 PR 자체의 변경 내용만을 근거로 삼는다.)"""

_RULES_SECTION = """[분석 규칙]
1. 단순 PR 요약이 아니라 변경 목적·전후·역할별 영향·후속 작업을 정리한다.
2. 위 [현재 PR]이 실제 변경의 판단 기준이다. [검색된 기존 프로젝트 컨텍스트]는 배경·맥락으로만 쓰고
   PR과 동급의 사실로 취급하지 않는다.
3. 위 자료에서 확인할 수 없는 사실은 쓰지 않는다. 근거가 부족하면 지어내지 말고
   needsConfirmation=true 로 두고 confirmationItems에 무엇을 확인해야 하는지 적는다.
4. 직접 확인된 사실과, 그로부터 예상한 영향·후속 작업을 구분해 서술한다.
   (예상에는 "…로 보인다 / 예상된다"처럼 추론임을 드러내는 표현을 쓴다.)
5. PR과 컨텍스트가 충돌하거나 컨텍스트끼리 충돌하면 임의로 하나를 고르지 말고,
   충돌 사실을 명시하고 needsConfirmation=true 로 처리한다.
6. 반드시 [출력 스키마]의 JSON 객체 하나만 출력한다. 스키마 밖의 자유 문장을 덧붙이지 않는다.
7. 주요 판단마다 근거가 된 diff 위치·파일·chunk_id를 evidence 항목으로 연결한다.
8. 파일명·변수명·클래스명·API 경로·브랜치명·커밋 해시는 번역하거나 고쳐 쓰지 않고 원문 그대로 쓴다.
9. affectedRoles / roleImpacts 에는 위 [프로젝트 정보]의 "팀 역할"에 실제로 있는 역할만,
   그중 이번 변경과 직접 연결되는 것만 쓴다. 팀 역할 정보가 "(정보 없음)"이면
   affectedRoles / roleImpacts 를 빈 배열로 두고, 확인이 필요하다는 사실을 confirmationItems에 적는다.
   ("개발자", "보안 담당자" 처럼 일반적인 역할을 임의로 만들어내지 않는다.)
10. 서술 언어는 {language} 로 작성한다. (단, 규칙 8의 식별자는 원문 유지)"""

_OUTPUT_SCHEMA_SECTION = """[출력 스키마 — 아래 JSON 객체 하나만 출력]
{
  "summary": "이번 PR 변경의 핵심 요약",
  "purpose": "이 변경의 목적",
  "changeReason": "이 변경이 필요했던 이유/배경",
  "before": "변경 전 상태",
  "after": "변경 후 상태",
  "relatedFeatures": ["이번 변경과 연결된 기능"],
  "affectedRoles": ["실제로 영향을 받는 역할만"],
  "roleImpacts": [
    {"role": "역할명", "impact": "그 역할이 받는 구체적 영향", "evidenceIds": ["chunk_id 또는 파일/diff 위치"]}
  ],
  "followUpTasks": ["근거가 있는 후속 작업만"],
  "needsConfirmation": true,
  "confirmationItems": ["사람이 확인해야 하는 항목"],
  "evidence": [
    {"chunkId": "위 컨텍스트의 chunk_id 문자열. 해당 없으면 JSON null(문자열 \\"null\\" 금지)",
     "filePath": "파일 경로 또는 JSON null",
     "diffLocation": "diff 위치(hunk 헤더 등) 또는 JSON null",
     "description": "이 근거로 판단한 내용"}
  ],
  "changes": [
    {"filePath": "변경된 파일 경로", "description": "그 파일에서 무엇이 바뀌었는지"}
  ],
  "reviews": [
    {"file_path": "리뷰 대상 파일 경로 또는 JSON null", "line_number": "정수 또는 JSON null",
     "comment": "diff에서 확인되는 구체적 리뷰 의견"}
  ],
  "risks": ["구조/보안상 확인이 필요한 위험 요소"],
  "recommendations": ["리뷰어·작성자를 위한 제안"],
  "riskScore": "0~100 사이 정수. 변경 범위·영향도·되돌리기 난이도로 판단하며 근거 없이 0을 넣지 않는다"
}"""

GROUNDED_PROMPT_TEMPLATE = """[역할] 당신은 이 PR의 변경 내용을 분석해 Contextory 프로젝트 기록 초안을 만드는 분석가다.
단순 PR 요약이 아니라 변경 목적·전후·역할별 영향·후속 작업을 정리한다.

{project_section}

{pr_section}

{context_section}

{rules_section}

{output_schema_section}"""


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


def _build_project_section(project: Optional[ProjectInfo]) -> str:
    project = project or ProjectInfo()
    return _PROJECT_SECTION_TEMPLATE.format(
        name=_fallback(project.name),
        one_liner=_fallback(project.one_liner),
        purpose=_fallback(project.purpose),
        key_features=", ".join(project.key_features) if project.key_features else "(정보 없음)",
        team_roles=", ".join(project.team_roles) if project.team_roles else "(정보 없음)",
        language=_fallback(project.language, "ko"),
    )


def _build_pr_section(pr: PRInput) -> str:
    return _PR_SECTION_TEMPLATE.format(
        title=_fallback(pr.title),
        body=_fallback(pr.body),
        changed_files=_bullet_list(pr.changed_files),
        diff=_fallback(pr.diff, "(diff 없음)"),
        commits=", ".join(pr.commits) if pr.commits else "(정보 없음)",
        issues=", ".join(pr.issues) if pr.issues else "(정보 없음)",
    )


def _build_context_section(filtered_contexts: List[Dict[str, Any]]) -> str:
    if not filtered_contexts:
        return _NO_CONTEXT_TEXT

    items = []
    for ctx in filtered_contexts:
        content = str(ctx.get("text") or ctx.get("source_code") or ctx.get("review_comment") or "")
        items.append(
            _CONTEXT_ITEM_TEMPLATE.format(
                chunk_id=ctx.get("chunk_id") or ctx.get("id") or "unknown",
                source=ctx.get("source") or ctx.get("file_path") or "unknown",
                score=float(ctx.get("similarity_score", 0.0) or 0.0),
                content=content[:CONTEXT_SNIPPET_MAX_CHARS].replace("\n", "\n    "),
            )
        )
    return _CONTEXT_SECTION_TEMPLATE.format(context_items="\n".join(items))


def build_grounded_prompt(
    pr: PRInput,
    filtered_contexts: List[Dict[str, Any]],
    project: Optional[ProjectInfo] = None,
) -> str:
    """
    Grounded Prompt를 생성한다. filtered_contexts에는 Context Filter를 통과한 chunk만 넘겨야 한다.
    """
    language = (project.language if project and project.language else "ko")
    return GROUNDED_PROMPT_TEMPLATE.format(
        project_section=_build_project_section(project),
        pr_section=_build_pr_section(pr),
        context_section=_build_context_section(filtered_contexts),
        rules_section=_RULES_SECTION.format(language="한국어" if language != "en" else "English"),
        output_schema_section=_OUTPUT_SCHEMA_SECTION,
    )
