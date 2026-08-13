from typing import Callable, List, Dict, Any

def evaluate_reference_free(
    questions: List[Dict[str, Any]],
    retrieve_fn: Callable,
    filter_fn: Callable = None,
    generate_fn: Callable = None
) -> Dict[str, Any]:
    """
    Reference-Free Evaluation Runner
    """
    results = []
    for q in questions:
        raw_contexts = retrieve_fn(q["query"])
        filtered_contexts = filter_fn(raw_contexts) if filter_fn else raw_contexts

        results.append({
            "question": q["query"],
            "raw_count": len(raw_contexts),
            "filtered_count": len(filtered_contexts)
        })
    return {"summary": "Reference-Free Eval Finished", "details": results}
