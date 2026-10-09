"""Host-aware readiness for non-R scientific dependencies.

This module is the single seam between package/workflow intent and the user's
machine.  Callers ask for package names, workflow text, or explicit dependency
identifiers and receive a path-free, JSON-safe readiness report.  The report is
diagnostic: it never installs operating-system software or mutates the host.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExternalDependency:
    dependency_id: str
    label: str
    kind: str
    command_groups: tuple[tuple[str, ...], ...] = ()
    description: str = ""
    setup_action: str = ""
    verification_action: str = ""
    automatic: bool = False


@dataclass(frozen=True)
class ExternalDependencyState:
    dependency_id: str
    label: str
    kind: str
    status: str
    detail: str
    found_commands: tuple[str, ...]
    missing_commands: tuple[str, ...]
    setup_action: str
    verification_action: str
    automatic: bool


DEPENDENCIES: dict[str, ExternalDependency] = {
    "c_cpp_toolchain": ExternalDependency(
        "c_cpp_toolchain",
        "C/C++ 编译工具链",
        "toolchain",
        (("clang", "gcc", "cc"), ("clang++", "g++", "c++"), ("make", "mingw32-make")),
        "源码 R 包、Stan 模型和部分高性能计算包需要编译器与 make。",
        "macOS 安装 Xcode Command Line Tools；Windows 安装与当前 R 版本匹配的 Rtools。",
        "运行 C/C++ 编译器与 make 的版本检查，再执行包或模型的最小编译测试。",
    ),
    "fortran_toolchain": ExternalDependency(
        "fortran_toolchain",
        "Fortran 编译器",
        "toolchain",
        (("gfortran",),),
        "部分数值、贝叶斯和组学包从源码安装时需要 gfortran。",
        "安装与当前 R 和系统架构匹配的 GNU Fortran 工具链。",
        "运行 gfortran --version，并用当前 R 执行最小 Fortran 编译测试。",
    ),
    "cmake": ExternalDependency(
        "cmake",
        "CMake",
        "toolchain",
        (("cmake",),),
        "Arrow、深度学习及部分 C/C++ 依赖从源码构建时可能需要 CMake。",
        "通过操作系统软件管理器或 CMake 官方安装程序安装 CMake。",
        "运行 cmake --version。",
    ),
    "pkg_config": ExternalDependency(
        "pkg_config",
        "pkg-config",
        "toolchain",
        (("pkg-config", "pkgconf"),),
        "源码包用它发现 GDAL、PROJ、GEOS、HDF5、NetCDF 等系统库。",
        "通过操作系统软件管理器安装 pkg-config/pkgconf。",
        "运行 pkg-config --version，并按安装日志检查目标系统库。",
    ),
    "system_library": ExternalDependency(
        "system_library",
        "系统开发库/头文件",
        "system_library",
        (("pkg-config", "pkgconf"),),
        "R 包配置或编译阶段缺少目标系统库、开发头文件或动态链接库。",
        "根据安装日志中的头文件、库名和 configure 提示安装对应系统开发包。",
        "重新运行包安装，并确认目标 namespace 可以无警告加载。",
    ),
    "git": ExternalDependency(
        "git",
        "Git",
        "tool",
        (("git",),),
        "从官方 GitHub 源安装尚未发布到 CRAN/Bioconductor 的 R 包时需要 Git。",
        "安装 Git 并确保应用进程的 PATH 可以找到它。",
        "运行 git --version。",
    ),
    "java": ExternalDependency(
        "java",
        "Java 运行环境",
        "runtime",
        (("java",),),
        "H2O、Spark 和部分 Java 驱动的 R 包需要可实际启动的 Java。",
        "安装与机器架构匹配的 JDK/JRE，并在需要时配置 JAVA_HOME。",
        "运行 java -version；随后执行所选 R 包的最小初始化。",
    ),
    "python_ml": ExternalDependency(
        "python_ml",
        "Python 与机器学习后端",
        "runtime",
        (("python3", "python"),),
        "TensorFlow/Keras 等 R 接口需要兼容的 Python 环境和对应框架。",
        "按所选 R 包的官方方式配置 Python 与框架后端；应用不强制创建 Conda 环境。",
        "从 R 中检查 reticulate Python 配置并执行框架的最小设备/张量运算。",
    ),
    "cmdstan": ExternalDependency(
        "cmdstan",
        "CmdStan",
        "runtime",
        description="cmdstanr 包只是接口；实际建模还需要 CmdStan 与 C++ 工具链。",
        setup_action="安装 cmdstanr 后运行 cmdstanr::check_cmdstan_toolchain() 和 cmdstanr::install_cmdstan()。",
        verification_action="运行 cmdstanr::cmdstan_version(error_on_NA = FALSE)，再编译最小模型。",
        automatic=False,
    ),
    "libtorch": ExternalDependency(
        "libtorch",
        "LibTorch/Lantern",
        "runtime",
        description="R torch 包首次使用还需要与包版本匹配的 LibTorch/Lantern。",
        setup_action="在 R 中运行 torch::install_torch()，完成后重新启动 R 进程。",
        verification_action="运行 torch::torch_is_installed() 并执行一个最小张量运算。",
        automatic=False,
    ),
    "spark": ExternalDependency(
        "spark",
        "Apache Spark",
        "runtime",
        (("spark-submit",),),
        "sparklyr 需要可启动且版本兼容的本机或远程 Spark 运行时。",
        "按 sparklyr 官方方式选择并安装/连接 Spark；本机安装前需用户确认版本和磁盘占用。",
        "运行 spark-submit --version，再用 sparklyr 建立并关闭一个最小连接。",
        automatic=False,
    ),
    "cuda": ExternalDependency(
        "cuda",
        "NVIDIA CUDA",
        "accelerator",
        (("nvidia-smi",), ("nvcc",)),
        "仅 CUDA/GPU 加速路径需要匹配的 NVIDIA 驱动、工具链与框架构建；CPU 路径不依赖它。",
        "在受支持的 NVIDIA 机器上安装相互兼容的驱动、CUDA 工具链和目标框架版本。",
        "运行 nvidia-smi 与 nvcc --version，再从目标框架执行设备枚举和最小张量运算。",
        automatic=False,
    ),
    "geospatial_libraries": ExternalDependency(
        "geospatial_libraries",
        "GDAL/GEOS/PROJ/udunits 系统库",
        "system_library",
        description="sf、terra、stars、units 等源码安装需要匹配的地理空间系统库。",
        setup_action="按包安装日志安装 GDAL、GEOS、PROJ、udunits 等缺失库；已有可加载二进制包时无需重复安装。",
        verification_action="加载目标 R 包并检查 sf::sf_extSoftVersion() 或对应包的运行时版本。",
    ),
    "image_libraries": ExternalDependency(
        "image_libraries",
        "图像处理系统库",
        "system_library",
        description="magick、部分影像包从源码安装或运行时需要 ImageMagick/图像编解码库。",
        setup_action="按目标包日志安装 ImageMagick 及所需图像格式库。",
        verification_action="加载目标 R 包并读写一个临时小图像。",
    ),
    "hdf5_netcdf_libraries": ExternalDependency(
        "hdf5_netcdf_libraries",
        "HDF5/NetCDF 系统库",
        "system_library",
        description="HDF5、NetCDF、质谱和部分科学文件包从源码安装时需要对应系统库。",
        setup_action="按目标包日志安装 HDF5/NetCDF；已有可加载二进制包时无需重复安装。",
        verification_action="加载目标 R 包并读取一个小型测试对象或包自带示例。",
    ),
    "genome_resources": ExternalDependency(
        "genome_resources",
        "参考基因组与注释资源",
        "scientific_data",
        description="基因组、变异、单细胞和表观组学分析需要与物种及版本匹配的参考和注释。",
        setup_action="由用户提供或下载与研究物种、基因组版本和方法匹配的参考资源，并记录版本。",
        verification_action="核对序列命名、版本、校验和以及样本/特征映射。",
    ),
    "scenic_databases": ExternalDependency(
        "scenic_databases",
        "SCENIC motif 数据库",
        "scientific_data",
        description="SCENIC/RcisTarget 需要与物种和基因组版本匹配的 motif ranking 数据库。",
        setup_action="下载官方支持的目标物种数据库，并在分析计划中明确数据库版本和路径。",
        verification_action="检查数据库文件可读、物种匹配且基因标识覆盖合理。",
    ),
    "samtools": ExternalDependency(
        "samtools", "samtools", "scientific_cli", (("samtools",),),
        "SAM/BAM/CRAM 排序、转换、索引和检查。",
        "通过操作系统软件管理器或 samtools/htslib 官方发行版安装。",
        "运行 samtools --version，并对测试文件执行 quickcheck/index 检查。",
    ),
    "bcftools": ExternalDependency(
        "bcftools", "bcftools + bgzip/tabix", "scientific_cli",
        (("bcftools",), ("bgzip",), ("tabix",)),
        "VCF/BCF 规范化、压缩、索引和查询。",
        "通过操作系统软件管理器或 bcftools/htslib 官方发行版安装。",
        "运行 bcftools --version、bgzip --help 和 tabix --help。",
    ),
    "fastqc": ExternalDependency(
        "fastqc", "FastQC", "scientific_cli", (("fastqc",),),
        "FASTQ 原始读段质量检查。",
        "安装 FastQC；它通常还需要 Java。",
        "运行 fastqc --version。",
    ),
    "fastp": ExternalDependency(
        "fastp", "fastp", "scientific_cli", (("fastp",),),
        "FASTQ 过滤、剪切与质控报告。",
        "通过操作系统软件管理器或 fastp 官方发行版安装。",
        "运行 fastp --version。",
    ),
    "read_aligner": ExternalDependency(
        "read_aligner", "读段比对器", "scientific_cli",
        (("STAR", "bwa", "bowtie2"),),
        "FASTQ 比对至少需要一种与实验类型匹配的比对器。",
        "根据 RNA-seq、DNA-seq 或其他实验选择并安装 STAR、BWA 或 Bowtie2。",
        "运行所选比对器的版本检查，并用少量读段验证参考索引。",
    ),
    "transcript_quantifier": ExternalDependency(
        "transcript_quantifier", "转录本定量工具", "scientific_cli",
        (("salmon", "kallisto"),),
        "RNA-seq 免比对/准比对定量至少需要一种兼容工具。",
        "选择并安装 Salmon 或 Kallisto，并建立与注释版本一致的索引。",
        "运行所选工具的版本检查，并用少量读段验证索引。",
    ),
    "plink": ExternalDependency(
        "plink", "PLINK/PLINK2", "scientific_cli", (("plink2", "plink"),),
        "大规模基因型数据质控、格式转换与关联分析。",
        "安装与系统架构匹配的 PLINK2（必要时同时保留 PLINK 1.9）。",
        "运行 plink2 --version 或 plink --version。",
    ),
    "qiime2": ExternalDependency(
        "qiime2", "QIIME 2", "scientific_cli", (("qiime",),),
        "从原始 16S/宏基因组读段重新处理或验证 QZA provenance 时需要 QIIME 2。",
        "按 QIIME 2 官方方式安装与目标插件集合匹配的发行版。",
        "运行 qiime --help 并验证目标 QZA semantic type。",
    ),
}


PACKAGE_DEPENDENCY_IDS: dict[str, tuple[str, ...]] = {
    "ArchR": ("c_cpp_toolchain", "genome_resources"),
    "SCENIC": ("scenic_databases", "genome_resources"),
    "brms": ("c_cpp_toolchain",),
    "cmdstanr": ("c_cpp_toolchain", "cmdstan"),
    "h2o": ("java",),
    "keras": ("python_ml",),
    "keras3": ("python_ml",),
    "monocle3": ("c_cpp_toolchain", "geospatial_libraries"),
    "rstan": ("c_cpp_toolchain",),
    "sparklyr": ("java", "spark"),
    "tfautograph": ("python_ml",),
    "tfdatasets": ("python_ml",),
    "tfruns": ("python_ml",),
    "tensorflow": ("python_ml",),
    "torch": ("libtorch",),
    "sf": ("geospatial_libraries",),
    "terra": ("geospatial_libraries",),
    "stars": ("geospatial_libraries",),
    "units": ("geospatial_libraries",),
    "exactextractr": ("geospatial_libraries",),
    "magick": ("image_libraries",),
    "hdf5r": ("hdf5_netcdf_libraries",),
    "rhdf5": ("hdf5_netcdf_libraries",),
    "ncdf4": ("hdf5_netcdf_libraries",),
    "MSnbase": ("hdf5_netcdf_libraries",),
    "mzR": ("hdf5_netcdf_libraries",),
    "VariantAnnotation": ("genome_resources",),
    "rtracklayer": ("genome_resources",),
    "BSgenome": ("genome_resources",),
    "minfi": ("genome_resources",),
}


_INTENT_DEPENDENCIES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("fastq", "raw read", "原始读段", "测序读段"), ("fastqc", "fastp", "read_aligner", "transcript_quantifier")),
    (("bam", "cram", "sam file", "比对文件"), ("samtools",)),
    (("vcf", "bcf", "variant file", "变异文件"), ("bcftools",)),
    (("plink", "gwas", "基因型"), ("plink",)),
    (("qza", "qiime", "16s raw", "原始16s"), ("qiime2",)),
    (("cmdstan", "stan model"), ("cmdstan", "c_cpp_toolchain")),
    (("cuda", "nvidia gpu", "gpu 加速"), ("cuda",)),
    (("spark", "分布式计算"), ("java", "spark")),
    (("system_library", "missing header", "configure error"), ("system_library",)),
)


CORE_DEPENDENCY_IDS = (
    "c_cpp_toolchain",
    "fortran_toolchain",
    "cmake",
    "pkg_config",
    "git",
    "java",
    "python_ml",
)


def package_dependency_ids(packages: Iterable[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for package in packages:
        for dependency_id in PACKAGE_DEPENDENCY_IDS.get(str(package).strip(), ()):
            if dependency_id not in selected:
                selected.append(dependency_id)
    return tuple(selected)


def intent_dependency_ids(intent: str) -> tuple[str, ...]:
    normalized = str(intent or "").casefold()
    selected: list[str] = []
    for triggers, dependency_ids in _INTENT_DEPENDENCIES:
        if not any(trigger.casefold() in normalized for trigger in triggers):
            continue
        for dependency_id in dependency_ids:
            if dependency_id not in selected:
                selected.append(dependency_id)
    return tuple(selected)


def dependency_setup_requirements(packages: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        DEPENDENCIES[dependency_id].description + " " + DEPENDENCIES[dependency_id].setup_action
        for dependency_id in package_dependency_ids(packages)
    )


def _command_path(command: str, environment: Mapping[str, str]) -> str:
    return str(shutil.which(command, path=environment.get("PATH")) or "")


def _probe_command(command: str, environment: Mapping[str, str]) -> tuple[bool, str]:
    arguments = [command, "-version" if command in {"java", "javac"} else "--version"]
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=5,
            env=dict(environment),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = str(completed.stdout or completed.stderr or "").strip().splitlines()
    detail = output[0][:240] if output else f"exit={completed.returncode}"
    return completed.returncode == 0, detail


def _cmdstan_path(environment: Mapping[str, str]) -> str:
    configured = str(environment.get("CMDSTAN") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    root = Path.home() / ".cmdstan"
    if root.is_dir():
        candidates.extend(sorted(root.glob("cmdstan-*"), reverse=True))
    for candidate in candidates:
        if (candidate / "makefile").is_file():
            return str(candidate.resolve(strict=False))
    return ""


def _dependency_state(
    dependency: ExternalDependency,
    environment: Mapping[str, str],
) -> ExternalDependencyState:
    found: list[str] = []
    missing: list[str] = []
    probe_details: list[str] = []
    for alternatives in dependency.command_groups:
        located = next(
            ((command, _command_path(command, environment)) for command in alternatives if _command_path(command, environment)),
            None,
        )
        if located is None:
            missing.append("/".join(alternatives))
            continue
        command, command_path = located
        found.append(f"{command}={command_path}")
        if dependency.dependency_id in {"java", "python_ml"}:
            ok, detail = _probe_command(command, environment)
            probe_details.append(detail)
            if not ok:
                missing.append(command)

    if dependency.dependency_id == "cmdstan":
        discovered = _cmdstan_path(environment)
        if discovered:
            found.append(f"cmdstan={discovered}")
            status = "ready"
            detail = discovered
        else:
            status = "needs_setup"
            detail = dependency.description
    elif dependency.dependency_id == "python_ml" and not missing:
        status = "needs_setup"
        detail = (
            ("；".join(probe_details) + "；") if probe_details else ""
        ) + "已发现 Python，但尚未证明 TensorFlow/Keras 等目标后端可导入和运行。"
    elif dependency.command_groups:
        if missing:
            status = "missing"
            detail = "未发现或无法启动：" + "、".join(dict.fromkeys(missing))
        else:
            status = "ready"
            detail = "；".join(probe_details) or "所需命令已发现"
    else:
        status = "needs_setup"
        detail = dependency.description

    return ExternalDependencyState(
        dependency_id=dependency.dependency_id,
        label=dependency.label,
        kind=dependency.kind,
        status=status,
        detail=detail,
        found_commands=tuple(found),
        missing_commands=tuple(dict.fromkeys(missing)),
        setup_action=dependency.setup_action,
        verification_action=dependency.verification_action,
        automatic=dependency.automatic,
    )


def inspect_external_dependencies(
    *,
    packages: Sequence[str] = (),
    intent: str = "",
    dependency_ids: Sequence[str] = (),
    loadable_packages: Sequence[str] = (),
    satisfied_dependency_ids: Sequence[str] = (),
    blocked_dependency_ids: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return actual host readiness for the requested non-R dependencies."""

    selected = list(dict.fromkeys((*dependency_ids, *package_dependency_ids(packages), *intent_dependency_ids(intent))))
    unknown = [dependency_id for dependency_id in selected if dependency_id not in DEPENDENCIES]
    selected = [dependency_id for dependency_id in selected if dependency_id in DEPENDENCIES]
    process_environment = {
        str(name): str(value)
        for name, value in (os.environ if environment is None else environment).items()
        if value is not None
    }
    states = [_dependency_state(DEPENDENCIES[dependency_id], process_environment) for dependency_id in selected]
    loadable = {str(package).strip() for package in loadable_packages if str(package).strip()}
    requested_packages = list(
        dict.fromkeys(str(package).strip() for package in packages if str(package).strip())
    )
    satisfied_system_dependencies = {
        dependency_id
        for package in requested_packages
        if package in loadable
        for dependency_id in PACKAGE_DEPENDENCY_IDS.get(package, ())
        if DEPENDENCIES[dependency_id].kind == "system_library"
    }
    states = [
        replace(
            state,
            status="ready",
            detail="触发此依赖的已选 R 包 namespace 已在当前 R 中无警告加载。",
        )
        if state.dependency_id in satisfied_system_dependencies
        else state
        for state in states
    ]
    satisfied = {
        str(dependency_id).strip()
        for dependency_id in satisfied_dependency_ids
        if str(dependency_id).strip() in DEPENDENCIES
    }
    blocked = {
        str(dependency_id).strip()
        for dependency_id in blocked_dependency_ids
        if str(dependency_id).strip() in DEPENDENCIES
    }
    states = [
        replace(
            state,
            status="needs_setup",
            detail="所选 R 包的 namespace 或外部运行时自检尚未通过。",
        )
        if state.dependency_id in blocked and state.dependency_id not in satisfied
        else state
        for state in states
    ]
    states = [
        replace(
            state,
            status="ready",
            detail="所选 R 包的运行时自检已通过。",
        )
        if state.dependency_id in satisfied
        else state
        for state in states
    ]
    counts = {status: sum(item.status == status for item in states) for status in ("ready", "missing", "needs_setup", "warning")}
    return {
        "ok": not unknown,
        "platform": {
            "system": platform.system().lower(),
            "machine": platform.machine().lower(),
        },
        "requested_packages": requested_packages,
        "intent": str(intent or ""),
        "counts": counts,
        "states": [asdict(item) for item in states],
        "unknown_dependency_ids": unknown,
        "policy": {
            "installs_system_software": False,
            "mutates_host": False,
            "note": "应用只检测并给出安装/验证路线；操作系统组件和科研参考资源由用户确认后安装。",
        },
    }
