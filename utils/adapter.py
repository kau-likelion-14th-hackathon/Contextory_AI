import difflib
import pandas as pd

def _generate_diff(before_code: str, after_code: str) -> str:
    if not before_code or not after_code:
        return ""
    before_lines = str(before_code).splitlines(keepends=True)
    after_lines = str(after_code).splitlines(keepends=True)
    diff = difflib.unified_diff(before_lines, after_lines, fromfile='before', tofile='after')
    return "".join(diff)


def adapt_code_review_gh(df: pd.DataFrame) -> pd.DataFrame:
    """1. code_review_gh: 15자 미만 단답형 및 극단적으로 긴 코드 컷오프"""
    # 15자 이상 코멘트만 필터링
    filtered_df = df[df['code_review_comment'].str.strip().str.len() >= 15].copy()
    
    # 길이가 너무 긴 코드/Diff 컷오프 (Context Window 보호, 예: 4000자 이하)
    filtered_df = filtered_df[filtered_df['diff_hunk'].str.len() <= 4000]
    
    adapted_df = pd.DataFrame()
    adapted_df['source_code'] = filtered_df['diff_hunk']
    adapted_df['pr_diff'] = filtered_df['diff_hunk']
    adapted_df['review_comment'] = filtered_df['code_review_comment']
    adapted_df['has_issue'] = True
    return adapted_df


def adapt_contextual_code_review(df: pd.DataFrame) -> pd.DataFrame:
    """2. contextual_code_review: done, fixed 등 단답형 10% 강하게 필터링"""
    # 15자 이상 코멘트만 살림 ('done', 'fixed' 완전 제거)
    filtered_df = df[df['comment'].str.strip().str.len() >= 15].copy()
    
    adapted_df = pd.DataFrame()
    adapted_df['source_code'] = filtered_df['method_body']
    adapted_df['pr_diff'] = [
        _generate_diff(b, a) 
        for b, a in zip(filtered_df['method_body'], filtered_df['method_body_after'])
    ]
    adapted_df['review_comment'] = filtered_df['comment']
    adapted_df['has_issue'] = True
    return adapted_df


def adapt_codereviewer(df: pd.DataFrame) -> pd.DataFrame:
    """3. codereviewer: 고품질 데이터셋이므로 6자 이하 극단적 노이즈만 필터링"""
    # 'Is this used?' (13자) 같은 유용한 짧은 리뷰도 살리기 위해 6자 이상으로 보수적 적용
    filtered_df = df[df['msg'].str.strip().str.len() >= 6].copy()
    
    adapted_df = pd.DataFrame()
    adapted_df['source_code'] = filtered_df['oldf']
    adapted_df['pr_diff'] = filtered_df['patch']
    adapted_df['review_comment'] = filtered_df['msg']
    adapted_df['has_issue'] = filtered_df['y'].apply(lambda x: True if x == 1 else False)
    return adapted_df