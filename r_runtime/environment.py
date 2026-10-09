"""Discover and prepare the user's local R installation and package library."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from infrastructure.paths import data_path
from r_runtime.library_settings import (
    MANAGED_LIBRARY_MODE,
    USER_LIBRARY_MODE,
    RLibrarySettings,
    load_r_library_settings,
)
from r_runtime.external_dependencies import inspect_external_dependencies
from r_runtime.tempfiles import RTemporaryDirectoryError, r_temporary_environment
from r_runtime.package_policy import (
    PACKAGE_GITHUB_SOURCES,
    PACKAGE_REPOSITORIES,
    package_setup_requirements,
)


_MINIMUM_R_VERSION = (4, 3)
_PACKAGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9.]*$")
_SECRET_ENV_NAME = re.compile(r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_?KEY)", re.I)
_PACKAGE_INSTALL_LOCK = threading.Lock()
_UTF8_LOCALE = "C.UTF-8"


def _is_same_or_child(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


# ── Package installation script & failure diagnosis ─────────────────────────


def default_binary_fallback_repo() -> str:
    """Binary-capable CRAN mirror used when stock mirrors only offer source.

    Apple silicon is the pain case: whenever the installed R release is newer
    than CRAN's arm64 binaries, install.packages() silently falls back to
    source compilation and users get flooded with gfortran/Xcode errors.
    Posit Package Manager serves arm64 binaries for every R version, so it is
    the default fallback round on macOS arm64; operators may override or
    disable it via BAA_CRAN_BINARY_FALLBACK.
    """
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "https://packagemanager.posit.co/cran/__macos__/arm64/latest"
    return ""


def build_install_script(
    repository_entries: str,
    github_entries: str,
    binary_fallback_default: str = "",
) -> str:
    """Assemble the multi-round R installation script (mirror → fallback →
    Bioconductor → binary fallback → GitHub sources)."""
    return r"""
