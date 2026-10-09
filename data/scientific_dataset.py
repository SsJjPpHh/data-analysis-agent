"""Scientific dataset asset metadata and local-path validation.

This module deliberately keeps host paths in storage-only records.  Public
descriptors contain the relative mount name and immutable import metadata, but
never the source path that the desktop user selected.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


REFERENCE_ACCESS = "reference"
COPY_ACCESS = "copy"
SCIENTIFIC_DATA_ACCESS_MODES = frozenset({REFERENCE_ACCESS, COPY_ACCESS})
SCIENTIFIC_DATA_ASSET_KINDS = frozenset({"file", "directory"})

# Longest suffix wins, so compressed scientific formats can coexist with their
# uncompressed forms.  The registry is intentionally mutable through
# ``register_scientific_format``; adapters can add formats without changing the
# manifest model.
SCIENTIFIC_FORMAT_SUFFIXES: dict[str, str] = {
    ".csv.gz": "csv",
    ".csv": "csv",
    ".tsv.gz": "tsv",
    ".tsv": "tsv",
    ".txt.gz": "txt",
    ".txt": "txt",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".sav": "sav",
    ".dta": "dta",
    ".rds": "rds",
    ".parquet": "parquet",
    ".rdata": "rdata",
    ".rda": "rdata",
    ".mtx": "mtx",
    ".mtx.gz": "mtx",
    # HDF5 is a container, not an analysis format. Existing .h5/.hdf5 files
    # are refined by a bounded content probe below.
    ".h5": "generic_hdf5",
    ".hdf5": "generic_hdf5",
    ".h5ad": "h5ad",
    ".loom": "loom",
    ".h5seurat": "h5seurat",
    ".fcs": "fcs",
    ".vcf": "vcf",
    ".vcf.gz": "vcf",
    ".vcf.bgz": "vcf",
    ".bcf": "bcf",
    ".bed": "bed",
    ".bed.gz": "bed",
    ".narrowpeak": "narrowpeak",
    ".narrowpeak.gz": "narrowpeak",
    ".bam": "bam",
    ".bam.bai": "bam_index",
    ".bai": "bam_index",
    ".sam": "sam",
    ".cram": "cram",
    ".cram.crai": "cram_index",
    ".crai": "cram_index",
    ".tbi": "vcf_index",
    ".csi": "vcf_index",
    ".bim": "plink_bim",
    ".fam": "plink_fam",
    ".gff": "gff",
    ".gff.gz": "gff",
    ".gff3": "gff",
    ".gff3.gz": "gff",
    ".gtf": "gtf",
    ".gtf.gz": "gtf",
    ".biom": "biom",
    ".mzml": "mzml",
    ".mzml.gz": "mzml",
    ".mzxml": "mzxml",
    ".mzxml.gz": "mzxml",
    ".fastq.gz": "fastq",
    ".fastq": "fastq",
    ".fq.gz": "fastq",
    ".fq": "fastq",
    ".fasta.gz": "fasta",
    ".fasta": "fasta",
    ".fna.gz": "fasta",
    ".fna": "fasta",
    ".fa.gz": "fasta",
    ".fa": "fasta",
    ".bigwig": "bigwig",
    ".bw": "bigwig",
    ".maf.gz": "maf",
    ".maf": "maf",
    ".newick": "newick",
    ".nwk": "newick",
    ".tree": "newick",
    ".qza": "qza",
    # Auxiliary Space Ranger assets are registered so a compound import can
    # retain their scientific role. They are never advertised as standalone
    # executable analysis formats.
    ".json": "json",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
}

_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
_HDF5_CONTENT_FORMATS = frozenset(
    {"10x_h5", "h5ad", "loom", "h5seurat", "generic_hdf5"}
)
_LEGACY_FORMAT_ALIASES = {"h5": "generic_hdf5", "hdf5": "generic_hdf5"}
_DIRECTORY_SCAN_MAX_ENTRIES_ENV = "BAA_SCIENTIFIC_DIRECTORY_SCAN_MAX_ENTRIES"
_DIRECTORY_SCAN_MAX_DEPTH_ENV = "BAA_SCIENTIFIC_DIRECTORY_SCAN_MAX_DEPTH"
_PROBE_MAX_BYTES_ENV = "BAA_SCIENTIFIC_PROBE_MAX_BYTES"
# Native desktop imports do not impose a small fixed file-count/depth ceiling.
# Entry protection is resource-derived by default. Operators may set a positive
# override for constrained workstations; an explicit zero disables the ceiling.
_DEFAULT_DIRECTORY_SCAN_MAX_ENTRIES = 0
_DEFAULT_DIRECTORY_SCAN_MAX_DEPTH = 0
_DEFAULT_PROBE_MAX_BYTES = 4 * 1024 * 1024
_IDENTITY_SAMPLE_BYTES = 64 * 1024
_IDENTITY_SAMPLE_BLOCKS = 4
_DIRECTORY_ENTRY_MEMORY_ESTIMATE = 1024

# These formats can be registered and truthfully described, but are not
# loader-backed analysis inputs yet. Format contracts must not expose them as
# executable merely because their suffix is recognized.
UPSTREAM_REQUIRED_FORMATS = frozenset(
    {
        "txt",
        "generic_hdf5",
        "loom",
        "h5seurat",
        "fastq",
        "fasta",
        "sam",
        "cram",
        "maf",
        "qza",
        "plink_bim",
        "plink_fam",
        "bam_index",
        "vcf_index",
        "cram_index",
        "json",
        "image",
    }
)


def _is_link_or_reparse(path_stat: os.stat_result) -> bool:
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    attributes = int(getattr(path_stat, "st_file_attributes", 0))
    return stat.S_ISLNK(path_stat.st_mode) or bool(reparse_flag and attributes & reparse_flag)


def register_scientific_format(format_name: str, *suffixes: str) -> None:
    """Register one format name and one or more filename suffixes."""
    normalized_format = str(format_name).strip().lower().lstrip(".")
    if not _FORMAT_RE.fullmatch(normalized_format):
        raise ValueError("Invalid scientific dataset format name")
    if not suffixes:
        raise ValueError("At least one scientific dataset suffix is required")
    for raw_suffix in suffixes:
        suffix = str(raw_suffix).strip().lower()
        if not suffix.startswith(".") or suffix in {".", ".."} or "/" in suffix or "\\" in suffix:
            raise ValueError("Invalid scientific dataset suffix")
        SCIENTIFIC_FORMAT_SUFFIXES[suffix] = normalized_format


def scientific_format_support(format_name: str) -> str:
    """Return the truthful execution status for one canonical format."""
    normalized = _LEGACY_FORMAT_ALIASES.get(
        str(format_name or "").strip().lower().lstrip("."),
        str(format_name or "").strip().lower().lstrip("."),
    )
    return "upstream_required" if normalized in UPSTREAM_REQUIRED_FORMATS else "loader_backed"


def _configured_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _probe_max_bytes() -> int:
    return _configured_int(
        _PROBE_MAX_BYTES_ENV,
        _DEFAULT_PROBE_MAX_BYTES,
        minimum=64 * 1024,
        maximum=64 * 1024 * 1024,
    )


def _read_probe(path: Path, maximum: int | None = None) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(maximum or _probe_max_bytes())
    except OSError as exc:
        raise ValueError(f"Scientific dataset content cannot be inspected: {path.name}") from exc


def _contains_hdf5_signature(data: bytes) -> bool:
    # HDF5 permits a user block before the superblock at offsets 0 or powers of
    # two starting at 512 bytes. Check only offsets within the bounded probe.
    offset = 0
    while offset + len(_HDF5_SIGNATURE) <= len(data):
        if data[offset : offset + len(_HDF5_SIGNATURE)] == _HDF5_SIGNATURE:
            return True
        offset = 512 if offset == 0 else offset * 2
    return False


def _probe_hdf5_format(path: Path) -> str:
    data = _read_probe(path)
    if not _contains_hdf5_signature(data):
        raise ValueError(f"Scientific dataset has an HDF5 suffix but invalid HDF5 content: {path.name}")
    lowered = data.lower()

    def has_all(*markers: bytes) -> bool:
        return all(marker.lower() in lowered for marker in markers)

    if has_all(b"encoding-type", b"anndata", b"obs", b"var"):
        return "h5ad"
    if has_all(b"loom_spec_version", b"row_attrs", b"col_attrs", b"layers"):
        return "loom"
    if has_all(b"h5seurat", b"assays", b"meta.data"):
        return "h5seurat"
    if has_all(b"matrix", b"barcodes", b"features", b"data", b"indices", b"indptr", b"shape"):
        return "10x_h5"
    return "generic_hdf5"


def _probe_bed_format(path: Path) -> str:
    data = _read_probe(path, 64 * 1024)
    if data[:2] == b"\x6c\x1b" and len(data) >= 3 and data[2] in {0, 1}:
        return "plink_bed"
    if b"\x00" in data:
        raise ValueError(
            f"Binary BED content is unsupported unless it is a PLINK BED file: {path.name}"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"BED content is neither genomic text BED nor PLINK BED: {path.name}"
        ) from exc
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "track ", "browser ")):
            continue
        fields = line.split("\t") if "\t" in line else line.split()
        if len(fields) < 3:
            raise ValueError(f"Genomic BED content requires at least three columns: {path.name}")
        try:
            int(fields[1])
            int(fields[2])
        except ValueError as exc:
            raise ValueError(f"Genomic BED start/end columns must be integers: {path.name}") from exc
        break
    return "bed"


def _format_from_name(name: str) -> str | None:
    lowered = str(name).strip().lower()
    for suffix in sorted(SCIENTIFIC_FORMAT_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix):
            return SCIENTIFIC_FORMAT_SUFFIXES[suffix]
    return None


def _supported_format_names() -> set[str]:
    return set(SCIENTIFIC_FORMAT_SUFFIXES.values()) | set(_HDF5_CONTENT_FORMATS) | {"plink_bed"}


def _refine_existing_file_format(path: Path, suffix_format: str) -> str:
    if suffix_format in _HDF5_CONTENT_FORMATS:
        content_format = _probe_hdf5_format(path)
        if suffix_format != "generic_hdf5" and content_format != suffix_format:
            raise ValueError(
                f"HDF5 content does not match declared {suffix_format} format: {path.name}"
            )
        return content_format
    if suffix_format == "bed":
        if path.name.casefold().endswith(".bed.gz"):
            return "bed"
        return _probe_bed_format(path)
    return suffix_format


def detect_scientific_format(
    source: str | Path,
    *,
    filename: str | None = None,
    declared_format: str | None = None,
) -> str:
    """Detect one canonical scientific format using suffix and bounded content."""
    path = Path(source)
    if declared_format is not None:
        raw_declared = str(declared_format).strip().lower().lstrip(".")
        normalized = _LEGACY_FORMAT_ALIASES.get(raw_declared, raw_declared)
        if normalized not in _supported_format_names():
            raise ValueError(f"Unsupported research dataset format: {declared_format}")
        if path.is_file() and not path.is_symlink() and normalized in (_HDF5_CONTENT_FORMATS | {"bed", "plink_bed"}):
            suffix_format = "bed" if normalized in {"bed", "plink_bed"} else normalized
            detected = _refine_existing_file_format(path, suffix_format)
            if detected != normalized:
                raise ValueError(
                    f"Scientific dataset content is {detected}, not declared {normalized}: {path.name}"
                )
        return normalized

    suffix_format: str | None = None
    for candidate in (filename, Path(source).name):
        if candidate:
            detected = _format_from_name(str(candidate))
            if detected is not None:
                suffix_format = detected
                break

    if path.is_file() and not path.is_symlink():
        if suffix_format is None:
            label = filename or path.name or str(source)
            raise ValueError(f"Unsupported research dataset format: {label}")
        return _refine_existing_file_format(path, suffix_format)
    if suffix_format is not None and not path.exists():
        # Filename-only routing is intentionally conservative for .h5: without
        # content evidence it remains generic and therefore non-executable.
        return suffix_format
    if path.is_dir() and not path.is_symlink():
        specs = infer_scientific_asset_specs([path])
        primary = next((item for item in specs if item["role"] == "primary"), None)
        if primary is not None:
            return str(primary["format"])

    label = filename or Path(source).name or str(source)
    raise ValueError(f"Unsupported research dataset format: {label}")


@dataclass(frozen=True)
class _DirectoryScan:
    files: tuple[Path, ...]
    size_bytes: int
    entries_scanned: int
    fingerprint_sha256: str


@dataclass(frozen=True)
class _DiscoveredAsset:
    path: Path
    format: str
    mount_name: str
    name: str


def _resource_directory_scan_max_entries(_root: Path) -> int:
    """Derive a fail-closed scan ceiling from local memory-like resources."""
    resource_bytes = 0
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                resource_bytes = max(0, int(status.available_physical))
        except (AttributeError, OSError, TypeError, ValueError):
            resource_bytes = 0
    try:
        if resource_bytes <= 0:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            try:
                available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
            except (OSError, ValueError):
                # macOS exposes physical pages but not available pages. Using
                # half of physical RAM, then one eighth below, reserves fifteen
                # sixteenths of total RAM for the rest of the process/system.
                available_pages = max(0, int(os.sysconf("SC_PHYS_PAGES")) // 2)
            resource_bytes = max(0, page_size * available_pages)
    except (AttributeError, OSError, TypeError, ValueError):
        resource_bytes = 0
    scan_memory_budget = resource_bytes // 8
    if scan_memory_budget <= 0:
        raise ValueError(
            "Scientific dataset directory resources cannot be measured safely; set "
            f"{_DIRECTORY_SCAN_MAX_ENTRIES_ENV} explicitly for this workstation."
        )
    return max(1, scan_memory_budget // _DIRECTORY_ENTRY_MEMORY_ESTIMATE)


def _stat_ns(metadata: os.stat_result, field: str) -> int:
    value = getattr(metadata, f"st_{field}_ns", None)
    if value is not None:
        return int(value)
    return int(getattr(metadata, f"st_{field}") * 1_000_000_000)


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(getattr(metadata, "st_dev", 0)),
        int(getattr(metadata, "st_ino", 0)),
        int(stat.S_IFMT(metadata.st_mode)),
        int(metadata.st_size),
        _stat_ns(metadata, "mtime"),
        _stat_ns(metadata, "ctime"),
    )


def _file_fingerprint(path: Path, expected_stat: os.stat_result) -> str:
    """Hash bounded, deterministic file samples while rejecting scan races."""
    flags = os.O_RDONLY | int(getattr(os, "O_CLOEXEC", 0)) | int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"Scientific dataset path cannot be fingerprinted: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _stat_identity(before) != _stat_identity(expected_stat):
            raise ValueError(f"Scientific dataset file changed during inspection: {path.name}")
        size = int(before.st_size)
        digest = hashlib.sha256()
        digest.update(b"baa-scientific-file-v1\0")
        digest.update(size.to_bytes(16, "big", signed=False))
        full_hash_limit = _IDENTITY_SAMPLE_BYTES * _IDENTITY_SAMPLE_BLOCKS
        if size <= full_hash_limit:
            offsets = (0,)
            lengths = (size,)
        else:
            maximum_offset = size - _IDENTITY_SAMPLE_BYTES
            offsets = tuple(
                (maximum_offset * index) // (_IDENTITY_SAMPLE_BLOCKS - 1)
                for index in range(_IDENTITY_SAMPLE_BLOCKS)
            )
            lengths = (_IDENTITY_SAMPLE_BYTES,) * len(offsets)
        for offset, length in zip(offsets, lengths, strict=True):
            digest.update(int(offset).to_bytes(16, "big", signed=False))
            digest.update(int(length).to_bytes(8, "big", signed=False))
            remaining = int(length)
            os.lseek(descriptor, int(offset), os.SEEK_SET)
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    raise ValueError(f"Scientific dataset file changed during inspection: {path.name}")
                digest.update(chunk)
                remaining -= len(chunk)
        after = os.fstat(descriptor)
        if _stat_identity(after) != _stat_identity(before):
            raise ValueError(f"Scientific dataset file changed during inspection: {path.name}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _manifest_record(
    relative_name: str,
    metadata: os.stat_result,
    content_fingerprint: str,
) -> bytes:
    encoded_name = relative_name.encode("utf-8", errors="surrogateescape")
    identity = _stat_identity(metadata)
    fields = b"\0".join(str(value).encode("ascii") for value in identity)
    return (
        len(encoded_name).to_bytes(8, "big", signed=False)
        + encoded_name
        + b"\0"
        + fields
        + b"\0"
        + content_fingerprint.encode("ascii")
    )


def _bounded_directory_scan(
    root: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
    compute_fingerprint: bool = True,
) -> _DirectoryScan:
    """Inspect a local bundle with explicit operator and resource guardrails."""
    configured_limit = str(os.getenv(_DIRECTORY_SCAN_MAX_ENTRIES_ENV, "") or "").strip()
    if configured_limit:
        max_entries = _configured_int(
            _DIRECTORY_SCAN_MAX_ENTRIES_ENV,
            _DEFAULT_DIRECTORY_SCAN_MAX_ENTRIES,
            minimum=0,
            maximum=100_000_000,
        )
        limit_kind = "configured inspection limit"
    else:
        max_entries = _resource_directory_scan_max_entries(root)
        limit_kind = "resource-derived safety limit"
    max_depth = _configured_int(
        _DIRECTORY_SCAN_MAX_DEPTH_ENV,
        _DEFAULT_DIRECTORY_SCAN_MAX_DEPTH,
        minimum=0,
        maximum=1024,
    )
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ValueError("Scientific dataset directory cannot be inspected") from exc
    pending: deque[tuple[Path, int]] = deque([(root, 0)])
    files: list[Path] = []
    snapshots: list[tuple[Path, tuple[int, int, int, int, int, int]]] = [
        (root, _stat_identity(root_stat))
    ]
    total_bytes = 0
    scanned = 0
    manifest_sum = 0
    manifest_xor = bytearray(hashlib.sha256().digest_size)

    def check_cancelled() -> None:
        if cancel_check is not None and cancel_check():
            raise ValueError("Scientific dataset directory inspection was cancelled")

    def add_manifest_record(record: bytes) -> None:
        nonlocal manifest_sum
        entry_digest = hashlib.sha256(record).digest()
        manifest_sum = (manifest_sum + int.from_bytes(entry_digest, "big")) % (1 << 256)
        for index, value in enumerate(entry_digest):
            manifest_xor[index] ^= value

    while pending:
        check_cancelled()
        directory, depth = pending.popleft()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    check_cancelled()
                    scanned += 1
                    if max_entries and scanned > max_entries:
                        raise ValueError(
                            f"Scientific dataset directory exceeds the {limit_kind} "
                            f"of {max_entries} entries; select a more precise subdirectory or raise "
                            f"{_DIRECTORY_SCAN_MAX_ENTRIES_ENV}."
                        )
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise ValueError(
                            f"Scientific dataset path cannot be inspected: {entry.name}"
                        ) from exc
                    if entry.is_symlink() or _is_link_or_reparse(entry_stat):
                        raise ValueError(
                            f"Scientific dataset directories cannot contain symlinks: {entry.name}"
                        )
                    mode = entry_stat.st_mode
                    path = Path(entry.path)
                    snapshots.append((path, _stat_identity(entry_stat)))
                    content_fingerprint = ""
                    if stat.S_ISDIR(mode):
                        if max_depth and depth >= max_depth:
                            raise ValueError(
                                "Scientific dataset directory exceeds the configured inspection depth "
                                f"of {max_depth}; select a more precise subdirectory or raise "
                                f"{_DIRECTORY_SCAN_MAX_DEPTH_ENV}."
                            )
                        pending.append((path, depth + 1))
                    elif stat.S_ISREG(mode):
                        files.append(path)
                        total_bytes += int(entry_stat.st_size)
                        if compute_fingerprint:
                            content_fingerprint = _file_fingerprint(path, entry_stat)
                    else:
                        raise ValueError(
                            f"Scientific dataset directories cannot contain special files: {entry.name}"
                        )
                    relative_name = path.relative_to(root).as_posix()
                    add_manifest_record(
                        _manifest_record(relative_name, entry_stat, content_fingerprint)
                    )
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError("Scientific dataset directory cannot be inspected") from exc

    # Recheck every observed object. This makes the fingerprint a coherent
    # snapshot instead of accepting a tree that changed behind the scanner.
    for path, expected_identity in snapshots:
        check_cancelled()
        try:
            current_stat = path.lstat()
        except OSError as exc:
            raise ValueError("Scientific dataset directory changed during inspection") from exc
        if _is_link_or_reparse(current_stat) or _stat_identity(current_stat) != expected_identity:
            raise ValueError("Scientific dataset directory changed during inspection")

    digest = hashlib.sha256()
    digest.update(b"baa-scientific-directory-v1\0")
    digest.update(scanned.to_bytes(16, "big", signed=False))
    digest.update(total_bytes.to_bytes(16, "big", signed=False))
    digest.update(manifest_sum.to_bytes(32, "big"))
    digest.update(bytes(manifest_xor))
    return _DirectoryScan(tuple(files), total_bytes, scanned, digest.hexdigest())


def _safe_mount_component(value: str, fallback: str) -> str:
    cleaned = str(value or "").replace("\\", "_").replace("/", "_").strip()
    if cleaned in {"", ".", ".."} or ":" in cleaned:
        return fallback
    return cleaned


def _discover_selected_assets(paths: Iterable[str | Path]) -> list[_DiscoveredAsset]:
    selected = [Path(value).expanduser() for value in paths]
    if not selected:
        raise ValueError("A scientific dataset bundle requires at least one selected path")
    discovered: list[_DiscoveredAsset] = []
    mount_names: set[str] = set()
    for root_index, source in enumerate(selected, start=1):
        if source.is_symlink():
            raise ValueError("Scientific dataset source cannot be a symlink")
        try:
            root = source.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"Scientific dataset source does not exist: {source}") from exc
        if root.is_file():
            files = (root,)
            mount_root = ""
        elif root.is_dir():
            files = _bounded_directory_scan(root, compute_fingerprint=False).files
            mount_root = _safe_mount_component(root.name, f"asset-{root_index}")
        else:
            raise ValueError(f"Scientific dataset source must be a file or directory: {source}")

        for file_path in files:
            suffix_format = _format_from_name(file_path.name)
            if suffix_format is None:
                continue
            # Images/JSON are retained only when they are recognized Space
            # Ranger assets. Arbitrary documents in a selected tree are not
            # silently imported as analysis inputs.
            lower_name = file_path.name.casefold()
            if suffix_format == "image" and not any(
                token in lower_name for token in ("tissue", "spatial", "hires", "lowres")
            ):
                continue
            if suffix_format == "json" and not any(
                token in lower_name for token in ("scalefactor", "spatial")
            ):
                continue
            data_format = detect_scientific_format(file_path)
            if mount_root:
                relative = file_path.relative_to(root).as_posix()
                mount_name = PurePosixPath(mount_root, relative).as_posix()
            else:
                mount_name = file_path.name
            mount_name = validate_relative_mount_name(mount_name)
            if mount_name in mount_names:
                raise ValueError(
                    "Scientific dataset selection contains duplicate mount names; "
                    "select their common directory instead."
                )
            mount_names.add(mount_name)
            discovered.append(
                _DiscoveredAsset(
                    path=file_path,
                    format=data_format,
                    mount_name=mount_name,
                    name=file_path.name,
                )
            )
    if not discovered:
        raise ValueError("Unsupported research dataset directory: no recognized scientific assets found")
    return sorted(discovered, key=lambda item: (item.mount_name.casefold(), item.mount_name))


def _normalized_basename(name: str) -> str:
    lowered = str(name).casefold()
    for suffix in (
        ".gz",
        ".bgz",
        ".bai",
        ".crai",
        ".tbi",
        ".csi",
        ".bed",
        ".bim",
        ".fam",
        ".bam",
        ".cram",
        ".vcf",
        ".bcf",
    ):
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
    return lowered


def _name_role(asset: _DiscoveredAsset) -> str | None:
    name = asset.name.casefold()
    stem = name.removesuffix(".gz")
    if re.fullmatch(r"barcodes?\.tsv", stem):
        return "barcodes"
    if re.fullmatch(r"(features|genes)\.tsv", stem):
        return "features"
    if "tissue_positions" in name or "spatial_coordinates" in name:
        return "spatial_coordinates"
    if "scalefactors" in name and asset.format == "json":
        return "spatial_metadata"
    if asset.format == "image" and any(
        token in name for token in ("tissue", "spatial", "hires", "lowres")
    ):
        return "spatial_image"
    if asset.format == "plink_bim":
        return "feature_metadata"
    if asset.format == "plink_fam":
        return "sample_metadata"
    if asset.format == "bam_index":
        return "alignment_index"
    if asset.format == "vcf_index":
        return "variant_index"
    if asset.format == "cram_index":
        return "alignment_index"
    if asset.format in {"csv", "tsv", "txt"}:
        if "metrics_summary" in name or "quality_metrics" in name:
            return "quality_metrics"
        if "feature_reference" in name:
            return "feature_metadata"
        if any(token in name for token in ("phenotype", "clinical", "sample", "metadata")):
            return "sample_metadata"
        if "covariate" in name:
            return "covariates"
    return None


def _auxiliary_role_for_primary(
    asset: _DiscoveredAsset,
    primary: _DiscoveredAsset,
) -> str | None:
    named = _name_role(asset)
    if named is not None:
        return named
    if asset.format in {"gff", "gtf"} and primary.format not in {"gff", "gtf"}:
        return "feature_annotation"
    if asset.format == "fasta" and primary.format in {
        "fastq",
        "sam",
        "bam",
        "cram",
        "vcf",
        "bcf",
        "plink_bed",
    }:
        return "reference_sequence"
    if asset.format == "bam" and primary.format in {"mtx", "10x_h5", "h5ad"}:
        return "alignments"
    if (
        asset.format == "generic_hdf5"
        and primary.format in {"mtx", "10x_h5", "h5ad"}
        and "molecule_info" in asset.name.casefold()
    ):
        return "molecule_info"
    return None


def _index_matches(primary: _DiscoveredAsset, index: _DiscoveredAsset) -> bool:
    primary_name = primary.name.casefold()
    index_name = index.name.casefold()
    if index.format == "bam_index":
        return index_name in {f"{primary_name}.bai", f"{_normalized_basename(primary_name)}.bai"}
    if index.format == "cram_index":
        return index_name in {f"{primary_name}.crai", f"{_normalized_basename(primary_name)}.crai"}
    if index.format == "vcf_index":
        return index_name in {
            f"{primary_name}.tbi",
            f"{primary_name}.csi",
            f"{_normalized_basename(primary_name)}.tbi",
            f"{_normalized_basename(primary_name)}.csi",
        }
    return True


def _select_tenx_primary(candidates: list[_DiscoveredAsset]) -> _DiscoveredAsset | None:
    tenx = [item for item in candidates if item.format in {"mtx", "10x_h5"}]
    if not tenx:
        return None
    if len(tenx) == 1:
        return tenx[0]
    filtered = [item for item in tenx if "filtered_feature_bc_matrix" in item.mount_name.casefold()]
    pool = filtered or tenx
    if len(pool) == 2 and {item.format for item in pool} == {"mtx", "10x_h5"}:
        return next(item for item in pool if item.format == "10x_h5")
    if len(pool) == 1:
        return pool[0]
    return None


def _assign_scientific_roles(discovered: list[_DiscoveredAsset]) -> list[tuple[_DiscoveredAsset, str]]:
    named_roles = {item: _name_role(item) for item in discovered}
    if len(discovered) == 1 and named_roles[discovered[0]] in {"sample_metadata", "covariates"}:
        # A role-like basename is meaningful only relative to another asset.
        # A lone supported table is itself the user's primary dataset.
        return [(discovered[0], "primary")]
    primary_candidates = [item for item in discovered if named_roles[item] is None]
    if not primary_candidates:
        raise ValueError("Scientific dataset bundle has sidecars/indexes but no primary data asset")

    tenx_primary = _select_tenx_primary(primary_candidates)
    if tenx_primary is not None:
        primary = tenx_primary
        duplicate_tenx = {
            item for item in primary_candidates if item.format in {"mtx", "10x_h5"} and item != primary
        }
    else:
        primary = None
        duplicate_tenx: set[_DiscoveredAsset] = set()

    plink_candidates = [item for item in primary_candidates if item.format == "plink_bed"]
    if plink_candidates:
        if len(plink_candidates) != 1 or (primary is not None and primary not in plink_candidates):
            raise ValueError("Scientific dataset selection is ambiguous: multiple primary matrices were found")
        primary = plink_candidates[0]
        stem = _normalized_basename(primary.name)
        bim = [item for item in discovered if item.format == "plink_bim" and _normalized_basename(item.name) == stem]
        fam = [item for item in discovered if item.format == "plink_fam" and _normalized_basename(item.name) == stem]
        if len(bim) != 1 or len(fam) != 1:
            raise ValueError("PLINK BED import requires exactly one matching BIM and FAM file")

    collection_groups = (
        {"fcs"},
        {"mzml", "mzxml"},
        {"fastq"},
        {"fasta"},
    )
    if primary is None:
        for group in collection_groups:
            if len(primary_candidates) > 1 and {item.format for item in primary_candidates} <= group:
                primary = primary_candidates[0]
                break

    if primary is None and len(primary_candidates) == 1:
        primary = primary_candidates[0]

    if primary is None:
        feasible: list[_DiscoveredAsset] = []
        for candidate in primary_candidates:
            others = [item for item in primary_candidates if item != candidate]
            if all(_auxiliary_role_for_primary(item, candidate) is not None for item in others):
                feasible.append(candidate)
        if len(feasible) == 1:
            primary = feasible[0]
        else:
            labels = ", ".join(item.mount_name for item in primary_candidates[:8])
            raise ValueError(
                "Scientific dataset selection is ambiguous; choose one primary dataset or a recognized "
                f"compound bundle. Candidates: {labels}"
            )

    assigned: list[tuple[_DiscoveredAsset, str]] = [(primary, "primary")]
    sample_number = 2
    spatial_image_number = 1
    for item in discovered:
        if item == primary or item in duplicate_tenx:
            continue
        role = named_roles[item] or _auxiliary_role_for_primary(item, primary)
        if role in {"barcodes", "features"}:
            if primary.format == "10x_h5":
                continue
            if primary.format == "mtx" and item.path.parent != primary.path.parent:
                continue
        if (
            role is None
            and primary.format in {"mtx", "10x_h5", "h5ad"}
            and item.format in {"csv", "tsv"}
            and "analysis" in {part.casefold() for part in item.path.parts}
        ):
            # Cell/Space Ranger derived clustering and dimensional-reduction
            # exports are outputs, not ambiguous alternate primary datasets.
            continue
        if role in {"alignment_index", "variant_index"} and not _index_matches(primary, item):
            # A BAM nested under a 10x bundle may own the index instead of the
            # expression matrix primary.
            alignment = next(
                (candidate for candidate in discovered if candidate.format == "bam" and _index_matches(candidate, item)),
                None,
            )
            if alignment is None:
                raise ValueError(f"Scientific dataset index does not match its primary asset: {item.name}")
        if role is None and (
            {primary.format, item.format} <= {"mzml", "mzxml"}
            or item.format == primary.format
            and primary.format in {"fcs", "fastq", "fasta"}
        ):
            role = f"sample_{sample_number:03d}"
            sample_number += 1
        if role is None:
            raise ValueError(
                "Scientific dataset selection is ambiguous; cannot infer the role of "
                f"{item.mount_name} relative to {primary.mount_name}."
            )
        if role == "spatial_image":
            role = "spatial_image" if spatial_image_number == 1 else f"spatial_image_{spatial_image_number:03d}"
            spatial_image_number += 1
        assigned.append((item, role))

    role_order = {
        "primary": 0,
        "barcodes": 10,
        "features": 20,
        "feature_metadata": 30,
        "sample_metadata": 40,
        "covariates": 45,
        "alignment_index": 50,
        "variant_index": 50,
        "feature_annotation": 60,
        "reference_sequence": 70,
        "alignments": 80,
        "molecule_info": 85,
        "quality_metrics": 86,
        "spatial_coordinates": 90,
        "spatial_metadata": 100,
        "spatial_image": 110,
    }
    return sorted(
        assigned,
        key=lambda item: (
            role_order.get(item[1], 200),
            item[1],
            item[0].mount_name.casefold(),
        ),
    )


def infer_scientific_asset_specs(paths: Iterable[str | Path]) -> list[dict[str, str]]:
    """Expand exact selections and infer a deterministic compound-asset manifest.

    The function never chooses a primary based on dialog ordering. Ambiguous
    mixtures and incomplete PLINK bundles fail before a workspace is replaced.
    """
    assigned = _assign_scientific_roles(_discover_selected_assets(paths))
    return [
        {
            "path": str(asset.path),
            "format": asset.format,
            "role": role,
            "relative_mount_name": asset.mount_name,
            "name": asset.name,
            "support": scientific_format_support(asset.format),
        }
        for asset, role in assigned
    ]


def validate_relative_mount_name(value: str) -> str:
    """Return one normalized, workspace-relative POSIX mount name."""
    raw = str(value).strip()
    relative = PurePosixPath(raw)
    if (
        not raw
        or raw in {".", ".."}
        or "\\" in raw
        or relative.is_absolute()
        or relative.as_posix() != raw
        or any(part in {"", ".", ".."} for part in relative.parts)
        or (relative.parts and ":" in relative.parts[0])
    ):
        raise ValueError("Invalid relative mount name")
    return relative.as_posix()


@dataclass(frozen=True)
class LocalAssetInspection:
    path: Path
    kind: str
    size_bytes: int
    device: int
    inode: int
    object_type: int
    mtime_ns: int
    ctime_ns: int
    fingerprint_sha256: str


def inspect_local_asset_path(
    source_path: str | Path,
    *,
    require_absolute: bool = True,
    cancel_check: Callable[[], bool] | None = None,
) -> LocalAssetInspection:
    """Validate exactly one local file/directory and reject symlink traversal.

    The selected entry itself and every entry below a selected directory must
    be a regular file or directory.  Symlinks in the selected path or below a
    selected directory are never followed.
    """
    source = Path(source_path).expanduser()
    if require_absolute and not source.is_absolute():
        raise ValueError("Scientific dataset source path must be absolute")
    if ".." in source.parts:
        raise ValueError("Scientific dataset source path must be exact")
    absolute_source = source if source.is_absolute() else Path.cwd() / source
    current = Path(absolute_source.anchor)
    for part in absolute_source.parts[1:]:
        current /= part
        try:
            if _is_link_or_reparse(current.lstat()):
                raise ValueError("Scientific dataset source path cannot traverse a symlink")
        except FileNotFoundError:
            break
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError("Scientific dataset source does not exist or cannot be inspected") from exc
    try:
        if source.is_symlink():
            raise ValueError("Scientific dataset source cannot be a symlink")
        resolved = source.resolve(strict=True)
        source_stat = source.lstat()
        if _is_link_or_reparse(source_stat):
            raise ValueError("Scientific dataset source cannot be a symlink")
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("Scientific dataset source does not exist or cannot be inspected") from exc

    mode = source_stat.st_mode
    identity = {
        "device": int(getattr(source_stat, "st_dev", 0)),
        "inode": int(getattr(source_stat, "st_ino", 0)),
        "object_type": int(stat.S_IFMT(mode)),
        "mtime_ns": int(
            getattr(source_stat, "st_mtime_ns", int(source_stat.st_mtime * 1_000_000_000))
        ),
        "ctime_ns": _stat_ns(source_stat, "ctime"),
    }
    if stat.S_ISREG(mode):
        return LocalAssetInspection(
            path=resolved,
            kind="file",
            size_bytes=int(source_stat.st_size),
            fingerprint_sha256=_file_fingerprint(resolved, source_stat),
            **identity,
        )
    if stat.S_ISDIR(mode):
        scan = _bounded_directory_scan(resolved, cancel_check=cancel_check)
        return LocalAssetInspection(
            path=resolved,
            kind="directory",
            size_bytes=scan.size_bytes,
            fingerprint_sha256=scan.fingerprint_sha256,
            **identity,
        )
    raise ValueError("Scientific dataset source must be a regular file or directory")


@dataclass(frozen=True)
class ScientificDataAsset:
    """One file or directory in a Scientific Dataset Bundle."""

    path: str
    format: str
    role: str
    size_bytes: int
    access_mode: str
    relative_mount_name: str
    kind: str = "file"
    name: str = ""
    support: str = ""
    device: int | None = None
    inode: int | None = None
    object_type: int | None = None
    mtime_ns: int | None = None
    ctime_ns: int | None = None
    fingerprint_sha256: str | None = None

    def __post_init__(self) -> None:
        path = str(self.path)
        data_format = str(self.format).strip().lower().lstrip(".")
        role = str(self.role).strip()
        access_mode = str(self.access_mode).strip().lower()
        kind = str(self.kind).strip().lower()
        support = str(self.support or scientific_format_support(data_format)).strip().lower()
        mount_name = validate_relative_mount_name(self.relative_mount_name)
        if not path:
            raise ValueError("Scientific data asset path is required")
        if not _FORMAT_RE.fullmatch(data_format):
            raise ValueError("Invalid scientific data asset format")
        if not role:
            raise ValueError("Scientific data asset role is required")
        if access_mode not in SCIENTIFIC_DATA_ACCESS_MODES:
            raise ValueError("Invalid scientific data asset access mode")
        if kind not in SCIENTIFIC_DATA_ASSET_KINDS:
            raise ValueError("Invalid scientific data asset kind")
        if support not in {"loader_backed", "upstream_required"}:
            raise ValueError("Invalid scientific data asset support status")
        try:
            size_bytes = int(self.size_bytes)
        except (TypeError, ValueError):
            raise ValueError("Invalid scientific data asset size")
        if size_bytes < 0:
            raise ValueError("Invalid scientific data asset size")
        identity_values: dict[str, int | None] = {}
        for field_name in ("device", "inode", "object_type", "mtime_ns", "ctime_ns"):
            raw_value = getattr(self, field_name)
            if raw_value is None or raw_value == "":
                identity_values[field_name] = None
                continue
            try:
                identity_values[field_name] = int(raw_value)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid scientific data asset {field_name}")
        fingerprint = self.fingerprint_sha256
        if fingerprint is not None and fingerprint != "":
            fingerprint = str(fingerprint).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise ValueError("Invalid scientific data asset fingerprint_sha256")
        else:
            fingerprint = None

        object.__setattr__(self, "path", path)
        object.__setattr__(self, "format", data_format)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(self, "access_mode", access_mode)
        object.__setattr__(self, "relative_mount_name", mount_name)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "name", str(self.name or PurePosixPath(mount_name).name))
        object.__setattr__(self, "support", support)
        for field_name, value in identity_values.items():
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "fingerprint_sha256", fingerprint)

    @property
    def mount_name(self) -> str:
        """Compatibility alias for callers that use the shorter term."""
        return self.relative_mount_name

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScientificDataAsset":
        path = data.get("path", data.get("stored_path", data.get("source_path", "")))
        mount_name = data.get("relative_mount_name", data.get("mount_name"))
        identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
        if not mount_name:
            mount_name = Path(str(path)).name
        return cls(
            path=str(path),
            format=str(data["format"]),
            role=str(data.get("role") or "primary"),
            size_bytes=int(data.get("size_bytes") or 0),
            access_mode=str(data.get("access_mode") or COPY_ACCESS),
            relative_mount_name=str(mount_name),
            kind=str(data.get("kind") or "file"),
            name=str(data.get("name") or PurePosixPath(str(mount_name)).name),
            support=str(data.get("support") or ""),
            device=identity.get("device", data.get("device")),
            inode=identity.get("inode", data.get("inode")),
            object_type=identity.get("type", data.get("object_type")),
            mtime_ns=identity.get("mtime_ns", data.get("mtime_ns")),
            ctime_ns=identity.get("ctime_ns", data.get("ctime_ns")),
            fingerprint_sha256=identity.get(
                "fingerprint_sha256",
                data.get("fingerprint_sha256"),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return browser-safe metadata without the real host path."""
        return {
            "name": self.name,
            "format": self.format,
            "role": self.role,
            "access_mode": self.access_mode,
            "relative_mount_name": self.relative_mount_name,
            "kind": self.kind,
            "support": self.support,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        stored = {
            **self.to_dict(),
            "path": self.path,
            "size_bytes": self.size_bytes,
        }
        if any(
            value is not None
            for value in (
                self.device,
                self.inode,
                self.object_type,
                self.mtime_ns,
                self.ctime_ns,
                self.fingerprint_sha256,
            )
        ):
            stored["identity"] = {
                "device": self.device,
                "inode": self.inode,
                "type": self.object_type,
                "mtime_ns": self.mtime_ns,
                "ctime_ns": self.ctime_ns,
                "fingerprint_sha256": self.fingerprint_sha256,
            }
        return stored


def validate_scientific_data_asset(asset: ScientificDataAsset) -> LocalAssetInspection:
    """Revalidate a stored asset before it becomes the active workspace input."""
    inspected = inspect_local_asset_path(asset.path)
    if str(inspected.path) != str(Path(asset.path)):
        raise ValueError("Scientific dataset asset path is no longer exact")
    if inspected.kind != asset.kind or inspected.size_bytes != asset.size_bytes:
        raise ValueError("Scientific dataset asset changed after import")
    for field_name in (
        "device",
        "inode",
        "object_type",
        "mtime_ns",
        "ctime_ns",
        "fingerprint_sha256",
    ):
        expected = getattr(asset, field_name)
        if expected is not None and expected != getattr(inspected, field_name):
            raise ValueError("Scientific dataset asset identity changed after import")
    return inspected
