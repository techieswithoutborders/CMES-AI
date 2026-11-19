#!/usr/bin/env python3
"""Validate build outputs against the frozen /spec (schema 1.1): every chunk in build/ingest/chunks.jsonl and every pack manifest.
   python3 validate_sample.py [build_dir]"""
import json
import sys
from pathlib import Path

import jsonschema

build = Path(sys.argv[1] if len(sys.argv) > 1 else "build")
spec = Path("spec")
cs = json.loads((spec / "chunk_schema.json").read_text())
ms = json.loads((spec / "manifest_schema.json").read_text())
bad = 0
n = 0
for line in open(build / "ingest" / "chunks.jsonl"):
    n += 1
    try:
        jsonschema.validate(json.loads(line), cs)
    except jsonschema.ValidationError as e:
        bad += 1
        print("chunk", n, e.message[:140])
print(f"chunks: {n - bad}/{n} valid")
for mp in sorted((build / "packs").glob("*/manifest.json")):
    try:
        jsonschema.validate(json.loads(mp.read_text()), ms)
        print("manifest valid:", mp.parent.name)
    except jsonschema.ValidationError as e:
        bad += 1
        print("manifest INVALID:", mp.parent.name, e.message[:140])
sys.exit(1 if bad else 0)
