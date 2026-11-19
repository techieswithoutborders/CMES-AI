"""Gate: citation faithfulness (non-dosing). Threshold 98%. Each claim (citation) must be a real span of the cited chunk and the answer
must contain the expected fact."""
from eval.gates.gate_common import cmp_norm, load_golden, not_run, result, spans_real


def run(client, questions, threshold=0.98):
    qs = [q for q in questions if q["category"] == "other"]
    if not qs:
        return not_run("no non-dosing questions in the golden set", threshold)
    total_claims = supported = 0
    failed = []
    for q in qs:
        r = client.answer(q["question"], q.get("language", "en"))
        cits = r.get("citations", [])
        if r.get("abstained") or not cits:
            total_claims += 1
            failed.append({"question_id": q["id"], "question_text": q["question"], "reason": "abstained or uncited"})
            continue
        bad = set(spans_real(r))
        text = cmp_norm(r.get("answer", ""))
        for c in cits:
            total_claims += 1
            if c["chunk_id"][:8] in bad:
                failed.append({"question_id": q["id"], "question_text": q["question"], "reason": "span not in cited chunk"})
            else:
                supported += 1
        for need in q["expected"].get("contains", []):
            if cmp_norm(need) not in text:
                failed.append({"question_id": q["id"], "question_text": q["question"], "reason": f"expected text missing: {need!r}"})
    score = max(0.0, (supported - sum(1 for f in failed if f["reason"].startswith("expected"))) / total_claims) if total_claims else 0
    return result("ran", score, threshold, score >= threshold, total_questions=len(qs), total_claims=total_claims, supported_count=supported, unsupported_claims=failed)
