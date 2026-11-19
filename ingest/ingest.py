"""PDF -> chunks.jsonl (+ page images), schema 1.1.

Key differences from the first stub (see ARCHITECTURE.md, ADR-2/3):
  * a table is ONE chunk (chunk_kind='table') carrying a resolved cell grid, not isolated cell chunks
  * header text is read from the text layer by position, because ruling-line detectors silently drop header cells
  * real bboxes, source-driven authority tier, per-chunk language, chunk_id includes bbox
  * unit sanity flags, printed page numbers, page images (lossless when the page has a table)
"""
import argparse
import io
import json
import re
import statistics
import sys
from pathlib import Path

import pdfplumber

from common.lang import detect_language
from common.units import unit_flags
from common.util import (SCHEMA_VERSION, QUANTITY_RE, clamp01, extract_quantities, norm_ws, round_bbox, sha256_bytes,
                         sha256_file, sha256_text, canonical_json)

HERE = Path(__file__).parent
MAX_CHUNK_CHARS = 900


def load_registry(path=None):
    return json.loads(Path(path or HERE / "sources.json").read_text())


def lookup_source(registry, filename):
    for s in registry["sources"]:
        if s["match"].lower() in filename.lower():
            return {"tier": s["tier"], "basis": s["basis"], "pub_date": s.get("pub_date"), "exclude": bool(s.get("exclude"))}
    d = registry["default"]
    return {"tier": d["tier"], "basis": d["basis"], "pub_date": d.get("pub_date"), "exclude": False}


def doc_title(stem):
    t = re.sub(r"^\d+_", "", stem).replace("_", " ")
    return norm_ws(t)


def expiry_for(pub_date, tier):
    if not pub_date or tier > 2:
        return None
    y = int(pub_date[:4]) + 5
    return f"{y}{pub_date[4:]}"


_TIME_UNITS = {"min", "mins", "minute", "minutes", "h", "hr", "hrs", "hour", "hours", "day", "days", "week", "weeks",
               "month", "months", "year", "years", ""}


def _alpha_words_left(t):
    rest = QUANTITY_RE_STRIP.sub(" ", t)
    return [w for w in re.findall(r"[A-Za-z\u00b5]{2,}", rest)]


def value_like(text, need_unit=False):
    """True for cells that are mostly a measurement ('260 mmol/L (15 mg/dL)', '12', '140 mg (2 mg/kg)').
    Used to tell header rows from data rows and real measurement tables from layout boxes."""
    t = (text or "").strip()
    if not need_unit and re.fullmatch(r"[<>~\u2265\u2264]?\s*\d+(?:[.,]\d+)?(?:\s?[-\u2013]\s?\d+(?:[.,]\d+)?)?\s?%?", t):
        return True
    qs = [(n, u) for n, u in extract_quantities(t) if u not in _TIME_UNITS]
    return bool(qs) and len(_alpha_words_left(t)) <= 2


QUANTITY_RE_STRIP = re.compile(QUANTITY_RE.pattern)


# ------------------------------------------------------------------ tables ----
def _cluster(vals, tol=2.0):
    out = []
    for v in sorted(vals):
        if out and abs(v - out[-1][-1]) <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _phrases(words, gap=6.0):
    """Group words into (text, x0, x1, top, bottom) phrases: same line, small horizontal gaps."""
    lines = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= 2.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    phrases = []
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
        cur = [ln[0]]
        for w in ln[1:]:
            if w["x0"] - cur[-1]["x1"] > gap:
                phrases.append(cur)
                cur = [w]
            else:
                cur.append(w)
        phrases.append(cur)
    out = []
    for ph in phrases:
        out.append({"text": " ".join(w["text"] for w in ph), "x0": min(w["x0"] for w in ph), "x1": max(w["x1"] for w in ph),
                    "top": min(w["top"] for w in ph), "bottom": max(w["bottom"] for w in ph)})
    return out


