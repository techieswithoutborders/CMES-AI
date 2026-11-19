"""Protocol-level test of the NVIDIA NIM path against a LOCAL FAKE server (stdlib). It proves our request/response handling,
batching, retries, JSON parsing and the whole `pipeline.run --mode nim` plumbing. It does NOT prove NVIDIA's models or live API
behaviour - run `python3 -m pipeline.run --live-check` with a real key for that.
   python3 -m unittest -v tests.test_nim_mock"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEEN = {"auth": set(), "paths": [], "flaky": 0}


def vec(text, dim=64):
    v = np.zeros(dim)
    for w in re.findall(r"\w+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


class FakeNim(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN["auth"].add(self.headers.get("Authorization"))
        SEEN["paths"].append(self.path)
        if self.path.endswith("/embeddings"):
            assert body["input_type"] in ("query", "passage") and body["truncate"] == "END" and len(body["input"]) <= 32
            if SEEN["flaky"] == 0:  # first call fails once: client must retry
                SEEN["flaky"] = 1
                self.send_response(503)
                self.end_headers()
                return
            data = [{"index": i, "embedding": vec(t)} for i, t in reversed(list(enumerate(body["input"])))]  # out of order on purpose
            return self._ok({"data": data})
        if self.path.endswith("/reranking"):
            q = set(re.findall(r"\w+", body["query"]["text"].lower()))
            r = [{"index": i, "logit": float(len(q & set(re.findall(r"\w+", p["text"].lower()))))} for i, p in enumerate(body["passages"])]
            return self._ok({"rankings": sorted(r, key=lambda x: -x["logit"])})
        if self.path.endswith("/chat/completions"):
            content = body["messages"][0]["content"]
            if isinstance(content, list):  # vision
                assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
                return self._ok({"choices": [{"message": {"content": "| a | 999 |\n| b | 888 |"}}]})
            if "SEARCH METADATA" in content:
                return self._ok({"choices": [{"message": {"content": json.dumps({"hypothetical_questions": ["What is this about 12345?", "Why?"], "local_aliases": ["alias one"], "glossary_terms": ["term"]})}}]})
            return self._ok({"choices": [{"message": {"content": "ok"}}]})
        self.send_response(404)
        self.end_headers()

    def _ok(self, obj):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Mock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeNim)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        port = cls.srv.server_address[1]
        cls.env = {"NVIDIA_API_KEY": "nvapi-TEST", "NIM_BASE_URL": f"http://127.0.0.1:{port}/v1", "NIM_RERANK_URL": f"http://127.0.0.1:{port}/rerank/reranking"}
        os.environ.update(cls.env)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for k in cls.env:
            os.environ.pop(k, None)

    def test_backend_calls(self):
        from common.nim import NimBackend
        b = NimBackend()
        v = b.embed([f"text {i}" for i in range(70)], "passage")  # 3 batches, retry once, reorder
        self.assertEqual(v.shape, (70, 64))
        np.testing.assert_allclose(v[5], np.asarray(vec("text 5")), atol=1e-5)  # index order restored
        sc = b.rerank("phototherapy day", ["phototherapy day two", "unrelated words"])
        self.assertGreater(sc[0], sc[1])
        self.assertEqual(b.chat([{"role": "user", "content": "hi"}]), "ok")
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (2000, 2600), "white").save(buf, "PNG")
        self.assertIn("999", b.vision(buf.getvalue(), "transcribe"))
        self.assertEqual(SEEN["auth"], {"Bearer nvapi-TEST"})

    def test_live_check_shape(self):
        from common.nim import NimBackend
        r = NimBackend().live_check()
        self.assertTrue(r["ok"], r)
        self.assertEqual(set(r["checks"]), {"embedding", "rerank", "chat", "vision"})

    def test_enrichment_drops_numbers_not_in_passage_and_caches(self):
        from common.nim import NimBackend
        from enrich.enrich import enrich_chunks
        b = NimBackend()
        c = [{"chunk_id": "a" * 64, "chunk_kind": "text", "section_path": ["S"], "text": "Kangaroo care for newborns."}]
        cache = Path(tempfile.mkdtemp()) / "c.jsonl"
        st = enrich_chunks(c, b, cache)
        self.assertEqual(st["model_calls"], 1)
        self.assertEqual(c[0]["enrichment"]["hypothetical_questions"], ["Why?"])  # '12345' is not in the passage -> dropped
        st2 = enrich_chunks(c, b, cache)
        self.assertEqual((st2["model_calls"], st2["cache_hits"]), (0, 1))

    def test_whole_pipeline_in_nim_mode(self):
        out = Path(tempfile.mkdtemp())
        env = dict(os.environ, **self.env)
        p = subprocess.run([sys.executable, "-m", "pipeline.run", "--mode", "nim", "--out", str(out / "b"), "--incoming", str(out / "inc"), "--keys", str(out / "k"),
                            "--enrich-limit", "3"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=170)
        self.assertEqual(p.returncode, 0, p.stdout[-1500:] + p.stderr[-1500:])
        rep = json.loads((out / "b" / "pipeline_report.json").read_text())
        self.assertEqual(rep["state"], "done")
        self.assertEqual(rep["backend"]["name"], "nim")
        names = {s["name"]: s for s in rep["stages"]}
        self.assertTrue(names["backend"]["detail"]["live_check"]["ok"])
        self.assertIn("nim-vision", names["verify_numbers"]["detail"]["reader"])
        # the fake vision model returns numbers that are not on the page -> every checked page must be a disagreement queued for a clinician
        self.assertGreater(names["verify_numbers"]["detail"]["disagree"], 0)
        self.assertEqual(names["enrich"]["detail"]["model_calls"], 3)
        ev = json.loads((out / "b" / "eval_report.json").read_text())
        self.assertEqual(ev["backend"]["name"], "nim")
        self.assertFalse(ev["release_eligible"])
        self.assertTrue(any("parser/second-reader" in b for b in ev["release_blockers"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
