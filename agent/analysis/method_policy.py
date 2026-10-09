"""User-visible method evidence; execution receipts are not scientific review."""

from __future__ import annotations

WITHDRAWN_FIXED_METHODS: dict[str, str] = {}


REFERENCE_CASES = {
    "publication_table": "已选连续变量不被低基数自动重分类；缺失与总体/分组描述参考用例。",
    "group_tests": "Student/Welch 参数与报告方法一致；Pearson 无连续性校正、Cramér V 和预期频数稀疏检查。",
    "regression_glm": "连续终点系数的独立闭式参考与小样本 t 区间；0/1 二分类 OR 的解析参考；秩不足拒绝。",
    "survival_km_cox": "0/1 右删失数据的 KM 风险集与生存概率；可选协变量缺省；错误事件编码拒绝。",
    "epi_measures": "明确事件类别和参照组的 RR/OR/风险差手算参考；零单元格拒绝使用 Wald 区间。",
    "missing_imputation": "MICE 随机插补与线性模型 Rubin 方差合并；MAR 与收敛需人工复核。",
    "multiple_imputation_pooling": "MICE 随机插补与线性模型 Rubin 方差合并；MAR 与收敛需人工复核。",
    "trial_simon_two_stage": "p0=.2、p1=.4、alpha=.05、power=.8 的 optimal/minimax 及精确操作特征。",
    "staggered_did_panel": "平衡、吸收处理面板的 group-time ATT；合成常数效应参考用例。",
    "clinical_target_trial_emulation": "已确认基线策略、固定时窗二分类终点的 IPTW 风险差与重拟合 bootstrap。",
    "group_sequential": "单侧、等分配连续终点；信息时间、Lan–DeMets 花费和总样本量。",
    "diagnostic_roc": "重复评分阈值与类别不平衡 PR 精确率；阳性类别和方向需确认。",
    "sample_size_power": "独立两组连续终点正态近似；对照/试验分配比。",
    "noninferiority_equivalence": "独立两组连续结局的 Welch 区间、双方向非劣效、等效及分析集筛选。",
    "mixed_repeated": "重复测量随机截距混合模型与交换型工作相关 GEE 的参考拟合。",
    "tabular_ml_workflow": "训练集交叉验证选模；改变测试集结局不改变选择分数与所选模型。",
}

# Module reference declarations are populated only after their real-R cases
# pass; keep the catalog display tied to the same registered implementations.
from .skeleton_cores import FIXED_METHOD_MODULES

for _module in FIXED_METHOD_MODULES:
    REFERENCE_CASES.update(_module.REFERENCE_CASES)

METHOD_SUPPORT_SCOPES = {
    "publication_table": "独立观测的总体或分组基线描述；变量类型以确认后的角色为准。",
    "group_tests": "独立组的连续/分类比较及 BH 校正；不支持配对或重复测量。",
    "regression_glm": "独立观测的连续高斯线性或二分类 logistic 主效应模型；不支持自动交互/分层或复杂抽样。",
    "survival_km_cox": "非负数值随访时长、0=删失/1=事件、右删失 KM 与固定协变量 Cox；不支持竞争风险或时间变化协变量。",
    "epi_measures": "个体行的二元结局×二元暴露；显式确认方向、非零单元格的 Wald 区间；不支持聚合分母、人时率或分层。",
}

SUPPORTED_DESIGNS = {
    "sample_size_power": "independent_normal_means",
    "group_sequential": "one_sided_normal_equal",
    "clinical_target_trial_emulation": "baseline_binary_ate",
    "staggered_did_panel": "absorbing_balanced_panel",
    "missing_imputation": "mar_linear_model",
    "multiple_imputation_pooling": "mar_linear_model",
}


def method_scope_issues(template_id: str, values: dict) -> dict[str, str]:
    if template_id == "mediation_moderation" and values.get("analysis_method") == "moderated_mediation":
        return {key: "调节中介分析需要同时选择中介与调节变量。" for key in ("mediator", "moderator") if not values.get(key)}
    if template_id == "extended_survival" and values.get("analysis_method") == "counting_process":
        return {key: "计数过程 Cox 需要区间起始时间与受试者 ID。" for key in ("start_time", "subject_id") if not values.get(key)}
    unsupported = {
        "group_tests": {"paired_marker": "当前固定组间比较仅支持独立观测；配对/重复测量请使用相应分析入口。"},
        "regression_glm": {"strata": "当前固定回归不支持交互/分层；请使用相应分析入口。"},
        "epi_measures": {
            "denominator": "当前固定风险指标仅支持个体行计数；聚合分母/人时率需另行分析。",
            "time_region": "当前固定风险指标不支持时间/地区分层；请使用相应分析入口。",
        },
    }.get(template_id, {})
    issues = {key: message for key, message in unsupported.items() if values.get(key)}
    if template_id == "epi_measures":
        issues.update({key: "必须确认事件类别与参照暴露组，不能依赖类别排序。" for key in ("positive_class", "reference_group") if values.get(key) in (None, "")})
    if issues:
        return issues
    expected = SUPPORTED_DESIGNS.get(template_id)
    if expected and values.get("design_scope") != expected:
        return {"design_scope": f"请明确确认支持的研究设计 {expected}；其他设计请使用自由分析，不能套用本固定方法。"}
    if template_id == "clinical_target_trial_emulation":
        return {key: message for key, message in {
            "covariates": "当前固定实现不支持时间变化协变量；请改选基线协变量或使用自由分析。",
            "event": "当前固定实现不支持删失终点；有失访/删失时需另行制定估计方案。",
        }.items() if values.get(key)}
    if template_id == "diagnostic_roc":
        return {
            key: message for key, message in {
                "positive_class": "请明确选择阳性类别，不能依赖字符排序。",
                "score_direction": "请明确评分方向：higher_positive 或 lower_positive。",
            }.items() if values.get(key) in (None, "")
        } | ({"score_direction": "评分方向不受支持。"} if values.get("score_direction") not in {None, "", "higher_positive", "lower_positive"} else {})
    return {}


def template_method_status(template_id: str, *, assumptions: tuple[str, ...] = (), input_shape: str = "") -> dict[str, str]:
    from .script_library import scripted_template_ids

    fixed = template_id in scripted_template_ids()
    reference = REFERENCE_CASES.get(template_id, "") if fixed else ""
    support_scope = METHOD_SUPPORT_SCOPES.get(template_id) or (
        ("结构化表格数据" if input_shape == "tabular" else "声明的科学数据对象" if input_shape == "r_object" else "冻结方案的数据与方法")
        + ("；需核对：" + "；".join(assumptions) if assumptions else "；需核对研究设计和方法适用条件")
    )
    return {
        "execution_mode": "fixed_script" if fixed else "llm_generated",
        "verification_status": "reference_cases" if reference else "method_review_pending",
        "label": "部分参考用例已验证" if reference else "实现验证待补" if fixed else "模型生成，实现验证待补",
        "scope": (reference + " 支持范围：" + support_scope) if reference else support_scope + "。尚无本实现的数值参考验证记录。",
        "support_tier": "reference_checked" if reference else "fixed_review_pending" if fixed else "generated_review_pending",
        "note": WITHDRAWN_FIXED_METHODS.get(template_id, "") or (
            "使用固定脚本；每项研究仍需核对适用条件、诊断与结论。" if fixed else
            "由模型生成分析代码；运行结果与方法适用性均需复核。"
        ),
        "scientific_review_status": "not_reviewed",
        "supported_design": SUPPORTED_DESIGNS.get(template_id, support_scope),
    }
