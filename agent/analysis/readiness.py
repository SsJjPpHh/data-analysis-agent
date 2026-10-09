"""Actionable preparation routes for analyses that fail dataset preflight.

The statistical catalog stays focused on analysis methods.  This module is a
single seam between dataset/workflow validation and every user entry path: it
turns a blocked run into a concrete, format-aware preparation plan that can
return to the originally selected template when the derived data are ready.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from agent.analysis.workflows.contracts import WorkflowRunSpec


RUNTIME_PREFLIGHT_ISSUE_CODES = frozenset(
    {
        "ambiguous_index_asset",
        "ambiguous_primary_asset",
        "dataset_mismatch",
        "dataset_shape_mismatch",
        "empty_omics_axis",
        "index_asset_mismatch",
        "invalid_dataset_binding",
        "invalid_indexed_vcf",
        "missing_dataset",
        "missing_feature_axis",
        "missing_index_asset",
        "missing_primary_asset",
        "missing_required_asset_roles",
        "missing_sample_axis",
        "orphan_index_asset",
        "sample_alignment_failed",
        "scientific_modality_mismatch",
        "unknown_dataset_shape",
        "unknown_index_pairing",
        "unknown_omics_axes",
        "unknown_processing_stage",
        "unknown_sample_alignment",
        "invalid_joint_inputs",
        "unresolved_critical_inputs",
        "unsupported_dataset_format",
        "unvalidated_index_content",
        "upstream_processing_required",
    }
)


@dataclass(frozen=True)
class PreparationRoute:
    route_id: str
    title: str
    mode: str
    summary: str
    required_inputs: tuple[str, ...] = ()
    r_packages: tuple[str, ...] = ()
    external_components: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()


_ROUTES: dict[str, PreparationRoute] = {
    "select_current_data": PreparationRoute(
        "select_current_data",
        "重新选择当前分析数据",
        "requires_input",
        "当前计划没有绑定现有数据，或仍指向上一份数据；重新选择数据后生成新计划。",
        ("一份当前要分析的数据或数据包",),
        expected_outputs=("与当前数据绑定的新分析计划",),
    ),
    "reimport_complete_bundle": PreparationRoute(
        "reimport_complete_bundle",
        "重新导入完整且唯一的数据包",
        "requires_input",
        "整理主数据、索引、注释和元数据的角色，确保一次只有一个 primary 主资产。",
        ("唯一主资产", "方法要求的配套侧车文件"),
        expected_outputs=("角色明确的完整数据包",),
    ),
    "inspect_structure": PreparationRoute(
        "inspect_structure",
        "重新检查数据结构与处理阶段",
        "guided",
        "重新执行有界数据检查，确认对象类型、维度、处理阶段和可用加载方式。",
        expected_outputs=("可追溯的数据结构画像", "明确的 analysis-ready 状态"),
    ),
    "convert_representation": PreparationRoute(
        "convert_representation",
        "转换为模板支持的数据表示",
        "guided",
        "保留原始数据，生成与目标模板形态和格式匹配的派生数据。",
        ("目标模板要求的数据形态",),
        expected_outputs=("分析就绪表格、矩阵或 R 对象",),
    ),
    "select_matching_scientific_data": PreparationRoute(
        "select_matching_scientific_data",
        "选择匹配实验模态的数据或模板",
        "requires_input",
        "当前数据的科学模态与目标方法不同；不能通过重命名或数值转换把一种实验数据变成另一种。",
        ("目标实验产生的数据，或与当前数据模态匹配的分析模板",),
        expected_outputs=("科学模态与方法一致的分析输入",),
    ),
    "attach_required_assets": PreparationRoute(
        "attach_required_assets",
        "补齐配套数据资产",
        "requires_input",
        "补齐样本表、特征注释、参考序列、空间坐标或其他方法必需的侧车文件。",
        ("预检列出的缺失资产",),
        expected_outputs=("主资产与配套资产完整的数据包",),
    ),
    "build_indexes": PreparationRoute(
        "build_indexes",
        "规范化并建立匹配索引",
        "guided",
        "对 BAM/CRAM/VCF/BCF 执行格式、排序、压缩和索引准备，并验证索引确实匹配主文件。",
        ("主数据文件", "CRAM 所需参考序列（如适用）"),
        ("Rsamtools",),
        ("samtools/htslib 或 bcftools（按数据格式）",),
        ("规范化主文件", "同名 BAI/CRAI/TBI/CSI 索引", "索引验证记录"),
    ),
    "sample_map": PreparationRoute(
        "sample_map",
        "建立并验证样本映射",
        "guided",
        "统一矩阵、表型、分组和批次信息中的样本 ID，检查重复、缺失和集合差异。",
        ("矩阵样本ID", "元数据样本ID"),
        expected_outputs=("一一对应的 sample_map", "未匹配样本清单"),
    ),
    "resolve_inputs": PreparationRoute(
        "resolve_inputs",
        "确认或补充关键分析字段",
        "requires_input",
        "只确认模板真正需要的变量、参数或设计信息；不存在的研究事实不能由系统编造。",
        ("预检列出的关键字段或研究设定",),
        expected_outputs=("完整变量映射或明确的不可分析说明",),
    ),
    "repair_omics_axes": PreparationRoute(
        "repair_omics_axes",
        "修复特征轴、样本轴或分析对象",
        "guided",
        "检查空矩阵、转置错误、特征/样本名称缺失以及 assay 选择，生成维度明确的分析对象。",
        ("特征矩阵", "样本和特征标识"),
        ("Matrix", "SummarizedExperiment"),
        expected_outputs=("非空且轴语义明确的矩阵或 R 对象",),
    ),
    "general_preparation": PreparationRoute(
        "general_preparation",
        "生成分析就绪数据",
        "guided",
        "根据预检问题清洗、整形或转换数据，并保留原始数据与转换记录。",
        expected_outputs=("分析就绪派生数据", "处理记录"),
    ),
}


_FORMAT_ROUTES: dict[str, PreparationRoute] = {
    "fastq": PreparationRoute(
        "fastq_primary_processing",
        "FASTQ 质控、剪切、比对或定量",
        "guided",
        "先确认测序类型和实验平台，再完成读段质控及适合该实验的比对/定量。",
        ("单端/双端及样本配对", "物种与参考基因组/转录组", "实验平台和文库类型"),
        ("ShortRead", "Rsubread", "tximport"),
        ("FastQC/fastp", "所选比对器或定量器"),
        ("QC 报告", "counts/feature 矩阵", "样本元数据", "处理记录"),
    ),
    "fasta": PreparationRoute(
        "fasta_sequence_preparation",
        "FASTA 序列索引、比对或注释准备",
        "guided",
        "根据核酸/蛋白序列和研究目标选择索引、比对、注释或系统发育流程。",
        ("序列类型", "物种/参考库", "目标分析"),
        ("Biostrings", "DECIPHER", "ape"),
        ("目标数据库或外部比对程序（如方法需要）",),
        ("标准化序列集", "比对/注释/树对象", "处理记录"),
    ),
    "sam": PreparationRoute(
        "sam_alignment_preparation",
        "SAM 排序、压缩与索引",
        "guided",
        "将 SAM 转为坐标排序的 BAM/CRAM，校验参考序列信息并建立匹配索引。",
        ("SAM 主文件", "参考序列（CRAM 或重新比对需要时）"),
        ("Rsamtools",),
        ("samtools/htslib",),
        ("坐标排序 BAM/CRAM", "BAI/CRAI 索引", "转换与验证记录"),
    ),
    "bam": _ROUTES["build_indexes"],
    "cram": _ROUTES["build_indexes"],
    "vcf": PreparationRoute(
        "variant_normalize_index",
        "VCF 规范化、压缩与索引",
        "guided",
        "核对参考基因组，规范化等位基因和多等位位点，生成 bgzip VCF 及匹配索引。",
        ("参考基因组版本", "参考 FASTA（规范化需要时）"),
        ("Rsamtools", "VariantAnnotation"),
        ("bcftools/htslib",),
        ("规范化 vcf.gz", "TBI/CSI 索引", "版本与规范化记录"),
    ),
    "bcf": _ROUTES["build_indexes"],
    "generic_hdf5": PreparationRoute(
        "hdf5_object_conversion",
        "识别 HDF5 内部对象并转换",
        "guided",
        "HDF5 只是容器；先确定 assay、轴、注释和生成软件，再转换为明确的矩阵或 R 对象。",
        ("生成软件/格式规范", "目标 assay", "样本与特征轴"),
        ("hdf5r", "rhdf5"),
        expected_outputs=("明确格式的 H5AD/10x/矩阵/RDS 对象", "转换记录"),
    ),
    "loom": PreparationRoute(
        "loom_object_conversion",
        "Loom 对象检查与转换",
        "guided",
        "确认表达层和行列注释后转换为 SingleCellExperiment、Seurat 或 H5AD。",
        ("表达层", "细胞与特征注释"),
        ("Seurat", "SingleCellExperiment"),
        expected_outputs=("分析就绪单细胞对象",),
    ),
    "h5seurat": PreparationRoute(
        "h5seurat_object_conversion",
        "H5Seurat 对象恢复与验证",
        "guided",
        "加载并核对 assay、细胞和元数据，生成可供下游模板使用的 Seurat/RDS 对象。",
        ("目标 assay",),
        ("Seurat", "SeuratDisk"),
        expected_outputs=("经验证的 Seurat/RDS 对象",),
    ),
    "bigwig": PreparationRoute(
        "bigwig_signal_extraction",
        "BigWig 信号区间提取",
        "guided",
        "根据基因组版本和目标区域提取信号，生成可分析区间表或 R 对象。",
        ("基因组版本", "目标区间或注释"),
        ("rtracklayer", "GenomicRanges"),
        expected_outputs=("区间信号表或 GRanges 对象",),
    ),
    "maf": PreparationRoute(
        "maf_schema_validation",
        "MAF 结构与基因组版本验证",
        "guided",
        "核对必需字段、样本标识、染色体命名和基因组版本，再生成标准变异对象。",
        ("基因组版本", "样本ID字段"),
        ("maftools", "GenomicRanges"),
        expected_outputs=("标准 MAF/变异表", "结构验证报告"),
    ),
    "newick": PreparationRoute(
        "newick_tree_preparation",
        "Newick 树对象与标签匹配",
        "guided",
        "加载树结构并将叶节点标签与样本/物种元数据匹配。",
        ("叶节点含义", "可选样本/物种元数据"),
        ("ape", "phytools"),
        expected_outputs=("树对象 RDS", "标签映射表"),
    ),
    "qza": PreparationRoute(
        "qza_provenance_import",
        "QIIME 2 产物识别与导入",
        "guided",
        "验证 QZA provenance 和 semantic type，再导出/导入特征表、分类和系统发育信息。",
        ("QZA semantic type", "配套 taxonomy/tree/metadata（按方法）"),
        ("qiime2R", "phyloseq"),
        ("QIIME 2（需要重新处理原始读段时）",),
        ("phyloseq/特征表对象", "provenance 记录"),
    ),
    "plink_bed": PreparationRoute(
        "plink_bundle_preparation",
        "PLINK 数据包与表型准备",
        "guided",
        "确保 BED/BIM/FAM 同名配套，并把表型、协变量和等位基因编码与样本对齐。",
        ("同名 BED/BIM/FAM", "表型与协变量", "基因组版本"),
        ("BEDMatrix", "snpStats"),
        ("PLINK/PLINK2（需要格式转换或质控时）",),
        ("完整 PLINK 数据包", "样本映射", "QC 记录"),
    ),
}


_ISSUE_ROUTES: dict[str, str] = {
    "missing_dataset": "select_current_data",
    "dataset_mismatch": "select_current_data",
    "invalid_dataset_binding": "select_current_data",
    "missing_primary_asset": "reimport_complete_bundle",
    "ambiguous_primary_asset": "reimport_complete_bundle",
    "orphan_index_asset": "reimport_complete_bundle",
    "missing_required_asset_roles": "attach_required_assets",
    "missing_index_asset": "build_indexes",
    "index_asset_mismatch": "build_indexes",
    "invalid_indexed_vcf": "build_indexes",
    "unknown_index_pairing": "build_indexes",
    "ambiguous_index_asset": "build_indexes",
    "unvalidated_index_content": "build_indexes",
    "unknown_dataset_shape": "inspect_structure",
    "unknown_processing_stage": "inspect_structure",
    "dataset_shape_mismatch": "convert_representation",
    "unsupported_dataset_format": "convert_representation",
    "upstream_processing_required": "general_preparation",
    "unknown_omics_axes": "repair_omics_axes",
    "empty_omics_axis": "repair_omics_axes",
    "missing_feature_axis": "repair_omics_axes",
    "missing_sample_axis": "repair_omics_axes",
    "sample_alignment_failed": "sample_map",
    "scientific_modality_mismatch": "select_matching_scientific_data",
    "unknown_sample_alignment": "sample_map",
    "unresolved_critical_inputs": "resolve_inputs",
    "invalid_joint_inputs": "resolve_inputs",
}

_FORMAT_ROUTE_ISSUE_CODES = frozenset(
    {
        "ambiguous_index_asset",
        "index_asset_mismatch",
        "invalid_indexed_vcf",
        "missing_index_asset",
        "unknown_index_pairing",
        "unvalidated_index_content",
        "unsupported_dataset_format",
        "upstream_processing_required",
    }
)

_ROUTE_PRIORITY = {
    "select_current_data": 0,
    "reimport_complete_bundle": 1,
    "attach_required_assets": 2,
    "inspect_structure": 3,
    "build_indexes": 4,
    "sample_map": 6,
    "repair_omics_axes": 7,
    "convert_representation": 8,
    "select_matching_scientific_data": 9,
    "resolve_inputs": 10,
    "general_preparation": 11,
}


def _normalized(value: object) -> str:
    return str(value or "").strip().casefold().lstrip(".")


def _profile_formats(dataset_profile: Mapping[str, Any]) -> tuple[str, ...]:
    formats: list[str] = []
    raw_assets = dataset_profile.get("assets")
    if isinstance(raw_assets, (list, tuple)):
        primary = [
            item
            for item in raw_assets
            if isinstance(item, Mapping) and _normalized(item.get("role")) == "primary"
        ]
        for item in primary or raw_assets[:1]:
            if isinstance(item, Mapping):
                value = _normalized(item.get("format"))
                if value and value not in formats:
                    formats.append(value)
    value = _normalized(dataset_profile.get("format"))
    if value and value not in formats:
        formats.append(value)
    return tuple(formats)


def has_format_specific_preparation_route(dataset_profile: Mapping[str, Any]) -> bool:
    """Return whether the active data have a known upstream conversion path."""

    return any(data_format in _FORMAT_ROUTES for data_format in _profile_formats(dataset_profile))


def _route_payload(route: PreparationRoute, *, reason_codes: Sequence[str]) -> dict[str, Any]:
    payload = asdict(route)
    payload["reason_codes"] = list(dict.fromkeys(reason_codes))
    return payload


def build_analysis_readiness_plan(
    run_spec: WorkflowRunSpec,
    dataset_profile: Mapping[str, Any],
    issues: Sequence[Any],
) -> dict[str, Any]:
    """Convert preflight issues into ordered preparation actions.

    The result is path-free and safe to expose to the browser and the model.
    Every runtime preflight issue has a route; unknown future codes fall back
    to the general preparation route rather than becoming a dead end.
    """

    issue_rows = [
        {
            "code": str(getattr(issue, "code", "") or "workflow_preflight"),
            "message": str(getattr(issue, "message", "") or "").strip(),
        }
        for issue in issues
    ]
    codes = tuple(dict.fromkeys(row["code"] for row in issue_rows))
    formats = _profile_formats(dataset_profile)
    selected: list[tuple[PreparationRoute, list[str]]] = []

    def add(route: PreparationRoute, code: str) -> None:
        for existing, reasons in selected:
            if existing.route_id == route.route_id:
                if code not in reasons:
                    reasons.append(code)
                return
        selected.append((route, [code]))

    format_reasons = tuple(code for code in codes if code in _FORMAT_ROUTE_ISSUE_CODES)
    if format_reasons:
        for data_format in formats:
            route = _FORMAT_ROUTES.get(data_format)
            if route is not None:
                for code in format_reasons:
                    add(route, code)

    for code in codes:
        if any(code in reasons for _route, reasons in selected):
            continue
        route_id = _ISSUE_ROUTES.get(code, "general_preparation")
        route = _ROUTES[route_id]
        if route_id == "general_preparation" and selected:
            continue
        add(route, code)

    if not selected:
        add(_ROUTES["general_preparation"], "workflow_preflight")
    selected.sort(key=lambda item: _ROUTE_PRIORITY.get(item[0].route_id, 5))

    target_label = run_spec.definition.title or run_spec.template_id or run_spec.definition.family
    prepare_step = next(
        (step for step in run_spec.steps if step.phase == "prepare"),
        None,
    )
    primary = selected[0][0]
    route_lines = []
    for index, (route, _reasons) in enumerate(selected, start=1):
        details = [f"{index}. {route.title}：{route.summary}"]
        if route.required_inputs:
            details.append("所需输入=" + "、".join(route.required_inputs))
        if route.r_packages:
            details.append("R包=" + "、".join(route.r_packages))
        if route.external_components:
            details.append("外部组件=" + "、".join(route.external_components))
        if route.expected_outputs:
            details.append("应生成=" + "、".join(route.expected_outputs))
        route_lines.append("；".join(details))
    prompt = "\n".join(
        (
            f"请为当前上传数据执行分析前准备流程“{primary.title}”。",
            f"目标是生成可继续执行“{target_label}”的派生数据；不得修改或覆盖原始数据。",
            (
                "目标方法前置条件：" + "；".join(prepare_step.checks)
                if prepare_step is not None and prepare_step.checks
                else "目标方法没有额外声明的前置条件。"
            ),
            "先核对所需输入和本机组件：当前数据与执行边界允许时，可用本机 R 安全完成并保存处理记录；"
            "缺少参考数据、实验平台信息或外部程序时，只询问无法可靠推断的关键项。",
            "完成后重新检查派生数据，确认格式、对象类型、处理阶段、样本/特征轴和配套资产，"
            "然后回到原分析目标，不要把有界检查摘要当作完整数据。",
            "这是上游准备任务，不是下游结果分析。若当前应用不能安全地产生派生数据，"
            "请给出明确的本机工具、命令/参数原则、预期输出和重新导入要求，然后等待用户导入，"
            "不要反复尝试同一个被预检阻止的下游工作流。",
            "\n".join(route_lines),
        )
    )
    return {
        "status": "preparation_required",
        "target": {
            "template_id": run_spec.template_id,
            "workflow_id": run_spec.workflow_id,
            "title": target_label,
            "accepted_formats": list(run_spec.data_contract.formats),
            "critical_inputs": list(run_spec.data_contract.critical_inputs),
            "preconditions": list(prepare_step.checks) if prepare_step is not None else [],
        },
        "dataset": {
            "dataset_id": str(dataset_profile.get("dataset_id") or ""),
            "formats": list(formats),
            "processing_stage": str(
                dataset_profile.get("processing_stage")
                or (
                    dataset_profile.get("scientific", {}).get("processing_stage")
                    if isinstance(dataset_profile.get("scientific"), Mapping)
                    else ""
                )
                or "unknown"
            ),
        },
        "headline": f"当前数据需要先准备，再执行“{target_label}”",
        "summary": primary.summary,
        "primary_route_id": primary.route_id,
        "actions": [
            _route_payload(route, reason_codes=reasons) for route, reasons in selected
        ],
        "issues": issue_rows,
        "prompt": prompt,
        "resume": {
            "template_id": run_spec.template_id,
            "conversation_prompt": (
                ""
                if run_spec.template_id
                else f"请基于处理后的当前数据继续完成以下分析：{target_label}"
            ),
            "available_after_reinspection": True,
            "requires_user_confirmation": True,
            "preserve_original": True,
        },
    }


def build_template_compatibility_guidance(
    run_spec: WorkflowRunSpec,
    dataset_profile: Mapping[str, Any],
    reason: str,
    *,
    alternative_templates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Explain a genuine template/data mismatch without implying conversion is safe.

    A not-applicable result is scientifically different from an upstream block:
    the current data may simply describe another study domain.  The guidance
    therefore exposes both paths—prepare the actual target object when it
    exists, or continue with a compatible analysis of the current data.
    """

    scientific = dataset_profile.get("scientific")
    scientific = scientific if isinstance(scientific, Mapping) else {}
    formats = _profile_formats(dataset_profile)
    modality = str(
        dataset_profile.get("modality") or scientific.get("modality") or "unknown"
    ).strip()
    representation = str(
        dataset_profile.get("representation")
        or scientific.get("representation")
        or dataset_profile.get("object_kind")
        or "unknown"
    ).strip()
    target_label = run_spec.definition.title or run_spec.template_id or run_spec.definition.family
    matrix_required = any(
        item.kind == "object" and "numeric_matrix" in item.accepts
        for item in run_spec.data_contract.inputs
    )
    if run_spec.data_contract.shape == "r_object":
        primary = PreparationRoute(
            "prepare_required_r_object",
            "准备模板要求的 R 分析对象或完整科学数据包",
            "guided",
            "确认当前表格是否只是样本注释；若是，应与真正的主分析对象一起导入，或由上游数据生成目标 R 对象。",
            ("目标方法的原始/上游数据", "对象类型、assay、样本轴与特征轴", "配套样本或特征注释"),
            expected_outputs=("对象类别和轴语义明确的 RDS/原生科学对象", "处理与样本映射记录"),
        )
    elif matrix_required:
        primary = PreparationRoute(
            "confirm_or_prepare_scientific_matrix",
            "确认矩阵科学语义，必要时生成分析对象",
            "guided",
            "仅有多列数值不能证明是表达或特征矩阵；需确认样本×特征方向、特征含义、数值尺度和分组对齐。",
            ("特征/基因/蛋白/代谢物标识", "样本标识与矩阵方向", "分组和批次注释", "数值是否为原始 counts 或已归一化值"),
            expected_outputs=("样本×特征分析表，或带 assay/colData 的 R 对象", "样本对齐与预处理记录"),
        )
    else:
        primary = PreparationRoute(
            "satisfy_template_data_contract",
            "补齐目标模板的数据合同",
            "guided",
            "核对当前数据与目标方法所需的形态、格式、关键字段和配套资产；不能从当前数据推断的研究事实需由用户提供。",
            tuple(run_spec.data_contract.critical_inputs),
            expected_outputs=("满足目标模板输入合同的数据或对象",),
        )

    alternatives = [
        {
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "reason": str(item.get("reason") or ""),
        }
        for item in alternative_templates
        if str(item.get("id") or "").strip() and str(item.get("title") or "").strip()
    ]
    actions = [_route_payload(primary, reason_codes=("template_not_applicable",))]
    if alternatives:
        actions.append(
            {
                "route_id": "choose_compatible_template",
                "title": "改用适合当前数据的分析模板",
                "mode": "requires_input",
                "summary": "若当前数据本来就是普通结构化研究表，不应强行转换成另一科学模态，可直接选择下列兼容分析。",
                "required_inputs": [],
                "r_packages": [],
                "external_components": [],
                "expected_outputs": [item["title"] for item in alternatives],
                "reason_codes": ["template_not_applicable"],
                "suggested_templates": alternatives,
            }
        )

    accepted = "、".join(run_spec.data_contract.formats) or "由对象结构决定"
    alternative_line = (
        "可选替代模板：" + "、".join(f"{item['title']}（{item['id']}）" for item in alternatives)
        if alternatives
        else "当前没有自动推荐替代模板；请根据真实研究问题重新选择方法。"
    )
    prompt = "\n".join(
        (
            f"请判断当前数据能否可靠用于“{target_label}”，并协助用户选择下一步。",
            f"当前证据：格式={('、'.join(formats) or 'unknown')}；modality={modality}；representation={representation}。",
            f"不能直接使用的原因：{reason}",
            f"目标接受格式：{accepted}；目标关键输入：{'、'.join(run_spec.data_contract.critical_inputs) or '无额外关键输入'}。",
            "若当前数据确实来自目标实验/方法，先核对矩阵方向、对象类别、assay、处理阶段和样本注释，"
            "再给出可在用户本机完成的整理步骤、所需组件和重新导入要求；不得覆盖原始数据。",
            "若当前数据属于普通临床、调查或其他研究表，明确说明它不能被转换成另一种实验数据，"
            "并推荐与当前研究问题匹配的统计模板，不要为了运行目标模板而伪造数据语义。",
            alternative_line,
        )
    )
    return {
        "status": "compatibility_guidance",
        "target": {
            "template_id": run_spec.template_id,
            "workflow_id": run_spec.workflow_id,
            "title": target_label,
            "accepted_formats": list(run_spec.data_contract.formats),
            "critical_inputs": list(run_spec.data_contract.critical_inputs),
            "preconditions": list(
                next((step.checks for step in run_spec.steps if step.phase == "prepare"), ())
            ),
        },
        "dataset": {
            "dataset_id": str(dataset_profile.get("dataset_id") or ""),
            "formats": list(formats),
            "modality": modality,
            "representation": representation,
        },
        "headline": f"当前数据不能直接执行“{target_label}”",
        "summary": str(reason or "当前数据不满足目标模板的数据合同。"),
        "primary_route_id": primary.route_id,
        "actions": actions,
        "issues": [{"code": "template_not_applicable", "message": str(reason or "")}],
        "alternative_templates": alternatives,
        "prompt": prompt,
        "resume": {
            "template_id": run_spec.template_id,
            "conversation_prompt": "",
            "available_after_reinspection": True,
            "requires_user_confirmation": True,
            "preserve_original": True,
        },
    }


def uncovered_runtime_issue_codes() -> tuple[str, ...]:
    """Return coverage drift for the template/readiness audit."""

    return tuple(sorted(RUNTIME_PREFLIGHT_ISSUE_CODES - set(_ISSUE_ROUTES)))
