# Offline Medical Q&A — Architecture (v1.1, 2026-10-18)

Authoritative description of what is built. The product requirements remain `Offline Medical Q&A Build Plan.pdf`; where this
document deviates from the plan, the deviation is an ADR below. The earlier `architecture-deck.html` draft contradicted the plan
(cloud vector DB / MinIO / Postgres / 27B LLMs in the answer path, ingestion on the edge) — that is withdrawn (ADR-1).

## 1. Data flow

```
 CLOUD / BUILD HOST (internet allowed)                                  FIELD BOX (never needs internet)
 ┌──────────┐  ┌─────────────┐  ┌──────────┐  ┌──────────────────┐  ┌───────────────┐   USB / tablet   ┌────────────────────────────┐
 │ 1 Ingest │→ │ 2 Verify    │→ │ 3 Enrich │→ │ 4 Embed + Index  │→ │ 5 Gates+Sign  │ ───────────────→ │ Updater: verify→stage→swap │
 │ pdfplumber│  │ NIM vision  │  │ NIM chat │  │ NIM embeddings   │  │ calibrate,    │   signed pack    │ Edge: search→rerank→abstain│
 │ tables as │  │ / text-layer│  │ (JSON,   │  │ SQLite FTS5 +    │  │ 8 gates,      │   (kb.sqlite +   │ → table / extractive /     │
 │ one chunk │  │ unit checks │  │ temp 0)  │  │ float32 vectors  │  │ Ed25519 chain │   manifest+blobs)│   verified summary + cites │
 └──────────┘  └─────────────┘  └──────────┘  └──────────────────┘  └───────────────┘                   └────────────────────────────┘
        source registry (tier by source)        NVIDIA NIM = build-time models                                  ▲ Android app / test console (HTTP)
```

## 2. Components

| Path | Role | State |
|---|---|---|
| `spec/` | chunk, manifest, eval-report schemas (v1.1) + `openapi.yaml` + `CHANGELOG.md` | frozen, all chunks validate |
| `ingest/` | `ingest.py` PDF → chunks/pages; `verify.py` second-reader check; `sources.json` authority registry | built, tested |
| `enrich/` | search-metadata enrichment with cache | built (offline rules tested; NIM path written, not run live) |
| `indexer/` | `build_pack.py` SQLite pack + manifest; `signing.py` Ed25519 chain | built, tested |
| `edge/` | `core.py` engine; `server.py` HTTP (stdlib); `updater.py` signed atomic updates | built, tested |
| `eval/` | 7 gate modules, `run_gates.py`, `calibrate.py`, dev golden set builder | built; fail closed |
| `common/` | `nim.py` NVIDIA NIM + offline backends, units, quantities, language | built |
| `pipeline/run.py` | end-to-end test pipeline with live progress report | built, tested offline |
| `ui/index.html` | test console (ask / pipeline / eval / update) | built, headless smoke test |
| `benchmarks/` | real-measurement hardware benchmark | built; needs the boxes |
| `android/`, hotspot image, audio pipeline | T8, T7, T11 | **not built** |

## 3. Decisions (ADRs)

**ADR-1 The build plan wins.** Box answers from a signed pack; cloud is for build and update only. Removed: Pinecone/Weaviate, MinIO,
PostgreSQL, LLM answer synthesis in the cloud, on-box ingestion/Whisper/upload API, "selective cloud sync" of embeddings.

**ADR-2 A table is one chunk.** v1.0 stored every cell as its own chunk with no row/column headers; a hit on `260 mmol/L (15 mg/dL)`
could not say which day/column it belonged to (the build-plan analysis even mis-assigned it). Now: `chunk_kind=table`, text = Markdown,
`table.cells[]` carry row header, column header path and bbox. Retrieval returns the table; the engine highlights the matching cells and,
when the question does not pin a column (e.g. gestational age), shows all matching cells and says so. A weight-band dose is a *table row
lookup*, never a computed number (rule 2).

**ADR-3 Headers are read from the text layer by position.** Ruling-line table detectors silently drop header cells (here: the
"EXCHANGE TRANSFUSION" group header and one sub-header) and cannot represent merged cells (Day 1 "Any visible jaundice" spans two columns).
Column intervals come from the detector; header/merged text comes from word positions. Layout boxes without measurements are treated as text.

**ADR-4 Gates fail closed.** `not_run` is a failure; a gate never defaults to 1.0. `release_eligible` additionally needs a
clinician-reviewed golden set, target hardware, and zero blockers (unreviewed chunks, unresolved unit flags, reader disagreements).
Abstention is measured on a held-out split; the calibration split only sets the threshold. A release pack cannot be signed unless eligible.

**ADR-5 Authority tier comes from the source.** `ingest/sources.json` assigns tiers; unknown sources default to tier 3. Board-review
quizzes (they explain "why the other answers are wrong") are excluded from the pack. A clinician signs off the registry.

