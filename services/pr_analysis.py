"""PR 분석 유스케이스.

트랜잭션 경계와 상태 전이 검증을 담당합니다.
"""

from __future__ import annotations

from core.exceptions import InvalidStateTransitionError, PermissionDeniedError
from core.logging import get_logger
from models.enums import PR_ANALYSIS_TRANSITIONS, PrAnalysisStatus
from models.schemas import AnalysisResultDTO, ProjectRecordDetailDTO
from repositories.project_member import ProjectMemberRepository
from repositories.project_record import ProjectRecordRepository
from repositories.pull_request import PullRequestRepository

logger = get_logger(__name__)


def assert_pr_transition(current: PrAnalysisStatus, target: PrAnalysisStatus) -> None:
    """허용되지 않은 상태 전이를 막습니다."""
    allowed = PR_ANALYSIS_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidStateTransitionError(
            f"PR 분석 상태를 {current.value} 에서 {target.value} 로 바꿀 수 없습니다.",
            detail={"current": current.value, "target": target.value},
        )


class PrAnalysisService:
    def __init__(
        self,
        prs: PullRequestRepository,
        records: ProjectRecordRepository,
        members: ProjectMemberRepository,
        llm: "LlmClient",  # noqa: F821 - services/llm_client.py 참조
    ) -> None:
        self.prs = prs
        self.records = records
        self.members = members
        self.llm = llm

    def analyze(self, project_id: int, pr_id: int, user_id: int) -> ProjectRecordDetailDTO:
        """PR 을 분석해 draft 기록을 만듭니다.

        절차:
          1) 소속/권한 검증 (members.find 로 프로젝트 멤버인지 확인)
          2) PR·diff 로드 (다른 프로젝트의 PR 이면 PermissionDeniedError)
          3) analysis_status: pending -> analyzing (전이 검증 후)
          4) LLM 호출 -> JSON 파싱 -> AnalysisResultDTO
             파싱 실패 시 LlmResponseError 를 잡아 status=failed 로 기록
          5) ProjectRecord(draft) 를 한 트랜잭션으로 저장
          6) analysis_status: analyzing -> needs_review
        """
        raise NotImplementedError

    def reanalyze(self, project_id: int, pr_id: int, user_id: int) -> ProjectRecordDetailDTO:
        """이미 분석된 PR 을 다시 분석합니다.

        기존 승인 기록(approval_status=approved)은 보존하고 새 draft 를 추가합니다.
        """
        raise NotImplementedError

    def _assert_member(self, project_id: int, user_id: int) -> None:
        """프로젝트 멤버가 아니면 접근을 막습니다."""
        if self.members.find(project_id, user_id) is None:
            raise PermissionDeniedError("이 프로젝트에 접근할 권한이 없습니다.")

    def _build_analysis_input(self, pr_id: int) -> dict:
        """diff 전처리. 불필요한 변경(락 파일 등)을 제외하고 길이를 제한합니다.

        core/constants.py 의 EXCLUDED_PATH_PATTERNS, MAX_DIFF_CHARS 를 사용합니다.
        """
        raise NotImplementedError

    def _persist_result(
        self, project_id: int, pr_id: int, result: AnalysisResultDTO
    ) -> ProjectRecordDetailDTO:
        """분석 결과를 기록으로 저장하고 커밋합니다."""
        raise NotImplementedError
