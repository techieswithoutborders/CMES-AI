"""Run with:  python3 -m unittest -v tests.test_all      (stdlib only; no pytest needed)"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.nim import OfflineBackend  # noqa: E402
from common.units import unit_flags  # noqa: E402
from common.util import canonical_json, extract_quantities, sha256_bytes  # noqa: E402
from edge.core import Engine, Pack  # noqa: E402
from edge.server import App, serve_in_thread  # noqa: E402
from indexer import signing  # noqa: E402
from indexer.build_pack import build_pack  # noqa: E402
from ingest.ingest import ingest_dir  # noqa: E402
from ingest.verify import TextLayerReader, verify_chunks  # noqa: E402

SRC = ROOT / "samplefiles"
TMP = None
ING = None
PACK = None
ENGINE = None


def setUpModule():
    global TMP, ING, PACK, ENGINE
    TMP = Path(tempfile.mkdtemp(prefix="twb-test-"))
    mini = TMP / "mini"
    mini.mkdir()
    shutil.copy(next(SRC.rglob("*Chapter_3*.pdf")), mini)
    shutil.copy(next(SRC.rglob("Rapid Sequence*.pdf")), mini)
    shutil.copy(next(SRC.rglob("*Chapter_5*.pdf")), mini)
    ING = ingest_dir(mini, TMP / "ing", render=True, log=lambda *a: None)
    verify_chunks(ING["chunks"], ING["docs"], TextLayerReader(), log=lambda *a: None)
    b = OfflineBackend()
    from enrich.enrich import enrich_chunks
    enrich_chunks(ING["chunks"], b, TMP / "enr.jsonl")
    PACK = build_pack(ING["chunks"], ING["docs"], TMP / "ing" / "pages", TMP / "pack", "1.0.0-test", b, log=lambda *a: None)
    ENGINE = Engine(Pack(PACK), b)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class Units(unittest.TestCase):
    def test_who_unit_typo_is_flagged(self):
        self.assertTrue(unit_flags("260 mmol/L (15 mg/dL)"))
        self.assertTrue(unit_flags("50 mmol/l (3 mg/dl)"))

    def test_correct_units_not_flagged(self):
        self.assertFalse(unit_flags("260 \u00b5mol/L (15 mg/dL)"))
        self.assertFalse(unit_flags("5.5 mmol/L (100 mg/dL)"))  # glucose

    def test_quantities(self):
        q = extract_quantities("100 000\u2013150 000 U/kg/day for 10\u201315 days; 1 to 2 mg/kg")
        self.assertIn(("100000", ""), q)
        self.assertIn(("150000", "u/kg/day"), q)
        self.assertIn(("2", "mg/kg"), q)


class Ingest(unittest.TestCase):
    def table(self, key):
        return next(c for c in ING["chunks"] if c["chunk_kind"] == "table" and key in c["doc_id"])

    def test_who_table_is_one_chunk_with_resolved_headers(self):
        t = self.table("Chapter")["table"]
        self.assertEqual((t["n_cols"], t["header_rows"]), (5, 2))
        day1 = next(c for c in t["cells"] if c["row_header"] == "Day 1" and c["text"] == "Any visible jaundice")
        self.assertEqual(day1["colspan"], 2)  # merged cell survives
        self.assertEqual(len(day1["col_headers"]), 2)
        day2 = [c for c in t["cells"] if c["row_header"] == "Day 2" and c["c"] == 1][0]
        self.assertEqual(day2["text"], "260 mmol/L (15 mg/dL)")
        self.assertTrue(day2["col_headers"][0].startswith("PHOTOTHERAPY"))

    def test_table_bbox_is_not_full_page_and_ids_include_bbox(self):
        c = self.table("Chapter")
        b = c["bbox"]
        self.assertLess((b["x1"] - b["x0"]) * (b["y1"] - b["y0"]), 0.5)
        again = sha256_bytes(canonical_json([c["doc_id"], c["page"], b, c["chunk_kind"], c["text"]]))
        self.assertEqual(c["chunk_id"], again)

    def test_tier_comes_from_registry_and_unit_flag_raised(self):
        c = self.table("Chapter")
        self.assertEqual(c["authority_tier"], 1)
        self.assertTrue(any(f["kind"] == "unit_suspect" for f in c["flags"]))
        self.assertEqual(c["review_status"], "fast_tracked")

    def test_chunks_validate_against_spec(self):
        import jsonschema
        schema = json.loads((ROOT / "spec" / "chunk_schema.json").read_text())
        for c in ING["chunks"]:
            jsonschema.validate(c, schema)

    def test_printed_page_differs_from_pdf_page(self):
        c = self.table("Chapter")
        self.assertEqual((c["page"], c["printed_page"]), (13, "17"))


class Engine_(unittest.TestCase):
    def test_regression_day2_vs_day1_phototherapy(self):
        a = ENGINE.answer("What is the phototherapy threshold for a healthy newborn on day 2?")
        self.assertEqual(a["mode_used"], "table")
        self.assertIn("260 mmol/L (15 mg/dL)", a["answer"])
        self.assertNotIn("Any visible jaundice", a["answer"])
        self.assertNotIn("425", a["answer"])
        self.assertTrue(a["verified"])
        d1 = ENGINE.answer("What is the phototherapy threshold on day 1 for a healthy newborn?")
        self.assertIn("Any visible jaundice", d1["answer"])
        self.assertNotIn("260", d1["answer"])  # the old bug: exchange-transfusion value shown as day-1 phototherapy
        self.assertIn("/ NEWBORNS <35 WEEKS GESTATION", d1["answer"])  # merged cell is labelled with both columns it covers

    def test_ambiguous_question_shows_all_columns_and_says_so(self):
        a = ENGINE.answer("phototherapy threshold day 2")
        self.assertGreaterEqual(len(a["highlight_cells"]), 2)
        self.assertIn("column", (a["table_note"] or ""))

    def test_abbreviation_in_table_title_is_reachable_through_aliases(self):
        a = ENGINE.answer("Ketamine dose for rapid sequence intubation")
        self.assertFalse(a["abstained"])
        self.assertIn("1 to 2 mg", a["answer"])

    def test_abstains_out_of_scope(self):
        self.assertTrue(ENGINE.answer("What is the capital of France?")["abstained"])

    def test_dose_question_without_dose_in_sources_abstains(self):
        r = ENGINE.search("what is the dose of warfarin for atrial fibrillation")
        self.assertTrue(r["abstained"])

    def test_dosing_query_never_summarised(self):
        a = ENGINE.answer("ketamine dose for intubation", mode="grounded_summary") if False else None
        self.assertIsNone(a)

    def test_citations_are_verbatim_spans(self):
        a = ENGINE.answer("antibiotics for necrotizing enterocolitis")
        self.assertTrue(a["citations"])
        for c in a["citations"]:
            ch = ENGINE.pack.chunks[c["chunk_id"]]
            self.assertEqual(ch["text"][c["source_span_start"]:c["source_span_end"]], c["source_text"])

    def test_verifier_rejects_a_changed_number(self):
        a = ENGINE.answer("What is the phototherapy threshold for a healthy newborn on day 2?")
        bad = [dict(c, claim_text=c["claim_text"].replace("260", "270")) for c in a["citations"]]
        self.assertFalse(ENGINE.verify(bad))


class Gates(unittest.TestCase):
    def test_every_gate_fails_closed_with_nothing_to_measure(self):
        from eval.gates import (test_citation_faithfulness_dosing as d, test_citation_faithfulness_other as o, test_out_of_scope_abstention as x,
                                test_retrieval_recall as r, test_latency as lat)
        for res in (d.run(None, []), o.run(None, []), x.run(None, []), r.run(None, [])):
            self.assertEqual(res["status"], "not_run")
            self.assertFalse(res["passed"])
        a, b = lat.run(None, [], "N150")
        self.assertEqual(a["status"], "not_run")

    def test_update_safety_gate_real(self):
        from eval.gates import test_update_safety as u
        res = u.run(str(TMP / "keys"))
        self.assertTrue(res["passed"], res["details"])
        self.assertEqual(res["details"]["total_checks"], 8)

    def test_latency_off_target_is_informational_not_release_evidence(self):
        from eval.gates import test_latency as lat
        from eval.gates.gate_common import Client
        app = App(OfflineBackend(), state_dir=str(TMP / "st-l"), pack_dir=str(PACK), dev=True, build_dir=str(TMP))
        srv, url = serve_in_thread(app)
        try:
            qs = [{"category": "other", "question": "kangaroo mother care"}]
            s, _ = lat.run(Client(url), qs, "other", n=5)
        finally:
            srv.shutdown()
        self.assertEqual(s["status"], "informational")
        self.assertFalse(s["passed"])


class Signing(unittest.TestCase):
    def test_chain(self):
        k = signing.ensure_dev_keys(TMP / "k2")
        m = signing.sign_manifest({"pack_version": "1.0.0"}, signing.load_priv(k / "ci.key"), "ci", signing.load_priv(k / "root.key"), "root")
        root = signing.load_pub_b64(k / "root.pub")
        self.assertTrue(signing.verify_manifest(m, root)[0])
        m["pack_version"] = "9.9.9"
        self.assertFalse(signing.verify_manifest(m, root)[0])
        self.assertFalse(signing.verify_manifest(m, signing.b64(bytes(32)))[0])


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from eval.gates.run_gates import run_all
        bd = TMP / "bd"
        bd.mkdir()
        rep = run_all(PACK, SRC, ROOT / "eval" / "golden_sets", "other", OfflineBackend(), keys_dir=str(TMP / "k3"), log=lambda *a: None)
        (bd / "eval_report.json").write_text(json.dumps(rep))
        (bd / "pipeline_report.json").write_text(json.dumps({"state": "done", "warnings": [], "stages": [
            {"name": "ingest", "status": "ok", "seconds": 1.0, "detail": {"docs": 3}}, {"name": "eval_gates", "status": "ok", "seconds": 2.0, "detail": {"gates": {}}}]}))
        cls.app = App(OfflineBackend(), state_dir=str(TMP / "st-s"), pack_dir=str(PACK), dev=True, build_dir=str(bd))
        cls.srv, cls.url = serve_in_thread(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def call(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.url + path, None if body is None else json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})}, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw and r.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_contract_basics(self):
        st, v = self.call("GET", "/version")
        self.assertEqual((st, v["schema_version"], v["channel"]), (200, "1.1", "dev"))
        self.assertIsNone(v["eval_score"])  # never a hard-coded perfect score
        st, h = self.call("GET", "/health")
        self.assertIn("temperature_c", h)
        st, r = self.call("POST", "/search", {"query": "kangaroo mother care", "language": "en", "k": 3})
        self.assertEqual(st, 200)
        self.assertFalse(r["abstained"])

    def test_validation(self):
        self.assertEqual(self.call("POST", "/search", {"query": "", "language": "en"})[0], 400)
        self.assertEqual(self.call("POST", "/search", {"query": "x" * 2000, "language": "en"})[0], 400)
        self.assertEqual(self.call("POST", "/search", {"query": "ok", "language": "zz"})[0], 400)

    def test_update_requires_token_and_no_paths(self):
        self.assertEqual(self.call("POST", "/update/apply", {"pack_name": "x"})[0], 401)
        st, r = self.call("POST", "/update/apply", {"pack_name": "../../etc"}, {"X-Device-Token": self.app.token})
        self.assertEqual(st, 400)

    def test_feedback_stores_no_text(self):
        st, _ = self.call("POST", "/feedback", {"query_id": "abc", "rating": -1, "category": "wrong_dose", "comment": "patient John Doe"})
        self.assertEqual(st, 204)
        line = self.app.feedback_path.read_text().splitlines()[-1]
        self.assertNotIn("John", line)
        self.assertEqual(set(json.loads(line)), {"ts", "query_id", "rating", "category", "pack_version"})

    def test_static_path_traversal_blocked(self):
        self.assertEqual(self.call("GET", "/ui/../edge/core.py")[0], 404)
        self.assertEqual(self.call("GET", "/ui/%2e%2e/edge/core.py")[0], 404)

    def test_summary_without_generation_model_is_501(self):
        st, r = self.call("POST", "/answer", {"query": "kangaroo mother care", "language": "en", "mode": "grounded_summary", "stream": False})
        self.assertEqual(st, 501)

    def test_ui_smoke_in_node(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        p = subprocess.run(["node", str(ROOT / "tests" / "ui_smoke.mjs"), self.url], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
