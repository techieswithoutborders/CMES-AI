# CMES-AI Clinical Safety Assessment

**Version:** 1.0  
**Date:** 2025-01-28  
**Classification:** CONFIDENTIAL — Internal Safety Review  
**Status:** **NOT CLEARED FOR CLINICAL USE**

---

## ⚠️ CRITICAL NOTICE

> **This system has NOT been validated for clinical decision-making. Deployment to field workers for patient-care decisions could result in serious harm or loss of life.**
>
> This document summarizes the safety gaps identified during architectural review of the CMES-AI v1.1 prototype. It is not a clearance document.

---

## Executive Summary

The CMES-AI v1.1 prototype implements a **deterministic, offline-first clinical reference assistant** with strong safeguards:

| Strength | Implementation |
|----------|----------------|
| **Zero LLM in default answer path** | Extractive retrieval only (BM25 + HNSW + ONNX rerank) |
| **Table-first retrieval** | Tables returned verbatim; cell-level highlights; verifier checks every number |
| **Dose refusal by design** | 100% of dose questions → table lookup or abstain |
| **Fail-closed gates** | 8 gates; not_run = fail; release requires explicit pass + clinician sign-off |
| **Signed atomic updates** | Ed25519 chain (Root → CI); instant rollback |
| **No PII / telemetry** | Zero patient data, zero usage analytics |

**Critical gaps remain** before any clinical deployment:

| Gap | Severity | Blocker |
|-----|----------|---------|
| No regulatory clearance (FDA/CE/Health Canada) | Critical | Phase 10 |
| No prospective clinical validation | Critical | Phase 10 |
| No clinician golden set (0/2 recruited) | Critical | Phase 10 |
| Live NIM embeddings untested (mock only) | High | Phase 1 |
| Target hardware benchmarks unrun | High | Phase 8 |
| Audio consent for TTS narration | Medium | Phase 9 |

---

## Use Case Suitability Matrix

| Use Case | Verdict | Conditions |
|----------|---------|------------|
| **Medical education / training (supervised)** | ✅ Acceptable | With disclaimer |
| **Protocol reference lookup** | ⚠️ Conditional | Verify against source page image |
| **Dose verification (table lookup)** | ⚠️ Conditional | Manual double-check required |
| **Clinical score calculation** | ⚠️ Conditional | Verify inputs; scores via rule engine only |
| **Emergency triage** | ❌ **PROHIBITED** | Not a triage tool; no emergency detection |
| **Primary clinical decision support** | ❌ **PROHIBITED** | No regulatory clearance; reference only |
| **Pediatric/neonatal dosing** | ❌ **PROHIBITED** | Table covers populations in source; no independent validation |
| **Polypharmacy / interaction checking** | ❌ **PROHIBITED** | Out of scope — no interaction engine |

---

## Existing Safety Features (v1.1 Prototype)

| Feature | Implementation | Risk Mitigated |
|---------|---------------|----------------|
| **Deterministic Ingest (Stage 1)** | pdfplumber + custom geometry; zero LLM | Eliminates hallucination at source |
| **Second-Reader Vision Verify** | Optional NIM Vision compares rendered page vs extracted table | Catches extraction errors (e.g., WHO mmol/L typo) |
| **Table-First Retrieval** | One chunk per table; headers resolved by position; merged cells preserved | Dose numbers always know their row/column context |
| **Cite-or-Abstain Engine** | Verifier requires citation_count ≥ 1 OR forced abstain | No uncited claims |
| **Abstention Threshold** | Calibrated rerank score threshold; held-out clinician calibration | Reduces false confidence |
| **Unit Suspect Flag** | Regex detection of `mmol/L`, `mg/dL`, etc.; flagged in chunk metadata | Known anomalies (WHO mmol/L) never auto-corrected |
| **Signed Atomic Updates** | Ed25519 chain; staged verify → atomic swap → instant rollback | No partial/corrupted packs |
| **Disclaimer on Every Screen** | Banner on all UI tabs; dev pack red banner; API injects disclaimer | Clear boundary: reference, not diagnosis |
| **Fail-Closed Gates** | 8 gates; release_eligible + blockers list | Explicit pass required |

---

## Critical Safety Gaps (Blockers for Clinical Use)

### 1. No Formal Clinical Validation / Regulatory Clearance
- No FDA SaMD classification determination (likely Class II — Clinical Decision Support)
- No IEC 62304 medical device software lifecycle compliance
- No ISO 14971 risk management file
- No clinical evaluation report per MDCG 2020-16 / FDA guidance
- No post-market surveillance plan

### 2. No Clinician Golden Set
- **0 of 2 clinicians recruited** for held-out calibration set
- Gates 2 (abstention) and 5 (recall@10) require clinician panel
- Golden set size target: ≥ 200 clinically representative queries

### 3. Live NIM Path Untested
- All 4 NIM mock tests pass (fake server)
- **No live API key tested** — embedding quality, latency, cost unknown
- ONNX export pipeline scripted but not run on target models

### 4. Target Hardware Unbenchmarked
- Jetson Orin Nano / Raspberry Pi 5 + NVMe not procured
- Latency p99 < 2s (Gate 6) unverified on actual edge device
- Power/thermal profile unknown

