#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic R script library for curated analysis templates.

A curated template is a known statistical method with a frozen plan. Writing
the R code for it is a deterministic task, so the code lives here as a
reviewed, versioned script instead of being improvised by the LLM on every
execution. The research loop executes the rendered script through the normal
``run_r_code`` machinery (dependency gate, frozen-input accessors, artifact
receipts, portfolio validation) before the model is first consulted; the
model then interprets results and repairs genuine failures.

Scripts must only reach data through ``baa_plan_input()`` accessors and must
save every required portfolio role with its exact role metadata. A template
without an entry here keeps the previous LLM-written behaviour unchanged.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from .models import AnalysisPlan


_RCS_SCRIPT = r'''## Deterministic script: clinical_restricted_cubic_spline
## Restricted cubic spline dose-response analysis (rms).
## Frozen plan: __PLAN_ID__
baa_script_plan_id <- "__PLAN_ID__"

__ACCESSORS__
if (!exists("covars_v")) covars_v <- character(0)
if (!exists("ref_v")) ref_v <- NA_real_
if (!exists("group_v")) group_v <- NULL

.planned_diag <- c("结点选择检查", "非线性检验", "极端区间样本支持检查")
.diag_status <- list()
.step <- "input-binding"
.l <- c(sprintf("deterministic RCS script; plan=%s", baa_script_plan_id))

res <- tryCatch({
  used <- unique(c(outcome_v, exposure_v, covars_v, if (!is.null(group_v)) group_v))
  missing_cols <- used[!used %in% names(dataset)]
  if (length(missing_cols)) stop("missing columns: ", paste(missing_cols, collapse = ", "))

  .step <- "preflight"
  df <- dataset[, used, drop = FALSE]
  if (is.character(df[[outcome_v]])) df[[outcome_v]] <- factor(df[[outcome_v]])
  keep <- stats::complete.cases(df)
  df <- df[keep, , drop = FALSE]
  n_total <- nrow(dataset)
  n_used <- nrow(df)
  .l <- c(.l, sprintf("preflight: n_used=%d n_dropped=%d", n_used, n_total - n_used))
  if (n_used < 20) stop(sprintf("有效分析样本过少 (n=%d)，无法进行样条分析", n_used))

  ov <- df[[outcome_v]]
  if (inherits(ov, "Surv")) {
    family <- "survival"
  } else if (is.factor(ov) || length(unique(ov)) == 2L) {
    family <- "binary"
    df[[outcome_v]] <- factor(ov)
  } else {
    family <- "continuous"
  }
  df[[exposure_v]] <- as.numeric(df[[exposure_v]])
  .l <- c(.l, sprintf("family=%s", family))

  .step <- "datadist"
  dd <- rms::datadist(df)
  ref <- if (!is.na(ref_v)) as.numeric(ref_v) else stats::median(df[[exposure_v]])
  dd$limits["Adjust to", exposure_v] <- ref
  # predictrms resolves the datadist name on the search path, not in this
  # script scope, so publish it globally before any prediction.
  assign("dd", dd, envir = globalenv())
  options(datadist = "dd")

  mkform <- function(kn) {
    rhs <- sprintf(
      "rms::rcs(%s, knots = c(%s))",
      exposure_v, paste(sprintf("%.10g", kn), collapse = ", ")
    )
    if (length(covars_v)) rhs <- paste(rhs, "+", paste(covars_v, collapse = " + "))
    if (!is.null(group_v)) rhs <- paste(rhs, sprintf("factor(%s)", group_v))
    stats::as.formula(paste(outcome_v, "~", rhs))
  }

  .step <- "knot-selection"
  qs <- list(
    c(0.10, 0.50, 0.90),
    c(0.05, 0.35, 0.65, 0.95),
    c(0.05, 0.275, 0.50, 0.725, 0.95)
  )
  kn_set <- lapply(qs, function(p) as.numeric(stats::quantile(df[[exposure_v]], p, names = FALSE)))
  fit_one <- function(kn) {
    f <- mkform(kn)
    switch(family,
      binary = rms::lrm(f, data = df),
      continuous = rms::ols(f, data = df),
      survival = rms::cph(f, data = df)
    )
  }
  fits <- lapply(kn_set, fit_one)
  aics <- vapply(fits, function(f) stats::AIC(f), numeric(1))
  best <- which.min(aics)
  fit <- fits[[best]]
  kn <- kn_set[[best]]
  .l <- c(.l, sprintf(
    "knots: k=%d aic=%s knots=%s",
    best + 2L, paste(sprintf("%.2f", aics), collapse = "/"),
    paste(sprintf("%.4g", kn), collapse = ",")
  ))
  .diag_status[["结点选择检查"]] <- "passed"

  .step <- "nonlinearity-test"
  an <- stats::anova(fit)
  an_rows <- rownames(an)
  nl_row <- an_rows[grepl("Nonlinear", an_rows, fixed = TRUE)][1]
  nl_p <- if (!is.na(nl_row)) an[nl_row, "P"] else NA_real_
  overall_row <- an_rows[trimws(an_rows) == exposure_v][1]
  overall_chi <- if (!is.na(overall_row)) an[overall_row, "Chi-Square"] else NA_real_
  if (is.na(nl_p)) {
    .diag_status[["非线性检验"]] <- "warning"
    .l <- c(.l, "nonlinearity: P not found in anova output")
  } else {
    .diag_status[["非线性检验"]] <- "passed"
    .l <- c(.l, sprintf("nonlinearity: P=%.5g overall_chi=%s", nl_p, format(overall_chi)))
  }

  .step <- "effects-table"
  q_probs <- c(0.05, 0.25, 0.50, 0.75, 0.95)
  x_pts <- as.numeric(stats::quantile(df[[exposure_v]], q_probs, names = FALSE))
  predict_at <- function(x_values) {
    # Programmatic rms::Predict: the exposure name comes from the argument
    # name, ref.zero pins effects to the chosen reference value.
    args <- c(
      list(object = fit),
      setNames(list(x_values), exposure_v),
      list(ref.zero = TRUE)
    )
    pg <- do.call(rms::Predict, args)
    data.frame(
      x = as.numeric(pg[[exposure_v]]),
      yhat = as.numeric(pg$yhat),
      lo = as.numeric(pg$lower),
      hi = as.numeric(pg$upper),
      stringsAsFactors = FALSE
    )
  }
  pr_pts <- predict_at(x_pts)
  eff <- pr_pts$yhat
  scale <- if (family == "binary") "OR" else "beta"
  trans <- if (family == "binary") exp else function(x) x
  fmt_ci <- function(lo, hi) if (is.na(lo) || is.na(hi)) "NA" else sprintf("%.3f, %.3f", trans(lo), trans(hi))

  primary <- data.frame(
    指标 = c(
      "样条结点", "AIC(k=3)", "AIC(k=4)", "AIC(k=5)",
      "总效应卡方", "非线性P",
      sprintf("效应@P5 (%s)", scale), sprintf("效应@P25 (%s)", scale),
      sprintf("效应@P50 (%s)", scale), sprintf("效应@P75 (%s)", scale),
      sprintf("效应@P95 (%s)", scale)
    ),
    数值 = c(
      paste(sprintf("%.4g", kn), collapse = ", "),
      sprintf("%.3f", aics[1]), sprintf("%.3f", aics[2]), sprintf("%.3f", aics[3]),
      if (!is.na(overall_chi)) sprintf("%.3f", overall_chi) else "NA",
      if (!is.na(nl_p)) sprintf("%.5g", nl_p) else "NA",
      sprintf("%.3f [95%%CI %s]", as.numeric(trans(eff[1])), fmt_ci(pr_pts$lo[1], pr_pts$hi[1])),
      sprintf("%.3f [95%%CI %s]", as.numeric(trans(eff[2])), fmt_ci(pr_pts$lo[2], pr_pts$hi[2])),
      sprintf("%.3f [95%%CI %s]", as.numeric(trans(eff[3])), fmt_ci(pr_pts$lo[3], pr_pts$hi[3])),
      sprintf("%.3f [95%%CI %s]", as.numeric(trans(eff[4])), fmt_ci(pr_pts$lo[4], pr_pts$hi[4])),
      sprintf("%.3f [95%%CI %s]", as.numeric(trans(eff[5])), fmt_ci(pr_pts$lo[5], pr_pts$hi[5]))
    ),
    stringsAsFactors = FALSE
  )
  baa_table(
    "primary_table", primary,
    metadata = list(
      role = "primary_table", semantic_key = "primary_table",
      title = "样条效应与非线性检验表", paper_ready = TRUE,
      diagnostic_status = as.list(.diag_status)
    )
  )

  .step <- "dose-response-plot"
  tail_share <- mean(df[[exposure_v]] < x_pts[1] | df[[exposure_v]] > x_pts[length(x_pts)])
  .diag_status[["极端区间样本支持检查"]] <- if (tail_share > 0.05) "warning" else "passed"
  .l <- c(.l, sprintf("tail_share=%.3f", tail_share))
  pg <- predict_at(seq(x_pts[1], x_pts[length(x_pts)], length.out = 200))
  yv <- if (family == "binary") exp(pg$yhat) else pg$yhat
  ylo <- if (family == "binary") exp(pg$lo) else pg$lo
  yhi <- if (family == "binary") exp(pg$hi) else pg$hi
  ylab <- if (family == "binary") "OR (参考值=1)" else "效应 (参考值=0)"
  plot_df <- data.frame(x = pg$x, y = as.numeric(yv), stringsAsFactors = FALSE)
  ribbon_df <- data.frame(x = pg$x, lo = as.numeric(ylo), hi = as.numeric(yhi), stringsAsFactors = FALSE)
  p <- ggplot2::ggplot(plot_df, ggplot2::aes(x = x, y = y)) +
    ggplot2::geom_ribbon(data = ribbon_df, ggplot2::aes(x = x, ymin = lo, ymax = hi), alpha = 0.18, inherit.aes = FALSE, fill = "#4477AA") +
    ggplot2::geom_line(linewidth = 1) +
    ggplot2::geom_hline(yintercept = if (family == "binary") 1 else 0, linetype = 2, colour = "grey40") +
    ggplot2::geom_vline(xintercept = ref, linetype = 3, colour = "grey40") +
    ggplot2::labs(
      x = exposure_v, y = ylab,
      title = paste0("限制性立方样条剂量反应曲线 (", paste(sprintf("%.4g", kn), collapse = ", "), " 结点)"),
      caption = sprintf("非线性 P=%s", if (!is.na(nl_p)) sprintf("%.5g", nl_p) else "NA")
    ) +
    ggplot2::theme_bw()
  baa_plot(
    "diagnostic_plot", p,
    metadata = list(
      role = "diagnostic_plot", semantic_key = "diagnostic_plot",
      title = "剂量反应曲线", paper_ready = TRUE,
      diagnostic_status = as.list(.diag_status)
    )
  )

  .step <- "manuscript-text"
  direction <- if (is.na(nl_p)) {
    "因非线性检验未获得数值，方向解读受限"
  } else if (nl_p < 0.05) {
    "存在统计学意义的非线性关系"
  } else {
    "未发现统计学意义的非线性，暴露与结局的关系近似线性"
  }
  txt <- paste0(
    sprintf("限制性立方样条分析（n=%d，%s 模型）。", n_used, family),
    sprintf("暴露变量 %s，参考值取 %s；按 AIC 选择 %d 结点方案（%s）。",
      exposure_v, sprintf("%.4g", ref), best + 2L, paste(sprintf("%.4g", kn), collapse = ", ")),
    sprintf("非线性检验 P=%s，%s。", if (!is.na(nl_p)) sprintf("%.5g", nl_p) else "NA", direction),
    sprintf("总效应卡方=%s。样条效应表与剂量反应曲线见右侧结果面板；曲线在 %s 至 %s 范围内绘制，超出该范围的极端取值未外推。",
      if (!is.na(overall_chi)) sprintf("%.2f", overall_chi) else "NA",
      sprintf("%.4g", x_pts[1]), sprintf("%.4g", x_pts[length(x_pts)])
    )
  )
  baa_text(
    "manuscript_results", txt,
    metadata = list(
      role = "manuscript_results", semantic_key = "manuscript_results",
      title = "样条模型论文结果", paper_ready = TRUE,
      diagnostic_status = as.list(.diag_status)
    )
  )

  .l <- c(.l, "final: OK")
  "OK"
}, error = function(e) {
  for (nm in .planned_diag) if (is.null(.diag_status[[nm]])) .diag_status[[nm]] <<- "failed"
  paste("ABORT at step:", .step, "|", conditionMessage(e))
})
if (!identical(res, "OK")) {
  baa_text(
    "manuscript_results",
    paste0("确定性模板脚本未能完成：", res),
    metadata = list(
      role = "manuscript_results", semantic_key = "manuscript_results",
      title = "样条模型论文结果", paper_ready = FALSE,
      diagnostic_status = as.list(.diag_status)
    )
  )
}
invisible(baa_text(
  "rcs_run_diagnostic", paste(.l, collapse = "\n"),
  metadata = list(role = "diagnostic_note", semantic_key = "diagnostic_note", paper_ready = FALSE)
))
if (!identical(res, "OK")) stop(res, call. = FALSE)
'''

