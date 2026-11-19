"""T4: second-reader cross-check of numbers on table/dosing pages + review-status triage.

Readers:
  TextLayerReader  - pypdf text layer (offline, independent implementation from pdfplumber/pdfminer; NOT vision)
  VisionReader     - NVIDIA NIM vision model re-reads the rendered page (build-time, needs NVIDIA_API_KEY)
A disagreement on any number sends the page to the clinician queue; a release pack ships with zero unreviewed ones.
"""
import io
import re
from collections import defaultdict

from common.util import dosing_intent, extract_numbers

VISION_PROMPT = ("Transcribe all tables and every dose, threshold, and number on this page as Markdown. "
                 "Copy numbers and units EXACTLY as printed, do not correct or convert anything, do not add commentary.")


class TextLayerReader:
    def __init__(self):
        self.name = "pypdf-text-layer"
        self._cache = {}

    def read_page(self, pdf_path, page_no):
        import pypdf
        r = self._cache.get(pdf_path)
        if r is None:
            r = self._cache[pdf_path] = pypdf.PdfReader(pdf_path)
        return r.pages[page_no - 1].extract_text() or ""


class VisionReader:
    def __init__(self, backend):
        self.backend = backend
        self.name = f"nim-vision:{backend.models['vision']}"

    def read_page(self, pdf_path, page_no):
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            im = pdf.pages[page_no - 1].to_image(resolution=150).original.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return self.backend.vision(buf.getvalue(), VISION_PROMPT)


def get_reader(backend, prefer="auto"):
    if prefer in ("auto", "vision") and getattr(backend, "can_vision", False):
        return VisionReader(backend)
    return TextLayerReader()


def verify_chunks(chunks, docs, reader, log=print, max_pages=None):
    """Mutates chunks: sets .verification and .review_status; appends flags. Returns summary dict."""
    src = {d["doc_id"]: d["source_path"] for d in docs}
    by_page = defaultdict(list)
    for c in chunks:
        by_page[(c["doc_id"], c["page"])].append(c)
    summary = {"reader": reader.name, "pages_checked": 0, "agree": 0, "disagree": 0, "errors": 0}
    todo = [k for k, cs in by_page.items() if any(c["chunk_kind"] == "table" or dosing_intent(c["text"]) for c in cs)]
    if max_pages:
        todo = todo[:max_pages]
    for key in todo:
        doc_id, page = key
        cs = by_page[key]
        try:
            raw = reader.read_page(src[doc_id], page)
        except Exception as e:  # noqa: BLE001
            summary["errors"] += 1
            for c in cs:
                c["verification"] = {"status": "not_checked", "reader": reader.name, "error": str(e)[:200]}
            continue
        reader_nums = set(extract_numbers(raw))
        parser_nums = set()
        for c in cs:
            parser_nums.update(extract_numbers(c["text"]))
            if c.get("printed_page"):
                parser_nums.add(c["printed_page"])
        # text layers often glue a row label to the next number ("Day 2" + "260" -> "2260"): tolerate that for short numbers
        def found(n):
            return n in reader_nums or (len(n) <= 3 and any(len(r) > len(n) and (r.startswith(n) or r.endswith(n)) for r in reader_nums))
        missing = sorted((n for n in parser_nums if not found(n)), key=float)
        extra = sorted((n for n in reader_nums - parser_nums if len(n.replace(".", "")) >= 2), key=float)
        # text-layer reader sees running headers/footers the parser drops: only count extras for the vision reader
        disagree = bool(missing) or (bool(extra) and isinstance(reader, VisionReader))
        status = "disagree" if disagree else "agree"
        summary["pages_checked"] += 1
        summary["disagree" if disagree else "agree"] += 1
        for c in cs:
            c["verification"] = {"status": status, "reader": reader.name, "missing_in_reader": missing[:20], "extra_in_reader": extra[:20]}
            if disagree:
                c["flags"].append({"kind": "parser_reader_disagree", "detail": f"missing_in_reader={missing[:6]} extra_in_reader={extra[:6]}"})
    for c in chunks:
        v = c.get("verification")
        has_unit_flag = any(f["kind"] == "unit_suspect" for f in c["flags"])
        if v and v["status"] == "agree":
            if c["authority_tier"] == 1 and not has_unit_flag:
                c["review_status"] = "auto_approved"
            elif has_unit_flag:
                c["review_status"] = "fast_tracked"  # one clinician looks at the flagged unit
        elif has_unit_flag:
            c["review_status"] = "fast_tracked"
    return summary
