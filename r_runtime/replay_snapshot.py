"""Bounded, private copies of the exact helper and mounted checkpoint inputs."""
from __future__ import annotations

import base64
import hashlib
import zlib
from pathlib import Path

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def capture_replay_snapshot(helper: Path, checkpoints: Path | None) -> dict:
    files, omissions = {}, []
    remaining = MAX_SNAPSHOT_BYTES
    candidates = [("baa_helpers.R", helper)]
    if checkpoints is not None and checkpoints.is_dir():
        candidates += [("checkpoints/" + p.name, p) for p in sorted(checkpoints.iterdir())]
    for name, path in candidates:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > remaining:
                raise ValueError("input is not an ordinary file within the snapshot budget")
            with path.open("rb") as stream:
                raw = stream.read(remaining + 1)
            if len(raw) > remaining:
                raise ValueError("input exceeded the snapshot budget")
            remaining -= len(raw)
            files[name] = {"zlib_base64": base64.b64encode(zlib.compress(raw)).decode("ascii"),
                           "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        except (OSError, ValueError):
            omissions.append(name)
    return {"status": "partial" if omissions else "complete", "files": files, "omissions": omissions}


def unpack_replay_snapshot(snapshot: dict) -> dict[str, bytes]:
    result = {}
    remaining = MAX_SNAPSHOT_BYTES
    for name, record in snapshot.get("files", {}).items():
        if name != "baa_helpers.R" and not (
            name.startswith("checkpoints/") and name.count("/") == 1
            and Path(name).suffix == ".rds" and ".." not in name and "\\" not in name
        ):
            raise ValueError("Invalid replay snapshot filename")
        packed = str(record.get("zlib_base64", ""))
        if len(packed) > MAX_SNAPSHOT_BYTES * 2:
            raise ValueError("Replay snapshot exceeds its budget")
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(packed, validate=True), remaining + 1)
        if not decoder.eof or decoder.unused_data or len(raw) > remaining:
            raise ValueError("Incomplete or oversized replay snapshot")
        if len(raw) != record.get("size") or hashlib.sha256(raw).hexdigest() != record.get("sha256"):
            raise ValueError("Replay snapshot fingerprint mismatch")
        remaining -= len(raw)
        result[name] = raw
    return result