_RCS_INPUT_VARS = {
    "outcome": "outcome_v",
    "exposure": "exposure_v",
    "covariates": "covars_v",
    "reference_value": "ref_v",
    "group": "group_v",
}


def _render_rcs_skeleton(plan: AnalysisPlan) -> Optional[str]:
    accessor_lines = []
    for input_id, var in _RCS_INPUT_VARS.items():
        if input_id in plan.resolved_inputs:
            accessor_lines.append(f'{var} <- baa_plan_input("{input_id}")')
    return (
        _RCS_SCRIPT.replace("__PLAN_ID__", plan.plan_id)
        .replace("__ACCESSORS__", "\n".join(accessor_lines))
    )


# ── Skeleton registry ────────────────────────────────────────────────────────
#
# Each wiring entry selects an explicitly implemented statistical core and
# maps portfolio roles to its actual computed outputs. Method modules share
# input and artifact helpers while retaining their own algorithms and tests.
# Only exact template IDs are registered; family similarity never authorizes
# a different algorithm. Unwired methods retain the validated model-code path.

from .skeleton_cores import ENGINES as _CORE_ENGINES, FIXED_METHOD_MODULES  # noqa: E402

# template_id -> (engine name, role map: portfolio role -> engine slot)
_WIRING: dict[str, tuple] = {
    "clinical_restricted_cubic_spline": ("rcs_dose_response", {}),
    "publication_table": ("descriptive_reporting", dict(
        baseline_table="t1",
        table_2="cmp",
        balance_table="bal",
        table_4="eff",
        table_5="exp",
        baseline_table_2="t1_fmt",
        text_1="report_note",
        method_note="method_note",
    )),
    "group_tests": ("classical_group_tests", dict(
        method_note="methods_table",
        table_2="p_table",
        table_3="eff_table",
        table_4="adj_table",
        table_5="cmp_table",
        table_6="eff_ci_table",
        figure_1="diff_figure",
        method_note_2="method_text",
        effect_estimate_table="eff_ci_table",
    )),
    "regression_glm": ("glm_regression", dict(
        table_1="model_table",
        table_2="marginal",
        table_3="robust_ci",
        table_4="uni_multi",
        table_5="eff_strings",
        diagnostic_output="diag_figure",
        diagnostic_output_2="diag_table",
        manuscript_results="results_text",
        effect_estimate_table="model_table",
    )),
    "survival_km_cox": ("survival_km_cox", dict(
        survival_plot="km_plot",
        table_1="uni_cox",
        table_2="ph_table",
        table_3="risk_table",
        survival_plot_2="km_plot2",
        table_4="multi_cox",
        diagnostic_output="ph_diag_table",
        survival_effect_estimates="multi_cox",
        survival_curve="km_plot",
        survival_model_diagnostics="ph_diag_table",
        scientific_result_interpretation="results_text",
    )),
    "missing_imputation": ("missing_imputation", dict(missingness_summary="missing_fig",diagnostic_output="diagnostics",table_2="pooled",sensitivity_output="notes",text_2="notes",table_3="variance",sensitivity_output_2="comparison")),
    "multiple_imputation_pooling": ("multiple_imputation_pooling", dict(primary_table="pooled",diagnostic_plot="trace_fig",manuscript_results="notes")),
    "trial_simon_two_stage": ("trial_simon_two_stage", dict(primary_table="primary",diagnostic_plot="figure",manuscript_results="notes")),
    "clinical_target_trial_emulation": ("clinical_target_trial_emulation", dict(primary_table="primary",diagnostic_plot="figure",manuscript_results="notes")),
    "staggered_did_panel": ("staggered_did_panel", dict(figure_1="figure",table_1="att",table_2="sensitivity",method_note="notes",manuscript_results="notes",diagnostic_output="notes")),
    "diagnostic_roc": ("diagnostic_roc", dict(
        roc_output="roc_curve",
        table_1="threshold_table",
        table_2="confusion",
        manuscript_results="results_text",
        roc_output_2="roc_strata",
        table_3="auc_table",
        table_4="youden_table",
        diagnostic_output="diag_text",
        prediction_performance_table="performance_table",
        prediction_discrimination_plot="roc_curve",
    )),
    "correlation_agreement": ("correlation_agreement", dict(
        table_1="corr_matrix",
        table_2="agreement_stats",
        figure_1="scatter_fig",
        table_3="agreement_table",
        figure_2="ba_fig",
        manuscript_results="results_text",
    )),
    "effect_size_precision": ("effect_size_precision", dict(
        table_1="eff_table",
        table_2="precision_table",
        manuscript_results="results_text",
        method_note="method_note",
        manuscript_results_2="results_paper",
    )),
    "ancova_adjusted_groups": ("ancova_adjusted_groups", dict(
        primary_table="adjusted_table",
        diagnostic_plot="diag_fig",
        manuscript_results="results_text",
    )),
    "bootstrap_permutation_inference": ("bootstrap_permutation_inference", dict(
        primary_table="boot_table",
        diagnostic_plot="boot_hist",
        manuscript_results="results_text",
    )),
    "propensity_clinical": ("propensity_clinical", dict(
        balance_table="balance_df",
        figure_1="overlap_fig",
        table_2="weighted_eff",
        sensitivity_output="sens_text",
        table_3="effect_table",
    )),
    "mediation_moderation": ("mediation_moderation", dict(
        table_1="paths_table",
        figure_1="coef_fig",
        table_2="decomp_table",
        method_note="method_text",
        manuscript_results="results_text",
        diagnostic_output="diag_text",
    )),
    "categorical_ordinal_models": ("categorical_ordinal_models", dict(
        table_1="model_summary",
        table_2="pred_probs",
        table_3="assumptions",
        table_4="coef_table",
        table_5="marginal",
        diagnostic_output="sparsity_table",
        manuscript_results="results_text",
    )),
    "mixed_repeated": ("mixed_repeated", dict(
        table_1="time_effect",
        table_2="random_eff",
        table_3="marginal_means",
        diagnostic_output="profile_fig",
        text_1="random_text",
        figure_2="trend_fig",
        diagnostic_output_2="model_diag",
    )),
    "noninferiority_equivalence": ("noninferiority_equivalence", dict(
        table_1="ni_table",
        figure_1="ci_fig",
        text_1="results_text",
        text_2="margin_text",
        table_2="paper_table",
    )),
    "pairwise_meta": ("pairwise_meta", dict(
        forest_plot="forest_fig",
        table_1="pooled_table",
        table_2="bias_table",
        table_3="het_table",
        sensitivity_output="loo_table",
    )),
    "subgroup_sensitivity_basic": ("subgroup_sensitivity_basic", dict(
        forest_plot="forest_fig",
        sensitivity_output="sens_table",
        manuscript_results="results_text",
        method_note="method_note",
        manuscript_results_2="paper_text",
        diagnostic_output="diag_text",
    )),
    "epi_measures": ("epi_measures", dict(
        table_1="meas_table",
        table_2="ci_table",
        manuscript_results="results_text",
        table_3="epi_table",
        figure_1="rate_fig",
        manuscript_results_2="pub_text",
    )),
    "interrater_agreement": ("interrater_agreement", dict(
        table_1="kappa_table",
        figure_1="rater_fig",
        analysis_recommendation="rec_text",
        method_note="method_text",
        manuscript_results="results_text",
        diagnostic_output="diag_text",
    )),
    "scale_reliability": ("scale_reliability", dict(
        table_1="rel_table",
        table_2="it_table",
        analysis_recommendation="drop_text",
        table_3="corr_table",
        table_4="del_table",
        manuscript_results="results_text",
    )),
    "forecasting": ("forecasting", dict(
        table_1="trend_table",
        figure_1="fc_fig",
        table_2="err_table",
        table_3="fc_table",
    )),
    "sample_size_power": ("sample_size_power", dict(
        table_1="ss_table",
        table_2="para_table",
        sensitivity_output="sens_table",
        table_4="para_table_2",
        text_1="plan_text",
    )),
    "efa_factor": ("efa_factor", dict(
        table_1="loadings",
        figure_1="scree_fig",
        manuscript_results="results_text",
        table_2="scores_table",
        survival_plot="adequacy_table",
        table_4="loadings_table",
    )),
    "cfa_sem": ("cfa_sem", dict(
        figure_1="path_fig",
        table_1="load_table",
        table_2="fit_table",
        table_3="struct_table",
        table_4="syntax_table",
        table_5="load_path_table",
        figure_2="path_note_text",
    )),
    "did_policy": ("did_policy", dict(
        table_1="did_table",
        figure_1="es_fig",
        diagnostic_output="parallel_df",
        table_3="did_model",
        text_1="robust_text",
    )),
    "iv_panel_public_health": ("iv_panel_public_health", dict(
        table_1="iv_table",
        table_2="fe_table",
        table_3="weak_table",
        table_4="robust_table",
        table_5="fe_iv_table",
        table_6="robust_cmp",
        text_1="ident_text",
    )),
    "tabular_ml_workflow": ("tabular_ml_workflow", dict(
        table_1="model_cmp", figure_1="roc_fig", table_2="test_perf", table_3="pred_table",
        table_4="cv_table", table_5="best_table", roc_output="best_table", figure_2="roc_fig",
        table_7="model_cmp", table_8="model_cmp", table_9="cv_table", table_10="cv_table",
        roc_output_2="roc_fig", text_1="leak_text", table_11="model_cmp", table_12="best_table",
        roc_output_3="roc_fig", table_13="overfit_table", figure_5="roc_fig", text_2="reuse_text",
    )),
    "evidence_meta_regression": ("evidence_meta_regression", dict(
        primary_table="reg_table",
        diagnostic_plot="bubble_fig",
        manuscript_results="results_text",
    )),
    "interrupted_time_series": ("interrupted_time_series", dict(
        figure_1="its_fig", table_1="its_table", table_2="slope_table",
        table_3="cp_table", figure_2="trend_fig", table_4="slope_table", text_1="its_text",
    )),
    "survey_weighted": ("survey_weighted", dict(
        table_1="est_table",
        table_2="est_table",
        table_3="design_table",
        table_4="design_table",
        text_1="results_text",
        manuscript_results="results_text",
    )),
    "cluster_rct_analysis": ("cluster_rct_analysis", dict(
        table_1="model_table",
        table_2="icc_table",
        text_1="results_text",
        method_note="results_text",
        manuscript_results="results_text",
        diagnostic_output="icc_text",
    )),
    "public_cost_effectiveness_psa": ("public_cost_effectiveness_psa", dict(
        primary_table="cea_table",
        diagnostic_plot="ce_fig",
        manuscript_results="results_text",
    )),
    "evidence_proportion_rate_meta": ("evidence_proportion_rate_meta", dict(
        primary_table="pool_table",
        diagnostic_plot="forest_fig",
        manuscript_results="results_text",
    )),
    "public_screening_evaluation": ("public_screening_evaluation", dict(
        primary_table="scr_table",
        diagnostic_plot="scr_fig",
        manuscript_results="results_text",
    )),
    "public_vaccine_test_negative": ("public_vaccine_test_negative", dict(
        primary_table="ve_table",
        diagnostic_plot="cmp_fig",
        manuscript_results="results_text",
    )),
    "trial_multiplicity_gatekeeping": ("trial_multiplicity_gatekeeping", dict(
        primary_table="mult_table",
        diagnostic_plot="summary_fig",
        manuscript_results="results_text",
    )),
    "health_inequality": ("health_inequality", dict(
        table_1="ineq_table",
        figure_1="group_fig",
        manuscript_results="results_text",
        method_note="method_note",
        manuscript_results_2="paper_text",
        diagnostic_output="diag_text",
    )),
    "clinical_rmst_non_ph": ("clinical_rmst_non_ph", dict(
        primary_table="rmst_table", diagnostic_plot="rmst_fig", manuscript_results="results_text")),
    "clinical_recurrent_events": ("clinical_recurrent_events", dict(
        primary_table="rec_table", diagnostic_plot="rec_fig", manuscript_results="results_text")),
    "time_dependent_roc": ("time_dependent_roc", dict(
        roc_output="roc_fig", table_1="auc_table", table_2="acc_table", table_3="trend_table",
        roc_output_2="roc2_fig", table_4="auc_table", manuscript_results="results_text")),
    "clinical_cutpoint_maxstat": ("clinical_cutpoint_maxstat", dict(
        table_1="cut_table", figure_1="cut_fig", text_1="cut_text", method_note="method_note",
        manuscript_results="results_paper", diagnostic_output="diag_text")),
    "clinical_subgroup_interaction": ("clinical_subgroup_interaction", dict(
        forest_plot="forest_fig", table_1="sens_table", manuscript_results="results_text",
        method_note="method_note", manuscript_results_2="paper_text", diagnostic_output="diag_text")),
    "extended_survival": ("extended_survival", dict(
        table_1="model_table", figure_1="hr_fig", table_2="ph_table", table_3="cmp_table", text_1="results_text")),
    "competing_risk": ("competing_risk", dict(
        figure_1="cif_fig", table_1="cs_table", manuscript_results="results_text",
        figure_2="cs_fig", table_2="comp_df", text_2="results_note", manuscript_results_2="results_note")),
    "prognostic_model": ("prognostic_model", dict(
        table_1="perf_table", figure_1="cal_fig", calibration_output="cal_fig", table_2="coef_table",
        table_3="score_preview", roc_output="auc_table", figure_3="scatter_fig")),
    "robust_outlier_analysis": ("robust_outlier_analysis", dict(
        text_1="text_1", table_1="out_table", sensitivity_output="sens_table",
        method_note="method_note", manuscript_results="results_text")),
    "data_quality": ("data_quality", dict(
        variable_dictionary="rec_text", missingness_summary="miss_table",
        missingness_summary_2="miss_table",
        outlier_summary="rec_text", analysis_recommendation="rec_text", analysis_recommendation_2="rec_text")),
    "data_import_workflow": ("data_import_workflow", dict(
        table_1="profile_table", table_2="miss_table", analysis_recommendation="rec_text",
        method_note="method_note", manuscript_results="results_text")),
    "data_cleaning_recoding": ("data_cleaning_recoding", dict(
        table_1="profile_table", table_2="miss_table", table_3="rec_text",
        method_note="method_note", manuscript_results="results_text")),
    "automated_eda_report": ("automated_eda_report", dict(
        text_1="rec_text", table_1="profile_table", figure_1="rec_text",
        method_note="method_note", manuscript_results="results_text")),
    "large_data_engine": ("large_data_engine", dict(
        table_1="profile_table", table_2="rec_text", analysis_recommendation="rec_text",
        table_3="profile_table", table_4="miss_table", table_5="miss_table",
        analysis_recommendation_2="rec_text")),
    "age_standardization": ("age_standardization", dict(
        table_1="std_table", figure_1="rate_fig", manuscript_results="results_text",
        method_note="method_note", manuscript_results_2="paper_text", diagnostic_output="diag_text")),
    "attributable_fraction": ("attributable_fraction", dict(
        table_1="af_table", table_2="cmp_table", manuscript_results="results_text",
        method_note="method_note", manuscript_results_2="paper_text", diagnostic_output="diag_text")),
    "negative_control_sensitivity": ("negative_control_sensitivity", dict(
        sensitivity_output="sens_table", table_2="cmp_table", manuscript_results="results_text",
        method_note="method_note", manuscript_results_2="paper_text", diagnostic_output="diag_text")),
    "clinical_doubly_robust_effect": ("clinical_doubly_robust_effect", dict(
        primary_table="primary_table", diagnostic_plot="diag_fig", manuscript_results="results_text")),
    "missing_visual_diagnostics": ("missing_visual_diagnostics", dict(
        missingness_summary="miss_fig", missingness_summary_2="miss_table",
        missingness_summary_3="pattern_text", method_note="method_note", manuscript_results="results_text")),
    "group_sequential": ("group_sequential", dict(
        figure_1="gs_fig", table_1="gs_table", manuscript_results="results_text",
        table_2="bound_df", text_2="results_note")),
}

