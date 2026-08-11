def compute_recall_at_k(retrieved_ids, gold_ids, k=5):
    retrieved_k = set(retrieved_ids[:k])
    gold = set(gold_ids)
    if not gold:
        return 0.0
    return len(retrieved_k.intersection(gold)) / len(gold)

def compute_mrr(retrieved_ids, gold_ids):
    gold = set(gold_ids)
    for rank, r_id in enumerate(retrieved_ids, 1):
        if r_id in gold:
            return 1.0 / rank
    return 0.0
