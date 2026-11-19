"""Model backends for the build pipeline and the test edge service.

NimBackend    -> NVIDIA hosted NIMs on build.nvidia.com (needs NVIDIA_API_KEY and internet)
OfflineBackend-> deterministic, dependency-light stand-ins so the whole pipeline runs with no network

IMPORTANT (architecture): rule 5 says the field box never needs the internet. NIM is therefore a
*cloud build-time* dependency (parse cross-check, enrichment, corpus embeddings) and a *test-harness*
convenience for query-time embedding/rerank. Production query-side embedding and reranking must run
on the box with open-weight models of the SAME family as the pack's embeddings (see ARCHITECTURE.md, ADR-6).
"""
import base64
import io
import math
import os
import re
import time

import numpy as np

from common.http import request_json
from common.util import tokens


class BackendUnavailable(Exception):
    pass


def _stem(t):
    if len(t) > 5 and t.endswith("ies"):
        return t[:-3] + "y"
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def stems(text):
    return [_stem(t) for t in tokens(text)]


# ------------------------------------------------------------------ offline ----
class OfflineBackend:
    name = "offline"
    dim = 512
    models = {"embedding": "offline-hash-512", "rerank": "offline-lexical-idf", "chat": None, "vision": None}
    can_generate = False
    can_vision = False

    def __init__(self):
        self.df = {}
        self.n_docs = 1

    def configure_corpus(self, doc_freq, n_docs):
        self.df = doc_freq
        self.n_docs = max(1, n_docs)

    def _idf(self, stem):
        return math.log(1 + (self.n_docs + 1) / (self.df.get(stem, 0) + 0.5))

    def embed(self, texts, input_type="passage"):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            ss = stems(t)
            feats = list(ss) + [a + "_" + b for a, b in zip(ss, ss[1:])]
            for f in feats:
                h = int.from_bytes(__import__("hashlib").blake2b(f.encode(), digest_size=8).digest(), "big")
                out[i, h % self.dim] += 1.0 if h & (1 << 40) else -1.0 if h & (1 << 41) else 0.7
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out

    def rerank(self, query, passages):
        q = stems(query)
        if not q:
            return [0.0] * len(passages)
        w = {s: self._idf(s) for s in set(q)}
        tot = sum(w.values()) or 1.0
        qbi = set(zip(q, q[1:]))
        scores = []
        for p in passages:
            ps = stems(p)
            pset = set(ps)
            cov = sum(w[s] for s in w if s in pset) / tot
            pbi = set(zip(ps, ps[1:]))
            bonus = 0.15 * (len(qbi & pbi) / len(qbi)) if qbi else 0.0
            scores.append(round(cov + bonus, 4))
        return scores

    def chat(self, *a, **k):
        raise BackendUnavailable("no chat model in offline mode")

    def vision(self, *a, **k):
        raise BackendUnavailable("no vision model in offline mode")

    def live_check(self):
        return {"backend": "offline", "ok": True, "note": "offline stand-ins; nothing to check"}