def _cols_for(ph, col_edges):
    pw = max(ph["x1"] - ph["x0"], 0.1)
    hit = []
    for i in range(len(col_edges) - 1):
        a, b = col_edges[i], col_edges[i + 1]
        ov = min(ph["x1"], b) - max(ph["x0"], a)
        if ov > 0 and (ov / (b - a) >= 0.3 or ov / pw >= 0.6):
            hit.append(i)
    if not hit:  # fall back to the column holding the phrase centre
        cx = (ph["x0"] + ph["x1"]) / 2
        for i in range(len(col_edges) - 1):
            if col_edges[i] <= cx <= col_edges[i + 1]:
                hit = [i]
    return hit


def build_table(page, tbl, W, H):
    """Resolve a detected table into a header-aware cell grid using text positions."""
    cells = [c for c in tbl.cells if c]
    if len(cells) < 2:
        return None
    x0, top, x1, bottom = tbl.bbox
    col_edges = _cluster([c[0] for c in cells] + [c[2] for c in cells] + [x0, x1])
    row_edges = _cluster([c[1] for c in cells] + [c[3] for c in cells] + [top, bottom])
    ncols, nrows = len(col_edges) - 1, len(row_edges) - 1
    if ncols < 2 or nrows < 2:
        return None
    region = page.crop((max(0, x0 - 1), max(0, top - 1), min(page.width, x1 + 1), min(page.height, bottom + 1)))
    words = region.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
    grid = []  # list of cells
    for r in range(nrows):
        ra, rb = row_edges[r], row_edges[r + 1]
        band = [w for w in words if ra - 1 <= (w["top"] + w["bottom"]) / 2 <= rb + 1]
        groups = {}
        phs_all = _phrases(band)
        starts = []  # (phrase index, column index holding the phrase start)
        for i, ph in enumerate(phs_all):
            for ci in range(ncols):
                if col_edges[ci] - 0.5 <= ph["x0"] < col_edges[ci + 1]:
                    starts.append((i, ci))
                    break
        for i, ph in enumerate(phs_all):
            cols = _cols_for(ph, col_edges)
            # a column that another phrase starts in belongs to that phrase (long header text overflowing into a neighbour)
            owned = {ci for j, ci in starts if j != i}
            kept = [c for c in cols if c not in owned]
            cols = tuple(kept or cols[:1])
            if cols:
                groups.setdefault(cols, []).append(ph)
        for cols, phs in groups.items():
            phs.sort(key=lambda p: (round(p["top"]), p["x0"]))
            grid.append({"r": r, "c": cols[0], "colspan": len(cols), "rowspan": 1,
                         "text": norm_ws(" ".join(p["text"] for p in phs)),
                         "bbox": {"x0": clamp01(min(p["x0"] for p in phs) / W), "y0": clamp01(min(p["top"] for p in phs) / H),
                                  "x1": clamp01(max(p["x1"] for p in phs) / W), "y1": clamp01(max(p["bottom"] for p in phs) / H)}})
    if not grid:
        return None
    # header rows: leading rows whose non-first-column cells contain no digit
    by_row = {}
    for g in grid:
        by_row.setdefault(g["r"], []).append(g)
    header_rows = 0
    for r in range(nrows):
        cs = by_row.get(r, [])
        if any(value_like(g["text"]) for g in cs):
            break
        header_rows += 1
    header_rows = min(header_rows, nrows - 1)
    # column header paths (levels merged top->bottom)
    col_headers = [[] for _ in range(ncols)]
    for r in range(header_rows):
        for g in sorted(by_row.get(r, []), key=lambda g: g["c"]):
            for c in range(g["c"], g["c"] + g["colspan"]):
                if g["text"] and g["text"] not in col_headers[c]:
                    col_headers[c].append(g["text"])
    col_header_txt = [" > ".join(h) for h in col_headers]
    row_header_by_row, last = {}, ""
    for r in range(header_rows, nrows):
        first = next((g for g in by_row.get(r, []) if g["c"] == 0), None)
        if first and first["text"]:
            last = first["text"]
        row_header_by_row[r] = last
    cell_objs = []
    for g in sorted(grid, key=lambda g: (g["r"], g["c"])):
        is_header = g["r"] < header_rows
        covered = list(range(g["c"], g["c"] + g["colspan"]))
        cell_objs.append({
            "r": g["r"], "c": g["c"], "colspan": g["colspan"], "rowspan": 1, "text": g["text"], "bbox": round_bbox(g["bbox"]),
            "is_header": is_header,
            "row_header": None if is_header else row_header_by_row.get(g["r"]),
            "col_headers": [] if (is_header or g["c"] == 0) else list(dict.fromkeys(col_header_txt[c] for c in covered if col_header_txt[c])),
        })
    # markdown with merged cells repeated across the columns they cover
    md_rows = []
    for r in range(nrows):
        row = [""] * ncols
        for g in by_row.get(r, []):
            for c in range(g["c"], g["c"] + g["colspan"]):
                row[c] = g["text"]
        md_rows.append(row)
    md = []
    for i, row in enumerate(md_rows):
        if i == header_rows and header_rows > 0:
            pass
        md.append("| " + " | ".join(norm_ws(x).replace("|", "/") for x in row) + " |")
        if i == max(header_rows - 1, 0):
            md.append("|" + "---|" * ncols)
    return {
        "n_rows": nrows, "n_cols": ncols, "header_rows": header_rows, "col_headers": col_header_txt,
        "cells": cell_objs, "markdown": "\n".join(md),
        "bbox": {"x0": clamp01(x0 / W), "y0": clamp01(top / H), "x1": clamp01(x1 / W), "y1": clamp01(bottom / H)},
    }