for _module in FIXED_METHOD_MODULES:
    for _template_id, _role_map in _module.ROLE_MAPS.items():
        _WIRING[_template_id] = (_template_id, _role_map)

_SKELETONS: Dict[str, Callable] = {
    "rcs_dose_response": lambda plan, rm: _render_rcs_skeleton(plan),
    **{
        name: (lambda plan, rm, _eng=render: _eng(plan, rm))
        for name, render in _CORE_ENGINES.items()
    },
}


# ── Family pairing candidates (NOT wired) ─────────────────────────────────
# Fallback contract (docs/deterministic-templates.md): a template without a verified
# dedicated wiring keeps the LLM-written code path — full validation pipeline,
# no deterministic guarantee.  Auto-attaching unverified templates to their
# family's engine was tried and removed: the identity role maps either ran the
# wrong method (e.g. psych_network → Table 1), aborted on missing engine input
# variables, or duplicated outputs across roles so portfolio validation passed
# dishonestly.  This map is kept purely as the *candidate* pairing reference
# for the future wiring work; an entry here does NOT make a template scripted.
# Wiring a template for real requires: role map review + dedicated fixture +
# real-R verification (see _ENGINE_PLANS in tests/science/test_script_library.py).

_FAMILY_ENGINE_CANDIDATES = {
    "publication_tables_and_reporting": "descriptive_reporting",
    "classical_statistics_and_mixed_models": "classical_group_tests",
    "classical_statistics": "classical_group_tests",
    "clinical_prediction_and_diagnostic_performance": "diagnostic_roc",
    "causal_inference_and_real_world_evidence": "propensity_clinical",
    "causal_inference": "propensity_clinical",
    "machine_learning_and_explainable_prediction": "glm_regression",
    "tabular_machine_learning": "glm_regression",
    "time_series_longitudinal_trends_signal_processing": "forecasting",
    "spatial_environmental_geographic_analysis": "descriptive_reporting",
    "text_mining_and_network_analysis": "descriptive_reporting",
    "bulk_omics_and_differential_expression": "descriptive_reporting",
    "pathway_enrichment_annotation": "descriptive_reporting",
    "single_cell_and_cell_annotation": "descriptive_reporting",
    "microbiome_proteomics_multi_omics_medical_imaging": "descriptive_reporting",
    "data_import_cleaning_profiling": "descriptive_reporting",
    "survey_weighted_analysis": "descriptive_reporting",
    "epidemiology_and_public_health": "descriptive_reporting",
    "clinical_trials_power_meta_analysis": "descriptive_reporting",
    "psychology_questionnaires_social_science": "descriptive_reporting",
    "evidence_synthesis": "descriptive_reporting",
    "clinical_trial_estimands": "descriptive_reporting",
    "trial_missing_data": "descriptive_reporting",
    "dose_finding": "descriptive_reporting",
    "phase2_design": "descriptive_reporting",
    "adaptive_trial_design": "descriptive_reporting",
    "adaptive_randomization": "descriptive_reporting",
    "multiple_testing": "descriptive_reporting",
    "psychometrics": "descriptive_reporting",
    "longitudinal_sem": "descriptive_reporting",
    "dyadic_analysis": "descriptive_reporting",
    "multilevel_sem": "descriptive_reporting",
    "measurement_bias": "descriptive_reporting",
    "latent_state_models": "descriptive_reporting",
    "survey_response_quality": "descriptive_reporting",
    "paired_comparison": "classical_group_tests",
    "adjusted_group_comparison": "glm_regression",
    "categorical_association": "classical_group_tests",
    "resampling_inference": "bootstrap_permutation_inference",
    "nonlinear_regression": "clinical_rmst_non_ph",
    "missing_data": "descriptive_reporting",
    "missing_data_and_imputation": "descriptive_reporting",
    "survival_analysis": "survival_km_cox",
    "multistate_survival": "descriptive_reporting",
    "clinical_regression": "glm_regression",
    "dynamic_prediction": "descriptive_reporting",
    "joint_longitudinal_survival": "descriptive_reporting",
    "infectious_disease_surveillance": "descriptive_reporting",
    "population_trend_analysis": "descriptive_reporting",
    "vaccine_effectiveness": "descriptive_reporting",
    "screening_evaluation": "descriptive_reporting",
    "burden_decomposition": "descriptive_reporting",
    "health_economics": "descriptive_reporting",
    "model_validation": "descriptive_reporting",
    "model_transportability": "descriptive_reporting",
    "algorithmic_fairness": "descriptive_reporting",
    "prediction_uncertainty": "descriptive_reporting",
    "structured_classification": "descriptive_reporting",
    "heterogeneous_treatment_effects": "descriptive_reporting",
    "clinical_prediction": "descriptive_reporting",
    "multivariate_time_series": "descriptive_reporting",
    "hierarchical_forecasting": "descriptive_reporting",
    "time_series_anomaly_detection": "descriptive_reporting",
    "spatial_point_process": "descriptive_reporting",
    "spatial_interpolation": "descriptive_reporting",
    "sentiment_analysis": "descriptive_reporting",
    "text_classification": "descriptive_reporting",
    "information_extraction": "descriptive_reporting",
    "topic_modeling": "descriptive_reporting",
    "genetic_association": "descriptive_reporting",
    "variant_analysis": "descriptive_reporting",
    "single_cell_trajectory": "descriptive_reporting",
    "single_cell_composition": "descriptive_reporting",
    "spatial_omics": "descriptive_reporting",
    "epigenomic_binding": "descriptive_reporting",
    "alternative_splicing": "descriptive_reporting",
    "molecular_qtl": "descriptive_reporting",
    "cancer_genomics": "descriptive_reporting",
    "microbiome_compositional": "descriptive_reporting",
    "metabolomics": "descriptive_reporting",
    "environment_mixture": "descriptive_reporting",
}

def wired_template_ids() -> tuple[str, ...]:
    return tuple(sorted(_WIRING))


_WIRING["tabular_ml_workflow"][1]["ml_resampling_metrics"] = "cv_table"


# Backwards-compatible alias: the set of templates with deterministic code.
SCRIPTED_TEMPLATE_IDS = wired_template_ids()


def scripted_template_ids() -> tuple[str, ...]:
    return wired_template_ids()


def has_script(plan: Optional[AnalysisPlan]) -> bool:
    return bool(
        plan is not None
        and plan.source == "template"
        and plan.template_id in _WIRING
    )


def render_script(plan: AnalysisPlan) -> Optional[str]:
    """Return the deterministic R source for this plan, or None.

    Only inputs actually frozen in the plan are accessed through
    ``baa_plan_input``; the remaining optional inputs fall back to inert
    defaults inside the script.
    """
    entry = _WIRING.get(plan.template_id)
    if not entry:
        return None
    engine_name, role_map = entry
    skeleton = _SKELETONS.get(engine_name)
    if skeleton is None:
        return None
    return skeleton(plan, role_map)