### 5. No Guideline Update Mechanism
- ChromaDB/SQLite contains only what was ingested at build time
- No mechanism for guideline updates (e.g., 2024 WHO sepsis bundle changes)
- No "last updated" metadata surfaced to user

### 6. Audio Consent for Narration
- Video explainer uses `en-US-AndrewNeural` (Microsoft Edge TTS)
- **No written consent** from voice talent / license verification for clinical distribution

### 7. No Audit Trail for Clinical Queries
- Session history storage not implemented
- No immutable log of: query → retrieved chunks → verifier decision → answer shown
- Required for clinical governance, liability, learning health system

### 8. Input Validation at Runtime
- Query classifier routes dose questions to table lookup
- No validation that user-provided context (weight, age) is physiologically plausible
- Free-text query parsing — no structured input forms on edge

### 9. No Human-in-the-Loop Enforcement
- System presents as answer engine
- No forced "verify with supervising clinician" step
- No role-based access control (nurse vs physician vs student)

### 10. Uncertainty Quantification
- Single heuristic abstention threshold — no confidence intervals
- No calibrated "I don't know" with reliability guarantees

---

## High-Risk Scenarios This System Could Mishandle

| Scenario | Why It's Dangerous | Current Behavior |
|----------|-------------------|------------------|
| **"Fentanyl 100mcg IV for 50kg patient — is this safe?"** | Dose validation only checks table limits, not clinical context (opioid-naive, respiratory status) | Returns table row for 50kg; verifier passes; no clinical context warning |
| **"Patient on morphine, now needs ketamine — any interaction?"** | No drug-drug interaction database | Returns separate table rows; no interaction warning |
| **"Newborn 2.5kg, ampicillin dose?"** | Neonatal dosing in source tables; but no independent validation | Returns verbatim table cell; verifier passes |
| **"Sepsis protocol for immunocompromised patient?"** | Standard protocol ≠ immunocompromised protocol | Returns standard protocol without qualification |
| **"Warfarin dose for AF with CHA2DS2-VASc 4?"** | Warfarin not in source formularies; CHA2DS2-VASc score only | Abstains (no source) — correct but user may not understand why |
| **"Patient allergic to penicillin — sepsis antibiotics?"** | Allergy not captured in ingestion or query | Returns standard regimen — anaphylaxis risk if user doesn't cross-check |

---

## Minimum Requirements Before Clinical Deployment

### Regulatory & Governance
- [ ] FDA SaMD classification determination
- [ ] IEC 62304 compliance
- [ ] ISO 14971 risk management file
- [ ] Clinical evaluation report
- [ ] Post-market surveillance plan

### Technical Safety
- [ ] Formal verification of rule engine calculations (property-based testing)
- [ ] LLM judge-based groundedness evaluation (not heuristic) with published thresholds
- [ ] Adversarial testing: prompt injection, jailbreak, edge cases (pediatric, obese, renal failure, polypharmacy)
- [ ] Continuous integration of guideline updates with versioned KB + diff alerts
- [ ] Structured input forms for calculations (weight, age, CrCl, allergies) — no free-text parsing
- [ ] Drug interaction database integration (Liverpool, Lexicomp, or open-source)
- [ ] Audit logging to immutable store (WORM) for every clinical query
- [ ] Uncertainty calibration: reliable "I don't know" with confidence intervals

### Operational
- [ ] Clinical champion + safety officer assigned
- [ ] Training curriculum for end users (limitations, when NOT to use)
- [ ] Incident reporting pathway for near-misses
- [ ] Regular re-evaluation (quarterly minimum) against current guidelines

---

## Evaluation Framework Status

The `eval/gates/run_gates.py` framework includes:
- 8 fail-closed gates (citation coverage, abstention calibration, dose refusal, unit suspect, recall@10, latency, PII scan, release eligibility)
- Gates 2, 5 require clinician-held-out queries
- Composite release eligibility: all gates pass + clinician sign-off + hardware smoke test

**Gap:** These are **regression tests against curated cases**, not:
- Prospective clinical validation
- Adversarial/red-team testing
- Uncertainty calibration measurement
- Real-world workflow integration testing

---

## Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-01-28 | Safety Review | Initial comprehensive safety assessment for CMES-AI v1.1 |

---

## Escalation Contacts

| Role | Contact |
|------|---------|
| Project Safety Officer | @cmes-safety |
| Clinical Champion | @cmes-clinical |
| Regulatory Lead | @cmes-regulatory |
| On-call Engineer | @cmes-oncall |

---

## Appendix: v1.1 Rule Engine Coverage (Extractive Only)

CMES-AI v1.1 **does not have a rule engine** — it uses extractive table retrieval. All clinical calculations (dosages, scores, protocols) come from source PDF tables verbatim.

| Source Tier | Example Documents | Tables Extracted |
|-------------|-------------------|------------------|
| Tier 1 (WHO) | Pocket Book, EML, MNH, ILCOR | 4 tables in prototype |
| Tier 2 (MSF) | Clinical Guidelines | 0 in prototype |
| Tier 3 (National) | Standard Treatment Guidelines | 0 in prototype |

**Critical observation:** The prototype ingested 15/16 PDFs (1 MCQ excluded), yielding 893 chunks and 4 tables. Table coverage is limited to what exists in source PDFs.

---

*End of Safety Assessment — Review with Safety Officer + Clinical Lead before Phase 1 sign-off.*