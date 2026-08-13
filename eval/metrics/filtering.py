def compute_filter_metrics(retrieved_ids, filtered_ids, gold_ids):
    gold = set(gold_ids)
    retained_gold = [gid for gid in filtered_ids if gid in gold]

    false_deletion = len([gid for gid in retrieved_ids if gid in gold and gid not in filtered_ids])
    gold_retained_ratio = len(retained_gold) / len(gold) if gold else 1.0

    return {
        "false_deletion": false_deletion,
        "gold_retained_ratio": gold_retained_ratio,
        "filter_ratio": (len(retrieved_ids) - len(filtered_ids)) / len(retrieved_ids) if retrieved_ids else 0.0
    }
