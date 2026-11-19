"""Unit sanity checks for dosing/lab text. Source text is NEVER altered; we only flag it for review.

Motivating case: WHO newborn guideline table prints '260 mmol/L (15 mg/dL)'. 15 mg/dL bilirubin is ~257 umol/L,
so the printed unit is off by 1000x. 'Render verbatim' would faithfully reproduce the error, so ingest must flag it.
"""
import re

_PAIR = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mmol|\u00b5mol|\u03bcmol|umol)\s*/\s*[lL]\s*\(\s*(\d+(?:[.,]\d+)?)\s*mg\s*/\s*dl\s*\)", re.I)


def _f(x):
    return float(x.replace(",", "."))


def unit_flags(text: str):
    flags = []
    for m in _PAIR.finditer(text or ""):
        n, unit, mg = _f(m.group(1)), m.group(2).lower().replace("\u03bc", "\u00b5"), _f(m.group(3))
        if mg == 0:
            continue
        ratio = n / mg
        if unit == "mmol" and ratio > 5:
            flags.append({"kind": "unit_suspect", "match": m.group(0), "ratio": round(ratio, 2),
                          "detail": "printed mmol/L but the paired mg/dL value implies umol/L (x1000 discrepancy); "
                                    "likely a source typo - clinician must confirm"})
        elif unit in ("\u00b5mol", "umol") and ratio < 1:
            flags.append({"kind": "unit_suspect", "match": m.group(0), "ratio": round(ratio, 3),
                          "detail": "printed umol/L but the paired mg/dL value implies mmol/L"})
    return flags