**ADR-6 Query-time models must run on the box.** Pack vectors are only useful if the box can embed a *query* with the same model.
NVIDIA NIM-hosted models therefore serve (a) build-time work (vision cross-check, enrichment, corpus embeddings) and (b) the *test
harness* query path. Production needs an open-weight embedding + reranker of the same family running on N150/Pi 5. The pack manifest
records the embedding model; the engine disables the vector leg (and says so) on a mismatch. Decide in T1.

**ADR-7 Verbatim and flag; never silently correct.** WHO's table prints `mmol/L` beside `mg/dL` (≈1000× off; it is µmol/L). The stored
text stays verbatim, the chunk gets `unit_suspect`, status `fast_tracked`, the answer carries a warning, and the release gate lists it as
a blocker until a clinician decides.

**ADR-8 Trust chain and channels.** Box pins the root public key. Root certifies the CI key (with expiry); the CI key signs the canonical
manifest; `build_hash` and every page-image hash are verified. `dev` packs are refused by production updaters. Updates are staged in the
inactive slot, smoke-tested, then switched with one fsync'd `os.replace`; the previous pack stays for rollback. The API takes a pack *name*
inside `incoming/`, never a path.

**ADR-9 Test service is stdlib; production is FastAPI.** Same OpenAPI contract. The stdlib service runs anywhere with zero installs and
is what the gates and UI test against; swap-in is mechanical.

**ADR-10 Dose answerability.** A dose/threshold question is answered only from passages that contain a dose-like quantity; otherwise
the system abstains with `dose_not_in_sources`. Summaries never cover tables or dosing; the verifier blocks any claim whose quote is not a
verbatim span or whose numbers are not in the quote.

**ADR-11 Privacy.** No request logging. `/feedback` stores ids, rating and category only (free text is dropped).

## 4. NVIDIA NIM roles (all env-configurable; offline stand-ins exist)

| Role | Default model (verify in T1) | Where used | Env |
|---|---|---|---|
| Embedding | `nvidia/llama-3.2-nv-embedqa-1b-v2` via `https://integrate.api.nvidia.com/v1/embeddings` | pack vectors; test-harness queries | `NIM_EMBED_MODEL` |
| Rerank | `nvidia/llama-3.2-nv-rerankqa-1b-v2` via `https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-3_2-nv-rerankqa-1b-v2/reranking` | abstention scores; test-harness queries | `NIM_RERANK_MODEL`, `NIM_RERANK_URL` |
| Chat | `meta/llama-3.1-70b-instruct` | enrichment; optional grounded summary | `NIM_CHAT_MODEL` |
| Vision | `meta/llama-3.2-90b-vision-instruct` | T4 second reader on table/dose pages | `NIM_VISION_MODEL` |

Auth: `export NVIDIA_API_KEY=...` (key from build.nvidia.com). **Status: request shapes follow NVIDIA's published API docs for the embedding and
rerank models; the NIM path is protocol-tested against a local fake NIM server (`tests/test_nim_mock.py`, incl. a full `pipeline.run --mode nim`) but has not been executed against the live service (no key, no network in the build sandbox).** Run
`python3 -m pipeline.run --live-check` or the console's *Check NIM models* first; the vision model id in particular must be confirmed.

## 5. Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export NVIDIA_API_KEY=...            # optional; without it offline stand-ins are used
./run_test.sh [auto|nim|offline]     # pipeline, then console at http://127.0.0.1:8080/ui/
python3 -m unittest -v tests.test_all
```

## 6. Known limitations (honest list)

* Offline stand-ins are for mechanics. The lexical reranker misses out-of-scope questions that share vocabulary with the corpus (held-out
  abstention 5/6 in the dev run); the dose-confusion-across-populations risk (infant vs child amoxicillin) is **open** until a real reranker and a red-team set exist.
* The golden set is an engineer-written dev set (32 questions). It cannot make a pack release-eligible.
* Second reader for scanned PDFs needs the vision model; the offline text-layer reader is not independent for scans (OCR path not built).
* Vector search is a numpy scan (fine ≤100k chunks); `sqlite-vec` is next. Chunks are held in memory (not for 250k chunks on a Pi without paging).
* Language detection is a stopword heuristic; Swahili/French/Portuguese have no test data yet.
* Latency numbers off target hardware are informational and never count toward release.

## 7. Plan task status

T0 spec ✅ · T1 hardware ⏳ (script ready, boxes not bought) · T2 golden set ⏳ (dev set only) · T3 ingest ✅ (sample corpus) · T4 vision check ✅ code / ⏳ live ·
T5 indexer ✅ · T6 edge ✅ · T7 hotspot ❌ · T8 Android ❌ · T9 eval harness ✅ · T10 pilot ❌ · T11 audio ❌ (consent) · T12 enrichment ✅ code / ⏳ live ·
T13–T19 later phases.
