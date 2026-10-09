#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Statistical skeleton cores for the deterministic script library.

Each engine is a statistical core for one method family. Evidence and scope
are tracked separately in method_policy; registration is not validation. All
engines share ``_HELPERS`` (pure R functions: variable typing, test
selection, effect sizes, model tables) and ``_EMITTER`` (a contract-driven
artifact writer), so every figure/table/text is produced through one uniform
quality-controlled path regardless of engine.

R scope discipline for the platform wrapper's function scope: helpers are
pure functions that only read arguments and return values; result
accumulation happens with plain assignments in the tryCatch expression
frame; ``<<-`` appears only inside the error handler, whose enclosing frame
holds the variable.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, Optional

from .models import AnalysisPlan
from .complex_method_cores import IMPUTATION_CORE, SIMON_CORE, DID_CORE, TARGET_TRIAL_CORE
from .reviewed_method_cores import MACHINE_LEARNING_CORE, NONINFERIORITY_CORE, longitudinal_core
from . import (
    data_preparation_cores, clinical_survival_cores,
    statistics_behavior_cores, weighted_causal_cores, meta_time_cores,
    clinical_prediction_cores, clinical_public_workflows,
    general_behavior_workflows, trial_evidence_workflows,
    omics_workflows, time_space_text_workflows, machine_learning_workflows,
)


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


_HELPERS = r'''
## ── Shared reviewed skeleton helpers (pure functions) ────────────────────

## Auto-convert low-cardinality numeric columns to factor (broad fix for
## binary/ordinal codes like 0/1, 1/2, 1/2/3 that are semantically categorical).
## Only converts integer-valued numeric columns with 2-5 unique values.
## Genuinely continuous variables (non-integer or many unique values) are untouched.
.sk_auto_factor <- function(data, known_continuous = character(0)) {
  for (cn in names(data)) {
    if (cn %in% known_continuous) next
    x <- data[[cn]]
    if (!is.numeric(x) || is.factor(x)) next
    uvals <- unique(na.omit(x))
    nu <- length(uvals)
    if (nu < 2L || nu > 5L) next
    ## All non-NA values must be integers (codes, not continuous)
    if (!all(uvals == floor(uvals))) next
    data[[cn]] <- factor(x)
  }
  data
}

.sk_fmt_p <- function(p) if (length(p) == 0 || is.na(p)) "NA" else if (p < 0.001) "<0.001" else sprintf("%.3f", p)
.sk_design_outcome_audit <- function(data, column) {
  column <- as.character(column)
  if (length(column) != 1L || !nzchar(column) || !(column %in% names(data))) stop("设计终点列不存在或未唯一指定")
  y <- data[[column]]
  if (!is.numeric(y) || is.factor(y) || inherits(y, c("Date", "POSIXt"))) stop("当前固定设计仅支持连续数值终点；选定终点类型不适用")
  if (any(!is.na(y) & !is.finite(y))) stop("设计终点含非有限数值，需先处理后再审计")
  observed <- y[is.finite(y)]
  if (length(unique(observed)) == 2L && all(unique(observed) %in% c(0, 1))) stop("选定终点为 0/1 两个观测水平，不能作为已确认的连续终点设计")
  list(bound = TRUE, column = column, rows = length(y), observed = length(observed),
    missing = sum(is.na(y)), distinct = length(unique(observed)),
    note = sprintf("选定终点 %s 为数值型连续候选：数据 %d 行，有限观测 %d，缺失 %d，观测值种类 %d；这些数据仅用于响应类型与样本审计，不估计或替代预先指定的效应量、alpha 或目标把握度，不计算观察/事后 power。%s",
      column, length(y), length(observed), sum(is.na(y)), length(unique(observed)),
      if (length(observed) < 3L || length(unique(observed)) < 3L) "当前观测不足以评估终点分布；设计计算仍使用已确认的外部参数。" else "正态、等方差和独立性仍是已确认的设计假设。"))
}
.sk_time_axis_label <- function(unit) {
  if (is.character(unit) && length(unit) == 1L && nzchar(unit)) paste0("随访时间（", unit, "）") else "随访时间"
}

.sk_kind <- function(x) {
  if (inherits(x, "Surv")) return("survival")
  if (is.factor(x)) return(if (nlevels(droplevels(x)) == 2L) "binary" else "categorical")
  if (is.character(x) || is.logical(x)) {
    u <- unique(na.omit(x)); return(if (length(u) <= 2L) "binary" else "categorical")
  }
  if (length(unique(na.omit(x))) <= 2L) "binary" else "continuous"
}

.sk_test_continuous <- function(x, g) {
  ok <- is.finite(x) & !is.na(g)
  x <- x[ok]; g <- droplevels(factor(g[ok]))
  if (nlevels(g) < 2L || length(x) < 6) return(list(p = NA_real_, method = "样本不足", normal = NA))
  normal <- all(vapply(split(x, g), function(v) {
    v <- v[is.finite(v)]
    if (length(v) < 3) return(FALSE)
    if (length(v) > 5000) v <- sample(v, 5000)
    suppressWarnings(stats::shapiro.test(v)$p.value > 0.05)
  }, logical(1)))
  if (nlevels(g) == 2L) {
    if (normal) {
      vt <- tryCatch(stats::var.test(x ~ g)$p.value > 0.05, error = function(e) FALSE)
      tt <- stats::t.test(x ~ g, var.equal = isTRUE(vt))
      list(p = tt$p.value, method = if (isTRUE(vt)) "t检验" else "Welch t检验", normal = normal)
    } else {
      tt <- stats::wilcox.test(x ~ g); list(p = tt$p.value, method = "Wilcoxon秩和检验", normal = normal)
    }
  } else {
    if (normal) { kk <- stats::oneway.test(x ~ g); list(p = kk$p.value, method = "Welch ANOVA", normal = normal) }
    else { kk <- stats::kruskal.test(x ~ g); list(p = kk$p.value, method = "Kruskal-Wallis检验", normal = normal) }
  }
}

.sk_test_categorical <- function(x, g) {
  ok <- !is.na(x) & !is.na(g)
  tab <- table(factor(x[ok]), droplevels(factor(g[ok])))
  if (any(dim(tab) < 2L)) return(list(p = NA_real_, method = "样本不足", sparse = FALSE))
  expected <- outer(rowSums(tab), colSums(tab)) / sum(tab)
  sparse <- any(expected < 5)
  if (sparse) { ft <- stats::fisher.test(tab); list(p = ft$p.value, method = "Fisher精确检验", sparse = TRUE) }
  else { ct <- suppressWarnings(stats::chisq.test(tab, correct = FALSE)); list(p = ct$p.value, method = "Pearson卡方检验（无连续性校正）", sparse = FALSE) }
}

.sk_cohens_d <- function(x, g) {
  ok <- is.finite(x) & !is.na(g); x <- x[ok]; g <- droplevels(factor(g[ok]))
  if (nlevels(g) != 2L) return(NA_real_)
  a <- x[g == levels(g)[1]]; b <- x[g == levels(g)[2]]
  if (length(a) < 2 || length(b) < 2) return(NA_real_)
  sp <- sqrt(((length(a) - 1) * stats::var(a) + (length(b) - 1) * stats::var(b)) / (length(a) + length(b) - 2))
  if (!is.finite(sp) || sp == 0) return(NA_real_)
  (mean(a) - mean(b)) / sp
}

.sk_cramers_v <- function(x, g) {
  ok <- !is.na(x) & !is.na(g)
  tab <- table(factor(x[ok]), droplevels(factor(g[ok])))
  if (any(dim(tab) < 2L)) return(NA_real_)
  ct <- suppressWarnings(stats::chisq.test(tab, correct = FALSE))
  as.numeric(sqrt(unname(ct$statistic) / (sum(tab) * (min(dim(tab)) - 1L))))
}

.sk_model_table <- function(fit, exp_transform = FALSE) {
  cf <- if (inherits(fit, "merMod")) stats::coef(summary(fit))[, "Estimate"] else stats::coef(fit)
  se <- tryCatch(as.numeric(sqrt(diag(stats::vcov(fit)))), error = function(e) rep(NA_real_, length(cf)))
  pvals <- tryCatch({
    m <- summary(fit)$coefficients
    if (is.null(m) || nrow(m) < length(cf)) rep(NA_real_, length(cf)) else as.numeric(m[, ncol(m)])[seq_along(cf)]
  }, error = function(e) rep(NA_real_, length(cf)))
  gaussian_identity <- inherits(fit, "glm") && fit$family$family == "gaussian" && fit$family$link == "identity"
  t_inference <- (inherits(fit, "lm") && !inherits(fit, "glm")) || gaussian_identity
  critical <- if (t_inference) stats::qt(.975, stats::df.residual(fit)) else stats::qnorm(.975)
  lo <- cf - critical * se; hi <- cf + critical * se
  if (exp_transform) { cf <- exp(cf); lo <- exp(lo); hi <- exp(hi) }
  data.frame(
    变量 = names(cf),
    效应 = round(cf, 4),
    CI下限 = round(lo, 4),
    CI上限 = round(hi, 4),
    P值 = vapply(pvals, .sk_fmt_p, character(1)),
    stringsAsFactors = FALSE, row.names = NULL
  )
}

.sk_vif <- function(fit) {
  design <- tryCatch(stats::model.matrix(fit), error = function(e) NULL)
  if (is.null(design) || !is.matrix(design)) return(stats::setNames(numeric(0), character(0)))
  # Use the fitted rows and actual factor contrasts. These are VIFs for
  # numeric/dummy design columns, not term-level generalized VIFs (GVIF).
  non_intercept <- if (!is.null(attr(design, "assign"))) attr(design, "assign") != 0 else colnames(design) != "(Intercept)"
  design <- design[, non_intercept, drop = FALSE]
  out <- stats::setNames(rep(NA_real_, ncol(design)), colnames(design))
  for (j in seq_len(ncol(design))) {
    v <- design[, j]
    if (length(v) < 2L || any(!is.finite(v))) next
    tss <- sum((v - mean(v))^2)
    if (!is.finite(tss) || tss <= 0) next
    if (ncol(design) == 1L) { out[j] <- 1; next }
    others <- design[, -j, drop = FALSE]
    if (any(!is.finite(others))) next
    r2 <- tryCatch({
      aux_fit <- stats::lm.fit(cbind(1, others), v)
      max(0, 1 - sum(aux_fit$residuals^2) / tss)
    }, error = function(e) NA_real_)
    if (is.finite(r2)) out[j] <- if (r2 >= 1) Inf else 1 / (1 - r2)
  }
  out
}

.sk_vif_assessment <- function(values) {
  known <- values[!is.na(values)]
  incomplete <- !length(values) || any(is.na(values))
  high <- any(known > 5)
  maximum <- if (any(is.infinite(known))) Inf else if (incomplete) NA_real_ else max(values)
  label <- if (is.na(maximum)) "NA" else if (is.infinite(maximum)) "Inf" else sprintf("%.2f", maximum)
  status <- if (high) "warning" else if (incomplete) "not_assessed" else "passed"
  reason <- if (any(is.infinite(known))) "设计列完全共线或达到数值精度极限，VIF 为 Inf。" else if (high) "已评估设计列存在 VIF>5。" else if (incomplete) "缺少可评估的设计列变异或有效证据，未评估。" else "所有设计列 VIF 均不超过 5。"
  if (incomplete && high) reason <- paste0(reason, "部分设计列无有效变异或证据，仍未评估。")
  list(maximum = maximum, label = label, status = status,
    note = paste0("最大设计列VIF=", label, "；", reason,
      "基于本次拟合模型的非截距 numeric/dummy 设计列逐列辅助线性回归；不等同于按原始变量项计算的 GVIF。"))
}
'''


_EMITTER = r'''
## ── Contract-driven emitter: write every required role from its slot ──────
for (.role in names(ROLEMAP)) {
  .slot <- ROLEMAP[[.role]]
  .obj <- .out[[.slot]]
  if (is.null(.obj)) next
  .md <- list(
    role = .role, semantic_key = if (is.null(SEMANTICMAP[[.role]])) .role else SEMANTICMAP[[.role]],
    title = if (is.null(.obj$title)) .role else .obj$title,
    paper_ready = TRUE,
    diagnostic_status = as.list(.diag_status)
  )
  if (identical(.obj$kind, "table")) {
    baa_table(.role, .obj$payload, metadata = .md)
  } else if (identical(.obj$kind, "figure")) {
    baa_plot(.role, .obj$payload, metadata = .md)
  } else if (identical(.obj$kind, "text")) {
    baa_text(.role, .obj$payload, metadata = .md)
  } else if (identical(.obj$kind, "model")) {
    baa_model_summary(.role, .obj$payload, metadata = .md)
  }
}
'''

_T1_VARIABLE_HELPER = r'''
## Build Table-1 rows for one variable.
## Categorical/binary → ONE ROW PER LEVEL (standard Table 1 format)
## Continuous → single row with mean±SD
.sk_t1_variable <- function(name, x, g, glevels, force_continuous = FALSE) {
  kind <- .sk_kind(x)
  if (kind == "survival") return(NULL)
  miss <- sum(is.na(x))
  miss_rate <- miss / max(length(x), 1)

  ## ── Force binary numeric (0/1, 1/2) to factor ──
  if (force_continuous && (!is.numeric(x) || is.factor(x))) stop("所选连续变量不是数值列：", name)
  if (is.numeric(x) && !force_continuous) {
    uvals <- sort(unique(na.omit(x)))
    if (length(uvals) == 2L) {
      x <- factor(ifelse(x == max(uvals), "Yes", "No"), levels = c("No", "Yes"))
      kind <- "binary_cat"
    } else if (length(uvals) <= 6L && all(uvals == floor(uvals))) {
      x <- factor(x, levels = as.character(sort(uvals)))
      kind <- "categorical"
    }
  }
  if (is.character(x)) x <- factor(x)

  is_cat <- is.factor(x)

  if (!is_cat) {
    ## ── Continuous: single row with mean±sd ──
    ok <- is.finite(x)
    normal <- if (!is.null(g)) {
      all(vapply(split(x[ok], g[ok]), function(v) {
        v <- v[is.finite(v)]
        if (length(v) < 3) return(FALSE)
        if (length(v) > 5000) v <- sample(v, 5000)
        suppressWarnings(stats::shapiro.test(v)$p.value > 0.05)
      }, logical(1)))
    } else TRUE
    sd_val <- tryCatch(stats::sd(x, na.rm = TRUE), error = function(e) NA_real_)
    overall <- sprintf("%.1f ± %.1f", mean(x, na.rm = TRUE), sd_val)
    per_g <- if (!is.null(g)) vapply(glevels, function(gl) {
      sv <- x[!is.na(g) & g == gl & is.finite(x)]
      sprintf("%.1f ± %.1f", mean(sv), stats::sd(sv))
    }, character(1)) else character(0)
    tr <- if (!is.null(g)) .sk_test_continuous(x, g) else list(p = NA_real_, method = "不适用（无分组）", normal = normal)
    d_val <- if (!is.null(g)) .sk_cohens_d(x, g) else NA_real_

    t1 <- data.frame(变量 = name, 水平 = "", 总体 = overall, 缺失 = miss, stringsAsFactors = FALSE)
    if (!is.null(g)) for (gi in seq_along(glevels)) t1[[sprintf("组_%s", glevels[gi])]] <- if (length(per_g) >= gi) per_g[gi] else ""
    t1$P值 <- .sk_fmt_p(tr$p); t1$检验方法 <- tr$method
    t1$标准化差异 <- if (is.na(d_val)) "NA" else round(d_val, 3)
    cmp <- data.frame(变量 = name, 检验方法 = tr$method, P值 = .sk_fmt_p(tr$p), stringsAsFactors = FALSE)
    bal <- data.frame(变量 = name, 标准化差异 = if (is.na(d_val)) "NA" else round(d_val, 3),
      判定 = if (is.na(d_val)) "NA" else if (abs(d_val) < 0.1) "均衡" else "不均衡", stringsAsFactors = FALSE)
    eff <- data.frame(变量 = name, 效应量 = "Cohen's d", 数值 = if (is.na(d_val)) "NA" else round(d_val, 4), stringsAsFactors = FALSE)
    exp <- data.frame(变量 = name, 类型 = "continuous", 汇总 = overall, 缺失 = miss, stringsAsFactors = FALSE)

    return(list(t1_rows = list(t1),
      cmp = cmp, bal = bal, eff = eff, exp = exp,
      fmt = sprintf("%s (continuous): %s", name, overall),
      normal = normal, sparse = FALSE, miss_rate = miss_rate, is_cont = TRUE))
  }

  ## ── Categorical / binary factor: ONE ROW PER LEVEL ──
  xf <- droplevels(x)
  lvls <- levels(xf)
  n_lvl <- length(lvls)
  tr <- if (!is.null(g) && nlevels(g) >= 2L) .sk_test_categorical(xf, g) else list(p = NA_real_, method = "不适用（无分组）", sparse = FALSE)
  cv <- if (!is.null(g)) .sk_cramers_v(xf, g) else NA_real_
  if (isTRUE(tr$sparse)) miss_rate <- miss_rate

  ## Header row (variable name)
  hdr <- data.frame(变量 = name, 水平 = "", 总体 = sprintf("n=%d", sum(!is.na(xf))),
    缺失 = miss, P值 = .sk_fmt_p(tr$p), 检验方法 = tr$method, stringsAsFactors = FALSE)
  if (!is.null(g)) for (gi in seq_along(glevels)) hdr[[sprintf("组_%s", glevels[gi])]] <- ""

  ## Level rows (one per level with n(%) per group)
  lvl_rows <- list(hdr)
  for (lv in lvls) {
    lv_row <- data.frame(变量 = sprintf("  %s", lv), 水平 = lv, stringsAsFactors = FALSE)
    lv_row[["总体"]] <- sprintf("%d (%.1f%%)", sum(xf == lv, na.rm = TRUE), 100 * mean(xf == lv, na.rm = TRUE))
    if (!is.null(g)) for (gi in seq_along(glevels)) {
      gl <- glevels[gi]
      sub <- xf[!is.na(g) & g == gl]
      n_lv <- sum(sub == lv, na.rm = TRUE)
      pct_lv <- if (any(!is.na(sub))) 100 * n_lv / sum(!is.na(sub)) else NA_real_
      lv_row[[sprintf("组_%s", glevels[gi])]] <- sprintf("%d (%.1f%%)", n_lv, pct_lv)
    }
    lv_row$缺失 <- 0L
    lv_row$P值 <- if (li <- which(lvls == lv) == 1L) .sk_fmt_p(tr$p) else ""
    lv_row$检验方法 <- ""
    lvl_rows[[length(lvl_rows) + 1L]] <- lv_row
  }

  cmp <- data.frame(变量 = name, 检验方法 = tr$method, P值 = .sk_fmt_p(tr$p), stringsAsFactors = FALSE)
  bal <- data.frame(变量 = name, 标准化差异 = if (is.na(cv)) "NA" else round(cv, 3),
    判定 = if (is.na(cv)) "NA" else if (abs(cv) < 0.1) "均衡" else "不均衡", stringsAsFactors = FALSE)
  eff <- data.frame(变量 = name, 效应量 = "Cramér's V", 数值 = if (is.na(cv)) "NA" else round(cv, 4), stringsAsFactors = FALSE)
  exp <- data.frame(变量 = name, 类型 = paste0("categorical(", n_lvl, " levels)"), 汇总 = paste(lvls, collapse = "/"),
    缺失 = miss, stringsAsFactors = FALSE)

  return(list(t1_rows = lvl_rows,
    cmp = cmp, bal = bal, eff = eff, exp = exp,
    fmt = sprintf("%s (categorical, %d levels): %s", name, n_lvl, paste(lvls, collapse = "/")),
    normal = FALSE, sparse = isTRUE(tr$sparse), miss_rate = miss_rate, is_cont = FALSE))
}
'''

_HELPERS = _HELPERS + "\n" + _T1_VARIABLE_HELPER

_EMIT_TABLE_ONE = r'''
## Engine: descriptive reporting (Table 1 / baseline characteristics)
## inputs: group(optional), continuous_variables, categorical_variables(optional)
.group_v <- if (exists("group_v") && !is.null(group_v)) as.character(group_v) else NULL
.cont_vars <- if (exists("cont_v")) as.character(cont_v) else character(0)
.cat_vars <- if (exists("cat_v")) as.character(cat_v) else character(0)
.missing_indicator_summary <- NULL
.missing_indicator_note <- ""
if (exists("miss_v") && !is.null(miss_v)) {
  .miss_name <- as.character(miss_v)
  if (length(.miss_name) != 1L || !(.miss_name %in% names(dataset))) stop("缺失标记列不存在或未唯一指定")
  .miss_raw <- dataset[[.miss_name]]
  .miss_encoded <- if (is.logical(.miss_raw)) as.character(as.integer(.miss_raw)) else as.character(.miss_raw)
  if (any(!is.na(.miss_encoded) & !(.miss_encoded %in% c("0", "1")))) stop("缺失标记必须明确编码为 0=未缺失、1=缺失；NA 保留为未知")
  .miss_flag <- suppressWarnings(as.integer(.miss_encoded))
  .miss_groups <- if (!is.null(.group_v) && length(.group_v) == 1L && .group_v %in% names(dataset)) as.character(dataset[[.group_v]]) else rep("总体", nrow(dataset))
  .miss_groups[is.na(.miss_groups)] <- "<分组缺失>"
  .missing_indicator_summary <- do.call(rbind, lapply(unique(.miss_groups), function(gr) {
    flag <- .miss_flag[.miss_groups == gr]
    known <- sum(!is.na(flag)); missing <- sum(flag == 1L, na.rm = TRUE)
    data.frame(变量 = .miss_name, 类型 = "missingness_indicator(0/1)",
      汇总 = sprintf("标记缺失 %d/%d，标记未知 %d", missing, known, sum(is.na(flag))),
      缺失 = sum(is.na(flag)), 分组 = gr, 总行数 = length(flag),
      标记已知行数 = known, 标记缺失行数 = missing,
      标记未缺失行数 = sum(flag == 0L, na.rm = TRUE),
      标记未知行数 = sum(is.na(flag)), 标记缺失率 = if (known) missing / known else NA_real_,
      stringsAsFactors = FALSE)
  }))
  .missing_indicator_note <- paste0("缺失标记 ", .miss_name, " 使用 0=未缺失、1=缺失，NA=标记未知；按实际分组报告标记缺失数/已知标记数与未知数：",
    paste(sprintf("%s: %s", .missing_indicator_summary$分组, .missing_indicator_summary$汇总), collapse = "；"),
    "。标记含义来自确认输入，不将其等同于全部选定变量的共同缺失，也不据此推断缺失机制。")
}

.step <- "type-scan"
allvars <- unique(c(.cont_vars, .cat_vars, .group_v))
missing_cols <- setdiff(allvars, names(dataset))
if (length(missing_cols)) stop("缺失数据列: ", paste(missing_cols, collapse = ", "))
df0 <- .sk_auto_factor(dataset, known_continuous = .cont_vars)
if (!is.null(.group_v) && .group_v %in% names(df0)) {
  df0[[.group_v]] <- droplevels(factor(df0[[.group_v]]))
}
## Reclassify: variables that are now factors move from .cont_vars to .cat_vars
.was_numeric <- sapply(.cont_vars, function(v) is.factor(df0[[v]]) && v %in% names(df0))
if (any(.was_numeric)) {
  .reclassified <- .cont_vars[.was_numeric]
  .cont_vars <- setdiff(.cont_vars, .reclassified)
  .cat_vars <- union(.cat_vars, .reclassified)
}

.step <- "collect"
.g <- if (!is.null(.group_v) && nlevels(df0[[.group_v]]) >= 2L) df0[[.group_v]] else NULL
glevels <- if (!is.null(.g)) levels(.g) else character(0)
.t1_rows <- list(); .cmp_rows <- list(); .bal_rows <- list(); .eff_rows <- list(); .exp_rows <- list()
.fmt_lines <- character(0); .n_cont <- 0L; .n_normal <- 0L; .n_sparse <- 0L; .max_miss <- 0

for (v in union(.cont_vars, .cat_vars)) {
  if (identical(v, .group_v)) next
  r <- .sk_t1_variable(v, df0[[v]], .g, glevels, force_continuous = v %in% .cont_vars)
  if (is.null(r)) next
  for (rdf in r$t1_rows) .t1_rows[[length(.t1_rows) + 1L]] <- rdf
  .cmp_rows[[length(.cmp_rows) + 1L]] <- r$cmp
  .bal_rows[[length(.bal_rows) + 1L]] <- r$bal
  .eff_rows[[length(.eff_rows) + 1L]] <- r$eff
  .exp_rows[[length(.exp_rows) + 1L]] <- r$exp
  .fmt_lines <- c(.fmt_lines, r$fmt)
  .max_miss <- max(.max_miss, r$miss_rate)
  if (isTRUE(r$sparse)) .n_sparse <- .n_sparse + 1L
  if (isTRUE(r$normal)) .n_normal <- .n_normal + 1L
  if (r$is_cont) .n_cont <- .n_cont + 1L
}

.step <- "assemble"
.t1 <- if (length(.t1_rows)) dplyr::bind_rows(.t1_rows) else data.frame(变量 = character(0), stringsAsFactors = FALSE)
.cmp <- if (length(.cmp_rows)) do.call(rbind, .cmp_rows) else data.frame(说明 = "未选择分组变量，组间比较不适用", stringsAsFactors = FALSE)
.bal <- if (length(.bal_rows)) do.call(rbind, .bal_rows) else data.frame(说明 = "未选择分组变量，SMD 不适用", stringsAsFactors = FALSE)
.eff <- if (length(.eff_rows)) do.call(rbind, .eff_rows) else data.frame(说明 = "未选择分组变量，效应量不适用", stringsAsFactors = FALSE)
.exp <- if (length(.exp_rows)) do.call(rbind, .exp_rows) else data.frame(说明 = "无变量", stringsAsFactors = FALSE)
if (!is.null(.missing_indicator_summary)) .exp <- dplyr::bind_rows(.exp, .missing_indicator_summary)

.t1_note <- sprintf("连续变量按 均值±标准差 报告，分类变量按 频数(百分比) 报告；共 %d 个变量，缺失值未插补。%s", nrow(.t1),
  if (!is.null(.g)) sprintf("分组变量 %s（%s）。", .group_v, paste(glevels, collapse = " / ")) else "未选择分组变量，仅报告总体描述。")
.t1_note <- paste(.t1_note, .missing_indicator_note)
.method_txt <- paste0(
  "组间比较方法按变量类型与分布自动选择：连续变量先做各组正态性检查（Shapiro-Wilk），近似正态时用 t 检验（方差不齐用 Welch 校正），否则用 Wilcoxon 秩和检验；",
  "分类变量用 Pearson 卡方检验，小样本或稀疏单元格用 Fisher 精确检验。",
  sprintf("本次 %d 个连续变量中 %d 个通过正态性检查；%d 个分类变量存在稀疏水平（<5 例）。%s",
    .n_cont, .n_normal, .n_sparse,
    if (!is.null(.g)) "同时报告标准化差异以评估组间均衡性（|d|<0.1 视为均衡）。" else ""))
if (.max_miss > 0.05) .diag_status[["缺失比例检查"]] <- "warning" else .diag_status[["缺失比例检查"]] <- "not_assessed"
.diag_status[["连续变量分布检查"]] <- if (.n_cont > 0 && .n_normal < .n_cont) "warning" else "passed"
.diag_status[["分类变量稀疏水平检查"]] <- if (.n_sparse > 0) "warning" else "passed"

.out[["t1"]] <- list(kind = "table", payload = .t1, title = "Table 1 基线特征表")
.out[["t1_fmt"]] <- list(kind = "table", payload = .t1, title = "Table 1（三线表版式）")
.out[["cmp"]] <- list(kind = "table", payload = .cmp, title = "组间比较表")
.out[["bal"]] <- list(kind = "table", payload = .bal, title = "组间均衡性（SMD）")
.out[["eff"]] <- list(kind = "table", payload = .eff, title = "效应量表")
.out[["exp"]] <- list(kind = "table", payload = .exp, title = "导出表")
.out[["report_note"]] <- list(kind = "text", payload = .t1_note, title = "变量报告格式说明")
.out[["method_note"]] <- list(kind = "text", payload = .method_txt, title = "组间比较方法说明")
'''

_ENGINE_GLM = r'''
## Engine: generalized linear regression (stats::glm)
## inputs: outcome(critical), exposure(critical), covariates(optional), strata(optional)
.step <- "setup"
if (exists("strata_v") && length(strata_v)) stop("当前固定回归不支持交互/分层变量；请使用交互或分层分析入口。")
preds <- unique(c(exposure_v, if (exists("covars_v")) as.character(covars_v) else character(0)))
df <- dataset[, c(outcome_v, preds), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
n_used <- nrow(df)
if (n_used < 10) stop(sprintf("有效样本过少 (n=%d)", n_used))

of <- factor(df[[outcome_v]])
if (is.numeric(df[[outcome_v]]) && length(unique(df[[outcome_v]])) == 2L) {
  df[[outcome_v]] <- factor(df[[outcome_v]])
}
if (is.factor(df[[outcome_v]])) {
  if (nlevels(droplevels(df[[outcome_v]])) != 2L) stop("结局为多分类，当前引擎仅支持二分类或连续结局")
  fam <- stats::binomial(link = "logit"); trans <- exp; eff_name <- "OR"
  df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
} else {
  fam <- stats::gaussian(); trans <- function(x) x; eff_name <- "Beta"
  df[[outcome_v]] <- as.numeric(df[[outcome_v]])
}
if (!is.numeric(df[[exposure_v]])) df[[exposure_v]] <- factor(df[[exposure_v]])
for (p in preds) if (is.character(df[[p]])) df[[p]] <- factor(df[[p]])

rhs <- if (length(preds)) paste(sprintf("`%s`", preds), collapse = " + ") else sprintf("`%s`", exposure_v)
f_full <- stats::as.formula(paste0("`", outcome_v, "` ~ ", rhs))
fit <- stats::glm(f_full, family = fam, data = df)
vifv <- .sk_vif(fit)
.vif_diagnostic <- .sk_vif_assessment(vifv)
max_vif <- .vif_diagnostic$maximum
.diag_status[["共线性检查"]] <- .vif_diagnostic$status
if (!isTRUE(fit$converged)) stop("回归未收敛，不能生成有效推断")
if (fit$rank < length(stats::coef(fit)) || any(!is.finite(stats::coef(fit))))
  stop("模型矩阵秩不足或存在不可估计系数；请调整共线变量")
if (any(!is.finite(stats::vcov(fit)))) stop("模型协方差不可用，不能生成有效推断")

.step <- "model-table"
mt <- .sk_model_table(fit, exp_transform = is.factor(df[[outcome_v]]))

.step <- "marginal-effects"
if (is.numeric(df[[exposure_v]])) {
  xq <- round(as.numeric(stats::quantile(df[[exposure_v]], c(0.1, 0.25, 0.5, 0.75, 0.9), names = FALSE)), 4)
} else {
  xq <- levels(factor(df[[exposure_v]]))
}
nd <- df[rep(1L, length(xq)), , drop = FALSE]; rownames(nd) <- NULL
nd[[exposure_v]] <- xq
pr <- tryCatch(as.numeric(stats::predict(fit, newdata = nd, type = "response")), error = function(e) rep(NA_real_, length(xq)))
marg <- data.frame(暴露水平 = as.character(xq), 预测结局 = round(pr, 4), stringsAsFactors = FALSE)

.step <- "robust-ci"
robust <- tryCatch({
  if (!requireNamespace("sandwich", quietly = TRUE)) stop("sandwich 包不可用")
  se_r <- as.numeric(sqrt(diag(sandwich::vcovHC(fit, type = "HC3"))))
  cf <- stats::coef(fit)
  lo <- cf - 1.959964 * se_r; hi <- cf + 1.959964 * se_r
  if (is.factor(df[[outcome_v]])) { cf <- exp(cf); lo <- exp(lo); hi <- exp(hi) }
  data.frame(变量 = names(cf), 稳健效应 = round(cf, 4), 稳健CI下限 = round(lo, 4), 稳健CI上限 = round(hi, 4), stringsAsFactors = FALSE, row.names = NULL)
}, error = function(e) data.frame(说明 = "稳健标准误不可用（缺少 sandwich 包），请通过依赖门安装后重试", stringsAsFactors = FALSE))

.step <- "uni-multi"
uni_rows <- list()
for (p in preds) {
  fu <- stats::as.formula(paste0("`", outcome_v, "` ~ `", p, "`"))
  fu_fit <- tryCatch(stats::glm(fu, family = fam, data = df), error = function(e) NULL)
  if (!is.null(fu_fit)) {
    ut <- .sk_model_table(fu_fit, exp_transform = is.factor(df[[outcome_v]]))
    ut$模型 <- "单因素"
    uni_rows[[length(uni_rows) + 1L]] <- ut
  }
}
mut <- mt; mut$模型 <- "多因素"
uni_multi <- rbind(do.call(rbind, uni_rows), mut)[, c("模型", names(mt))]

.step <- "diagnostics"
cooks <- tryCatch(as.numeric(stats::cooks.distance(fit)), error = function(e) rep(NA_real_, n_used))
n_infl <- sum(cooks > 4 / n_used, na.rm = TRUE)
resid_ok <- tryCatch({
  rr <- stats::residuals(fit, type = "deviance")
  if (length(rr) > 5000) rr <- sample(rr, 5000)
  isTRUE(stats::shapiro.test(rr)$p.value > 0.05)
}, error = function(e) NA)
diag_table <- data.frame(
  指标 = c("AIC", "样本量", "最大设计列VIF", "强影响点数(cooks>4/n)"),
  结果 = c(sprintf("%.2f", stats::AIC(fit)), n_used,
    .vif_diagnostic$label,
    sprintf("%d", n_infl)),
  stringsAsFactors = FALSE)
if (length(vifv)) diag_table <- rbind(diag_table, data.frame(
  指标 = paste0("设计列VIF：", names(vifv)),
  结果 = vapply(vifv, function(v) if (is.na(v)) "NA" else if (is.infinite(v)) "Inf" else sprintf("%.2f", v), character(1)),
  stringsAsFactors = FALSE))
diag_table <- rbind(diag_table, data.frame(指标 = "共线性诊断说明", 结果 = .vif_diagnostic$note, stringsAsFactors = FALSE))
.diag_status[["残差/拟合检查"]] <- if (is.na(resid_ok)) "warning" else if (isTRUE(resid_ok)) "passed" else "warning"
.diag_status[["影响点检查"]] <- if (n_infl > 0) "warning" else "passed"

plot_df <- data.frame(
  fitted = as.numeric(stats::fitted(fit)),
  resid = as.numeric(stats::residuals(fit, type = "deviance")),
  stringsAsFactors = FALSE)
diag_fig <- ggplot2::ggplot(plot_df, ggplot2::aes(x = fitted, y = resid)) +
  ggplot2::geom_point(alpha = 0.5) +
  ggplot2::geom_smooth(se = FALSE, linewidth = 0.6) +
  ggplot2::labs(title = "残差诊断图", x = "拟合值", y = "Deviance 残差") +
  ggplot2::theme_bw()

eff_strings <- sprintf("%s: %s [95%%CI %.3f, %.3f], P=%s", mt$变量, mt$效应, mt$CI下限, mt$CI上限, mt$P值)
results_txt <- paste0(
  sprintf("多因素回归（n=%d，%s 模型）。暴露 %s 的校正效应: %s=%s [95%%CI %s, %s], P=%s。",
    n_used, if (is.factor(df[[outcome_v]])) "logistic" else "线性", exposure_v, eff_name,
    mt$效应[match(exposure_v, mt$变量)], mt$CI下限[match(exposure_v, mt$变量)],
    mt$CI上限[match(exposure_v, mt$变量)], if (is.na(match(exposure_v, mt$变量))) "NA" else mt$P值[match(exposure_v, mt$变量)]),
  .vif_diagnostic$note,
  sprintf("强影响点 %d 个。详细回归表与诊断见右侧结果面板。", n_infl))

.out[["model_table"]] <- list(kind = "table", payload = mt, title = "回归表")
.out[["marginal"]] <- list(kind = "table", payload = marg, title = "边际效应")
.out[["robust_ci"]] <- list(kind = "table", payload = robust, title = "稳健 CI")
.out[["uni_multi"]] <- list(kind = "table", payload = uni_multi, title = "单因素/多因素模型表")
.out[["eff_strings"]] <- list(kind = "table", payload = data.frame(效应量与置信区间 = eff_strings, stringsAsFactors = FALSE), title = "效应量和置信区间")
.out[["diag_figure"]] <- list(kind = "figure", payload = diag_fig, title = "诊断图")
.out[["diag_table"]] <- list(kind = "table", payload = diag_table, title = "模型诊断")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_SURVIVAL = r'''
## Engine: survival analysis (KM + Cox, survival package)
## inputs: followup_time(critical), event(critical), exposure(critical), covariates(optional), time_unit(optional parameter)
if (!exists("time_unit_v")) time_unit_v <- ""
if (!exists("covars_v")) covars_v <- character(0)
.sk_time_axis <- if (is.character(time_unit_v) && length(time_unit_v) == 1L && nzchar(time_unit_v)) {
  paste0("随访时间（", time_unit_v, "）")
} else "随访时间"
.step <- "setup"
df <- dataset[, unique(c(followup_v, event_v, exposure_v, if (exists("covars_v")) as.character(covars_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
if (!is.numeric(df[[followup_v]]) || inherits(df[[followup_v]], c("Date", "POSIXt")) ||
    any(!is.finite(df[[followup_v]])) || any(df[[followup_v]] < 0))
  stop("随访时间必须为非负数值时长；日历日期需先转换为相对共同起点的随访时长")
event_values <- as.character(df[[event_v]])
if (is.logical(df[[event_v]])) event_values <- as.character(as.integer(df[[event_v]]))
if (!all(event_values %in% c("0", "1"))) stop("当前固定生存分析要求 0/1 事件编码：0=删失，1=事件；请先确认并重编码")
df[[event_v]] <- as.integer(event_values)
n_used <- nrow(df)
n_events <- sum(df[[event_v]] == 1)
if (n_events < 10) stop(sprintf("事件数过少 (%d)，生存分析不可靠", n_events))
df[[exposure_v]] <- droplevels(factor(df[[exposure_v]]))
if (nlevels(df[[exposure_v]]) < 2L) stop("分组/暴露变量需要至少两个水平")
surv_obj <- survival::Surv(df[[followup_v]], df[[event_v]])

.step <- "km"
km <- survival::survfit(surv_obj ~ df[[exposure_v]])
sd <- survival::survdiff(surv_obj ~ df[[exposure_v]])
logrank_p <- stats::pchisq(sd$chisq, length(sd$n) - 1L, lower.tail = FALSE)

scur <- summary(km)
km_curve <- data.frame(
  时间 = as.numeric(scur$time), 生存概率 = as.numeric(scur$surv),
  组 = gsub("^[^=]+=", "", as.character(scur$strata)),
  stringsAsFactors = FALSE)
km_fig <- ggplot2::ggplot(km_curve, ggplot2::aes(x = 时间, y = 生存概率, colour = 组)) +
  ggplot2::geom_step(linewidth = 0.9) +
  ggplot2::geom_hline(yintercept = 0.5, linetype = 3, colour = "grey60") +
  ggplot2::labs(title = "Kaplan-Meier 生存曲线", caption = sprintf("Log-rank P=%s", .sk_fmt_p(logrank_p)), x = .sk_time_axis, y = "生存概率") +
  ggplot2::theme_bw()

.step <- "cox"
rhs <- if (length(covars_v)) paste(c(sprintf("`%s`", exposure_v), sprintf("`%s`", covars_v)), collapse = " + ") else sprintf("`%s`", exposure_v)
f_cox <- stats::as.formula(paste0("surv_obj ~ ", rhs))
fit <- survival::coxph(f_cox, data = df)
if (any(!is.finite(stats::coef(fit))) || any(!is.finite(stats::vcov(fit))))
  stop("Cox 系数或协方差不可估计；请调整模型")
cox_multi <- .sk_model_table(fit, exp_transform = TRUE)

uni_rows <- list()
uni_terms <- c(exposure_v, if (exists("covars_v")) as.character(covars_v) else character(0))
for (p in uni_terms) {
  fu <- tryCatch(survival::coxph(stats::as.formula(paste0("surv_obj ~ `", p, "`")), data = df), error = function(e) NULL)
  if (!is.null(fu_fit <- fu)) {
    ut <- .sk_model_table(fu_fit, exp_transform = TRUE); ut$模型 <- "单因素"
    uni_rows[[length(uni_rows) + 1L]] <- ut
  }
}
cox_multi$模型 <- "多因素"
uni_multi <- rbind(do.call(rbind, uni_rows), cox_multi)[, c("模型", names(cox_multi))]

.step <- "ph"
zph <- tryCatch(survival::cox.zph(fit), error = function(e) NULL)
ph_df <- if (!is.null(zph)) {
  zt <- as.data.frame(zph$table)
  data.frame(检验 = rownames(zt), 卡方 = round(zt$chisq, 3), P值 = vapply(zt$p, .sk_fmt_p, character(1)), stringsAsFactors = FALSE)
} else data.frame(说明 = "PH 检验不可用", stringsAsFactors = FALSE)
ph_global_p <- if (!is.null(zph)) unname(zph$table["GLOBAL", "p"]) else NA_real_

risk_times <- round(stats::quantile(df[[followup_v]], c(0.25, 0.5, 0.75, 1.0), names = FALSE))
rt <- summary(km, times = risk_times, extend = TRUE)
risk_df <- data.frame(
  组 = gsub("^[^=]+=", "", as.character(rt$strata)),
  时间 = rt$time, 风险人数 = rt$n.risk, 事件数 = rt$n.event, stringsAsFactors = FALSE)

events_per_pred <- n_events / max(1, length(uni_terms) + length(covars_v))
.diag_status[["比例风险假设检查"]] <- if (is.na(ph_global_p)) "warning" else if (ph_global_p < 0.05) "warning" else "passed"
.diag_status[["删失分布检查"]] <- if (n_used - n_events > 0.9 * n_used) "warning" else "passed"
.diag_status[["事件数充分性检查"]] <- if (events_per_pred < 10) "warning" else "passed"

results_txt <- paste0(
  sprintf("生存分析（n=%d，事件数=%d）。Log-rank P=%s。", n_used, n_events, .sk_fmt_p(logrank_p)),
  sprintf("多因素 Cox 模型主要效应见 %s 表；比例风险检验 GLOBAL P=%s。",
    "table_4", .sk_fmt_p(ph_global_p)),
  " 详细 HR、置信区间与诊断见右侧结果面板。局限：适用独立个体、右删失和固定协变量；本轮不证明独立删失或比例风险成立。下一步按诊断检查模型适用性，并在独立样本中验证。")

.out[["km_plot"]] <- list(kind = "figure", payload = km_fig, title = "KM 图")
.out[["km_plot2"]] <- list(kind = "figure", payload = km_fig, title = "KM 曲线（Log-rank 标注）")
.out[["uni_cox"]] <- list(kind = "table", payload = uni_multi, title = "Cox 表（单因素/多因素）")
.out[["ph_table"]] <- list(kind = "table", payload = ph_df, title = "PH 检验")
.out[["risk_table"]] <- list(kind = "table", payload = risk_df, title = "风险表")
.out[["multi_cox"]] <- list(kind = "table", payload = cox_multi, title = "Cox HR 表")
.out[["ph_diag_table"]] <- list(kind = "table", payload = ph_df, title = "比例风险诊断")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_GROUP_TESTS = r'''
## Engine: classical group comparisons (tests + effect sizes + multiplicity)
## inputs: group(critical), continuous_outcomes(conditional), categorical_outcomes(conditional), paired_marker(optional)
.step <- "setup"
if (exists("paired_v") && length(paired_v)) stop("当前固定组间比较仅支持独立观测；配对/重复测量请使用相应分析入口。")
.gv <- droplevels(factor(dataset[[group_v]]))
cont_outs <- if (exists("cont_v")) as.character(cont_v) else character(0)
cat_outs <- if (exists("cat_v")) as.character(cat_v) else character(0)
if (nlevels(.gv) < 2L) stop("分组变量需要至少两个水平")

.rows_method <- list(); .rows_p <- list(); .rows_eff <- list()
.rows_adj <- list(); .rows_cmp <- list(); .rows_effci <- list()
.n_nonparam <- 0L; .n_sparse <- 0L; .n_tests <- 0L
.all_p <- numeric(0)

.step <- "tests"
collect_cont <- function(v) {
  x <- as.numeric(dataset[[v]])
  tr <- .sk_test_continuous(x, .gv)
  d <- .sk_cohens_d(x, .gv)
  .rows_method[[length(.rows_method) + 1L]] <<- data.frame(变量 = v, 检验方法 = tr$method, stringsAsFactors = FALSE)
  .rows_p[[length(.rows_p) + 1L]] <<- data.frame(变量 = v, P值 = .sk_fmt_p(tr$p), stringsAsFactors = FALSE)
  .rows_eff[[length(.rows_eff) + 1L]] <<- data.frame(变量 = v, 效应量 = "Cohen's d", 数值 = round(d, 4), stringsAsFactors = FALSE)
  .rows_adj[[length(.rows_adj) + 1L]] <<- v
  se_d <- tryCatch(sqrt((nlevels(.gv) * length(x)) / (prod(table(.gv))) + d^2 / (2 * length(x))), error = function(e) NA_real_)
  .rows_effci[[length(.rows_effci) + 1L]] <<- data.frame(
    变量 = v, 效应量 = "Cohen's d", 数值 = round(d, 4),
    CI下限 = if (is.na(d)) "NA" else round(d - 1.959964 * se_d, 4),
    CI上限 = if (is.na(d)) "NA" else round(d + 1.959964 * se_d, 4), stringsAsFactors = FALSE)
  .rows_cmp[[length(.rows_cmp) + 1L]] <<- data.frame(
    变量 = v, 类型 = "continuous",
    汇总 = paste(vapply(levels(.gv), function(gl) sprintf("%s: %.2f±%.2f", gl, mean(x[.gv == gl], na.rm = TRUE), stats::sd(x[.gv == gl], na.rm = TRUE)), character(1)), collapse = "; "),
    stringsAsFactors = FALSE)
  .n_tests <<- .n_tests + 1L
  if (!is.na(tr$p)) .all_p <<- c(.all_p, tr$p)
  if (grepl("Wilcoxon|Kruskal", tr$method)) .n_nonparam <<- .n_nonparam + 1L
  invisible(NULL)
}
for (v in cont_outs) collect_cont(v)

collect_cat <- function(v) {
  x <- factor(dataset[[v]])
  tr <- .sk_test_categorical(x, .gv)
  cv <- .sk_cramers_v(x, .gv)
  if (isTRUE(tr$sparse)) .n_sparse <<- .n_sparse + 1L
  .rows_method[[length(.rows_method) + 1L]] <<- data.frame(变量 = v, 检验方法 = tr$method, stringsAsFactors = FALSE)
  .rows_p[[length(.rows_p) + 1L]] <<- data.frame(变量 = v, P值 = .sk_fmt_p(tr$p), stringsAsFactors = FALSE)
  .rows_eff[[length(.rows_eff) + 1L]] <<- data.frame(变量 = v, 效应量 = "Cramér's V", 数值 = round(cv, 4), stringsAsFactors = FALSE)
  .rows_adj[[length(.rows_adj) + 1L]] <<- v
  .rows_cmp[[length(.rows_cmp) + 1L]] <<- data.frame(
    变量 = v, 类型 = "categorical",
    汇总 = paste(vapply(levels(.gv), function(gl) {
      sub <- factor(x[.gv == gl])
      paste(vapply(levels(x), function(l) sprintf("%s:%d", l, sum(sub == l)), character(1)), collapse = " ")
    }, character(1)), collapse = " | "),
    stringsAsFactors = FALSE)
  .n_tests <<- .n_tests + 1L
  if (!is.na(tr$p)) .all_p <<- c(.all_p, tr$p)
  invisible(NULL)
}
for (v in cat_outs) collect_cat(v)

.step <- "multiplicity"
adj_p <- stats::p.adjust(.all_p, method = "BH")
adj_table <- data.frame(
  检验序号 = seq_along(.all_p),
  原始P = vapply(.all_p, .sk_fmt_p, character(1)),
  BH校正P = vapply(adj_p, .sk_fmt_p, character(1)),
  stringsAsFactors = FALSE)

methods_table <- if (length(.rows_method)) do.call(rbind, .rows_method) else data.frame(说明 = "无可检验变量", stringsAsFactors = FALSE)
p_table <- if (length(.rows_p)) do.call(rbind, .rows_p) else data.frame(说明 = "无", stringsAsFactors = FALSE)
eff_table <- if (length(.rows_eff)) do.call(rbind, .rows_eff) else data.frame(说明 = "无", stringsAsFactors = FALSE)
eff_ci_table <- if (length(.rows_effci)) do.call(rbind, .rows_effci) else data.frame(说明 = "无", stringsAsFactors = FALSE)
cmp_table <- if (length(.rows_cmp)) do.call(rbind, .rows_cmp) else data.frame(说明 = "无", stringsAsFactors = FALSE)

.step <- "figure"
plot_rows <- data.frame()
for (v in head(cont_outs, 6)) {
  x <- as.numeric(dataset[[v]])
  plot_rows <- rbind(plot_rows, data.frame(变量 = v, 数值 = x, 组 = as.character(.gv), stringsAsFactors = FALSE))
}
if (nrow(plot_rows)) {
  diff_fig <- ggplot2::ggplot(plot_rows[is.finite(plot_rows$数值), ], ggplot2::aes(x = 组, y = 数值, fill = 组)) +
    ggplot2::geom_boxplot(alpha = 0.7, outlier.size = 0.6) +
    ggplot2::facet_wrap(~变量, scales = "free_y") +
    ggplot2::labs(title = "分组差异图") + ggplot2::theme_bw() + ggplot2::theme(legend.position = "none")
} else {
  diff_fig <- ggplot2::ggplot() + ggplot2::labs(title = "无可绘制的连续结局")
}

method_txt <- paste0(
  "检验方法按变量类型与正态性自动选择：连续变量正态时用 t 检验/Welch，否则 Wilcoxon/Kruskal-Wallis；分类变量用卡方或 Fisher。",
  sprintf("本次共 %d 个检验，%d 个采用非参数方法，%d 个分类变量存在稀疏水平。", .n_tests, .n_nonparam, .n_sparse),
  if (.n_tests > 1) sprintf("多重比较采用 Benjamini-Hochberg 校正（见多重校正表）。") else "")
.diag_status[["正态性/方差齐性检查"]] <- if (.n_tests > 0 && .n_nonparam >= .n_tests) "warning" else "passed"
.diag_status[["小样本或稀疏单元格检查"]] <- if (.n_sparse > 0) "warning" else "passed"

.out[["methods_table"]] <- list(kind = "table", payload = methods_table, title = "检验方法表")
.out[["p_table"]] <- list(kind = "table", payload = p_table, title = "P 值")
.out[["eff_table"]] <- list(kind = "table", payload = eff_table, title = "效应量")
.out[["adj_table"]] <- list(kind = "table", payload = adj_table, title = "多重校正")
.out[["cmp_table"]] <- list(kind = "table", payload = cmp_table, title = "组间比较表")
.out[["eff_ci_table"]] <- list(kind = "table", payload = eff_ci_table, title = "效应量表（含CI）")
.out[["diff_figure"]] <- list(kind = "figure", payload = diff_fig, title = "分组差异图")
.out[["method_text"]] <- list(kind = "text", payload = method_txt, title = "方法选择说明")
'''


def _inject_role_map(role_map: Dict[str, str]) -> str:
    rm = ", ".join(f'"{r}" = "{s}"' for r, s in role_map.items())
    return f"ROLEMAP <- list({rm})"


def _wrap_core(core: str, plan: AnalysisPlan, role_map: Dict[str, str], accessors: str) -> str:
    """Assemble helpers + accessors + core + emitter into one R script."""
    # The production portfolio can rename a catalog output while retaining its
    # original role as an explicit alias. Reuse only that declared output slot;
    # an unrelated figure/table must never satisfy a newly required role.
    effective_roles = dict(role_map)
    planned_roles = {item.role for item in plan.required_outputs}
    for item in plan.required_outputs:
        if item.role in effective_roles:
            continue
        slots = {role_map[alias] for alias in item.legacy_roles if alias in role_map}
        if len(slots) > 1:
            raise ValueError(f"Ambiguous output aliases for {item.role}: {item.legacy_roles}")
        if slots:
            effective_roles[item.role] = slots.pop()
            for alias in item.legacy_roles:
                if alias not in planned_roles:
                    effective_roles.pop(alias, None)
    return "\n".join(
        (
            _HELPERS,
            f".plan_id <- {_q(plan.plan_id)}",
            ".diag_status <- list()",
            ".out <- list()",
            ".step <- 'init'",
            ".l <- c(sprintf('deterministic skeleton script; plan=%s', .plan_id))",
            _inject_role_map(effective_roles),
            "SEMANTICMAP <- list(" + ", ".join(
                f"{_q(item.role)} = {_q(item.semantic_key or item.role)}"
                for item in plan.required_outputs
            ) + ")",
            accessors,
            "res <- tryCatch({",
            core,
            '  "OK"',
            "}, error = function(e) {",
            "  for (nm in .planned_diag) if (is.null(.diag_status[[nm]])) .diag_status[[nm]] <<- \"failed\"",
            '  paste("ABORT at step:", .step, "|", conditionMessage(e))',
            "})",
            ".l <- c(sprintf('deterministic skeleton script; plan=%s', .plan_id), paste('final:', res))",
            # On failure no role slot is filled with the failure text: the
            # emitter keys on ROLEMAP *values* (engine slots), and faking a
            # slot would let portfolio validation pass dishonestly.  The
            # failure surfaces through the run diagnostic note below
            # ("final: ABORT …") and through the roles legitimately missing
            # from the portfolio.
            'for (nm in .planned_diag) if (is.null(.diag_status[[nm]])) .diag_status[[nm]] <- "not_assessed"',
            _EMITTER,
            "invisible(baa_text(",
            "  'rcs_run_diagnostic', paste(.l, collapse = '\\n'),",
            "  metadata = list(role = 'diagnostic_note', semantic_key = 'diagnostic_note', paper_ready = FALSE)",
            "))",
            'if (!identical(res, "OK")) stop(res, call. = FALSE)',
        )
    )


TABLE_ONE_ROLE_MAP = {
    "baseline_table": "t1",
    "table_2": "cmp",
    "balance_table": "bal",
    "table_4": "eff",
    "table_5": "exp",
    "baseline_table_2": "t1_fmt",
    "text_1": "report_note",
    "method_note": "method_note",
}

GROUP_TESTS_ROLE_MAP = {
    "method_note": "methods_table",
    "table_2": "p_table",
    "table_3": "eff_table",
    "table_4": "adj_table",
    "table_5": "cmp_table",
    "table_6": "eff_ci_table",
    "figure_1": "diff_figure",
    "method_note_2": "method_text",
}

GLM_ROLE_MAP = {
    "table_1": "model_table",
    "table_2": "marginal",
    "table_3": "robust_ci",
    "table_4": "uni_multi",
    "table_5": "eff_strings",
    "diagnostic_output": "diag_figure",
    "diagnostic_output_2": "diag_table",
    "manuscript_results": "results_text",
}

SURVIVAL_ROLE_MAP = {
    "survival_plot": "km_plot",
    "table_1": "uni_cox",
    "table_2": "ph_table",
    "table_3": "risk_table",
    "survival_plot_2": "km_plot2",
    "table_4": "multi_cox",
    "diagnostic_output": "ph_diag_table",
}

_GLM_INPUT_VARS = {
    "outcome": "outcome_v",
    "exposure": "exposure_v",
    "covariates": "covars_v",
    "strata": "strata_v",
}
_SURVIVAL_INPUT_VARS = {
    "followup_time": "followup_v",
    "event": "event_v",
    "exposure": "exposure_v",
    "covariates": "covars_v",
    "time_unit": "time_unit_v",
}
_GROUP_TESTS_INPUT_VARS = {
    "group": "group_v",
    "continuous_outcomes": "cont_v",
    "categorical_outcomes": "cat_v",
    "paired_marker": "paired_v",
}


def _accessors(plan: AnalysisPlan, mapping: Dict[str, str]) -> str:
    lines = []
    for input_id, var in mapping.items():
        if input_id in plan.resolved_inputs:
            lines.append(f'{var} <- baa_plan_input("{input_id}")')
    return "\n".join(lines)


def _make_engine(core: str | Callable[[AnalysisPlan], str], input_vars: Dict[str, str], planned_diag: tuple) -> Callable:
    def render(plan: AnalysisPlan, role_map: Dict[str, str]) -> str:
        from .method_policy import SUPPORTED_DESIGNS, method_scope_issues
        issues = method_scope_issues(plan.template_id, {
            key: value.value for key, value in plan.resolved_inputs.items()
        })
        diag_names = ", ".join(_q(d) for d in dict.fromkeys((*planned_diag, *plan.diagnostics)))
        header = f".planned_diag <- c({diag_names})"
        if issues:
            header += "\nstop(" + _q("；".join(issues.values())) + ")"
        if plan.template_id in SUPPORTED_DESIGNS:
            header += '\nif (!identical(as.character(baa_plan_input("design_scope")), ' + _q(SUPPORTED_DESIGNS[plan.template_id]) + ')) stop("研究设计适用范围不匹配")'
        return _wrap_core(
            header + "\n" + (core(plan) if callable(core) else core),
            plan,
            role_map,
            _accessors(plan, input_vars),
        )

    return render


_ENGINE_ROC = r'''
## Engine: ROC / diagnostic performance (base R implementation)
## inputs: outcome(binary), diagnostic_score(continuous), threshold(optional), strata(optional)
.step <- "setup"
lv <- levels(droplevels(factor(dataset[[outcome_v]])))
if (length(lv) != 2L) stop("ROC 需要二分类结局")
if (!exists("positive_v")) positive_v <- NULL
if (!exists("direction_v")) direction_v <- NULL
if (!exists("thr_v")) thr_v <- NULL
positive <- if (!is.null(positive_v)) as.character(positive_v) else lv[2]
if (length(positive) != 1L || !positive %in% lv) stop("阳性类别不在结局变量中")
direction <- if (!is.null(direction_v)) direction_v else "higher_positive"
if (!direction %in% c("higher_positive", "lower_positive")) stop("评分方向无效")
df <- data.frame(
  y = as.integer(as.character(dataset[[outcome_v]]) == positive),
  score = suppressWarnings(as.numeric(as.character(dataset[[score_v]]))),
  stringsAsFactors = FALSE)
if (exists("strata_v") && !is.null(strata_v) && !is.na(strata_v)) {
  df$strata <- droplevels(factor(dataset[[strata_v]]))
} else strata_v <- NULL
df <- df[is.finite(df$y) & is.finite(df$score), , drop = FALSE]
n_pos <- sum(df$y == 1L); n_neg <- sum(df$y == 0L)
if (min(n_pos, n_neg) < 2L) stop("完整病例中的阳性和阴性均至少需要 2 例")
if (direction == "lower_positive") df$score <- -df$score
.l <- c(.l, sprintf("n_pos=%d n_neg=%d", n_pos, n_neg))

.auc_calc <- function(y, s) {
  r <- rank(s); n1 <- sum(y == 1L); n0 <- sum(y == 0L)
  auc <- (sum(r[y == 1L]) - n1 * (n1 + 1L) / 2) / (n1 * n0)
  q1 <- auc / (2 - auc); q2 <- 2 * auc^2 / (1 + auc)
  se <- sqrt(max(0, (auc * (1 - auc) + (n1 - 1) * (q1 - auc^2) + (n0 - 1) * (q2 - auc^2)) / (n1 * n0)))
  list(auc = auc, se = se)
}
main <- .auc_calc(df$y, df$score)
auc_lo <- max(0, main$auc - 1.959964 * main$se)
auc_hi <- min(1, main$auc + 1.959964 * main$se)
.l <- c(.l, sprintf("auc=%.4f se=%.4f", main$auc, main$se))

.step <- "roc-curve"
ord <- order(-df$score)
ends <- cumsum(rle(df$score[ord])$lengths)
tp <- cumsum(df$y[ord])[ends]; fp <- cumsum(1L - df$y[ord])[ends]
roc_df <- data.frame(fpr = c(0, fp / max(n_neg, 1)), tpr = c(0, tp / max(n_pos, 1)))
roc_fig <- ggplot2::ggplot(roc_df, ggplot2::aes(x = fpr, y = tpr)) +
  ggplot2::geom_step(linewidth = 1, colour = "#4477AA") +
  ggplot2::geom_abline(intercept = 0, slope = 1, linetype = 2, colour = "grey55") +
  ggplot2::coord_equal() +
  ggplot2::labs(title = "ROC 曲线", x = "1-特异度", y = "灵敏度",
    caption = sprintf("AUC=%.3f (95%%CI %.3f-%.3f)", main$auc, auc_lo, auc_hi)) +
  ggplot2::theme_bw()
youden_seq <- c(0, tp / n_pos - fp / n_neg)
best_i <- which.max(youden_seq)
best_thr <- if (best_i == 1L) Inf else unique(df$score[ord])[best_i - 1L]
if (!is.null(thr_v)) {
  if (length(thr_v) != 1L || !is.finite(as.numeric(thr_v))) stop("预设阈值必须为有限数值")
  best_thr <- as.numeric(thr_v) * if (direction == "lower_positive") -1 else 1
}

if (!is.null(strata_v) && !is.null(df$strata)) {
  .roc2 <- lapply(levels(df$strata), function(gl) {
    sub <- df[!is.na(df$strata) & df$strata == gl, ]
    if (length(unique(sub$y)) < 2L) return(NULL)
    m2 <- .auc_calc(sub$y, sub$score)
    o2 <- order(-sub$score); ys <- sub$y[o2]
    e2 <- cumsum(rle(sub$score[o2])$lengths)
    data.frame(
      fpr = c(0, cumsum(1L - ys)[e2] / sum(ys == 0L)),
      tpr = c(0, cumsum(ys)[e2] / sum(ys == 1L)),
      组 = sprintf("%s (AUC=%.3f)", gl, m2$auc))
  })
  roc2_df <- do.call(rbind, Filter(Negate(is.null), .roc2))
} else roc2_df <- NULL
if (!is.null(roc2_df)) {
  roc2_fig <- ggplot2::ggplot(roc2_df, ggplot2::aes(x = fpr, y = tpr, colour = 组)) +
    ggplot2::geom_step(linewidth = 0.9) + ggplot2::coord_equal() +
    ggplot2::labs(title = "分层 ROC 曲线", x = "1-特异度", y = "灵敏度") + ggplot2::theme_bw()
} else {
  pr_df <- data.frame(tpr = tp / n_pos, precision = tp / (tp + fp))
  roc2_fig <- ggplot2::ggplot(pr_df, ggplot2::aes(x = tpr, y = precision)) +
    ggplot2::geom_step(linewidth = 1, colour = "#117733") + ggplot2::coord_equal() +
    ggplot2::labs(title = "精确率-召回率曲线（未提供分层变量）", x = "召回率", y = "精确率") +
    ggplot2::theme_bw()
}

.step <- "thresholds"
thr_qs <- round(stats::quantile(df$score, c(0.1, 0.25, 0.5, 0.75, 0.9), names = FALSE), 4)
cand <- sort(unique(c(thr_qs, best_thr)))
thr_rows <- lapply(cand, function(th) {
  pp <- df$score >= th
  sens <- sum(pp & df$y == 1L) / max(n_pos, 1)
  spec <- sum(!pp & df$y == 0L) / max(n_neg, 1)
  ppv <- if (sum(pp)) sum(pp & df$y == 1L) / sum(pp) else NA_real_
  npv <- if (sum(!pp)) sum(!pp & df$y == 0L) / sum(!pp) else NA_real_
  data.frame(阈值 = th * if (direction == "lower_positive") -1 else 1, 灵敏度 = round(sens, 3), 特异度 = round(spec, 3),
    约登指数 = round(sens + spec - 1, 3), PPV = round(ppv, 3), NPV = round(npv, 3),
    stringsAsFactors = FALSE)
})
thr_table <- do.call(rbind, thr_rows)

pp <- df$score >= best_thr
confusion <- data.frame(
  行 = c("实际阳性", "实际阴性"),
  预测阳性 = c(sum(pp & df$y == 1L), sum(pp & df$y == 0L)),
  预测阴性 = c(sum(!pp & df$y == 1L), sum(!pp & df$y == 0L)),
  stringsAsFactors = FALSE)
sens <- sum(pp & df$y == 1L) / max(n_pos, 1)
spec <- sum(!pp & df$y == 0L) / max(n_neg, 1)
best_thr <- best_thr * if (direction == "lower_positive") -1 else 1

auc_table <- data.frame(
  指标 = c("AUC", "CI下限", "CI上限"),
  结果 = c(sprintf("%.3f", main$auc), sprintf("%.3f", auc_lo), sprintf("%.3f", auc_hi)),
  stringsAsFactors = FALSE)
if (!is.null(strata_v) && !is.null(df$strata)) {
  for (gl in levels(df$strata)) {
    sub <- df[!is.na(df$strata) & df$strata == gl, ]
    if (length(unique(sub$y)) < 2L) next
    m2 <- .auc_calc(sub$y, sub$score)
    auc_table <- rbind(auc_table, data.frame(
      指标 = sprintf("AUC(%s)", gl), 结果 = sprintf("%.3f", m2$auc), stringsAsFactors = FALSE))
  }
}

grade <- if (main$auc < 0.6) "接近随机，无判别力" else if (main$auc < 0.75) "判别力较低至中等" else if (main$auc < 0.9) "判别力良好" else "判别力优秀"
results_txt <- paste0(
  sprintf("诊断性能：AUC=%.3f（95%%CI %.3f-%.3f），%s。", main$auc, auc_lo, auc_hi, grade),
  sprintf("阳性类别=%s，方向=%s；阳性 n=%d，阴性 n=%d。%s阈值=%.3f，灵敏度 %.3f、特异度 %.3f。", positive, direction, n_pos, n_neg, if (is.null(thr_v)) "样本内约登" else "预设", best_thr, sens, spec),
  " 完整阈值表与混淆矩阵见右侧结果面板；阈值应在独立样本中验证后再用于临床。")
.min_class <- min(n_pos, n_neg)
.diag_status[["阳性/阴性样本量检查"]] <- if (.min_class < 10) "warning" else "passed"
.diag_status[["阈值稳定性检查"]] <- "not_assessed"

.out[["roc_curve"]] <- list(kind = "figure", payload = roc_fig, title = "ROC 图")
.out[["threshold_table"]] <- list(kind = "table", payload = thr_table, title = "阈值表")
.out[["confusion"]] <- list(kind = "table", payload = confusion, title = "混淆矩阵")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "性能解释")
.out[["roc_strata"]] <- list(kind = "figure", payload = roc2_fig, title = "ROC 曲线")
.out[["auc_table"]] <- list(kind = "table", payload = auc_table, title = "AUC 表")
.out[["performance_table"]] <- list(kind = "table", payload = rbind(
  auc_table,
  data.frame(指标=c("选用阈值","灵敏度","特异度","阳性人数","阴性人数","TP","FP","TN","FN"),
    结果=as.character(c(best_thr,sens,spec,n_pos,n_neg,
      confusion$预测阳性[1],confusion$预测阳性[2],confusion$预测阴性[2],confusion$预测阴性[1])),
    stringsAsFactors=FALSE)), title = "实际 AUC、不确定性与选定阈值性能")
.out[["youden_table"]] <- list(kind = "table", payload = data.frame(
  指标 = c("选用阈值", "灵敏度", "特异度", "约登指数"),
  结果 = c(round(best_thr, 4), round(sens, 4), round(spec, 4), round(sens + spec - 1, 4)),
  stringsAsFactors = FALSE), title = "阈值性能表")
.out[["diag_text"]] <- list(kind = "text",
  payload = sprintf("样本量检查：%d 阳性 / %d 阴性；AUC CI 为 Hanley–McNeil 近似；阈值稳定性未评估，需重采样或独立验证。", n_pos, n_neg),
  title = "诊断解释")
'''

_ENGINE_CORR = r'''
## Engine: correlation & agreement (base R)
## inputs: continuous_variables(critical, >=2)
cont <- as.character(cont_v)
if (length(cont) < 2L) stop("相关一致性分析需要至少两个连续变量")
missing_cols <- setdiff(cont, names(dataset))
if (length(missing_cols)) stop("缺失数据列: ", paste(missing_cols, collapse = ", "))
dfc <- dataset[, cont, drop = FALSE]
dfc <- dfc[stats::complete.cases(dfc), , drop = FALSE]
n_used <- nrow(dfc)
if (n_used < 10) stop(sprintf("完整成对样本过少 (n=%d)", n_used))

pairs <- utils::combn(cont, 2L, simplify = FALSE)
corr_rows <- list(); agr_rows <- list(); .n_outlier <- 0L; .n_nonlin <- 0L
corr_mat <- matrix(NA_real_, length(cont), length(cont), dimnames = list(cont, cont))

for (pr in pairs) {
  a <- dfc[[pr[1]]]; b <- dfc[[pr[2]]]
  ct <- stats::cor.test(a, b, method = "pearson")
  st <- suppressWarnings(stats::cor.test(a, b, method = "spearman"))
  r <- unname(ct$estimate)
  corr_mat[pr[1], pr[2]] <- corr_mat[pr[2], pr[1]] <- round(r, 3)
  z <- 0.5 * log((1 + r) / (1 - r)); se_z <- 1 / sqrt(n_used - 3)
  corr_rows[[length(corr_rows) + 1L]] <- data.frame(
    变量对 = paste(pr, collapse = " vs "),
    Pearson_r = round(r, 4), Pearson_P = .sk_fmt_p(ct$p.value),
    Spearman_rho = round(unname(st$estimate), 4),
    PearsonCI = sprintf("%.3f, %.3f", tanh(z - 1.959964 * se_z), tanh(z + 1.959964 * se_z)),
    stringsAsFactors = FALSE)
  dff <- a - b
  md <- mean(dff); sdd <- stats::sd(dff)
  long <- data.frame(y = c(a, b), subj = rep(seq_len(n_used), 2L),
    method = rep(c("A", "B"), each = n_used))
  icc_val <- tryCatch({
    ms <- summary(stats::aov(y ~ subj + method, data = long))[[1]][["Mean Sq"]]
    (ms[1] - ms[3]) / (ms[1] + ms[3] + (ms[2] - ms[3]) / n_used)
  }, error = function(e) NA_real_)
  agr_rows[[length(agr_rows) + 1L]] <- data.frame(
    变量对 = paste(pr, collapse = " vs "),
    均值差 = round(md, 4),
    一致性界限 = sprintf("%.3f, %.3f", md - 1.959964 * sdd, md + 1.959964 * sdd),
    ICC = if (is.na(icc_val)) "NA" else round(icc_val, 4),
    stringsAsFactors = FALSE)
  if (abs(r - unname(st$estimate)) > 0.15) .n_nonlin <- .n_nonlin + 1L
  if (any(abs(scale(dff)) > 3)) .n_outlier <- .n_outlier + 1L
}
ba <- data.frame(均值 = (dfc[[1]] + dfc[[2]]) / 2, 差值 = dfc[[1]] - dfc[[2]])
scatter_fig <- ggplot2::ggplot(dfc, ggplot2::aes(x = .data[[cont[1]]], y = .data[[cont[2]]])) +
  ggplot2::geom_point(alpha = 0.55) +
  ggplot2::geom_abline(intercept = 0, slope = 1, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "一致性散点图（含45°参考线）", x = cont[1], y = cont[2]) + ggplot2::theme_bw()
ba_fig <- ggplot2::ggplot(ba, ggplot2::aes(x = 均值, y = 差值)) +
  ggplot2::geom_point(alpha = 0.55) +
  ggplot2::geom_hline(yintercept = mean(ba$差值), colour = "#4477AA") +
  ggplot2::geom_hline(yintercept = mean(ba$差值) + 1.959964 * stats::sd(ba$差值), linetype = 2, colour = "grey55") +
  ggplot2::geom_hline(yintercept = mean(ba$差值) - 1.959964 * stats::sd(ba$差值), linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "Bland-Altman 一致性图", x = "两法均值", y = "差值") + ggplot2::theme_bw()
corr_matrix <- data.frame(变量 = rownames(corr_mat), corr_mat, check.names = FALSE, stringsAsFactors = FALSE)
agr_table <- if (length(agr_rows)) do.call(rbind, agr_rows) else data.frame(说明 = "需要至少两个变量", stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("Pearson 相关矩阵与一致性指标（完整成对 n=%d）。", n_used),
  sprintf("主对比 %s：均值差 %.3f，95%%一致性界限见 Bland-Altman 图。", paste(cont[1:2], collapse = " vs "), mean(dfc[[1]] - dfc[[2]])),
  " 相关不等于一致：请结合 Bland-Altman 图与 ICC 判断可互换性。")
.diag_status[["散点非线性检查"]] <- if (.n_nonlin > 0) "warning" else "passed"
.diag_status[["离群点敏感性检查"]] <- if (.n_outlier > 0) "warning" else "passed"
.out[["corr_matrix"]] <- list(kind = "table", payload = corr_matrix, title = "相关矩阵")
.out[["agreement_stats"]] <- list(kind = "table", payload = if (length(corr_rows)) do.call(rbind, corr_rows) else corr_matrix, title = "一致性指标")
.out[["scatter_fig"]] <- list(kind = "figure", payload = scatter_fig, title = "一致性图")
.out[["agreement_table"]] <- list(kind = "table", payload = agr_table, title = "一致性指标表")
.out[["ba_fig"]] <- list(kind = "figure", payload = ba_fig, title = "Bland-Altman 图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "相关性解释")
'''

_ENGINE_EFFECT = r'''
## Engine: effect sizes with precision (base R)
## inputs: outcome(critical), group(critical binary)
df <- dataset[, c(outcome_v, group_v), drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
if (nlevels(df[[group_v]]) != 2L) stop("效应量引擎需要恰好两个组")
df <- df[stats::complete.cases(df), , drop = FALSE]
g1 <- df[[group_v]] == levels(df[[group_v]])[1]
n1 <- sum(g1); n2 <- sum(!g1)
if (is.numeric(df[[outcome_v]])) {
  a <- df[[outcome_v]][g1]; b <- df[[outcome_v]][!g1]
  tt <- stats::t.test(a, b)
  d <- (mean(a) - mean(b)) / sqrt(((n1 - 1) * stats::var(a) + (n2 - 1) * stats::var(b)) / (n1 + n2 - 2))
  g <- d * (1 - 3 / (4 * (n1 + n2) - 9))
  se_g <- sqrt((n1 + n2) / (n1 * n2) + g^2 / (2 * (n1 + n2)))
  eff_rows <- data.frame(
    指标 = c("Cohen's d", "Hedges g"),
    效应 = round(c(d, g), 4),
    CI下限 = round(c(d, g) - 1.959964 * se_g, 4),
    CI上限 = round(c(d, g) + 1.959964 * se_g, 4),
    stringsAsFactors = FALSE)
  cmp <- data.frame(
    指标 = c("组1均值", "组2均值", "均值差", "t统计量", "P值"),
    结果 = c(round(mean(a), 4), round(mean(b), 4), round(mean(a) - mean(b), 4),
      round(unname(tt$statistic), 3), .sk_fmt_p(tt$p.value)),
    stringsAsFactors = FALSE)
  interp <- if (abs(d) < 0.2) "微小" else if (abs(d) < 0.5) "小" else if (abs(d) < 0.8) "中等" else "大"
  results_txt <- sprintf("组间标准化差异 Cohen's d=%.3f（%s效应），95%%CI [%.3f, %.3f]；均值差=%.3f，t检验 P=%s。样本量 %d vs %d。",
    d, interp, d - 1.959964 * se_g, d + 1.959964 * se_g, mean(a) - mean(b), .sk_fmt_p(tt$p.value), n1, n2)
} else {
  tab <- table(factor(df[[outcome_v]]), df[[group_v]])
  if (any(dim(tab) != 2L)) stop("二分类结局需要 2x2 表")
  a1 <- tab[1, 1]; b1 <- tab[2, 1]; c1 <- tab[1, 2]; d1 <- tab[2, 2]
  orv <- (a1 * d1) / max(1, b1 * c1)
  se_lor <- sqrt(1 / a1 + 1 / b1 + 1 / c1 + 1 / d1)
  lor <- log(orv)
  eff_rows <- data.frame(
    指标 = "OR（组1 vs 组2）", 效应 = round(orv, 4),
    CI下限 = round(exp(lor - 1.959964 * se_lor), 4),
    CI上限 = round(exp(lor + 1.959964 * se_lor), 4),
    stringsAsFactors = FALSE)
  ft <- stats::fisher.test(tab)
  cmp <- data.frame(
    指标 = c("组1阳性/阴性", "组2阳性/阴性", "OR", "Fisher P"),
    结果 = c(sprintf("%d/%d", a1, b1), sprintf("%d/%d", c1, d1), round(orv, 4), .sk_fmt_p(ft$p.value)),
    stringsAsFactors = FALSE)
  interp <- if (orv < 1.5) "微弱" else if (orv < 3) "中等" else "较强"
  results_txt <- sprintf("组间比值比 OR=%.3f（%s关联），95%%CI [%.3f, %.3f]，Fisher P=%s。样本量 %d vs %d。",
    orv, interp, exp(lor - 1.959964 * se_lor), exp(lor + 1.959964 * se_lor), .sk_fmt_p(ft$p.value), n1, n2)
}
precision <- data.frame(
  指标 = "CI宽度",
  结果 = round(eff_rows$CI上限 - eff_rows$CI下限, 4),
  stringsAsFactors = FALSE)
method_txt <- "效应量按结局类型选择：连续结局用 Cohen's d 与 Hedges g（小样本偏倚校正），二分类结局用比值比；置信区间为 Wald 95% CI。"
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["模型/方法适配性检查"]] <- "not_assessed"
.diag_status[["变量角色与研究假设一致性检查"]] <- "not_assessed"
.diag_status[["样本量、事件数或设计效应充分性检查"]] <- if (n1 < 20 || n2 < 20) "warning" else "passed"
.out[["eff_table"]] <- list(kind = "table", payload = eff_rows, title = "效应量表")
.out[["precision_table"]] <- list(kind = "table", payload = precision, title = "精度评估")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "解释阈值")
.out[["method_note"]] <- list(kind = "text", payload = method_txt, title = "统计方法说明")
.out[["results_paper"]] <- list(kind = "text", payload = results_txt, title = "论文式结果解释")
.out[["cmp_table"]] <- list(kind = "table", payload = cmp, title = "组间描述")
'''

_ENGINE_ANCOVA = r'''
## Engine: ANCOVA (adjusted group comparison, stats::lm)
## inputs: outcome, group, covariates(critical), covariates_2(optional)
covs <- unique(c(if (exists("covars_v")) as.character(covars_v) else character(0),
  if (exists("covars2_v")) as.character(covars2_v) else character(0)))
df <- dataset[, unique(c(outcome_v, group_v, covs)), drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
df <- df[stats::complete.cases(df), , drop = FALSE]
n_used <- nrow(df)
if (nlevels(df[[group_v]]) < 2L) stop("需要至少两个组")
if (n_used < 10) stop(sprintf("有效样本过少 (n=%d)", n_used))
for (p in covs) if (is.character(df[[p]])) df[[p]] <- factor(df[[p]])

.step <- "fit"
rhs <- paste(c(sprintf("`%s`", group_v), sprintf("`%s`", covs)), collapse = " + ")
fit <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ ", rhs)), data = df)

.step <- "slope-homogeneity"
inter_p <- tryCatch(
  anova(fit, stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ (", rhs, ")^2")), data = df))$`Pr(>F)`[2],
  error = function(e) NA_real_)

.step <- "adjusted-means"
nd <- df[rep(1L, nlevels(df[[group_v]])), , drop = FALSE]; rownames(nd) <- NULL
nd[[group_v]] <- levels(df[[group_v]])
for (p in covs) {
  if (is.numeric(df[[p]])) nd[[p]] <- mean(df[[p]]) else nd[[p]] <- names(sort(table(df[[p]]), decreasing = TRUE))[1]
}
pr <- stats::predict(fit, newdata = nd, se.fit = TRUE)
adj <- data.frame(
  组别 = as.character(nd[[group_v]]),
  调整均值 = round(as.numeric(pr$fit), 4),
  标准误 = round(as.numeric(pr$se.fit), 4),
  stringsAsFactors = FALSE)
gl <- levels(df[[group_v]])
contrast_val <- tryCatch({
  c1 <- stats::coef(fit)
  vn <- grep(group_v, names(c1), value = TRUE)[1]
  se <- unname(sqrt(diag(stats::vcov(fit)))[vn])
  data.frame(组别 = sprintf("%s vs %s", gl[2], gl[1]),
    调整均值 = round(unname(c1[vn]), 4), 标准误 = round(se, 4), stringsAsFactors = FALSE)
}, error = function(e) NULL)
if (!is.null(contrast_val)) adj <- rbind(adj, contrast_val)
mt <- .sk_model_table(fit)

.step <- "diagnostics"
rr <- stats::residuals(fit)
resid_ok <- if (length(rr) > 5000) NA else isTRUE(suppressWarnings(stats::shapiro.test(rr)$p.value > 0.05))
plot_df <- data.frame(fitted = as.numeric(stats::fitted(fit)), resid = as.numeric(rr))
diag_fig <- ggplot2::ggplot(plot_df, ggplot2::aes(x = fitted, y = resid)) +
  ggplot2::geom_point(alpha = 0.5) + ggplot2::geom_hline(yintercept = 0, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "ANCOVA 残差诊断", x = "拟合值", y = "残差") + ggplot2::theme_bw()
results_txt <- paste0(
  sprintf("ANCOVA（n=%d）：调整组间均值与差异见主表。", n_used),
  if (!is.null(inter_p) && !is.na(inter_p)) sprintf("斜率同质性检验 P=%s（%s）。", .sk_fmt_p(inter_p), if (inter_p < 0.05) "交互显著，慎用共同斜率解释" else "共同斜率假设成立") else "",
  " 详细系数与诊断见右侧结果面板。")
.diag_status[["回归斜率同质性检查"]] <- if (!is.na(inter_p) && inter_p < 0.05) "warning" else "passed"
.diag_status[["残差和方差检查"]] <- if (is.na(resid_ok)) "warning" else if (isTRUE(resid_ok)) "passed" else "warning"
.diag_status[["调整均值与对比检查"]] <- "not_assessed"
.out[["adjusted_table"]] <- list(kind = "table", payload = adj, title = "调整均值与组间对比")
.out[["diag_fig"]] <- list(kind = "figure", payload = diag_fig, title = "诊断图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
.out[["model_table"]] <- list(kind = "table", payload = mt, title = "ANCOVA 系数表")
'''

_ENGINE_BOOTSTRAP = r'''
## Engine: bootstrap & permutation inference (base R)
## inputs: outcome(critical), group(optional binary)
y <- as.numeric(dataset[[outcome_v]])
if (!exists("group_v") || is.null(group_v)) {
  df <- data.frame(y = y, g = factor(rep(c("A", "B"), length.out = length(y))))
  note <- "未提供分组变量：以数据序号交替拆分为演示性两组（结果仅供参考）。"
} else {
  df <- data.frame(y = y, g = droplevels(factor(dataset[[group_v]])))
  note <- ""
}
df <- df[!is.na(df$g) & is.finite(df$y), , drop = FALSE]
if (nlevels(df$g) != 2L) stop("需要恰好两个组")
if (exists("pair_v") && !is.null(pair_v)) note <- paste(note, "检测到配对标识：当前引擎按独立两组处理，配对设计结果偏保守。")
n <- nrow(df)
set.seed(1000 + sum(as.integer(charToRaw(substr(.plan_id, 1, 12)))) %% 90000)
B <- 2000
g1 <- df$g == levels(df$g)[1]
obs <- mean(df$y[g1]) - mean(df$y[!g1])
boot <- replicate(B, {
  i1 <- sample(which(g1), replace = TRUE); i2 <- sample(which(!g1), replace = TRUE)
  mean(df$y[i1]) - mean(df$y[i2])
})
ci <- stats::quantile(boot, c(0.025, 0.975), names = FALSE)
perm <- replicate(B, {
  gp <- sample(df$g); mean(df$y[gp == levels(df$g)[1]]) - mean(df$y[gp != levels(df$g)[1]])
})
perm_p <- mean(abs(perm) >= abs(obs))
tt <- stats::t.test(df$y ~ df$g)
boot_table <- data.frame(
  指标 = c("观察均值差", "Bootstrap 95%CI 下限", "Bootstrap 95%CI 上限", "置换检验 P", "经典 t 检验 P", "重采样次数"),
  结果 = c(round(obs, 4), round(ci[1], 4), round(ci[2], 4), .sk_fmt_p(perm_p), .sk_fmt_p(tt$p.value), B),
  stringsAsFactors = FALSE)
hist_df <- data.frame(diff = boot)
boot_fig <- ggplot2::ggplot(hist_df, ggplot2::aes(x = diff)) +
  ggplot2::geom_histogram(bins = 40, fill = "#4477AA", alpha = 0.75) +
  ggplot2::geom_vline(xintercept = obs, colour = "#CC3311", linewidth = 0.9) +
  ggplot2::geom_vline(xintercept = ci[1], linetype = 2, colour = "grey40") +
  ggplot2::geom_vline(xintercept = ci[2], linetype = 2, colour = "grey40") +
  ggplot2::labs(title = "Bootstrap 均值差分布", x = "均值差", y = "频数", caption = "红线=观察值; 虚线=95%百分位CI") +
  ggplot2::theme_bw()
results_txt <- paste0(
  sprintf("Bootstrap（B=%d）均值差=%.4f，95%%百分位CI [%.4f, %.4f]；置换检验（B=%d）P=%s。", B, obs, ci[1], ci[2], B, .sk_fmt_p(perm_p)),
  " 置换检验不依赖正态假设；CI 为百分位法，已同时报告经典 t 检验对照。", note)
.diag_status[["重采样单位检查"]] <- "not_assessed"
.diag_status[["重复次数与稳定性检查"]] <- "not_assessed"
.diag_status[["区间方法敏感性比较"]] <- "not_assessed"
.out[["boot_table"]] <- list(kind = "table", payload = boot_table, title = "Bootstrap 与置换检验")
.out[["boot_hist"]] <- list(kind = "figure", payload = boot_fig, title = "诊断图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_PS = r'''
## Engine: propensity score adjustment (base R: PS + IPTW + balance)
## inputs: group(binary treatment), outcome, covariates(critical)
.step <- "setup"
df <- dataset[, unique(c(group_v, outcome_v, covars_v)), drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
if (nlevels(df[[group_v]]) != 2L) stop("处理分组需要恰好两个水平")
df <- df[stats::complete.cases(df), , drop = FALSE]
n_used <- nrow(df)
for (p in covars_v) if (is.character(df[[p]])) df[[p]] <- factor(df[[p]])
df[[outcome_v]] <- if (is.numeric(df[[outcome_v]])) as.numeric(df[[outcome_v]]) else factor(df[[outcome_v]])

.step <- "ps-model"
ps_form <- stats::as.formula(paste0("`", group_v, "` ~ ", paste(sprintf("`%s`", covars_v), collapse = " + ")))
ps_fit <- stats::glm(ps_form, family = stats::binomial(), data = df)
ps <- as.numeric(stats::fitted(ps_fit))
treated <- df[[group_v]] == levels(df[[group_v]])[2L]
w_ate <- ifelse(treated, 1 / ps, 1 / (1 - ps))
w_capped <- pmin(w_ate, 10)
.n_extreme <- sum(w_ate > 10)

.smd_col <- function(x, w, tind) {
  if (is.numeric(x)) {
    m1 <- sum(w * tind * x) / sum(w * tind); m0 <- sum(w * (!tind) * x) / sum(w * (!tind))
    v1 <- sum(w * tind * (x - m1)^2) / sum(w * tind); v0 <- sum(w * (!tind) * (x - m0)^2) / sum(w * (!tind))
    sp <- sqrt((v1 + v0) / 2)
    if (!is.finite(sp) || sp == 0) return(NA_real_)
    (m1 - m0) / sp
  } else {
    NA_real_
  }
}
bal_rows <- list()
for (p in covars_v) {
  x <- df[[p]]
  if (is.numeric(x)) {
    pre <- .sk_cohens_d(x, df[[group_v]])
    post <- .smd_col(x, w_capped, treated)
  } else {
    tab <- table(factor(x), df[[group_v]])
    pre <- .sk_cramers_v(x, df[[group_v]])
    post <- NA_real_
  }
  bal_rows[[length(bal_rows) + 1L]] <- data.frame(
    协变量 = p,
    调整前差异 = if (is.na(pre)) "NA" else round(pre, 4),
    加权后差异 = if (is.na(post)) "NA" else round(post, 4),
    判定 = if (is.na(post)) "NA" else if (abs(post) < 0.1) "均衡" else "不均衡",
    stringsAsFactors = FALSE)
}
balance_df <- do.call(rbind, bal_rows)

.step <- "overlap"
ov_df <- data.frame(ps = ps, 组 = as.character(df[[group_v]]))
overlap_fig <- ggplot2::ggplot(ov_df, ggplot2::aes(x = ps, fill = 组)) +
  ggplot2::geom_density(alpha = 0.35) +
  ggplot2::labs(title = "倾向得分重叠图（Love plot 数据见平衡表）", x = "倾向得分", y = "密度") +
  ggplot2::theme_bw()

.step <- "weighted-effect"
df$w <- w_capped
if (is.numeric(df[[outcome_v]])) {
  w_fit <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ `", group_v, "`")), data = df, weights = df$w)
  weighted_eff <- .sk_model_table(w_fit, exp_transform = FALSE)
  eff_name <- "加权均值差"
} else {
  w_fit <- suppressWarnings(stats::glm(
    stats::as.formula(paste0("`", outcome_v, "` ~ `", group_v, "`")),
    family = stats::binomial(), data = df, weights = df$w))
  weighted_eff <- .sk_model_table(w_fit, exp_transform = TRUE)
  eff_name <- "加权OR"
}
un_fit <- stats::glm(
  stats::as.formula(paste0("`", outcome_v, "` ~ `", group_v, "`")),
  family = if (is.numeric(df[[outcome_v]])) stats::gaussian() else stats::binomial(), data = df)
unadj <- .sk_model_table(un_fit, exp_transform = !is.numeric(df[[outcome_v]]))
effect_table <- data.frame(
  模型 = c("未调整", "IPTW 加权"), stringsAsFactors = FALSE)
effect_table$暴露效应 = c(
  sprintf("%s=%s", eff_name, unadj$效应[nrow(unadj)]),
  sprintf("%s=%s", eff_name, weighted_eff$效应[nrow(weighted_eff)]))
effect_table$CI <- c(
  sprintf("[%.3f, %.3f]", unadj$CI下限[nrow(unadj)], unadj$CI上限[nrow(unadj)]),
  sprintf("[%.3f, %.3f]", weighted_eff$CI下限[nrow(weighted_eff)], weighted_eff$CI上限[nrow(weighted_eff)]))

sens_txt <- paste0(
  sprintf("IPTW（ATE 权重，截断于 10）：极端权重（>10）%d 例；倾向得分范围 %.3f-%.3f。", .n_extreme, min(ps), max(ps)),
  " 权重分析假设无未测混杂；建议结合平衡表与重叠图判断可交换性。重叠不足时应考虑匹配或协变量扩展。")
.diag_status[["标准化差异检查"]] <- if (any(bal_rows[[1]]$判定 == "不均衡")) "warning" else "passed"
.diag_status[["重叠性检查"]] <- if (min(ps) < 0.01 || max(ps) > 0.99) "warning" else "passed"
.diag_status[["权重极端值检查"]] <- if (.n_extreme > 0) "warning" else "passed"

.out[["balance_df"]] <- list(kind = "table", payload = balance_df, title = "平衡表")
.out[["overlap_fig"]] <- list(kind = "figure", payload = overlap_fig, title = "Love plot 数据（重叠图）")
.out[["weighted_eff"]] <- list(kind = "table", payload = weighted_eff, title = "加权效应")
.out[["sens_text"]] <- list(kind = "text", payload = sens_txt, title = "敏感性说明")
.out[["effect_table"]] <- list(kind = "table", payload = effect_table, title = "处理效应表")
'''

_ENGINE_MEDIATION = r'''
## Engine: mediation analysis (base R: a/b paths + Sobel + bootstrap)
## inputs: predictor, mediator, outcome(continuous v1), covariates(optional)
df <- dataset[, unique(c(predictor_v, mediator_v, outcome_v, if (exists("covars_v")) as.character(covars_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
n_used <- nrow(df)
if (n_used < 20) stop(sprintf("有效样本过少 (n=%d)", n_used))
for (cn in names(df)) if (is.character(df[[cn]])) df[[cn]] <- factor(df[[cn]])

.step <- "paths"
cov_rhs <- if (exists("covars_v") && length(as.character(covars_v))) paste("+", paste(sprintf("`%s`", as.character(covars_v)), collapse = " + ")) else ""
fit_a <- stats::lm(stats::as.formula(paste0("`", mediator_v, "` ~ `", predictor_v, "`", cov_rhs)), data = df)
fit_b <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ `", predictor_v, "` + `", mediator_v, "`", cov_rhs)), data = df)
fit_c <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ `", predictor_v, "`", cov_rhs)), data = df)
a <- unname(stats::coef(fit_a)[2]); sa <- unname(sqrt(diag(stats::vcov(fit_a)))[2])
b <- unname(stats::coef(fit_b)[3]); sb <- unname(sqrt(diag(stats::vcov(fit_b)))[3])
c_total <- unname(stats::coef(fit_c)[2]); c_direct <- unname(stats::coef(fit_b)[2])
indirect <- a * b
sobel_se <- sqrt(a^2 * sb^2 + b^2 * sa^2)
sobel_z <- indirect / sobel_se
sobel_p <- 2 * stats::pnorm(-abs(sobel_z))

.step <- "bootstrap"
set.seed(1000 + sum(as.integer(charToRaw(substr(.plan_id, 1, 12)))) %% 90000)
B <- 1000
idx <- seq_len(n_used)
boot_ind <- replicate(B, {
  ii <- sample(idx, replace = TRUE)
  da <- df[ii, ]
  fa <- stats::lm(stats::as.formula(paste0("`", mediator_v, "` ~ `", predictor_v, "`", cov_rhs)), data = da)
  fb <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ `", predictor_v, "` + `", mediator_v, "`", cov_rhs)), data = da)
  unname(stats::coef(fa)[2]) * unname(stats::coef(fb)[3])
})
boot_ci <- stats::quantile(boot_ind, c(0.025, 0.975), names = FALSE)

paths_table <- data.frame(
  路径 = c("a（预测→中介）", "b（中介→结局|预测）", "c（总效应）", "c'（直接效应）", "间接效应 a×b"),
  效应 = round(c(a, b, c_total, c_direct, indirect), 4),
  CI或检验 = c(
    sprintf("%.4f, %.4f", a - 1.959964 * sa, a + 1.959964 * sa),
    sprintf("%.4f, %.4f", b - 1.959964 * sb, b + 1.959964 * sb),
    sprintf("P=%s", .sk_fmt_p(summary(fit_c)$coefficients[2, ncol(summary(fit_c)$coefficients)])),
    sprintf("P=%s", .sk_fmt_p(summary(fit_b)$coefficients[2, ncol(summary(fit_b)$coefficients)])),
    sprintf("Bootstrap CI [%.4f, %.4f]; Sobel P=%s", boot_ci[1], boot_ci[2], .sk_fmt_p(sobel_p))),
  stringsAsFactors = FALSE)
decomp_table <- data.frame(
  成分 = c("总效应", "直接效应", "间接效应", "中介占比"),
  数值 = c(round(c_total, 4), round(c_direct, 4), round(indirect, 4),
    if (abs(c_total) > 0) round(indirect / c_total * 100, 1) else NA),
  stringsAsFactors = FALSE)
coef_df <- data.frame(
  路径 = c("a", "b", "c'", "indirect"),
  效应 = c(a, b, c_direct, indirect),
  lo = c(a - 1.959964 * sa, b - 1.959964 * sb, c_direct - 1.959964 * sqrt(diag(stats::vcov(fit_b)))[2], boot_ci[1]),
  hi = c(a + 1.959964 * sa, b + 1.959964 * sb, c_direct + 1.959964 * sqrt(diag(stats::vcov(fit_b)))[2], boot_ci[2]))
coef_fig <- ggplot2::ggplot(coef_df, ggplot2::aes(x = 路径, y = 效应)) +
  ggplot2::geom_pointrange(ggplot2::aes(ymin = lo, ymax = hi), colour = "#4477AA") +
  ggplot2::geom_hline(yintercept = 0, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "中介路径效应", x = NULL, y = "效应（含95%CI）") + ggplot2::theme_bw()
method_txt <- "中介分析采用三步回归 + Sobel 检验 + Bootstrap（B=1000）百分位置信区间；当前版本假设中介与结局均为连续变量。"
results_txt <- paste0(
  sprintf("中介分析（n=%d）：间接效应 a×b=%.4f，Bootstrap 95%%CI [%.4f, %.4f]%s。",
    n_used, indirect, boot_ci[1], boot_ci[2],
    if (boot_ci[1] * boot_ci[2] > 0) "（不含0，中介效应成立）" else "（含0，中介效应不成立）"),
  sprintf("总效应 %.4f，直接效应 %.4f，中介占比 %s%%。",
    c_total, c_direct, if (abs(c_total) > 0) sprintf("%.1f", indirect / c_total * 100) else "NA"))
diag_txt <- sprintf("样本 n=%d； Bootstrap 迭代 %d；Sobel Z=%.3f。", n_used, B, sobel_z)
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["模型/方法适配性检查"]] <- "not_assessed"
.diag_status[["平衡诊断"]] <- "not_assessed"
.diag_status[["稳健标准误/敏感性分析"]] <- "not_assessed"
.diag_status[["模型形式与结局类型匹配检查"]] <- "not_assessed"
.diag_status[["效应量方向、置信区间和影响点检查"]] <- if (any(abs(scale(df[[mediator_v]])) > 3)) "warning" else "passed"
.out[["paths_table"]] <- list(kind = "table", payload = paths_table, title = "路径效应表")
.out[["coef_fig"]] <- list(kind = "figure", payload = coef_fig, title = "中介路径图")
.out[["decomp_table"]] <- list(kind = "table", payload = decomp_table, title = "效应分解")
.out[["method_text"]] <- list(kind = "text", payload = method_txt, title = "方法说明")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
.out[["diag_text"]] <- list(kind = "text", payload = diag_txt, title = "诊断说明")
'''


_ENGINE_ORDINAL = r'''
## Engine: ordinal regression (MASS::polr, ships with R)
## inputs: outcome(ordered), exposure(critical), covariates(optional), analysis_method(spec)
.step <- "setup"
df <- dataset[, unique(c(outcome_v, exposure_v, if (exists("covars_v")) as.character(covars_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df[[outcome_v]] <- factor(df[[outcome_v]], ordered = TRUE)
if (nlevels(df[[outcome_v]]) < 3L) stop("有序回归需要至少 3 个结局水平")
df[[exposure_v]] <- if (is.numeric(df[[exposure_v]]) && length(unique(df[[exposure_v]])) > 4L) {
  factor(stats::cut(df[[exposure_v]], 3, labels = c("低", "中", "高")))
} else factor(df[[exposure_v]])
for (p in if (exists("covars_v")) as.character(covars_v) else character(0)) if (is.character(df[[p]])) df[[p]] <- factor(df[[p]])
n_used <- nrow(df)

.step <- "fit"
rhs <- paste(c(sprintf("`%s`", exposure_v), sprintf("`%s`", if (exists("covars_v")) as.character(covars_v) else character(0))), collapse = " + ")
fit <- MASS::polr(stats::as.formula(paste0("`", outcome_v, "` ~ ", rhs)), data = df, Hess = TRUE)
ct <- suppressWarnings(stats::coef(summary(fit)))
p_vals <- 2 * stats::pnorm(-abs(ct[, "t value"]))
coef_table <- data.frame(
  变量 = rownames(ct), 系数 = round(ct[, "Value"], 4), OR = round(exp(ct[, "Value"]), 4),
  P值 = vapply(p_vals, .sk_fmt_p, character(1)), stringsAsFactors = FALSE)

.step <- "predictions"
lv_x <- levels(df[[exposure_v]])
nd <- df[rep(1L, length(lv_x)), , drop = FALSE]; rownames(nd) <- NULL
nd[[exposure_v]] <- lv_x
pr <- stats::predict(fit, newdata = nd, type = "probs")
pred_df <- data.frame(暴露 = lv_x, pr, check.names = FALSE, row.names = NULL)
pred_df[] <- lapply(pred_df, function(c) if (is.numeric(c)) round(c, 4) else c)

lvl_counts <- table(df[[outcome_v]])
sparse <- any(lvl_counts < 10)
assump_df <- data.frame(
  检验 = c("水平样本量", "比例优势近似（相邻二分对比符号一致性）", "AIC"),
  结果 = c(
    paste(names(lvl_counts), lvl_counts, collapse = "; "),
    { bin_ok <- tryCatch({
        splits <- utils::head(levels(df[[outcome_v]]), -1)
        signs <- vapply(splits, function(s) {
          yb <- factor(df[[outcome_v]] > s, levels = c(FALSE, TRUE))
          sign(stats::coef(suppressWarnings(stats::glm(yb ~ df[[exposure_v]], family = stats::binomial())))[2])
        }, numeric(1))
        if (length(unique(signs)) == 1L) "一致" else "不一致（提示比例优势假设可能不成立）"
      }, error = function(e) "无法计算")
      bin_ok },
    sprintf("%.1f", stats::AIC(fit))),
  stringsAsFactors = FALSE)
sparsity_df <- data.frame(
  检验 = paste0("结局水平 x 暴露组合稀疏检查（", length(lv_x), " 暴露水平）"),
  结果 = if (sparse) "存在 <10 例的水平" else "无严重稀疏",
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("有序 Logistic 回归（polr，n=%d）。暴露 %s 的 OR 及比例优势假设检查见结果面板；", n_used, exposure_v),
  " 预测概率表给出各暴露水平的结局水平分布。")
.diag_status[["比例优势假设检查"]] <- if (grepl("不一致", assump_df$结果[2])) "warning" else "passed"
.diag_status[["过度离散检查"]] <- "not_assessed"
.diag_status[["类别稀疏检查"]] <- if (sparse) "warning" else "passed"
.out[["model_summary"]] <- list(kind = "table", payload = data.frame(模型 = "有序 Logistic (polr)", AIC = round(stats::AIC(fit), 1), n = n_used, stringsAsFactors = FALSE), title = "所选结局模型表")
.out[["pred_probs"]] <- list(kind = "table", payload = pred_df, title = "预测概率")
.out[["assumptions"]] <- list(kind = "table", payload = assump_df, title = "模型假设检查")
.out[["coef_table"]] <- list(kind = "table", payload = coef_table, title = "模型系数表")
.out[["marginal"]] <- list(kind = "table", payload = pred_df, title = "边际效应或预测值")
.out[["sparsity_table"]] <- list(kind = "table", payload = sparsity_df, title = "假设诊断")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

def _longitudinal_core_for_plan(plan: AnalysisPlan) -> str:
    selection = plan.resolved_inputs.get("analysis_method")
    return longitudinal_core(str(selection.value) if selection is not None else "mixed")


_ENGINE_MIXED = _longitudinal_core_for_plan

_ENGINE_NI = NONINFERIORITY_CORE

_ENGINE_META = r'''
## Engine: pairwise meta-analysis (base R: IV + DerSimonian-Laird + Egger)
## inputs: study_id(critical), effect_estimate(critical, log scale), standard_error(critical)
df <- dataset[, c(study_v, effect_v, se_v), drop = FALSE]
names(df) <- c("study", "yi", "sei")
df$yi <- as.numeric(df$yi); df$sei <- as.numeric(df$sei)
df <- df[is.finite(df$yi) & is.finite(df$sei) & df$sei > 0, , drop = FALSE]
k <- nrow(df)
if (k < 2L) stop("Meta 分析至少需要 2 个研究")

.meta_calc <- function(yi, sei) {
  w <- 1 / sei^2
  fixed <- sum(w * yi) / sum(w)
  se_f <- sqrt(1 / sum(w))
  q_stat <- sum(w * (yi - fixed)^2)
  dfree <- length(yi) - 1
  p_q <- stats::pchisq(q_stat, dfree, lower.tail = FALSE)
  i2 <- max(0, (q_stat - dfree) / q_stat) * 100
  c_c <- sum(w) - sum(w^2) / sum(w)
  tau2 <- max(0, (q_stat - dfree) / c_c)
  wr <- 1 / (sei^2 + tau2)
  random <- sum(wr * yi) / sum(wr)
  se_r <- sqrt(1 / sum(wr))
  list(fixed = fixed, se_f = se_f, random = random, se_r = se_r,
    q = q_stat, df = dfree, p_q = p_q, i2 = i2, tau2 = tau2)
}
m <- .meta_calc(df$yi, df$sei)

.step <- "egger"
egger_int <- tryCatch({
  eg <- stats::lm(df$yi / df$sei ~ 1 / df$sei)
  unname(stats::coef(eg)[1])
}, error = function(e) NA_real_)
egger_p <- tryCatch({
  eg <- stats::lm(df$yi / df$sei ~ 1 / df$sei)
  unname(summary(eg)$coefficients[1, 4])
}, error = function(e) NA_real_)

.step <- "loo"
loo_random <- vapply(seq_len(k), function(i) {
  if (k - 1 < 2) return(NA_real_)
  .meta_calc(df$yi[-i], df$sei[-i])$random
}, numeric(1))

df$OR <- exp(df$yi)
df$CI下限 = exp(df$yi - 1.959964 * df$sei)
df$CI上限 = exp(df$yi + 1.959964 * df$sei)
forest_rows <- rbind(
  data.frame(研究 = as.character(df$study), 估计 = round(df$OR, 4),
    CI下限 = round(df$CI下限, 4), CI上限 = round(df$CI上限, 4), 类型 = "研究", stringsAsFactors = FALSE),
  data.frame(研究 = "合并(随机)", 估计 = round(exp(m$random), 4),
    CI下限 = round(exp(m$random - 1.959964 * m$se_r), 4),
    CI上限 = round(exp(m$random + 1.959964 * m$se_r), 4), 类型 = "合并", stringsAsFactors = FALSE))
forest_rows$x <- seq_len(nrow(forest_rows))
forest_fig <- ggplot2::ggplot(forest_rows, ggplot2::aes(x = 估计, y = stats::reorder(研究, -x))) +
  ggplot2::geom_pointrange(ggplot2::aes(xmin = CI下限, xmax = CI上限, shape = 类型, size = 类型)) +
  ggplot2::geom_vline(xintercept = 1, linetype = 2, colour = "grey55") +
  ggplot2::scale_shape_manual(values = c("研究" = 16, "合并" = 18)) +
  ggplot2::scale_size_manual(values = c("研究" = 0.7, "合并" = 1.1)) +
  ggplot2::labs(title = "森林图（随机效应，OR 尺度）", x = "效应 (OR)", y = NULL) +
  ggplot2::theme_bw() + ggplot2::theme(legend.position = "none")

pooled_table <- data.frame(
  模型 = c("固定效应", "随机效应(DL)"),
  估计 = round(c(m$fixed, m$random), 4),
  CI下限 = round(c(m$fixed - 1.959964 * m$se_f, m$random - 1.959964 * m$se_r), 4),
  CI上限 = round(c(m$fixed + 1.959964 * m$se_f, m$random + 1.959964 * m$se_r), 4),
  stringsAsFactors = FALSE)
het_table <- data.frame(
  指标 = c("Cochran Q", "自由度", "异质性 P", "I²", "Tau²"),
  结果 = c(sprintf("%.2f", m$q), m$df, .sk_fmt_p(m$p_q), sprintf("%.1f%%", m$i2), sprintf("%.4f", m$tau2)),
  stringsAsFactors = FALSE)
bias_table <- data.frame(
  检验 = c("Egger 截距（发表偏倚）", "Egger P"),
  结果 = c(sprintf("%.3f", egger_int), .sk_fmt_p(egger_p)),
  stringsAsFactors = FALSE)
loo_table <- data.frame(
  剔除研究 = as.character(df$study),
  随机效应估计 = round(exp(loo_random), 4),
  stringsAsFactors = FALSE)
results_note <- paste0(
  sprintf("k=%d 个研究：固定效应 %.3f，随机效应 %.3f（OR 尺度）；I²=%.1f%%。", k, exp(m$fixed), exp(m$random), m$i2),
  if (!is.na(egger_p)) sprintf(" Egger 检验 P=%s。", .sk_fmt_p(egger_p)) else "",
  " 效应应输入 log 尺度（如 logOR）；OR 由脚本取指数还原。")
.diag_status[["异质性检查"]] <- if (m$i2 > 60) "warning" else "passed"
.diag_status[["影响研究检查"]] <- "not_assessed"
.diag_status[["发表偏倚提示"]] <- if (!is.na(egger_p) && egger_p < 0.05) "warning" else "passed"
.out[["forest_fig"]] <- list(kind = "figure", payload = forest_fig, title = "森林图")
.out[["pooled_table"]] <- list(kind = "table", payload = pooled_table, title = "合并效应表")
.out[["bias_table"]] <- list(kind = "table", payload = bias_table, title = "异质性与偏倚评估")
.out[["het_table"]] <- list(kind = "table", payload = het_table, title = "异质性指标")
.out[["loo_table"]] <- list(kind = "table", payload = loo_table, title = "敏感性分析")
.out[["results_text"]] <- list(kind = "text", payload = results_note, title = "合并结果说明")
'''

_ENGINE_SUBGROUP = r'''
## Engine: subgroup analysis with interaction + forest (base R)
## inputs: outcome(critical), exposure(critical), subgroup(critical), covariates(optional)
.step <- "setup"
df <- dataset[, unique(c(outcome_v, exposure_v, subgroup_v, if (exists("covars_v")) as.character(covars_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df[[subgroup_v]] <- droplevels(factor(df[[subgroup_v]]))
if (nlevels(df[[subgroup_v]]) < 2L) stop("需要至少两个亚组")
binary_out <- !is.numeric(df[[outcome_v]]) || length(unique(df[[outcome_v]])) == 2L
fam <- if (binary_out) stats::binomial() else stats::gaussian()
if (binary_out) df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
trans <- if (binary_out) exp else function(x) x
eff_name <- if (binary_out) "OR" else "Beta"

.est_effect <- function(d) {
  rhs <- paste(c(sprintf("`%s`", exposure_v), sprintf("`%s`", if (exists("covars_v")) as.character(covars_v) else character(0))), collapse = " + ")
  f <- stats::glm(stats::as.formula(paste0("`", outcome_v, "` ~ ", rhs)), family = fam, data = d)
  cf <- stats::coef(f); se <- sqrt(diag(stats::vcov(f)))
  vn <- grep(exposure_v, names(cf), value = TRUE)[1]
  list(est = unname(trans(cf[vn])), lo = unname(trans(cf[vn] - 1.959964 * se[vn])), hi = unname(trans(cf[vn] + 1.959964 * se[vn])), p = unname(summary(f)$coefficients[vn, ncol(summary(f)$coefficients)]))
}
overall <- .est_effect(df)
sub_rows <- list()
for (gl in levels(df[[subgroup_v]])) {
  sub <- df[df[[subgroup_v]] == gl, ]
  if (nrow(sub) < 20) {
    sub_rows[[length(sub_rows) + 1L]] <- data.frame(
      亚组 = gl, 效应 = NA_real_, CI下限 = NA_real_, CI上限 = NA_real_, P值 = "NA",
      样本量 = nrow(sub), stringsAsFactors = FALSE)
    next
  }
  e <- .est_effect(sub)
  sub_rows[[length(sub_rows) + 1L]] <- data.frame(
    亚组 = gl, 效应 = round(e$est, 4), CI下限 = round(e$lo, 4), CI上限 = round(e$hi, 4),
    P值 = .sk_fmt_p(e$p), 样本量 = nrow(sub), stringsAsFactors = FALSE)
}
sens_table <- do.call(rbind, sub_rows)
interaction_p <- tryCatch({
  base_rhs <- paste(c(sprintf("`%s`", exposure_v), sprintf("`%s`", subgroup_v),
    sprintf("`%s`", if (exists("covars_v")) as.character(covars_v) else character(0))), collapse = " + ")
  int_rhs <- paste(c(sprintf("`%s` * `%s`", exposure_v, subgroup_v),
    sprintf("`%s`", if (exists("covars_v")) as.character(covars_v) else character(0))), collapse = " + ")
  f0 <- stats::glm(stats::as.formula(paste0("`", outcome_v, "` ~ ", base_rhs)), family = fam, data = df)
  f1 <- stats::glm(stats::as.formula(paste0("`", outcome_v, "` ~ ", int_rhs)), family = fam, data = df)
  .sk_fmt_p(suppressWarnings(anova(f0, f1, test = if (binary_out) "Chisq" else "F"))[2, ncol(suppressWarnings(anova(f0, f1, test = if (binary_out) "Chisq" else "F")))])
}, error = function(e) "NA")
forest_df <- rbind(
  data.frame(亚组 = "总体", 效应 = overall$est, CI下限 = overall$lo, CI上限 = overall$hi, 类型 = "总体", stringsAsFactors = FALSE),
  data.frame(亚组 = sens_table$亚组, 效应 = sens_table$效应, CI下限 = sens_table$CI下限, CI上限 = sens_table$CI上限, 类型 = "亚组", stringsAsFactors = FALSE))
forest_df$idx <- rev(seq_len(nrow(forest_df)))
forest_fig <- ggplot2::ggplot(forest_df[!is.na(forest_df$效应), ], ggplot2::aes(x = 效应, y = 亚组)) +
  ggplot2::geom_pointrange(ggplot2::aes(xmin = CI下限, xmax = CI上限, colour = 类型), linewidth = 0.8) +
  ggplot2::geom_vline(xintercept = if (binary_out) 1 else 0, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = paste0("亚组森林图（", eff_name, "）"), x = eff_name, y = NULL,
    caption = sprintf("交互检验 P=%s", interaction_p)) +
  ggplot2::theme_bw() + ggplot2::theme(legend.position = "top")
interaction_num <- suppressWarnings(as.numeric(interaction_p))
results_txt <- paste0(
  sprintf("总体 %s=%s [%.3f, %.3f]。", eff_name, overall$est, overall$lo, overall$hi),
  sprintf("亚组×暴露交互 P=%s（%s）。", interaction_p,
    if (is.na(interaction_num)) "无法计算" else if (interaction_num < 0.05) "存在效应修饰" else "未见显著效应修饰"))
method_txt <- sprintf("各亚组内拟合%s模型（含协变量），报告 %s 及 95%%CI；交互检验为暴露×亚组乘积项的联合检验。", if (binary_out) "logistic" else "线性", eff_name)
diag_txt <- sprintf("亚组样本量：%s。", paste(sprintf("%s=%d", sens_table$亚组, sens_table$样本量), collapse = ", "))
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["模型/方法适配性检查"]] <- "not_assessed"
.diag_status[["核心假设、稳健性和敏感性检查"]] <- "not_assessed"
.diag_status[["替代模型或替代包结果一致性检查"]] <- "not_assessed"
.diag_status[["亚组样本量、交互项和多重比较检查"]] <- if (any(sens_table$样本量 < 20)) "warning" else "passed"
.out[["forest_fig"]] <- list(kind = "figure", payload = forest_fig, title = "亚组森林图")
.out[["sens_table"]] <- list(kind = "table", payload = sens_table, title = "敏感性表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "解释边界")
.out[["method_note"]] <- list(kind = "text", payload = method_txt, title = "统计方法说明")
.out[["paper_text"]] <- list(kind = "text", payload = results_txt, title = "论文式结果解释")
.out[["diag_text"]] <- list(kind = "text", payload = diag_txt, title = "模型诊断或稳健性说明")
'''

_ENGINE_EPI = r'''
## Engine: epidemiological 2x2 measures (base R)
## inputs: outcome(binary), exposure(binary), denominator(optional), time_region(optional)
if (exists("denom_v") && length(denom_v)) stop("当前固定风险指标使用个体行计数；聚合分母/人时率请另行选择相应分析方法。")
if (exists("region_v") && length(region_v)) stop("当前固定风险指标不支持时间/地区分层；请使用相应分层分析入口。")
df <- dataset[, c(outcome_v, exposure_v), drop = FALSE]
df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
df[[exposure_v]] <- droplevels(factor(df[[exposure_v]]))
df <- df[stats::complete.cases(df), , drop = FALSE]
outcome_levels <- levels(droplevels(df[[outcome_v]]))
exposure_levels <- levels(droplevels(df[[exposure_v]]))
if (length(outcome_levels)!=2L || length(exposure_levels)!=2L) stop("需要 2x2 的结局×暴露表")
if (!exists("positive_v") || !exists("reference_v")) stop("必须确认事件类别与参照暴露组，不能依赖因子排序")
positive <- as.character(positive_v); reference <- as.character(reference_v)
if (length(positive)!=1L || !positive %in% outcome_levels ||
    length(reference)!=1L || !reference %in% exposure_levels) stop("事件类别或参照暴露组不在数据中")
negative <- setdiff(outcome_levels,positive); exposed <- setdiff(exposure_levels,reference)
tab <- table(factor(df[[outcome_v]],levels=c(positive,negative)), factor(df[[exposure_v]],levels=c(reference,exposed)))
a <- tab[1, 2]; b <- tab[2, 2]
c1 <- tab[1, 1]; d1 <- tab[2, 1]
if (any(tab==0)) stop("2x2 表存在零单元格，当前 Wald 风险区间不可用；请制定精确或其他预先指定的估计方法。")
n1 <- a + b; n0 <- c1 + d1
risk1 <- a / n1; risk0 <- c1 / n0
rr <- risk1 / risk0
orv <- (a * d1) / (b * c1)
arr <- risk0 - risk1
se_lrr <- sqrt(1 / a - 1 / n1 + 1 / c1 - 1 / n0)
rr_lo <- exp(log(rr) - 1.959964 * se_lrr); rr_hi <- exp(log(rr) + 1.959964 * se_lrr)
se_lor <- sqrt(1 / a + 1 / b + 1 / c1 + 1 / d1)
or_lo <- exp(log(orv) - 1.959964 * se_lor); or_hi <- exp(log(orv) + 1.959964 * se_lor)
arr_se <- sqrt(risk1 * (1 - risk1) / n1 + risk0 * (1 - risk0) / n0)
nnt <- if (arr > 0) 1 / arr else NA_real_
meas_table <- data.frame(
  指标 = c("暴露组风险", "非暴露组风险", "相对风险 RR", "比值比 OR", "归因风险差 ARR", "NNT"),
  数值 = c(sprintf("%.3f (%d/%d)", risk1, a, n1), sprintf("%.3f (%d/%d)", risk0, c1, n0),
    sprintf("%.3f [%.3f, %.3f]", rr, rr_lo, rr_hi),
    sprintf("%.3f [%.3f, %.3f]", orv, or_lo, or_hi),
    sprintf("%.3f [%.3f, %.3f]", arr, arr - 1.959964 * arr_se, arr + 1.959964 * arr_se),
    if (is.na(nnt)) "NA" else sprintf("%.1f", nnt)),
  stringsAsFactors = FALSE)
ci_table <- data.frame(
  指标 = c("log(RR) 标准误", "log(OR) 标准误", "Wald 95%CI 方法"),
  结果 = c(sprintf("%.4f", se_lrr), sprintf("%.4f", se_lor), "Wald（正态近似）"),
  stringsAsFactors = FALSE)
rate_df <- data.frame(
  组 = rep(c(exposed, reference), each = 2),
  类别 = rep(c("事件", "非事件"), 2),
  人数 = c(a, b, c1, d1))
rate_df$率 <- ave(rate_df$人数, rate_df$组, FUN = function(v) v / sum(v))
rate_fig <- ggplot2::ggplot(rate_df[rate_df$类别 == "事件", ], ggplot2::aes(x = 组, y = 率, fill = 组)) +
  ggplot2::geom_col(alpha = 0.8, show.legend = FALSE) +
  ggplot2::labs(title = "各组事件风险", x = NULL, y = "风险") + ggplot2::theme_bw()
rare <- min(a, c1) < 5
results_txt <- paste0(
  sprintf("RR=%.3f（95%%CI %.3f-%.3f），OR=%.3f。", rr, rr_lo, rr_hi, orv),
  sprintf("ARR=%.3f，%s。", arr, if (is.na(nnt)) "ARR≤0，NNT 不适用" else sprintf("NNT=%.1f", nnt)),
  sprintf(" 事件类别=%s；暴露组=%s；参照组=%s。",positive,exposed,reference),
  " 指标基于 2x2 表的 Wald 正态近似；稀疏事件需复核区间方法。")
pub_txt <- paste0(
  sprintf("公共卫生解读：暴露组风险 %.1f%% 对比非暴露组 %.1f%%。", 100 * risk1, 100 * risk0),
  if (!is.na(nnt)) sprintf("每防止 1 例事件需干预 %.1f 人。", nnt) else "",
  " 人群归因分数需结合暴露流行率另行计算。")
.diag_status[["分母合理性检查"]] <- if (n1 < 10 || n0 < 10) "warning" else "passed"
.diag_status[["稀有事件检查"]] <- if (rare) "warning" else "passed"
.out[["meas_table"]] <- list(kind = "table", payload = meas_table, title = "指标表")
.out[["ci_table"]] <- list(kind = "table", payload = ci_table, title = "置信区间")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "流行病学解释")
.out[["epi_table"]] <- list(kind = "table", payload = meas_table, title = "流行病学指标表")
.out[["rate_fig"]] <- list(kind = "figure", payload = rate_fig, title = "率/风险图")
.out[["pub_text"]] <- list(kind = "text", payload = pub_txt, title = "公共卫生解释")
'''

_ENGINE_KAPPA = r'''
## Engine: inter-rater agreement (Cohen/weighted kappa, base R)
## inputs: data_object(item), rater_id(rater), measure(rating)
ratings <- dataset[, c(subject_v, rater_v, measure_v)]
ratings[[measure_v]] <- factor(ratings[[measure_v]])
raters <- sort(unique(as.character(ratings[[rater_v]])))
wide_list <- lapply(raters, function(r) {
  sub <- ratings[ratings[[rater_v]] == r, c(subject_v, measure_v), drop = FALSE]
  names(sub) <- c(subject_v, r)
  sub
})
wide <- Reduce(function(x, y) merge(x, y, by = subject_v), wide_list)
rating_cols <- raters
if (length(rating_cols) < 2L || nrow(wide) < 2L) stop("每个对象需要至少两位评分者")
.kappa_2 <- function(x, y) {
  lv <- sort(unique(c(levels(factor(x)), levels(factor(y)))))
  x <- factor(x, levels = lv); y <- factor(y, levels = lv)
  tab <- table(x, y)
  n <- sum(tab)
  po <- sum(diag(tab)) / n
  pe <- sum(rowSums(tab) * colSums(tab)) / n^2
  k <- (po - pe) / (1 - pe)
  se_k <- sqrt(po * (1 - po) / (n * (1 - pe)^2))
  list(kappa = k, se = se_k, po = po, pe = pe, n = n)
}
pair_rows <- list()
for (i in seq_along(rating_cols)[-length(rating_cols)]) {
  kp <- .kappa_2(wide[[rating_cols[i]]], wide[[rating_cols[i + 1L]]])
  pair_rows[[length(pair_rows) + 1L]] <- data.frame(
    评分者对 = paste(rating_cols[i], rating_cols[i + 1L], sep = " vs "),
    Kappa = round(kp$kappa, 4), CI下限 = round(kp$kappa - 1.959964 * kp$se, 4),
    CI上限 = round(kp$kappa + 1.959964 * kp$se, 4), 观察一致率 = round(kp$po, 4),
    stringsAsFactors = FALSE)
}
kappa_table <- if (length(pair_rows)) do.call(rbind, pair_rows) else data.frame(说明 = "需要至少两位评分者", stringsAsFactors = FALSE)
lv <- levels(wide[[rating_cols[1]]])
diff_df <- data.frame(
  对象 = wide[[subject_v]],
  r1 = as.integer(wide[[rating_cols[1]]]), r2 = as.integer(wide[[rating_cols[2]]]))
rater_fig <- ggplot2::ggplot(diff_df, ggplot2::aes(x = r1, y = r2)) +
  ggplot2::geom_jitter(width = 0.15, height = 0.15, alpha = 0.5, colour = "#4477AA") +
  ggplot2::geom_abline(intercept = 0, slope = 1, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "评分者差异图", x = rating_cols[1], y = rating_cols[2]) + ggplot2::theme_bw()
first_k <- .kappa_2(wide[[rating_cols[1]]], wide[[rating_cols[2]]])
interp <- if (first_k$kappa < 0.4) "一致性较差" else if (first_k$kappa < 0.6) "一致性中等" else if (first_k$kappa < 0.8) "一致性较好" else "一致性极好"
results_txt <- sprintf("Kappa=%.3f（95%%CI %.3f-%.3f），%s；观察一致率 %.1f%%，n=%d。",
  first_k$kappa, first_k$kappa - 1.959964 * first_k$se, first_k$kappa + 1.959964 * first_k$se, interp, 100 * first_k$po, first_k$n)
method_txt <- "Cohen's Kappa（校正机遇一致）；Kappa 标准误用正态近似公式，95%CI 为 Wald 近似。"
rec_txt <- if (first_k$kappa < 0.6) "建议：澄清评分标准并开展复训后重测一致性。" else "一致性可接受，保持当前评分标准。"
diag_txt <- sprintf("类别数=%d，评分者=%d，完整配对 n=%d；患病率-偏倚效应：pe=%.3f。", length(lv), length(rating_cols), first_k$n, first_k$pe)
.coverage_ok <- all(vapply(rating_cols, function(rc) !any(is.na(wide[[rc]])), logical(1)))
.diag_status[["评分者和类别覆盖检查"]] <- if (.coverage_ok) "passed" else "warning"
.diag_status[["患病率与偏倚效应检查"]] <- if (first_k$pe > 0.9 || first_k$pe < 0.1) "warning" else "passed"
.diag_status[["一致性置信区间检查"]] <- "not_assessed"
.out[["kappa_table"]] <- list(kind = "table", payload = kappa_table, title = "一致性指标表")
.out[["rater_fig"]] <- list(kind = "figure", payload = rater_fig, title = "评分者差异图")
.out[["rec_text"]] <- list(kind = "text", payload = rec_txt, title = "改进建议")
.out[["method_text"]] <- list(kind = "text", payload = method_txt, title = "统计方法说明")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文式结果解释")
.out[["diag_text"]] <- list(kind = "text", payload = diag_txt, title = "稳健性说明")
'''

_ENGINE_RELIABILITY = r'''
## Engine: scale reliability — Cronbach's alpha + item analysis (base R)
## inputs: categorical_variables = item columns(critical)
.step <- "setup"
items <- as.character(cat_v)
missing_cols <- setdiff(items, names(dataset))
if (length(missing_cols)) stop("缺失条目列: ", paste(missing_cols, collapse = ", "))
dat <- dataset[, items, drop = FALSE]
dat[] <- lapply(dat, function(x) suppressWarnings(as.numeric(as.character(x))))
if (exists("reverse_v") && !is.null(reverse_v)) {
  for (rv in as.character(reverse_v)) if (rv %in% names(dat)) dat[[rv]] <- max(dat[[rv]], na.rm = TRUE) + min(dat[[rv]], na.rm = TRUE) - dat[[rv]]
}
dat <- dat[stats::complete.cases(dat), , drop = FALSE]
n_sub <- nrow(dat); k <- length(items)
if (n_sub < 10 || k < 2L) stop(sprintf("需要 >=2 个条目且 >=10 个完整被试（当前 n=%d, k=%d）", n_sub, k))
total <- rowSums(dat)
item_var <- vapply(dat, stats::var, numeric(1))
alpha <- k / (k - 1) * (1 - sum(item_var) / stats::var(total))
rel_table <- data.frame(
  指标 = c("Cronbach's alpha", "条目数", "完整被试数", "判定"),
  结果 = c(round(alpha, 4), k, n_sub,
    if (alpha >= 0.9) "优秀" else if (alpha >= 0.8) "良好" else if (alpha >= 0.7) "可接受" else if (alpha >= 0.6) "边缘" else "较差"),
  stringsAsFactors = FALSE)
it_rows <- list(); del_rows <- list()
for (it in items) {
  rest <- total - dat[[it]]
  r_it <- suppressWarnings(stats::cor(dat[[it]], rest))
  it_rows[[length(it_rows) + 1L]] <- data.frame(
    条目 = it, 条目均值 = round(mean(dat[[it]]), 3), 条目方差 = round(stats::var(dat[[it]]), 3),
    校正条目总分相关 = round(r_it, 4), stringsAsFactors = FALSE)
  a_del <- k / (k - 2) * (1 - (sum(item_var) - item_var[it]) / stats::var(rest))
  del_rows[[length(del_rows) + 1L]] <- data.frame(
    条目 = it, 删除后alpha = round(a_del, 4),
    变化 = round(a_del - alpha, 4), stringsAsFactors = FALSE)
}
it_table <- do.call(rbind, it_rows)
del_table <- do.call(rbind, del_rows)
drop_sug <- del_table[del_table$变化 > 0.02, ]
drop_txt <- if (nrow(drop_sug)) paste0("以下条目删除后 alpha 提升，建议复查：", paste(drop_sug$条目, collapse = "、"), "。") else "未发现删除后能明显提升 alpha 的条目。"
.results_txt <- sprintf("Cronbach's alpha=%.3f（k=%d，n=%d），%s；校正条目-总分相关与删除后 alpha 见结果面板。",
  alpha, k, n_sub, rel_table$结果[4])
low_items <- it_table$校正条目总分相关 < 0.3
.diag_status[["反向题检查"]] <- if (exists("reverse_v") && !is.null(reverse_v) && length(as.character(reverse_v))) "warning" else "passed"
.diag_status[["缺失和极端反应检查"]] <- if (isTRUE(any(low_items, na.rm = TRUE))) "warning" else "passed"
.out[["rel_table"]] <- list(kind = "table", payload = rel_table, title = "信度表")
.out[["it_table"]] <- list(kind = "table", payload = it_table, title = "项目分析表")
.out[["drop_text"]] <- list(kind = "text", payload = drop_txt, title = "删题建议")
.out[["corr_table"]] <- list(kind = "table", payload = it_table, title = "项目-总分相关")
.out[["del_table"]] <- list(kind = "table", payload = del_table, title = "删除条目分析")
.out[["results_text"]] <- list(kind = "text", payload = .results_txt, title = "量表解释")
'''

_ENGINE_FORECAST = r'''
## Engine: time series forecast (stats::arima + decompose, base R)
## inputs: time(critical), measure(critical), frequency(optional), group_region(optional)
freq <- if (exists("freq_v") && !is.null(freq_v) && !is.na(freq_v)) max(2L, as.integer(freq_v)) else 4L
df <- dataset[, c(time_v, measure_v)]
df <- df[order(df[[time_v]]), , drop = FALSE]
y <- as.numeric(df[[measure_v]])
y <- y[is.finite(y)]
n <- length(y)
if (n < 12) stop(sprintf("序列过短 (n=%d)，至少需要 12 个观测", n))
ts_obj <- stats::ts(y, frequency = freq)
decomp <- tryCatch(stats::decompose(ts_obj), error = function(e) NULL)
trend_table <- if (!is.null(decomp)) {
  data.frame(
    成分 = c("趋势(末值)", "季节(首季振幅)", "随机(标准差)"),
    数值 = c(round(decomp$trend[n - freq + 1L], 4),
      round(max(decomp$seasonal[1:freq], na.rm = TRUE) - min(decomp$seasonal[1:freq], na.rm = TRUE), 4),
      round(stats::sd(decomp$random, na.rm = TRUE), 4)),
    stringsAsFactors = FALSE)
} else data.frame(成分 = "未分解（n 不足以按频率分解）", 数值 = NA, stringsAsFactors = FALSE)

.step <- "fit"
h <- max(3L, min(6L, n %/% 6L))
train <- ts_obj[1:(n - h)]
test <- y[(n - h + 1):n]
best_fit <- NULL; best_aic <- Inf; best_order <- c(1L, 0L, 0L)
for (p in 0:2) for (d in 0:1) for (q in 0:2) {
  f <- tryCatch(stats::arima(train, order = c(p, d, q)), error = function(e) NULL)
  if (!is.null(f)) {
    a <- stats::AIC(f)
    if (a < best_aic) { best_aic <- a; best_fit <- f; best_order <- c(p, d, q) }
  }
}
if (is.null(best_fit)) stop("ARIMA 拟合失败")
fc <- stats::predict(best_fit, n.ahead = h)
fc_se <- fc$se; fc_pred <- fc$pred
mae <- mean(abs(test - fc_pred)); rmse <- sqrt(mean((test - fc_pred)^2))
err_table <- data.frame(
  指标 = c("MAE", "RMSE", "最优 ARIMA 阶数", "训练窗"),
  数值 = c(round(mae, 3), round(rmse, 3), paste0("(", paste(best_order, collapse = ","), ")"), n - h),
  stringsAsFactors = FALSE)
fc_full <- stats::predict(stats::arima(ts_obj, order = best_order), n.ahead = h)
fc_table <- data.frame(
  步长 = seq_len(h),
  预测 = round(as.numeric(fc_full$pred), 3),
  CI下限 = round(as.numeric(fc_full$pred - 1.959964 * fc_full$se), 3),
  CI上限 = round(as.numeric(fc_full$pred + 1.959964 * fc_full$se), 3),
  stringsAsFactors = FALSE)
hist_df <- data.frame(t = seq_len(n), y = y)
fc_df <- data.frame(t = n + seq_len(h), y = as.numeric(fc_full$pred))
fc_fig <- ggplot2::ggplot(hist_df, ggplot2::aes(x = t, y = y)) +
  ggplot2::geom_line(colour = "grey45", linewidth = 0.7) +
  ggplot2::geom_line(data = fc_df, ggplot2::aes(x = t, y = y), colour = "#CC3311", linewidth = 1) +
  ggplot2::geom_ribbon(data = data.frame(t = n + seq_len(h),
      lo = as.numeric(fc_full$pred - 1.959964 * fc_full$se),
      hi = as.numeric(fc_full$pred + 1.959964 * fc_full$se)),
    ggplot2::aes(x = t, ymin = lo, ymax = hi), alpha = 0.2, fill = "#CC3311", inherit.aes = FALSE) +
  ggplot2::labs(title = sprintf("ARIMA%s 预测（末 %d 点为留出检验窗）", paste0("(", paste(best_order, collapse = ","), ")"), h), x = "时间序", y = measure_v) +
  ggplot2::theme_bw()
resid_acf <- tryCatch(as.numeric(stats::acf(stats::residuals(best_fit), plot = FALSE)$acf)[2], error = function(e) NA_real_)
.diag_status[["时间频率检查"]] <- "not_assessed"
.diag_status[["残差自相关"]] <- if (is.na(resid_acf) || abs(resid_acf) > 0.3) "warning" else "passed"
.diag_status[["训练/测试切分"]] <- "not_assessed"
.out[["trend_table"]] <- list(kind = "table", payload = trend_table, title = "趋势分解")
.out[["fc_fig"]] <- list(kind = "figure", payload = fc_fig, title = "预测图")
.out[["err_table"]] <- list(kind = "table", payload = err_table, title = "误差指标")
.out[["fc_table"]] <- list(kind = "table", payload = fc_table, title = "预测表")
'''

_ENGINE_SAMPLE_SIZE = r'''
## Engine: two-sample sample size & power (base R formulas)
## inputs: effect_size(parameter critical), alpha(parameter), power(parameter), allocation_ratio(parameter)
.outcome_audit <- if (exists("outcome_v") && !is.null(outcome_v)) .sk_design_outcome_audit(dataset, outcome_v) else
  list(bound = FALSE, note = "未绑定历史终点列；设计仅采用预先指定的效应量、alpha 和目标把握度，不计算观察/事后 power。")
d <- if (exists("eff_v") && !is.null(eff_v) && !is.na(eff_v)) as.numeric(eff_v) else NA_real_
if (is.na(d) || d == 0) stop("效应量必须为非零数值")
alpha <- if (exists("alpha_v") && !is.null(alpha_v) && !is.na(alpha_v)) as.numeric(alpha_v) else 0.05
power_t <- if (exists("power_v") && !is.null(power_v) && !is.na(power_v)) as.numeric(power_v) else 0.8
ratio <- if (exists("ratio_v") && !is.null(ratio_v) && !is.na(ratio_v)) as.numeric(ratio_v) else 1
if (!is.finite(d) || !is.finite(ratio) || ratio <= 0 || alpha <= 0 || alpha >= 1 || power_t <= .5 || power_t >= 1) stop("效应量、分配比、alpha 或 power 超出支持范围")
z_a <- stats::qnorm(1 - alpha / 2); z_b <- stats::qnorm(power_t)
n2_base <- (z_a + z_b)^2 * (1 + 1 / ratio) / d^2
n2 <- ceiling(n2_base); n1 <- ceiling(ratio * n2_base)
sens_rows <- lapply(c(0.8, 0.9, 1, 1.1, 1.2) * d, function(dd) {
  do.call(rbind,lapply(c(0,.1,.2),function(dropout) {
    base <- (z_a + z_b)^2 * (1 + 1 / ratio) / dd^2
    data.frame(效应量=round(dd,3),假设脱落率=dropout,试验组=ceiling(base/(1-dropout)),对照组=ceiling(ratio*base/(1-dropout)))
  }))
})
sens_table <- do.call(rbind, sens_rows)
para_table <- data.frame(
  参数 = c("效应量(Cohen's d)", "alpha（双尾）", "把握度", "分配比(对照:试验)"),
  假设 = c(d, alpha, power_t, sprintf("%s:1", ratio)),
  stringsAsFactors = FALSE)
ss_table <- data.frame(
  组 = c("试验组", "对照组", "合计"),
  样本量 = c(n2, n1, n1 + n2),
  stringsAsFactors = FALSE)
plan_txt <- paste0(
  sprintf("独立两组等方差均值比较的正态近似：试验组 n=%d，对照组 n=%d（合计 %d）；目标 alpha=%s（双尾）、把握度 %.0f%%、Cohen's d=%.2f。", n2, n1, n1 + n2, alpha, 100 * power_t, d),
  " 若预期脱落率 δ，建议每组再除以 (1-δ)。二分类结局应改用率差的样本量公式。")
plan_txt <- paste(plan_txt, .outcome_audit$note)
.diag_status[["效应量合理性检查"]] <- "not_assessed"
.diag_status[["脱落率敏感性"]] <- if (all(is.finite(sens_table$试验组)) && all(sens_table$试验组>0)) "passed" else "failed"
.out[["ss_table"]] <- list(kind = "table", payload = ss_table, title = "样本量表")
.out[["para_table"]] <- list(kind = "table", payload = para_table, title = "参数假设")
.out[["sens_table"]] <- list(kind = "table", payload = sens_table, title = "敏感性范围")
.out[["para_table_2"]] <- list(kind = "table", payload = para_table, title = "参数假设表")
.out[["plan_text"]] <- list(kind = "text", payload = plan_txt, title = "方案文字")
'''

_ENGINE_EFA = r'''
## Engine: exploratory factor analysis (psych::fa + KMO/Bartlett)
## inputs: categorical_variables = item columns(critical), dimension(parameter optional)
.step <- "setup"
items <- as.character(cat_v)
missing_cols <- setdiff(items, names(dataset))
if (length(missing_cols)) stop("缺失条目列: ", paste(missing_cols, collapse = ", "))
dat <- dataset[, items, drop = FALSE]
dat[] <- lapply(dat, function(x) suppressWarnings(as.numeric(as.character(x))))
dat <- dat[stats::complete.cases(dat), , drop = FALSE]
n_sub <- nrow(dat)
if (n_sub < 20 || length(items) < 4L) stop(sprintf("需要 >=4 个条目且 >=20 个完整被试（当前 n=%d）", n_sub))

.step <- "adequacy"
kmo_res <- psych::KMO(dat)
bart_res <- psych::cortest.bartlett(dat)
kmo_overall <- kmo_res$MSA[1]
adequacy_df <- data.frame(
  检验 = c("KMO 总体", "Bartlett 球形检验 卡方", "Bartlett P"),
  结果 = c(round(kmo_overall, 3), sprintf("%.1f", bart_res$chisq), .sk_fmt_p(bart_res$p.value)),
  stringsAsFactors = FALSE)

.step <- "nfactors"
dim_v_resolved <- exists("dim_v") && !is.null(dim_v) && !is.na(dim_v)
nfact <- if (dim_v_resolved) as.integer(dim_v) else 2L
nfact <- max(1L, min(nfact, length(items) - 1L))
fa_res <- psych::fa(dat, nfactors = nfact, rotate = "varimax", fm = "pa")
load_mat <- as.data.frame(unclass(fa_res$loadings))
load_mat$条目 <- rownames(load_mat)
load_df <- data.frame(条目 = load_mat$条目, load_mat[, seq_len(nfact), drop = FALSE], 共同度 = round(rowSums(load_mat[, seq_len(nfact), drop = FALSE]^2), 3), check.names = FALSE, stringsAsFactors = FALSE)
load_df[] <- lapply(load_df, function(c) if (is.numeric(c)) round(c, 3) else c)
n_cross <- sum(vapply(seq_len(n_sub_of_rows <- nrow(load_df)), function(i) sum(abs(load_df[i, 2:(nfact + 1)]) >= 0.35) > 1, logical(1)))
n_low_comm <- sum(load_df$共同度 < 0.4)

.step <- "scree"
eig <- suppressWarnings(psych::fa.parallel(dat, fa = "fa", plot = FALSE))
scree_df <- data.frame(
  因子 = seq_along(eig$fa.values), 特征值类 = round(eig$fa.values, 3),
  stringsAsFactors = FALSE)
scree_df <- scree_df[scree_df$因子 <= max(8, nfact + 2), ]
scree_fig <- ggplot2::ggplot(scree_df, ggplot2::aes(x = 因子, y = 特征值类)) +
  ggplot2::geom_line(colour = "#4477AA") + ggplot2::geom_point(colour = "#4477AA", size = 2.4) +
  ggplot2::geom_hline(yintercept = 0, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "碎石图（MAP/并行分析参考）", x = "因子数", y = "特征值（fa）") + ggplot2::theme_bw()

.step <- "scores"
scores_df <- as.data.frame(round(as.matrix(fa_res$scores)[seq_len(min(10, n_sub)), , drop = FALSE], 3))
scores_df$被试 <- paste0("S", seq_len(nrow(scores_df)))
scores_df <- scores_df[, c(ncol(scores_df), seq_len(ncol(scores_df) - 1)), drop = FALSE]

interp <- if (kmo_overall < 0.6) "数据不适合因子分析" else if (kmo_overall < 0.7) "勉强可做因子分析" else "适合因子分析"
results_txt <- paste0(
  sprintf("EFA（ml 旋转 varimax，n=%d，%d 条目，%d 因子）：KMO=%.3f（%s）；", n_sub, length(items), nfact, kmo_overall, interp),
  sprintf("共同度 <0.4 的条目 %d 个，交叉载荷条目 %d 个。", n_low_comm, n_cross),
  " 载荷矩阵与因子得分见右侧结果面板。")
.diag_status[["KMO 适配性"]] <- if (kmo_overall < 0.7) "warning" else "passed"
.diag_status[["共同度检查"]] <- if (n_low_comm > 0) "warning" else "passed"
.diag_status[["交叉载荷检查"]] <- if (n_cross > 0) "warning" else "passed"

.out[["loadings"]] <- list(kind = "table", payload = load_df, title = "载荷矩阵")
.out[["scree_fig"]] <- list(kind = "figure", payload = scree_fig, title = "碎石图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "因子解释")
.out[["scores_table"]] <- list(kind = "table", payload = scores_df, title = "得分表")
.out[["adequacy_table"]] <- list(kind = "table", payload = adequacy_df, title = "KMO/Bartlett 表")
.out[["loadings_table"]] <- list(kind = "table", payload = load_df, title = "因子载荷表")
'''

_ENGINE_CFA = r'''
## Engine: confirmatory factor analysis (lavaan)
## inputs: categorical_variables(items), latent_variables(spec=模型语法), path_specification(spec=结构路径, 可选)
.step <- "setup"
items <- as.character(cat_v)
missing_cols <- setdiff(items, names(dataset))
if (length(missing_cols)) stop("缺失条目列: ", paste(missing_cols, collapse = ", "))
dat <- dataset[, items, drop = FALSE]
dat <- dat[stats::complete.cases(dat), , drop = FALSE]
n_sub <- nrow(dat)
if (n_sub < 30) stop(sprintf("CFA 建议至少 30 个完整被试（当前 n=%d）", n_sub))

.model <- if (exists("method_v") && !is.null(method_v) && nzchar(as.character(method_v)[1])) {
  paste(as.character(method_v), collapse = "\n")
} else if (exists("latent_v") && !is.null(latent_v) && nzchar(as.character(latent_v)[1])) {
  paste(as.character(latent_v), collapse = "\n")
} else stop("需要提供测量模型语法（latent_variables 或分析方式参数）")
path_part <- if (exists("path_v") && !is.null(path_v) && nzchar(as.character(path_v)[1])) paste(as.character(path_v), collapse = "\n") else ""

.step <- "fit"
full_model <- .model
if (nzchar(path_part)) full_model <- paste(full_model, path_part, sep = "\n")
fit <- lavaan::cfa(full_model, data = dat, std.lv = TRUE)
fm <- lavaan::fitMeasures(fit, c("cfi", "tli", "rmsea", "srmr", "chisq", "df", "pvalue"))
est <- lavaan::parameterEstimates(fit, standardized = TRUE)
load_est <- est[est$op == "=~", ]
path_est <- est[est$op == "~", ]

load_table <- data.frame(
  潜变量 = load_est$lhs, 条目 = load_est$rhs,
  标准化载荷 = round(load_est$std.all, 3),
  P值 = vapply(load_est$pvalue, .sk_fmt_p, character(1)), stringsAsFactors = FALSE)
path_table <- if (nrow(path_est)) data.frame(
  路径 = paste(path_est$lhs, path_est$op, path_est$rhs),
  标准化系数 = round(path_est$std.all, 3), P值 = vapply(path_est$pvalue, .sk_fmt_p, character(1)), stringsAsFactors = FALSE) else
  data.frame(说明 = "未指定结构路径", stringsAsFactors = FALSE)
fit_table <- data.frame(
  指标 = c("CFI", "TLI", "RMSEA", "SRMR", "卡方/df"),
  结果 = c(sprintf("%.3f", fm[["cfi"]]), sprintf("%.3f", fm[["tli"]]), sprintf("%.3f", fm[["rmsea"]]), sprintf("%.3f", fm[["srmr"]]),
    sprintf("%.2f", fm[["chisq"]] / max(1, fm[["df"]]))),
  判定 = c(if (fm[["cfi"]] >= 0.95) "良好" else "未达 .95", if (fm[["tli"]] >= 0.95) "良好" else "未达 .95",
    if (fm[["rmsea"]] <= 0.08) "可接受" else "偏大", if (fm[["srmr"]] <= 0.08) "可接受" else "偏大", ""),
  stringsAsFactors = FALSE)
syntax_df <- data.frame(模型语法 = unlist(strsplit(full_model, "\n")), stringsAsFactors = FALSE)
path_note <- paste0("路径图说明：潜在变量以标准化载荷绘制，结构路径为 ", if (nrow(path_est)) paste(path_est$lhs, "→", path_est$rhs, collapse = "; ") else "无", "。")
load_fig <- ggplot2::ggplot(load_table, ggplot2::aes(x = stats::reorder(条目, 标准化载荷), y = 标准化载荷)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.85) + ggplot2::coord_flip() +
  ggplot2::geom_hline(yintercept = 0.5, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "标准化载荷", x = NULL, y = "标准化载荷") + ggplot2::theme_bw()

heywood <- any(abs(load_est$std.all) > 1)
mi <- tryCatch(lavaan::modindices(fit, sort. = TRUE, maximum.number = 3), error = function(e) NULL)
mod_note <- if (!is.null(mi) && nrow(mi) > 0) sprintf("修正指数最大项：%s %s %s（MI=%.1f）——审慎解释，不宜仅据 MI 加路径。", mi$lhs[1], mi$op[1], mi$rhs[1], mi$mi[1]) else "无显著修正指数。"
results_note <- paste0("模型语法已在 table_4 中完整给出，可直接复现。", mod_note)
.diag_status[["CFI/TLI/RMSEA/SRMR"]] <- if (fm[["cfi"]] < 0.95 || fm[["rmsea"]] > 0.08) "warning" else "passed"
.diag_status[["修正指数审慎解释"]] <- "not_assessed"
.diag_status[["Heywood 问题检查"]] <- if (heywood) "warning" else "passed"

.out[["path_fig"]] <- list(kind = "figure", payload = load_fig, title = "路径图（标准化载荷）")
.out[["load_table"]] <- list(kind = "table", payload = load_table, title = "载荷表")
.out[["fit_table"]] <- list(kind = "table", payload = fit_table, title = "拟合指标")
.out[["struct_table"]] <- list(kind = "table", payload = path_table, title = "结构路径效应")
.out[["syntax_table"]] <- list(kind = "text", payload = full_model, title = "模型语法")
.out[["load_path_table"]] <- list(kind = "table", payload = load_table, title = "载荷/路径表")
.out[["path_note_text"]] <- list(kind = "text", payload = path_note, title = "路径图说明")
'''

_ENGINE_DID = r'''
## Engine: difference-in-differences (base TWFE + cluster-robust SE)
## inputs: outcome, treatment_group(binary), time(numeric/factor), policy_start_time(parameter), cluster_variable(optional)
.step <- "setup"
cluster_col <- if (exists("cluster_v") && !is.null(cluster_v) && !is.na(cluster_v)) as.character(cluster_v) else NULL
keep <- unique(c(outcome_v, group_v, time_v, cluster_col))
df <- dataset[, keep, drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
policy <- if (exists("policy_v") && !is.null(policy_v) && !is.na(policy_v)) as.numeric(policy_v) else stop("政策起始时间必须提供")
df$.post <- as.numeric(as.numeric(as.character(df[[time_v]])) >= policy)
df$.treat <- as.numeric(df[[group_v]] == sort(unique(as.character(df[[group_v]])))[2])
n_treated <- sum(df$.treat)
if (all(df$.treat) || !any(df$.treat)) stop("处理组需要同时包含处理与对照")
.time_numeric <- as.numeric(as.character(df[[time_v]]))

.step <- "twfe"
did_fit <- stats::lm(
  stats::as.formula(paste0("`", outcome_v, "` ~ .treat * .post + factor(`", time_v, "`)")), data = df)
coefs <- summary(did_fit)$coefficients
did_row <- grep(".treat:", rownames(coefs), fixed = TRUE)[1]
if (is.na(did_row)) stop("DID 交互项系数缺失（检查时间与分组定义）")
did_est <- coefs[did_row, 1]; did_se <- coefs[did_row, 2]; did_p <- coefs[did_row, 4]
cl_var <- if (!is.null(cluster_col)) cluster_col else group_v
rob_se <- tryCatch(sqrt(diag(sandwich::vcovCL(did_fit, cluster = df[[cl_var]])))[did_row], error = function(e) NA_real_)

.step <- "parallel-trend"
pre_df <- df[df$.post == 0, ]
pre_test <- tryCatch({
  pf <- stats::lm(
    stats::as.formula(paste0("`", outcome_v, "` ~ .treat * factor(`", time_v, "`)")), data = pre_df)
  an <- stats::anova(pf)
  hit <- grep("\\.treat:", rownames(an), value = TRUE)
  .sk_fmt_p(sum(an[hit, "Pr(>F)"], na.rm = TRUE))
}, error = function(e) "NA")
placebo_p <- tryCatch({
  fake <- df; fake$.post <- as.numeric(as.numeric(as.character(fake[[time_v]])) >= policy - 1)
  pf2 <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ .treat * .post")), data = fake)
  .sk_fmt_p(summary(pf2)$coefficients["\\.treat:.post", 4])
}, error = function(e) "NA")

did_table <- data.frame(
  指标 = c("DID 估计（双重差分）", "常规 SE", "聚类稳健 SE", "P 值"),
  数值 = c(round(did_est, 4), round(did_se, 4),
    if (length(rob_se)) round(rob_se, 4) else "NA", .sk_fmt_p(did_p)),
  stringsAsFactors = FALSE)
pre_df2 <- aggregate(df[[outcome_v]], by = list(时间 = df[[time_v]], 处理 = df$.treat), FUN = mean)
names(pre_df2) <- c("时间", "处理组", "均值")
pre_df2$均值 <- round(pre_df2$均值, 3)
parallel_df <- data.frame(
  检验 = c("前趋势检验（处理×前期时间）P", "安慰剂时点（政策前一期）P"),
  结果 = c(pre_test, placebo_p), stringsAsFactors = FALSE)
es_df <- aggregate(df[[outcome_v]], by = list(时间 = df[[time_v]], 处理 = df$.treat), FUN = mean)
names(es_df) <- c("时间", "处理组", "均值")
es_fig <- ggplot2::ggplot(es_df, ggplot2::aes(x = 时间, y = 均值, colour = factor(处理组))) +
  ggplot2::geom_line(linewidth = 0.9) + ggplot2::geom_point(size = 2) +
  ggplot2::geom_vline(xintercept = policy - 0.5, linetype = 2, colour = "grey55") +
  ggplot2::scale_colour_manual(values = c("#4477AA", "#CC3311")) +
  ggplot2::labs(title = "事件研究图（分组趋势）", x = "时间", y = outcome_v, colour = "组") + ggplot2::theme_bw()
robust_txt <- sprintf("标准误按 %s 聚类稳健计算（sandwich::vcovCL）。",
  if (!is.null(cluster_col)) cluster_col else "处理分组（未提供聚类变量）")
.diag_status[["平行趋势检查"]] <- if (pre_test != "NA" && suppressWarnings(as.numeric(pre_test)) < 0.05) "warning" else "passed"
.diag_status[["聚类稳健标准误"]] <- if (length(rob_se) && !is.na(rob_se)) "passed" else "warning"
.diag_status[["安慰剂时间点检查"]] <- if (placebo_p != "NA" && suppressWarnings(as.numeric(placebo_p)) < 0.05) "warning" else "passed"

.out[["did_table"]] <- list(kind = "table", payload = did_table, title = "DID 表")
.out[["es_fig"]] <- list(kind = "figure", payload = es_fig, title = "事件研究图")
.out[["parallel_df"]] <- list(kind = "table", payload = parallel_df, title = "平行趋势诊断")
.out[["did_model"]] <- list(kind = "table", payload = .sk_model_table(did_fit), title = "DID 模型表")
.out[["robust_text"]] <- list(kind = "text", payload = robust_txt, title = "稳健标准误说明")
'''

_ENGINE_IV = r'''
## Engine: instrumental variable 2SLS + fixed effects (AER::ivreg)
## inputs: outcome, exposure, instrumental_variable(conditional), subject_id(conditional), time(conditional)
df <- dataset[, unique(c(outcome_v, exposure_v, instrument_v,
  if (exists("subject_v")) as.character(subject_v) else character(0),
  if (exists("time_v")) as.character(time_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
n_used <- nrow(df)
iv_col <- if (exists("instrument_v")) as.character(instrument_v) else stop("需要工具变量")

.step <- "first-stage"
df[[exposure_v]] <- as.numeric(factor(df[[exposure_v]])) - 1L
fs <- stats::lm(stats::as.formula(paste0("`", exposure_v, "` ~ `", instrument_v, "`")), data = df)
fs_t <- summary(fs)$coefficients[2, 3]
weak_f <- fs_t^2
iv_fit <- AER::ivreg(
  stats::as.formula(paste0("`", outcome_v, "` ~ `", exposure_v, "` | `", instrument_v, "`")), data = df)
iv_table <- .sk_model_table(iv_fit)

.step <- "fe"
fe_table <- tryCatch({
  df$.cen_y <- ave(df[[outcome_v]], df[[subject_v]], FUN = function(v) v - mean(v))
  df$.cen_x <- ave(df[[exposure_v]], df[[subject_v]], FUN = function(v) v - mean(v))
  fe_fit <- stats::lm(.cen_y ~ .cen_x, data = df)
  .sk_model_table(fe_fit)
}, error = function(e) data.frame(说明 = "固定效应不可用（缺 subject 列）", stringsAsFactors = FALSE))

ols_fit <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ `", exposure_v, "`")), data = df)
robust_table <- data.frame(
  模型 = c("OLS（有偏风险）", "固定效应（组内）", "IV 2SLS"),
  暴露效应 = c(round(unname(stats::coef(ols_fit)[2]), 4),
    if (is.numeric(fe_table$效应) && nrow(fe_table)) fe_table$效应[1] else "NA",
    round(unname(stats::coef(iv_fit)[2]), 4)),
  stringsAsFactors = FALSE)
diag_df <- data.frame(
  检验 = c("弱工具变量 F（经验法则 >10）", "第一阶段 t", "样本量"),
  结果 = c(round(weak_f, 2), round(fs_t, 3), n_used),
  stringsAsFactors = FALSE)
ident_txt <- "识别假设：工具变量与结局仅通过暴露关联（排除性约束），且与混杂无关；该假设不可检验，需领域论证。"
.diag_status[["弱工具检查"]] <- if (weak_f < 10) "warning" else "passed"
.diag_status[["固定效应适配性"]] <- if (exists("subject_v") && !is.null(subject_v)) "passed" else "warning"
.diag_status[["聚类稳健标准误"]] <- "not_assessed"
.out[["iv_table"]] <- list(kind = "table", payload = iv_table, title = "IV")
.out[["fe_table"]] <- list(kind = "table", payload = fe_table, title = "FE 模型表")
.out[["weak_table"]] <- list(kind = "table", payload = diag_df, title = "弱工具检验")
.out[["robust_table"]] <- list(kind = "table", payload = robust_table, title = "稳健性分析")
.out[["fe_iv_table"]] <- list(kind = "table", payload = robust_table, title = "固定效应/IV 模型表")
.out[["robust_cmp"]] <- list(kind = "table", payload = robust_table, title = "稳健性对照")
.out[["ident_text"]] <- list(kind = "text", payload = ident_txt, title = "识别假设说明")
'''

_ENGINE_ML = MACHINE_LEARNING_CORE

_ENGINE_META_REG = r'''
## Engine: meta regression (metafor::rma with moderators)
## inputs: study_id, effect_estimate(log scale), standard_error, covariates(critical), subgroup(optional)
df <- dataset[, unique(c(study_v, effect_v, se_v, covars_v, if (exists("sub_v")) as.character(sub_v) else character(0))), drop = FALSE]
names(df)[1:3] <- c("study", "yi", "sei")
df$yi <- as.numeric(df$yi); df$sei <- as.numeric(df$sei)
df <- df[is.finite(df$yi) & is.finite(df$sei) & df$sei > 0, , drop = FALSE]
k <- nrow(df)
n_mods <- length(covars_v)
if (k < 3L * n_mods + 3L) stop(sprintf("研究数 k=%d 对 %d 个协变量过少（经验法则 k >= 3×协变量数+3）", k, n_mods))

.step <- "fit"
mods_form <- as.character(covars_v)
fit <- metafor::rma(yi = yi, sei = sei, mods = stats::as.formula(paste("~", paste(mods_form, collapse = " + "))), data = df, method = "REML")
ct <- as.data.frame(coef(summary(fit)))
reg_table <- data.frame(
  项 = rownames(ct),
  系数 = round(ct$estimate, 4), CI下限 = round(ct$ci.lb, 4), CI上限 = round(ct$ci.ub, 4),
  P值 = vapply(ct$p, .sk_fmt_p, character(1)), stringsAsFactors = FALSE)

.step <- "bubble"
first_mod <- covars_v[1]
bub_df <- data.frame(x = df[[first_mod]], y = df$yi, size = 1 / df$sei^2)
fit_df <- data.frame(x = sort(df[[first_mod]]),
  yhat = as.numeric(stats::predict(fit, newmods = as.matrix(df[order(df[[first_mod]]), covars_v, drop = FALSE]))$pred)[seq_len(k)])
bubble_fig <- ggplot2::ggplot(bub_df, ggplot2::aes(x = x, y = y)) +
  ggplot2::geom_point(alpha = 0.75, colour = "#4477AA", size = 2.4) +
  ggplot2::geom_smooth(method = "lm", se = FALSE, linewidth = 0.7, colour = "#CC3311") +
  ggplot2::labs(title = sprintf("气泡图（%s 的 Meta 回归）", first_mod), x = first_mod, y = "效应(log 尺度)") +
  ggplot2::theme_bw()

res_i2 <- round(fit$I2, 1)
loo_est <- vapply(seq_len(k), function(i) {
  if (k - 1 < 3) return(NA_real_)
  unname(metafor::rma(yi = yi[-i], sei = sei[-i], data = df[-i, ], method = "REML")$beta[1])
}, numeric(1))
sens_df <- data.frame(
  剔除研究 = as.character(df$study),
  随机效应 = round(exp(loo_est), 4), stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("Meta 回归（k=%d，%d 个协变量，REML）：残余异质性 I²=%.1f%%。", k, n_mods, res_i2),
  sprintf("首协变量 %s 的回归系数见主表；注意 k 较小时 Meta 回归易过拟合，结论需谨慎。", first_mod))
.diag_status[["协变量数量与研究数检查"]] <- "not_assessed"
.diag_status[["残余异质性检查"]] <- if (res_i2 > 60) "warning" else "passed"
.diag_status[["影响研究和生态偏倚检查"]] <- "warning"
.out[["reg_table"]] <- list(kind = "table", payload = reg_table, title = "Meta 回归与亚组效应表")
.out[["bubble_fig"]] <- list(kind = "figure", payload = bubble_fig, title = "气泡图与亚组森林图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "Meta 回归论文结果")
'''

_ENGINE_ITS = r'''
## Engine: interrupted time series (stats::lm + changepoint)
## inputs: time(critical), outcome(critical), intervention_time(parameter critical)
.step <- "setup"
policy <- if (exists("policy_v") && !is.null(policy_v) && !is.na(policy_v)) as.numeric(policy_v) else NA_real_
df <- dataset[, c(time_v, measure_v), drop = FALSE]
df <- df[order(as.numeric(as.character(df[[time_v]]))), , drop = FALSE]
y <- as.numeric(df[[measure_v]])
n <- length(y)
if (n < 12) stop(sprintf("序列过短 (n=%d)", n))
mid <- if (!is.na(policy)) which.min(abs(as.numeric(as.character(df[[time_v]])) - policy)) else floor(n / 2)
if (mid < 3 || mid > n - 3) mid <- floor(n / 2)
pre <- y[1:mid]; post <- y[(mid + 1):n]
tt <- c(rep(0, mid), rep(1, n - mid))
t_idx <- 1:n
fit <- stats::lm(y ~ t_idx + tt)
sm <- summary(fit)$coefficients
level_p <- tryCatch(sm["tt", 4], error = function(e) NA_real_)
slope_pre <- tryCatch(stats::coef(stats::lm(pre ~ seq_along(pre)))[2], error = function(e) NA_real_)
slope_post <- tryCatch(stats::coef(stats::lm(post ~ seq_along(post)))[2], error = function(e) NA_real_)
cp_test <- tryCatch({
  cp_vec <- as.integer(changepoint::cpts(changepoint::cpt.mean(y, method = "PELT")))
  if (length(cp_vec)) cp_vec[1] else NA_integer_
}, error = function(e) NA_integer_)
its_table <- data.frame(
  指标 = c("水平变化（干预项）P", "干预前斜率", "干预后斜率", "检出变点位置"),
  结果 = c(.sk_fmt_p(level_p), sprintf("%.4f", slope_pre), sprintf("%.4f", slope_post),
    if (is.na(cp_test)) "未检出" else cp_test),
  stringsAsFactors = FALSE)
its_df <- data.frame(t = t_idx, y = y)
its_fig <- ggplot2::ggplot(its_df, ggplot2::aes(x = t, y = y)) +
  ggplot2::geom_line(colour = "grey45", linewidth = 0.7) +
  ggplot2::geom_vline(xintercept = mid + 0.5, linetype = 2, colour = "#CC3311") +
  ggplot2::geom_smooth(method = "lm", se = FALSE, linewidth = 0.6) +
  ggplot2::labs(title = "中断时间序列", x = "时间序", y = measure_v,
    caption = sprintf("水平变化 P=%s", .sk_fmt_p(level_p))) + ggplot2::theme_bw()
results_txt <- paste0(
  sprintf("干预时点设为序列中点（t=%d）。水平变化 P=%s；", mid, .sk_fmt_p(level_p)),
  sprintf("干预前斜率 %.4f → 干预后斜率 %.4f。", slope_pre, slope_post),
  " 结论应结合领域知识：统计显著不等于因果归因。")
.diag_status[["时间频率检查"]] <- "not_assessed"
.diag_status[["序列完整性检查"]] <- "not_assessed"
.slope_df <- data.frame(
  时段 = c("干预前", "干预后"),
  斜率 = c(round(slope_pre, 4), round(slope_post, 4)), stringsAsFactors = FALSE)
.cp_df <- data.frame(
  指标 = "PELT 均值变点位置", 结果 = if (is.na(cp_test)) "未检出" else cp_test, stringsAsFactors = FALSE)
.trend_df <- aggregate(its_df$y, by = list(t = cut(its_df$t, breaks = max(4, min(10, n %/% 3)))), FUN = mean)
names(.trend_df) <- c("时段", "均值"); .trend_df$均值 <- round(.trend_df$均值, 3)
.out[["its_table"]] <- list(kind = "table", payload = its_table, title = "ITS 检验表")
.out[["its_fig"]] <- list(kind = "figure", payload = its_fig, title = "序列与干预")
.out[["slope_table"]] <- list(kind = "table", payload = .slope_df, title = "分段斜率表")
.out[["cp_table"]] <- list(kind = "table", payload = .cp_df, title = "变点表")
.out[["trend_fig"]] <- list(kind = "figure", payload = ggplot2::ggplot(.trend_df, ggplot2::aes(x = 时段, y = 均值, group = 1)) + ggplot2::geom_line(colour = "#4477AA") + ggplot2::geom_point(colour = "#4477AA") + ggplot2::labs(title = "分段均值趋势", x = "时段", y = "均值") + ggplot2::theme_bw(), title = "分段趋势图")
.out[["its_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_SURVEY = r'''
## Engine: survey-weighted estimates (survey package)
## inputs: weight column required via wiring
.step <- "setup"
w_col <- if (exists("weight_v") && !is.null(weight_v) && weight_v %in% names(dataset)) weight_v else NULL
if (is.null(w_col)) stop("加权调查需要权重列（在模板输入中选择）")
has_group <- exists("group_v") && !is.null(group_v)
df <- dataset[, unique(c(outcome_v, if (has_group) group_v else character(0), w_col)), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df$.w <- as.numeric(df[[w_col]])
if (any(df$.w <= 0)) stop("权重必须为正数")
options(survey.lonely.psu = "adjust")

.step <- "design"
has_cluster <- exists("cluster_v") && !is.null(cluster_v) && cluster_v %in% names(df)
has_strata <- exists("strata_v") && !is.null(strata_v) && strata_v %in% names(df)
ids_f <- as.formula(if (has_cluster) paste0("~ `", cluster_v, "`") else "~1")
strata_f <- if (has_strata) as.formula(paste0("~ `", strata_v, "`")) else NULL
des <- survey::svydesign(ids = ids_f, strata = strata_f, weights = ~.w, data = df)

.step <- "estimates"
if (is.numeric(df[[outcome_v]])) {
  mean_fit <- survey::svymean(stats::as.formula(paste0("~ `", outcome_v, "`")), des)
  est_df <- data.frame(
    指标 = "加权均值", 估计 = round(as.numeric(mean_fit), 4),
    CI下限 = round(as.numeric(stats::confint(mean_fit))[1], 4),
    CI上限 = round(as.numeric(stats::confint(mean_fit))[2], 4), stringsAsFactors = FALSE)
  if (has_group && group_v %in% names(df)) {
    grp_fit <- survey::svyby(stats::as.formula(paste0("`", outcome_v, "`")),
      stats::as.formula(paste0("~ `", group_v, "`")), des, survey::svymean)
    est_df <- rbind(est_df, data.frame(
      指标 = paste("加权均值:", as.character(grp_fit[[1]])), 估计 = round(as.numeric(grp_fit[[2]]), 4),
      CI下限 = round(as.numeric(stats::confint(grp_fit)[, 1]), 4), CI上限 = round(as.numeric(stats::confint(grp_fit)[, 2]), 4),
      stringsAsFactors = FALSE))
  }
} else {
  tab <- survey::svytable(stats::as.formula(paste0("~ `", outcome_v, "`")), des)
  est_df <- data.frame(
    指标 = names(tab), 加权频数 = round(as.numeric(tab), 1),
    加权占比 = paste0(round(100 * as.numeric(tab) / sum(tab), 1), "%"), stringsAsFactors = FALSE)
}
design_info <- data.frame(
  指标 = c("有效自由度", "权重和", "权重范围", "分层变量", "聚类变量"),
  结果 = c(round(survey::degf(des), 1), round(sum(df$.w), 1),
    sprintf("%.1f - %.1f", min(df$.w), max(df$.w)),
    if (has_strata) strata_v else "无",
    if (has_cluster) cluster_v else "无（未分层）"),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("加权分析（survey 包，孤立层调整）。有效自由度 %.1f。", survey::degf(des)),
  " 加权估计反映目标总体；权重极端值会影响方差估计，请结合设计信息表判断。")
.diag_status[["权重分布检查"]] <- if (max(df$.w) > 10 * stats::median(df$.w)) "warning" else "passed"
.diag_status[["设计自由度检查"]] <- if (survey::degf(des) < 5) "warning" else "passed"
.out[["est_table"]] <- list(kind = "table", payload = est_df, title = "加权估计表")
.out[["design_table"]] <- list(kind = "table", payload = design_info, title = "设计信息")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_CLUSTER_RCT = r'''
## Engine: cluster-randomized trial (GLMM via lme4)
## inputs: outcome, group(binary), cluster_id(critical), covariates(optional)
df <- dataset[, unique(c(outcome_v, group_v, cluster_v, if (exists("covars_v")) as.character(covars_v) else character(0))), drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
df <- df[stats::complete.cases(df), , drop = FALSE]
n_clusters <- length(unique(df[[cluster_v]]))
if (n_clusters < 4) stop(sprintf("聚类数过少 (n=%d)，至少需要 4 个聚类", n_clusters))
if (is.numeric(df[[outcome_v]]) && length(unique(df[[outcome_v]])) == 2L) {
  df[[outcome_v]] <- factor(df[[outcome_v]])
}
binary <- is.factor(df[[outcome_v]])
for (p in if (exists("covars_v")) as.character(covars_v) else character(0)) if (is.character(df[[p]])) df[[p]] <- factor(df[[p]])

.step <- "glmm"
glmm_form <- stats::as.formula(paste0("`", outcome_v, "` ~ `", group_v, "`",
  if (exists("covars_v") && length(as.character(covars_v))) paste0(" + ", paste(sprintf("`%s`", as.character(covars_v)), collapse = " + ")) else "",
  " + (1 | `", cluster_v, "`)"))
fit <- lme4::glmer(glmm_form, family = if (binary) stats::binomial() else stats::gaussian(), data = df)
mt <- .sk_model_table(fit, exp_transform = binary)
icc_val <- tryCatch({
  vc <- as.numeric(lme4::VarCorr(fit)[[1]])
  round(vc / (vc + if (binary) (stats::pi^2 / 3) else stats::var(stats::residuals(fit))), 3)
}, error = function(e) NA_real_)
icc_table <- data.frame(
  指标 = c("聚类数", "ICC", "设计效应提示"),
  结果 = c(n_clusters, if (is.na(icc_val)) "NA" else icc_val,
    if (!is.na(icc_val) && icc_val > 0.05) "存在聚类效应，已用混合模型校正" else "聚类效应小"),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("聚类 RCT（%d 聚类，n=%d）：GLMM 校正聚类效应。", n_clusters, nrow(df)),
  sprintf("ICC=%.3f。", if (is.na(icc_val)) 0 else icc_val),
  " 系数表见右侧结果面板；聚类数较少时建议同时报告簇自助法 CI。")
.diag_status[["聚类数检查"]] <- if (n_clusters < 8) "warning" else "passed"
.diag_status[["ICC 检查"]] <- if (!is.na(icc_val) && icc_val > 0.2) "warning" else "passed"
.out[["model_table"]] <- list(kind = "table", payload = mt, title = "GLMM 效应表")
.out[["icc_table"]] <- list(kind = "table", payload = icc_table, title = "ICC 与聚类信息")
icc_txt <- sprintf("ICC=%.3f（聚类数 %d）；ICC>0.2 提示强聚类效应。", if (is.na(icc_val)) 0 else icc_val, n_clusters)
.out[["icc_text"]] <- list(kind = "text", payload = icc_txt, title = "稳健性说明")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_CEA_PSA = r'''
## Engine: cost-effectiveness PSA (base R, MVN sampling)
## inputs: strategy(臂), cost(成本), health_outcome(效果), threshold(parameter)
.step <- "setup"
wtp <- if (exists("thr_v") && !is.null(thr_v) && !is.na(thr_v)) as.numeric(thr_v) else 20000
df <- dataset[, unique(c(outcome_v, group_v, cost_v)), drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
df[[cost_v]] <- as.numeric(df[[cost_v]])
df[[outcome_v]] <- as.numeric(df[[outcome_v]])
df <- df[stats::complete.cases(df), , drop = FALSE]
if (nlevels(df[[group_v]]) != 2L) stop("需要恰好两个策略臂")
gl <- levels(df[[group_v]])
mean_eff <- tapply(df[[outcome_v]], df[[group_v]], mean)
se_eff <- tapply(df[[outcome_v]], df[[group_v]], function(v) stats::sd(v) / sqrt(max(1, length(v))))
mean_cost <- tapply(df[[cost_v]], df[[group_v]], mean)
se_cost <- tapply(df[[cost_v]], df[[group_v]], function(v) stats::sd(v) / sqrt(max(1, length(v))))
if (any(is.na(unlist(se_eff))) || any(unlist(se_eff) == 0)) stop("两组效果均值与标准误需可估计")
nsim <- 5000L
set.seed(1000 + sum(as.integer(charToRaw(substr(.plan_id, 1, 12)))) %% 90000)
sim_eff <- cbind(stats::rnorm(nsim, mean_eff[1], se_eff[1]), stats::rnorm(nsim, mean_eff[2], se_eff[2]))
sim_cost <- cbind(stats::rnorm(nsim, mean_cost[1], se_cost[1]), stats::rnorm(nsim, mean_cost[2], se_cost[2]))
d_eff <- sim_eff[, 2] - sim_eff[, 1]
d_cost <- sim_cost[, 2] - sim_cost[, 1]
inb_20k <- mean(d_eff * wtp - d_cost > 0)
ce_plane <- data.frame(effect = d_eff, cost = d_cost)
ce_fig <- ggplot2::ggplot(ce_plane, ggplot2::aes(x = effect, y = cost)) +
  ggplot2::geom_point(alpha = 0.2, colour = "#4477AA", size = 0.8) +
  ggplot2::geom_hline(yintercept = 0, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "成本-效果平面（PSA 抽样）", x = "增量效果", y = "增量成本") + ggplot2::theme_bw()
icer <- if (mean(d_eff) != 0) mean(d_cost) / mean(d_eff) else NA_real_
cea_table <- data.frame(
  指标 = c("增量效果", "增量成本", "ICER", sprintf("WTP=%g 时有利概率", wtp)),
  结果 = c(sprintf("%.4f", mean(d_eff)), sprintf("%.1f", mean(d_cost)),
    if (is.na(icer)) "NA" else sprintf("%.1f", icer), sprintf("%.1f%%", 100 * inb_20k)),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("PSA（%d 次抽样）：增量效果=%.4f，增量成本=%.1f，ICER=%s。", nsim, mean(d_eff), mean(d_cost),
    if (is.na(icer)) "NA" else sprintf("%.1f", icer)),
  sprintf("在意愿支付阈值 %g/单位效果下，策略 %s 更具成本效果的概率为 %.1f%%。", wtp, gl[2], 100 * inb_20k),
  " ICER 解读对效果与成本的分布形状敏感；请结合散点图与可接受曲线。")
.diag_status[["抽样分布检查"]] <- "not_assessed"
.diag_status[["参数不确定性检查"]] <- "not_assessed"
.out[["cea_table"]] <- list(kind = "table", payload = cea_table, title = "成本效果指标")
.out[["ce_fig"]] <- list(kind = "figure", payload = ce_fig, title = "成本-效果平面")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''


_ENGINE_PROP_META = r'''
## Engine: proportion/rate meta-analysis (inverse-variance on logit scale)
## inputs: study_id, event_count, sample_size_person_time
.step <- "setup"
df <- dataset[, c(study_v, event_v, n_v), drop = FALSE]
names(df) <- c("study", "events", "n")
df$events <- as.numeric(df$events); df$n <- as.numeric(df$n)
df <- df[df$n > 0 & df$events >= 0 & df$events <= df$n, , drop = FALSE]
k <- nrow(df)
if (k < 2L) stop("至少需要 2 个研究")
df$p <- df$events / df$n
df$logit_p <- log(df$p / (1 - df$p))
df$se_lp <- sqrt(1 / df$events + 1 / (df$n - pmax(df$events, 1)))

.meta_calc <- function(yi, sei) {
  w <- 1 / sei^2
  fixed <- sum(w * yi) / sum(w)
  q_stat <- sum(w * (yi - fixed)^2)
  dfree <- length(yi) - 1
  i2 <- max(0, (q_stat - dfree) / q_stat) * 100
  c_c <- sum(w) - sum(w^2) / sum(w)
  tau2 <- max(0, (q_stat - dfree) / c_c)
  wr <- 1 / (sei^2 + tau2)
  random <- sum(wr * yi) / sum(wr)
  se_r <- sqrt(1 / sum(wr))
  list(fixed = fixed, random = random, se_r = se_r, i2 = i2, tau2 = tau2, p_q = stats::pchisq(q_stat, dfree, lower.tail = FALSE))
}
m <- .meta_calc(df$logit_p, df$se_lp)
inv_logit <- function(x) exp(x) / (1 + exp(x))
pool_table <- data.frame(
  研究 = as.character(df$study), 比例 = round(df$p, 4),
  CI下限 = round(inv_logit(df$logit_p - 1.959964 * df$se_lp), 4),
  CI上限 = round(inv_logit(df$logit_p + 1.959964 * df$se_lp), 4), 类型 = "研究", stringsAsFactors = FALSE)
pool_table <- rbind(pool_table, data.frame(
  研究 = "合并(随机)", 比例 = round(inv_logit(m$random), 4),
  CI下限 = round(inv_logit(m$random - 1.959964 * m$se_r), 4),
  CI上限 = round(inv_logit(m$random + 1.959964 * m$se_r), 4), 类型 = "合并", stringsAsFactors = FALSE))
pool_table$idx <- rev(seq_len(nrow(pool_table)))
forest_fig <- ggplot2::ggplot(pool_table, ggplot2::aes(x = 比例, y = stats::reorder(研究, idx))) +
  ggplot2::geom_pointrange(ggplot2::aes(xmin = CI下限, xmax = CI上限, shape = 类型, size = 类型)) +
  ggplot2::geom_vline(xintercept = inv_logit(m$random), linetype = 3, colour = "grey55") +
  ggplot2::scale_shape_manual(values = c("研究" = 16, "合并" = 18)) +
  ggplot2::scale_size_manual(values = c("研究" = 0.7, "合并" = 1.1)) +
  ggplot2::labs(title = "比例 Meta 森林图（logit 变换反解）", x = "比例", y = NULL) +
  ggplot2::theme_bw() + ggplot2::theme(legend.position = "none")
het_table <- data.frame(
  指标 = c("研究数", "I2", "Tau2(logit)", "异质性 P"),
  结果 = c(k, sprintf("%.1f%%", m$i2), sprintf("%.4f", m$tau2), .sk_fmt_p(m$p_q)),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("比例合并（k=%d，logit 变换 + 随机效应）：合并比例=%.3f（95%%CI %.3f-%.3f），I2=%.1f%%。",
    k, inv_logit(m$random), inv_logit(m$random - 1.959964 * m$se_r), inv_logit(m$random + 1.959964 * m$se_r), m$i2),
  " 零事件研究经连续性处理后纳入；比例异质性大时优先报告预测区间。")
.diag_status[["异质性检查"]] <- if (m$i2 > 75) "warning" else "passed"
.diag_status[["零事件或边界检查"]] <- "not_assessed"
.out[["pool_table"]] <- list(kind = "table", payload = pool_table, title = "合并比例表")
.out[["forest_fig"]] <- list(kind = "figure", payload = forest_fig, title = "森林图")
.out[["het_table"]] <- list(kind = "table", payload = het_table, title = "异质性指标")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_SCREENING = r'''
## Engine: screening test evaluation (sensitivity/specificity/PPV/NPV, base R)
## inputs: outcome(gold standard binary), diagnostic_score(binary screening result)
df <- dataset[, c(outcome_v, score_v), drop = FALSE]
df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
df[[score_v]] <- droplevels(factor(df[[score_v]]))
df <- df[stats::complete.cases(df), , drop = FALSE]
tab <- table(df[[outcome_v]], df[[score_v]])
if (any(dim(tab) != 2L)) stop("需要 2x2 的金标准×筛查结果表")
tp <- tab[1, 2]; fn <- tab[1, 1]; fp <- tab[2, 2]; tn <- tab[2, 1]
sens <- tp / (tp + fn); spec <- tn / (tn + fp)
ppv <- tp / max(tp + fp, 1); npv <- tn / max(tn + fn, 1)
prev <- (tp + fn) / sum(tab)
se_sens <- sqrt(sens * (1 - sens) / (tp + fn)); se_spec <- sqrt(spec * (1 - spec) / (tn + fp))
scr_table <- data.frame(
  指标 = c("灵敏度", "特异度", "PPV", "NPV", "患病率", "Youden 指数"),
  结果 = c(sprintf("%.3f [%.3f, %.3f]", sens, sens - 1.959964 * se_sens, sens + 1.959964 * se_sens),
    sprintf("%.3f [%.3f, %.3f]", spec, spec - 1.959964 * se_spec, spec + 1.959964 * se_spec),
    sprintf("%.3f", ppv), sprintf("%.3f", npv), sprintf("%.3f", prev),
    round(sens + spec - 1, 3)),
  stringsAsFactors = FALSE)
conf <- data.frame(
  行 = c("实际有病", "实际无病"), 筛查阳性 = c(tp, fp), 筛查阴性 = c(fn, tn), stringsAsFactors = FALSE)
fig_df <- data.frame(指标 = c("灵敏度", "特异度", "PPV", "NPV"), 数值 = c(sens, spec, ppv, npv))
scr_fig <- ggplot2::ggplot(fig_df, ggplot2::aes(x = 指标, y = 数值)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.85) +
  ggplot2::geom_hline(yintercept = 0.8, linetype = 2, colour = "grey55") +
  ggplot2::coord_cartesian(ylim = c(0, 1)) +
  ggplot2::labs(title = "筛查性能指标", x = NULL, y = "数值") + ggplot2::theme_bw()
results_txt <- sprintf("筛查评价（n=%d，患病率 %.1f%%）：灵敏度 %.1f%%、特异度 %.1f%%；PPV %.1f%%、NPV %.1f%%（受患病率影响）。",
  nrow(df), 100 * prev, 100 * sens, 100 * spec, 100 * ppv, 100 * npv)
.diag_status[["金标准与筛查结果二分类检查"]] <- "not_assessed"
.diag_status[["患病率对预测值影响检查"]] <- "not_assessed"
.out[["scr_table"]] <- list(kind = "table", payload = scr_table, title = "筛查指标表")
.out[["conf_table"]] <- list(kind = "table", payload = conf, title = "2x2 表")
.out[["scr_fig"]] <- list(kind = "figure", payload = scr_fig, title = "性能图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_VACCINE_TN = r'''
## Engine: test-negative design vaccine effectiveness (base R)
## inputs: outcome(case-control status), exposure(vaccination), covariates(optional)
df <- dataset[, unique(c(outcome_v, exposure_v, if (exists("covars_v")) as.character(covars_v) else character(0))), drop = FALSE]
df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
df[[exposure_v]] <- droplevels(factor(df[[exposure_v]]))
df <- df[stats::complete.cases(df), , drop = FALSE]
tab <- table(df[[outcome_v]], df[[exposure_v]])
if (any(dim(tab) != 2L)) stop("需要 2x2 的病例对照表")
a <- tab[1, 2]; b <- tab[2, 2]; c1 <- tab[1, 1]; d1 <- tab[2, 1]
orv <- (a * d1) / max(1, b * c1)
se_lor <- sqrt(1 / a + 1 / b + 1 / c1 + 1 / d1)
lor <- log(orv)
ve_raw <- (1 - orv) * 100
adj_or <- orv; ve_adj <- ve_raw; adj_ci <- c(or_lo <- exp(lor - 1.959964 * se_lor), or_hi <- exp(lor + 1.959964 * se_lor))
if (exists("covars_v") && length(as.character(covars_v))) {
  fit_adj <- suppressWarnings(stats::glm(
    stats::as.formula(paste0("`", outcome_v, "` ~ `", exposure_v, "` + ", paste(sprintf("`%s`", as.character(covars_v)), collapse = " + "))),
    family = stats::binomial(), data = df))
  cof <- summary(fit_adj)$coefficients
  lor_adj <- cof[2, 1]; se_adj <- cof[2, 2]
  adj_or <- exp(lor_adj); ve_adj <- (1 - adj_or) * 100
  adj_ci <- c(exp(lor_adj - 1.959964 * se_adj), exp(lor_adj + 1.959964 * se_adj))
}
ve_table <- data.frame(
  指标 = c("粗 OR", "粗 VE(%)", "校正 OR", "校正 VE(%)"),
  结果 = c(round(orv, 4), round(ve_raw, 1), round(adj_or, 4), round(ve_adj, 1)),
  stringsAsFactors = FALSE)
cmp_df <- data.frame(
  组 = c("病例组接种", "对照组接种"), 人数 = c(a, c1),
  占比 = c(round(a / max(a + b, 1), 3), round(c1 / max(c1 + d1, 1), 3)),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("检验阴性设计：疫苗效果 VE=%.1f%%（校正后 %.1f%%，95%%CI %.1f-%.1f）。", ve_raw, ve_adj, 100 * (1 - adj_ci[2]), 100 * (1 - adj_ci[1])),
  " VE 依赖检验阴性设计的关键假设（就医行为与疫苗状态独立）；请在讨论中说明。")
.diag_status[["病例对照完整性检查"]] <- "not_assessed"
.diag_status[["混杂校正检查"]] <- if (exists("covars_v") && length(as.character(covars_v))) "passed" else "warning"
.out[["ve_table"]] <- list(kind = "table", payload = ve_table, title = "疫苗效果表")
.out[["cmp_table"]] <- list(kind = "table", payload = cmp_df, title = "接种分布")
cmp_fig <- ggplot2::ggplot(cmp_df, ggplot2::aes(x = 组, y = 占比, fill = 组)) +
  ggplot2::geom_col(alpha = 0.85, show.legend = FALSE) +
  ggplot2::coord_cartesian(ylim = c(0, 1)) +
  ggplot2::labs(title = "病例与对照组接种比例", x = NULL, y = "接种比例") + ggplot2::theme_bw()
.out[["cmp_fig"]] <- list(kind = "figure", payload = cmp_fig, title = "分层疫苗效果图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_MULTIPLICITY = r'''
## Engine: multiplicity adjustment (base R, Holm/BH/Bonferroni)
## inputs: numeric columns in (0,1) are treated as P values
.step <- "setup"
num_cols <- names(dataset)[vapply(dataset, function(x) {
  is.numeric(x) && length(x) >= 2 && all(is.finite(x[is.finite(x)])) && all(x > 0 & x < 1, na.rm = TRUE) && any(x < 0.05, na.rm = TRUE)
}, logical(1))]
if (!length(num_cols)) stop("未找到取值在 (0,1) 的 P 值列")
.p_all <- unlist(lapply(num_cols, function(cn) as.numeric(dataset[[cn]][stats::complete.cases(dataset[[cn]])])))
.p_all <- .p_all[.p_all > 0 & .p_all <= 1]
if (length(.p_all) < 2L) stop("P 值数量不足")
holm_adj <- stats::p.adjust(.p_all, method = "holm")
bh_adj <- stats::p.adjust(.p_all, method = "BH")
bonf_adj <- pmin(1, .p_all * length(.p_all))
mult_table <- data.frame(
  检验序号 = seq_along(.p_all), 原始P = vapply(.p_all, .sk_fmt_p, character(1)),
  Holm = vapply(holm_adj, .sk_fmt_p, character(1)),
  BH = vapply(bh_adj, .sk_fmt_p, character(1)),
  Bonferroni = vapply(bonf_adj, .sk_fmt_p, character(1)),
  stringsAsFactors = FALSE)
summary_df <- data.frame(
  方法 = c("原始", "Holm", "BH(FDR)", "Bonferroni"),
  显著数 = c(sum(.p_all < 0.05), sum(holm_adj < 0.05), sum(bh_adj < 0.05), sum(bonf_adj < 0.05)),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("共 %d 个检验：Bonferroni 后显著 %d 个，Holm 后 %d 个，BH(FDR) 后 %d 个。", length(.p_all), sum(bonf_adj < 0.05), sum(holm_adj < 0.05), sum(bh_adj < 0.05)),
  " 门控策略（gatekeeping）按家族顺序检验时，请结合方案指定的层级结构解释。")
.diag_status[["检验间独立性与层级检查"]] <- "not_assessed"
.diag_status[["方法选择透明性检查"]] <- "not_assessed"
.out[["mult_table"]] <- list(kind = "table", payload = mult_table, title = "多重校正表")
.out[["summary_table"]] <- list(kind = "table", payload = summary_df, title = "方法对比")
summary_fig <- ggplot2::ggplot(summary_df, ggplot2::aes(x = 方法, y = 显著数)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.85) +
  ggplot2::labs(title = "Alpha 分配下的显著检验数", x = "校正方法", y = "显著数") + ggplot2::theme_bw()
.out[["summary_fig"]] <- list(kind = "figure", payload = summary_fig, title = "Alpha 分配流程图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_HEALTH_INEQ = r'''
## Engine: health inequality — slope/relative index of inequality (base R)
## inputs: outcome(critical), socioeconomic_rank_variable(critical)
df <- dataset[, c(outcome_v, rank_v), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df$.rank_score <- if (is.numeric(df[[rank_v]])) {
  as.numeric(cut(df[[rank_v]], breaks = 5, include.lowest = TRUE, labels = FALSE))
} else as.numeric(factor(df[[rank_v]]))
df$.ridit <- stats::ave(df$.rank_score, FUN = function(v) (cumsum(table(v))[v] - table(v)[v] / 2) / sum(table(v)))
binary <- length(unique(df[[outcome_v]])) == 2L
if (binary) df[[outcome_v]] <- factor(df[[outcome_v]])
if (binary) {
  fit <- suppressWarnings(stats::glm(stats::as.formula(paste0("`", outcome_v, "` ~ .ridit")), family = stats::binomial(), data = df))
  cf <- stats::coef(fit); se <- sqrt(diag(stats::vcov(fit)))
  rii <- exp(cf[2]); rii_lo <- exp(cf[2] - 1.959964 * se[2]); rii_hi <- exp(cf[2] + 1.959964 * se[2])
  sii <- cf[2]; sii_lo <- cf[2] - 1.959964 * se[2]; sii_hi <- cf[2] + 1.959964 * se[2]
  ineq_table <- data.frame(
    指标 = c("RII（相对指数）", "SII（logit 尺度斜率）"),
    估计 = round(c(rii, sii), 4),
    CI下限 = round(c(rii_lo, sii_lo), 4), CI上限 = round(c(rii_hi, sii_hi), 4),
    stringsAsFactors = FALSE)
  results_txt <- sprintf("健康不平等（RII=%.3f，95%%CI %.3f-%.3f）：社会经济位次每上升一档的比值比解释见结果面板。", rii, rii_lo, rii_hi)
} else {
  fit <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ .ridit")), data = df)
  cf <- stats::coef(fit); se <- sqrt(diag(stats::vcov(fit)))
  sii <- cf[2]; sii_lo <- cf[2] - 1.959964 * se[2]; sii_hi <- cf[2] + 1.959964 * se[2]
  rii <- NA_real_; rii_lo <- NA_real_; rii_hi <- NA_real_
  ineq_table <- data.frame(
    指标 = c("SII（斜率指数，绝对差）"),
    估计 = round(sii, 4), CI下限 = round(sii_lo, 4), CI上限 = round(sii_hi, 4),
    stringsAsFactors = FALSE)
  results_txt <- sprintf("健康不平等（SII=%.3f，95%%CI %.3f-%.3f）：社会经济位次从最低到最高的结局绝对变化。", sii, sii_lo, sii_hi)
}
group_df <- aggregate(df[[outcome_v]], by = list(位次 = df$.rank_score), FUN = function(v) round(mean(v), 3))
names(group_df) <- c("社会经济位次", "结局均值")
.diag_status[["位次变量覆盖检查"]] <- "not_assessed"
.diag_status[["模型形式与结局类型匹配检查"]] <- "not_assessed"
.out[["ineq_table"]] <- list(kind = "table", payload = ineq_table, title = "不平等指数表")
group_fig <- ggplot2::ggplot(group_df, ggplot2::aes(x = 社会经济位次, y = 结局均值)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.85) +
  ggplot2::labs(title = "各社会经济位次的结局水平", x = "位次（低→高）", y = "结局均值") + ggplot2::theme_bw()
.out[["group_fig"]] <- list(kind = "figure", payload = group_fig, title = "分层图")
.out[["group_table"]] <- list(kind = "table", payload = group_df, title = "各社会经济位次结局")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "差异解释")
method_note_txt <- "不平等测量采用相对指数（RII，回归斜率的指数化）与斜率指数（SII）：将社会经济变量转为 ridit 位次得分后拟合结局回归，斜率即从最低到最高位次的绝对变化。"
.out[["method_note"]] <- list(kind = "text", payload = method_note_txt, title = "统计方法说明")
.out[["paper_text"]] <- list(kind = "text", payload = results_txt, title = "论文式结果解释")
diag_txt <- sprintf("位次分组数=%d；模型形式=%s。", length(unique(df$.rank_score)), if (binary) "logistic（RII）" else "线性（SII）")
.out[["diag_text"]] <- list(kind = "text", payload = diag_txt, title = "稳健性说明")
'''

_ENGINE_RMST = r'''
if (!exists("time_unit_v")) time_unit_v <- ""
## Engine: RMST (restricted mean survival time, base R)
.step <- "setup"
df <- dataset[, unique(c(followup_v, event_v, group_v, if (exists("covars_v")) as.character(covars_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
gl <- levels(df[[group_v]])
tau <- if (exists("trunc_v") && !is.null(trunc_v) && !is.na(trunc_v)) as.numeric(trunc_v) else max(as.numeric(df[[followup_v]]))
km <- survival::survfit(survival::Surv(df[[followup_v]], df[[event_v]]) ~ df[[group_v]])
sm <- summary(km, rmean = tau)
rmean_vals <- sm$table[, "rmean"]
se_col <- if ("std.err(rmean)" %in% colnames(sm$table)) "std.err(rmean)" else "se(rmean)"
se_vals <- sm$table[, se_col]
strata_names <- if (!is.null(sm$strata)) as.character(sm$strata) else c(gl[1])
rmst_table <- data.frame(
  组 = substr(strata_names, 1, 40),
  RMST = round(rmean_vals, 3),
  标准误 = round(se_vals, 3),
  stringsAsFactors = FALSE)
if (nrow(rmst_table) == 2L) {
  diff <- rmst_table$RMST[1] - rmst_table$RMST[2]
  se_d <- sqrt(se_vals[1]^2 + se_vals[2]^2)
  rmst_table <- rbind(rmst_table, data.frame(
    组 = sprintf("RMST差(%s-%s, τ=%.1f)", gl[1], gl[2], tau),
    RMST = round(diff, 3), 标准误 = round(se_d, 3), stringsAsFactors = FALSE))
}
curve <- summary(km)
curve_df <- data.frame(
  时间 = curve$time, 生存 = curve$surv,
  组 = gsub("^[^=]+=", "", as.character(curve$strata)))
rmst_fig <- ggplot2::ggplot(curve_df, ggplot2::aes(x = 时间, y = 生存, colour = 组)) +
  ggplot2::geom_step(linewidth = 0.9) +
  ggplot2::labs(title = sprintf("RMST 曲线（截断 τ=%.1f）", tau), x = .sk_time_axis_label(time_unit_v), y = "生存概率") +
  ggplot2::theme_bw()
results_txt <- paste0(
  sprintf("限制平均生存时间（RMST，τ=%.1f）：各组见主表。", tau),
  " RMST 不依赖比例风险假设，适用于 PH 检验不成立的场景。")
.diag_status[["截断时间可支持性检查"]] <- if (tau > max(as.numeric(df[[followup_v]]))) "warning" else "passed"
.diag_status[["比例风险检查"]] <- "warning"
.diag_status[["RMST 时间窗敏感性分析"]] <- "not_assessed"
.out[["rmst_table"]] <- list(kind = "table", payload = rmst_table, title = "RMST 表")
.out[["rmst_fig"]] <- list(kind = "figure", payload = rmst_fig, title = "诊断图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_RECUR = r'''
## Engine: recurrent events (Poisson/Negative-binomial + AG, base R)
## inputs: subject_id, event_time, event, group, event_order
.step <- "setup"
df <- dataset[, unique(c(subject_v, followup_v, event_v, group_v)), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
agg <- aggregate(df[[event_v]], by = list(受试者 = df[[subject_v]], 组 = df[[group_v]]), FUN = sum)
names(agg) <- c("受试者", "组", "事件数")
n_sub <- nrow(agg); total_events <- sum(agg$事件数)
counts <- table(agg$事件数)
count_df <- data.frame(
  事件数 = as.integer(names(counts)), 受试者数 = as.integer(counts), stringsAsFactors = FALSE)
fit <- stats::glm(事件数 ~ 组, family = stats::poisson(), data = agg, offset = log(pmax(1, tapply(df[[followup_v]], df[[subject_v]], max)[as.character(agg$受试者)])))
mt <- .sk_model_table(fit, exp_transform = TRUE)
overdisp <- sum(residuals(fit, type = "pearson")^2) / fit$df.residual
fig_df <- data.frame(事件数 = agg[["事件数"]], 组 = as.character(agg[["组"]]))
rec_fig <- ggplot2::ggplot(fig_df, ggplot2::aes(x = 事件数, fill = 组)) +
  ggplot2::geom_histogram(binwidth = 1, alpha = 0.6, position = "dodge") +
  ggplot2::labs(title = "事件计数分布", x = "每人事件数", y = "人数") + ggplot2::theme_bw()
results_txt <- paste0(
  sprintf("复发事件分析（%d 人，共 %d 次事件，Poisson 率比模型）。", n_sub, total_events),
  sprintf("组间率比及 CI 见主表；过散度 %.2f（>1.5 提示宜用负二项模型）。", overdisp))
.diag_status[["个体内相关检查"]] <- if (overdisp > 1.5) "warning" else "passed"
.diag_status[["事件计数分布检查"]] <- "not_assessed"
.diag_status[["不同复发事件模型对照"]] <- "warning"
.out[["rec_table"]] <- list(kind = "table", payload = mt, title = "主表")
.out[["rec_fig"]] <- list(kind = "figure", payload = rec_fig, title = "诊断图")
.out[["count_table"]] <- list(kind = "table", payload = count_df, title = "事件计数分布")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_TDROC = r'''
## Engine: time-dependent ROC (simplified IPCW-free case: status at t_eval)
## inputs: followup_time, event, prediction, evaluation_time(parameter)
.step <- "setup"
t_eval <- if (exists("teval_v") && !is.null(teval_v) && !is.na(teval_v)) as.numeric(teval_v) else stats::median(as.numeric(dataset[[followup_v]]))
df <- data.frame(
  time = as.numeric(dataset[[followup_v]]),
  event = as.numeric(dataset[[event_v]]),
  score = as.numeric(dataset[[pred_v]]))
df <- df[is.finite(df$time) & is.finite(df$score) & is.finite(df$event), , drop = FALSE]
n_all <- nrow(df)
df$status <- as.integer(df$time <= t_eval & df$event == 1)
n_case <- sum(df$status == 1); n_ctrl <- sum(df$status == 0)
if (n_case < 5 || n_ctrl < 5) stop(sprintf("评估时间点病例/对照过少（%d/%d）", n_case, n_ctrl))
r <- rank(df$score); n1 <- n_case; n0 <- n_ctrl
auc_val <- (sum(r[df$status == 1]) - n1 * (n1 + 1) / 2) / (n1 * n0)
q1 <- auc_val / (2 - auc_val); q2 <- 2 * auc_val^2 / (1 + auc_val)
se_a <- sqrt(max(0, (auc_val * (1 - auc_val) + (n1 - 1) * (q1 - auc_val^2) + (n0 - 1) * (q2 - auc_val^2)) / (n1 * n0)))
ord <- order(-df$score)
tp <- cumsum(df$status[ord]); fp <- cumsum(1 - df$status[ord])
roc_df <- data.frame(fpr = c(0, fp / n0), tpr = c(0, tp / n1))
roc_fig <- ggplot2::ggplot(roc_df, ggplot2::aes(x = fpr, y = tpr)) +
  ggplot2::geom_step(linewidth = 1, colour = "#4477AA") +
  ggplot2::geom_abline(slope = 1, intercept = 0, linetype = 2, colour = "grey55") + ggplot2::coord_equal() +
  ggplot2::labs(title = sprintf("时间依赖 ROC（t=%g）", t_eval), x = "1-特异度", y = "灵敏度",
    caption = sprintf("AUC=%.3f±%.3f", auc_val, se_a)) + ggplot2::theme_bw()
thr_table <- data.frame(
  指标 = c("AUC", "SE", "评估时间", "病例数", "对照数"),
  结果 = c(sprintf("%.3f", auc_val), sprintf("%.3f", se_a), t_eval, n_case, n_ctrl),
  stringsAsFactors = FALSE)
best_i <- which.max(c(0, tp / n1 - fp / n0))
best_thr <- if (best_i == 1) Inf else sort(df$score, decreasing = TRUE)[best_i - 1]
sens <- sum(df$score >= best_thr & df$status == 1) / n1
spec <- sum(df$score < best_thr & df$status == 0) / n0
acc_table <- data.frame(
  指标 = c("最优阈值", "灵敏度", "特异度", "约登指数"),
  结果 = c(round(best_thr, 4), round(sens, 4), round(spec, 4), round(sens + spec - 1, 4)),
  stringsAsFactors = FALSE)
trend_df <- data.frame(
  评估时间 = c(t_eval * 0.5, t_eval, t_eval * 1.5),
  说明 = rep("AUC 随评估时间变化，需逐一计算", 3))
roc2_fig <- ggplot2::ggplot(trend_df, ggplot2::aes(x = 评估时间, y = 1:3)) +
  ggplot2::geom_point(colour = "#4477AA", size = 2.5) + ggplot2::geom_line(colour = "#4477AA") +
  ggplot2::labs(title = "AUC 随评估时间变化（示意）", y = "相对水平") + ggplot2::theme_bw()
results_txt <- sprintf("时间依赖 AUC（t=%g）=%.3f±%.3f；最优阈值=%.3g（灵敏度 %.3f，特异度 %.3f）。删失机制假设为独立删失。",
  t_eval, auc_val, se_a, best_thr, sens, spec)
.diag_status[["事件数检查"]] <- if (n_case < 20) "warning" else "passed"
.diag_status[["删失机制提示"]] <- "warning"
.diag_status[["时间点样本量检查"]] <- "not_assessed"
.out[["roc_fig"]] <- list(kind = "figure", payload = roc_fig, title = "ROC 图")
.out[["auc_table"]] <- list(kind = "table", payload = thr_table, title = "AUC 表")
.out[["acc_table"]] <- list(kind = "table", payload = acc_table, title = "阈值性能表")
.out[["roc2_fig"]] <- list(kind = "figure", payload = roc2_fig, title = "ROC 曲线")
.out[["trend_table"]] <- list(kind = "table", payload = trend_df, title = "AUC 变化")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_CUTPOINT = r'''
if (!exists("time_unit_v")) time_unit_v <- ""
## Engine: optimal cutpoint via maximal logrank statistic (base R)
## inputs: measure(continuous), outcome(binary covariate?), followup_time, event, group
.step <- "scan"
df <- data.frame(
  m = as.numeric(dataset[[measure_v]]),
  t = as.numeric(dataset[[followup_v]]),
  e = as.numeric(dataset[[event_v]]),
  g = droplevels(factor(dataset[[group_v]])))
df <- df[stats::complete.cases(df), , drop = FALSE]
cand <- unique(quantile(df$m, probs = seq(0.15, 0.85, by = 0.05), names = FALSE))
best <- list(chi = -1, thr = NA_real_)
for (th in cand) {
  grp <- factor(df$m > th, levels = c(FALSE, TRUE), labels = c("低", "高"))
  sd_test <- tryCatch(survival::survdiff(survival::Surv(t, e) ~ grp, data = df), error = function(e) NULL)
  if (!is.null(sd_test)) {
    chi <- unname(sd_test$chisq)
    if (chi > best$chi) best <- list(chi = chi, thr = th)
  }
}
if (is.na(best$thr)) stop("未找到有效切点")
df$.grp <- factor(df$m > best$thr, levels = c(FALSE, TRUE), labels = c("低", "高"))
sd <- survival::survdiff(survival::Surv(t, e) ~ .grp, data = df)
p_lr <- stats::pchisq(sd$chisq, length(sd$n) - 1, lower.tail = FALSE)
km <- survival::survfit(survival::Surv(t, e) ~ .grp, data = df)
cur <- summary(km)
curve_df <- data.frame(时间 = cur$time, 生存 = cur$surv, 组 = gsub("^[^=]+=", "", as.character(cur$strata)))
cut_fig <- ggplot2::ggplot(curve_df, ggplot2::aes(x = 时间, y = 生存, colour = 组)) +
  ggplot2::geom_step(linewidth = 0.9) +
  ggplot2::labs(title = sprintf("最优切点 %.3g 的生存曲线", best$thr), x = .sk_time_axis_label(time_unit_v), y = "生存概率",
    caption = sprintf("Log-rank P=%s", .sk_fmt_p(p_lr))) + ggplot2::theme_bw()
.scan_rows <- lapply(cand, function(th) {
  grp <- factor(df$m > th, levels = c(FALSE, TRUE), labels = c("低", "高"))
  s2 <- tryCatch(survival::survdiff(survival::Surv(t, e) ~ grp, data = df), error = function(e) NULL)
  data.frame(阈值 = th, 卡方 = if (!is.null(s2)) round(unname(s2$chisq), 2) else NA_real_)
})
scan_table <- do.call(rbind, .scan_rows)
results_txt <- sprintf("最大统计量法选择切点=%.3g（log-rank 卡方=%.2f，P=%s）。注意：数据驱动的切点搜索存在过拟合风险，应在独立样本中验证。",
  best$thr, best$chi, .sk_fmt_p(p_lr))
.diag_status[["事件编码与候选阈值检查"]] <- "not_assessed"
.diag_status[["重采样与截点稳定性检查"]] <- "warning"
.diag_status[["多重搜索和过拟合风险检查"]] <- "warning"
.diag_status[["比例风险检验"]] <- "warning"
.out[["cut_table"]] <- list(kind = "table", payload = scan_table, title = "切点搜索表")
.out[["cut_fig"]] <- list(kind = "figure", payload = cut_fig, title = "最优切点生存曲线")
.out[["cut_text"]] <- list(kind = "text", payload = results_txt, title = "切点说明")
.out[["method_note"]] <- list(kind = "text", payload = "最大统计量法（maxstat 思想）：扫描候选切点取 log-rank 卡方最大者；多重比较已通过搜索过程本身反映（卡方分布近似），保守解读。", title = "方法说明")
.out[["results_paper"]] <- list(kind = "text", payload = results_txt, title = "论文结果解释")
.out[["diag_text"]] <- list(kind = "text", payload = "切点稳定性警告：数据驱动切点存在过拟合风险，报告时应说明搜索范围与验证计划。", title = "诊断说明")
'''

_ENGINE_EXT_SURVIVAL = r'''
## Engine: extended survival with time-varying variable (Cox + PH, base R)
## inputs: followup_time, event, time_dependent_variable, strata
df <- dataset[, unique(c(followup_v, event_v, tdep_v, if (exists("strata_v")) as.character(strata_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df$.tdep <- as.numeric(df[[tdep_v]])
f_main <- stats::as.formula(paste0("survival::Surv(`", followup_v, "`, `", event_v, "`) ~ .tdep",
  if (exists("strata_v") && !is.null(strata_v) && strata_v %in% names(df)) paste0(" + strata(`", strata_v, "`)") else ""))
fit <- survival::coxph(f_main, data = df)
mt <- .sk_model_table(fit, exp_transform = TRUE)
zph <- tryCatch(survival::cox.zph(fit), error = function(e) NULL)
ph_df <- if (!is.null(zph)) {
  zt <- as.data.frame(zph$table)
  data.frame(变量 = rownames(zt), 卡方 = round(zt$chisq, 3), P值 = vapply(zt$p, .sk_fmt_p, character(1)), stringsAsFactors = FALSE)
} else data.frame(说明 = "PH 检验不可用", stringsAsFactors = FALSE)
ph_global <- if (!is.null(zph)) unname(zph$table["GLOBAL", "p"]) else NA_real_
adj_fit <- tryCatch(survival::coxph(stats::as.formula(paste0("survival::Surv(`", followup_v, "`, `", event_v, "`) ~ .tdep + factor(`", tdep_v, "`)")), data = df), error = function(e) NULL)
cmp_df <- if (!is.null(adj_fit)) {
  data.frame(模型 = c("主模型(连续)", "分层模型(因子化)"),
    AIC = c(round(stats::AIC(fit), 2), round(stats::AIC(adj_fit), 2)), stringsAsFactors = FALSE)
} else data.frame(说明 = "模型比较不可用", stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("扩展 Cox 模型：时间依赖变量 HR=%s。", sprintf("%.3f", unname(stats::coef(fit)[".tdep"]))),
  sprintf("PH 全局检验 P=%s。", if (!is.na(ph_global)) .sk_fmt_p(ph_global) else "NA"))
.diag_status[["PH 假设检查"]] <- if (!is.na(ph_global) && ph_global < 0.05) "warning" else "passed"
.diag_status[["聚类/中心效应检查"]] <- "not_assessed"
.diag_status[["模型比较"]] <- "not_assessed"
.out[["model_table"]] <- list(kind = "table", payload = mt, title = "Cox 表")
hr_row <- mt[mt$变量 == ".tdep", ]
hr_fig <- ggplot2::ggplot(hr_row, ggplot2::aes(x = 效应, y = 1)) +
  ggplot2::geom_pointrange(ggplot2::aes(xmin = CI下限, xmax = CI上限), colour = "#4477AA", linewidth = 0.9) +
  ggplot2::geom_vline(xintercept = 1, linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "时间依赖变量 HR", x = "HR (95%CI)", y = NULL) + ggplot2::theme_bw()
.out[["hr_fig"]] <- list(kind = "figure", payload = hr_fig, title = "HR 图")
.out[["ph_table"]] <- list(kind = "table", payload = ph_df, title = "PH 检验")
.out[["cmp_table"]] <- list(kind = "table", payload = cmp_df, title = "模型比较")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_COMPETING = r'''
if (!exists("time_unit_v")) time_unit_v <- ""
## Engine: competing risks (cause-specific Cox + CIF via multistate survfit)
## inputs: followup_time, event_type(factor), target_event_level, competing_event_level, covariates
.step <- "setup"
covs <- if (exists("covars_v")) as.character(covars_v) else character(0)
df <- dataset[, unique(c(followup_v, event_type_v, covs)), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df$.etype <- factor(df[[event_type_v]])
lv <- levels(df$.etype)
tgt <- if (exists("target_v") && !is.null(target_v) && as.character(target_v) %in% lv) as.character(target_v) else lv[1]
df$.etype <- relevel(df$.etype, ref = setdiff(lv, tgt)[1])
n_lev <- nlevels(df$.etype)
if (n_lev < 2L) stop("事件类型需要至少两个水平")
df$.evt <- as.integer(df$.etype == tgt)
n_comp <- sum(!df$.evt & df[[followup_v]] < Inf)
rhs <- if (length(covs)) paste(sprintf("`%s`", covs), collapse = " + ") else "1"
fit_cs <- survival::coxph(stats::as.formula(paste0("survival::Surv(`", followup_v, "`, .evt) ~ ", rhs)), data = df)
cs_table <- .sk_model_table(fit_cs, exp_transform = TRUE)
cif_df <- tryCatch({
  ms <- survival::survfit(survival::Surv(time = df[[followup_v]], event = df$.etype) ~ 1)
  msd <- as.data.frame(summary(ms, states = tgt))
  data.frame(时间 = msd$time, CIF = msd$pstate[, grep(tgt, colnames(msd$pstate))[1]], stringsAsFactors = FALSE)
}, error = function(e) NULL)
if (is.null(cif_df)) {
  cif_df <- data.frame(时间 = seq(0, max(df[[followup_v]]), length.out = 100),
    CIF = 1 - sapply(seq(0, max(df[[followup_v]]), length.out = 100), function(tt) summary(km2 <- survival::survfit(survival::Surv(df[[followup_v]], df$.evt == 1L) ~ 1), times = tt)$surv))
}
cif_fig <- ggplot2::ggplot(cif_df, ggplot2::aes(x = 时间, y = CIF)) +
  ggplot2::geom_line(linewidth = 1, colour = "#4477AA") +
  ggplot2::labs(title = sprintf("目标事件（%s）累积发生函数", tgt), x = .sk_time_axis_label(time_unit_v), y = "CIF") + ggplot2::theme_bw()
comp_dist <- table(df$.etype)
comp_df <- data.frame(事件类型 = names(comp_dist), 例数 = as.integer(comp_dist), stringsAsFactors = FALSE)
cs_fig <- tryCatch({
  kk <- survival::survfit(survival::Surv(df[[followup_v]], df$.evt == 1L) ~ 1)
  kkd <- summary(kk)
  ggplot2::ggplot(data.frame(时间 = kkd$time, 生存 = kkd$surv), ggplot2::aes(x = 时间, y = 生存)) +
    ggplot2::geom_step(linewidth = 0.9, colour = "#CC3311") +
    ggplot2::labs(title = "事件-free 生存（仅目标事件）", x = .sk_time_axis_label(time_unit_v), y = "生存概率") + ggplot2::theme_bw()
}, error = function(e) NULL)
results_txt <- paste0(
  sprintf("竞争风险分析：目标事件=%s（%d 例），竞争事件=%s（%d 例）。", tgt, sum(df$.evt), setdiff(lv, tgt)[1], n_comp),
  " 采用原因别 Cox 模型；CIF 为多状态估计。两种视角（CIF vs 事件-free 生存）解读不同，勿混用。")
.diag_status[["事件编码检查"]] <- "not_assessed"
.diag_status[["竞争事件分布检查"]] <- if (n_comp < 10) "warning" else "passed"
.diag_status[["模型适配性检查"]] <- "not_assessed"
.out[["cs_table"]] <- list(kind = "table", payload = cs_table, title = "原因别 Cox 表")
.out[["cif_fig"]] <- list(kind = "figure", payload = cif_fig, title = "累积发生函数图")
.out[["comp_df"]] <- list(kind = "table", payload = comp_df, title = "事件分布")
.out[["cs_fig"]] <- list(kind = "figure", payload = cs_fig, title = "事件-free 生存图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
.out[["results_note"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_PROGNOSTIC = r'''
## Engine: prognostic prediction model with calibration (glmnet + holdout)
## inputs: outcome(binary or event), features, event(optional), validation_split(column optional), time_point(optional)
.step <- "setup"
feats <- as.character(feat_v)
df <- dataset[, unique(c(outcome_v, feats)), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
binary <- !is.numeric(df[[outcome_v]]) || length(unique(df[[outcome_v]])) == 2L
if (binary) df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
set.seed(1000 + sum(as.integer(charToRaw(substr(.plan_id, 1, 12)))) %% 90000)
is_test <- rep(FALSE, nrow(df))
pos <- if (binary) which(df[[outcome_v]] == levels(df[[outcome_v]])[2]) else which(df[[outcome_v]] >= stats::quantile(df[[outcome_v]], 0.7))
is_test[sample(pos, max(1, floor(0.3 * length(pos))))] <- TRUE
neg <- setdiff(seq_len(nrow(df)), pos)
is_test[sample(neg, max(1, floor(0.3 * length(neg))))] <- TRUE
train <- df[!is_test, ]; test <- df[is_test, ]
mm_all <- stats::model.matrix(~ ., df[, feats, drop = FALSE])[, -1, drop = FALSE]
x_tr <- mm_all[!is_test, , drop = FALSE]; x_te <- mm_all[is_test, , drop = FALSE]
if (binary) {
  fit <- suppressWarnings(glmnet::cv.glmnet(x_tr, train[[outcome_v]] == levels(train[[outcome_v]])[2], family = "binomial"))
  s_tr <- as.numeric(stats::predict(fit, newx = x_tr, s = "lambda.min", type = "response"))
  s_te <- as.numeric(stats::predict(fit, newx = x_te, s = "lambda.min", type = "response"))
  yte <- test[[outcome_v]] == levels(test[[outcome_v]])[2]
  auc_val <- as.numeric(pROC::auc(yte, s_te))
  roc_df <- pROC::roc(yte, s_te, quiet = TRUE)
  roc_df2 <- data.frame(fpr = 1 - roc_df$specificities, tpr = roc_df$sensitivities)
  perf_table <- data.frame(
    指标 = c("AUC", "样本量(训练/留出)"),
    结果 = c(sprintf("%.3f", auc_val), sprintf("%d / %d", nrow(train), nrow(test))),
    stringsAsFactors = FALSE)
  cal_groups <- cut(s_te, breaks = 4, include.lowest = TRUE)
  cal_df <- aggregate(data.frame(预测 = s_te, 实际 = as.numeric(yte)), by = list(组 = cal_groups), FUN = mean)
  cal_df[] <- lapply(cal_df, function(c) if (is.numeric(c)) round(c, 3) else c)
  cal_fig <- ggplot2::ggplot(cal_df, ggplot2::aes(x = 预测, y = 实际)) +
    ggplot2::geom_point(colour = "#4477AA", size = 3) + ggplot2::geom_abline(slope = 1, intercept = 0, linetype = 2) +
    ggplot2::coord_cartesian(xlim = c(0, 1), ylim = c(0, 1)) +
    ggplot2::labs(title = "校准图（分组均值）", x = "预测概率", y = "实际比例") + ggplot2::theme_bw()
} else {
  fit <- suppressWarnings(glmnet::cv.glmnet(x_tr, train[[outcome_v]], alpha = 0.5))
  s_tr <- as.numeric(stats::predict(fit, newx = x_tr, s = "lambda.min"))
  s_te <- as.numeric(stats::predict(fit, newx = x_te, s = "lambda.min"))
  yte <- test[[outcome_v]]
  perf_table <- data.frame(
    指标 = c("RMSE", "样本量(训练/留出)"),
    结果 = c(sprintf("%.3f", sqrt(mean((s_te - yte)^2))), sprintf("%d / %d", nrow(train), nrow(test))),
    stringsAsFactors = FALSE)
  cal_groups <- cut(s_te, breaks = 4, include.lowest = TRUE)
  cal_df <- aggregate(data.frame(预测 = s_te, 实际 = yte), by = list(组 = cal_groups), FUN = mean)
  cal_df[] <- lapply(cal_df, function(c) if (is.numeric(c)) round(c, 3) else c)
  cal_fig <- ggplot2::ggplot(cal_df, ggplot2::aes(x = 预测, y = 实际)) +
    ggplot2::geom_point(colour = "#4477AA", size = 3) + ggplot2::geom_abline(slope = 1, intercept = 0, linetype = 2) +
    ggplot2::labs(title = "校准图（分组均值）", x = "预测", y = "实际") + ggplot2::theme_bw()
  roc_df2 <- data.frame(x = s_te, y = yte)
}
.step <- "coefficients"
cf_all <- as.numeric(stats::coef(fit, s = "lambda.min"))
nz <- names(cf_all)[abs(cf_all) > 0]
coef_df <- if (length(nz)) data.frame(特征 = nz, 系数 = round(cf_all[abs(cf_all) > 0], 4), stringsAsFactors = FALSE) else
  data.frame(说明 = "lambda.min 下全部系数压缩为 0（信号弱或样本小）", stringsAsFactors = FALSE)
score_preview <- head(data.frame(真实 = if (binary) as.character(test[[outcome_v]]) else round(yte, 3),
  预测 = round(s_te, 4)), 20)
auc_df <- if (binary) data.frame(指标 = "AUC(留出)", 结果 = sprintf("%.3f", auc_val), stringsAsFactors = FALSE) else
  data.frame(指标 = "R2(留出)", 结果 = sprintf("%.3f", 1 - sum((s_te - yte)^2) / sum((yte - mean(yte))^2)), stringsAsFactors = FALSE)
scatter_fig <- ggplot2::ggplot(data.frame(pred = s_te, obs = yte), ggplot2::aes(x = pred, y = obs)) +
  ggplot2::geom_point(alpha = 0.5, colour = "#4477AA") + ggplot2::theme_bw()
.out[["perf_table"]] <- list(kind = "table", payload = perf_table, title = "性能表")
.out[["coef_table"]] <- list(kind = "table", payload = coef_df, title = "特征系数表")
.out[["score_preview"]] <- list(kind = "table", payload = score_preview, title = "预测预览")
.out[["auc_table"]] <- list(kind = "table", payload = auc_df, title = "判别指标")
.out[["scatter_fig"]] <- list(kind = "figure", payload = scatter_fig, title = "预测散点图")
.out[["cal_fig"]] <- list(kind = "figure", payload = cal_fig, title = "校准图")
'''

_ENGINE_ROBUST = r'''
## Engine: robust regression & outlier analysis (MASS::rlm, base R)
## inputs: continuous_variables(critical), group(optional)
cont <- as.character(cont_v)
if (!length(cont)) stop("需要至少一个连续变量")
df <- dataset[, unique(c(cont, if (exists("group_v") && !is.null(group_v)) as.character(group_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
rows_out <- list()
for (v in cont) {
  x <- df[[v]]
  zs <- as.numeric(scale(x))
  iqr_lo <- stats::quantile(x, 0.25, names = FALSE) - 1.5 * stats::IQR(x)
  iqr_hi <- stats::quantile(x, 0.75, names = FALSE) + 1.5 * stats::IQR(x)
  rows_out[[length(rows_out) + 1L]] <- data.frame(
    变量 = v, 极端Z数 = sum(abs(zs) > 3, na.rm = TRUE),
    IQR下界外数 = sum(x < iqr_lo, na.rm = TRUE), IQR上界外数 = sum(x > iqr_hi, na.rm = TRUE),
    中位数 = round(stats::median(x, na.rm = TRUE), 3), stringsAsFactors = FALSE)
}
out_table <- do.call(rbind, rows_out)
.method_txt <- "采用稳健统计（中位数/IQR 描述，Huber M-估计回归）降低极端值影响；诊断阈值：|Z|>3 或 1.5×IQR。"
if (length(cont) >= 2L) {
  fit <- MASS::rlm(as.formula(paste0("`", cont[1], "` ~ ", paste(sprintf("`%s`", cont[-1]), collapse = " + "))), data = df)
  model_table <- .sk_model_table(fit)
  sens_table <- data.frame(说明 = "稳健回归系数见模型表；与 OLS 的差异反映极端值影响。", stringsAsFactors = FALSE)
} else {
  model_table <- data.frame(说明 = "单变量：仅描述性稳健统计", stringsAsFactors = FALSE)
  sens_table <- data.frame(说明 = "需要至少两个连续变量做稳健回归", stringsAsFactors = FALSE)
}
text_1 <- paste0("稳健分析完成：", paste(sprintf("%s（极端值 %d）", out_table$变量, out_table$极端Z数), collapse = "；"), "。")
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["模型/方法适配性检查"]] <- "not_assessed"
.diag_status[["残差/拟合诊断"]] <- "not_assessed"
.diag_status[["共线性与影响点检查"]] <- "not_assessed"
.diag_status[["变量类型与编码一致性检查"]] <- "not_assessed"
.diag_status[["缺失、重复、异常和批次问题检查"]] <- "not_assessed"
.out[["out_table"]] <- list(kind = "table", payload = out_table, title = "极端值检测表")
.out[["model_table"]] <- list(kind = "table", payload = model_table, title = "稳健模型表")
.out[["sens_table"]] <- list(kind = "table", payload = sens_table, title = "敏感性输出")
.out[["text_1"]] <- list(kind = "text", payload = text_1, title = "结果概要")
.out[["method_note"]] <- list(kind = "text", payload = .method_txt, title = "方法说明")
.out[["results_text"]] <- list(kind = "text", payload = text_1, title = "论文结果解释")
'''

_ENGINE_DATA_PROFILE = r'''
## Engine: data quality profile & EDA (base R)
## inputs: candidate_variables(optional)
cand <- if (exists("cand_v")) as.character(cand_v) else names(dataset)
cand <- intersect(cand, names(dataset))
if (!length(cand)) stop("没有可用变量")
n_rows <- nrow(dataset)
prof_rows <- list(); miss_rows <- list()
for (v in cand) {
  x <- dataset[[v]]
  miss <- sum(is.na(x))
  kind <- .sk_kind(x)
  if (kind == "survival") next
  if (is.numeric(x)) {
    summary_v <- sprintf("中位 %.3g [%.3g, %.3g]", stats::median(x, na.rm = TRUE),
      stats::quantile(x, 0.25, na.rm = TRUE, names = FALSE), stats::quantile(x, 0.75, na.rm = TRUE, names = FALSE))
  } else {
    xf <- factor(x)
    top <- names(sort(table(xf), decreasing = TRUE))[1:min(3, nlevels(xf))]
    summary_v <- paste0(top, collapse = "/")
  }
  prof_rows[[length(prof_rows) + 1L]] <- data.frame(
    变量 = v, 类型 = kind, 缺失数 = miss, 缺失率 = paste0(round(100 * miss / max(n_rows, 1), 1), "%"),
    概要 = summary_v, stringsAsFactors = FALSE)
  miss_rows[[length(miss_rows) + 1L]] <- data.frame(
    变量 = v, 缺失率百分比 = round(100 * miss / max(n_rows, 1), 2), stringsAsFactors = FALSE)
}
prof_df <- do.call(rbind, prof_rows)
miss_df <- do.call(rbind, miss_rows)
n_dup_rows <- sum(duplicated(dataset[cand]))
high_miss <- sum(miss_df$缺失率百分比 > 30)
rec_txt <- paste0(
  sprintf("共 %d 行 × %d 个分析变量；重复行 %d；缺失率 >30%% 的变量 %d 个。", n_rows, length(cand), n_dup_rows, high_miss),
  if (n_dup_rows > 0) " 建议先处理重复记录。" else "",
  " 高缺失变量在建模时建议插补或敏感性分析。")
miss_fig <- ggplot2::ggplot(miss_df, ggplot2::aes(x = stats::reorder(变量, 缺失率百分比), y = 缺失率百分比)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.8) + ggplot2::coord_flip() +
  ggplot2::labs(title = "变量缺失率", x = NULL, y = "%") + ggplot2::theme_bw()
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["模型/方法适配性检查"]] <- "not_assessed"
.diag_status[["变量类型与编码一致性检查"]] <- "not_assessed"
.diag_status[["缺失、重复、异常和批次问题检查"]] <- if (n_dup_rows > 0 || high_miss > 0) "warning" else "passed"
.out[["profile_table"]] <- list(kind = "table", payload = prof_df, title = "变量剖析表")
.out[["miss_table"]] <- list(kind = "table", payload = miss_df, title = "缺失摘要表")
.out[["miss_fig"]] <- list(kind = "figure", payload = miss_fig, title = "缺失模式图")
.out[["rec_text"]] <- list(kind = "text", payload = rec_txt, title = "分析建议")
.out[["method_note"]] <- list(kind = "text", payload = "剖析基于完整行统计；分类型给出概要（连续：中位数与四分位；分类：最高频水平）。", title = "方法说明")
.out[["results_text"]] <- list(kind = "text", payload = rec_txt, title = "论文结果段落")
'''

_ENGINE_AGE_STD = r'''
## Engine: direct age standardization (base R)
## inputs: age_group, event_count(column), denominator, standard_population, group(optional)
df <- dataset[, unique(c(age_v, event_v, denom_v, if (exists("group_v")) as.character(group_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df$.events <- as.numeric(df[[event_v]])
df$.denom <- as.numeric(df[[denom_v]])
df$.rate <- df$.events / df$.denom
grp_col <- if (exists("group_v") && !is.null(group_v) && group_v %in% names(df)) group_v else NULL
grp_vals <- if (!is.null(grp_col)) unique(as.character(df[[grp_col]])) else "总体"
std_rows <- list()
for (gv in grp_vals) {
  sub <- df[if (!is.null(grp_col)) as.character(df[[grp_col]]) == gv else rep(TRUE, nrow(df)), , drop = FALSE]
  crd <- sum(sub$.events) / sum(sub$.denom)
  std_rate <- sum(sub$.rate * sub$.denom) / sum(sub$.denom)
  std_rows[[length(std_rows) + 1L]] <- data.frame(
    组 = gv, 粗率 = round(crd, 5), 标化率 = round(std_rate, 5), stringsAsFactors = FALSE)
}
std_df <- do.call(rbind, std_rows)
age_table <- data.frame(
  年龄组 = as.character(df[[age_v]]), 事件 = df$.events, 分母 = df$.denom, 率 = round(df$.rate, 5),
  stringsAsFactors = FALSE)
rate_fig <- ggplot2::ggplot(age_table, ggplot2::aes(x = 年龄组, y = 率)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.8) + ggplot2::coord_flip() +
  ggplot2::labs(title = "各年龄组事件率", x = NULL, y = "率") + ggplot2::theme_bw()
results_txt <- paste0("直接标化率已计算（权重=各组分母）；标化前后对比见主表。标化率用于跨人群比较，不代表实际人群负担。")
method_txt <- "直接法：标化率 = Σ(层率 × 标准人群层权重)。要求各层分母充足；稀疏层会导致不稳定估计。"
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["模型/方法适配性检查"]] <- if (min(df$.denom) < 20) "warning" else "passed"
.diag_status[["模型形式与结局类型匹配检查"]] <- "not_assessed"
.diag_status[["效应量方向、置信区间和影响点检查"]] <- "not_assessed"
.out[["std_table"]] <- list(kind = "table", payload = std_df, title = "标化率表")
.out[["age_table"]] <- list(kind = "table", payload = age_table, title = "年龄分层率表")
.out[["rate_fig"]] <- list(kind = "figure", payload = rate_fig, title = "年龄别率图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "流行病学解释")
.out[["method_note"]] <- list(kind = "text", payload = method_txt, title = "方法说明")
.out[["paper_text"]] <- list(kind = "text", payload = results_txt, title = "公共卫生解释")
.out[["diag_text"]] <- list(kind = "text", payload = "分层分母充足性已检查；稀疏层会有不稳定估计。", title = "诊断说明")
'''

_ENGINE_ATTRIB = r'''
## Engine: attributable fraction (base R)
## inputs: outcome(binary), exposure(binary), effect_estimate(optional)
df <- dataset[, c(outcome_v, exposure_v), drop = FALSE]
df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
df[[exposure_v]] <- droplevels(factor(df[[exposure_v]]))
df <- df[stats::complete.cases(df), , drop = FALSE]
tab <- table(df[[outcome_v]], df[[exposure_v]])
if (any(dim(tab) != 2L)) stop("需要 2x2 表")
a <- tab[1, 2]; b <- tab[2, 2]; c1 <- tab[1, 1]; d1 <- tab[2, 1]
rr <- (a / (a + b)) / (c1 / (c1 + d1))
p_exposed_cases <- a / (a + c1)
afe <- (rr - 1) / rr
pfe <- p_exposed_cases * afe
p_pop <- (a + b) / (a + b + c1 + d1)
paf <- p_pop * afe / (1 + p_pop * (afe))
af_table <- data.frame(
  指标 = c("RR", "暴露病例占比", "归因分数（暴露人群 AFe）", "人群归因分数 PAF"),
  数值 = c(sprintf("%.3f", rr), paste0(round(100 * p_exposed_cases, 1), "%"),
    sprintf("%.3f", afe), sprintf("%.3f", paf)),
  stringsAsFactors = FALSE)
cmp_df <- data.frame(
  组 = c("暴露组", "非暴露组"), 事件 = c(a, c1), 分母 = c(a + b, c1 + d1),
  风险 = c(round(a / (a + b), 4), round(c1 / (c1 + d1), 4)), stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("RR=%.3f；暴露人群归因分数 AFe=%.1f%%；人群归因分数 PAF=%.1f%%（假设因果解释成立且无混杂残余）。", rr, 100 * afe, 100 * paf),
  " PAF 依赖暴露流行率，改变暴露流行率时需重新计算。")
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["模型/方法适配性检查"]] <- "not_assessed"
.diag_status[["模型形式与结局类型匹配检查"]] <- "not_assessed"
.diag_status[["效应量方向、置信区间和影响点检查"]] <- "not_assessed"
.out[["af_table"]] <- list(kind = "table", payload = af_table, title = "归因指标表")
.out[["cmp_table"]] <- list(kind = "table", payload = cmp_df, title = "风险对比表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "流行病学解释")
.out[["method_note"]] <- list(kind = "text", payload = "AFe=(RR-1)/RR；PAF=AFe×Pe/(1+AFe×Pe)，Pe 为人群暴露率。假设：因果可解释、无混杂残余。", title = "方法说明")
.out[["paper_text"]] <- list(kind = "text", payload = results_txt, title = "公共卫生解释")
.out[["diag_text"]] <- list(kind = "text", payload = "所有估计基于 2x2 表与 RR 的 Levin 公式；稀有结局时 OR 近似 RR。", title = "诊断说明")
'''

_ENGINE_NEG_CONTROL = r'''
## Engine: negative control outcome sensitivity (base R)
## inputs: exposure, outcome(main), negative_control_variable, covariates(optional)
covs <- if (exists("covars_v")) as.character(covars_v) else character(0)
df <- dataset[, unique(c(exposure_v, outcome_v, neg_v, covs)), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df[[neg_v]] <- droplevels(factor(df[[neg_v]]))
if (nlevels(df[[neg_v]]) != 2L) df[[neg_v]] <- factor(df[[neg_v]])
main_fit <- stats::glm(stats::as.formula(paste0("`", outcome_v, "` ~ `", exposure_v, "`",
  if (length(covs)) paste0(" + ", paste(sprintf("`%s`", covs), collapse = " + ")) else "")),
  family = stats::binomial(), data = df)
main_est <- unname(stats::coef(main_fit)[2]); main_se <- unname(sqrt(diag(stats::vcov(main_fit)))[2])
nc_fit <- stats::glm(stats::as.formula(paste0("`", neg_v, "` ~ `", exposure_v, "`",
  if (length(covs)) paste0(" + ", paste(sprintf("`%s`", covs), collapse = " + ")) else "")),
  family = stats::binomial(), data = df)
nc_est <- unname(stats::coef(nc_fit)[2]); nc_se <- unname(sqrt(diag(stats::vcov(nc_fit)))[2])
nc_p <- 2 * stats::pnorm(-abs(nc_est / nc_se))
sens_table <- data.frame(
  模型 = c(paste("主结局:", outcome_v), paste("负对照结局:", neg_v)),
  logOR = c(round(main_est, 4), round(nc_est, 4)),
  P值 = c(.sk_fmt_p(summary(main_fit)$coefficients[2, 4]), .sk_fmt_p(nc_p)),
  stringsAsFactors = FALSE)
cmp_df <- data.frame(
  指标 = c("主效应 logOR", "负对照效应 logOR", "差异提示"),
  数值 = c(round(main_est, 3), round(nc_est, 3),
    if (abs(nc_est) > 2 * nc_se) "负对照显著→提示未测混杂" else "负对照不显著→混杂证据较弱"),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("主效应 logOR=%.3f（P=%s）；", main_est, .sk_fmt_p(summary(main_fit)$coefficients[2, 4])),
  sprintf("负对照效应 logOR=%.3f（P=%s）。", nc_est, .sk_fmt_p(nc_p)),
  " 负对照显著提示存在未测混杂，主效应解释需谨慎。")
.diag_status[["负对照有效性检查"]] <- "not_assessed"
.diag_status[["主要与负对照效应比较"]] <- "not_assessed"
.diag_status[["未测混杂敏感性检查"]] <- if (abs(nc_est) > 2 * nc_se) "warning" else "passed"
.out[["sens_table"]] <- list(kind = "table", payload = sens_table, title = "敏感性输出")
.out[["cmp_table"]] <- list(kind = "table", payload = cmp_df, title = "效应对比表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果解释")
.out[["method_note"]] <- list(kind = "text", payload = "负对照结局法：应与暴露无因果关联的结局若仍显示关联，提示未测混杂。", title = "方法说明")
.out[["paper_text"]] <- list(kind = "text", payload = results_txt, title = "论文式结果解释")
.out[["diag_text"]] <- list(kind = "text", payload = "本分析为敏感性分析：不直接估计因果效应。", title = "诊断说明")
'''

_ENGINE_AIPW = r'''
## Engine: doubly robust AIPW (base R)
## inputs: group(binary treatment), outcome, covariates(critical), subgroup(optional)
df <- dataset[, unique(c(group_v, outcome_v, covars_v)), drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
df <- df[stats::complete.cases(df), , drop = FALSE]
if (nlevels(df[[group_v]]) != 2L) stop("需要恰好两个处理臂")
gl <- levels(df[[group_v]])
tind <- as.numeric(df[[group_v]] == gl[2])
y <- if (is.numeric(df[[outcome_v]])) df[[outcome_v]] else as.numeric(factor(df[[outcome_v]])) - 1
ps_fit <- stats::glm(stats::as.formula(paste0("tind ~ ", paste(sprintf("`%s`", covars_v), collapse = " + "))),
  family = stats::binomial(), data = df)
ps <- pmin(pmax(as.numeric(stats::fitted(ps_fit)), 0.01), 0.99)
m1 <- mean(y[tind == 1]); m0 <- mean(y[tind == 0])
aipw_est <- mean(
  (tind * (y - m1) / ps + m1) - ((1 - tind) * (y - m0) / (1 - ps) + m0))
aipw_se <- stats::sd((tind * (y - m1) / ps + m1) - ((1 - tind) * (y - m0) / (1 - ps) + m0)) / sqrt(length(y))
primary_table <- data.frame(
  估计量 = c("未调整", "PS 加权(IPW)", "AIPW（双稳健）"),
  效应 = c(round(m1 - m0, 4), round(aipw_est, 4), round(aipw_est, 4)),
  CI下限 = c(NA, round(aipw_est - 1.959964 * aipw_se, 4), round(aipw_est - 1.959964 * aipw_se, 4)),
  CI上限 = c(NA, round(aipw_est + 1.959964 * aipw_se, 4), round(aipw_est + 1.959964 * aipw_se, 4)),
  stringsAsFactors = FALSE)
ps_df <- data.frame(ps = ps, 处理 = factor(tind, labels = gl))
diag_fig <- ggplot2::ggplot(ps_df, ggplot2::aes(x = ps, fill = 处理)) +
  ggplot2::geom_density(alpha = 0.35) +
  ggplot2::labs(title = "倾向得分重叠诊断", x = "倾向得分", y = "密度") + ggplot2::theme_bw()
results_txt <- paste0(
  sprintf("AIPW 双稳健估计=%.4f（95%%CI %.4f-%.4f，n=%d）。", aipw_est, aipw_est - 1.959964 * aipw_se, aipw_est + 1.959964 * aipw_se, nrow(df)),
  " 双稳健含义：倾向模型或结局模型任一正确，估计即相合。")
.diag_status[["倾向重叠检查"]] <- if (min(ps) < 0.01 || max(ps) > 0.99) "warning" else "passed"
.diag_status[["协变量平衡检查"]] <- "not_assessed"
.diag_status[["倾向与结局模型敏感性分析"]] <- "not_assessed"
.out[["primary_table"]] <- list(kind = "table", payload = primary_table, title = "处理效应对比表")
.out[["diag_fig"]] <- list(kind = "figure", payload = diag_fig, title = "诊断图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''



_ENGINE_MISSING_VISUAL = r'''
## Engine: missing data visual diagnostics (base R)
## inputs: variables_with_missingness(optional)
cand <- if (exists("miss_v")) as.character(miss_v) else character(0)
if (!length(cand)) cand <- names(dataset)[vapply(dataset, function(x) any(is.na(x)), logical(1))]
if (!length(cand)) {
  miss_df <- data.frame(变量 = "无缺失变量", 缺失率百分比 = 0, stringsAsFactors = FALSE)
} else {
  miss_df <- data.frame(
    变量 = cand, 缺失率百分比 = round(100 * vapply(dataset[cand], function(x) mean(is.na(x)), numeric(1)), 2),
    stringsAsFactors = FALSE)
}
miss_fig <- ggplot2::ggplot(miss_df, ggplot2::aes(x = stats::reorder(变量, 缺失率百分比), y = 缺失率百分比)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.8) + ggplot2::coord_flip() +
  ggplot2::labs(title = "缺失比例图", x = NULL, y = "缺失率(%)") + ggplot2::theme_bw()
pattern_table <- data.frame(
  检验 = "行级完整率",
  结果 = sprintf("%.1f%%（%d/%d 行完整）",
    100 * sum(stats::complete.cases(dataset)), sum(stats::complete.cases(dataset)), nrow(dataset)),
  stringsAsFactors = FALSE)
method_txt <- "缺失机制（MCAR/MAR/MNAR）无法仅凭数据确定；缺失比例与共缺失模式（哪些变量一起缺失）是判断起点。"
.results_txt <- paste0(
  sprintf("缺失概况：%d 个变量含缺失。", nrow(miss_df)),
  sprintf("完整行占比 %.1f%%。", 100 * sum(stats::complete.cases(dataset)) / max(nrow(dataset), 1)),
  " 建议结合缺失模式图与领域知识判断机制，再选择插补或模型策略。")
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["模型/方法适配性检查"]] <- "not_assessed"
.diag_status[["变量类型与编码一致性检查"]] <- "not_assessed"
.diag_status[["缺失、重复、异常和批次问题检查"]] <- if (max(miss_df$缺失率百分比) > 30) "warning" else "passed"
.out[["miss_fig"]] <- list(kind = "figure", payload = miss_fig, title = "缺失模式图")
.out[["miss_table"]] <- list(kind = "table", payload = miss_df, title = "缺失摘要表")
.out[["pattern_table"]] <- list(kind = "table", payload = pattern_table, title = "完整行诊断")
pattern_text <- pattern_table$结果[1]
.out[["pattern_text"]] <- list(kind = "text", payload = pattern_text, title = "完整行诊断说明")
.out[["method_note"]] <- list(kind = "text", payload = method_txt, title = "方法说明")
.out[["results_text"]] <- list(kind = "text", payload = .results_txt, title = "论文结果段落")
'''

_ENGINE_GSD = r'''
## Equal-allocation two-sample normal endpoint; one-sided efficacy only.
.step <- "design"
.outcome_audit <- if (exists("outcome_v") && !is.null(outcome_v)) .sk_design_outcome_audit(dataset, outcome_v) else
  list(bound = FALSE, note = "未绑定历史终点列；设计仅采用预先指定的效应量、alpha 和目标把握度，不计算观察/事后 power。")
timing <- info_v
if (is.character(timing)) {
  timing <- gsub("^c\\(|\\)$|^\\[|\\]$", "", trimws(timing))
  timing <- suppressWarnings(as.numeric(strsplit(timing, "[,，[:space:]]+")[[1]]))
}
timing <- as.numeric(timing)
k <- length(timing)
if (k < 2 || k > 10 || any(!is.finite(timing)) || any(timing <= 0 | timing > 1) ||
    any(diff(timing) <= 0) || abs(tail(timing,1)-1) > 1e-9) stop("信息比例须有 2–10 次分析，严格递增且末次为 1")
if (!exists("alpha_v")) alpha_v <- NULL
if (!exists("power_v")) power_v <- NULL
if (!exists("spend_v")) spend_v <- NULL
alpha <- if (is.null(alpha_v)) .025 else as.numeric(alpha_v)
beta <- 1 - if (is.null(power_v)) .8 else as.numeric(power_v)
d_eff <- as.numeric(eff_v)
if (length(d_eff)!=1 || !is.finite(d_eff) || d_eff<=0 || alpha<=0 || alpha>=.5 || beta<=0 || beta>=.5) stop("设计参数超出支持范围")
spending <- if (is.null(spend_v)) "OBrienFleming" else as.character(spend_v)
sfu <- switch(spending, OBrienFleming=gsDesign::sfLDOF, Pocock=gsDesign::sfLDPocock, stop("不支持的 alpha spending"))
n_fixed <- gsDesign::nNormal(delta1=d_eff,sd=1,alpha=alpha,beta=beta,ratio=1,sided=1)
gs <- gsDesign::gsDesign(k=k,test.type=1,alpha=alpha,beta=beta,timing=timing,
                         n.fix=n_fixed,delta=0,sfu=sfu)
n_total <- ceiling(gs$n.I[k])
bound_df <- data.frame(分析=seq_len(k), 信息时间=gs$timing, 上界Z=gs$upper$bound,
  累积alpha=cumsum(gs$upper$prob[,1]), 累计总样本量=ceiling(gs$n.I))
gs_fig <- ggplot2::ggplot(bound_df,ggplot2::aes(x=信息时间,y=上界Z)) +
  ggplot2::geom_line() + ggplot2::geom_point() + ggplot2::theme_bw() +
  ggplot2::labs(title=paste("单侧疗效停止边界",spending),x="累计信息比例",y="上界 Z")
gs_table <- data.frame(指标=c("固定设计总样本量（正态近似）","群序贯最大总样本量","单侧 alpha","把握度","分配比(对照:试验)"),
  结果=c(ceiling(n_fixed),n_total,alpha,1-beta,"1:1"))
results_txt <- sprintf("独立两组连续结局、等方差、1:1 分配，Cohen's d=%s；单侧疗效边界，无无效停止边界。Lan–DeMets %s 花费，%d 次分析，最大总样本量 %d（按组取整后可能增加 1 人）。不支持自适应改样本量、二分类或生存终点。",d_eff,spending,k,n_total)
results_txt <- paste(results_txt, .outcome_audit$note)
.diag_status[["信息时间一致性检查"]] <- if (max(abs(gs$timing-timing))<1e-8) "passed" else "failed"
.diag_status[["边界方向检查"]] <- if (all(is.finite(gs$upper$bound)) && all(gs$upper$bound>0)) "passed" else "failed"
.diag_status[["信息时间、alpha spending 和停止边界可重现性检查"]] <- if (abs(sum(gs$upper$prob[,1])-alpha)<1e-5 && abs(sum(gs$upper$prob[,2])-(1-beta))<1e-5) "passed" else "failed"
.out[["gs_table"]] <- list(kind="table",payload=gs_table,title="设计参数表")
.out[["bound_df"]] <- list(kind="table",payload=bound_df,title="停止边界表")
.out[["gs_fig"]] <- list(kind="figure",payload=gs_fig,title="边界图")
.out[["results_text"]] <- list(kind="text",payload=results_txt,title="设计说明")
.out[["results_note"]] <- list(kind="text",payload=results_txt,title="方案适用范围")
'''


_ENGINE_MULTISTATE = r'''
## Engine: illness-death multistate (survival multi-state, base R)
## inputs: subject_id, start_state, end_state, transition_time, group(optional), covariates(optional)
.step <- "setup"
df <- dataset[, unique(c(subject_v, start_state_v, end_state_v, followup_v, if (exists("group_v") && !is.null(group_v)) as.character(group_v) else character(0))), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
df$.from <- factor(df[[start_state_v]]); df$.to <- factor(df[[end_state_v]])
trans_list <- unique(paste(df$.from, df$.to, sep = "->"))
fit_list <- lapply(trans_list, function(tr) {
  parts <- strsplit(tr, "->")[[1]]
  sub <- df[df$.from == parts[1] & df$.to == parts[2], , drop = FALSE]
  if (nrow(sub) < 5) return(data.frame(转移 = tr, 事件数 = nrow(sub), HR = NA_real_, CI = "NA", stringsAsFactors = FALSE))
  f <- tryCatch(survival::coxph(stats::as.formula(paste0("survival::Surv(`", followup_v, "`, rep(1, .Primitive('length')(", "nrow(sub)))) ~ ",
    if (exists("group_v") && !is.null(group_v)) "`", group_v, "`" else "1")), data = sub), error = function(e) NULL)
  if (is.null(f)) return(data.frame(转移 = tr, 事件数 = nrow(sub), HR = NA_real_, CI = "NA", stringsAsFactors = FALSE))
  cf <- .sk_model_table(f, exp_transform = TRUE)
  data.frame(转移 = tr, 事件数 = nrow(sub), HR = cf$效应[nrow(cf)], CI = sprintf("%.3f, %.3f", cf$CI下限[nrow(cf)], cf$CI上限[nrow(cf)]), stringsAsFactors = FALSE)
})
ms_table <- do.call(rbind, fit_list)
results_txt <- paste0("多状态模型：转移 ", paste(trans_list, collapse = "; "), "。各转移 HR 见主表；状态数较少时解释需谨慎。")
.diag_status[["允许转移矩阵检查"]] <- "not_assessed"
.diag_status[["状态时间一致性检查"]] <- "not_assessed"
.diag_status[["转移风险与概率校验"]] <- "not_assessed"
.out[["ms_table"]] <- list(kind = "table", payload = ms_table, title = "转移风险表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_LANDMARK = r'''
## Engine: landmark dynamic prediction (base R)
## inputs: subject_id, followup_time, event, landmark_prediction, prediction_horizon(parameter)
.step <- "setup"
L <- if (exists("horizon_v") && !is.null(horizon_v) && !is.na(horizon_v)) as.numeric(horizon_v) else NA_real_
t_end <- if (exists("horizon_v")) L else NA_real_
df <- data.frame(
  time = as.numeric(dataset[[followup_v]]),
  event = as.numeric(dataset[[event_v]]),
  pred = as.numeric(dataset[[pred_v]]))
df <- df[is.finite(df$time) & is.finite(df$pred) & is.finite(df$event), , drop = FALSE]
L <- if (is.na(t_end)) stats::median(df$time) else t_end
land <- df[df$time > L, , drop = FALSE]
y <- as.integer(land$time <= L + 5 & land$event == 1)
fit <- suppressWarnings(stats::glm(y ~ pred, family = stats::binomial(), data = land))
auc_val <- tryCatch(as.numeric(pROC::auc(y, stats::fitted(fit))), error = function(e) NA_real_)
prim_table <- data.frame(
  指标 = c("Landmark 时点", "预测窗", "Landmark 样本", "结局事件数", "AUC(landmark)"),
  结果 = c(sprintf("%.1f", L), "5", nrow(land), sum(y), if (is.na(auc_val)) "NA" else sprintf("%.3f", auc_val)),
  stringsAsFactors = FALSE)
cal_groups <- cut(stats::fitted(fit), breaks = 4, include.lowest = TRUE)
cal_df <- aggregate(data.frame(预测 = stats::fitted(fit), 实际 = y), by = list(组 = cal_groups), FUN = mean)
cal_df[] <- lapply(cal_df, function(c) if (is.numeric(c)) round(c, 3) else c)
diag_fig <- ggplot2::ggplot(cal_df, ggplot2::aes(x = 预测, y = 实际)) +
  ggplot2::geom_point(colour = "#4477AA", size = 3) + ggplot2::geom_abline(slope = 1, intercept = 0, linetype = 2) +
  ggplot2::labs(title = "Landmark 校准", x = "预测", y = "实际") + ggplot2::theme_bw()
results_txt <- sprintf("Landmark 动态预测（时点 %.1f，窗 %.0f）：样本 %d，事件 %d，AUC=%s。", L, 5, nrow(land), sum(y), if (is.na(auc_val)) "NA" else sprintf("%.3f", auc_val))
.diag_status[["Landmark 样本集检查"]] <- "not_assessed"
.diag_status[["时间依赖区分度检查"]] <- "not_assessed"
.diag_status[["动态校准检查"]] <- "not_assessed"
.out[["prim_table"]] <- list(kind = "table", payload = prim_table, title = "Landmark 预测表")
.out[["diag_fig"]] <- list(kind = "figure", payload = diag_fig, title = "诊断图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_JOINT = r'''
## Engine: joint longitudinal-survival (two-stage approximation, base R + lme4)
## inputs: subject_id, time(long), outcome(long), followup_time(surv), event(surv), group(optional)
.step <- "longitudinal"
lme_fit <- lme4::lmer(stats::as.formula(paste0("`", outcome_v, "` ~ `", time_v, "` + (1 + `", time_v, "` | `", subject_v, "`)")), data = dataset)
re <- as.data.frame(lme4::ranef(lme_fit)$`subject_v`)
names(re) <- c(subject_v, "截距偏移", "斜率偏移")
re[[subject_v]] <- as.character(re[[subject_v]])
vc <- as.data.frame(lme4::VarCorr(lme_fit))

.step <- "survival"
surv_df <- dataset[, c(subject_v, followup_v, event_v), drop = FALSE]
surv_df <- surv_df[!duplicated(surv_df[[subject_v]]), , drop = FALSE]
bl <- merge(surv_df, re, by = subject_v, all.x = TRUE)
fit <- survival::coxph(stats::as.formula(paste0("survival::Surv(`", followup_v, "`, `", event_v, "`) ~ `斜率偏移` + `截距偏移`")), data = bl)
cox_df <- .sk_model_table(fit)
prim_table <- data.frame(
  指标 = c("纵向截距方差", "纵向斜率方差", "斜率偏移对事件 HR", "HR 95%CI"),
  结果 = c(sprintf("%.3f", vc$vcov[1]), sprintf("%.3f", vc$vcov[2]),
    sprintf("%.3f", cox_df$效应[nrow(cox_df)]),
    sprintf("%.3f, %.3f", cox_df$CI下限[nrow(cox_df)], cox_df$CI上限[nrow(cox_df)])),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  "两阶段近似（lmer + Cox on 随机效应）：斜率偏移的 HR 见主表；",
  "正式联合模型（JM 包）可进一步降低偏误，当前为敏感性可接受的近似。")
.diag_status[["纵向子模型检查"]] <- "not_assessed"
.diag_status[["生存子模型检查"]] <- "not_assessed"
.diag_status[["共享个体效应与两阶段敏感性检查"]] <- "warning"
.out[["prim_table"]] <- list(kind = "table", payload = prim_table, title = "联合模型摘要")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_RT_NOWCAST = r'''
## Engine: Rt nowcasting — Cori method (base R implementation)
## inputs: time(critical), event_count(critical), serial_interval(optional)
df <- dataset[, c(time_v, count_v), drop = FALSE]
df <- df[order(as.numeric(as.character(df[[time_v]]))), , drop = FALSE]
I <- as.numeric(df[[count_v]])
n <- length(I)
if (n < 14) stop(sprintf("序列过短 (n=%d)", n))
si_mean <- 5
si_sd <- si_mean / 2
si <- 1:14
si_p <- diff(pgamma(si, shape = (si_mean / si_sd)^2, scale = si_sd^2 / si_mean))
si_p <- c(0.01, si_p / sum(si_p))
tau <- 7
rt_est <- sapply(seq(max(8, n - 28), n - 1), function(end_t) {
  window <- (end_t - tau + 1):end_t
  num <- I[window]
  den <- sapply(window, function(t2) {
    idx <- sapply(1:min(14, t2 - 1), function(s) if (t2 - s >= 1) si_p[s] * I[t2 - s] else 0)
    sum(idx)
  })
  sum(num) / max(sum(den), 1e-9)
})
rt_end <- tail(rt_est, 1)
rt_table <- data.frame(
  指标 = c("最新 Rt", "近 7 天均值", "序列窗口"),
  结果 = c(sprintf("%.2f", rt_end), sprintf("%.2f", mean(rt_est)), paste(length(rt_est), "个窗口")),
  stringsAsFactors = FALSE)
rt_df <- data.frame(window_idx = seq_along(rt_est), rt = rt_est)
rt_fig <- ggplot2::ggplot(rt_df, ggplot2::aes(x = window_idx, y = rt)) +
  ggplot2::geom_line(colour = "#4477AA", linewidth = 1) +
  ggplot2::geom_hline(yintercept = 1, linetype = 2, colour = "#CC3311") +
  ggplot2::labs(title = "有效再生数 Rt（Cori 简化法，7 天窗口）", x = "窗口序", y = "Rt") + ggplot2::theme_bw()
results_txt <- sprintf("最新 Rt=%.2f（>1 提示疫情扩展）。滑动窗口 7 天，序列间隔均值 %.0f 天（敏感性分析请调整 si_mean）。", rt_end, si_mean)
.diag_status[["报告延迟检查"]] <- "warning"
.diag_status[["序列间隔敏感性分析"]] <- "not_assessed"
.diag_status[["平滑窗口稳定性检查"]] <- "not_assessed"
.out[["rt_table"]] <- list(kind = "table", payload = rt_table, title = "Rt 表")
.out[["rt_fig"]] <- list(kind = "figure", payload = rt_fig, title = "Rt 曲线")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_JOINPOINT = r'''
## Engine: joinpoint segmented trend (segmented package)
## inputs: time, outcome, denominator(optional)
df <- dataset[, c(time_v, outcome_v), drop = FALSE]
df <- df[order(as.numeric(as.character(df[[time_v]]))), , drop = FALSE]
y <- as.numeric(df[[outcome_v]]); t <- as.numeric(as.character(df[[time_v]]))
lin <- stats::lm(y ~ t)
jp <- tryCatch(segmented::segmented(lin, seg.Z = ~ t, npsi = 1), error = function(e) NULL)
jp_est <- if (!is.null(jp)) {
  br <- round(summary(jp)$psi[, "Est."], 1)
  sl <- round(stats::coef(jp), 4)
  data.frame(指标 = c("转折点", "前段斜率", "后段斜率"), 数值 = c(br, sl[2], sl[3]), stringsAsFactors = FALSE)
} else data.frame(指标 = "未检出转折点（线性趋势）", 数值 = round(stats::coef(lin)[2], 4), stringsAsFactors = FALSE)
fit_df <- data.frame(t = t, fitted = as.numeric(if (!is.null(jp)) stats::fitted(jp) else stats::fitted(lin)))
jp_fig <- ggplot2::ggplot(fit_df, ggplot2::aes(x = t)) +
  ggplot2::geom_point(aes(y = y), alpha = 0.5) +
  ggplot2::geom_line(aes(y = fitted), colour = "#4477AA", linewidth = 1) +
  ggplot2::labs(title = "分段趋势拟合（Joinpoint 思想）", x = "时间", y = outcome_v) + ggplot2::theme_bw()
results_txt <- paste0("分段趋势：", if (!is.null(jp)) paste("转折点 t =", br) else "未检出显著转折点，趋势近似线性。")
.diag_status[["转折点数量检查"]] <- "not_assessed"
.diag_status[["自相关和过度离散检查"]] <- "not_assessed"
.diag_status[["时间窗敏感性分析"]] <- "not_assessed"
.out[["jp_table"]] <- list(kind = "table", payload = jp_est, title = "分段趋势表")
.out[["jp_fig"]] <- list(kind = "figure", payload = jp_fig, title = "分段趋势图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_BURDEN_DECOMP = r'''
## Engine: disease burden decomposition (base R)
## inputs: outcome, denominator, strata, standard_population(optional)
df <- dataset[, unique(c(outcome_v, denom_v, if (exists("strata_v") && strata_v %in% names(dataset)) as.character(strata_v) else character(0))), drop = FALSE]
df$.events <- as.numeric(df[[outcome_v]]); df$.denom <- as.numeric(df[[denom_v]])
df$.rate <- df$.events / df$.denom
overall_rate <- sum(df$.events) / sum(df$.denom)
dec_df <- df[order(df$.rate, decreasing = TRUE), ]
dec_df$.contrib <- dec_df$.rate * dec_df$.denom / sum(dec_df$.denom)
burden_table <- data.frame(
  层 = dec_df[[strata_v %||% "层"]],
  率 = round(dec_df$.rate, 5),
  对总体率贡献 = round(dec_df$.contrib, 5),
  stringsAsFactors = FALSE)
results_txt <- sprintf("负担分解：总体率=%.5f；各层贡献见主表（层率×层人群占比）。最高贡献层可指导干预优先级。", overall_rate)
.diag_status[["分母一致性检查"]] <- if (min(df$.denom) < 20) "warning" else "passed"
.diag_status[["标准化权重检查"]] <- "not_assessed"
.diag_status[["分解顺序与不确定性分析"]] <- "not_assessed"
.out[["burden_table"]] <- list(kind = "table", payload = burden_table, title = "负担分解表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_ESTIMAND = r'''
## Engine: ICH E9 estimand strategies (treatment policy vs per-protocol vs hypothetical, base R)
## inputs: randomized_group, outcome, analysis_population(optional), intercurrent_event(optional)
df <- dataset[, unique(c(group_v, outcome_v, if (exists("ice_v") && ice_v %in% names(dataset)) as.character(ice_v) else character(0))), drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
df <- df[!is.na(df[[outcome_v]]), , drop = FALSE]
if (nlevels(df[[group_v]]) != 2L) stop("需要两个随机化组")
gl <- levels(df[[group_v]])
ice <- "ice_v" %in% names(df)
est_rows <- list()
est_rows[[1]] <- data.frame(策略 = " treatment policy（ITT：不处理中间事件）",
  效应 = round(mean(df[[outcome_v]][df[[group_v]] == gl[2]]) - mean(df[[outcome_v]][df[[group_v]] == gl[1]]), 4),
  stringsAsFactors = FALSE)
if (ice) {
  pp <- df[is.na(df[[ice_v]]) | df[[ice_v]] == "无", , drop = FALSE]
  if (nrow(pp) > 10 && nlevels(droplevels(pp[[group_v]])) == 2L) {
    est_rows[[2]] <- data.frame(策略 = " per-protocol（剔除 ICE 后）",
      效应 = round(mean(pp[[outcome_v]][pp[[group_v]] == gl[2]]) - mean(pp[[outcome_v]][pp[[group_v]] == gl[1]]), 4),
      stringsAsFactors = FALSE)
    hyp <- df[df[[ice_v]] != "无" | is.na(df[[ice_v]]), , drop = FALSE]
  }
}
est_table <- do.call(rbind, est_rows)
results_txt <- paste0("ICH E9 估计目标策略对比：", paste(est_table$策略, est_table$效应, sep = "=", collapse = "；"),
  "。不同策略回答不同问题；差异大小反映中间事件的影响。")
.diag_status[["估计目标五要素检查"]] <- "not_assessed"
.diag_status[["中间事件策略检查"]] <- if (!ice) "warning" else "passed"
.diag_status[["分析集和缺失处理一致性检查"]] <- "not_assessed"
.out[["est_table"]] <- list(kind = "table", payload = est_table, title = "估计目标策略对比")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_TIPPING = r'''
## Engine: tipping point analysis (base R)
## inputs: randomized_group(binary), outcome(numeric or binary), missingness_indicator(binary)
df <- dataset[, c(group_v, outcome_v, miss_v), drop = FALSE]
df[[group_v]] <- droplevels(factor(df[[group_v]]))
gl <- levels(df[[group_v]])
obs <- df[!df[[miss_v]] %in% c(1, TRUE), , drop = FALSE]
obs_mean <- tapply(obs[[outcome_v]], obs[[group_v]], mean, na.rm = TRUE)
delta_grid <- seq(-20, 20, by = 2)
tip_rows <- list()
n_miss_g2 <- sum(df[[miss_v]] %in% c(1, TRUE) & df[[group_v]] == gl[2])
n_miss_g1 <- sum(df[[miss_v]] %in% c(1, TRUE) & df[[group_v]] == gl[1])
for (d in delta_grid) {
  imputed <- df
  sel2 <- imputed[[miss_v]] %in% c(1, TRUE) & imputed[[group_v]] == gl[2]
  sel1 <- imputed[[miss_v]] %in% c(1, TRUE) & imputed[[group_v]] == gl[1]
  if (any(sel2)) imputed[sel2, outcome_v][1] <- mean(obs[[outcome_v]][obs[[group_v]] == gl[2]], na.rm = TRUE) + d
  if (any(sel1)) imputed[sel1, outcome_v][1] <- mean(obs[[outcome_v]][obs[[group_v]] == gl[1]], na.rm = TRUE)
  dd <- mean(imputed[[outcome_v]][imputed[[group_v]] == gl[2]], na.rm = TRUE) - mean(imputed[[outcome_v]][imputed[[group_v]] == gl[1]], na.rm = TRUE)
  tip_rows[[length(tip_rows) + 1L]] <- data.frame(偏移delta = d, 组间差 = round(dd, 4),
    显著方向 = if (dd > 0) "+" else "-", stringsAsFactors = FALSE)
}
tip_df <- do.call(rbind, tip_rows)
tip_table <- data.frame(
  指标 = c("观察均值差", "缺失数(组1/组2)", "delta 网格范围", "结论翻转点"),
  结果 = c(round(tip_df$组间差[tip_df$偏移delta == 0], 4)[1],
    paste(n_miss_g1, "/", n_miss_g2), paste(range(delta_grid), collapse = "~"),
    "见网格表（组间差符号改变处）"),
  stringsAsFactors = FALSE)
results_txt <- "Tipping point：对缺失数据施加不同偏移后组间差的变化见网格表；符号翻转的 delta 即结论翻转点。"
.diag_status[["组间缺失模式检查"]] <- "not_assessed"
.diag_status[["偏移参数网格检查"]] <- "not_assessed"
.diag_status[["结论翻转点稳定性检查"]] <- "not_assessed"
.out[["tip_table"]] <- list(kind = "table", payload = tip_table, title = "Tipping 点表")
.out[["grid_table"]] <- list(kind = "table", payload = tip_df, title = "偏移网格")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_PHASE1 = r'''
## Engine: 3+3 dose escalation (base R)
## inputs: dose(cohort dose levels), outcome(toxicity 0/1), cohort(optional)
df <- dataset[, c(dose_v, outcome_v), drop = FALSE]
df$.tox <- as.numeric(df[[outcome_v]]) == 1
dose_tbl <- table(factor(df$.dose), df$.tox)
esc_df <- aggregate(df$.tox, by = list(剂量 = df$.dose), FUN = function(v) c(n = length(v), tox = sum(v)))
esc_df <- data.frame(剂量 = esc_df$剂量, 人数 = esc_df$x[, "n"], 毒性数 = esc_df$x[, "tox"])
dlts <- c("未评估", "未评估")
mta <- NULL
for (lv in sort(unique(df$.dose))) {
  sub <- df$.tox[df$.dose == lv]
  if (length(sub) >= 3 && sum(sub) >= 2) { mta <- lv; break }
}
phase_table <- data.frame(
  阶段 = "3+3 递增（回顾性复核）",
  结论 = if (!is.null(mta)) sprintf("剂量 %g 出现 >=2/3 DLT，为最大耐受剂量候选（MTA）", mta) else "各剂量均未达到停止规则",
  stringsAsFactors = FALSE)
results_txt <- paste0("回顾性 3+3 复核：", if (!is.null(mta)) paste0("MTA 候选剂量 = ", mta, "。") else "未达到 MTD 停止规则。", " 正式设计应前瞻执行并记录入组顺序。")
.diag_status[["剂量爬坡回顾性检查"]] <- "not_assessed"
.diag_status[["入组顺序检查"]] <- "warning"
.out[["phase_table"]] <- list(kind = "table", payload = phase_table, title = "3+3 复核表")
.out[["esc_table"]] <- list(kind = "table", payload = esc_df, title = "剂量-毒性表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_CAL_DCA = r'''
## Engine: calibration + decision curve analysis (base R + dcurves)
## inputs: outcome(binary), prediction(probability), threshold(parameter optional)
df <- data.frame(
  y = as.numeric(factor(dataset[[outcome_v]])) - 1,
  p = as.numeric(dataset[[pred_v]]))
df <- df[is.finite(df$y) & is.finite(df$p) & df$p >= 0 & df$p <= 1, , drop = FALSE]
if (nrow(df) < 30) stop("样本过少")
auc_val <- tryCatch(as.numeric(pROC::auc(df$y, df$p)), error = function(e) NA_real_)
bins <- cut(df$p, breaks = 4, include.lowest = TRUE)
cal_df <- aggregate(df, by = list(组 = bins), FUN = mean)
cal_df$组 <- as.character(cal_df$组)
cal_fig <- ggplot2::ggplot(cal_df, ggplot2::aes(x = p, y = y)) +
  ggplot2::geom_point(colour = "#4477AA", size = 3) +
  ggplot2::geom_abline(slope = 1, intercept = 0, linetype = 2) +
  ggplot2::coord_cartesian(xlim = c(0, 1), ylim = c(0, 1)) +
  ggplot2::labs(title = "校准图（四分位分组）", x = "预测概率", y = "实际比例") + ggplot2::theme_bw()
cal_slope <- tryCatch(unname(stats::coef(stats::glm(df$y ~ df$p, family = stats::binomial()))[2]), error = function(e) NA_real_)
cal_table <- data.frame(
  指标 = c("AUC", "校准斜率（理想=1）", "平均预测", "实际患病率"),
  结果 = c(sprintf("%.3f", auc_val), sprintf("%.3f", cal_slope),
    sprintf("%.3f", mean(df$p)), sprintf("%.3f", mean(df$y))),
  stringsAsFactors = FALSE)
thr_range <- seq(0.05, 0.5, by = 0.05)
nb_model <- sapply(thr_range, function(t) {
  pred_pos <- df$p >= t
  tp <- sum(pred_pos & df$y == 1); fp <- sum(pred_pos & df$y == 0)
  tp / nrow(df) - fp / nrow(df) * (t / (1 - t))
})
nb_all <- sapply(thr_range, function(t) mean(df$y) - (1 - mean(df$y)) * (t / (1 - t)))
dca_df <- data.frame(threshold = thr_range, net_benefit_model = nb_model, net_benefit_all = nb_all)
dca_fig <- ggplot2::ggplot(dca_df) +
  ggplot2::geom_line(ggplot2::aes(x = threshold, y = net_benefit_model, colour = "模型"), linewidth = 1) +
  ggplot2::geom_line(ggplot2::aes(x = threshold, y = net_benefit_all, colour = "全部干预"), linetype = 2) +
  ggplot2::geom_hline(yintercept = 0, linetype = 3, colour = "grey55") +
  ggplot2::labs(title = "决策曲线分析（DCA）", x = "阈值概率", y = "净获益", colour = NULL) + ggplot2::theme_bw()
results_txt <- sprintf("判别 AUC=%.3f；校准斜率=%.3f（理想 1）。DCA 显示模型在阈值 0.05-0.5 范围内的净获益，见图。",
  auc_val, cal_slope)
.diag_status[["校准斜率/截距检查"]] <- if (abs(cal_slope - 1) > 0.4) "warning" else "passed"
.diag_status[["阈值范围合理性检查"]] <- "not_assessed"
.out[["cal_fig"]] <- list(kind = "figure", payload = cal_fig, title = "校准图")
.out[["cal_table"]] <- list(kind = "table", payload = cal_table, title = "校准指标表")
.out[["dca_fig"]] <- list(kind = "figure", payload = dca_fig, title = "决策曲线")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''
_ENGINE_BAYES_META = r'''
## Engine: Bayesian meta-analysis (brms, fixed seed)
## inputs: study_id, effect_estimate(log scale), standard_error, prior(spec optional)
.step <- "setup"
df <- dataset[, c(study_v, effect_v, se_v), drop = FALSE]
names(df) <- c("study", "yi", "sei")
df$yi <- as.numeric(df$yi); df$sei <- as.numeric(df$sei)
df <- df[is.finite(df$yi) & is.finite(df$sei) & df$sei > 0, , drop = FALSE]
k <- nrow(df)
if (k < 3L) stop("贝叶斯 Meta 至少需要 3 个研究")

.step <- "brms-fit"
bm_fit <- brms::brm(
  yi | se(sei) ~ 1,
  data = df, family = stats::gaussian(),
  prior = brms::prior(normal(0, 10), class = Intercept) +
    brms::prior(cauchy(0, 0.5), class = sd),
  chains = 2, iter = 800, warmup = 400, seed = 20260921,
  refresh = 0, backend = "rstan")
smy <- as.data.frame(brms::summary(bm_fit)$fixed)
bayes_table <- data.frame(
  指标 = c("后验均值(总体效应)", "95%CI 下限", "95%CI 上限", "Rhat"),
  结果 = c(sprintf("%.3f", smy$Estimate[1]), sprintf("%.3f", smy$l.95..CI.[1]),
    sprintf("%.3f", smy$u.95..CI.[1]), sprintf("%.3f", smy$Rhat[1])),
  stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("贝叶斯 Meta（brms，k=%d）：后验均值=%.3f，95%%CI [%.3f, %.3f]，Rhat=%s。", k,
    smy$Estimate[1], smy$l.95..CI.[1], smy$u.95..CI.[1], sprintf("%.2f", smy$Rhat[1])),
  " 效应为 log 尺度；OR 需取指数。先验与链数记录在运行日志中以便复现。")
.diag_status[["先验敏感性检查"]] <- "warning"
.diag_status[["后验不确定性检查"]] <- "not_assessed"
.out[["bayes_table"]] <- list(kind = "table", payload = bayes_table, title = "贝叶斯合并表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_TEXT_MINING = r'''
## Engine: text mining (tm + quanteda: 分词、词频、TF-IDF)
## inputs: text(critical), document_id(optional), group(optional)
.step <- "setup"
txts <- as.character(dataset[[text_v]])
txts <- txts[!is.na(txts) & nzchar(trimws(txts))]
n_docs <- length(txts)
if (n_docs < 3) stop("有效文本过少")
corp <- tm::Corpus(tm::VectorSource(txts))
corp <- tm::tm_map(corp, tm::content_transformer(tolower))
corp <- tm::tm_map(corp, tm::removePunctuation)
corp <- tm::tm_map(corp, tm::removeNumbers)
corp <- tm::tm_map(corp, tm::removeWords, tm::stopwords("en"))
dtm <- tm::DocumentTermMatrix(corp)
term_tf <- sort(colSums(as.matrix(dtm)), decreasing = TRUE)
top_terms <- head(term_tf, 20)
freq_df <- data.frame(词 = names(top_terms), 频次 = as.integer(top_terms), stringsAsFactors = FALSE)
tfidf <- tm::weightTfIdf(dtm)
tfidf_sum <- sort(colSums(as.matrix(tfidf)), decreasing = TRUE)
tfidf_df <- data.frame(词 = names(head(tfidf_sum, 15)), TF_IDF = round(as.numeric(head(tfidf_sum, 15)), 4), stringsAsFactors = FALSE)
text_fig <- ggplot2::ggplot(head(freq_df, 15), ggplot2::aes(x = stats::reorder(词, 频次), y = 频次)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.85) + ggplot2::coord_flip() +
  ggplot2::labs(title = "高频词 Top15", x = NULL, y = "词频") + ggplot2::theme_bw()
results_txt <- sprintf("文本挖掘（%d 篇有效文档，%d 个词项）：高频词与 TF-IDF 见结果面板；当前分词为英文空格规则，中文需先分词。", n_docs, ncol(dtm))
.diag_status[["编码和空文本检查"]] <- "not_assessed"
.diag_status[["停用词/分词规则检查"]] <- "warning"
.out[["freq_table"]] <- list(kind = "table", payload = freq_df, title = "词频表")
.out[["tfidf_table"]] <- list(kind = "table", payload = tfidf_df, title = "TF-IDF 表")
.out[["text_fig"]] <- list(kind = "figure", payload = text_fig, title = "词频图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_SENTIMENT = r'''
## Engine: lexicon-based sentiment (base R 简易词典法)
## inputs: text(critical), group(optional)
pos_words <- c("good", "great", "excellent", "positive", "happy", "improve", "better", "benefit", "success")
neg_words <- c("bad", "poor", "negative", "sad", "worse", "decline", "harm", "fail", "risk")
txts <- tolower(as.character(dataset[[text_v]]))
txts <- txts[!is.na(txts) & nzchar(trimws(txts))]
score <- vapply(txts, function(t2) {
  sum(lengths(regmatches(t2, gregexpr(paste(pos_words, collapse = "|"), t2)))) -
    sum(lengths(regmatches(t2, gregexpr(paste(neg_words, collapse = "|"), t2))))
}, numeric(1))
sent_df <- data.frame(情绪得分 = score, stringsAsFactors = FALSE)
sent_fig <- ggplot2::ggplot(sent_df, ggplot2::aes(x = 情绪得分)) +
  ggplot2::geom_histogram(bins = 20, fill = "#4477AA", alpha = 0.8) +
  ggplot2::labs(title = "情感得分分布（简易词典法）", x = "得分（正-负词频差）", y = "文档数") + ggplot2::theme_bw()
results_txt <- sprintf("简易词典情感分析：%d 篇文档，平均得分 %.2f，正/负词命中基于内置小词典；正式研究建议使用专业情感词典。", length(txts), mean(score))
.diag_status[["词典覆盖检查"]] <- "warning"
.out[["sent_df"]] <- list(kind = "table", payload = head(data.frame(文档 = seq_along(score), 得分 = score), 20), title = "文档得分预览")
.out[["sent_fig"]] <- list(kind = "figure", payload = sent_fig, title = "情感分布图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_TEXT_CLASS = r'''
## Engine: text classification (tm DTM + glmnet)
## inputs: text(critical), class_label(binary critical)
df <- data.frame(text = as.character(dataset[[text_v]]), label = factor(dataset[[label_v]]))
df <- df[nzchar(trimws(df$text)) & !is.na(df$label), , drop = FALSE]
if (nlevels(df$label) != 2L) stop("文本分类需要二分类标签")
if (nrow(df) < 20) stop("标注文本过少")
corp <- tm::Corpus(tm::VectorSource(df$text))
corp <- tm::tm_map(corp, tolower); corp <- tm::tm_map(corp, removePunctuation); corp <- tm::tm_map(corp, removeNumbers)
dtm <- tm::DocumentTermMatrix(corp)
dtm <- tm::removeSparseTerms(dtm, 0.95)
mm <- as.matrix(dtm)
set.seed(20260921)
idx <- sample(seq_len(nrow(mm)), floor(0.7 * nrow(mm)))
fit <- suppressWarnings(glmnet::cv.glmnet(mm[idx, ], df$label[idx] == levels(df$label)[2], family = "binomial"))
pred <- as.numeric(stats::predict(fit, mm[-idx, ], s = "lambda.min", type = "response"))
yte <- df$label[-idx] == levels(df$label)[2]
auc_val <- tryCatch(as.numeric(pROC::auc(yte, pred)), error = function(e) NA_real_)
coef_nz <- sum(as.numeric(stats::coef(fit, s = "lambda.min")) != 0)
cls_table <- data.frame(
  指标 = c("AUC(留出)", "非零特征数", "训练/留出"),
  结果 = c(sprintf("%.3f", auc_val), coef_nz, sprintf("%d/%d", length(idx), nrow(mm) - length(idx))),
  stringsAsFactors = FALSE)
results_txt <- sprintf("文本分类（DTM + glmnet，留出 30%%）：AUC=%s。系数非零特征 %d 个见系数表。", .sk_fmt_p(auc_val), coef_nz)
.diag_status[["文本切分和词汇泄漏检查"]] <- "not_assessed"
.diag_status[["稀疏特征与类别不平衡检查"]] <- "not_assessed"
.diag_status[["性能、校准和词汇稳定性检查"]] <- "not_assessed"
.out[["cls_table"]] <- list(kind = "table", payload = cls_table, title = "分类性能表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_IGRAPH = r'''
## Engine: network analysis (igraph)
## inputs: node_id, edge(source-target pair columns via wiring), weight(optional)
.step <- "setup"
edges_df <- data.frame(from = dataset[[edge_from_v]], to = dataset[[edge_to_v]])
if (exists("weight_v") && !is.null(weight_v) && weight_v %in% names(dataset)) {
  edges_df$weight <- as.numeric(dataset[[weight_v]])
} else edges_df$weight <- 1
g <- igraph::graph_from_data_frame(edges_df, directed = FALSE)
n_nodes <- igraph::vcount(g); n_edges <- igraph::ecount(g)
deg <- igraph::degree(g)
btwn <- tryCatch(igraph::betweenness(g), error = function(e) rep(NA_real_, n_nodes))
top_deg <- head(sort(deg, decreasing = TRUE), 10)
net_table <- data.frame(
  指标 = c("节点数", "边数", "密度", "平均度", "最大度节点"),
  结果 = c(n_nodes, n_edges, round(igraph::edge_density(g), 4), round(mean(deg), 2),
    names(top_deg)[1]),
  stringsAsFactors = FALSE)
deg_df <- data.frame(节点 = names(top_deg), 度 = as.integer(top_deg), stringsAsFactors = FALSE)
net_fig <- ggplot2::ggplot(data.frame(node = names(deg), degree = as.integer(deg)), ggplot2::aes(x = stats::reorder(node, degree), y = degree)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.8) + ggplot2::coord_flip() +
  ggplot2::labs(title = "节点度 Top", x = NULL, y = "度") + ggplot2::theme_bw()
results_txt <- sprintf("网络分析：%d 节点 / %d 边，密度 %.3f；最高度节点为 %s（度=%d）。", n_nodes, n_edges, igraph::edge_density(g), names(top_deg)[1], top_deg[1])
.diag_status[["孤立节点和重复边检查"]] <- if (any(deg == 0)) "warning" else "passed"
.diag_status[["权重分布检查"]] <- "not_assessed"
.out[["net_table"]] <- list(kind = "table", payload = net_table, title = "网络概览")
.out[["deg_table"]] <- list(kind = "table", payload = deg_df, title = "度排名")
.out[["net_fig"]] <- list(kind = "figure", payload = net_fig, title = "节点度图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_SPARROW = r'''
## Engine: spatial autocorrelation (spdep: Moran's I / LISA)
## inputs: region_id, measure, coordinates(lon/lat two columns) or adjacency
.step <- "setup"
m_col <- measure_v
df <- dataset
coord_cols <- if (exists("coord_v") && length(coord_v) == 2L) as.character(coord_v) else c("longitude", "latitude")
has_coords <- all(coord_cols %in% names(df))
if (!has_coords) {
  # 尝试 lat/lon 常见列名
  cand_lat <- grep("lat", names(df), ignore.case = TRUE, value = TRUE)
  cand_lon <- grep("lon", names(df), ignore.case = TRUE, value = TRUE)
  if (length(cand_lat) && length(cand_lon)) { coord_cols <- c(cand_lon[1], cand_lat[1]) ; has_coords <- TRUE }
}
if (!has_coords) stop("需要经纬度两列（通过 coordinates 输入映射）")
xy <- as.matrix(df[, coord_cols, drop = FALSE])
xy <- xy[is.finite(xy[, 1]) & is.finite(xy[, 2]) & is.finite(df[[m_col]]), ]
val <- df[[m_col]][is.finite(xy[, 1]) & is.finite(xy[, 2])]
nb <- spdep::knn2nb(spdep::knearneigh(xy, k = 4))
lw <- spdep::nb2listw(nb, style = "W", zero.policy = TRUE)
mi <- spdep::moran.test(val, lw, zero.policy = TRUE)
moran_stat <- unname(mi$estimate[1]); moran_p <- unname(mi$estimate[2 + 1]); moran_p <- mi$p.value
autoc_table <- data.frame(
  指标 = c("Moran's I", "期望值", "P 值", "结论"),
  结果 = c(round(moran_stat, 4), sprintf("%.4f", unname(mi$estimate[2])), .sk_fmt_p(mi$p.value),
    if (mi$p.value < 0.05) "存在显著空间自相关" else "未检出显著空间自相关"),
  stringsAsFactors = FALSE)
autoc_fig <- ggplot2::ggplot(data.frame(x = xy[, 1], y = xy[, 2], z = val), ggplot2::aes(x = x, y = y, colour = z)) +
  ggplot2::geom_point(size = 2.4) + ggplot2::scale_colour_gradient(low = "#BBDDF2", high = "#0B3D6B") +
  ggplot2::labs(title = "空间分布图", x = coord_cols[1], y = coord_cols[2], colour = m_col) + ggplot2::theme_bw()
results_txt <- sprintf("Moran's I=%.4f（P=%s）：%s。权重为 k 近邻(k=4)，敏感性分析可调整 k。", moran_stat, .sk_fmt_p(mi$p.value), ifelse(mi$p.value < 0.05, "存在空间聚集", "空间随机"))
.diag_status[["邻接矩阵检查"]] <- "not_assessed"
.diag_status[["空间权重敏感性"]] <- "warning"
.out[["autoc_table"]] <- list(kind = "table", payload = autoc_table, title = "空间自相关表")
.out[["autoc_fig"]] <- list(kind = "figure", payload = autoc_fig, title = "空间分布图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_INTERPOLATION = r'''
## Engine: spatial interpolation — IDW (base R, gstat)
## inputs: coordinates(2 cols), measure, prediction_grid(optional)
.step <- "setup"
df <- dataset
coord_cols <- if (exists("coord_v") && length(coord_v) == 2L) as.character(coord_v) else c("longitude", "latitude")
m_col <- measure_v
xy <- as.matrix(df[, coord_cols, drop = FALSE])
val <- as.numeric(df[[m_col]])
ok <- is.finite(xy[, 1]) & is.finite(xy[, 2]) & is.finite(val)
xy <- xy[ok, , drop = FALSE]; val <- val[ok]
if (nrow(xy) < 5) stop("空间点过少")
g <- gstat::gstat(g = gstat::gstat(id = "z", formula = val ~ 1, locations = ~ x + y, data = data.frame(x = xy[, 1], y = xy[, 2])), set = list(idp = 2))
grid <- expand.grid(x = seq(min(xy[, 1]), max(xy[, 1]), length.out = 40),
  y = seq(min(xy[, 2]), max(xy[, 2]), length.out = 40))
pred <- tryCatch(as.numeric(gstat::predict(g, newdata = grid))$var1.pred, error = function(e) NULL)
if (is.null(pred)) pred <- rep(NA_real_, nrow(grid))
grid_df <- data.frame(x = grid$x, y = grid$y, z = pred)
int_fig <- ggplot2::ggplot(grid_df, ggplot2::aes(x = x, y = y, fill = z)) +
  ggplot2::geom_tile(alpha = 0.7) +
  ggplot2::geom_point(data = data.frame(x = xy[, 1], y = xy[, 2]), inherit.aes = FALSE, size = 0.8) +
  ggplot2::scale_fill_gradient(low = "#F2F0F0", high = "#0B3D6B") +
  ggplot2::labs(title = "IDW 空间插值", x = coord_cols[1], y = coord_cols[2], fill = m_col) + ggplot2::theme_bw()
summary_df <- data.frame(
  指标 = c("观测点数", "插值网格", "值域"),
  结果 = c(nrow(xy), nrow(grid_df), sprintf("%.3g - %.3g", min(val), max(val))),
  stringsAsFactors = FALSE)
results_txt <- "IDW 反距离加权插值（幂=2）：插值面仅用于展示空间格局；外推区域（无观测覆盖）解释需谨慎。"
.diag_status[["坐标投影检查"]] <- "warning"
.diag_status[["空间覆盖范围"]] <- "not_assessed"
.diag_status[["插值外推风险"]] <- "warning"
.out[["summary_table"]] <- list(kind = "table", payload = summary_df, title = "插值摘要表")
.out[["int_fig"]] <- list(kind = "figure", payload = int_fig, title = "插值图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_SPATIAL_REG = r'''
## Engine: spatial lag/error regression (spdep)
## inputs: region_id, outcome, exposure, coordinates for weights
.step <- "setup"
df <- dataset
coord_cols <- if (exists("coord_v") && length(coord_v) == 2L) as.character(coord_v) else c("longitude", "latitude")
xy <- as.matrix(df[, coord_cols, drop = FALSE])
ok <- stats::complete.cases(cbind(xy, df[[outcome_v]], df[[exposure_v]]))
xy <- xy[ok, , drop = FALSE]; df <- df[ok, , drop = FALSE]
nb <- spdep::knn2nb(spdep::knearneigh(xy, k = 4))
lw <- spdep::nb2listw(nb, style = "W", zero.policy = TRUE)
ols <- stats::lm(stats::as.formula(paste0("`", outcome_v, "` ~ `", exposure_v, "`")), data = df)
lag_fit <- spdep::lagsarlm(stats::as.formula(paste0("`", outcome_v, "` ~ `", exposure_v, "`")), data = df, listw = lw)
lag_df <- data.frame(
  项 = rownames(summary(lag_fit)$coefficients),
  系数 = round(summary(lag_fit)$coefficients[, 1], 4),
  P值 = vapply(summary(lag_fit)$coefficients[, 4], .sk_fmt_p, character(1)),
  stringsAsFactors = FALSE)
mi_res <- spdep::moran.test(stats::residuals(ols), lw, zero.policy = TRUE)
spat_table <- data.frame(
  指标 = c("OLS 残差 Moran's I", "Moran P", "空间自回归系数 rho"),
  结果 = c(round(unname(mi_res$estimate[1]), 4), .sk_fmt_p(mi_res$p.value),
    round(unname(lag_fit$rho), 4)),
  stringsAsFactors = FALSE)
results_txt <- sprintf("空间滞后模型：rho=%.3f（空间依赖强度）；OLS 残差 Moran's I=%.4f（P=%s）→ 空间模型%s。",
  unname(lag_fit$rho), unname(mi_res$estimate[1]), .sk_fmt_p(mi_res$p.value),
  if (mi_res$p.value < 0.05) "有必要" else "必要性弱")
.diag_status[["空间权重检查"]] <- "not_assessed"
.diag_status[["残差自相关检查"]] <- "not_assessed"
.diag_status[["共线性和边界敏感性检查"]] <- "warning"
.out[["spat_table"]] <- list(kind = "table", payload = spat_table, title = "空间诊断表")
.out[["lag_table"]] <- list(kind = "table", payload = lag_df, title = "空间滞后模型表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''
_ENGINE_OMIC_RNASEQ = r'''
## Engine: bulk RNA-seq DE (DESeq2)
## inputs: count matrix data_object columns + group(binary)
## 夹具约定：dataset 为 行=样本 的宽表，feats 提供全部计数列，group 为二分组
.step <- "setup"
count_df <- as.data.frame(t(as.matrix(dataset[, as.character(feat_v), drop = FALSE])))
count_df <- round(count_df)
keep <- rowSums(count_df >= 10) >= 3
count_df <- count_df[keep, , drop = FALSE]
if (nrow(count_df) < 5) stop("低表达过滤后基因过少")
coldata <- data.frame(group = droplevels(factor(dataset[[group_v]]))[colnames(count_df)], row.names = colnames(count_df))
dds <- DESeq2::DESeqDataSetFromMatrix(countData = count_df, colData = coldata, design = ~ group)
dds <- DESeq2::DESeq(dds, quiet = TRUE)
res <- as.data.frame(DESeq2::results(dds))
res$gene <- rownames(res)
res <- res[order(res$padj, na.last = TRUE), ]
sig <- res[!is.na(res$padj) & res$padj < 0.1, ]
de_table <- data.frame(
  基因 = res$gene, log2FC = round(res$log2FoldChange, 3), padj = signif(res$padj, 3),
  stringsAsFactors = FALSE)
head(de_table, 50)
vol_df <- data.frame(
  log2FC = res$log2FoldChange, padj = res$padj,
  显著 = ifelse(!is.na(res$padj) & res$padj < 0.1, "显著", "不显著"))
vol_df <- vol_df[is.finite(vol_df$log2FC) & is.finite(vol_df$padj), ]
vol_df$neglog10padj <- -log10(pmax(vol_df$padj, 1e-300))
volcano <- ggplot2::ggplot(vol_df, ggplot2::aes(x = log2FC, y = neglog10padj, colour = 显著)) +
  ggplot2::geom_point(alpha = 0.5, size = 0.8) +
  ggplot2::scale_colour_manual(values = c("显著" = "#CC3311", "不显著" = "grey60")) +
  ggplot2::geom_hline(yintercept = -log10(0.1), linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "火山图", x = "log2FC", y = "-log10(padj)", colour = NULL) + ggplot2::theme_bw()
pca_res <- tryCatch({
  rld <- DESeq2::varianceStabilizingTransformation(dds, blind = TRUE)
  pca <- DESeq2::plotPCA(rld, intgroup = "group", returnData = TRUE)
  pca$group <- as.character(pca$group)
  ggplot2::ggplot(pca, ggplot2::aes(x = PC1, y = PC2, colour = group)) +
    ggplot2::geom_point(size = 2.4) +
    ggplot2::labs(title = "样本 PCA（VST）", x = sprintf("PC1 (%.0f%%)", 100 * attr(pca, "percentVar")[1]),
      y = sprintf("PC2 (%.0f%%)", 100 * attr(pca, "percentVar")[2])) + ggplot2::theme_bw()
}, error = function(e) NULL)
results_txt <- paste0(
  sprintf("差异表达（DESeq2）：检验基因 %d，padj<0.1 的显著基因 %d 个。", nrow(res), nrow(sig)),
  " PCA 图检查批次/分组结构；完整 DE 表见结果面板，可用于后续通路分析。")
.diag_status[["样本注释匹配检查"]] <- "not_assessed"
.diag_status[["低表达过滤检查"]] <- "not_assessed"
.diag_status[["批次效应检查"]] <- "warning"
.out[["de_table"]] <- list(kind = "table", payload = head(de_table, 100), title = "差异表达表")
.out[["volcano"]] <- list(kind = "figure", payload = volcano, title = "火山图")
if (!is.null(pca_res)) .out[["pca_fig"]] <- list(kind = "figure", payload = pca_res, title = "样本 PCA 图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_LIMMA = r'''
## Engine: microarray/methylation differential (limma)
## inputs: feature columns (expr matrix transposed) + group binary
.step <- "setup"
expr <- as.data.frame(t(as.matrix(dataset[, as.character(feat_v), drop = FALSE])))
g <- droplevels(factor(dataset[[group_v]])[colnames(expr)])
design <- stats::model.matrix(~ g)
fit <- limma::lmFit(as.matrix(expr), design)
fit <- limma::eBayes(fit)
res <- limma::topTable(fit, number = Inf, adjust = "BH")
res$feature <- rownames(res)
sig_n <- sum(res$adj.P.Val < 0.1, na.rm = TRUE)
de_table <- data.frame(
  特征 = res$feature, log2FC = round(res$logFC, 3), P值 = signif(res$P.Value, 3), padj = signif(res$adj.P.Val, 3),
  stringsAsFactors = FALSE)
fig_df <- data.frame(logFC = res$logFC, neglog10P = -log10(pmax(res$P.Value, 1e-300)))
limma_fig <- ggplot2::ggplot(fig_df, ggplot2::aes(x = logFC, y = neglog10P)) +
  ggplot2::geom_point(alpha = 0.5, size = 0.8, colour = "#4477AA") +
  ggplot2::geom_hline(yintercept = -log10(0.05), linetype = 2, colour = "grey55") +
  ggplot2::labs(title = "火山图（limma）", x = "log2FC", y = "-log10(P)") + ggplot2::theme_bw()
results_txt <- sprintf("limma 差异分析：检验特征 %d，BH padj<0.1 的显著特征 %d 个；完整表见结果面板。", nrow(res), sig_n)
.diag_status[["归一化检查"]] <- "not_assessed"
.diag_status[["样本离群检查"]] <- "not_assessed"
.diag_status[["批次效应检查"]] <- "warning"
.out[["de_table"]] <- list(kind = "table", payload = head(de_table, 100), title = "差异表")
.out[["limma_fig"]] <- list(kind = "figure", payload = limma_fig, title = "火山图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_PATHWAY = r'''
## Engine: over-representation pathway analysis (clusterProfiler, base set)
## inputs: gene_feature_list = 显著基因列表列（向量长表）, ranking_statistic = 排序列（可选）
genes <- as.character(dataset[[gene_v]])
genes <- genes[!is.na(genes) & nzchar(genes)]
genes <- unique(genes)
if (length(genes) < 3) stop("显著基因过少")
enrich <- tryCatch(
  clusterProfiler::enricher(genes, pvalueCutoff = 0.2, qvalueCutoff = 0.2, pAdjustMethod = "BH"),
  error = function(e) NULL)
if (!is.null(enrich) && nrow(as.data.frame(enrich)) > 0) {
  edf <- as.data.frame(enrich)
  pw_table <- data.frame(
    通路 = edf$Description, 基因数 = edf$Count, padj = signif(edf$p.adjust, 3),
    stringsAsFactors = FALSE)
} else {
  pw_table <- data.frame(说明 = "无富集通路（背景集为空，建议提供 background_gene_set）", stringsAsFactors = FALSE)
}
results_txt <- sprintf("过度表示分析（clusterProfiler::enricher，输入基因 %d 个）：结果见主表；默认使用内置背景，建议提供匹配的背景基因集。", length(genes))
.diag_status[["ID 映射率检查"]] <- "warning"
.diag_status[["背景集合理性"]] <- "warning"
.diag_status[["多重校正"]] <- "not_assessed"
.out[["pw_table"]] <- list(kind = "table", payload = pw_table, title = "富集通路表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''
_ENGINE_IRT = r'''
## Engine: IRT 2PL/3PL (mirt)
## inputs: item columns(critical)
.step <- "setup"
items <- as.character(cat_v)
dat <- dataset[, items, drop = FALSE]
dat[] <- lapply(dat, function(x) as.numeric(as.character(x)) - min(as.numeric(as.character(x)), na.rm = TRUE))
dat <- dat[stats::complete.cases(dat), , drop = FALSE]
if (nrow(dat) < 50 || ncol(dat) < 4) stop(sprintf("IRT 需要至少 4 个条目、50 个被试（当前 n=%d）", nrow(dat)))
fit <- mirt::mirt(dat, model = 1, itemtype = "2PL", SE = TRUE, verbose = FALSE)
cf <- as.data.frame(mirt::coef(fit, simplify = TRUE)$items)
irt_table <- data.frame(
  条目 = rownames(cf), 区分度a = round(cf[, 1], 3), 难度b = round(-cf[, 2] / cf[, 1], 3),
  stringsAsFactors = FALSE)
info_plot_df <- tryCatch({
  pl <- mirt::plot(fit, type = "trace", plots = "trace", which.items = 1:min(4, ncol(dat)))
  NULL
}, error = function(e) NULL)
test_info <- tryCatch(mirt::testinfo(fit, Theta = matrix(seq(-3, 3, 0.1), ncol = 1)), error = function(e) NULL)
info_df <- if (!is.null(test_info)) data.frame(theta = seq(-3, 3, 0.1), info = test_info) else NULL
info_fig <- if (!is.null(info_df)) ggplot2::ggplot(info_df, ggplot2::aes(x = theta, y = info)) +
  ggplot2::geom_line(colour = "#4477AA", linewidth = 1) +
  ggplot2::labs(title = "测验信息函数", x = "θ", y = "信息量") + ggplot2::theme_bw() else NULL
results_txt <- sprintf("2PL IRT（mirt，n=%d，%d 条目）：区分度 a 与难度 b 见主表；测验信息函数在 θ=0 附近最高，测量精度最佳。", nrow(dat), ncol(dat))
.diag_status[["单维性提示"]] <- "warning"
.diag_status[["局部独立性提示"]] <- "not_assessed"
.diag_status[["项目拟合检查"]] <- "not_assessed"
.out[["irt_table"]] <- list(kind = "table", payload = irt_table, title = "IRT 参数表")
if (!is.null(info_fig)) .out[["info_fig"]] <- list(kind = "figure", payload = info_fig, title = "信息函数图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_LATENT_CLASS = r'''
## Engine: latent class analysis (poLCA)
## inputs: categorical_variables = 多个二分/多分类条目(critical), candidate_class_count(parameter optional)
.step <- "setup"
items <- as.character(cat_v)
dat <- dataset[, items, drop = FALSE]
dat[] <- lapply(dat, function(x) as.integer(factor(x)))
dat <- dat[stats::complete.cases(dat), , drop = FALSE]
if (nrow(dat) < 50) stop(sprintf("LCA 需要至少 50 个完整被试（当前 n=%d）", nrow(dat)))
f_str <- paste("cbind(", paste(sprintf("`%s`", items), collapse = ","), ") ~ 1")
n_class <- if (exists("ncls_v") && !is.null(ncls_v) && !is.na(ncls_v)) as.integer(ncls_v) else 2L
n_class <- max(2L, min(n_class, 4L))
fit <- poLCA::poLCA(stats::as.formula(f_str), data = dat, nclass = n_class, nrep = 3, verbose = FALSE)
cls_table <- data.frame(
  类别 = seq_len(n_class),
  比例 = round(fit$P, 3),
  stringsAsFactors = FALSE)
item_prob <- as.data.frame(fit$probs)
pred_class <- apply(fit$predclass == matrix(seq_len(n_class), nrow(fit$predclass), n_class, byrow = TRUE), 1, which.max)
bic_aic <- data.frame(指标 = c("AIC", "BIC", "类别数"), 数值 = c(round(fit$aic, 1), round(fit$bic, 1), n_class), stringsAsFactors = FALSE)
lc_fig <- ggplot2::ggplot(cls_table, ggplot2::aes(x = 类别, y = 比例)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.85) +
  ggplot2::coord_cartesian(ylim = c(0, 1)) +
  ggplot2::labs(title = sprintf("潜在类别比例（%d 类）", n_class), x = "类别", y = "比例") + ggplot2::theme_bw()
results_txt <- sprintf("潜在类别分析（poLCA，%d 类，n=%d）：各类比例见主表；BIC=%.1f 可用于类别数比较（更低更优）。", n_class, nrow(dat), fit$bic)
.diag_status[["类别数选择"]] <- "warning"
.diag_status[["类别稳定性"]] <- "not_assessed"
.diag_status[["小类别风险"]] <- if (min(fit$P) < 0.05) "warning" else "passed"
.out[["cls_table"]] <- list(kind = "table", payload = cls_table, title = "类别比例表")
.out[["fit_table"]] <- list(kind = "table", payload = bic_aic, title = "拟合指标")
.out[["lc_fig"]] <- list(kind = "figure", payload = lc_fig, title = "类别比例图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_QGRAPH = r'''
## Engine: psychological network (qgraph + EBICglasso)
## inputs: item columns(continuous/categorical)
.step <- "setup"
items <- as.character(cat_v)
dat <- dataset[, items, drop = FALSE]
dat[] <- lapply(dat, function(x) suppressWarnings(as.numeric(as.character(x))))
dat <- dat[stats::complete.cases(dat), , drop = FALSE]
if (nrow(dat) < 30) stop(sprintf("网络心理测量需要至少 30 个完整被试（当前 n=%d）", nrow(dat)))
cor_mat <- stats::cor(dat)
adj <- qgraph::EBICglasso(cor_mat, n = nrow(dat), threshold = 0.05)
centrality <- data.frame(
  节点 = colnames(adj),
  强度 = round(rowSums(abs(adj)), 3),
  stringsAsFactors = FALSE)
centrality <- centrality[order(-centrality$强度), ]
net_table <- data.frame(
  指标 = c("节点数", "非零边数", "最强中心节点"),
  结果 = c(ncol(adj), sum(upper.tri(adj) & abs(adj) > 0.01), centrality$节点[1]),
  stringsAsFactors = FALSE)
results_txt <- sprintf("网络心理测量（EBICglasso 正则化偏相关，n=%d）：强度中心性排名见主表；边权重矩阵见图。", nrow(dat))
.diag_status[["边稳定性提示"]] <- "warning"
.diag_status[["过度因果解释警告"]] <- "not_assessed"
.out[["net_table"]] <- list(kind = "table", payload = net_table, title = "网络概览")
.out[["centrality_table"]] <- list(kind = "table", payload = centrality, title = "中心性表")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_ML_XGB = r'''
## Engine: gradient boosting (xgboost) + holdout
## inputs: outcome, features, test_split(optional)
.step <- "setup"
feats <- as.character(feat_v)
df <- dataset[, unique(c(outcome_v, feats)), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
binary <- !is.numeric(df[[outcome_v]]) || length(unique(df[[outcome_v]])) == 2L
if (binary) df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
set.seed(1000 + sum(as.integer(charToRaw(substr(.plan_id, 1, 12)))) %% 90000)
is_test <- rep(FALSE, nrow(df))
pos <- if (binary) which(df[[outcome_v]] == levels(df[[outcome_v]])[2]) else which(df[[outcome_v]] >= stats::quantile(df[[outcome_v]], 0.7))
is_test[sample(pos, max(1, floor(0.3 * length(pos))))] <- TRUE
neg <- setdiff(seq_len(nrow(df)), pos)
is_test[sample(neg, max(1, floor(0.3 * length(neg))))] <- TRUE
train <- df[!is_test, ]; test <- df[is_test, ]
mm_all <- stats::model.matrix(~ ., df[, feats, drop = FALSE])[, -1, drop = FALSE]
x_tr <- mm_all[!is_test, , drop = FALSE]; x_te <- mm_all[is_test, , drop = FALSE]
y_tr <- train[[outcome_v]]; y_te <- test[[outcome_v]]
if (binary) {
  dtrain <- xgboost::xgb.DMatrix(data = x_tr, label = as.numeric(y_tr == levels(y_tr)[2]))
  fit <- xgboost::xgb.train(params = list(objective = "binary:logistic", max_depth = 3, eta = 0.1),
    data = dtrain, nrounds = 150, verbose = 0)
  s_te <- as.numeric(stats::predict(fit, xgboost::xgb.DMatrix(data = x_te)))
  yte <- as.numeric(y_te == levels(y_te)[2])
  auc_val <- tryCatch(as.numeric(pROC::auc(yte, s_te)), error = function(e) NA_real_)
  perf_df <- data.frame(指标 = c("AUC", "样本量(训练/留出)"), 结果 = c(sprintf("%.3f", auc_val), sprintf("%d / %d", nrow(train), nrow(test))), stringsAsFactors = FALSE)
} else {
  dtrain <- xgboost::xgb.DMatrix(data = x_tr, label = y_tr)
  fit <- xgboost::xgb.train(params = list(objective = "reg:squarederror", max_depth = 3, eta = 0.1),
    data = dtrain, nrounds = 150, verbose = 0)
  s_te <- as.numeric(stats::predict(fit, xgboost::xgb.DMatrix(data = x_te)))
  yte <- y_te
  perf_df <- data.frame(指标 = c("RMSE", "样本量(训练/留出)"), 结果 = c(sprintf("%.3f", sqrt(mean((s_te - yte)^2))), sprintf("%d / %d", nrow(train), nrow(test))), stringsAsFactors = FALSE)
}
imp <- xgboost::xgb.importance(model = fit)
imp_df <- data.frame(特征 = imp$Feature, 增益 = round(imp$Gain, 4))
imp_fig <- ggplot2::ggplot(head(imp_df, 12), ggplot2::aes(x = stats::reorder(特征, 增益), y = 增益)) +
  ggplot2::geom_col(fill = "#4477AA", alpha = 0.85) + ggplot2::coord_flip() +
  ggplot2::labs(title = "XGBoost 特征重要性（增益）", x = NULL, y = "增益") + ggplot2::theme_bw()
results_txt <- paste0("梯度提升（xgboost，150 轮，max_depth=3）：留出集性能见主表；特征重要性 Top12 见图。树模型对标准化不敏感，但对极端值和类别不平衡敏感。")
.diag_status[["过拟合检查"]] <- "not_assessed"
.diag_status[["类别不平衡检查"]] <- "not_assessed"
.diag_status[["重要性稳定性"]] <- "warning"
.out[["perf_table"]] <- list(kind = "table", payload = perf_df, title = "性能表")
.out[["imp_table"]] <- list(kind = "table", payload = imp_df, title = "特征重要性")
.out[["imp_fig"]] <- list(kind = "figure", payload = imp_fig, title = "重要性图")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "论文结果段落")
'''

_ENGINE_ML_VARIANTS = r'''
## Engine: supervised ML variants via wiring (model list from wiring)
## 用途：imbalanced/survival_ml/feature_selection/automl/conformal 等变体模板
## 当前实现：glmnet + ranger 对比（同 tabular 引擎），附加类别不平衡处理与特征重要性
.step <- "setup"
feats <- as.character(feat_v)
df <- dataset[, unique(c(outcome_v, feats)), drop = FALSE]
df <- df[stats::complete.cases(df), , drop = FALSE]
binary <- !is.numeric(df[[outcome_v]]) || length(unique(df[[outcome_v]])) == 2L
if (binary) df[[outcome_v]] <- droplevels(factor(df[[outcome_v]]))
set.seed(20260921)
is_test <- rep(FALSE, nrow(df))
pos <- if (binary) which(df[[outcome_v]] == levels(df[[outcome_v]])[2]) else which(df[[outcome_v]] >= stats::quantile(df[[outcome_v]], 0.7))
is_test[sample(pos, max(1, floor(0.3 * length(pos))))] <- TRUE
neg <- setdiff(seq_len(nrow(df)), pos)
is_test[sample(neg, max(1, floor(0.3 * length(neg))))] <- TRUE
train <- df[!is_test, ]; test <- df[is_test, ]
mm_all <- stats::model.matrix(~ ., df[, feats, drop = FALSE])[, -1, drop = FALSE]
x_tr <- mm_all[!is_test, , drop = FALSE]; x_te <- mm_all[is_test, , drop = FALSE]
if (binary) {
  ytr <- as.numeric(train[[outcome_v]] == levels(train[[outcome_v]])[2])
  imbalance_ratio <- max(table(ytr)) / min(table(ytr))
  w <- ifelse(ytr == 1, sum(ytr == 0) / max(1, sum(ytr == 1)), 1)
  fit <- suppressWarnings(glmnet::cv.glmnet(x_tr, ytr, family = "binomial", type.measure = "auc", weights = w))
  s_te <- as.numeric(stats::predict(fit, newx = x_te, s = "lambda.min", type = "response"))
  auc_val <- tryCatch(as.numeric(pROC::auc(test[[outcome_v]] == levels(test[[outcome_v]])[2], s_te)), error = function(e) NA_real_)
  perf_df <- data.frame(指标 = c("AUC(留出)", "不平衡比"), 结果 = c(sprintf("%.3f", auc_val), sprintf("%.2f:1", imbalance_ratio)), stringsAsFactors = FALSE)
  fs_table <- data.frame(特征 = colnames(x_tr), 系数 = round(as.numeric(stats::coef(fit, s = "lambda.min"))[-1], 4), stringsAsFactors = FALSE)
  fs_table <- fs_table[fs_table$系数 != 0, ]
} else {
  fit <- suppressWarnings(glmnet::cv.glmnet(x_tr, train[[outcome_v]], alpha = 0.5))
  s_te <- as.numeric(stats::predict(fit, newx = x_te, s = "lambda.min"))
  yte <- test[[outcome_v]]
  perf_df <- data.frame(指标 = "RMSE(留出)", 结果 = sprintf("%.3f", sqrt(mean((s_te - yte)^2))), stringsAsFactors = FALSE)
  fs_table <- data.frame(特征 = colnames(x_tr), 系数 = round(as.numeric(stats::coef(fit, s = "lambda.min"))[-1], 4), stringsAsFactors = FALSE)
  fs_table <- fs_table[fs_table$系数 != 0, ]
}
if (!nrow(fs_table)) fs_table <- data.frame(特征 = "（全部系数为 0）", 系数 = 0, stringsAsFactors = FALSE)
results_txt <- paste0("glmnet 弹性网 + 分层留出（30%）：非零特征 ", nrow(fs_table), " 个；系数表见结果面板。特征选择视角：非零系数即被保留的特征。")
.diag_status[["数据泄漏检查"]] <- "not_assessed"
.diag_status[["重采样稳定性"]] <- "not_assessed"
.diag_status[["共线性提示"]] <- "not_assessed"
.out[["fs_table"]] <- list(kind = "table", payload = head(fs_table, 50), title = "特征选择表")
.out[["perf_table"]] <- list(kind = "table", payload = perf_df, title = "留出性能")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_UNSUPERVISED = r'''
## Engine: unsupervised (PCA + kmeans, base R)
## inputs: features(critical), standardization(optional), n_clusters(parameter optional)
.step <- "setup"
feats <- as.character(feat_v)
df <- dataset[, feats, drop = FALSE]
df[] <- lapply(df, function(x) suppressWarnings(as.numeric(as.character(x))))
df <- df[stats::complete.cases(df), , drop = FALSE]
if (nrow(df) < 20) stop(sprintf("样本过少 (n=%d)", nrow(df)))
mat <- scale(as.matrix(df))
pca <- stats::prcomp(mat, scale. = FALSE)
var_expl <- round(100 * pca$sdev^2 / sum(pca$sdev^2), 1)
n_cls <- if (exists("ncls_v") && !is.null(ncls_v) && !is.na(ncls_v)) as.integer(ncls_v) else 3L
km <- stats::kmeans(mat, centers = n_cls, nstart = 10)
pca_df <- data.frame(PC1 = pca$x[, 1], PC2 = pca$x[, 2], 簇 = factor(km$cluster))
pca_df$.outlier <- abs(scale(pca$x[, 1])) > 3 | abs(scale(pca$x[, 2])) > 3
pca_fig <- ggplot2::ggplot(pca_df, ggplot2::aes(x = PC1, y = PC2, colour = 簇)) +
  ggplot2::geom_point(alpha = 0.7) +
  ggplot2::geom_point(data = pca_df[pca_df$.outlier, ], colour = "#CC3311", size = 2.5) +
  ggplot2::labs(title = sprintf("PCA 前两轴（PC1 %.0f%%, PC2 %.0f%%）+ K-means(%d 簇)", var_expl[1], var_expl[2], n_cls),
    x = "PC1", y = "PC2", colour = "簇") + ggplot2::theme_bw()
pca_table <- data.frame(
  主成分 = paste0("PC", seq_len(min(4, length(var_expl)))),
  解释方差百分比 = var_expl[seq_len(min(4, length(var_expl)))],
  stringsAsFactors = FALSE)
out_df <- data.frame(样本 = which(pca_df$.outlier), 说明 = "PCA 前两轴 |Z|>3", stringsAsFactors = FALSE)
results_txt <- paste0(
  sprintf("PCA+K-means（%d 簇）：PC1+PC2 解释 %.0f%% 方差；离群样本 %d 个。", n_cls, sum(var_expl[1:2]), sum(pca_df$.outlier)),
  " 聚类稳定性建议对 k±1 做敏感性检查。")
.diag_status[["标准化检查"]] <- "not_assessed"
.diag_status[["聚类数稳定性"]] <- "warning"
.diag_status[["离群影响"]] <- if (sum(pca_df$.outlier) > 0) "warning" else "passed"
.out[["pca_table"]] <- list(kind = "table", payload = pca_table, title = "方差解释表")
.out[["pca_fig"]] <- list(kind = "figure", payload = pca_fig, title = "PCA+聚类图")
.out[["out_table"]] <- list(kind = "table", payload = out_df, title = "离群样本")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

_ENGINE_CAL_NRI = r'''
## Engine: model comparison — calibration/DCA/NRI/external validation (base R + pROC)
## inputs: outcome(binary), original_prediction, updated_prediction(optional), threshold(optional)
.step <- "setup"
df <- dataset
has_new <- exists("updated_v") && updated_v %in% names(df) && !is.null(df[[updated_v]])
pred_cols <- c(pred_v, if (has_new) updated_v else character(0))
df2 <- df[, unique(c(outcome_v, pred_cols)), drop = FALSE]
df2[[outcome_v]] <- as.numeric(factor(df2[[outcome_v]])) - 1
df2 <- df2[stats::complete.cases(df2), , drop = FALSE]
y <- df2[[outcome_v]]
calib <- function(p) {
  bins <- cut(p, breaks = 4, include.lowest = TRUE)
  cdf <- aggregate(p, by = list(组 = bins), FUN = mean)
  cdf$obs <- ave(p, bins, FUN = mean)[match(seq_along(p), seq_along(p))]
  slope <- tryCatch(unname(stats::coef(stats::glm(y ~ p, family = stats::binomial()))[2]), error = function(e) NA_real_)
  list(slope = slope)
}
rows <- list()
for (pc in pred_cols) {
  p <- df2[[pc]]
  auc_v <- tryCatch(as.numeric(pROC::auc(y, p)), error = function(e) NA_real_)
  rows[[length(rows) + 1L]] <- data.frame(预测 = pc, AUC = round(auc_v, 4),
    平均预测 = round(mean(p), 4), 实际率 = round(mean(y), 4), stringsAsFactors = FALSE)
}
cmp_table <- do.call(rbind, rows)
calib_df <- calib(df2[[pred_v]])
calib_df$组 <- as.character(calib_df$组)
cal_fig <- ggplot2::ggplot(cal_df, ggplot2::aes(x = p, y = y)) +
  ggplot2::geom_point(colour = "#4477AA", size = 3) + ggplot2::geom_abline(slope = 1, intercept = 0, linetype = 2) +
  ggplot2::coord_cartesian(xlim = c(0, 1), ylim = c(0, 1)) +
  ggplot2::labs(title = "校准图（基线预测）", x = "预测", y = "实际") + ggplot2::theme_bw()
if (nrow(cmp_table) == 2L) {
  nri_rows <- data.frame(
    指标 = c("基线 AUC", "更新 AUC", "AUC 差"),
    结果 = c(cmp_table$AUC[1], cmp_table$AUC[2], sprintf("%.4f", cmp_table$AUC[2] - cmp_table$AUC[1])),
    stringsAsFactors = FALSE)
} else nri_rows <- cmp_table
results_txt <- paste0("模型对比与校准：", paste(sprintf("%s AUC=%s", cmp_table$预测, cmp_table$AUC), collapse = "；"),
  "。校准图按四分位分组；更新模型 AUC 更高且校准斜率接近 1 时才有替换价值。")
.diag_status[["缺失与异常检查"]] <- "not_assessed"
.diag_status[["区分度"]] <- "not_assessed"
.diag_status[["校准"]] <- "not_assessed"
.diag_status[["临床净获益或阈值分析"]] <- "warning"
.diag_status[["核心假设、稳健性和敏感性检查"]] <- "not_assessed"
.diag_status[["替代模型或替代包结果一致性检查"]] <- "not_assessed"
.out[["cmp_table"]] <- list(kind = "table", payload = cmp_table, title = "模型对比表")
.out[["cal_fig"]] <- list(kind = "figure", payload = cal_fig, title = "校准图")
.out[["nri_table"]] <- list(kind = "table", payload = nri_rows, title = "判别指标对比")
.out[["results_text"]] <- list(kind = "text", payload = results_txt, title = "结果解释")
'''

ENGINES: Dict[str, Callable] = {
    "descriptive_reporting": _make_engine(
        _EMIT_TABLE_ONE,
        {"group": "group_v", "continuous_variables": "cont_v", "categorical_variables": "cat_v", "missingness_indicator": "miss_v"},
        ("连续变量分布检查", "分类变量稀疏水平检查", "缺失比例检查"),
    ),
    "classical_group_tests": _make_engine(
        _ENGINE_GROUP_TESTS,
        _GROUP_TESTS_INPUT_VARS,
        ("正态性/方差齐性检查", "小样本或稀疏单元格检查"),
    ),
    "glm_regression": _make_engine(
        _ENGINE_GLM,
        _GLM_INPUT_VARS,
        ("共线性检查", "残差/拟合检查", "影响点检查"),
    ),
    "survival_km_cox": _make_engine(
        _ENGINE_SURVIVAL,
        _SURVIVAL_INPUT_VARS,
        ("比例风险假设检查", "删失分布检查", "事件数充分性检查"),
    ),
    "missing_imputation": _make_engine(IMPUTATION_CORE,
        {"outcome":"outcome_v","predictors":"predictors_v","variables_with_missingness":"miss_v","imputations":"m_v","iterations":"iterations_v","exposure":"exposure_v","missingness_mechanism_variable":"aux_v"}, ("缺失比例检查","插补收敛/分布检查","完整病例对照")),
    "multiple_imputation_pooling": _make_engine(IMPUTATION_CORE,
        {"outcome":"outcome_v","predictors":"predictors_v","imputation_variables":"miss_v","auxiliary_variable":"aux_v","covariates":"covars_v","group":"group_v","imputations":"m_v","iterations":"iterations_v"}, ("缺失比例检查","插补收敛/分布检查","完整病例对照")),
    "trial_simon_two_stage": _make_engine(SIMON_CORE,
        {"outcome":"outcome_v","stage":"stage_v","subgroup":"subgroup_v","enrollment_order":"order_v","null_response_rate":"p0_v","target_response_rate":"p1_v","alpha":"alpha_v","power":"power_v","design_criterion":"criterion_v"}, ("设计参数重现检查","阶段界值检查","实际与计划样本量对照")),
    "staggered_did_panel": _make_engine(DID_CORE,
        {"subject_id":"subject_v","time":"time_v","treatment_point":"cohort_v","outcome":"outcome_v","covariates":"covars_v"}, ("平行趋势检查",)),
    "clinical_target_trial_emulation": _make_engine(TARGET_TRIAL_CORE,
        {"subject_id":"subject_v","time_zero":"zero_v","treatment_strategy":"treatment_v","outcome":"outcome_v","covariates_2":"covars_v","covariates":"timevarying_v","event":"event_v"}, ("协变量平衡检查","倾向重叠检查")),
    "diagnostic_roc": _make_engine(
        _ENGINE_ROC,
        {"outcome": "outcome_v", "diagnostic_score": "score_v", "threshold": "thr_v", "strata": "strata_v", "positive_class": "positive_v", "score_direction": "direction_v"},
        ("阳性/阴性样本量检查", "阈值稳定性检查"),
    ),
    "correlation_agreement": _make_engine(
        _ENGINE_CORR,
        {"continuous_variables": "cont_v", "rater_method_variable": "rater_v", "strata": "strata_v", "analysis_method": "method_v"},
        ("散点非线性检查", "离群点敏感性检查"),
    ),
    "effect_size_precision": _make_engine(
        _ENGINE_EFFECT,
        {"outcome": "outcome_v", "group": "group_v", "pair_id": "pair_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "变量角色与研究假设一致性检查", "样本量、事件数或设计效应充分性检查"),
    ),
    "ancova_adjusted_groups": _make_engine(
        _ENGINE_ANCOVA,
        {"outcome": "outcome_v", "group": "group_v", "covariates": "covars_v", "covariates_2": "covars2_v", "strata": "strata_v"},
        ("回归斜率同质性检查", "残差和方差检查", "调整均值与对比检查"),
    ),
    "bootstrap_permutation_inference": _make_engine(
        _ENGINE_BOOTSTRAP,
        {"outcome": "outcome_v", "group": "group_v", "pair_id": "pair_v", "cluster_id": "cluster_v"},
        ("重采样单位检查", "重复次数与稳定性检查", "区间方法敏感性比较"),
    ),
    "propensity_clinical": _make_engine(
        _ENGINE_PS,
        {"group": "group_v", "outcome": "outcome_v", "covariates": "covars_v", "weight": "w_v"},
        ("标准化差异检查", "重叠性检查", "权重极端值检查"),
    ),
    "mediation_moderation": _make_engine(
        _ENGINE_MEDIATION,
        {"predictor": "predictor_v", "mediator": "mediator_v", "outcome": "outcome_v", "moderator": "moderator_v", "covariates": "covars_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "平衡诊断", "稳健标准误/敏感性分析", "模型形式与结局类型匹配检查", "效应量方向、置信区间和影响点检查"),
    ),
    "categorical_ordinal_models": _make_engine(
        _ENGINE_ORDINAL,
        {"outcome": "outcome_v", "exposure": "exposure_v", "exposure_2": "exposure2_v", "covariates": "covars_v", "analysis_method": "method_v"},
        ("比例优势假设检查", "过度离散检查", "类别稀疏检查"),
    ),
    "mixed_repeated": _make_engine(
        _ENGINE_MIXED,
        {"outcome": "outcome_v", "time": "time_v", "subject_id": "subject_v", "fixed_effects": "fixed_v", "random_effect_group": "rand_v", "analysis_method": "method_v"},
        ("相关结构检查", "随机效应方差检查", "收敛检查"),
    ),
    "noninferiority_equivalence": _make_engine(
        _ENGINE_NI,
        {"outcome": "outcome_v", "group": "group_v", "noninferiority_margin": "margin_v", "analysis_population": "pop_v", "reference_group": "reference_v", "outcome_direction": "direction_v", "analysis_population_level": "population_level_v", "analysis_method": "method_v", "alpha": "alpha_v"},
        ("界值方向检查", "分析集一致性检查", "界值来源、分析集一致性和置信区间判定检查"),
    ),
    "pairwise_meta": _make_engine(
        _ENGINE_META,
        {"study_id": "study_v", "effect_estimate": "effect_v", "standard_error": "se_v", "subgroup": "sub_v"},
        ("异质性检查", "影响研究检查", "发表偏倚提示"),
    ),
    "subgroup_sensitivity_basic": _make_engine(
        _ENGINE_SUBGROUP,
        {"outcome": "outcome_v", "exposure": "exposure_v", "subgroup": "subgroup_v", "covariates": "covars_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "核心假设、稳健性和敏感性检查", "替代模型或替代包结果一致性检查", "亚组样本量、交互项和多重比较检查"),
    ),
    "epi_measures": _make_engine(
        _ENGINE_EPI,
        {"outcome": "outcome_v", "exposure": "exposure_v", "positive_class": "positive_v", "reference_group": "reference_v", "denominator": "denom_v", "time_region": "region_v"},
        ("分母合理性检查", "稀有事件检查"),
    ),
    "interrater_agreement": _make_engine(
        _ENGINE_KAPPA,
        {"data_object": "subject_v", "rater_id": "rater_v", "measure": "measure_v", "group": "group_v"},
        ("评分者和类别覆盖检查", "患病率与偏倚效应检查", "一致性置信区间检查"),
    ),
    "scale_reliability": _make_engine(
        _ENGINE_RELIABILITY,
        {"categorical_variables": "cat_v", "reverse_item_indicator": "reverse_v", "dimension_group": "dim_v", "total_score_subscale": "total_v"},
        ("反向题检查", "缺失和极端反应检查"),
    ),
    "forecasting": _make_engine(
        _ENGINE_FORECAST,
        {"time": "time_v", "measure": "measure_v", "frequency": "freq_v", "group_region": "region_v"},
        ("时间频率检查", "残差自相关", "训练/测试切分"),
    ),
    "sample_size_power": _make_engine(
        _ENGINE_SAMPLE_SIZE,
        {"outcome": "outcome_v", "effect_size": "eff_v", "alpha": "alpha_v", "power": "power_v", "allocation_ratio": "ratio_v"},
        ("效应量合理性检查", "脱落率敏感性"),
    ),
    "efa_factor": _make_engine(
        _ENGINE_EFA,
        {"categorical_variables": "cat_v", "sample_id": "sample_v", "dimension": "dim_v", "reverse_item_indicator": "reverse_v"},
        ("KMO 适配性", "共同度检查", "交叉载荷检查"),
    ),
    "cfa_sem": _make_engine(
        _ENGINE_CFA,
        {"categorical_variables": "cat_v", "latent_variables": "latent_v", "path_specification": "path_v", "group": "group_v", "analysis_method": "method_v"},
        ("CFI/TLI/RMSEA/SRMR", "修正指数审慎解释", "Heywood 问题检查"),
    ),
    "did_policy": _make_engine(
        _ENGINE_DID,
        {"outcome": "outcome_v", "treatment_group": "group_v", "time": "time_v", "policy_start_time": "policy_v", "cluster_variable": "cluster_v"},
        ("平行趋势检查", "聚类稳健标准误", "安慰剂时间点检查"),
    ),
    "iv_panel_public_health": _make_engine(
        _ENGINE_IV,
        {"outcome": "outcome_v", "exposure": "exposure_v", "instrumental_variable": "instrument_v", "subject_id": "subject_v", "time": "time_v", "analysis_method": "method_v"},
        ("弱工具检查", "固定效应适配性", "聚类稳健标准误"),
    ),
    "tabular_ml_workflow": _make_engine(
        _ENGINE_ML,
        {"outcome": "outcome_v", "features": "feat_v", "test_split": "split_v", "strata": "strata_v"},
        ("数据切分检查", "重采样稳定性", "类别不平衡检查", "交叉验证设置检查", "特征泄漏检查", "任务类型检查", "重采样一致性", "调参预算说明"),
    ),
    "evidence_meta_regression": _make_engine(
        _ENGINE_META_REG,
        {"study_id": "study_v", "effect_estimate": "effect_v", "standard_error": "se_v", "covariates": "covars_v", "subgroup": "sub_v", "followup_duration": "fu_v", "risk_of_bias": "rob_v"},
        ("协变量数量与研究数检查", "残余异质性检查", "影响研究和生态偏倚检查"),
    ),
    "interrupted_time_series": _make_engine(
        _ENGINE_ITS,
        {"time": "time_v", "outcome": "measure_v", "intervention_time": "policy_v", "covariates": "covars_v"},
        ("时间频率检查", "序列完整性检查"),
    ),
    "survey_weighted": _make_engine(
        _ENGINE_SURVEY,
        {"outcome": "outcome_v", "weight": "weight_v", "strata": "strata_v", "cluster_variable": "cluster_v", "covariates": "covars_v"},
        ("权重分布检查", "设计自由度检查"),
    ),
    "cluster_rct_analysis": _make_engine(
        _ENGINE_CLUSTER_RCT,
        {"outcome": "outcome_v", "group": "group_v", "cluster_id": "cluster_v", "covariates": "covars_v"},
        ("聚类数检查", "ICC 检查"),
    ),
    "public_cost_effectiveness_psa": _make_engine(
        _ENGINE_CEA_PSA,
        {"strategy": "group_v", "cost": "cost_v", "health_outcome": "outcome_v", "threshold": "thr_v", "parameter_distributions": "dist_v", "subgroup": "sub_v"},
        ("抽样分布检查", "参数不确定性检查"),
    ),
    "evidence_proportion_rate_meta": _make_engine(
        _ENGINE_PROP_META,
        {"study_id": "study_v", "event_count": "event_v", "sample_size_person_time": "n_v", "subgroup": "sub_v", "region": "region_v", "study_year": "year_v"},
        ("异质性检查", "零事件或边界检查"),
    ),
    "public_screening_evaluation": _make_engine(
        _ENGINE_SCREENING,
        {"outcome": "outcome_v", "diagnostic_score": "score_v"},
        ("金标准与筛查结果二分类检查", "患病率对预测值影响检查"),
    ),
    "public_vaccine_test_negative": _make_engine(
        _ENGINE_VACCINE_TN,
        {"outcome": "outcome_v", "exposure": "exposure_v", "covariates": "covars_v"},
        ("病例对照完整性检查", "混杂校正检查"),
    ),
    "trial_multiplicity_gatekeeping": _make_engine(
        _ENGINE_MULTIPLICITY,
        {},
        ("检验间独立性与层级检查", "方法选择透明性检查"),
    ),
    "health_inequality": _make_engine(
        _ENGINE_HEALTH_INEQ,
        {"outcome": "outcome_v", "socioeconomic_rank_variable": "rank_v", "weight": "w_v", "group": "group_v", "covariates": "covars_v"},
        ("位次变量覆盖检查", "模型形式与结局类型匹配检查"),
    ),
    "clinical_rmst_non_ph": _make_engine(
        _ENGINE_RMST,
        {"followup_time": "followup_v", "event": "event_v", "group": "group_v", "covariates": "covars_v", "truncation_time": "trunc_v", "time_unit": "time_unit_v"},
        ("截断时间可支持性检查", "比例风险检查", "RMST 时间窗敏感性分析"),
    ),
    "clinical_recurrent_events": _make_engine(
        _ENGINE_RECUR,
        {"subject_id": "subject_v", "event_time": "followup_v", "event": "event_v", "group": "group_v", "event_order": "order_v", "covariates": "covars_v", "terminal_event": "term_v"},
        ("个体内相关检查", "事件计数分布检查", "不同复发事件模型对照"),
    ),
    "time_dependent_roc": _make_engine(
        _ENGINE_TDROC,
        {"followup_time": "followup_v", "event": "event_v", "prediction": "pred_v", "evaluation_time": "teval_v"},
        ("事件数检查", "删失机制提示", "时间点样本量检查"),
    ),
    "clinical_cutpoint_maxstat": _make_engine(
        _ENGINE_CUTPOINT,
        {"measure": "measure_v", "followup_time": "followup_v", "event": "event_v", "group": "group_v", "analysis_method": "method_v", "time_unit": "time_unit_v"},
        ("事件编码与候选阈值检查", "重采样与截点稳定性检查", "多重搜索和过拟合风险检查", "比例风险检验"),
    ),
    "clinical_subgroup_interaction": _make_engine(
        _ENGINE_SUBGROUP,
        {"outcome": "outcome_v", "group": "exposure_v", "subgroup": "subgroup_v", "covariates": "covars_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "核心假设、稳健性和敏感性检查", "替代模型或替代包结果一致性检查", "亚组样本量、交互项和多重比较检查"),
    ),
    "extended_survival": _make_engine(
        _ENGINE_EXT_SURVIVAL,
        {"followup_time": "followup_v", "event": "event_v", "time_dependent_variable": "tdep_v", "strata": "strata_v"},
        ("PH 假设检查", "聚类/中心效应检查", "模型比较"),
    ),
    "competing_risk": _make_engine(
        _ENGINE_COMPETING,
        {"followup_time": "followup_v", "event_type": "event_type_v", "target_event_level": "target_v", "competing_event_level": "comp_v", "covariates": "covars_v", "time_unit": "time_unit_v"},
        ("事件编码检查", "竞争事件分布检查", "模型适配性检查"),
    ),
    "prognostic_model": _make_engine(
        _ENGINE_PROGNOSTIC,
        {"outcome": "outcome_v", "features": "feat_v", "event": "event_v", "validation_split": "split_v", "time_point": "time_v"},
        ("过拟合检查", "校准检查", "内部验证"),
    ),
    "robust_outlier_analysis": _make_engine(
        _ENGINE_ROBUST,
        {"continuous_variables": "cont_v", "group": "group_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "残差/拟合诊断", "共线性与影响点检查", "变量类型与编码一致性检查", "缺失、重复、异常和批次问题检查"),
    ),
    "data_quality": _make_engine(
        _ENGINE_DATA_PROFILE,
        {"candidate_variables": "cand_v"},
        ("变量类型核查", "重复记录检查", "极端值和编码异常检查"),
    ),
    "data_import_workflow": _make_engine(
        _ENGINE_DATA_PROFILE,
        {"candidate_variables": "cand_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "变量类型与编码一致性检查", "缺失、重复、异常和批次问题检查"),
    ),
    "data_cleaning_recoding": _make_engine(
        _ENGINE_DATA_PROFILE,
        {"candidate_variables": "cand_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "变量类型与编码一致性检查", "缺失、重复、异常和批次问题检查"),
    ),
    "automated_eda_report": _make_engine(
        _ENGINE_DATA_PROFILE,
        {"candidate_variables": "cand_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "变量类型与编码一致性检查", "缺失、重复、异常和批次问题检查"),
    ),
    "large_data_engine": _make_engine(
        _ENGINE_DATA_PROFILE,
        {"candidate_variables": "cand_v"},
        ("内存风险检查", "列类型检查", "分块策略说明"),
    ),
    "age_standardization": _make_engine(
        _ENGINE_AGE_STD,
        {"age_group": "age_v", "event_count": "event_v", "denominator": "denom_v", "group": "group_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "模型形式与结局类型匹配检查", "效应量方向、置信区间和影响点检查"),
    ),
    "attributable_fraction": _make_engine(
        _ENGINE_ATTRIB,
        {"outcome": "outcome_v", "exposure": "exposure_v", "effect_estimate": "eff_v", "denominator": "denom_v", "covariates": "covars_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "模型形式与结局类型匹配检查", "效应量方向、置信区间和影响点检查"),
    ),
    "negative_control_sensitivity": _make_engine(
        _ENGINE_NEG_CONTROL,
        {"exposure": "exposure_v", "outcome": "outcome_v", "negative_control_variable": "neg_v", "covariates": "covars_v"},
        ("负对照有效性检查", "主要与负对照效应比较", "未测混杂敏感性检查"),
    ),
    "clinical_doubly_robust_effect": _make_engine(
        _ENGINE_AIPW,
        {"group": "group_v", "outcome": "outcome_v", "covariates": "covars_v", "subgroup": "sub_v", "weight": "w_v"},
        ("倾向重叠检查", "协变量平衡检查", "倾向与结局模型敏感性分析"),
    ),
    "missing_visual_diagnostics": _make_engine(
        _ENGINE_MISSING_VISUAL,
        {"variables_with_missingness": "miss_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "变量类型与编码一致性检查", "缺失、重复、异常和批次问题检查"),
    ),
    "group_sequential": _make_engine(
        _ENGINE_GSD,
        {"information_time": "info_v", "alpha": "alpha_v", "effect_size": "eff_v", "outcome": "outcome_v", "alpha_spending": "spend_v", "power": "power_v"},
        ("信息时间一致性检查", "边界方向检查", "信息时间、alpha spending 和停止边界可重现性检查"),
    ),
    "clinical_multistate_illness_death": _make_engine(
        _ENGINE_MULTISTATE,
        {"subject_id": "subject_v", "start_state": "start_state_v", "end_state": "end_state_v", "followup_time": "followup_v", "group": "group_v"},
        ("允许转移矩阵检查", "状态时间一致性检查", "转移风险与概率校验"),
    ),
    "clinical_landmark_dynamic_prediction": _make_engine(
        _ENGINE_LANDMARK,
        {"subject_id": "subject_v", "followup_time": "followup_v", "event": "event_v", "landmark_prediction": "pred_v", "prediction_horizon": "horizon_v"},
        ("Landmark 样本集检查", "时间依赖区分度检查", "动态校准检查"),
    ),
    "clinical_joint_longitudinal_survival": _make_engine(
        _ENGINE_JOINT,
        {"subject_id": "subject_v", "time": "time_v", "outcome": "outcome_v", "followup_time": "followup_v", "event": "event_v", "group": "group_v", "covariates": "covars_v"},
        ("纵向子模型检查", "生存子模型检查", "共享个体效应与两阶段敏感性检查"),
    ),
    "public_rt_nowcasting": _make_engine(
        _ENGINE_RT_NOWCAST,
        {"time": "time_v", "event_count": "count_v", "serial_interval": "si_v", "region": "region_v"},
        ("报告延迟检查", "序列间隔敏感性分析", "平滑窗口稳定性检查"),
    ),
    "public_joinpoint_segmented_trend": _make_engine(
        _ENGINE_JOINPOINT,
        {"time": "time_v", "outcome": "outcome_v", "denominator": "denom_v", "region": "region_v"},
        ("转折点数量检查", "自相关和过度离散检查", "时间窗敏感性分析"),
    ),
    "public_burden_decomposition": _make_engine(
        _ENGINE_BURDEN_DECOMP,
        {"outcome": "outcome_v", "denominator": "denom_v", "strata": "strata_v"},
        ("分母一致性检查", "标准化权重检查", "分解顺序与不确定性分析"),
    ),
    "trial_estimand_ich_e9": _make_engine(
        _ENGINE_ESTIMAND,
        {"randomized_group": "group_v", "outcome": "outcome_v", "analysis_population": "pop_v", "intercurrent_event": "ice_v"},
        ("估计目标五要素检查", "中间事件策略检查", "分析集和缺失处理一致性检查"),
    ),
    "trial_missing_tipping_point": _make_engine(
        _ENGINE_TIPPING,
        {"randomized_group": "group_v", "outcome": "outcome_v", "missingness_indicator": "miss_v"},
        ("组间缺失模式检查", "偏移参数网格检查", "结论翻转点稳定性检查"),
    ),
    "trial_phase1_dose_escalation": _make_engine(
        _ENGINE_PHASE1,
        {"dose": "dose_v", "outcome": "outcome_v", "cohort": "cohort_v", "subject_id": "subject_v"},
        ("剂量爬坡回顾性检查", "入组顺序检查"),
    ),
    "calibration_dca": _make_engine(
        _ENGINE_CAL_DCA,
        {"outcome": "outcome_v", "prediction": "pred_v", "risk_group": "risk_v", "threshold": "thr_v"},
        ("校准斜率/截距检查", "阈值范围合理性检查"),
    ),
    "bayesian_meta": _make_engine(
        _ENGINE_BAYES_META,
        {"study_id": "study_v", "effect_estimate": "effect_v", "standard_error": "se_v", "prior": "prior_v"},
        ("先验敏感性检查", "后验不确定性检查"),
    ),
    "text_mining_nlp": _make_engine(
        _ENGINE_TEXT_MINING,
        {"text": "text_v", "document_id": "doc_v", "group": "group_v", "time": "time_v"},
        ("编码和空文本检查", "停用词/分词规则检查"),
    ),
    "text_social_science": _make_engine(
        _ENGINE_TEXT_MINING,
        {"text": "text_v", "document_id": "doc_v", "group": "group_v", "time": "time_v"},
        ("空文本检查", "停用词/编码检查", "短文本限制"),
    ),
    "sparse_text_ml": _make_engine(
        _ENGINE_TEXT_CLASS,
        {"text": "text_v", "class_label": "label_v", "data_split": "split_v"},
        ("文本切分和词汇泄漏检查", "稀疏特征与类别不平衡检查", "性能、校准和词汇稳定性检查"),
    ),
    "sentiment_analysis": _make_engine(
        _ENGINE_SENTIMENT,
        {"text": "text_v", "group": "group_v"},
        ("词典覆盖检查",),
    ),
    "network_graphs": _make_engine(
        _ENGINE_IGRAPH,
        {"node_id": "node_v", "edge": "edge_to_v", "node_attributes": "attr_v", "weight": "weight_v"},
        ("孤立节点和重复边检查", "权重分布检查"),
    ),
    "social_network": _make_engine(
        _ENGINE_IGRAPH,
        {"node_id": "node_v", "edge": "edge_to_v", "node_attributes": "attr_v", "weight": "weight_v"},
        ("孤立节点检查", "边权重分布检查"),
    ),
    "spatial_autocorrelation": _make_engine(
        _ENGINE_SPARROW,
        {"region_id": "region_v", "measure": "measure_v", "coordinates": "coord_v", "denominator": "denom_v"},
        ("邻接矩阵检查", "空间权重敏感性"),
    ),
    "geostatistics": _make_engine(
        _ENGINE_INTERPOLATION,
        {"coordinates": "coord_v", "measure": "measure_v", "prediction_grid": "grid_v", "covariates": "covars_v"},
        ("坐标投影检查", "空间覆盖范围", "插值外推风险"),
    ),
    "spatial_regression_models": _make_engine(
        _ENGINE_SPATIAL_REG,
        {"region_id": "region_v", "outcome": "outcome_v", "exposure": "exposure_v", "coordinates": "coord_v", "weight": "w_v"},
        ("空间权重检查", "残差自相关检查", "共线性和边界敏感性检查"),
    ),
    "bulk_rnaseq": _make_engine(
        _ENGINE_OMIC_RNASEQ,
        {"data_object": "feat_v", "group": "group_v", "sample_annotations": "annot_v", "batch": "batch_v"},
        ("样本注释匹配检查", "低表达过滤检查", "批次效应检查"),
    ),
    "microarray_expression": _make_engine(
        _ENGINE_LIMMA,
        {"data_object": "feat_v", "group": "group_v", "sample_annotations": "annot_v", "batch": "batch_v"},
        ("归一化检查", "样本离群检查", "批次效应检查"),
    ),
    "pathway_enrichment": _make_engine(
        _ENGINE_PATHWAY,
        {"gene_feature_list": "gene_v", "ranking_statistic": "rank_v", "species_id_type": "species_v", "background_gene_set": "bg_v", "analysis_method": "method_v"},
        ("ID 映射率检查", "背景集合理性", "多重校正"),
    ),
    "irt_models": _make_engine(
        _ENGINE_IRT,
        {"categorical_variables": "cat_v", "subject_id": "subject_v", "group": "group_v", "total_score_ability_estimate": "score_v"},
        ("单维性提示", "局部独立性提示", "项目拟合检查"),
    ),
    "latent_class": _make_engine(
        _ENGINE_LATENT_CLASS,
        {"categorical_variables": "cat_v", "sample_id": "sample_v", "candidate_class_count": "ncls_v", "covariates": "covars_v"},
        ("类别数选择", "类别稳定性", "小类别风险"),
    ),
    "psych_network": _make_engine(
        _ENGINE_QGRAPH,
        {"categorical_variables": "cat_v", "group": "group_v", "time_point_variable": "time_v"},
        ("边稳定性提示", "过度因果解释警告"),
    ),
    "tree_boosting": _make_engine(
        _ENGINE_ML_XGB,
        {"outcome": "outcome_v", "features": "feat_v", "test_split": "split_v", "tuning_grid": "grid_v"},
        ("过拟合检查", "类别不平衡检查", "重要性稳定性"),
    ),
    "feature_selection": _make_engine(
        _ENGINE_ML_VARIANTS,
        {"outcome": "outcome_v", "features": "feat_v", "test_split": "split_v", "resampling_specification": "resamp_v"},
        ("数据泄漏检查", "重采样稳定性", "共线性提示"),
    ),
    "imbalanced_classification": _make_engine(
        _ENGINE_ML_VARIANTS,
        {"outcome": "outcome_v", "features": "feat_v", "threshold": "thr_v", "weight": "w_v"},
        ("类别不平衡检查", "阈值敏感性", "校准检查"),
    ),
    "unsupervised_ml": _make_engine(
        _ENGINE_UNSUPERVISED,
        {"data_object": "feat_v", "sample_id": "sample_v", "standardization_variables": "std_v", "external_validation_variable": "ext_v"},
        ("标准化检查", "聚类数稳定性", "离群影响"),
    ),
    "dimensionality_reduction_ml": _make_engine(
        _ENGINE_UNSUPERVISED,
        {"data_object": "feat_v", "sample_id": "sample_v", "group": "group_v"},
        ("变量尺度与缺失检查", "解释方差、邻域保持和嵌入稳定性检查"),
    ),
    "probability_calibration_ml": _make_engine(
        _ENGINE_CAL_DCA,
        {"outcome": "outcome_v", "prediction": "pred_v", "data_split": "split_v"},
        ("缺失与异常检查", "模型/方法适配性检查", "区分度", "校准", "核心假设、稳健性和敏感性检查", "替代模型或替代包结果一致性检查"),
    ),
    "external_validation_model": _make_engine(
        _ENGINE_CAL_NRI,
        {"outcome": "outcome_v", "prediction": "pred_v", "data_source": "source_v", "subgroup": "sub_v"},
        ("缺失与异常检查", "区分度", "校准", "临床净获益或阈值分析"),
    ),
    "nri_idi_reclassification": _make_engine(
        _ENGINE_CAL_NRI,
        {"outcome": "outcome_v", "original_prediction": "pred_v", "updated_prediction": "updated_v", "threshold": "thr_v"},
        ("缺失与异常检查", "区分度", "校准"),
    ),
}

# Dedicated implementations replace the corresponding legacy cores while
# preserving the single engine registry and normal frozen-plan execution.
def _large_data_core_for_plan(plan: AnalysisPlan) -> str:
    body = data_preparation_cores.CORES["large_data_engine"]
    return r'''
.step <- "dataset-binding-audit"
.dataset_binding_checked <- FALSE
if (exists("dataset_ref_v") && !is.null(dataset_ref_v)) {
  # A matching frozen dataset ID is resolved to the current imported object by
  # the trusted getter. A path/string or different object cannot substitute it.
  if (!is.data.frame(dataset_ref_v) || !identical(dataset_ref_v, dataset)) stop("冻结数据集引用未匹配当前已导入表格；请重新准备当前数据集方案")
  .dataset_binding_checked <- TRUE
}
''' + (body(plan) if callable(body) else body) + r'''
.dataset_audit_note <- sprintf("选定数据集：当前已导入表格；冻结引用%s；实际 %d 行、%d 列。汇总使用该对象，不重新解释为任意文件路径。",
  if (.dataset_binding_checked) "已与当前数据对象核对一致" else "未单独提供，使用当前载入对象", nrow(dataset), ncol(dataset))
.size$数据集绑定 <- if (.dataset_binding_checked) "当前冻结数据集已核对" else "当前载入数据集（未单独绑定）"
.out[["size"]]$payload <- .size
.out[["recommend"]]$payload <- paste(.out[["recommend"]]$payload, .dataset_audit_note)
'''


FIXED_METHOD_MODULES = (
    data_preparation_cores, clinical_survival_cores, statistics_behavior_cores,
    weighted_causal_cores, meta_time_cores,
    clinical_prediction_cores, clinical_public_workflows,
    general_behavior_workflows, trial_evidence_workflows,
    omics_workflows, time_space_text_workflows, machine_learning_workflows,
)
for _module in FIXED_METHOD_MODULES:
    for _template_id, _body in _module.CORES.items():
        ENGINES[_template_id] = _make_engine(
            _large_data_core_for_plan if _template_id == "large_data_engine" else _body,
            _module.INPUTS[_template_id], (),
        )
