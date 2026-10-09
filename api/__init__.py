"""Flask application factory for the loopback-only desktop product."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import timedelta

from flask import Flask, jsonify, render_template, request

from desktop.version import APPLICATION_DEVELOPER, APPLICATION_VERSION
from infrastructure.paths import data_path, resource_path
from infrastructure.runtime_dependencies import inspect_runtime_dependencies
from infrastructure.secure_files import write_private_text
from r_runtime.config import RRuntimeConfig, is_production_runtime
from r_runtime.environment import HostREnvironment, REnvironmentError, VerifiedREnvironment
from r_runtime.external_dependencies import CORE_DEPENDENCY_IDS, inspect_external_dependencies
from r_runtime.library_settings import RLibrarySettings, load_r_library_settings, save_r_library_settings
from r_runtime.resource_policy import compute_local_resource_policy
from r_runtime.isolation import isolation_availability


log = logging.getLogger(__name__)


def _public_r_runtime(
    backend: str,
    verified_r: VerifiedREnvironment,
    library_settings: RLibrarySettings,
) -> dict[str, object]:
    """Serialize one verified local-R runtime consistently for all settings APIs."""

    runtime_environment = HostREnvironment(verified_r.rscript).process_environment(
        verified=verified_r
    )
    external_readiness = inspect_external_dependencies(
        dependency_ids=CORE_DEPENDENCY_IDS,
        environment=runtime_environment,
    )
    resource_policy = compute_local_resource_policy(environment=runtime_environment)
    isolation = isolation_availability()
    return {
        "backend": backend,
        "available": bool(isolation["available"]),
        "isolation": isolation,
        "source": "user-installed",
        "rscript_bin": str(verified_r.rscript),
        "package_library": str(verified_r.install_library),
        "library_mode": verified_r.library_mode,
        "user_library": str(verified_r.user_library),
        "managed_library_root": str(library_settings.managed_root),
        "custom_library_root": library_settings.custom_directory,
        "library_settings_warning": library_settings.load_warning,
        "installed_packages": verified_r.package_count,
        "runtime_id": verified_r.runtime_id,
        "r_version": verified_r.r_version,
        "worker_count": resource_policy.worker_count,
        "native_thread_count": resource_policy.native_thread_count,
        "memory_budget_bytes": resource_policy.memory_budget_bytes,
        "in_memory_threshold_bytes": resource_policy.in_memory_threshold_bytes,
        "external_readiness": external_readiness,
    }


def _max_upload_bytes() -> int | None:
    configured = os.getenv("BAA_MAX_UPLOAD_BYTES")
    if configured is None:
        # This product is loopback-only in both desktop and source modes. Local
        # disk capacity, not a legacy server upload ceiling, is the limit.
        return None
    try:
        return max(1, int(configured or str(200 * 1024 * 1024)))
    except ValueError:
        return 200 * 1024 * 1024


def _secret_key() -> str:
    configured = str(os.getenv("BAA_SECRET_KEY") or "").strip()
    if configured:
        return configured
    path = data_path("config", "desktop_flask_secret.key")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    write_private_text(path, value)
    return value


def _multiprocess_wsgi_gate():
    if is_production_runtime() and bool(request.environ.get("wsgi.multiprocess")):
        log.critical("Desktop request rejected: WSGI server is running multiple processes")
        return jsonify({"error": "本地应用进程模型无效。"}), 503
    return None


def _flask_shell() -> Flask:
    return Flask(
        __name__,
        template_folder=str(resource_path("templates")),
        static_folder=str(resource_path("static")),
    )


def create_app() -> Flask:
    from data.research_workspace import cleanup_research_storage

    cleanup_summary = cleanup_research_storage()
    if any(cleanup_summary.values()):
        log.info("Research storage cleanup: %s", cleanup_summary)

    app = _flask_shell()
    app.secret_key = _secret_key()
    app.permanent_session_lifetime = timedelta(days=30)
    app.config.update(
        MAX_CONTENT_LENGTH=_max_upload_bytes(),
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"],
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=False,
    )

    from .chat import bp as chat_bp
    from .jobs import bp as jobs_bp
    from .local_access import bp as local_access_bp, csrf_gate, local_request_gate
    from .models import bp as models_bp
    from .output import bp as output_bp
    from .research import bp as research_bp
    from .saved_sessions import bp as saved_sessions_bp

    for blueprint in (
        local_access_bp,
        models_bp,
        chat_bp,
        saved_sessions_bp,
        output_bp,
        jobs_bp,
        research_bp,
    ):
        app.register_blueprint(blueprint)

    app.before_request(_multiprocess_wsgi_gate)
    app.before_request(local_request_gate)
    app.before_request(csrf_gate)

    @app.after_request
    def apply_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            "form-action 'self'; script-src 'self' qrc:; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self';",
        )
        return response

    @app.get("/")
    def index():
        return render_template("research_workbench.html", app_developer=APPLICATION_DEVELOPER)

    @app.get("/api/health")
    def health():
        return {"ok": True, "status": "healthy", "service": "research-statistics-desktop"}

    @app.get("/api/system/mode")
    def system_mode():
        return {"ok": True, "mode": "research"}

    @app.get("/api/system/runtime")
    def system_runtime():
        from .state import config_manager

        provider = config_manager.get_default_provider()
        config = config_manager.get_config(provider)
        defaults = config_manager.DEFAULT_CONFIGS[provider]
        r_config = RRuntimeConfig.from_env()
        r_environment = HostREnvironment(r_config.rscript_bin or None)
        library_settings = load_r_library_settings()
        try:
            verified_r = r_environment.prepare()
            r_status = _public_r_runtime(r_config.backend, verified_r, library_settings)
        except REnvironmentError as exc:
            r_status = {
                "backend": r_config.backend, "source": "user-installed", "available": False,
                "error": str(exc), "library_mode": library_settings.mode,
                "managed_library_root": str(library_settings.managed_root),
                "custom_library_root": library_settings.custom_directory,
            }
        return {
            "ok": True,
            "mode": "desktop",
            "app": {
                "name": "Research Statistics Workbench",
                "version": APPLICATION_VERSION,
                "developer": APPLICATION_DEVELOPER,
            },
            "dependencies": inspect_runtime_dependencies(),
            "llm": {
                "provider": provider,
                "model": config.model if config else defaults["model"],
                "transport": "https",
                "base_url": defaults["base_url"],
                "configured": bool(config and config.api_key),
            },
            "r": r_status,
        }

    @app.post("/api/system/r-library")
    def update_r_library():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "请求必须是 JSON 对象。"}), 400
        try:
            settings = RLibrarySettings(
                mode=str(payload.get("mode") or "").strip().lower(),
                custom_directory=str(payload.get("custom_directory") or "").strip(),
            )
            local_r = HostREnvironment.locate()
            verified = HostREnvironment(
                local_r.rscript,
                library_settings=settings,
            ).prepare()
        except (REnvironmentError, ValueError) as exc:
            return jsonify({"error": str(exc), "code": "invalid_r_library"}), 400

        save_r_library_settings(settings)
        from .research import invalidate_template_runtime_inventory

        invalidate_template_runtime_inventory()
        return jsonify(
            {
                "ok": True,
                "r": _public_r_runtime("local", verified, settings),
            }
        )

    return app
