from typing import List, Dict, Any, Tuple

def filter_contexts(contexts: List[Dict[str, Any]], sim_threshold: float = 0.5) -> Tuple[List[Dict[str, Any]], float]:
    """
    Context Filter Agent: 질문과 무관한 Chunk 제거 및 Top-1 보존
    Returns: (filtered_contexts, filter_ratio)
    """
    if not contexts:
        return [], 0.0

    total_count = len(contexts)
    top_1 = contexts[0]

    filtered = [c for c in contexts if c.get("similarity_score", 0.0) >= sim_threshold]

    if top_1 not in filtered:
        filtered.insert(0, top_1)

    filter_ratio = (total_count - len(filtered)) / total_count if total_count > 0 else 0.0
    return filtered, filter_ratio
