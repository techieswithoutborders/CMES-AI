"""Calibrate the abstention threshold per reranker on the CALIBRATION split of the golden set (held-out TEST split is used by the gate).
Policy: prefer abstaining. If scores overlap, the threshold sits just above the highest out-of-scope score (accepting lost recall)."""
import json

from edge.core import Engine, Pack
from eval.gates.gate_common import IN_SCOPE, load_golden
from indexer.build_pack import set_meta


def calibrate(pack_dir, backend, golden, log=print):
    qs, meta = load_golden(golden)
    qs = [q for q in qs if q.get("split") == "calibration"]
    eng = Engine(Pack(pack_dir), backend)
    eng.pack.calibration = {}  # measure raw scores with no threshold
    ins, oos = [], []
    for q in qs:
        r = eng.search(q["question"], q.get("language", "en"), k=5)
        if r["retrieval"].get("abstain_reason") == "dose_not_in_sources":
            continue  # already handled by the dose-answerability guard; do not let it drag the threshold up
        top = max(r["reranker_scores"]) if r["reranker_scores"] else float("-inf")
        (ins if q["category"] in IN_SCOPE else oos if q["category"] == "oos" else []).append(top)
    if len(ins) < 3 or len(oos) < 3:
        log("  calibration skipped: need >=3 in-scope and >=3 out-of-scope calibration questions")
        return None
    ins.sort()
    p10 = ins[int(0.1 * (len(ins) - 1))]
    mx = max(oos)
    separable = mx < p10
    thr = (mx + p10) / 2 if separable else mx + 1e-3
    cal = {"threshold": round(float(thr), 4), "n_in_scope": len(ins), "n_oos": len(oos), "p10_in_scope": round(float(p10), 4),
           "max_oos": round(float(mx), 4), "separable": bool(separable), "golden_set": meta["versions"]}
    old = json.loads(eng.pack.meta.get("calibration") or "{}")
    old[backend.models["rerank"]] = cal
    set_meta(pack_dir, "calibration", json.dumps(old))
    log(f"  calibrated abstain threshold for {backend.models['rerank']}: {cal['threshold']} (separable={separable})")
    return cal