# ---------------------------------------------------------------------- NIM ----
class NimBackend:
    name = "nim"
    can_generate = True
    can_vision = True

    def __init__(self, api_key=None):
        self.key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        if not self.key:
            raise BackendUnavailable("NVIDIA_API_KEY is not set (get one at https://build.nvidia.com)")
        self.base = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
        self.models = {
            "embedding": os.environ.get("NIM_EMBED_MODEL", "nvidia/nemotron-3-embed-1b"),
            "rerank": os.environ.get("NIM_RERANK_MODEL", None),
            "chat": os.environ.get("NIM_CHAT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
            "vision": os.environ.get("NIM_VISION_MODEL", "meta/llama-3.2-90b-vision-instruct"),
        }
        self.rerank_url = os.environ.get("NIM_RERANK_URL", None)
        self.dim = None
        self.timeout = int(os.environ.get("NIM_TIMEOUT", "15"))

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    def _post(self, url, body):
        status, js = request_json("POST", url, body, self._h(), timeout=self.timeout, retries=3)
        if status != 200:
            raise BackendUnavailable(f"NIM {status}: {str(js)[:300]}")
        return js

    def embed(self, texts, input_type="passage", batch=32):
        vecs = []
        for i in range(0, len(texts), batch):
            js = self._post(self.base + "/embeddings", {
                "model": self.models["embedding"], "input": texts[i:i + batch],
                "input_type": "query" if input_type == "query" else "passage",
                "encoding_format": "float", "truncate": "END"})
            data = sorted(js["data"], key=lambda d: d["index"])
            vecs.extend(d["embedding"] for d in data)
        arr = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1
        self.dim = arr.shape[1]
        return arr / norms

    def rerank(self, query, passages):
        if not passages:
            return []
        # No rerank model configured — fall back to uniform scores
        # (embedding similarity from search is used as the ranking signal)
        if not self.models.get("rerank") or not self.rerank_url:
            return [0.0] * len(passages)
        js = self._post(self.rerank_url, {
            "model": self.models["rerank"], "query": {"text": query},
            "passages": [{"text": p[:4000]} for p in passages], "truncate": "END"})
        scores = [0.0] * len(passages)
        for r in js["rankings"]:
            scores[r["index"]] = float(r["logit"])
        return scores

    def chat(self, messages, max_tokens=700, temperature=0.0, json_mode=False):
        body = {"model": self.models["chat"], "messages": messages, "temperature": temperature,
                "top_p": 1.0, "max_tokens": max_tokens, "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        js = self._post(self.base + "/chat/completions", body)
        msg = js["choices"][0]["message"]
        content = msg.get("content") or msg.get("reasoning_content") or ""
        return content

    @staticmethod
    def _shrink(png_bytes, limit=170_000):
        from PIL import Image
        im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        scale, q = 1.0, 85
        while True:
            buf = io.BytesIO()
            w, h = int(im.width * scale), int(im.height * scale)
            im.resize((w, h)).save(buf, "JPEG", quality=q)
            if buf.tell() <= limit or scale < 0.3:
                return buf.getvalue()
            scale *= 0.85
            q = max(60, q - 5)

    def vision(self, image_bytes, prompt, max_tokens=900):
        jpg = self._shrink(image_bytes)
        b64 = base64.b64encode(jpg).decode()
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]
        body = {"model": self.models["vision"], "messages": msgs, "temperature": 0.0,
                "max_tokens": max_tokens, "stream": False}
        js = self._post(self.base + "/chat/completions", body)
        return js["choices"][0]["message"]["content"]

    def live_check(self):
        """One tiny call per capability. Never raises; reports per-model status."""
        res = {"backend": "nim", "base_url": self.base, "checks": {}}

        def timed(name, fn):
            t = time.time()
            try:
                detail = fn()
                res["checks"][name] = {"ok": True, "model": self.models[name], "ms": int((time.time() - t) * 1000), "detail": detail}
            except Exception as e:  # noqa: BLE001
                res["checks"][name] = {"ok": False, "model": self.models[name], "ms": int((time.time() - t) * 1000), "error": str(e)[:300]}

        timed("embedding", lambda: {"dim": int(self.embed(["neonatal jaundice"], "query").shape[1])})
        timed("rerank", lambda: {"scores": self.rerank("phototherapy threshold", ["bilirubin cut-offs", "capital of France"])})
        timed("chat", lambda: {"reply": self.chat([{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=5)[:20]})
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (320, 90), "white")
        ImageDraw.Draw(im).text((10, 30), "260 umol/L (15 mg/dL)", fill="black")
        b = io.BytesIO()
        im.save(b, "PNG")
        timed("vision", lambda: {"reply": self.vision(b.getvalue(), "Transcribe the text in this image exactly.", 40)[:80]})
        res["ok"] = all(c["ok"] for c in res["checks"].values())
        return res


def get_backend(mode="auto"):
    """mode: offline | nim | auto. Returns (backend, note)."""
    if mode == "offline":
        return OfflineBackend(), "offline mode requested"
    if mode == "nim":
        return NimBackend(), "NIM backend"
    try:
        b = NimBackend()
        b.embed(["ping"], "query")
        return b, "NIM reachable"
    except Exception as e:  # noqa: BLE001
        return OfflineBackend(), f"NIM unavailable ({str(e)[:120]}); using offline stand-ins"
