"""Inspect the active application's Python/Qt dependencies on this host.

This report is independent of R and of optional scientific toolchains. It only
reads the source requirements and imports installed components; it never
installs packages, invokes a model, or changes the user's environment.
"""

from __future__ import annotations

import importlib
from importlib import metadata
import platform
import sys

from infrastructure.paths import resource_path


_PACKAGE_MODULES = {
    "flask": ("Flask", ("flask",)),
    "waitress": ("Waitress", ("waitress",)),
    "PySide6": ("PySide6 / Qt WebEngine", (
        "PySide6.QtCore", "PySide6.QtWidgets", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    )),
    "openai": ("模型 API 客户端", ("openai",)),
    "httpx": ("HTTPX", ("httpx",)),
    "Pillow": ("Pillow", ("PIL.Image",)),
    "openpyxl": ("Excel 导出", ("openpyxl",)),
    "python-docx": ("Word 导出", ("docx",)),
}


def inspect_runtime_dependencies() -> dict[str, object]:
    """Report the actual interpreter and importable source runtime packages."""
    requirements = []
    for line in resource_path("requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, separator, version = line.partition("==")
        if not separator:
            raise ValueError(f"Unsupported runtime requirement: {line}")
        requirements.append((name.strip(), version.strip()))

    python_ready = sys.version_info >= (3, 10)
    states = [{
        "dependency_id": "python",
        "label": "Python",
        "required_version": ">=3.10",
        "installed_version": platform.python_version(),
        "status": "ready" if python_ready else "version_mismatch",
        "detail": "当前应用解释器" if python_ready else "需要 Python 3.10 或更高版本",
    }]
    qt_version = ""
    for name, required in requirements:
        label, modules = _PACKAGE_MODULES.get(name, (name, ()))
        installed = ""
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            status, detail = "missing", "当前 Python 环境未安装"
        else:
            try:
                for module in modules:
                    loaded = importlib.import_module(module)
                    if module == "PySide6.QtCore":
                        qt_version = loaded.qVersion()
            except (ImportError, OSError, RuntimeError):
                status, detail = "unavailable", "已安装但无法加载，请检查当前系统组件并重新安装运行依赖"
            else:
                if installed != required:
                    status, detail = "version_mismatch", f"已安装 {installed}，项目要求 {required}"
                else:
                    status, detail = "ready", "已安装并可加载"
        states.append({
            "dependency_id": name,
            "label": label,
            "required_version": required,
            "installed_version": installed,
            "status": status,
            "detail": detail,
        })

    counts = {status: sum(item["status"] == status for item in states)
              for status in ("ready", "missing", "unavailable", "version_mismatch")}
    system = platform.system()
    return {
        "source": "current-application-environment",
        "platform": {
            "system": {"Darwin": "macOS"}.get(system, system),
            "version": platform.mac_ver()[0] if system == "Darwin" else platform.release(),
            "architecture": platform.machine(),
        },
        "python_version": platform.python_version(),
        "qt_version": qt_version,
        "ready": counts["ready"] == len(states),
        "counts": counts,
        "states": states,
    }
