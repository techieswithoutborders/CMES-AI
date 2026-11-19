"""Gate: retrieval recall@10 per language. Pass = no drop versus the previous report. With no previous report it records a BASELINE
(status 'baseline'); a baseline can never be the final release evidence on its own - release_eligible still needs the other conditions."""
from collections import defaultdict

from eval.gates.gate_common import IN_SCOPE, cmp_norm, not_run, result


def run(client, questions, previous=None):
    qs = [q for q in questions if q["category"] in IN_SCOPE and q["expected"].get("recall_snippet")]
    if not qs:
        return not_run("no in-scope questions with a recall snippet")
    per = defaultdict(lambda: [0, 0])
    misses = []
    for q in qs:
        r = client.search(q["question"], 10, q.get("language", "en"))
        snip = cmp_norm(q["expected"]["recall_snippet"])
        hit = any(snip in cmp_norm(p["text"]) for p in r.get("passages", []))
        per[q.get("language", "en")][1] += 1
        per[q.get("language", "en")][0] += int(hit)
        if not hit:
            misses.append({"question_id": q["id"], "question_text": q["question"]})
    scores = {l: a / b for l, (a, b) in per.items()}
    overall = sum(a for a, _ in per.values()) / sum(b for _, b in per.values())
    prev = ((previous or {}).get("gates", {}).get("retrieval_recall_at_10") or {}).get("details", {}).get("per_language")
    if prev:
        drops = {l: (prev[l], s) for l, s in scores.items() if l in prev and s < prev[l] - 1e-9}
        return result("ran", overall, None, not drops, per_language=scores, misses=misses, dropped_vs_previous=drops)
    return result("baseline", overall, None, True, per_language=scores, misses=misses, note="no previous report: this run is the baseline")
