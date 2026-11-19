"""Edge HTTP service (stdlib only, no installs). Implements spec/openapi.yaml; production target is the same contract on FastAPI.

Run:  python3 -m edge.server --state state --bootstrap build/packs/kb-1.0.0-dev --dev --backend auto
Rule 7: no query or answer text is ever written to disk; /feedback stores only ids, rating and category.
"""
import argparse
import base64
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from common.nim import get_backend
from edge.core import Engine, GenerationUnavailable, Pack
from edge.updater import UpdateError, Updater
from indexer import signing

ROOT = Path(__file__).resolve().parent.parent
LANGS = {"en", "fr", "pt", "sw", "auto"}


class App:
    def __init__(self, backend, state_dir="state", root_pub=None, allow_dev=False, dev=False, build_dir="build", pack_dir=None, host="127.0.0.1", auth_user=None, auth_pass=None, auth_search_user=None, auth_search_pass=None):
        self.backend, self.dev, self.build_dir, self.host = backend, dev, Path(build_dir), host
        self.auth_user, self.auth_pass = auth_user, auth_pass
        self.auth_search_user, self.auth_search_pass = auth_search_user, auth_search_pass
        self.lock = threading.RLock()
        self.started = time.time()
        self.proc = None
        self.feedback_path = Path(state_dir) / "feedback_queue.jsonl"
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        if root_pub is None:
            rp = ROOT / "keys" / "root.pub"
            if not rp.exists():
                if not dev:
                    raise SystemExit("keys/root.pub (pinned root public key) is missing; a production box must ship with it")
                signing.ensure_dev_keys(ROOT / "keys")  # dev only: generates DEV keys
            root_pub = signing.load_pub_b64(rp)
        self.root_pub = root_pub
        self.updater = Updater(state_dir, self.root_pub, allow_dev=allow_dev)
        tok = Path(state_dir) / "device_token"
        env_tok = os.environ.get("TWB_DEVICE_TOKEN")
        if env_tok:
            self.token = env_tok
        else:
            if not tok.exists():
                tok.write_text(secrets.token_urlsafe(9))
            self.token = tok.read_text().strip()
        self.unsigned_pack_dir = pack_dir  # evaluation mode: serve an unfinalised pack directly (never used on a box)
        self.engine = None
        self.reload()

    def reload(self):
        with self.lock:
            if self.unsigned_pack_dir:
                d = Path(self.unsigned_pack_dir)
            else:
                d = self.updater.active_dir()
            self.engine = Engine(Pack(d), self.backend) if d else None
            self.pack_dir = d


