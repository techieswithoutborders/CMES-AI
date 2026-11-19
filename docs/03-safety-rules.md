# CMES-AI Safety Rules — Non-Negotiable Constraints

**Document Version:** 1.0  
**Status:** DRAFT — For Review  
**Owner:** Safety Officer + Clinical Lead  
**Last Updated:** 2025-01-28  
**Applies To:** All Phases (0–10) — Immutable Unless Explicitly Amended

---

## ⚠️ CRITICAL NOTICE

> **This system has NOT been validated for clinical decision-making. Deployment to field workers for patient-care decisions could result in serious harm or loss of life.**
>
> These rules are **design constraints** — they must be enforced in code, tested in gates, and audited before any field pilot. They are not aspirational.

---

## 1. Scope & Purpose

These 8 safety rules are **hard constraints** derived from the TWB v1.1 architecture and clinical risk analysis. Every component — ingest, enrich, index, edge runtime, UI — must comply. Violation = release blocker (Gate 8).

| Rule ID | Title | Category |
|---------|-------|----------|
| SR-01 | Offline-First — Zero External Network Calls at Runtime | Architecture |
| SR-02 | Cite or Abstain — Every Answer Cites Source or Says "Not Found" | Retrieval |
| SR-03 | No Generated Doses — Tables Verbatim; Summaries Refuse Dose Questions | Clinical |
| SR-04 | Atomic Updates + Instant Rollback — Signed Packs, No Partial State | Deployment |
| SR-05 | No PII / Telemetry — Zero Patient Data, Zero Usage Analytics | Privacy |
| SR-06 | Disclaimer on Every Screen — "Not a Substitute for Clinical Judgment" | UI/UX |
| SR-07 | Fail-Closed Gates — Not Run = Fail; Release Requires Explicit Pass | Quality |
| SR-08 | Deterministic Ingest — Stage 1 Uses Zero LLM (pdfplumber Only) | Build Pipeline |

---

## 2. Rule Specifications

### SR-01: Offline-First — Zero External Network Calls at Runtime

| Aspect | Requirement |
|--------|-------------|
| **Enforcement** | Edge server (`edge/server.py`) binds only to local interfaces (WiFi AP, localhost). No outbound sockets. |
| **Test** | Gate 4: Network isolation test — `tcpdump` on edge interface during 100 queries; assert 0 external connections. |
| **Build-Time Exception** | Builder machine calls NIM API (`integrate.api.nvidia.com`) for embeddings, rerank, enrichment. This is **build-time only**. |
| **Model Updates** | ONNX exports built in cloud, shipped in signed pack. Edge runs ONNX Runtime locally. |
| **Update Channel** | Signed packs delivered via USB, local WiFi, or sneakernet. No OTA from internet. |

**Code Locations:**
- `edge/server.py` — `ThreadingHTTPServer` binding logic
- `edge/core.py` — `SearchEngine` uses only local SQLite + ONNX
- `common/offline.py` — Offline embedding/rerank backends (ONNX, sentence-transformers)

---

### SR-02: Cite or Abstain — Every Answer Cites Source or Says "Not Found"

| Aspect | Requirement |
|--------|-------------|
| **Enforcement** | `Verifier` in `edge/core.py` checks every answer: `citation_count >= 1` OR `abstain == true`. |
| **Abstain Threshold** | Rerank score < calibrated threshold → forced abstain. Threshold set per Gate 2 calibration. |
| **Citation Format** | `{source_id, chunk_id, page_num, bbox, text_span}` — verifiable to page image. |
| **Table Citations** | Cell-level: `{source_id, table_id, row_idx, col_idx, cell_text, row_header, col_header}`. |
| **No Silent Failure** | If verifier fails, answer replaced with abstain message + reason. |

**Code Locations:**
- `edge/core.py` — `Verifier.verify()` + `SearchEngine.search()`
- `edge/server.py` — `/answer` SSE stream includes citations
- `eval/gates/run_gates.py` — Gate 1 (citation coverage), Gate 2 (abstention calibration)

---

### SR-03: No Generated Doses — Tables Verbatim; Summaries Refuse Dose Questions

| Aspect | Requirement |
|--------|-------------|
| **Enforcement** | `TableHandler` in `edge/core.py` returns stored table rows verbatim. No arithmetic, no interpolation. |
| **Dose Question Detection** | Query classifier flags `dose`/`dosing`/`mg/kg`/`mcg/kg` → routes to table lookup only. |
| **Summary Mode Refusal** | If `mode=summary` and query asks for dose → abstain with "Dose questions require table lookup." |
| **Unit Suspect Flag** | Chunks with `unit_suspect=true` (e.g., WHO mmol/L typo) → highlighted, never auto-corrected. |
| **Weight-Based Calculations** | NOT supported. User must read table row for their weight band. |

