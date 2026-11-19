"""Chunks -> SQLite knowledge pack (FTS5 + float32 vectors) + manifest + Ed25519 signatures.

Lifecycle: build_pack() -> [calibrate / run eval gates against the unsigned pack] -> finalize_pack() signs it.
sqlite-vec is the planned vector index; this test pipeline stores plain float32 BLOBs and searches with numpy
(fine for <=100k chunks in tests; swap in sqlite-vec behind Pack.vector_search in T5/T6).
"""
import datetime as dt
import json
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from common.nim import stems
import tempfile

from common.util import SCHEMA_VERSION, canonical_json, ro_connect, sha256_bytes, sha256_file
from indexer import signing

DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE docs(doc_id TEXT PRIMARY KEY, title TEXT, file TEXT, tier INTEGER, tier_basis TEXT, pub_date TEXT, n_pages INTEGER);
CREATE TABLE chunks(
  chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, page INTEGER, printed_page TEXT, bbox TEXT, section_path TEXT, text TEXT NOT NULL,
  chunk_kind TEXT, table_json TEXT, table_ref TEXT, language TEXT, authority_tier INTEGER, tier_basis TEXT, pub_date TEXT, expiry_date TEXT,
  source_kind TEXT, confidence REAL, review_status TEXT, page_image_hash TEXT, enrichment TEXT, verification TEXT, flags TEXT);
CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, body, tokenize='porter unicode61 remove_diacritics 2');
CREATE TABLE vectors(chunk_id TEXT PRIMARY KEY, dim INTEGER, vec BLOB);
"""


def search_text(c):
    parts = [" ".join(c.get("section_path") or []), c["text"]]
    e = c.get("enrichment") or {}
    parts += e.get("hypothetical_questions", []) + e.get("local_aliases", []) + e.get("glossary_terms", [])
    if c.get("table"):
        parts += [x for x in c["table"].get("col_headers", [])]
    return "\n".join(p for p in parts if p)


def embed_text(c):
    sec = " / ".join(c.get("section_path") or [])
    return (sec + "\n" if sec else "") + c["text"][:6000]


def build_pack(chunks, docs, pages_dir, out_dir, version, backend, channel="dev", log=print):
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "blobs").mkdir(parents=True)
    # build on local temp disk, then copy: SQLite needs real file locking, the destination may be a synced/FUSE folder
    tmpdir = tempfile.mkdtemp(prefix="twb-build-")
    db_path = Path(tmpdir) / "kb.sqlite"
    con = sqlite3.connect(db_path)
    con.executescript(DDL)
    for d in docs:
        con.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?)", (d["doc_id"], d["title"], d["file"], d["tier"], d["tier_basis"], d["pub_date"], len(d["pages"])))
    j = lambda o: json.dumps(o, ensure_ascii=False)  # noqa: E731
    for c in chunks:
        con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            c["chunk_id"], c["doc_id"], c["page"], c.get("printed_page"), j(c["bbox"]), j(c["section_path"]), c["text"], c["chunk_kind"],
            j(c["table"]) if c.get("table") else None, c.get("table_ref"), c["language"], c["authority_tier"], c.get("tier_basis"),
            c.get("pub_date"), c.get("expiry_date"), c["source_kind"], c["confidence"], c["review_status"], c.get("page_image_hash"),
            j(c.get("enrichment")), j(c.get("verification")), j(c.get("flags", []))))
        con.execute("INSERT INTO chunks_fts(chunk_id, body) VALUES (?,?)", (c["chunk_id"], search_text(c)))
    log(f"  embedding {len(chunks)} chunks with {backend.name}:{backend.models['embedding']}")
    texts = [embed_text(c) for c in chunks]
    vecs = backend.embed(texts, "passage") if texts else np.zeros((0, 8), dtype=np.float32)
    dim = int(vecs.shape[1])
    for c, v in zip(chunks, vecs):
        con.execute("INSERT INTO vectors VALUES (?,?,?)", (c["chunk_id"], dim, v.astype(np.float32).tobytes()))
    df = Counter()
    for c in chunks:
        df.update(set(stems(c["text"])))
    meta = {
        "schema_version": SCHEMA_VERSION, "pack_version": version, "channel": channel, "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "embedding_model": backend.models["embedding"], "embedding_dim": str(dim), "embedding_backend": backend.name,
        "rerank_model": backend.models["rerank"] or "embedding-similarity-fallback", "n_chunks": str(len(chunks)), "stem_df": json.dumps(dict(df)), "calibration": "{}",
    }
    for k, v in meta.items():
        con.execute("INSERT INTO meta VALUES (?,?)", (k, v))
    con.commit()
    con.close()
    shutil.copy2(db_path, out / "kb.sqlite")
    shutil.rmtree(tmpdir, ignore_errors=True)
    pages_dir = Path(pages_dir)
    used = {c["page_image_hash"] for c in chunks if c.get("page_image_hash")}
    for h in used:
        src = pages_dir / f"{h}.webp"
        if src.exists():
            shutil.copy2(src, out / "blobs" / f"{h}.webp")
    return out


def set_meta(pack_dir, key, value):
    """Only used before signing (calibration). Edits a temp copy, then replaces the file."""
    dst = Path(pack_dir) / "kb.sqlite"
    tmpdir = tempfile.mkdtemp(prefix="twb-meta-")
    tmp = Path(tmpdir) / "kb.sqlite"
    shutil.copy2(dst, tmp)
    con = sqlite3.connect(tmp)
    con.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    con.commit()
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()
    shutil.copy2(tmp, dst)
    shutil.rmtree(tmpdir, ignore_errors=True)


def read_chunks(pack_dir):
    con = ro_connect(Path(pack_dir) / "kb.sqlite")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM chunks")]
    meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
    con.close()
    return rows, meta


def finalize_pack(pack_dir, eval_report=None, channel="dev", keys_dir="keys", previous_manifest=None, log=print):
    """Compute hashes, attach eval results, sign. Release channel is refused unless the eval report says release_eligible."""
    pack = Path(pack_dir)
    rows, meta = read_chunks(pack)
    if channel == "release" and not (eval_report and eval_report.get("release_eligible")):
        raise RuntimeError("refusing to sign a release pack: eval report is missing or not release_eligible")
    table_hashes = sorted({sha256_bytes(canonical_json(json.loads(r["table_json"]))) for r in rows if r["table_json"]})
    blobs = sorted(p.stem for p in (pack / "blobs").glob("*.webp"))
    tiers = Counter(str(r["authority_tier"]) for r in rows)
    kinds = Counter(r["source_kind"] for r in rows)
    reviews = Counter(r["review_status"] for r in rows)
    chunk_hashes = sorted(r["chunk_id"] for r in rows)
    gates = None
    if eval_report:
        g = eval_report["gates"]
        pick = lambda k, f: (g.get(k) or {}).get(f)  # noqa: E731
        gates = {k: g.get(k, {}).get("status") for k in g}
        gates = {
            "statuses": gates, "overall_pass": eval_report["overall_pass"], "release_eligible": eval_report["release_eligible"],
            "citation_faithfulness_dosing": pick("citation_faithfulness_dosing", "score"),
            "citation_faithfulness_other": pick("citation_faithfulness_other", "score"),
            "out_of_scope_abstention": pick("out_of_scope_abstention", "score"),
            "retrieval_recall_at_10": pick("retrieval_recall_at_10", "score"),
            "table_fidelity": pick("table_fidelity", "score"),
            "latency_search_p95_ms": pick("latency_search_ms", "p95_ms"),
            "update_safety": pick("update_safety", "passed"),
        }
    manifest = {
        "manifest_version": SCHEMA_VERSION, "schema_version": SCHEMA_VERSION, "pack_version": meta["pack_version"], "channel": channel,
        "build_date": meta["built_at"], "build_hash": sha256_file(pack / "kb.sqlite"), "chunk_count": len(rows), "chunk_hashes": chunk_hashes,
        "page_image_count": len(blobs), "page_image_hashes": blobs, "table_count": len(table_hashes), "table_hashes": table_hashes,
        "languages": sorted({r["language"] for r in rows}),
        "authority_tier_distribution": {k: tiers.get(k, 0) for k in "1234"},
        "source_kind_distribution": {"pdf": kinds.get("pdf", 0), "audio": kinds.get("audio", 0)},
        "review_status_distribution": {k: reviews.get(k, 0) for k in ("unreviewed", "auto_approved", "fast_tracked", "clinician_reviewed", "rejected")},
        "embedding": {"model": meta["embedding_model"], "dim": int(meta["embedding_dim"]), "backend": meta["embedding_backend"]},
        "eval_gate_results": gates, "release_eligible": bool(eval_report and eval_report.get("release_eligible")),
        "delta_from": previous_manifest["pack_version"] if previous_manifest else None,
        "delta_chunk_hashes_added": [], "delta_chunk_hashes_removed": [], "delta_page_image_hashes_added": [], "delta_page_image_hashes_removed": [],
    }
    if previous_manifest:
        old, new = set(previous_manifest["chunk_hashes"]), set(chunk_hashes)
        manifest["delta_chunk_hashes_added"] = sorted(new - old)
        manifest["delta_chunk_hashes_removed"] = sorted(old - new)
        oi, ni = set(previous_manifest["page_image_hashes"]), set(blobs)
        manifest["delta_page_image_hashes_added"] = sorted(ni - oi)
        manifest["delta_page_image_hashes_removed"] = sorted(oi - ni)
    ensure = signing.ensure_dev_keys(keys_dir)
    signed = signing.sign_manifest(manifest, signing.load_priv(ensure / "ci.key"), "ci-dev-key", signing.load_priv(ensure / "root.key"), "root-dev-key")
    (pack / "manifest.json").write_text(json.dumps(signed, indent=1))
    log(f"  signed {pack.name} channel={channel} chunks={len(rows)} release_eligible={manifest['release_eligible']}")
    return signed


if __name__ == "__main__":
    sys.exit("use `python3 -m pipeline.run`")
