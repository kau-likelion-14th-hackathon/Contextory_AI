"""
role_normalizer.py — 자유 입력 역할 문자열을 계약상 허용 역할 7종으로 정규화한다.

배경
    백엔드의 project_role 은 자유 입력 VARCHAR(50) 이고 저장 시 trim 만 한다.
        ProjectMemberService.normalizeProjectRole() -> projectRole.trim()
    따라서 같은 역할이 "프론트엔드" / "프론트" / "FE" / "Frontend" / "프론트엔드개발자"
    처럼 제각각 들어온다. 값 정규화는 AI 서버가 담당하기로 팀에서 정했다.

왜 정규화가 필요한가
    프롬프트의 [팀 역할]과 응답 매핑(_allowed_roles / _role_impacts / _follow_up_tasks)은
    ALLOWED_ROLES 정확히 일치만 인정한다. 정규화가 없으면 "프론트"로 저장된 멤버는
    역할 자체가 사라지고, 그 역할의 affectedRoles / roleImpacts 가 통째로 비어버린다.

설계 원칙
    - 확신할 수 있는 표현만 매핑한다. 애매한 값은 매핑하지 않고 미해석으로 남긴다.
      (틀리게 붙이는 것이 안 붙이는 것보다 나쁘다 — 잘못된 역할에 영향이 배정되면
       그 역할 담당자가 잘못된 작업을 하게 된다.)
    - 미해석 값은 버리되, partition_roles() 로 호출부가 확인·로깅할 수 있게 돌려준다.
"""

import re
import unicodedata
from typing import Any, Iterable, List, Optional, Tuple

from services.prompt_builder import ALLOWED_ROLES

# 정규화 키에서 떼어낼 접미사. "백엔드개발자" → "백엔드"
_SUFFIXES = (
    "개발자", "개발", "담당자", "담당", "엔지니어", "파트", "팀원", "팀",
    "engineer", "developer", "dev", "team", "part",
)

# 정규화 키 → 허용 역할.
# 키는 _key() 를 거친 형태(소문자, 공백·구분자 제거, 접미사 제거)로 적는다.
_ALIASES = {
    # ── 프론트엔드 ──────────────────────────────────────────────
    "프론트엔드": "프론트엔드", "프런트엔드": "프론트엔드", "프론트앤드": "프론트엔드",
    "프론트": "프론트엔드", "프런트": "프론트엔드",
    "frontend": "프론트엔드", "front": "프론트엔드", "fe": "프론트엔드",
    "client": "프론트엔드", "클라이언트": "프론트엔드",
    "react": "프론트엔드", "리액트": "프론트엔드", "nextjs": "프론트엔드", "next": "프론트엔드",

    # ── 백엔드 ─────────────────────────────────────────────────
    "백엔드": "백엔드", "벡엔드": "백엔드", "백앤드": "백엔드", "백": "백엔드",
    "backend": "백엔드", "back": "백엔드", "be": "백엔드",
    "server": "백엔드", "서버": "백엔드", "api": "백엔드",
    "spring": "백엔드", "스프링": "백엔드", "springboot": "백엔드",
    "django": "백엔드", "node": "백엔드", "nodejs": "백엔드",

    # ── AI ────────────────────────────────────────────────────
    "ai": "AI", "인공지능": "AI", "aiml": "AI",
    "ml": "AI", "머신러닝": "AI", "machinelearning": "AI",
    "딥러닝": "AI", "deeplearning": "AI",
    "llm": "AI", "rag": "AI", "mlops": "AI", "데이터사이언스": "AI",

    # ── 기획 ──────────────────────────────────────────────────
    "기획": "기획", "기획자": "기획", "서비스기획": "기획",
    "planner": "기획", "planning": "기획", "plan": "기획",
    "po": "기획", "productowner": "기획", "productmanager": "기획",
    "프로덕트오너": "기획", "프로덕트매니저": "기획",

    # ── 디자인 ────────────────────────────────────────────────
    "디자인": "디자인", "디자이너": "디자인",
    "design": "디자인", "designer": "디자인",
    "ux": "디자인", "uiux": "디자인", "uxui": "디자인", "uxdesigner": "디자인",

    # ── QA ────────────────────────────────────────────────────
    "qa": "QA", "qc": "QA", "테스터": "QA", "tester": "QA",
    "테스트": "QA", "test": "QA", "품질": "QA", "quality": "QA",

    # ── 프로젝트 관리자 ────────────────────────────────────────
    "프로젝트관리자": "프로젝트 관리자", "프로젝트매니저": "프로젝트 관리자",
    "프로젝트리더": "프로젝트 관리자",
    "projectmanager": "프로젝트 관리자", "projectlead": "프로젝트 관리자",
    "스크럼마스터": "프로젝트 관리자", "scrummaster": "프로젝트 관리자",
    "pl": "프로젝트 관리자",
}

# 의도적으로 매핑하지 않는 값 — 뜻이 하나로 정해지지 않는다.
#   pm       : Product Manager(기획) / Project Manager(프로젝트 관리자) 양쪽으로 쓰인다
#   ui       : UI 디자인 / UI 구현(프론트엔드) 양쪽으로 쓰인다
#   웹, 개발자 : 파트를 특정하지 못한다
#   풀스택    : 프론트엔드와 백엔드 양쪽 — 하나로 접으면 나머지 하나가 누락된다
AMBIGUOUS = frozenset({
    "pm", "manager", "매니저", "ui", "유아이",
    "웹", "web", "풀스택", "fullstack", "개발", "리더", "leader", "팀장",
})


def _key(value: Any) -> str:
    """비교용 키: NFKC 정규화 → 소문자 → 공백·구분자 제거 → 접미사 제거."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = re.sub(r"[\s_\-/·.,()\[\]]+", "", text)
    for suffix in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text


def normalize_role(value: Any) -> Optional[str]:
    """
    역할 문자열 하나를 허용 역할로 정규화한다.
    매핑할 수 없거나 뜻이 애매하면 None — 임의로 추측해 붙이지 않는다.
    """
    key = _key(value)
    if not key or key in AMBIGUOUS:
        return None
    return _ALIASES.get(key)


def partition_roles(values: Any) -> Tuple[List[str], List[str]]:
    """
    역할 목록을 (정규화 성공, 미해석 원본) 으로 나눈다.
    미해석 값은 호출부가 로그로 남겨 별칭 표를 보강하는 데 쓴다.
    """
    if isinstance(values, str) or not isinstance(values, Iterable):
        return [], []

    resolved: List[str] = []
    unresolved: List[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        role = normalize_role(text)
        if role is None:
            if text not in unresolved:
                unresolved.append(text)
        elif role not in resolved:
            resolved.append(role)
    return resolved, unresolved


def normalize_roles(values: Any) -> List[str]:
    """역할 목록 정규화 (중복 제거, 입력 순서 유지). 미해석 값은 버린다."""
    return partition_roles(values)[0]


__all__ = ["ALLOWED_ROLES", "AMBIGUOUS", "normalize_role", "normalize_roles", "partition_roles"]
