"""Shared singletons — import from here, never instantiate elsewhere."""

import logging
import re
import sys
from pathlib import Path

log = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.session import SessionManager
from LLM.llm_config_manager import get_config_manager
from data.workspace import workspace_manager
from infrastructure.paths import data_path


class _ChartStore:
    """Write-through local chart store keyed by an unguessable chart ID."""

    _ID_RE = re.compile(r"^[a-f0-9]{32}$")

    def __init__(self, store_dir: Path):
        self._dir = store_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, cid: str) -> Path:
        clean_id = str(cid or "")
        if not self._ID_RE.fullmatch(clean_id):
            raise KeyError(clean_id)
        return self._dir / f"{clean_id}.html"

    def put(self, cid: str, html: str) -> None:
        self._path(cid).write_text(str(html), encoding="utf-8")

    def __setitem__(self, cid: str, html: str):
        self.put(cid, html)

    def __getitem__(self, cid: str) -> str:
        p = self._path(cid)
        if not p.exists():
            raise KeyError(cid)
        return p.read_text(encoding="utf-8")

    def __contains__(self, cid: object) -> bool:
        try:
            p = self._path(str(cid))
        except KeyError:
            return False
        return p.exists()

    def get(self, cid: str, default=None):
        try:
            p = self._path(cid)
        except KeyError:
            return default
        return p.read_text(encoding="utf-8") if p.exists() else default


def _resolve_writable_dir(preferred: Path) -> Path:
    """Return *preferred* if writable, otherwise fall back to /tmp equivalent.

    Source checkouts can be read-only, so keep a bounded temporary fallback.
    """
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        # Quick write-test so we don't silently fail later
        test = preferred / ".write_test"
        test.write_text("ok")
        test.unlink()
        return preferred
    except OSError as e:
        log.warning("[state] chart store dir %s not writable, using fallback: %s", preferred, e)
        fallback = Path("/tmp") / "baa" / preferred.name
        fallback.mkdir(parents=True, exist_ok=True)
        try:
            # Chart HTML can embed research data; /tmp is shared, so keep the
            # fallback directory private to the current user.
            import os

            os.chmod(fallback, 0o700)
        except OSError:
            pass
        return fallback


_CHARTS_DIR = _resolve_writable_dir(data_path("outputs", "charts"))

session_manager: SessionManager = SessionManager()
config_manager = get_config_manager()
chart_store: _ChartStore = _ChartStore(_CHARTS_DIR)

# workspace_manager 已从 data.workspace 导入（模块级单例），直接可用
