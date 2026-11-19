"""Gate: table fidelity (100%). For every table chunk (sample up to 50):
  structure - header rows resolved, every data cell has a row header and column header(s), bbox is the table (not the full page)
  numbers   - every number in the table is found by an independent second reader on the source page
Source errors (e.g. printed mmol/L vs mg/dL) are NOT parser faults: they are listed under unresolved_unit_flags and become release blockers."""
import json
import sqlite3
from pathlib import Path

from common.util import extract_numbers, ro_connect
from ingest.verify import TextLayerReader, get_reader
from eval.gates.gate_common import not_run, result


def run(pack_dir, sources_dir, backend=None, threshold=1.0, sample=50):
    if not sources_dir or not Path(sources_dir).exists():
        return not_run("source PDFs not available to re-read tables", threshold)
    con = ro_connect(Path(pack_dir) / "kb.sqlite")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT c.*, d.file AS file FROM chunks c JOIN docs d USING(doc_id) WHERE chunk_kind='table'")]
    if not rows:
        return not_run("pack contains no tables", threshold)
    files = {p.name: p for p in Path(sources_dir).rglob("*.pdf")}
    reader = get_reader(backend, "auto") if backend is not None else TextLayerReader()
    bad, ok, unit_flags = [], 0, []
    for r in rows[:sample]:
        t = json.loads(r["table_json"])
        bbox = json.loads(r["bbox"])
        problems = []
        if t["header_rows"] < 1:
            problems.append("no header row resolved")
        for c in t["cells"]:
            if not c["is_header"] and c["c"] > 0 and not c["col_headers"]:
                problems.append(f"cell r{c['r']}c{c['c']} has no column header")
                break
            if not c["is_header"] and not c["row_header"]:
                problems.append(f"cell r{c['r']}c{c['c']} has no row header")
                break
        if (bbox["x1"] - bbox["x0"]) * (bbox["y1"] - bbox["y0"]) > 0.9:
            problems.append("table bbox covers the whole page")
        pdf = files.get(r["file"])
        if pdf is None:
            problems.append("source PDF not found for second read")
        else:
            try:
                reader_nums = set(extract_numbers(reader.read_page(str(pdf), r["page"])))
                tn = {n for c in t["cells"] for n in extract_numbers(c["text"])}
                miss = sorted(n for n in tn if n not in reader_nums and not any(len(n) <= 3 and len(x) > len(n) and (x.startswith(n) or x.endswith(n)) for x in reader_nums))
                if miss:
                    problems.append(f"numbers not confirmed by second reader ({reader.name}): {miss[:6]}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"second read failed: {str(e)[:80]}")
        flags = [f for f in json.loads(r["flags"] or "[]") if f["kind"] == "unit_suspect"]
        if flags and r["review_status"] != "clinician_reviewed":
            unit_flags.append({"table_id": r["table_ref"], "page": r["page"], "example": flags[0]["match"], "n": len(flags)})
        if problems:
            bad.append({"table_id": r["table_ref"], "page": r["page"], "problems": problems})
        else:
            ok += 1
    n = min(len(rows), sample)
    score = ok / n
    return result("ran", score, threshold, score >= threshold, total_tables_sampled=n, matching_tables=ok, mismatches=bad,
                  second_reader=reader.name, unresolved_unit_flags=unit_flags)
