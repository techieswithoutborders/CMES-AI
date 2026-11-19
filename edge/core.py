"""Framework-independent edge engine: hybrid search (FTS5 + vectors, RRF), rerank, abstention, table mode,
extractive answers, grounded summary with a synchronous verifier. The HTTP layer lives in edge/server.py.

Safety rules enforced here (see ARCHITECTURE.md):
  R1 cite-or-abstain          every answer claim carries chunk_id + verbatim source_text + span offsets
  R2 doses never generated    dosing/table queries return stored table cells verbatim; summaries refuse them
  R3 generation optional      default is extractive; summary needs a chat backend and passes the verifier or is blocked
"""
import base64
import datetime as dt
import json
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from pathlib import Path

import numpy as np

from common.nim import BackendUnavailable, OfflineBackend, stems
from common.util import dosing_intent, extract_quantities, norm_ws, ro_connect, tokens

TIER_LABELS = {1: "WHO / national guideline", 2: "Society / peer-reviewed", 3: "Textbook / educational", 4: "Clinician talk, not guideline"}
DEFAULT_THRESHOLD = {"offline-lexical-idf": 0.55}
RRF_K = 60
_TIME = {"min", "mins", "minute", "minutes", "h", "hr", "hrs", "hour", "hours", "day", "days", "week", "weeks", "month", "months", "year", "years"}


class GenerationUnavailable(Exception):
    pass


class Pack:
    def __init__(self, path):
        self.path = Path(path)
        con = ro_connect(self.path / "kb.sqlite", check_same_thread=False)
        con.row_factory = sqlite3.Row
        self.meta = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM meta")}
        self.docs = {r["doc_id"]: dict(r) for r in con.execute("SELECT * FROM docs")}
        self.chunks, ids, vecs = {}, [], []
        for r in con.execute("SELECT * FROM chunks"):
            d = dict(r)
            for k in ("bbox", "section_path", "table_json", "enrichment", "verification", "flags"):
                d[k] = json.loads(d[k]) if d.get(k) else None
            d["table"] = d.pop("table_json")
            self.chunks[d["chunk_id"]] = d
        for r in con.execute("SELECT chunk_id, dim, vec FROM vectors"):
            ids.append(r["chunk_id"])
            vecs.append(np.frombuffer(r["vec"], dtype=np.float32))
        self.vec_ids = ids
        self.vecs = np.vstack(vecs) if vecs else np.zeros((0, 1), dtype=np.float32)
        self.con = con
        self.version = self.meta.get("pack_version")
        self.channel = self.meta.get("channel", "dev")
        self.embedding_model = self.meta.get("embedding_model")
        self.rerank_model = self.meta.get("rerank_model")
        self.calibration = json.loads(self.meta.get("calibration") or "{}")
        self.stem_df = json.loads(self.meta.get("stem_df") or "{}")
        mp = self.path / "manifest.json"
        self.manifest = json.loads(mp.read_text()) if mp.exists() else None

    def fts(self, toks, limit=30):
        clean = [re.sub(r"[^\w]", "", t) for t in toks]
        clean = [t for t in clean if t]
        if not clean:
            return []
        q = " OR ".join(f'"{t}"' for t in clean)
        try:
            rows = self.con.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?", (q, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r[0] for r in rows]

    def vector_search(self, qvec, limit=30):
        if not len(self.vec_ids) or qvec.shape[0] != self.vecs.shape[1]:
            return []
        sims = self.vecs @ qvec
        order = np.argsort(-sims)[:limit]
        return [self.vec_ids[i] for i in order]

    def image(self, h):
        p = self.path / "blobs" / f"{h}.webp"
        return p.read_bytes() if p.exists() else None


def _cell_offsets(markdown, table, cell):
    """Character span of a table cell's text inside the table chunk's markdown (for citations)."""
    lines = markdown.split("\n")
    li = cell["r"] + (1 if cell["r"] > max(table["header_rows"] - 1, 0) else 0)
    if li >= len(lines):
        return None
    start = sum(len(x) + 1 for x in lines[:li])
    line = lines[li]
    needle = norm_ws(cell["text"]).replace("|", "/")
    pos = line.find(needle, 1)
    # merged cells are repeated across columns; the first occurrence is the right span
    if pos < 0:
        return None
    return start + pos, start + pos + len(needle)


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\u2022(])|\s\u2022\s", text)
    return [p.strip() for p in parts if len(p.strip()) > 12]


