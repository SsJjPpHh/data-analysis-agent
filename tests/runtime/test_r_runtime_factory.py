from types import SimpleNamespace

from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.config import RRuntimeConfig
from r_runtime.factory import get_r_backend


def test_factory_returns_host_local_backend(monkeypatch, tmp_path):
    rscript = tmp_path / "bin" / "Rscript"
    monkeypatch.setattr(
        "r_runtime.factory.HostREnvironment.prepare",
        lambda self: SimpleNamespace(rscript=rscript),
    )
    monkeypatch.setattr(
        "r_runtime.factory.HostREnvironment.process_environment",
        lambda self, **_kwargs: {
            "R_HOME": str(tmp_path / "lib" / "R"),
            "R_LIBS_USER": str(tmp_path / "library"),
        },
    )

    backend = get_r_backend()

    assert isinstance(backend, LocalRscriptBackend)
    assert backend.rscript_bin == str(rscript)
    assert backend.runtime_env["R_LIBS_USER"] == str(tmp_path / "library")
    assert backend.package_installer is not None


def test_factory_uses_configured_host_executable(monkeypatch, tmp_path):
    rscript = tmp_path / "Rscript"
    cfg = RRuntimeConfig(
        backend="local",
        rscript_bin=str(rscript),
        package_library=str(tmp_path / "library"),
        output_size="10g",
        timeout_seconds=0,
    )
    monkeypatch.setattr(
        "r_runtime.factory.HostREnvironment.prepare",
        lambda self: SimpleNamespace(rscript=rscript),
    )
    monkeypatch.setattr(
        "r_runtime.factory.HostREnvironment.process_environment",
        lambda self, **_kwargs: {},
    )

    assert get_r_backend(cfg).rscript_bin == str(rscript)
