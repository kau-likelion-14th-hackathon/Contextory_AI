from typing import List, Dict, Any

def build_grounded_prompt(pr_title: str, pr_diff: str, filtered_contexts: List[Dict[str, Any]]) -> str:
    """
    필터를 통과한 Evidence만 포함하는 Grounded Prompt 생성
    """
    context_blocks = []
    for idx, ctx in enumerate(filtered_contexts, 1):
        context_blocks.append(
            f"[Context {idx}] (Score: {ctx.get('similarity_score', 0):.2f})\n"
            f"Review: {ctx.get('review_comment', '')}\n"
            f"Diff snippet: {ctx.get('pr_diff', '')[:300]}"
        )

    context_str = "\n\n".join(context_blocks)

    prompt = f"""You are a senior code reviewer AI. Analyze the following PR and provide code review and risk score.
Strictly base your reasoning on the provided contexts.

Contexts:
{context_str if context_str else "No highly relevant past context found."}

PR Title: {pr_title}
PR Diff:
{pr_diff}
"""
    return prompt