**Code Locations:**
- `edge/core.py` — `TableHandler.resolve_cells()`, `SearchEngine.route_query()`
- `ingest/ingest.py` — `unit_suspect` flag during table extraction
- `eval/gates/run_gates.py` — Gate 3 (dose refusal rate = 100%)

---

### SR-04: Atomic Updates + Instant Rollback — Signed Packs, No Partial State

| Aspect | Requirement |
|--------|-------------|
| **Pack Format** | SQLite DB + `manifest.json` + `manifest.json.sig` (Ed25519). |
| **Signature Chain** | Root key (offline, hardware-secured) → CI key (rotating) → Pack manifest. |
| **Staging** | New pack written to `packs/pending/kb-X.Y.Z/`. Verified in-place. |
| **Switch** | Single atomic rename: `packs/active/` → `packs/previous/`, `packs/pending/` → `packs/active/`. |
| **Rollback** | Rename `packs/previous/` → `packs/active/` (< 1 second). |
| **Boot Verification** | On startup: verify signature chain, schema hash, gate results in manifest. Fail = boot to previous. |

**Code Locations:**
- `indexer/build_pack.py` — `PackBuilder.build()` + `Signer.sign()`
- `edge/server.py` — `/update/stage`, `/update/apply`, `/update/rollback` endpoints
- `common/signing.py` — Ed25519 sign/verify, key loading

---

### SR-05: No PII / Telemetry — Zero Patient Data, Zero Usage Analytics

| Aspect | Requirement |
|--------|-------------|
| **Enforcement** | Code review gate: grep for `patient`, `pii`, `analytics`, `telemetry`, `tracking` → must return 0 hits in `src/edge/`. |
| **Query Logging** | Optional local audit log (JSONL) — **opt-in only**, contains: timestamp, query_hash, abstain_flag, latency_ms. **No query text.** |
| **Error Reporting** | Local only. No Sentry, no remote logging. |
| **Model Telemetry** | None. ONNX Runtime runs fully local. |

**Code Locations:**
- `edge/server.py` — No logging middleware for query content
- `edge/core.py` — No user identifiers in search path
- `eval/gates/run_gates.py` — Gate 7 (PII scan)

---

### SR-06: Disclaimer on Every Screen — "Not a Substitute for Clinical Judgment"

| Aspect | Requirement |
|--------|-------------|
| **Enforcement** | UI (`/ui/`) renders disclaimer banner on **every tab** (Ask, Build, Gates, Update). |
| **Text** | `"This tool provides cited guideline excerpts for reference only. It does not diagnose, recommend treatment, or replace clinical judgment. Verify all information against current authoritative sources before clinical use."` |
| **Dev Pack Banner** | If `manifest.dev_build == true` → red banner: `"DEVELOPMENT PACK — NOT FOR CLINICAL USE"`. |
| **API Response** | `/answer` SSE includes `disclaimer` field in first chunk. |

**Code Locations:**
- `ui/ask.html` — Disclaimer banner component
- `ui/build.html`, `ui/gates.html`, `ui/update.html` — Shared header with disclaimer
- `edge/server.py` — `/answer` injects disclaimer in stream

---

### SR-07: Fail-Closed Gates — Not Run = Fail; Release Requires Explicit Pass

| Gate | Name | Pass Criteria | Not Run = |
|------|------|---------------|-----------|
| 1 | Citation Coverage | ≥ 95% answers have ≥1 citation | **FAIL** |
| 2 | Abstention Calibration | Abstain rate within 5% of clinician panel | **FAIL** |
| 3 | Dose Refusal | 100% dose questions → table lookup or abstain | **FAIL** |
| 4 | Unit Suspect Detection | 100% known anomalies (WHO mmol/L) flagged | **FAIL** |
| 5 | Retrieval Recall@10 | ≥ 90% on clinician golden set | **FAIL** |
| 6 | Latency p99 | < 2s on target hardware (Jetson Orin Nano / Pi 5) | **FAIL** |
| 7 | PII / Telemetry Scan | Zero hits in edge codebase | **FAIL** |
| 8 | Release Eligibility | All Gates 1–7 pass + clinician sign-off + hardware smoke test | **FAIL** |

| Aspect | Requirement |
|--------|-------------|
| **Held-Out Split** | Gates 2, 5 use clinician-held-out queries (not in training/dev). |
| **Blockers List** | Gate 8 output: `release_eligible: bool`, `blockers: string[]`. Any blocker = no release. |
| **CI Enforcement** | `run_gates.py` exits non-zero on any fail. GitHub Action (Phase 9) blocks merge. |

**Code Locations:**
- `eval/gates/run_gates.py` — All 8 gates implementation
- `eval/gates/gate_*.py` — Individual gate modules
- `pipeline/run.py` — `--mode gates` runs full gate suite

---

### SR-08: Deterministic Ingest — Stage 1 Uses Zero LLM (pdfplumber Only)