def _json_body(h, max_bytes=65536):
    n = int(h.headers.get("Content-Length") or 0)
    if n > max_bytes:
        raise ValueError("request body too large")
    raw = h.rfile.read(n) if n else b""
    return json.loads(raw) if raw else {}


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "twb-edge/1.1"

        def log_message(self, fmt, *args):  # no request logging: rule 7
            pass

        # ------------------------------------------------------- auth ---
        def _auth_challenge(self):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "0")
            self.send_header("WWW-Authenticate", 'Basic realm="twb"')
            self.end_headers()

        def _auth_required(self):
            if not app.auth_user:
                return True  # auth not configured: open access
            auth = self.headers.get("Authorization")
            if not auth or not auth.startswith("Basic "):
                self._auth_challenge()
                return False
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                user, _, pwd = decoded.partition(":")
            except Exception:  # noqa: BLE001
                self._auth_challenge()
                return False
            if user == app.auth_user and pwd == app.auth_pass:
                return "admin"
            if app.auth_search_user and user == app.auth_search_user and pwd == app.auth_search_pass:
                return "search"
            self._auth_challenge()
            return False

        def _send(self, code, obj, ctype="application/json", raw=None):
            body = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _err(self, code, msg, ecode="error"):
            self._send(code, {"error": msg, "code": ecode})

        # ------------------------------------------------------- GET ---
        def do_GET(self):
            level = self._auth_required()
            if not level:
                return
            u = urlparse(self.path)
            p, qs = u.path, parse_qs(u.query)
            try:
                if p in ("/", "/ui"):
                    self.send_response(302)
                    self.send_header("Location", "/ui/")
                    self.end_headers()
                    return
                if p.startswith("/ui/"):
                    return self._static(p[4:] or "index.html")
                if p == "/health":
                    return self._send(200, self._health())
                if p == "/version":
                    return self._send(200, self._version())
                if p.startswith("/doc/"):
                    doc_id = p[len("/doc/"):]
                    page = int((qs.get("page") or ["1"])[0])
                    eng = app.engine
                    r = eng.doc_page(doc_id, page) if eng else None
                    return self._send(200, r) if r else self._err(404, "document page not found", "not_found")
                if p.startswith("/dev/") and app.dev:
                    if p == "/dev/examples":
                        return self._dev_get(p)
                    if level != "admin":
                        return self._err(403, "admin credentials required", "forbidden")
                    return self._dev_get(p)
                self._err(404, "not found", "not_found")
            except ValueError:
                self._err(400, "bad request", "bad_request")

        def _static(self, rel):
            base = (ROOT / "ui").resolve()
            f = (base / rel).resolve()
            if base not in f.parents and f != base or not f.is_file():
                return self._err(404, "not found", "not_found")
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            self._send(200, None, ctype, raw=f.read_bytes())

        def _health(self):
            temp = None
            try:
                temp = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0
            except Exception:  # noqa: BLE001
                pass
            du = shutil.disk_usage(str(Path(app.updater.state).resolve()))
            return {"status": "healthy" if app.engine else "degraded", "disk_free_gb": round(du.free / 1e9, 2), "temperature_c": temp,
                    "uptime_seconds": int(time.time() - app.started), "pack_loaded": bool(app.engine)}

        def _version(self):
            eng = app.engine
            if not eng:
                return {"pack_version": None, "build_date": None, "eval_score": None, "schema_version": "1.1", "channel": None,
                        "release_eligible": False, "signature": {"ok": False, "reason": "no pack loaded"}}
            m = eng.pack.manifest
            sig = {"ok": False, "reason": "unsigned pack (evaluation mode)"}
            if m:
                ok, why = signing.verify_manifest(m, app.root_pub)
                sig = {"ok": ok, "reason": why}
            return {"pack_version": eng.pack.version, "build_date": eng.pack.meta.get("built_at"), "schema_version": "1.1",
                    "channel": eng.pack.channel, "eval_score": (m or {}).get("eval_gate_results"), "release_eligible": bool((m or {}).get("release_eligible")),
                    "signature": sig, "embedding_model": eng.pack.embedding_model, "backend": app.backend.name,
                    "reranker": app.backend.models["rerank"], "counts": {"chunks": len(eng.pack.chunks)}}

        # ------------------------------------------------------ POST ---
        def do_POST(self):
            level = self._auth_required()
            if not level:
                return
            p = urlparse(self.path).path
            try:
                body = _json_body(self)
            except Exception:  # noqa: BLE001
                return self._err(400, "invalid JSON body", "bad_request")
            try:
                if p == "/search":
                    return self._search(body)
                if p == "/answer":
                    return self._answer(body)
                if p == "/feedback":
                    return self._feedback(body)
                if p == "/update/apply":
                    if level != "admin":
                        return self._err(403, "admin credentials required", "forbidden")
                    return self._update(body, rollback=False)
                if p == "/update/rollback":
                    if level != "admin":
                        return self._err(403, "admin credentials required", "forbidden")
                    return self._update(body, rollback=True)
                if p.startswith("/dev/") and app.dev:
                    if level != "admin":
                        return self._err(403, "admin credentials required", "forbidden")
                    return self._dev_post(p, body)
                self._err(404, "not found", "not_found")
            except GenerationUnavailable as e:
                self._err(501, str(e), "generation_unavailable")
            except Exception as e:  # noqa: BLE001
                self._err(500, f"{type(e).__name__}: {str(e)[:200]}", "internal")

        def _check_query(self, body):
            q = body.get("query")
            if not isinstance(q, str) or not (1 <= len(q.strip()) <= 1000):
                self._err(400, "query must be 1-1000 characters", "bad_request")
                return None
            lang = body.get("language", "auto")
            if lang not in LANGS:
                self._err(400, "unsupported language", "bad_request")
                return None
            if not app.engine:
                self._err(503, "no knowledge pack loaded", "no_pack")
                return None
            return q, lang

        def _search(self, b):
            c = self._check_query(b)
            if not c:
                return
            k = b.get("k", 10)
            if not isinstance(k, int) or not 1 <= k <= 20:
                return self._err(400, "k must be 1-20", "bad_request")
            self._send(200, app.engine.search(c[0], c[1], k, b.get("mode", "passages")))

        def _answer(self, b):
            c = self._check_query(b)
            if not c:
                return
            mode = b.get("mode", "extractive")
            if mode not in ("extractive", "grounded_summary", "table"):
                return self._err(400, "bad mode", "bad_request")
            res = app.engine.answer(c[0], c[1], mode)
            if b.get("stream", True):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                w = lambda o: self.wfile.write(("data: " + json.dumps(o, ensure_ascii=False) + "\n\n").encode())  # noqa: E731
                w({"citations": res["citations"]})
                for tok in res["answer"].split(" "):
                    w({"token": tok + " "})
                w({"verified": res["verified"], "query_id": res["query_id"], "abstained": res["abstained"]})
                return
            self._send(200, res)

        def _feedback(self, b):
            if b.get("rating") not in (1, -1) or b.get("category") not in ("accurate", "inaccurate", "missing_info", "wrong_dose", "other") \
                    or not isinstance(b.get("query_id"), str):
                return self._err(400, "invalid feedback", "bad_request")
            rec = {"ts": int(time.time()), "query_id": b["query_id"][:16], "rating": b["rating"], "category": b["category"],
                   "pack_version": app.engine.pack.version if app.engine else None}  # comment text intentionally dropped
            with app.lock, open(app.feedback_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            self.send_response(204)
            self.end_headers()

        def _update(self, b, rollback):
            tok = self.headers.get("X-Device-Token", "")
            if not secrets.compare_digest(tok, app.token):
                return self._err(401, "missing or invalid X-Device-Token", "unauthorized")
            try:
                with app.lock:
                    res = app.updater.rollback() if rollback else app.updater.apply_incoming(b.get("pack_name", ""))
                    app.unsigned_pack_dir = None
                    app.reload()
                self._send(200, {"success": True, "message": "rolled back" if rollback else "update applied", **res})
            except UpdateError as e:
                self._send(400, {"success": False, "message": str(e), "new_version": None, "rollback_available": bool(app.updater.status()["previous"])})

        # -------------------------------------------------------- dev ---
        def _read_json(self, path):
            try:
                return json.loads(Path(path).read_text())
            except Exception:  # noqa: BLE001
                return None

        def _dev_get(self, p):
            if p == "/dev/status":
                eng = app.engine
                return self._send(200, {
                    "pipeline_report": self._read_json(app.build_dir / "pipeline_report.json"), "eval_report": self._read_json(app.build_dir / "eval_report.json"),
                    "manifest": eng.pack.manifest if eng else None, "version": self._version(), "updater": app.updater.status(),
                    "backend": {"name": app.backend.name, "models": app.backend.models, "can_generate": app.backend.can_generate},
                    "nvidia_key_set": bool(os.environ.get("NVIDIA_API_KEY")),
                    "device_token": app.token if app.host in ("127.0.0.1", "localhost") else None,
                    "pipeline_running": bool(app.proc and app.proc.poll() is None)})
            if p == "/dev/examples":
                g = self._read_json(ROOT / "eval" / "golden_sets" / "golden_en.dev.json") or {"questions": []}
                return self._send(200, [{"id": q["id"], "category": q["category"], "question": q["question"]} for q in g["questions"]])
            if p == "/dev/pipeline/log":
                lp = app.build_dir / "pipeline.log"
                lines = lp.read_text().splitlines()[-200:] if lp.exists() else []
                return self._send(200, {"running": bool(app.proc and app.proc.poll() is None), "lines": lines})
            self._err(404, "not found", "not_found")

        def _dev_post(self, p, b):
            if p == "/dev/nim-check":
                try:
                    from common.nim import NimBackend
                    return self._send(200, NimBackend().live_check())
                except Exception as e:  # noqa: BLE001
                    return self._send(200, {"backend": "nim", "ok": False, "error": str(e)[:300]})
            if p == "/dev/pipeline/start":
                if app.proc and app.proc.poll() is None:
                    return self._err(409, "pipeline already running", "busy")
                mode = b.get("mode", "auto")
                if mode not in ("auto", "offline", "nim"):
                    return self._err(400, "bad mode", "bad_request")
                cmd = [sys.executable, "-m", "pipeline.run", "--mode", mode, "--out", str(app.build_dir), "--incoming", str(app.updater.state / "incoming")]
                if isinstance(b.get("max_pdfs"), int):
                    cmd += ["--max-pdfs", str(b["max_pdfs"])]
                if isinstance(b.get("enrich_limit"), int):
                    cmd += ["--enrich-limit", str(b["enrich_limit"])]
                app.build_dir.mkdir(parents=True, exist_ok=True)
                logf = open(app.build_dir / "pipeline.log", "w")
                app.proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT)
                return self._send(200, {"started": True})
            self._err(404, "not found", "not_found")

    return H