packages <- unique(commandArgs(TRUE))
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
options(timeout = max(600L, as.integer(getOption("timeout", 60L))))
install_from_cran <- function(targets, repository) {
  if (!length(targets)) return(invisible(NULL))
  options(repos = c(
    CRAN = repository,
    __PACKAGE_REPOSITORIES__
  ))
  try(suppressWarnings(utils::install.packages(
    targets,
    dependencies = c("Depends", "Imports", "LinkingTo"),
    Ncpus = max(1L, parallel::detectCores(logical = FALSE) - 1L)
  )), silent = TRUE)
}
if (length(missing)) {
  install_from_cran(
    missing,
    Sys.getenv("BAA_CRAN_MIRROR", "https://mirrors.tuna.tsinghua.edu.cn/CRAN")
  )
}
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) {
  install_from_cran(
    missing,
    Sys.getenv("BAA_CRAN_FALLBACK", "https://cloud.r-project.org")
  )
}
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) {
  if (!requireNamespace("BiocManager", quietly = TRUE)) {
    install_from_cran("BiocManager", Sys.getenv("BAA_CRAN_FALLBACK", "https://cloud.r-project.org"))
  }
  options(BioC_mirror = Sys.getenv("BAA_BIOC_MIRROR", "https://mirrors.tuna.tsinghua.edu.cn/bioconductor"))
  try(BiocManager::install(missing, ask = FALSE, update = FALSE), silent = TRUE)
}
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing) && requireNamespace("BiocManager", quietly = TRUE)) {
  options(BioC_mirror = Sys.getenv("BAA_BIOC_FALLBACK", "https://bioconductor.org"))
  try(BiocManager::install(missing, ask = FALSE, update = FALSE), silent = TRUE)
}
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
binary_fallback <- Sys.getenv("BAA_CRAN_BINARY_FALLBACK", "__BINARY_FALLBACK_DEFAULT__")
if (length(missing) && nzchar(binary_fallback)) {
  install_from_cran(missing, binary_fallback)
}
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
github_sources <- c(
  __GITHUB_SOURCES__
)
github_missing <- intersect(missing, names(github_sources))
if (length(github_missing)) {
  if (!requireNamespace("remotes", quietly = TRUE)) utils::install.packages("remotes")
  for (package in github_missing) {
    try(remotes::install_github(
      github_sources[[package]],
      dependencies = TRUE,
      upgrade = "never",
      quiet = FALSE
    ), silent = TRUE)
  }
}
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) stop(paste(missing, collapse = ", "))
""".replace("__PACKAGE_REPOSITORIES__", repository_entries).replace(
        "__GITHUB_SOURCES__", github_entries
    ).replace("__BINARY_FALLBACK_DEFAULT__", binary_fallback_default)


_SIGNATURE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"dependency\s+'([^']+)' is not available", re.I),
        "依赖包 {0} 没有装上——它的系统组件或版本要求不满足；先单独解决 {0} 再重试",
    ),
    (
        re.compile(r"package\s+'([^']+)' is not available(?: \(for [^)]+\))?", re.I),
        "{0} 在已配置源上不存在，或与当前 R 版本不匹配（该源可能还没有为你的 R 版本提供此包）",
    ),
    (
        re.compile(r"configuration failed for package '([^']+)'", re.I),
        "{0} 的系统配置检查失败——通常是缺少系统库（libxml2、openssl、gdal、poppler 等），请按当前系统安装对应开发库",
    ),
    (
        re.compile(r"compilation failed for package '([^']+)'", re.I),
        "{0} 源码编译失败——请检查当前系统的编译器与 R 版本是否匹配，优先使用可用的二进制包",
    ),
    (
        re.compile(r"JAVA_HOME cannot be determined|Cannot find Java|cannot find Java", re.I),
        "Java 环境不可用——安装与机器架构匹配的 JDK，并检查 JAVA_HOME 与所用 R 包的 Java 配置",
    ),
    (
        re.compile(r"cannot open URL|download of |timed out|Timed out", re.I),
        "网络或镜像超时——可在系统设置里更换 CRAN 镜像后重试",
    ),
)


def diagnose_install_failure(
    requested: Sequence[str],
    log_text: str,
    r_version: str = "",
    platform_label: str = "",
) -> str:
    """Turn a raw install log into per-cause findings with concrete actions."""
    text = str(log_text or "")
    lines: list[str] = []
    env_line = "、".join(
        part for part in (f"R {r_version}" if r_version else "", platform_label) if part
    )
    if env_line:
        lines.append(f"运行环境：{env_line}。")
    findings: list[str] = []
    seen: set[str] = set()
    for pattern, template in _SIGNATURE_RULES:
        for match in pattern.finditer(text):
            detail = template.format(match.group(1)) if "{0}" in template else template
            if detail in seen:
                continue
            seen.add(detail)
            findings.append(f"- {detail}")
            if len(findings) >= 8:
                break
        if len(findings) >= 8:
            break
    if findings:
        lines.append("失败原因诊断：")
        lines.extend(findings)
        if sys.platform != "win32" and re.search(r"JAVA_HOME cannot be determined|Cannot find Java", text, re.I):
            lines.append("在当前 Unix 系统上安装 JDK 后，可运行 R CMD javareconf 重新配置 R 的 Java 支持。")
    else:
        lines.append("未能识别具体的失败签名；请按下方安装日志排查，或在系统设置中更换 CRAN 镜像后重试。")
    if sys.platform == "darwin":
        lines.append(
            "macOS 通用准备：终端执行 xcode-select --install 安装编译工具；"
            "编译类报错可由二进制回退源绕过（自动启用，可用 BAA_CRAN_BINARY_FALLBACK 覆盖）。"
        )
    elif sys.platform == "win32":
        lines.append(
            "Windows 编译准备：源码包需要与当前 R 版本和架构匹配的 Rtools；"
            "CRAN 二进制包通常不需要 Rtools。Java 类包请另行检查 JDK 与 JAVA_HOME。"
        )
    lines.append("以上失败原因不影响应用策略：任何包家族都未受限制；外部组件按系统正常方式安装即可。")
    return chr(10).join(lines)


class REnvironmentError(RuntimeError):
    """The local R installation is missing, incompatible, or unavailable."""


def platform_id() -> str:
    machine = platform.machine().strip().lower()
    if sys.platform == "darwin":
        return "macos-arm64" if machine in {"arm64", "aarch64"} else "macos-amd64"
    if sys.platform == "win32":
        return "windows-arm64" if machine in {"arm64", "aarch64"} else "windows-amd64"
    if sys.platform.startswith("linux"):
        return "linux-arm64" if machine in {"arm64", "aarch64"} else "linux-amd64"
    raise REnvironmentError(f"Unsupported R runtime platform: {sys.platform}/{machine or 'unknown'}")


def _configured_path(name: str) -> Path | None:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise REnvironmentError(f"{name} must be an absolute path")
    return path.resolve(strict=False)


def _windows_registry_r_roots() -> list[Path]:
    """Read installer registrations, including per-user/custom installations."""
    try:
        import winreg
    except ImportError:
        return []
    roots: list[Path] = []
    views = (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in dict.fromkeys(views):
            for key_name in (r"Software\R-core\R", r"Software\R-core\R64"):
                try:
                    with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                        versions: list[str] = []
                        try:
                            version = str(winreg.QueryValueEx(key, "Current Version")[0])
                            versions.append(version)
                        except OSError:
                            pass
                        index = 0
                        while True:
                            try:
                                versions.append(winreg.EnumKey(key, index))
                                index += 1
                            except OSError:
                                break
                        for version in sorted(set(versions), key=_version_tuple, reverse=True):
                            try:
                                with winreg.OpenKey(key, version) as installed:
                                    value = str(winreg.QueryValueEx(installed, "InstallPath")[0]).strip()
                                    if value:
                                        roots.append(Path(value))
                            except OSError:
                                continue
                        # Some installers also register InstallPath on the base key.
                        try:
                            value = str(winreg.QueryValueEx(key, "InstallPath")[0]).strip()
                            if value:
                                roots.append(Path(value))
                        except OSError:
                            pass
                except OSError:
                    continue
    return list(dict.fromkeys(roots))


def _rscript_candidates() -> list[Path]:
    candidates: list[Path] = []
    discovered = shutil.which("Rscript")
    if discovered:
        candidates.append(Path(discovered))
    if sys.platform == "darwin":
        candidates.extend(
            (
                Path("/usr/local/bin/Rscript"),
                Path("/opt/homebrew/bin/Rscript"),
                Path("/Library/Frameworks/R.framework/Resources/bin/Rscript"),
            )
        )
    elif sys.platform == "win32":
        roots = _windows_registry_r_roots()
        bases = [Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files") / "R"]
        for name in ("LOCALAPPDATA", "APPDATA"):
            if os.environ.get(name):
                bases.extend((Path(os.environ[name]) / "Programs" / "R", Path(os.environ[name]) / "R"))
        for base in dict.fromkeys(bases):
            roots.extend(sorted(base.glob("R-*"), key=lambda path: _version_tuple(path.name), reverse=True))
        for root in dict.fromkeys(roots):
            candidates.extend((root / "bin" / "Rscript.exe", root / "bin" / "x64" / "Rscript.exe", root / "bin" / "aarch64" / "Rscript.exe"))
    else:
        candidates.extend((Path("/usr/local/bin/Rscript"), Path("/usr/bin/Rscript")))
    return list(dict.fromkeys(path.expanduser().resolve(strict=False) for path in candidates))


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value)[:3])


def _sanitized_host_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    activated_python_roots = tuple(
        Path(str(source.get(name) or "")).expanduser().resolve(strict=False)
        for name in ("VIRTUAL_ENV", "CONDA_PREFIX")
        if str(source.get(name) or "").strip()
    )
    environment = {
        str(name): str(value)
        for name, value in source.items()
        if value is not None and not _SECRET_ENV_NAME.search(str(name))
    }
    for name in (
        "R_HOME",
        "R_ENVIRON",
        "R_PROFILE",
        "R_ENVIRON_USER",
        "R_PROFILE_USER",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_SHLVL",
        "VIRTUAL_ENV",
        "PYTHONHOME",
        "PYTHONPATH",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "LD_LIBRARY_PATH",
    ):
        environment.pop(name, None)
    raw_path = str(environment.get("PATH") or "")
    if raw_path and activated_python_roots:
        retained: list[str] = []
        for entry in raw_path.split(os.pathsep):
            if not entry:
                continue
            candidate = Path(entry).expanduser().resolve(strict=False)
            if any(_is_same_or_child(candidate, root) for root in activated_python_roots):
                continue
            retained.append(entry)
        environment["PATH"] = os.pathsep.join(retained)
    # Recent reticulate releases may resolve ``py_require()`` declarations by
    # downloading a managed Python environment on first import.  Analysis and
    # readiness probes must never mutate the machine implicitly; installation
    # is an explicit, user-confirmed action in this application.
    environment["RETICULATE_USE_MANAGED_VENV"] = "no"
    # GUI applications on macOS commonly inherit the launchd ``C`` locale
    # rather than the user's interactive-shell locale. R/readr then treats
    # UTF-8 paths and column labels as byte strings (for example ``<e6>``),
    # even though every generated script and manifest is UTF-8. Keep all R
    # subprocesses on one deterministic UTF-8 locale boundary.
    utf8_locale = "English_United States.UTF-8" if sys.platform == "win32" else _UTF8_LOCALE
    environment.update(
        {
            # Do not trust a locale merely because its name contains UTF-8.
            # macOS GUI sessions commonly expose the invalid shorthand
            # ``UTF-8`` and inherited custom locale names may not be installed;
            # R then warns and silently falls back to a byte-oriented C locale.
            "LANG": utf8_locale,
            "LC_ALL": utf8_locale,
            "LC_CTYPE": utf8_locale,
        }
    )
    return environment


@dataclass(frozen=True)
class VerifiedREnvironment:
    rscript: Path
    r_home: Path
    install_library: Path
    libraries: tuple[Path, ...]
    user_library: Path
    library_mode: str
    r_version: str
    package_count: int
    runtime_id: str


class HostREnvironment:
    """Deep module for local-R discovery, package isolation, and installation."""

    def __init__(
        self,
        rscript: str | Path | None = None,
        *,
        library_settings: RLibrarySettings | None = None,
    ):
        self.rscript = Path(rscript).expanduser().resolve(strict=False) if rscript else None
        self.library_settings = library_settings

    @classmethod
    def locate(cls) -> "HostREnvironment":
        configured = _configured_path("BAA_RSCRIPT_BIN")
        if configured is not None:
            return cls(configured)
        for candidate in _rscript_candidates():
            if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return cls(candidate)
        return cls()

    @property
    def runtime_label(self) -> str:
        return str(self.rscript) if self.rscript else "Rscript not found"

    @staticmethod
    def _versioned_library(base: Path, version: str) -> Path:
        version_parts = _version_tuple(version)
        suffix = ".".join(str(part) for part in version_parts[:2]) or "current"
        return base / suffix

    def _probe(
        self,
        environment: Mapping[str, str],
    ) -> tuple[str, Path, tuple[Path, ...], tuple[Path, ...], int]:
        if self.rscript is None or not self.rscript.is_file():
            raise REnvironmentError(
                "未找到本机 Rscript。请先安装 R，或在系统设置中指定 Rscript 的绝对路径。"
            )
        script = r'''
encode <- function(value) utils::URLencode(as.character(value), reserved = TRUE)
cat("VERSION\t", encode(getRversion()), "\n", sep = "")
cat("HOME\t", encode(normalizePath(R.home(), winslash = "/", mustWork = TRUE)), "\n", sep = "")
raw_user_libraries <- Sys.getenv("R_LIBS_USER", unset = "")
if (nzchar(raw_user_libraries)) {
  user_libraries <- strsplit(raw_user_libraries, .Platform$path.sep, fixed = TRUE)[[1]]
  user_libraries <- user_libraries[nzchar(user_libraries)]
  for (library in user_libraries) {
    cat("USERLIB\t", encode(normalizePath(path.expand(library), winslash = "/", mustWork = FALSE)), "\n", sep = "")
  }
}
for (library in normalizePath(.libPaths(), winslash = "/", mustWork = TRUE)) {
  cat("LIB\t", encode(library), "\n", sep = "")
}
cat("PACKAGES\t", nrow(utils::installed.packages()), "\n", sep = "")
'''
        try:
            with r_temporary_environment(environment) as process_environment:
                completed = subprocess.run(
                    [str(self.rscript), "--vanilla", "-e", script],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    env=process_environment,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise REnvironmentError(f"无法启动本机 R：{exc}") from exc
        if completed.returncode != 0:
            detail = str(completed.stderr or completed.stdout or "").strip()
            raise REnvironmentError(f"本机 R 自检失败：{detail[:1000]}")
        version = ""
        r_home = Path()
        libraries: list[Path] = []
        user_libraries: list[Path] = []
        package_count = 0
        for line in completed.stdout.splitlines():
            kind, separator, encoded = line.partition("\t")
            if not separator:
                continue
            value = unquote(encoded)
            if kind == "VERSION":
                version = value
            elif kind == "HOME":
                r_home = Path(value).resolve(strict=False)
            elif kind == "LIB":
                libraries.append(Path(value).resolve(strict=False))
            elif kind == "USERLIB":
                user_libraries.append(Path(value).resolve(strict=False))
            elif kind == "PACKAGES":
                package_count = int(value)
        if not version or not r_home.is_dir() or not libraries:
            raise REnvironmentError("本机 R 未返回完整的版本、R_HOME 和包目录信息。")
        return version, r_home, tuple(libraries), tuple(user_libraries), package_count

    @staticmethod
    def _process_environment(
        *,
        r_home: Path,
        install_library: Path,
        library_mode: str,
        inherited_libraries: Sequence[Path],
        base: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        environment = _sanitized_host_environment(base)
        user_libraries = [install_library]
        system_library = (r_home / "library").resolve(strict=False)
        for library in inherited_libraries:
            if library != system_library and library not in user_libraries:
                user_libraries.append(library)
        environment.update(
            {
                "R_LIBS_USER": os.pathsep.join(str(path) for path in user_libraries),
                "R_ENVIRON_USER": os.devnull,
                "R_PROFILE_USER": os.devnull,
                "BAA_R_LIBRARY_MODE": library_mode,
            }
        )
        if library_mode == MANAGED_LIBRARY_MODE:
            environment["BAA_R_LIBRARY_DIR"] = str(install_library.parent)
        else:
            environment.pop("BAA_R_LIBRARY_DIR", None)
        return environment

    def prepare(self) -> VerifiedREnvironment:
        version, r_home, libraries, standard_user_libraries, _ = self._probe(
            _sanitized_host_environment()
        )
        if _version_tuple(version)[:2] < _MINIMUM_R_VERSION:
            minimum = ".".join(str(part) for part in _MINIMUM_R_VERSION)
            raise REnvironmentError(f"本机 R 版本过低：{version}；需要 R {minimum} 或更高版本。")
        settings = self.library_settings or load_r_library_settings()
        if settings.mode == USER_LIBRARY_MODE:
            if not standard_user_libraries:
                raise REnvironmentError("本机 R 未提供可写的个人包目录 R_LIBS_USER。")
            install_library = standard_user_libraries[0]
        else:
            install_library = self._versioned_library(settings.managed_root, version)
        install_library = install_library.resolve(strict=False)

        standard_user_roots = {path.resolve(strict=False) for path in standard_user_libraries}
        protected_roots = {r_home.resolve(strict=False)}
        protected_roots.update(
            path.resolve(strict=False)
            for path in libraries
            if path.resolve(strict=False) not in standard_user_roots
        )
        protected_match = next(
            (root for root in protected_roots if _is_same_or_child(install_library, root)),
            None,
        )
        if protected_match is not None:
            raise REnvironmentError(
                f"不能把可写包目录设置到 R 系统/站点包目录内：{protected_match}。"
                "请选择 R 的个人包目录或其他项目专用目录。"
            )
        try:
            install_library.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=".baa-write-",
                dir=install_library,
            ) as handle:
                handle.write("write-check\n")
                handle.flush()
        except OSError as exc:
            raise REnvironmentError(f"R 包目录不可创建或不可写 {install_library}：{exc}") from exc

        inherited_libraries = list(libraries)
        legacy_managed = self._versioned_library(data_path("r-library"), version).resolve(strict=False)
        if settings.mode == USER_LIBRARY_MODE and legacy_managed.is_dir():
            inherited_libraries.insert(0, legacy_managed)
        environment = self._process_environment(
            r_home=r_home,
            install_library=install_library,
            library_mode=settings.mode,
            inherited_libraries=inherited_libraries,
        )
        environment["BAA_RSCRIPT_BIN"] = str(self.rscript)
        checked_version, checked_home, checked_libraries, _, package_count = self._probe(environment)
        if checked_version != version or checked_home != r_home:
            raise REnvironmentError("本机 R 在准备过程中发生变化，请重新启动应用。")
        if install_library not in checked_libraries:
            raise REnvironmentError(f"选定的 R 包目录没有进入 .libPaths()：{install_library}")
        runtime_id = hashlib.sha256(
            f"{self.rscript}|{version}|{r_home}|{settings.mode}|{install_library}".encode("utf-8")
        ).hexdigest()
        return VerifiedREnvironment(
            rscript=self.rscript,
            r_home=r_home,
            install_library=install_library,
            libraries=checked_libraries,
            user_library=standard_user_libraries[0] if standard_user_libraries else install_library,
            library_mode=settings.mode,
            r_version=version,
            package_count=package_count,
            runtime_id=runtime_id,
        )

    def process_environment(
        self,
        base: Mapping[str, str] | None = None,
        *,
        verified: VerifiedREnvironment | None = None,
    ) -> dict[str, str]:
        verified = verified or self.prepare()
        environment = self._process_environment(
            r_home=verified.r_home,
            install_library=verified.install_library,
            library_mode=verified.library_mode,
            inherited_libraries=verified.libraries,
            base=base,
        )
        environment["BAA_RSCRIPT_BIN"] = str(verified.rscript)
        environment["BAA_R_HOME"] = str(verified.r_home)
        return environment

    def ensure_packages(
        self,
        packages: Sequence[str],
        *,
        verified: VerifiedREnvironment | None = None,
        conversation_job_id: str = "",
        on_output=None,
    ) -> tuple[str, ...]:
        requested = tuple(dict.fromkeys(str(package).strip() for package in packages if package))
        invalid = [package for package in requested if not _PACKAGE_NAME.fullmatch(package)]
        if invalid:
            raise REnvironmentError(f"无效的 R 包名：{invalid[0]}")
        if not requested:
            return ()
        verified = verified or self.prepare()
        runtime_environment = self.process_environment(verified=verified)
        external_readiness = inspect_external_dependencies(
            packages=requested,
            environment=runtime_environment,
        )
        repository_entries = ",\n    ".join(
            f'"{package}" = "{repository}"'
            for package, repository in PACKAGE_REPOSITORIES.items()
        )
        github_entries = ",\n  ".join(
            f'"{package}" = "{repository}"'
            for package, repository in PACKAGE_GITHUB_SOURCES.items()
        )
        script = build_install_script(
            repository_entries,
            github_entries,
            default_binary_fallback_repo(),
        )
        raw_timeout = str(os.getenv("BAA_R_PACKAGE_INSTALL_TIMEOUT_SECONDS") or "0").strip()
        try:
            configured_timeout = int(raw_timeout)
        except ValueError:
            configured_timeout = 0
        timeout = max(60, configured_timeout) if configured_timeout > 0 else None
        from agent.analysis.run_control import (
            ResearchRunCancelled, research_run_controller, run_cancellable_process,
        )

        acquired = False
        try:
            while not acquired:
                if conversation_job_id and research_run_controller.is_cancelled(conversation_job_id):
                    raise ResearchRunCancelled("R package installation canceled")
                acquired = _PACKAGE_INSTALL_LOCK.acquire(timeout=0.2)
            try:
                with r_temporary_environment(runtime_environment) as process_environment:
                    completed = run_cancellable_process(
                        [str(verified.rscript), "--vanilla", "-e", script, *requested],
                        text=True,
                        timeout_seconds=timeout or 0,
                        conversation_job_id=conversation_job_id,
                        on_output=on_output,
                        env=process_environment,
                    )
            finally:
                _PACKAGE_INSTALL_LOCK.release()
        except subprocess.TimeoutExpired as exc:
            raise REnvironmentError(
                f"R 包安装超过配置时限：{', '.join(requested)}。可取消安装时限后重试。"
            ) from exc
        except RTemporaryDirectoryError as exc:
            raise REnvironmentError(str(exc)) from exc
        if completed.returncode != 0:
            detail = str(completed.stderr or completed.stdout or "").strip()
            requirements = package_setup_requirements(requested)
            states = list(external_readiness.get("states") or [])
            unresolved = [
                state
                for state in states
                if isinstance(state, dict) and state.get("status") != "ready"
            ]
            readiness_lines = [
                f"{state.get('label')}: {state.get('status')}；"
                f"{state.get('detail') or ''}；{state.get('setup_action') or ''}"
                for state in unresolved
            ]
            requirement_text = (
                "\n可能缺少的外部组件：" + " ".join(requirements)
                if requirements
                else ""
            )
            readiness_text = (
                "\n本机依赖检查：" + " | ".join(readiness_lines)
                if readiness_lines
                else ""
            )
            diagnosis = diagnose_install_failure(
                requested,
                detail,
                r_version=str(getattr(verified, "r_version", "") or ""),
                platform_label=f"{platform.system()} {platform.machine()}",
            )
            raise REnvironmentError(
                "R 包自动安装未完成："
                f"{', '.join(requested)}。"
                + chr(10)
                + diagnosis
                + requirement_text
                + readiness_text
                + chr(10)
                + f"安装日志（末尾）：{detail[-2000:]}"
            )
        return requested