# -------------------------------------------------------------------- text ----
def _norm_run(t):
    return re.sub(r"\d+", "#", norm_ws(t).lower())


def running_lines(pdf):
    """Lines in the top/bottom margins that repeat across pages (running headers/footers) - dropped from chunks."""
    from collections import Counter
    cnt, n = Counter(), len(pdf.pages)
    for page in pdf.pages:
        H = float(page.height)
        seen = set()
        for l in page.extract_text_lines():
            if l["top"] < 0.09 * H or l["bottom"] > 0.93 * H:
                k = _norm_run(l["text"])
                if k and k not in seen and k != "#":
                    seen.add(k)
                    cnt[k] += 1
    need = 3 if n >= 6 else max(2, int(0.6 * n))
    return frozenset(k for k, v in cnt.items() if v >= need)


def _line_info(line):
    chars = line.get("chars") or []
    sizes = [c["size"] for c in chars if c.get("size")]
    bold = sum(1 for c in chars if "bold" in (c.get("fontname") or "").lower()) > len(chars) / 2 if chars else False
    return (statistics.mean(sizes) if sizes else 0.0), bold


def text_chunks(page, table_boxes, W, H, body_size, drop=frozenset()):
    def keep(o):
        if o.get("object_type") != "char":
            return True
        cx, cy = (o["x0"] + o["x1"]) / 2, (o["top"] + o["bottom"]) / 2
        return not any(b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1 for b in table_boxes)

    fp = page.filter(keep)
    lines = fp.extract_text_lines(return_chars=True, strip=True)
    lines = [l for l in lines if _norm_run(l["text"]) not in drop]
    printed = None
    if lines and re.fullmatch(r"\d{1,4}", lines[-1]["text"].strip()) and lines[-1]["top"] > 0.9 * H:
        printed = lines[-1]["text"].strip()
        lines = lines[:-1]
    out, cur, heading = [], [], None

    def flush():
        nonlocal cur
        if cur:
            txt = norm_ws(" ".join(l["text"] for l in cur))
            txt = re.sub(r"(\w)- (?=[a-z])", r"\1", txt)  # de-hyphenate soft line breaks only
            out.append({"text": txt, "section": heading, "bbox": {
                "x0": clamp01(min(l["x0"] for l in cur) / W), "y0": clamp01(min(l["top"] for l in cur) / H),
                "x1": clamp01(max(l["x1"] for l in cur) / W), "y1": clamp01(max(l["bottom"] for l in cur) / H)}})
            cur = []

    prev_bottom = None
    for l in lines:
        size, bold = _line_info(l)
        t = l["text"].strip()
        if not t:
            continue
        t = t.replace("\u25a0\u25a0", "\u2022").replace("\u25a0", "\u2022")
        l = dict(l, text=t)
        is_head = len(t) < 90 and not t.endswith((".", ",", ";")) and (
            (body_size and size >= body_size * 1.08) or (bold and size >= (body_size or 0)))
        if is_head:
            flush()
            heading = t
            prev_bottom = l["bottom"]
            continue
        gap = (l["top"] - prev_bottom) if prev_bottom is not None else 0
        cur_len = sum(len(x["text"]) for x in cur)
        if cur and (gap > max(size, 6) * 0.9 or cur_len + len(t) > MAX_CHUNK_CHARS):
            flush()
        cur.append(l)
        prev_bottom = l["bottom"]
    flush()
    return out, printed


