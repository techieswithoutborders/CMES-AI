"""Gate: citation faithfulness (dosing). Threshold 100%.
A dosing/table answer passes only if (1) it is cited, (2) every number+unit in it is in a cited span, (3) the cited spans really occur
in the cited chunks, (4) it contains the expected value and none of the forbidden values (regression: the Day-1/Day-2 table-cell mix-up)."""
from eval.gates.gate_common import cmp_norm, faithful, load_golden, not_run, result, spans_real


def run(client, questions, threshold=1.0):
    qs = [q for q in questions if q["category"] in ("dosing", "table")]
    if not qs:
        return not_run("no dosing/table questions in the golden set", threshold)
    failed, ok = [], 0
    for q in qs:
        r = client.answer(q["question"], q.get("language", "en"))
        reasons = []
        exp = q["expected"]
        text = cmp_norm(r.get("answer", ""))
        if r.get("abstained"):
            reasons.append("abstained on an in-scope question")
        elif not r.get("citations"):
            reasons.append("no citations")
        else:
            bad = faithful(r["answer"], r["citations"])
            if bad:
                reasons.append(f"numbers not in cited span: {bad}")
            if spans_real(r):
                reasons.append("cited span not found in cited chunk")
            if not r.get("verified"):
                reasons.append("server verifier did not confirm")
            for c in exp.get("contains", []):
                if cmp_norm(c) not in text:
                    reasons.append(f"expected text missing: {c!r}")
            for c in exp.get("must_not_contain", []):
                if cmp_norm(c) in text:
                    reasons.append(f"forbidden text present: {c!r}")
        if reasons:
            failed.append({"question_id": q["id"], "question_text": q["question"], "reasons": reasons, "answer": r.get("answer", "")[:300]})
        else:
            ok += 1
    score = ok / len(qs)
    return result("ran", score, threshold, score >= threshold, total_questions=len(qs), passed_count=ok, failed_cases=failed)
