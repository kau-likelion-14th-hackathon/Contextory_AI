"""
project_registry.py — 프로젝트 메타 정보 조회 (분석 프롬프트의 [프로젝트 정보] 섹션 재료)

배경
    Grounded Prompt는 "팀 역할"을 근거로 affectedRoles / roleImpacts를 판단한다.
    그런데 분석 요청(PRAnalysisRequest / AsyncAnalysisRequest)에는 프로젝트 메타가 없어서,
    정보가 비면 프롬프트 규칙에 따라 두 항목이 통째로 비게 된다.

설계
    백엔드 요청 스키마를 확장하지 않고도 채울 수 있도록, AI 서버가 설정 파일(project.yml)에서
    저장소 이름으로 프로젝트 메타를 찾아 쓴다. 나중에 백엔드가 요청에 project 객체를 담게 되면
    그때는 호출부에서 ProjectInfo를 직접 넘기면 되고(우선순위가 더 높다) 이 모듈은 폴백으로 남는다.

파일이 없거나 항목이 없으면 조용히 None을 반환한다 — 분석은 그대로 진행되고,
프롬프트는 "(정보 없음)"으로 표기되어 역할 판단을 하지 않는다.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.config import settings
from services.prompt_builder import ProjectInfo
from services.role_normalizer import partition_roles

# (경로, mtime) → 파싱 결과 캐시. 파일을 고치면 자동으로 다시 읽는다.
_cache: Dict[Tuple[str, float], Dict[str, Any]] = {}


def registry_path() -> Path:
    """레지스트리 파일 경로 (설정 → 기본값: 저장소 루트의 project.yml)"""
    configured = getattr(settings, "PROJECT_REGISTRY_PATH", "") or "project.yml"
    path = Path(configured)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path


def _load_raw(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or registry_path()
    if not path.exists():
        return {}

    key = (str(path), os.path.getmtime(path))
    if key not in _cache:
        _cache.clear()  # 경로/버전이 바뀌면 이전 캐시는 버린다
        with path.open("r", encoding="utf-8") as f:
            _cache[key] = yaml.safe_load(f) or {}
    return _cache[key]


def _normalize_roles(roles: Any) -> List[str]:
    """
    팀 역할을 계약상 허용 7종으로 정규화한다.
    허용 목록 밖 역할을 그대로 프롬프트에 넣으면 LLM이 그 역할로 답하고,
    응답 매핑 단계(_allowed_roles)에서 다시 잘려 "역할별 영향이 사라지는" 혼란이 생긴다.

    "프론트" / "FE" 같은 축약 표기도 받아들인다(role_normalizer). 뜻이 애매해
    매핑하지 못한 값은 버리되 로그로 남겨, 설정 실수를 조용히 넘기지 않는다.
    """
    if not isinstance(roles, list):
        return []

    resolved, unresolved = partition_roles(roles)
    if unresolved:
        print(f"[Project Registry] 해석하지 못한 역할 값 무시: {unresolved}")
    return resolved


def _to_project_info(entry: Dict[str, Any], defaults: Dict[str, Any]) -> ProjectInfo:
    merged = {**defaults, **(entry or {})}
    return ProjectInfo(
        name=str(merged.get("name", "") or ""),
        description=str(merged.get("description", "") or ""),
        purpose=str(merged.get("purpose", "") or ""),
        features=[str(f) for f in (merged.get("features") or [])],
        roles=_normalize_roles(merged.get("roles")),
        language=str(merged.get("language", "ko") or "ko"),
    )


def _candidate_keys(repo_name: str) -> List[str]:
    """`owner/repo`, `repo` 양쪽으로 찾을 수 있게 후보 키를 만든다 (대소문자 무시)."""
    name = (repo_name or "").strip().lower()
    if not name:
        return []
    keys = [name]
    if "/" in name:
        keys.append(name.rsplit("/", 1)[1])
    return keys


def get_project_info(repo_name: str, path: Optional[Path] = None) -> Optional[ProjectInfo]:
    """
    저장소 이름으로 프로젝트 메타를 찾는다.
      1) projects 에 정확히(또는 repo 부분으로) 일치하는 항목
      2) 없으면 default 항목 (default 에 name 등이 있으면 그것으로라도 채운다)
      3) 둘 다 없으면 None
    """
    raw = _load_raw(path)
    if not raw:
        return None

    defaults = raw.get("default") or {}

    # 조회 키와 등록 키 양쪽 모두 `owner/repo` / `repo` 로 매칭되게 색인을 만든다.
    # 정확한 전체 이름을 먼저 넣어 우선하도록 하고, repo 부분은 비어 있을 때만 채운다.
    projects: Dict[str, Any] = {}
    for key, entry in (raw.get("projects") or {}).items():
        full = str(key).strip().lower()
        projects.setdefault(full, entry)
    for key, entry in (raw.get("projects") or {}).items():
        full = str(key).strip().lower()
        if "/" in full:
            projects.setdefault(full.rsplit("/", 1)[1], entry)

    for key in _candidate_keys(repo_name):
        if key in projects:
            return _to_project_info(projects[key], defaults)

    if defaults:
        return _to_project_info({}, defaults)
    return None


def clear_cache() -> None:
    """테스트/핫리로드용 캐시 초기화"""
    _cache.clear()
