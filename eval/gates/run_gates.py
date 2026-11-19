"""Runs all 8 gates and writes eval_report.json (spec/eval_report_schema.json v1.1).

  python3 -m eval.gates.run_gates --pack build/packs/kb-1.0.0-dev --sources samplefiles/pdf --hardware other \
        --golden eval/golden_sets --output build/eval_report.json [--backend offline|nim|auto]

release_eligible is true ONLY if: every counted gate ran and passed, the golden set is clinician reviewed, hardware is a target box,
and there are no release blockers. A dev run therefore always reports release_eligible=false - by design."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from common.nim import get_backend
from edge.server import App, serve_in_thread
from eval.gates import (test_citation_faithfulness_dosing as g_dose, test_citation_faithfulness_other as g_other, test_latency as g_lat,
                        test_out_of_scope_abstention as g_oos, test_retrieval_recall as g_rec, test_table_fidelity as g_tab, test_update_safety as g_upd)
from eval.gates.gate_common import Client, load_golden
from common.util import SCHEMA_VERSION

COUNTED = ("passed", "ran", "baseline")


def run_all(pack, sources, golden, hardware, backend, previous=None, keys_dir="keys", log=print):
    questions, gmeta = load_golden(golden)
    app = App(backend, state_dir=str(Path(pack).parent / "_eval_state"), pack_dir=str(pack), dev=True, build_dir=str(Path(pack).parent))
    srv, url = serve_in_thread(app)
    client = Client(url)
    gates = {}
    try:
        for name, fn in (("citation_faithfulness_dosing", lambda: g_dose.run(client, questions)),
                         ("citation_faithfulness_other", lambda: g_other.run(client, questions)),
                         ("out_of_scope_abstention", lambda: g_oos.run(client, questions)),
                         ("retrieval_recall_at_10", lambda: g_rec.run(client, questions, previous)),
                         ("table_fidelity", lambda: g_tab.run(pack, sources, backend))):
            log(f"  gate {name} ...")
            gates[name] = fn()
        log("  gate latency ...")
        gates["latency_search_ms"], gates["latency_first_token_ms"] = g_lat.run(client, questions, hardware, generation_enabled=False)
    finally:
        srv.shutdown()
    log("  gate update_safety ...")
    gates["update_safety"] = g_upd.run(keys_dir)

    def counts(g):
        return g["status"] in ("ran", "baseline") or g["status"] in ("not_run",)

    counted = {k: g for k, g in gates.items() if g["status"] not in ("informational", "not_applicable")}
    overall = all(g["status"] in ("ran", "baseline") and g["passed"] for g in counted.values()) and len(counted) >= 6
    blockers = []
    if not gmeta["clinician_reviewed"]:
        blockers.append("golden set is not clinician reviewed (T2)")
    if hardware not in g_lat.TARGETS:
        blockers.append(f"evaluated on '{hardware}', not on target hardware (T1)")
    if any(g["status"] == "baseline" for g in gates.values()):
        blockers.append("retrieval recall has no previous release to compare against (baseline run)")
    for t in gates["table_fidelity"]["details"].get("unresolved_unit_flags", []):
        blockers.append(f"unit discrepancy in source table {t['table_id']} ({t['example']}) awaiting clinician decision")
    n_unrev = sum(1 for c in app.engine.pack.chunks.values() if c["review_status"] in ("unreviewed", "rejected")) if app.engine else 0
    if n_unrev:
        blockers.append(f"{n_unrev} chunks are unreviewed (release pack must have zero unreviewed numeric disagreements and clinician-approved tiers)")
    if app.engine and any(f["kind"] == "parser_reader_disagree" for c in app.engine.pack.chunks.values() for f in (c.get("flags") or [])):
        blockers.append("parser/second-reader numeric disagreements in the clinician queue")
    report = {
        "report_version": SCHEMA_VERSION, "schema_version": SCHEMA_VERSION, "timestamp": datetime.now(timezone.utc).isoformat(),
        "pack_version_tested": app.engine.pack.version, "hardware": {"model": hardware}, "backend": {"name": backend.name, "models": backend.models},
        "golden_set": {"versions": gmeta["versions"], "files": gmeta["files"], "clinician_reviewed": gmeta["clinician_reviewed"], "n_questions": len(questions)},
        "golden_set_version": ",".join(str(v) for v in gmeta["versions"]) or "none", "gates": gates, "overall_pass": bool(overall),
        "release_eligible": bool(overall and not blockers), "release_blockers": blockers,
    }
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--sources", default="samplefiles/pdf")
    ap.add_argument("--golden", default="eval/golden_sets")
    ap.add_argument("--hardware", default="other", choices=["N150", "Pi5_16GB_NVMe", "other"])
    ap.add_argument("--backend", default="auto", choices=["auto", "offline", "nim"])
    ap.add_argument("--previous-report")
    ap.add_argument("--output", default="build/eval_report.json")
    a = ap.parse_args()
    backend, note = get_backend(a.backend)
    prev = json.loads(Path(a.previous_report).read_text()) if a.previous_report and Path(a.previous_report).exists() else None
    rep = run_all(a.pack, a.sources, a.golden, a.hardware, backend, prev)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    for k, g in rep["gates"].items():
        print(f"  {k:32s} {g['status']:14s} score={g['score']} {'PASS' if g['passed'] else 'FAIL'}")
    print(f"overall_pass={rep['overall_pass']} release_eligible={rep['release_eligible']}")
    for b in rep["release_blockers"]:
        print("  blocker:", b)
    sys.exit(0 if rep["overall_pass"] else 1)


if __name__ == "__main__":
    main()