| Aspect | Requirement |
|--------|-------------|
| **Enforcement** | `ingest/ingest.py` imports **no LLM libraries**. Uses: `pdfplumber`, `pypdf`, `pypdfium2`, `pdfminer.six`, `numpy`, `regex`, `dataclasses`. |
| **Table Extraction** | Custom geometry code: header resolution by position, merged cell detection via bbox overlap, cell-level text assignment. |
| **Second-Reader Verify** | Optional NIM Vision model (Stage 2) compares rendered page image vs extracted table. Disagreement → clinician queue. |
| **Language Detection** | Stopword-based (fastText-style), not LLM. |
| **Unit Flagging** | Regex patterns for `mmol/L`, `mg/dL`, `mcg/kg`, etc. — no LLM interpretation. |

**Code Locations:**
- `ingest/ingest.py` — `IngestPipeline.run()`, `TableExtractor`, `HeaderResolver`
- `enrich/enrich.py` — **Optional** NIM enrichment (Stage 2), controlled by `--mode nim|offline`
- `pipeline/run.py` — Stage orchestration

---

## 3. Enforcement Matrix

| Phase | New Code Must Satisfy | Gates That Enforce |
|-------|----------------------|-------------------|
| 1 (Architecture) | All 8 rules reflected in ADRs, contracts | — |
| 2 (Ingest) | SR-08 (zero LLM), SR-03 (unit flagging) | Gate 3, 4 |
| 3 (Enrich) | SR-01 (optional NIM only), SR-02 (enrichment citations) | Gate 1 |
| 4 (Index/Pack) | SR-04 (signing), SR-07 (gate results in manifest) | Gate 4, 7, 8 |
| 5 (Edge) | SR-01, SR-02, SR-03, SR-05, SR-06 | Gate 1, 2, 3, 5, 6, 7 |
| 6 (Gates) | SR-07 (all gates implemented) | All |
| 7 (UI) | SR-06 (disclaimer), SR-02 (citation display) | Gate 1, 6 |
| 8 (Benchmarks) | SR-01, SR-06 verified on target hardware | Gate 6 |
| 9 (CI/CD) | SR-07 (gates in CI), SR-04 (signing in CI) | Gate 8 |
| 10 (Clinical) | SR-02, SR-03 validated by clinicians | Gate 2, 3, 8 |

---

## 4. Amendment Process

These rules are **immutable** unless:
1. Clinical Lead + Safety Officer + Tech Lead **all** approve in writing
2. Amendment recorded in `docs/01-architecture-decisions.md` as new ADR
3. All affected gates updated and re-run
4. Full regression test suite passes (31+ tests)

**No exceptions for "urgency," "prototype," or "internal use only."**

---

## 5. Relationship to v1.0 (twb-rag-chat) Safety Assessment

The abandoned v1.0 prototype (`twb-rag-chat/SAFETY.md`) identified 10 critical gaps. **CMES-AI v1.1 addresses 8 of 10 by architecture:**

| v1.0 Gap | CMES-AI v1.1 Resolution |
|----------|-------------------------|
| LLM hallucination in answer path | **SR-02, SR-08** — Zero LLM in default answer path; extractive only |
| No dose safety | **SR-03** — Tables verbatim, dose refusal = 100% |
| No update mechanism | **SR-04** — Signed atomic updates with rollback |
| No audit trail | **SR-05** — Opt-in local audit log (no PII) |
| Keyword-only emergency detection | **N/A** — CMES-AI is reference tool, not triage; no emergency detection |
| No drug interactions | **N/A** — Out of scope (SR-03: no generated doses) |
| Pediatric/neonatal gaps | **SR-03** — Table-first covers all populations in source PDFs |
| Input validation gaps | **SR-08** — Structured ingest, no free-text parsing at runtime |
| No uncertainty quantification | **SR-02** — Abstain gate with calibrated threshold |
| No human-in-the-loop enforcement | **SR-06** — Disclaimer on every screen; dev pack banner |

**Remaining from v1.0 (by design, not gap):**
- No regulatory clearance (SaMD) — CMES-AI is clinical reference, not CDS
- No prospective clinical trial — Requires Phase 10 clinical validation

---

## 6. References

- [Problem Statement](00-problem-statement.md) — Clinical context, success criteria
- [Architecture Decisions](01-architecture-decisions.md) — ADR log (Phase 1)
- [Requirements](02-requirements.md) — Functional/non-functional (Phase 1)
- [Production Readiness](04-production-readiness.md) — Gaps, pilot criteria (Phase 9+)
- TWB v1.1 `ARCHITECTURE.md` — Architecture specification
- TWB v1.1 `HANDOFF_DOCUMENTATION.md` — ADR-1, release blockers
- TWB v1.0 `SAFETY.md` — Historical assessment (superseded)

---

*End of Safety Rules — Review with Safety Officer + Clinical Lead before Phase 1 sign-off.*