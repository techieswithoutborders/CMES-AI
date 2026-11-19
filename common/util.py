"""Shared helpers: hashing, canonical JSON, number/quantity extraction, tokenising."""
import hashlib
import json
import re
import sqlite3
import unicodedata

SCHEMA_VERSION = "1.1"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def round_bbox(b, nd=4):
    return {k: round(float(v), nd) for k, v in b.items()}


def clamp01(v):
    return max(0.0, min(1.0, float(v)))


# ---- numbers and quantities -------------------------------------------------
# thousands groups: "100 000", "1,000", "100\u202f000"; decimals: "0.5" or "0,5"
_NUM = r"\d{1,3}(?:[ ,\u202f\u00a0]\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?"
_UNIT = r"(?:[A-Za-z\u00b5\u03bc%]+(?:/[A-Za-z\u00b5\u03bc]+)*)"
QUANTITY_RE = re.compile(r"(?<![\w.])(" + _NUM + r")(?:\s?(" + _UNIT + r"))?")


def _canon_num(raw: str) -> str:
    s = raw
    if re.fullmatch(r"\d{1,3}(?:[ ,\u202f\u00a0]\d{3})+(?:\.\d+)?", s):
        s = re.sub(r"[ ,\u202f\u00a0]", "", s)
    else:
        s = s.replace(",", ".")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


_UNIT_OK = re.compile(
    r"^(mg|mcg|\u00b5g|ug|g|kg|ml|l|dl|iu|u|units?|%|mmol|\u00b5mol|umol|mol|meq|mm|cm|min|mins|minutes?|"
    r"hours?|hrs?|h|days?|weeks?|months?|years?|bpm|mmhg|kcal|j)(/[a-z\u00b5]+)*$")


def _canon_unit(u):
    if not u:
        return ""
    u = u.replace("\u03bc", "\u00b5").lower()
    return u if _UNIT_OK.match(u) else ""


def extract_quantities(text: str):
    """Return list of (number, unit) tuples; unit is '' when none follows."""
    out = []
    for m in QUANTITY_RE.finditer(text or ""):
        out.append((_canon_num(m.group(1)), _canon_unit(m.group(2))))
    return out


def extract_numbers(text: str):
    return [n for n, _ in extract_quantities(text)]


# ---- tokenising ---------------------------------------------------------------
STOP = set("""a an and are as at be by can do does for from has have how i in is it its me of on or should
the to was what when where which who why will with you your use used using give tell about per""".split())


def tokens(text: str, drop_stop=True):
    t = unicodedata.normalize("NFKC", (text or "").lower())
    toks = re.findall(r"[\w\u00b5]+", t)
    if drop_stop:
        toks = [x for x in toks if x not in STOP]
    return toks


DOSING_RE = re.compile(
    r"\b(dose|dosing|dosage|mg|mcg|ml|iu|units?|u/kg|mg/kg|how much|threshold|cut-?off|level|"
    r"phototherapy|exchange transfusion|infusion|rate)\b", re.I)


def dosing_intent(query: str) -> bool:
    return bool(DOSING_RE.search(query or ""))


def ro_connect(path, check_same_thread=True):
    """Open a knowledge pack read-only and IMMUTABLE (no locks, no journal files): packs never change in place,
    and this also works on read-only roots and network/FUSE mounts."""
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=check_same_thread)