def serve(app, host="127.0.0.1", port=8080):
    srv = ThreadingHTTPServer((host, port), make_handler(app))
    return srv


def serve_in_thread(app, port=0):
    srv = serve(app, "127.0.0.1", port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="state")
    ap.add_argument("--bootstrap", help="pack directory to install into slot A on first run")
    ap.add_argument("--build", default="build")
    ap.add_argument("--backend", default="auto", choices=["auto", "offline", "nim"])
    ap.add_argument("--dev", action="store_true", help="enable /dev/* endpoints and accept dev-channel packs")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--auth-user", default=None, help="HTTP Basic Auth admin username (enables password protection)")
    ap.add_argument("--auth-pass", default=None, help="HTTP Basic Auth admin password")
    ap.add_argument("--auth-search-user", default=None, help="HTTP Basic Auth search-only username")
    ap.add_argument("--auth-search-pass", default=None, help="HTTP Basic Auth search-only password")
    a = ap.parse_args()
    backend, note = get_backend(a.backend)
    app = App(backend, a.state, allow_dev=a.dev, dev=a.dev, build_dir=a.build, host=a.host,
              auth_user=a.auth_user, auth_pass=a.auth_pass,
              auth_search_user=a.auth_search_user, auth_search_pass=a.auth_search_pass)
    if a.bootstrap and not app.updater.current():
        print("installing bootstrap pack:", app.updater.install(a.bootstrap))
        app.reload()
    print(f"backend: {backend.name} ({note})")
    print(f"pack: {app.engine.pack.version if app.engine else 'NONE - run `python3 -m pipeline.run` then apply from the Update tab'}")
    print(f"device token (for /update/apply): {app.token}")
    print(f"UI: http://{a.host}:{a.port}/ui/")
    serve(app, a.host, a.port).serve_forever()


if __name__ == "__main__":
    main()
