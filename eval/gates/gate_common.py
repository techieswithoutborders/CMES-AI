"""Shared helpers for the eval gates. Design rule: GATES FAIL CLOSED.
A gate that has nothing to measure returns status='not_run', passed=False - never a default 1.0."""
import json
import re
from pathlib import Path

from common.http import request_json
from common.util import extract_quantities, norm_ws

IN_SCOPE = ("dosing", "table", "other")


def cmp_norm(s):
    return norm_ws(s).lower().replace("/ ", "/").replace(" /", "/")


def result(status, score=None, threshold=None, passed=False, **details):
    return {"status": status, "score": None if score is None else round(float(score), 4), "threshold": threshold,
            "passed": bool(passed), "details": details}


def not_run(reason, threshold=None):
    return result("not_run", None, threshold, False, reason=reason)


class Client:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def post(self, path, body):
        st, js = request_json("POST", self.base + path, body, timeout=60)
        return st, js

    def search(self, q, k=10, language="en"):
        return self.post("/search", {"query": q, "language": language, "k": k, "mode": "passages"})[1]

    def answer(self, q, language="en", mode="extractive"):
        return self.post("/answer", {"query": q, "language": language, "mode": mode, "stream": False})[1]


def load_golden(path):
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("golden_*.json"))
    else:
        files = [p] if p.exists() else []
    qs, meta = [], {"clinician_reviewed": True, "versions": [], "files": []}
    for f in files:
        d = json.loads(f.read_text())
        qs += [dict(q, language=q.get("language", d.get("language", "en"))) for q in d["questions"]]
        meta["clinician_reviewed"] &= bool(d.get("clinician_reviewed"))
        meta["versions"].append(d.get("golden_set_version"))
        meta["files"].append(f.name)
    if not qs:
        meta["clinician_reviewed"] = False
    return qs, meta


def faithful(answer_text, citations):
    """Every (number, unit) in the answer must appear in some cited span (unit must match when the answer states one)."""
    span_q = [q for c in citations for q in extract_quantities(c.get("source_text", ""))]
    bad = []
    for n, u in extract_quantities(answer_text):
        if not any(n == sn and (not u or u == su) for sn, su in span_q):
            bad.append(f"{n} {u}".strip())
    return bad


def spans_real(resp):
    """Each citation's source_text must really occur in the passage it cites (checked against the passages the server returned)."""
    texts = {}
    for p in resp.get("passages", []) + resp.get("nearest_documents", []):
        texts[p["chunk_id"]] = cmp_norm(p["text"])
    bad = []
    for c in resp.get("citations", []):
        t = texts.get(c["chunk_id"])
        if t is None or cmp_norm(c.get("source_text", "")) not in t:
            bad.append(c["chunk_id"][:8])
    return bad