class Engine:
    def __init__(self, pack: Pack, backend, today=None):
        self.pack, self.backend = pack, backend
        self.today = today or dt.date.today().isoformat()
        self.vector_ok = bool(pack.embedding_model == backend.models["embedding"] and len(pack.vec_ids))
        if hasattr(backend, "configure_corpus"):
            backend.configure_corpus(pack.stem_df, len(pack.chunks))

    # ------------------------------------------------------------- helpers ---
    def threshold(self):
        rm = self.backend.models["rerank"]
        cal = self.pack.calibration.get(rm)
        if cal:
            return cal["threshold"], True
        return DEFAULT_THRESHOLD.get(rm, 0.0), False

    def _rerank_text(self, c):
        sec = " / ".join(c.get("section_path") or [])
        e = c.get("enrichment") or {}
        alias = ", ".join((e.get("local_aliases") or []) + (e.get("glossary_terms") or []))[:240]  # search metadata only, never shown as an answer
        return (sec + "\n" if sec else "") + c["text"][:3500] + (f"\n[aliases: {alias}]" if alias else "")

    def _passage(self, cid, score, query, with_highlights=True):
        c = self.pack.chunks[cid]
        doc = self.pack.docs.get(c["doc_id"], {})
        p = {k: c[k] for k in ("chunk_id", "doc_id", "page", "printed_page", "bbox", "section_path", "text", "chunk_kind", "table_ref",
                               "language", "authority_tier", "source_kind", "confidence", "review_status", "page_image_hash",
                               "pub_date", "expiry_date", "flags", "verification")}
        p.update(doc_title=doc.get("title"), tier_label=TIER_LABELS.get(c["authority_tier"]), score=round(float(score), 4),
                 expired=bool(c.get("expiry_date") and c["expiry_date"] < self.today), table=c.get("table"), highlight_cells=[], table_note=None, highlight_strength=0)
        if c["chunk_kind"] == "table" and with_highlights:
            p["highlight_cells"], p["table_note"], p["highlight_strength"] = self.highlight_cells(c["table"], query)
        return p

    @staticmethod
    def highlight_cells(table, query):
        q = set(stems(query))
        data = [c for c in table["cells"] if not c["is_header"] and c["c"] > 0]
        if not data:
            return [], None, 0
        col_sets = defaultdict(set)
        for c in data:
            for ci in range(c["c"], c["c"] + c["colspan"]):
                col_sets[ci] |= set(stems(table["col_headers"][ci])) if ci < len(table["col_headers"]) else set()
        common = set.intersection(*col_sets.values()) if col_sets else set()
        # a column is "hit" through tokens that distinguish it from at least one other column
        distinct = {ci: s - common for ci, s in col_sets.items()}
        col_score = {ci: len(s & q) for ci, s in distinct.items()}
        best_col = max(col_score.values()) if col_score else 0
        hit_cols = {ci for ci, v in col_score.items() if v == best_col and v > 0}
        row_score = {}
        for c in data:
            rs = set(stems(c["row_header"] or ""))
            row_score[c["r"]] = len(rs & q)
        best = max(row_score.values()) if row_score else 0
        hit_rows = {r for r, v in row_score.items() if v == best and v > 0}
        if not hit_rows and not hit_cols:
            return [], None, 0
        strength = (best if hit_rows else 0) + (best_col if hit_cols else 0)
        sel = []
        for c in data:
            cols = set(range(c["c"], c["c"] + c["colspan"]))
            row_ok = (not hit_rows) or c["r"] in hit_rows
            col_ok = (not hit_cols) or bool(cols & hit_cols)
            if row_ok and col_ok:
                sel.append({"r": c["r"], "c": c["c"], "colspan": c["colspan"], "text": c["text"], "row_header": c["row_header"],
                            "col_headers": c["col_headers"], "bbox": c["bbox"]})
        note = None
        if len(sel) > 1:
            heads = sorted({h for s in sel for h in s["col_headers"]})
            note = ("Several cells match your question. Use the column that matches the patient: " + "; ".join(heads)) if heads else \
                   "Several cells match your question; check the row that matches the patient."
        return sel, note, strength

    # --------------------------------------------------------------- search ---
    def search(self, query, language="auto", k=5, mode="passages"):
        t0 = time.time()
        q = norm_ws(query)[:1000]
        toks = tokens(q)
        fast = len(toks) < 3
        warnings = []
        t = time.time()
        fts_ids = self.pack.fts(toks, 30)
        fts_ms = (time.time() - t) * 1000
        vec_ids, vec_ms = [], 0.0
        if not fast:
            if self.vector_ok:
                t = time.time()
                try:
                    vec_ids = self.pack.vector_search(self.backend.embed([q], "query")[0], 30)
                except BackendUnavailable as e:
                    warnings.append(f"vector leg skipped: {str(e)[:80]}")
                vec_ms = (time.time() - t) * 1000
            else:
                warnings.append("vector leg disabled: pack embeddings were built with a different model than this backend")
        rrf = defaultdict(float)
        for ids in (fts_ids, vec_ids):
            for r, cid in enumerate(ids):
                rrf[cid] += 1.0 / (RRF_K + r + 1)
        cands = sorted(rrf, key=rrf.get, reverse=True)[:30]
        if language != "auto" and any(self.pack.chunks[c]["language"] == language for c in cands):
            cands = [c for c in cands if self.pack.chunks[c]["language"] in (language, "auto")]
        qid = uuid.uuid4().hex[:8]
        thr, calibrated = self.threshold()
        info = {"mode": "keyword-fast-path" if fast else "hybrid-rrf", "vector_leg": bool(vec_ids), "reranker": self.backend.models["rerank"],
                "backend": self.backend.name, "threshold": thr, "calibrated": calibrated, "warnings": warnings}
        if not cands:
            return {"passages": [], "abstained": True, "query_id": qid, "nearest_documents": [], "reranker_scores": [], "retrieval": info,
                    "timings_ms": {"total": round((time.time() - t0) * 1000, 1)}}
        t = time.time()
        scores = self.backend.rerank(q, [self._rerank_text(self.pack.chunks[c]) for c in cands])
        rerank_ms = (time.time() - t) * 1000
        ranked = sorted(zip(cands, scores), key=lambda x: (-x[1], self.pack.chunks[x[0]]["authority_tier"]))
        abstained = ranked[0][1] < thr
        if not abstained and dosing_intent(q) and not self._has_dose(ranked[:3]):
            abstained = True
            info["abstain_reason"] = "dose_not_in_sources"
        timings = {"fts": round(fts_ms, 1), "vector": round(vec_ms, 1), "rerank": round(rerank_ms, 1), "total": round((time.time() - t0) * 1000, 1)}
        if abstained:
            near = [self._passage(c, s, q, with_highlights=False) for c, s in ranked[:3]]
            return {"passages": [], "abstained": True, "query_id": qid, "nearest_documents": near, "reranker_scores": [round(float(s), 4) for _, s in ranked[:3]],
                    "retrieval": info, "timings_ms": timings}
        top = self._collapse(ranked, max(1, min(k, 20)))
        return {"passages": [self._passage(c, s, q) for c, s in top], "abstained": False, "query_id": qid, "nearest_documents": [],
                "reranker_scores": [round(float(s), 4) for _, s in top], "retrieval": info, "timings_ms": timings}

    def _has_dose(self, ranked):
        """A dosing question may only be answered from passages that actually contain a dose/threshold quantity."""
        for cid, _ in ranked:
            for n, u in extract_quantities(self.pack.chunks[cid]["text"]):
                if u and u not in _TIME and u not in ("cm", "mm", "kg", "g"):
                    return True
                if u in ("g", "kg") and False:
                    return True
        return False

    def _collapse(self, ranked, k):
        """The same paragraph often appears in several source documents: keep the best copy once."""
        seen, out = set(), []
        for cid, sc in ranked:
            c = self.pack.chunks[cid]
            key = norm_ws(c["text"]).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((cid, sc))
            if len(out) >= k:
                break
        return out

    # --------------------------------------------------------------- answer ---
    def answer(self, query, language="auto", mode="extractive"):
        s = self.search(query, language, k=5)
        base = {"query_id": s["query_id"], "citations": [], "answer": "", "verified": False, "abstained": s["abstained"], "mode_used": mode,
                "passages": s["passages"], "nearest_documents": s["nearest_documents"], "table": None, "table_note": None, "warnings": [],
                "retrieval": s["retrieval"], "timings_ms": s["timings_ms"]}
        if s["abstained"]:
            base.update(answer="No reliable source found.", mode_used="abstain")
            return base
        top = s["passages"][0]
        table_p = top if (top["chunk_kind"] == "table" and top["highlight_cells"]) else None
        if table_p is None and dosing_intent(query):
            # a table that matches BOTH a row and a column of the question beats a looser prose passage for dose/threshold questions
            table_p = next((p for p in s["passages"][:3] if p["chunk_kind"] == "table" and p["highlight_strength"] >= 3), None)
        wants_table = table_p is not None
        if mode == "grounded_summary":
            if wants_table or table_p or dosing_intent(query):
                base["warnings"].append("Dosing/table content is never summarised by a model: showing the stored table cells verbatim (rule 2).")
                mode = "table"
            else:
                return self._summary(query, s, base)
        if table_p:
            return self._table_answer(table_p, base)
        return self._extractive(query, s, base)

    def _table_answer(self, p, base):
        table, cits, lines, seen = p["table"], [], [], set()
        md = p["text"]
        cells = table["cells"]

        def cite(cell):
            off = _cell_offsets(md, table, cell)
            if not off or off in seen:
                return
            seen.add(off)
            cits.append({"chunk_id": p["chunk_id"], "claim_text": cell["text"], "source_span_start": off[0], "source_span_end": off[1],
                         "source_text": md[off[0]:off[1]]})

        for hc in p["highlight_cells"]:
            value = next((c for c in cells if c["r"] == hc["r"] and c["c"] == hc["c"] and not c["is_header"]), None)
            rowcell = next((c for c in cells if c["r"] == hc["r"] and c["c"] == 0 and not c["is_header"]), None)
            covered = range(hc["c"], hc["c"] + hc["colspan"])
            hdrs = sorted({(c["r"], c["c"]): c for c in cells if c["is_header"] and any(c["c"] <= ci < c["c"] + c["colspan"] for ci in covered)}.values(),
                          key=lambda c: (c["r"], c["c"]))
            if not value:
                continue
            for c in ([rowcell] if rowcell else []) + hdrs + [value]:
                cite(c)
            levels = {}
            for h_ in hdrs:
                if h_["text"]:
                    levels.setdefault(h_["r"], []).append(h_["text"])
            col = " > ".join(" / ".join(dict.fromkeys(v)) for _, v in sorted(levels.items()))  # merged cell: sub-headers joined by ' / '

            lines.append((rowcell["text"] if rowcell else hc["row_header"] or "") + (f" \u2014 {col}" if col else "") + f": {value['text']}")
        flags = [f for f in (p.get("flags") or []) if f["kind"] == "unit_suspect"]
        if flags:
            base["warnings"].append("This table prints mmol/L next to mg/dL values; the unit looks wrong in the source (probably \u00b5mol/L). "
                                    "Values are shown exactly as printed - confirm before use.")
        if p.get("expired"):
            base["warnings"].append("Guidance past its expiry date: check for newer guidance.")
        base.update(answer="\n".join(lines), citations=cits, mode_used="table", table=p["table"], table_note=p["table_note"],
                    table_chunk_id=p["chunk_id"], highlight_cells=p["highlight_cells"])
        base["verified"] = bool(cits) and self.verify(cits)
        return base

    def _extractive(self, query, s, base):
        q = set(stems(query))
        cands = []
        for pi, p in enumerate(s["passages"][:3]):
            if p["chunk_kind"] != "text":
                continue
            for sent in _sentences(p["text"]):
                ss = set(stems(sent))
                if not ss:
                    continue
                cov = len(ss & q) / (len(q) or 1)
                if cov > 0:
                    cands.append((cov - 0.05 * pi, sent, p))
        cands.sort(key=lambda x: -x[0])
        chosen, seen = [], set()
        for cand in cands:
            key = norm_ws(cand[1]).lower()
            if key not in seen:
                seen.add(key)
                chosen.append(cand)
            if len(chosen) == 2:
                break
        if not chosen:
            base.update(answer="No reliable source found.", abstained=True, mode_used="abstain")
            return base
        cits, lines = [], []
        for _, sent, p in chosen:
            st = p["text"].find(sent)
            if st < 0:
                continue
            cits.append({"chunk_id": p["chunk_id"], "claim_text": sent, "source_span_start": st, "source_span_end": st + len(sent), "source_text": sent})
            lines.append(sent)
        if any(p.get("expired") for _, _, p in chosen):
            base["warnings"].append("Guidance past its expiry date: check for newer guidance.")
        if any(p["authority_tier"] >= 3 for _, _, p in chosen):
            base["warnings"].append("Source is educational material, not a guideline.")
        base.update(answer="\n".join(lines), citations=cits, mode_used="extractive")
        base["verified"] = self.verify(cits) and bool(cits)
        return base

    def _summary(self, query, s, base):
        if not self.backend.can_generate:
            raise GenerationUnavailable("No generation model is available on this backend (offline mode); extractive answers only.")
        passages = [p for p in s["passages"][:4] if p["chunk_kind"] == "text"]
        ctx = "\n\n".join(f"[{p['chunk_id'][:8]}] {p['text']}" for p in passages)
        prompt = ("Answer the question using ONLY the passages. Return JSON {\"claims\":[{\"text\":str,\"source_id\":str,\"quote\":str}]}. "
                  "Every claim must copy a supporting quote verbatim from the passage with that source_id. Do not add numbers or doses "
                  "that are not in the quote. If the passages do not answer it return {\"claims\":[]}.\n\nQUESTION: " + query + "\n\nPASSAGES:\n" + ctx)
        raw = self.backend.chat([{"role": "user", "content": prompt}], max_tokens=600, temperature=0.0, json_mode=True)
        try:
            claims = json.loads(re.search(r"\{.*\}", raw, re.S).group(0)).get("claims", [])
        except Exception:  # noqa: BLE001
            claims = []
        by_prefix = {p["chunk_id"][:8]: p for p in passages}
        cits, lines, bad = [], [], []
        for cl in claims:
            p = by_prefix.get(str(cl.get("source_id", ""))[:8])
            quote = norm_ws(str(cl.get("quote", "")))
            text = str(cl.get("text", ""))
            if not p or not quote:
                bad.append(text)
                continue
            st = p["text"].find(quote)
            if st < 0:
                st = norm_ws(p["text"]).find(quote)
                if st < 0:
                    bad.append(text)
                    continue
            claim_q = extract_quantities(text)
            quote_q = {n for n, _ in extract_quantities(quote)}
            if any(n not in quote_q for n, _ in claim_q):
                bad.append(text)
                continue
            cits.append({"chunk_id": p["chunk_id"], "claim_text": text, "source_span_start": st, "source_span_end": st + len(quote), "source_text": quote})
            lines.append(text)
        if bad or not cits:
            base.update(answer="", mode_used="blocked", verified=False)
            base["warnings"].append(f"Summary blocked by verifier ({len(bad)} unsupported claim(s)). Showing source passages only.")
            return base
        base.update(answer="\n".join(lines), citations=cits, mode_used="grounded_summary")
        base["verified"] = True
        return base

    def verify(self, citations):
        """Synchronous verifier: span text must equal the stored chunk text at those offsets and every number in the claim
        must be present in the span."""
        for c in citations:
            ch = self.pack.chunks.get(c["chunk_id"])
            if not ch:
                return False
            span = ch["text"][c["source_span_start"]:c["source_span_end"]]
            if norm_ws(span) != norm_ws(c["source_text"]):
                if norm_ws(c["source_text"]) not in norm_ws(ch["text"]):
                    return False
            span_q = {(n, u) for n, u in extract_quantities(c["source_text"])}
            span_n = {n for n, _ in span_q}
            for n, u in extract_quantities(c["claim_text"]):
                if n not in span_n:
                    return False
        return True

    # ------------------------------------------------------------------ doc ---
    def doc_page(self, doc_id, page):
        for c in self.pack.chunks.values():
            if c["doc_id"] == doc_id and c["page"] == page and c.get("page_image_hash"):
                data = self.pack.image(c["page_image_hash"])
                if data:
                    return {"doc_id": doc_id, "page": page, "printed_page": c.get("printed_page"), "image_base64": base64.b64encode(data).decode(),
                            "content_type": "image/webp", "doc_title": self.pack.docs.get(doc_id, {}).get("title")}
        return None
