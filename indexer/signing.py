"""Ed25519 dual-signature chain: pinned ROOT key -> certifies CI key -> signs manifest.

Verification order on the edge (all must pass): root signature over the CI public key certificate,
CI signature over the canonical manifest (minus 'signatures'), then build_hash == sha256(kb.sqlite).
DEV keys are generated into keys/ and are NOT secret-safe: real root key lives offline with the owner.
"""
import base64
import datetime as dt
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from common.util import canonical_json

b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
unb64 = lambda s: base64.b64decode(s.encode())  # noqa: E731


def _raw_pub(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_priv(priv: Ed25519PrivateKey) -> bytes:
    return priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


def ensure_dev_keys(keys_dir="keys"):
    d = Path(keys_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name in ("root", "ci"):
        priv_p, pub_p = d / f"{name}.key", d / f"{name}.pub"
        if not priv_p.exists():
            k = Ed25519PrivateKey.generate()
            priv_p.write_text(b64(_raw_priv(k)))
            priv_p.chmod(0o600)
            pub_p.write_text(b64(_raw_pub(k.public_key())))
    return d


def load_priv(path) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(unb64(Path(path).read_text().strip()))


def load_pub_b64(path) -> str:
    return Path(path).read_text().strip()


def sign_manifest(manifest: dict, ci_priv, ci_key_id, root_priv, root_key_id, not_after=None):
    ci_pub_b64 = b64(_raw_pub(ci_priv.public_key()))
    not_after = not_after or (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=100)).date().isoformat()
    cert = {"ci_key_id": ci_key_id, "ci_public_key": ci_pub_b64, "not_after": not_after}
    body = {k: v for k, v in manifest.items() if k != "signatures"}
    out = dict(body)
    out["signatures"] = {
        "ci_key": {"key_id": ci_key_id, "algorithm": "Ed25519", "public_key": ci_pub_b64, "not_after": not_after,
                   "signature": b64(ci_priv.sign(canonical_json(body)))},
        "root_key": {"key_id": root_key_id, "algorithm": "Ed25519", "signature": b64(root_priv.sign(canonical_json(cert)))},
    }
    return out


def verify_manifest(manifest: dict, root_pub_b64: str, today=None):
    """Returns (ok, reason). Never raises on malformed input."""
    try:
        sigs = manifest.get("signatures")
        if not sigs:
            return False, "unsigned manifest"
        ci, root = sigs["ci_key"], sigs["root_key"]
        cert = {"ci_key_id": ci["key_id"], "ci_public_key": ci["public_key"], "not_after": ci["not_after"]}
        Ed25519PublicKey.from_public_bytes(unb64(root_pub_b64)).verify(unb64(root["signature"]), canonical_json(cert))
        today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
        if ci["not_after"] < today:
            return False, f"CI key expired on {ci['not_after']}"
        body = {k: v for k, v in manifest.items() if k != "signatures"}
        Ed25519PublicKey.from_public_bytes(unb64(ci["public_key"])).verify(unb64(ci["signature"]), canonical_json(body))
        return True, "signature chain valid"
    except InvalidSignature:
        return False, "invalid signature"
    except Exception as e:  # noqa: BLE001
        return False, f"malformed signature data: {type(e).__name__}"