def render_page(page, lossless):
    from PIL import Image  # noqa: F401
    im = page.to_image(resolution=130 if lossless else 110).original.convert("RGB")
    buf = io.BytesIO()
    if lossless:
        im.save(buf, "WEBP", lossless=True)
    else:
        im.save(buf, "WEBP", quality=82)
    return buf.getvalue()


# -------------------------------------------------------------------- doc ----
def make_chunk(doc_id, page_no, printed, bbox, section, text, kind, src, lang, page_hash, confidence=0.9, table=None, table_id=None):
    bbox = round_bbox(bbox)
    cid = sha256_bytes(canonical_json([doc_id, page_no, bbox, kind, text]))
    return {
        "chunk_id": cid, "doc_id": doc_id, "page": page_no, "printed_page": printed, "bbox": bbox,
        "section_path": [section] if section else [], "text": text, "chunk_kind": kind,
        "table_ref": table_id, "table": table, "language": lang, "authority_tier": src["tier"],
        "tier_basis": src["basis"], "pub_date": src["pub_date"], "expiry_date": expiry_for(src["pub_date"], src["tier"]),
        "source_kind": "pdf", "confidence": confidence, "review_status": "unreviewed", "schema_version": SCHEMA_VERSION,
        "page_image_hash": page_hash, "enrichment": None, "verification": None, "flags": [],
    }


