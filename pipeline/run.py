"""End-to-end test pipeline: ingest -> second-reader verify -> enrich -> embed/index -> calibrate -> eval gates -> sign (dev) -> drop in incoming/.

  python3 -m pipeline.run --mode auto      # NVIDIA NIM if NVIDIA_API_KEY is set and reachable, else offline stand-ins
  python3 -m pipeline.run --mode nim       # require NIM (fails loudly)
  python3 -m pipeline.run --live-check     # call each NIM model once and report

Writes build/pipeline_report.json after EVERY stage so the front end can show live progress.
NIM roles: embedding (corpus + query), rerank (abstention scores), vision (T4 page re-read), chat (enrichment). All build-time except
query-side embedding/rerank in this TEST harness; production runs those on the box with open weights (ARCHITECTURE.md ADR-6)."""
import argparse
import json
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from common.nim import NimBackend, get_backend
from eval.calibrate import calibrate
from eval.gates.run_gates import run_all
from eval.make_dev_golden import build as build_golden
from indexer.build_pack import build_pack, finalize_pack
from ingest.ingest import ingest_dir
from ingest.verify import get_reader, verify_chunks
from enrich.enrich import enrich_chunks


class Report:
    def __init__(self, path, mode):
        self.path = Path(path)
        self.d = {"state": "running", "started": datetime.now(timezone.utc).isoformat(), "mode_requested": mode, "backend": None, "stages": [],
                  "warnings": [], "outputs": {}}
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, indent=1, ensure_ascii=False))
        tmp.replace(self.path)

    def stage(self, name):
        rec = {"name": name, "status": "running", "seconds": None, "detail": {}}
        self.d["stages"].append(rec)
        self.save()
        return rec, time.time()

    def done(self, rec, t0, status="ok", **detail):
        rec["status"], rec["seconds"], rec["detail"] = status, round(time.time() - t0, 2), detail
        self.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", choices=["auto", "offline", "nim"])
    ap.add_argument("--src", default="samplefiles", help="folder with source PDFs")
    ap.add_argument("--out", default="build")
    ap.add_argument("--incoming", default="state/incoming")
    ap.add_argument("--keys", default="keys")
    ap.add_argument("--max-pdfs", type=int)
    ap.add_argument("--enrich-limit", type=int, default=150, help="max NIM chat calls for enrichment (cost cap)")
    ap.add_argument("--hardware", default="other", choices=["N150", "Pi5_16GB_NVMe", "other"])
    ap.add_argument("--live-check", action="store_true")
    a = ap.parse_args()

    if a.live_check:
        res = NimBackend().live_check()
        print(json.dumps(res, indent=1))
        return 0 if res.get("ok") else 1

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = Report(out / "pipeline_report.json", a.mode)
    log = lambda *x: print(*x, flush=True)  # noqa: E731
    try:
        rec, t0 = rep.stage("backend")
        backend, note = get_backend(a.mode)
        rep.d["backend"] = {"name": backend.name, "models": backend.models, "note": note}
        if backend.name == "offline" and a.mode != "offline":
            rep.d["warnings"].append(note)
        detail = {"backend": backend.name, "note": note}
        if backend.name == "nim":
            chk = backend.live_check()
            detail["live_check"] = chk
            if not chk["ok"]:
                rep.d["warnings"].append("one or more NIM models failed the live check: " + ", ".join(k for k, v in chk["checks"].items() if not v["ok"]))
        rep.done(rec, t0, **detail)
        log(f"backend: {backend.name} - {note}")

        rec, t0 = rep.stage("ingest")
        ing = ingest_dir(a.src, out / "ingest", a.max_pdfs, render=True, log=log)
        chunks, docs = ing["chunks"], ing["docs"]
        tables = [c for c in chunks if c["chunk_kind"] == "table"]
        rep.done(rec, t0, docs=len(docs), chunks=len(chunks), tables=len(tables), excluded=ing["excluded"],
                 unit_flags=sum(1 for c in chunks for f in c["flags"] if f["kind"] == "unit_suspect"),
                 tier_counts={str(t): sum(1 for c in chunks if c["authority_tier"] == t) for t in (1, 2, 3, 4)},
                 languages=sorted({c["language"] for c in chunks}), warnings=[w for d in docs for w in d["warnings"]][:20])

        rec, t0 = rep.stage("verify_numbers")
        reader = get_reader(backend, "auto")
        vs = verify_chunks(chunks, docs, reader, log=log)
        rep.done(rec, t0, **vs, review_status={k: sum(1 for c in chunks if c["review_status"] == k) for k in ("unreviewed", "auto_approved", "fast_tracked")})
        log(f"verify: {vs}")

        rec, t0 = rep.stage("enrich")
        es = enrich_chunks(chunks, backend, out / "cache" / "enrich.jsonl", limit=a.enrich_limit, log=log)
        rep.done(rec, t0, **es)

        rec, t0 = rep.stage("golden_dev_set")
        gold = build_golden(out / "ingest" / "chunks.jsonl", "eval/golden_sets/golden_en.dev.json")
        rep.done(rec, t0, questions=len(gold["questions"]), clinician_reviewed=False)

        cnt = out / "pack_counter.txt"
        n = int(cnt.read_text()) + 1 if cnt.exists() else 1
        cnt.write_text(str(n))
        version = f"1.0.{n}-dev"
        rec, t0 = rep.stage("embed_and_index")
        pack = build_pack(chunks, docs, out / "ingest" / "pages", out / "packs" / f"kb-{version}", version, backend, "dev", log=log)
        rep.done(rec, t0, pack=str(pack), embedding_model=backend.models["embedding"], pack_mb=round(sum(f.stat().st_size for f in pack.rglob("*") if f.is_file()) / 1e6, 1))

        rec, t0 = rep.stage("calibrate_abstention")
        cal = calibrate(pack, backend, "eval/golden_sets", log=log)
        rep.done(rec, t0, "ok" if cal else "skipped", calibration=cal)

        rec, t0 = rep.stage("eval_gates")
        report = run_all(pack, a.src, "eval/golden_sets", a.hardware, backend, keys_dir=a.keys, log=log)
        (out / "eval_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
        rep.done(rec, t0, "ok" if report["overall_pass"] else "failed_gates", overall_pass=report["overall_pass"], release_eligible=report["release_eligible"],
                 gates={k: {"status": g["status"], "score": g["score"], "passed": g["passed"]} for k, g in report["gates"].items()},
                 blockers=report["release_blockers"])

        rec, t0 = rep.stage("sign_and_stage")
        signed = finalize_pack(pack, report, "dev", a.keys, log=log)
        inc = Path(a.incoming)
        inc.mkdir(parents=True, exist_ok=True)
        dest = inc / pack.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(pack, dest)
        rep.d["outputs"] = {"pack": str(pack), "incoming": str(dest), "eval_report": str(out / "eval_report.json"), "version": version}
        rep.done(rec, t0, channel="dev", build_hash=signed["build_hash"][:16], incoming=str(dest))
        rep.d["state"] = "done"
        rep.d["finished"] = datetime.now(timezone.utc).isoformat()
        rep.save()
        log(f"done: pack {version} staged in {dest}; overall_pass={report['overall_pass']} release_eligible={report['release_eligible']}")
        return 0
    except Exception as e:  # noqa: BLE001
        rep.d["state"] = "error"
        rep.d["error"] = f"{type(e).__name__}: {e}"
        for s in rep.d["stages"]:
            if s["status"] == "running":
                s["status"], s["detail"] = "error", {"error": str(e)[:300]}
        rep.save()
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
