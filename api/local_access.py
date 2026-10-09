"""Loopback access, launch-token exchange, and local session helpers."""

from __future__ import annotations

import hmac
import hashlib
import ipaddress
import os
import secrets

from flask import Blueprint, jsonify, redirect, request, session as flask_session, url_for

from infrastructure.paths import data_path
from infrastructure.secure_files import write_private_text


bp = Blueprint("local_access", __name__)


def _is_loopback(value: str) -> bool:
    try:
        return ipaddress.ip_address(str(value or "")).is_loopback
    except ValueError:
        return False


def _launch_token() -> str:
    return str(os.getenv("BAA_DESKTOP_TOKEN") or "").strip()


def _launch_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def local_request_gate():
    """Allow loopback access; desktop launches also require their launch token."""
    if not _is_loopback(request.remote_addr or ""):
        return jsonify({"error": "local application accepts loopback clients only"}), 403
    token = _launch_token()
    if not token:
        return None
    session_launch = str(flask_session.get("desktop_launch") or "")
    if session_launch and hmac.compare_digest(session_launch, _launch_id(token)):
        return None
    if request.path in {"/desktop/bootstrap", "/api/health"}:
        return None
    return jsonify({"error": "desktop session required"}), 401


def csrf_gate():
    """Protect local state changes from arbitrary pages reaching localhost."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    if os.getenv("PYTEST_CURRENT_TEST") and not _launch_token() and os.getenv("BAA_CSRF_FORCE") != "1":
        return None
    expected = str(flask_session.get("csrf_token") or "")
    supplied = str(request.headers.get("X-CSRF-Token") or "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return jsonify({"error": "CSRF token missing or invalid"}), 403
    return None


def create_session_for_request():
    from .state import session_manager

    sess = session_manager.create()
    write_private_text(data_path("runtime", "current-session-id"), sess.session_id)
    return sess


def current_session_id() -> str:
    from data.session import validate_session_id

    try:
        value = data_path("runtime", "current-session-id").read_text(encoding="utf-8").strip()
        return validate_session_id(value)
    except (OSError, ValueError):
        return ""


def session_for_request(sid: str):
    from data.session import validate_session_id
    from .state import session_manager

    try:
        safe_sid = validate_session_id(sid)
    except ValueError:
        from flask import abort

        abort(400, description="invalid session id")
    return session_manager.get_or_create(safe_sid)


@bp.get("/desktop/bootstrap")
def desktop_bootstrap():
    configured = _launch_token()
    supplied = str(request.args.get("token") or "")
    if configured and (not supplied or not hmac.compare_digest(configured, supplied)):
        return jsonify({"error": "invalid desktop launch token"}), 401
    flask_session.clear()
    flask_session["desktop_launch"] = _launch_id(configured) if configured else "source-mode"
    flask_session["csrf_token"] = secrets.token_urlsafe(32)
    flask_session.permanent = True
    return redirect(url_for("index"))


@bp.get("/api/local/session")
def local_session():
    token = str(flask_session.get("csrf_token") or "")
    if not token:
        token = secrets.token_urlsafe(32)
        flask_session["csrf_token"] = token
    return jsonify(
        {
            "ok": True,
            "mode": "desktop" if _launch_token() else "browser",
            "csrf_token": token,
            "current_session_id": current_session_id(),
        }
    )