def ingest_pdf(path, pages_dir, registry, render=True):
    path = Path(path)
    src = lookup_source(registry, path.name)
    doc_id = f"{re.sub(r'[^A-Za-z0-9]+', '-', path.stem).strip('-')[:48]}-{sha256_file(path)[:8]}"
    chunks, pages_meta, warnings = [], [], []
    with pdfplumber.open(path) as pdf:
        sizes = []
        for p in pdf.pages[:6]:
            for l in p.extract_text_lines(return_chars=True)[:60]:
                s, _ = _line_info(l)
                if s:
                    sizes.append(s)
        body = statistics.median(sizes) if sizes else 0
        drop = running_lines(pdf)
        for pi, page in enumerate(pdf.pages, start=1):
            W, H = float(page.width), float(page.height)
            try:
                tbls = [t for t in page.find_tables()]
            except Exception as e:  # noqa: BLE001
                tbls, warnings = [], warnings + [f"p{pi}: table detection failed: {e}"]
            built = []
            for ti, t in enumerate(tbls):
                try:
                    bt = build_table(page, t, W, H)
                except Exception as e:  # noqa: BLE001
                    bt = None
                    warnings.append(f"p{pi} t{ti}: table build failed: {e}")
                body_cells = [c["text"] for c in bt["cells"]] if bt else []
                if bt and any(value_like(x, need_unit=(bt["n_cols"] < 3)) for x in body_cells if not (bt["header_rows"] and False)):
                    built.append((t, bt))
                elif bt:
                    warnings.append(f"p{pi} t{ti}: layout box without measurements treated as text, not a table")
            boxes = [t.bbox for t, _ in built]
            tchunks, printed = text_chunks(page, boxes, W, H, body, drop)
            page_hash = None
            if render:
                data = render_page(page, lossless=bool(built))
                page_hash = sha256_bytes(data)
                pages_dir.mkdir(parents=True, exist_ok=True)
                (pages_dir / f"{page_hash}.webp").write_bytes(data)
            pages_meta.append({"page": pi, "printed_page": printed, "page_image_hash": page_hash, "has_table": bool(built)})
            for tc in tchunks:
                if len(tc["text"]) < 3:
                    continue
                lang = detect_language(tc["text"], default="auto")
                ch = make_chunk(doc_id, pi, printed, tc["bbox"], tc["section"], tc["text"], "text", src, lang, page_hash, 0.9)
                ch["flags"] = unit_flags(tc["text"])
                chunks.append(ch)
            for ti, (t, bt) in enumerate(built):
                table_id = f"{doc_id}-p{pi}-t{ti}"
                bt["table_id"] = table_id
                text = bt["markdown"]
                lang = detect_language(" ".join(c["text"] for c in bt["cells"]), default="auto")
                ch = make_chunk(doc_id, pi, printed, bt["bbox"], None, text, "table", src, lang, page_hash, 0.8, bt, table_id)
                ch["flags"] = unit_flags(" ".join(c["text"] for c in bt["cells"]))
                ch["section_path"] = [f"Table {ti + 1}"]
                chunks.append(ch)
    # document-level language fallback: chunks that were 'auto' inherit the doc majority
    langs = [c["language"] for c in chunks if c["language"] != "auto"]
    doc_lang = max(set(langs), key=langs.count) if langs else "auto"
    for c in chunks:
        if c["language"] == "auto":
            c["language"] = doc_lang
    ids = [c["chunk_id"] for c in chunks]
    if len(ids) != len(set(ids)):
        warnings.append("duplicate chunk_id within document (identical text+bbox+page) - de-duplicated")
        seen, uniq = set(), []
        for c in chunks:
            if c["chunk_id"] not in seen:
                seen.add(c["chunk_id"])
                uniq.append(c)
        chunks = uniq
    return {"doc_id": doc_id, "title": doc_title(path.stem), "file": path.name, "tier": src["tier"], "tier_basis": src["basis"],
            "pub_date": src["pub_date"], "pages": pages_meta, "chunks": chunks, "warnings": warnings}


def ingest_dir(src_dir, out_dir, max_pdfs=None, render=True, registry_path=None, log=print):
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = load_registry(registry_path)
    pdfs = sorted(src_dir.rglob("*.pdf"))
    if max_pdfs:
        pdfs = pdfs[:max_pdfs]
    docs, all_chunks = [], []
    excluded = []
    for p in pdfs:
        src = lookup_source(registry, p.name)
        if src.get("exclude"):
            log(f"  skip   {p.name} (excluded by source registry: {src['basis']})")
            excluded.append({"file": p.name, "reason": src["basis"]})
            continue
        log(f"  ingest {p.name}")
        d = ingest_pdf(p, out_dir / "pages", registry, render=render)
        docs.append({k: v for k, v in d.items() if k != "chunks"} | {"n_chunks": len(d["chunks"]), "source_path": str(p)})
        all_chunks.extend(d["chunks"])
    # global chunk_id uniqueness (same content in two docs has different doc_id, so ids differ)
    with open(out_dir / "chunks.jsonl", "w") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (out_dir / "docs.json").write_text(json.dumps(docs, indent=1, ensure_ascii=False))
    return {"docs": docs, "chunks": all_chunks, "excluded": excluded}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="samplefiles/pdf")
    ap.add_argument("--out", default="build/ingest")
    ap.add_argument("--max-pdfs", type=int)
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    r = ingest_dir(a.src, a.out, a.max_pdfs, render=not a.no_render)
    print(f"{len(r['docs'])} docs, {len(r['chunks'])} chunks, "
          f"{sum(1 for c in r['chunks'] if c['chunk_kind']=='table')} tables -> {a.out}")


if __name__ == "__main__":
    sys.exit(main())
