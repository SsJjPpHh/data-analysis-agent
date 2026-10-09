"""Read application resources directly from the open-source checkout."""

from contextlib import contextmanager
from pathlib import PurePosixPath

from infrastructure.paths import resource_path


def _resource(relative: str):
    path = PurePosixPath(str(relative).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("invalid application resource path")
    return resource_path(*path.parts)


def resource_bytes(relative: str) -> bytes:
    return _resource(relative).read_bytes()


def resource_text(relative: str) -> str:
    return _resource(relative).read_text(encoding="utf-8")


@contextmanager
def materialized_resource(relative: str, *, suffix: str = ""):
    """Provide an existing source file to an external tool."""
    yield _resource(relative)
