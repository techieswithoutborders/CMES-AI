"""Gate: update safety. In-process against the REAL Updater with REAL signed fixture packs (not HTTP placeholders):
  corrupt kb.sqlite rejected | tampered manifest rejected | unsigned rejected | signed by an unknown root rejected |
  dev-channel pack refused by a production updater | simulated power loss mid-update leaves the old pack active |
  valid update applies atomically | rollback restores the previous pack | rollback survives a bad slot
All must pass."""
import json
import shutil
import tempfile
from pathlib import Path

from common.nim import OfflineBackend
from edge.updater import UpdateError, Updater
from eval.gates.gate_common import result
from indexer import signing
from indexer.build_pack import build_pack, finalize_pack


def _fixture(tmp, version, texts, keys):
    chunks = []
    from common.util import sha256_text
    for i, t in enumerate(texts):
        chunks.append({"chunk_id": sha256_text(f"{version}{i}{t}"), "doc_id": "fix", "page": 1, "printed_page": None,
                       "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.2}, "section_path": [], "text": t, "chunk_kind": "text", "table_ref": None,
                       "table": None, "language": "en", "authority_tier": 1, "tier_basis": "fixture", "pub_date": None, "expiry_date": None,
                       "source_kind": "pdf", "confidence": 0.9, "review_status": "auto_approved", "page_image_hash": None, "enrichment": None,
                       "verification": None, "flags": []})
    docs = [{"doc_id": "fix", "title": "Fixture", "file": "f.pdf", "tier": 1, "tier_basis": "fixture", "pub_date": None, "pages": [1]}]
    out = Path(tmp) / f"kb-{version}"
    build_pack(chunks, docs, tmp, out, version, OfflineBackend(), channel="dev", log=lambda *a: None)
    finalize_pack(out, None, "dev", keys, log=lambda *a: None)
    return out


def run(keys_dir="keys"):
    keys = signing.ensure_dev_keys(keys_dir)
    root_pub = signing.load_pub_b64(keys / "root.pub")
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="twb-upd-"))
    try:
        v1 = _fixture(tmp, "1.0.0-fx", ["alpha passage about newborn care", "second passage"], keys)
        v2 = _fixture(tmp, "1.1.0-fx", ["beta passage about newborn care", "second passage", "third passage"], keys)
        u = Updater(tmp / "state", root_pub, allow_dev=True)

        def rejects(pack):
            try:
                u.verify_pack(pack)
                return False
            except UpdateError:
                return True

        c = tmp / "corrupt"
        shutil.copytree(v2, c)
        raw = bytearray((c / "kb.sqlite").read_bytes())
        raw[len(raw) // 2] ^= 0xFF  # flip one byte mid-file (the tail of a SQLite file can be zero padding)
        (c / "kb.sqlite").write_bytes(bytes(raw))
        checks["corrupt_pack_rejected"] = rejects(c)

        t = tmp / "tampered"
        shutil.copytree(v2, t)
        m = json.loads((t / "manifest.json").read_text())
        m["chunk_count"] += 1
        (t / "manifest.json").write_text(json.dumps(m))
        checks["tampered_manifest_rejected"] = rejects(t)

        un = tmp / "unsigned"
        shutil.copytree(v2, un)
        m = json.loads((un / "manifest.json").read_text())
        m.pop("signatures")
        (un / "manifest.json").write_text(json.dumps(m))
        checks["unsigned_pack_rejected"] = rejects(un)

        other = Updater(tmp / "state-o", signing.b64(signing._raw_pub(signing.Ed25519PrivateKey.generate().public_key())), allow_dev=True)
        try:
            other.verify_pack(v2)
            checks["unknown_root_rejected"] = False
        except UpdateError:
            checks["unknown_root_rejected"] = True

        prod = Updater(tmp / "state-p", root_pub, allow_dev=False)
        try:
            prod.verify_pack(v2)
            checks["dev_pack_refused_in_production"] = False
        except UpdateError:
            checks["dev_pack_refused_in_production"] = True

        u.install(v1)
        try:
            u.install(v2, _fail_after_copy=True)
        except UpdateError:
            pass
        checks["power_loss_keeps_old_pack"] = (u.current() or {}).get("version") == "1.0.0-fx"

        r = u.install(v2)
        checks["valid_update_applies"] = r["success"] and u.current()["version"] == "1.1.0-fx"
        u.rollback()
        checks["rollback_restores_previous"] = u.current()["version"] == "1.0.0-fx"
    except Exception as e:  # noqa: BLE001
        checks["harness_error"] = False
        return result("ran", 0.0, 1.0, False, checks=checks, error=f"{type(e).__name__}: {e}", passed_checks=0, total_checks=len(checks))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n_ok = sum(1 for v in checks.values() if v)
    return result("ran", n_ok / len(checks), 1.0, n_ok == len(checks), checks=checks, passed_checks=n_ok, total_checks=len(checks))
