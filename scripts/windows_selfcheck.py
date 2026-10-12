"""Opt-in Windows acceptance; no model calls or automatic package installation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def record(name, status, category, detail):
    return dict(name=name, status=status, category=category, detail=str(detail))


def read_junit(path):
    rows = []
    for case in ET.parse(path).iter("testcase"):
        failure, error, skipped = (case.find(tag) for tag in ("failure", "error", "skipped"))
        node = failure if failure is not None else error if error is not None else skipped
        status = "FAIL" if failure is not None or error is not None else "SKIP" if skipped is not None else "PASS"
        category = "suspected_defect" if failure is not None else "unclassified" if error is not None else "test"
        detail = "" if node is None else node.get("message", "") + "\n" + "".join(node.itertext())
        rows.append(record(case.get("name", "unknown"), status, category, detail.strip()))
    return rows


def exit_code(rows):
    if any(row["status"] == "FAIL" for row in rows):
        return 1
    return 0 if rows and all(row["status"] == "PASS" for row in rows) else 2


def run_tests(label, target, directory, env, expected, selection=()):
    xml = directory / (label + ".xml")
    log = directory / (label + ".log")
    command = [sys.executable, "-m", "pytest", target, *selection, "-q", "-ra",
               "--tb=short", "--color=no", "--junitxml=" + str(xml)]
    with log.open("w", encoding="utf-8") as output:
        completed = subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    try:
        rows = read_junit(xml)
    except (OSError, ET.ParseError) as exc:
        rows = [record(label, "FAIL", "unclassified", f"No valid JUnit report: {exc}; see {log.name}")]
    if len(rows) != expected or completed.returncode not in (0, 1):
        rows.append(record(label + ".completion", "FAIL", "unclassified",
                           f"pytest exit={completed.returncode}; expected {expected} cases, got {len(rows)}; see {log.name}"))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New report directory outside the checkout")
    args = parser.parse_args(argv)
    directory = args.output.expanduser().resolve()
    if directory == ROOT or ROOT in directory.parents:
        parser.error("--output must be outside the checkout")
    if directory.exists():
        parser.error("--output must be a new directory (reports are never overwritten)")
    directory.mkdir(parents=True)
    rows = []
    facts = dict(python=sys.version, executable=sys.executable, platform=platform.platform(),
                 machine=platform.machine())

    def add(name, status, category, detail):
        rows.append(record(name, status, category, detail))

    def finish():
        code = exit_code(rows)
        payload = dict(created_at=datetime.now(timezone.utc).isoformat(), environment=facts,
                       acceptance="PASS" if code == 0 else "FAIL" if code == 1 else "INCOMPLETE",
                       checks=rows, exit_code=code)
        (directory / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"[{r['status']}] {r['name']} ({r['category']}): {r['detail']}" for r in rows]
        lines.append(f"Windows acceptance: {payload['acceptance']}; exit={code}")
        (directory / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for line in lines:
            print(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))
        return code

    if sys.platform != "win32":
        add("native_windows", "SKIP", "environment", "Requires a real Windows 10/11 host; no native tests executed.")
        return finish()
    add("python", "PASS" if sys.version_info >= (3, 10) else "FAIL", "environment", sys.executable)
    if sys.version_info < (3, 10):
        add("native_tests", "SKIP", "environment", "Python version prerequisite failed")
        return finish()

    # A fresh workspace prevents reading the user's saved application/model settings.
    with tempfile.TemporaryDirectory(prefix="daa-acceptance-") as work:
        env = dict(os.environ, BAA_DATA_DIR=str(Path(work) / "科研 工作区"),
                   PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", BAA_REQUIRE_SCIENCE_R="1",
                   PYTHONIOENCODING="utf-8")
        if env.get("BAA_TEST_R_LIBS"):
            env["R_LIBS_USER"] = env["BAA_TEST_R_LIBS"]
        os.environ.update({k: env[k] for k in ("BAA_DATA_DIR", "R_LIBS_USER") if k in env})
        sys.path.insert(0, str(ROOT))
        missing = []
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            if "==" not in line or line.lstrip().startswith("#"):
                continue
            name, required = line.strip().split("==", 1)
            try:
                actual = metadata.version(name)
                status = "PASS" if actual == required else "FAIL"
            except metadata.PackageNotFoundError:
                actual, status = "not installed", "FAIL"
            add("python_dependency." + name, status, "dependency", f"{actual}; required {required}")
            if status == "FAIL":
                missing.append(name)
        try:
            metadata.version("pytest")
        except metadata.PackageNotFoundError:
            missing.append("pytest")
            add("pytest", "FAIL", "dependency", "Install pytest==9.0.1 in this Python environment")
        if missing:
            add("native_tests", "SKIP", "dependency", "Resolve Python dependencies first: " + ", ".join(missing))
            return finish()
        try:
            from r_runtime.environment import HostREnvironment
            from r_runtime.tempfiles import private_r_temp_directory
            with private_r_temp_directory() as scratch:
                (scratch / "验证.txt").write_text("temporary write check", encoding="utf-8")
                facts["r_temp_path"] = str(scratch)
            add("private_temp", "PASS", "environment", "Application temporary directory created, writable and cleaned")
            host = HostREnvironment.locate()
            verified = host.prepare()
            facts.update(rscript=str(verified.rscript), r_version=verified.r_version,
                         r_home=str(verified.r_home), r_libraries=[str(p) for p in verified.libraries])
            add("r_discovery", "PASS", "environment", f"R {verified.r_version}: {verified.rscript}")
            # Existing native fixture uses PATH. Pin it to the application's discovered R.
            env = host.process_environment(base=env, verified=verified)
            env["PATH"] = str(verified.rscript.parent) + os.pathsep + env.get("PATH", "")
            env["BAA_TEST_R_LIBS"] = env["R_LIBS_USER"]
        except Exception as exc:
            category = "environment" if isinstance(exc, OSError) or type(exc).__name__ == "REnvironmentError" else "unclassified"
            add("r_preflight", "FAIL", category, f"{type(exc).__name__}: {exc}")
            add("native_tests", "SKIP", category, "R/temporary environment prerequisite failed")
            return finish()

        rows.extend(run_tests("portability", "tests/runtime/test_windows_portability.py", directory, env, 11))
        rows.extend(run_tests("native", "tests/runtime/test_windows_r_isolation.py", directory, env, 4,
                              ("-k", "test_native_")))
        # Probe dependencies without installing them; the existing smoke test uses these namespaces.
        from r_runtime.tempfiles import r_temporary_environment
        packages = 'p <- c("jsonlite", "ggplot2", "readr"); for (x in p) cat(x, requireNamespace(x, quietly=TRUE), sep="\\t", fill=TRUE)'
        try:
            with r_temporary_environment(env) as probe_env:
                probe = subprocess.run([str(verified.rscript), "--vanilla", "-e", packages],
                                       env=probe_env, capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=30)
            ready = probe.returncode == 0 and all(
                f"{name}\tTRUE" in probe.stdout for name in ("jsonlite", "ggplot2", "readr"))
            add("smoke_dependencies", "PASS" if ready else "FAIL", "dependency",
                probe.stdout + probe.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            ready = False
            add("smoke_dependencies", "FAIL", "environment", str(exc))
        if ready:
            rows.extend(run_tests("backend_smoke",
                                  "tests/runtime/test_research_r_smoke.py::test_local_rscript_research_smoke",
                                  directory, env, 1))
        else:
            add("backend_smoke", "SKIP", "dependency", "Install/load jsonlite, ggplot2 and readr in the selected R library")
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
