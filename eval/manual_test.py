"""
manual_test.py — judge.py / report.py를 실제 PR 하나로 손으로 테스트해보는 스크립트.

실행 전 .env에 OPENAI_API_KEY를 넣어야 한다.

실행:
    python -m eval.manual_test
(Contextory_AI 폴더 루트에서 실행)
"""

from eval.judge import keyword_judge
from eval.report import build_report, to_text_report, to_json_report

# PR #29 [FEAT] 프로젝트 생성 UI 구현 (OffCourse_FrontEnd)
PR_TITLE = "[FEAT] 프로젝트 생성 UI 구현"
PR_DESCRIPTION = """
프로젝트 생성 3단계 UI를 구현했습니다.
기존 /projects/new 라우트를 유지하고 placeholder 화면을 실제 생성 플로우로 교체했습니다.
실제 백엔드 API/DTO, GitHub OAuth, 프로젝트 생성 API는 연결하지 않고 mock 상태로 구현했습니다.

저장소 연결 확인이 완료되지 않으면 팀 설정 단계로 이동할 수 없습니다.
이메일 직접 초대 기능은 MVP 범위에서 제외했습니다.
단계 전환 시 해당 단계 heading으로 focus 이동합니다.
완료된 이전 단계로 이동 가능하지만, 미래 단계로 직접 이동은 차단합니다.
route 변경 없이 local React state로 3단계 wizard를 구성했습니다.
"""
PR_DIFF = ""  # 실제 diff 없이 description만으로 테스트

# 손으로 골라본 후보 키워드 (일부러 확실한 것/애매한 것/노이즈를 섞어 넣음)
CANDIDATE_KEYWORDS = [
    "저장소 연결 확인이 완료되지 않으면 팀 설정 단계로 이동할 수 없음",  # A(Why)+D(Impact) 기대
    "이메일 직접 초대 기능은 MVP 범위에서 제외",  # B(Scope/Limitation) 기대
    "단계 전환 시 heading으로 focus 이동",  # E(역할: QA/접근성) 관련, A와 엮이는지가 관건
    "route 변경 없이 local React state로 3단계 wizard 구성",  # A(설계 이유) 기대
    "React",  # not_important 기대 (오프라인에서 바로 걸러짐)
    "record-128",  # not_important 기대 (오프라인에서 바로 걸러짐)
]


def main():
    keyword_results = [
        keyword_judge(PR_TITLE, PR_DESCRIPTION, PR_DIFF, keyword)
        for keyword in CANDIDATE_KEYWORDS
    ]

    print("=== 키워드별 판정 결과 ===")
    for result in keyword_results:
        print(result)
        print()

    report = build_report([{"pr_id": 29, "keywords": keyword_results}])

    print(to_text_report(report))
    print()
    print(to_json_report(report))


if __name__ == "__main__":
    main()
