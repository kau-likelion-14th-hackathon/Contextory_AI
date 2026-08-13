from typing import List, Dict, Any, Optional

def build_grounded_prompt(
    pr_title: str,
    pr_diff: str,
    filtered_contexts: List[Dict[str, Any]],
    repo_contexts: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    필터를 통과한 Evidence(code_review_vectors)와 프로젝트 소스코드 Context(repo_code_vectors)를
    모두 포함하는 Grounded Prompt 생성
    """
    context_blocks = []
    for idx, ctx in enumerate(filtered_contexts, 1):
        context_blocks.append(
            f"[Context {idx}] (Score: {ctx.get('similarity_score', 0):.2f})\n"
            f"Review: {ctx.get('review_comment', '')}\n"
            f"Diff snippet: {ctx.get('pr_diff', '')[:300]}"
        )

    context_str = "\n\n".join(context_blocks)

    repo_blocks = []
    for ctx in (repo_contexts or []):
        repo_blocks.append(
            f"[Project File: {ctx.get('file_path', 'unknown')}] (Score: {ctx.get('similarity_score', 0):.2f})\n"
            f"{ctx.get('source_code', '')[:500]}"
        )

    repo_context_str = "\n\n".join(repo_blocks)

    prompt = f"""You are a senior code reviewer AI. Analyze the following PR and provide code review and risk score.
Strictly base your reasoning on the provided contexts.

Our Project Source Code Context:
{repo_context_str if repo_context_str else "No relevant project file found."}

Past Code Review Evidences:
{context_str if context_str else "No highly relevant past context found."}

PR Title: {pr_title}
PR Diff:
{pr_diff}
"""
    return prompt
