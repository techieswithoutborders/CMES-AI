"""T12: search-metadata enrichment (hypothetical questions, aliases, glossary, duplicate-content flags).

Enrichment is SEARCH METADATA ONLY: it is appended to the FTS/embedding text, never shown as an answer, and never
contains numbers that are not in the passage. Cached by sha256(chunk_id + model + prompt_hash).
"""
import json
import re
from pathlib import Path

from common.nim import BackendUnavailable
from common.util import extract_numbers, sha256_text, norm_ws

PROMPT = """You write SEARCH METADATA for one passage of a clinical reference document. You are not answering anything.
Return ONLY a JSON object with these keys:
  "hypothetical_questions": 3 short questions a health worker might ask that this passage answers,
  "local_aliases": alternate names, abbreviations, and common synonyms for the key terms (include French/Portuguese/Swahili only if you are certain),
  "glossary_terms": up to 5 key medical terms from the passage.
Rules: do not invent facts; do not include any number, dose, or threshold that is not written in the passage; no advice.

PASSAGE:
{passage}
"""
PROMPT_HASH = sha256_text(PROMPT)

OFFLINE_ALIASES = {
    "phototherapy": ["light therapy", "bili lights"], "jaundice": ["hyperbilirubinaemia", "yellow baby"],
    "exchange transfusion": ["double volume transfusion"], "sepsis": ["blood infection"], "ketamine": ["ketalar"],
    "intubation": ["rapid sequence intubation", "RSI", "endotracheal"], "syphilis": ["congenital syphilis"],
    "resuscitation": ["newborn resuscitation", "bag and mask ventilation"], "hypothermia": ["cold baby"],
    "rsi": ["rapid sequence intubation", "intubation", "induction agent", "paralytic"], "etomidate": ["induction agent"], "propofol": ["induction agent"],
    "succinylcholine": ["paralytic", "suxamethonium"], "rocuronium": ["paralytic"],
}


def _parse_json(s):
    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0)) if m else {}


def _clean(enr, passage):
    allowed = set(extract_numbers(passage))
    qs = [q for q in enr.get("hypothetical_questions", []) if isinstance(q, str) and set(extract_numbers(q)) <= allowed][:3]
    al = [a for a in enr.get("local_aliases", []) if isinstance(a, str) and len(a) < 60][:8]
    gl = [g for g in enr.get("glossary_terms", []) if isinstance(g, str) and len(g) < 80][:5]
    return qs, al, gl


def offline_enrich(c):
    sec = (c.get("section_path") or [""])[0]
    first = norm_ws(re.split(r"(?<=[.!?])\s", c["text"])[0])[:140]
    qs = [f"What does the guideline say about {sec}?"] if sec else []
    qs.append(f"Find information: {first}")
    low = c["text"].lower()
    al = [a for k, v in OFFLINE_ALIASES.items() if k in low for a in v]
    return {"hypothetical_questions": qs[:3], "local_aliases": al[:8], "glossary_terms": [sec] if sec else []}


def enrich_chunks(chunks, backend, cache_path, limit=None, log=print):
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if cache_path.exists():
        for line in cache_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                cache[r["key"]] = r["value"]
    model = backend.models["chat"] if backend.can_generate else "offline-rules-v1"
    stats = {"model": model, "cache_hits": 0, "cache_misses": 0, "model_calls": 0, "failures": 0, "skipped_over_limit": 0}
    newlines = []
    dup = {}
    for c in chunks:
        if c["chunk_kind"] == "table":
            dup.setdefault(sha256_text(norm_ws(c["text"])), []).append(c)
    for i, c in enumerate(chunks):
        key = sha256_text(f"{c['chunk_id']}|{model}|{PROMPT_HASH}")
        if key in cache:
            enr = cache[key]
            stats["cache_hits"] += 1
        else:
            stats["cache_misses"] += 1
            if backend.can_generate and (limit is None or stats["model_calls"] < limit):
                try:
                    raw = backend.chat([{"role": "user", "content": PROMPT.format(passage=c["text"][:3500])}], max_tokens=400, json_mode=True)
                    qs, al, gl = _clean(_parse_json(raw), c["text"])
                    stats["model_calls"] += 1
                except (BackendUnavailable, ValueError, KeyError) as e:
                    stats["failures"] += 1
                    log(f"  enrich failed for chunk {i}: {str(e)[:80]}")
                    o = offline_enrich(c)
                    qs, al, gl = o["hypothetical_questions"], o["local_aliases"], o["glossary_terms"]
            else:
                if backend.can_generate:
                    stats["skipped_over_limit"] += 1
                o = offline_enrich(c)
                qs, al, gl = o["hypothetical_questions"], o["local_aliases"], o["glossary_terms"]
            enr = {"hypothetical_questions": qs, "local_aliases": al, "glossary_terms": gl, "contradiction_flags": [],
                   "enrichment_model_version": model if (backend.can_generate and qs) else "offline-rules-v1", "enrichment_prompt_hash": PROMPT_HASH}
            cache[key] = enr
            newlines.append(json.dumps({"key": key, "value": enr}))
        c["enrichment"] = dict(enr)
    for group in dup.values():  # identical tables in different places: surface, do not guess which is right
        if len(group) > 1:
            for c in group:
                others = [o for o in group if o is not c]
                c["enrichment"]["contradiction_flags"] = [
                    {"conflicts_with_chunk_id": o["chunk_id"], "conflict_type": "other", "severity": "low",
                     "description": "identical table also appears elsewhere in the corpus (duplicate, not a conflict)"} for o in others]
    if newlines:
        with open(cache_path, "a") as f:
            f.write("\n".join(newlines) + "\n")
    return stats
