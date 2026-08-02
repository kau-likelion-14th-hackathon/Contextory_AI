"""enum 과 상태 전이 규칙 테스트 (스텁)."""

from __future__ import annotations

import pytest

from models.enums import (
    PR_ANALYSIS_TRANSITIONS,
    ApprovalStatus,
    PrAnalysisStatus,
)


def test_pr_transition_allows_pending_to_analyzing() -> None:
    assert PrAnalysisStatus.ANALYZING in PR_ANALYSIS_TRANSITIONS[PrAnalysisStatus.PENDING]


def test_pr_transition_blocks_pending_to_approved() -> None:
    assert PrAnalysisStatus.APPROVED not in PR_ANALYSIS_TRANSITIONS[PrAnalysisStatus.PENDING]


@pytest.mark.skip(reason="미구현")
def test_approval_transition_from_approved_is_terminal() -> None:
    raise NotImplementedError
