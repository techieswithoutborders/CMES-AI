"""Signed-pack updater: verify -> stage in the INACTIVE slot -> re-verify -> smoke test -> atomic switch -> keep previous for rollback.

State layout:  state/slots/A, state/slots/B, state/current.json, state/previous.json, state/incoming/<pack>/
The switch is a single os.replace() of current.json after fsync, so a power cut leaves either the old or the new pack active, never a mix.
The updater only reads packs from state/incoming (the USB/tablet drop folder) - the API never accepts arbitrary filesystem paths.
"""
import json
import os
import shutil
from pathlib import Path

from common.util import ro_connect, sha256_file
from indexer import signing

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"


class UpdateError(Exception):
    pass


def _fsync_dir(p):
    try:
        fd = os.open(str(p), os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass


class Updater:
    def __init__(self, state_dir, root_pub_b64, allow_dev=False):
        self.state = Path(state_dir)
        self.root_pub = root_pub_b64
        self.allow_dev = allow_dev
        (self.state / "slots").mkdir(parents=True, exist_ok=True)
        (self.state / "incoming").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ state ---
    def _read(self, name):
        p = self.state / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            return None

    def _write_atomic(self, name, obj):
        tmp = self.state / (name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state / name)
        _fsync_dir(self.state)

    def current(self):
        c = self._read("current.json")
        if c and (self.state / "slots" / c["slot"] / "kb.sqlite").exists():
            return c
        return None

    def active_dir(self):
        c = self.current()
        return (self.state / "slots" / c["slot"]) if c else None

    def status(self):
        return {"current": self.current(), "previous": self._read("previous.json"),
                "incoming": sorted(p.name for p in (self.state / "incoming").iterdir() if p.is_dir()),
                "allow_dev_packs": self.allow_dev}

    # ----------------------------------------------------------- verify ---
    def verify_pack(self, pack_dir):
        pack = Path(pack_dir)
        mp, db = pack / "manifest.json", pack / "kb.sqlite"
        if not mp.exists() or not db.exists():
            raise UpdateError("pack incomplete: manifest.json or kb.sqlite missing")
        try:
            manifest = json.loads(mp.read_text())
        except Exception:  # noqa: BLE001
            raise UpdateError("manifest is not valid JSON")
        try:
            import jsonschema
            jsonschema.validate(manifest, json.loads((SPEC_DIR / "manifest_schema.json").read_text()))
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            raise UpdateError(f"manifest does not match schema: {str(e).splitlines()[0][:160]}")
        ok, why = signing.verify_manifest(manifest, self.root_pub)
        if not ok:
            raise UpdateError(f"signature check failed: {why}")
        if manifest.get("channel") != "release" and not self.allow_dev:
            raise UpdateError("dev-channel pack refused on a production box")
        if sha256_file(db) != manifest["build_hash"]:
            raise UpdateError("kb.sqlite does not match signed build_hash (corrupt or tampered)")
        for h in manifest["page_image_hashes"]:
            f = pack / "blobs" / f"{h}.webp"
            if not f.exists() or sha256_file(f) != h:
                raise UpdateError(f"page image {h[:12]} missing or corrupt")
        import sqlite3
        try:
            con = ro_connect(db)
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise UpdateError("sqlite integrity_check failed")
            n = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
            nv = con.execute("SELECT count(*) FROM vectors").fetchone()[0]
            con.close()
        except sqlite3.DatabaseError as e:
            raise UpdateError(f"sqlite error: {e}")
        if n != manifest["chunk_count"] or nv != n:
            raise UpdateError("chunk/vector count does not match manifest")
        return manifest

    def _smoke(self, pack_dir):
        from edge.core import Pack
        try:
            p = Pack(pack_dir)
            if not p.chunks:
                raise UpdateError("smoke test: pack has no chunks")
            p.fts(["the"], 1)
        except UpdateError:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateError(f"smoke test failed: {type(e).__name__}: {e}")

    # ------------------------------------------------------------ apply ---
    def install(self, pack_dir, _fail_after_copy=False):
        manifest = self.verify_pack(pack_dir)
        cur = self.current()
        slot = "B" if (cur and cur["slot"] == "A") else "A"
        dest = self.state / "slots" / slot
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(pack_dir, dest)
        if _fail_after_copy:
            raise UpdateError("simulated power loss after copy, before switch")
        try:
            self.verify_pack(dest)  # re-verify the staged copy, not the source
            self._smoke(dest)
        except UpdateError:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        if cur:
            self._write_atomic("previous.json", cur)
        self._write_atomic("current.json", {"slot": slot, "version": manifest["pack_version"], "channel": manifest.get("channel")})
        return {"success": True, "new_version": manifest["pack_version"], "slot": slot, "rollback_available": cur is not None}

    def apply_incoming(self, name):
        if not name or "/" in name or name.startswith("."):
            raise UpdateError("invalid pack name")
        d = self.state / "incoming" / name
        if not d.is_dir():
            raise UpdateError("no such pack in the incoming folder")
        return self.install(d)

    def rollback(self):
        prev = self._read("previous.json")
        if not prev or not (self.state / "slots" / prev["slot"] / "kb.sqlite").exists():
            raise UpdateError("no previous pack available")
        try:
            self.verify_pack(self.state / "slots" / prev["slot"])
        except UpdateError as e:
            raise UpdateError(f"previous pack failed verification: {e}")
        cur = self.current()
        self._write_atomic("current.json", prev)
        if cur:
            self._write_atomic("previous.json", cur)
        return {"success": True, "new_version": prev["version"], "rollback_available": cur is not None}
