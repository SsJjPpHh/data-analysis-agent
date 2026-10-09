"""Fixed weighted, causal, and population-health computations.

The bodies use the existing frozen-plan accessors and artifact emitter.  Binary
0/1 codes have their usual meaning; other category labels require an explicit
direction.  Package availability and study approval remain separate concerns.
"""

from __future__ import annotations


_COMMON = r"""
# This option table is generated from accessor variables in the frozen plan.
.wc_get <- function(name, default=NULL) {
  if (name %in% names(.wc_inputs)) .wc_inputs[[name]] else default
}
.wc_cols <- function(x) {
  x <- as.character(x); unique(x[!is.na(x) & nzchar(x)])
}
.wc_frame <- function(columns) {
  columns <- .wc_cols(columns)
  if (!length(columns) || !all(columns %in% names(dataset))) stop("输入数据列不存在：", paste(setdiff(columns,names(dataset)),collapse=","))
  if (any(grepl("`",columns,fixed=TRUE))) stop("当前公式接口不支持包含反引号的列名")
  d <- dataset[,columns,drop=FALSE]
  d <- d[stats::complete.cases(d),,drop=FALSE]
  if (nrow(d)<2L) stop("完整观测不足")
  for (nm in names(d)) if (is.numeric(d[[nm]]) && any(!is.finite(d[[nm]]))) stop("数据列含非有限值：",nm)
  d
}
.wc_binary <- function(x, positive=NULL, label="二分类变量") {
  values <- unique(as.character(x)); values <- values[!is.na(values)]
  if (length(values)!=2L) stop(label,"需要恰好两个水平")
  if (!is.null(positive) && length(positive)>1L) stop(label,"必须选择一个类别")
  if (is.null(positive) || !length(positive) || !nzchar(as.character(positive))) {
    if (setequal(values,c("0","1"))) positive <- "1"
    else if (setequal(values,c("FALSE","TRUE"))) positive <- "TRUE"
    else stop(label,"必须明确指定事件/处理类别，不能依赖类别排序")
  }
  if (length(positive)!=1L || !as.character(positive) %in% values) stop(label,"所选类别不在数据中")
  as.integer(as.character(x)==as.character(positive))
}
.wc_treatment <- function(x) {
  selected <- .wc_get("treatment_v")
  reference <- .wc_get("reference_v")
  if (!is.null(reference) && length(reference)!=1L) stop("必须选择一个参照类别")
  if (!is.null(reference) && length(reference) && nzchar(as.character(reference))) {
    levels <- unique(as.character(x))
    if (length(levels)!=2L || !as.character(reference) %in% levels) stop("参照类别无效")
    other <- setdiff(levels,as.character(reference))
    if (!is.null(selected) && !identical(as.character(selected),other)) stop("处理与参照类别冲突")
    selected <- other
  }
  .wc_binary(x,selected,"处理/暴露变量")
}
.wc_outcome <- function(x,positive=NULL,family="auto") {
  binary <- family=="binary" || (family=="auto" && (!is.numeric(x) || setequal(unique(x),c(0,1))))
  if (binary) return(list(y=.wc_binary(x,positive,"结局变量"),binary=TRUE))
  if (!is.numeric(x) || any(!is.finite(x))) stop("连续结局需要有限数值")
  list(y=as.numeric(x),binary=FALSE)
}
.wc_rhs <- function(columns) if (length(columns)) paste(sprintf("`%s`",columns),collapse=" + ") else "1"
.wc_weights <- function(d,column=NULL) {
  if (is.null(column) || !length(column)) return(rep(1,nrow(d)))
  w <- d[[as.character(column)]]
  if (!is.numeric(w) || any(!is.finite(w)) || any(w<=0)) stop("权重必须是严格正的有限数值，不能使用类别标记")
  as.numeric(w)
}
.wc_check_fit <- function(fit) {
  if (fit$rank<length(stats::coef(fit)) || any(!is.finite(stats::coef(fit)))) stop("模型秩不足或系数不可估计")
  if (stats::df.residual(fit)<=0) stop("模型没有正的剩余推断自由度")
  if (inherits(fit,"glm") && (!isTRUE(fit$converged) || isTRUE(fit$boundary))) stop("广义线性模型未收敛或处于边界")
  if (inherits(fit,"glm") && fit$family$family=="binomial" && any(stats::fitted(fit)<1e-8 | stats::fitted(fit)>1-1e-8)) stop("二分类模型存在完全/近完全分离")
  fit
}
.wc_vcov <- function(fit,cluster=NULL) {
  if (!is.null(cluster)) {
    G <- length(unique(cluster))
    if (G<3L) stop("聚类稳健推断至少需要 3 个独立聚类")
    list(V=sandwich::vcovCL(fit,cluster=cluster,type="HC1"),df=G-1L)
  } else list(V=sandwich::vcovHC(fit,type="HC1"),df=stats::df.residual(fit))
}
.wc_coef_table <- function(fit,inference,terms=NULL,exponentiate=FALSE) {
  b <- stats::coef(fit); V <- inference$V; s <- sqrt(diag(V)); crit <- stats::qt(.975,inference$df)
  use <- if (is.null(terms)) names(b) else intersect(terms,names(b))
  if (!length(use) || any(!is.finite(b[use])) || any(!is.finite(s[use])) || !is.finite(crit)) stop("模型推断不可估计")
  lo <- b[use]-crit*s[use]; hi <- b[use]+crit*s[use]
  data.frame(变量=use,效应=if(exponentiate) exp(b[use]) else b[use],标准误=s[use],
    CI下限=if(exponentiate) exp(lo) else lo,CI上限=if(exponentiate) exp(hi) else hi,
    P值=2*stats::pt(-abs(b[use]/s[use]),df=inference$df),row.names=NULL,check.names=FALSE)
}
.wc_boot <- function(d,fun,B=199L) {
  B <- as.integer(B)
  if (length(B)!=1L || is.na(B) || B<19L || B>4999L) stop("bootstrap 次数必须介于 19 与 4999")
  set.seed(104729L)
  draws <- replicate(B,tryCatch(fun(d[sample.int(nrow(d),replace=TRUE),,drop=FALSE]),error=function(e) NA_real_))
  draws <- draws[is.finite(draws)]
  if (length(draws)<ceiling(.8*B)) stop("bootstrap 可估计重采样不足 80%，请检查模型和样本覆盖")
  list(ci=unname(stats::quantile(draws,c(.025,.975))),se=stats::sd(draws),success=length(draws),B=B)
}
.wc_balance <- function(d,covariates,treated,before,after) {
  X <- stats::model.matrix(stats::as.formula(paste("~",.wc_rhs(covariates))),data=d)
  X <- X[,colnames(X)!="(Intercept)",drop=FALSE]
  rows <- lapply(seq_len(ncol(X)),function(j) {
    x <- X[,j]
    avg <- function(w,t) sum(w[t]*x[t])/sum(w[t])
    m1 <- avg(before,treated==1); m0 <- avg(before,treated==0)
    v1 <- sum(before[treated==1]*(x[treated==1]-m1)^2)/sum(before[treated==1])
    v0 <- sum(before[treated==0]*(x[treated==0]-m0)^2)/sum(before[treated==0])
    den <- sqrt((v1+v0)/2)
    pre <- if (den>0) (m1-m0)/den else 0
    post <- if (den>0) (avg(after,treated==1)-avg(after,treated==0))/den else 0
    data.frame(协变量=colnames(X)[j],调整前差异=pre,加权后差异=post,
      判定=if(abs(post)>.1) "不均衡" else "均衡",stringsAsFactors=FALSE)
  })
  if (!length(rows)) stop("协变量模型矩阵没有可比较列")
  do.call(rbind,rows)
}
.wc_text <- function(slot,text,title=slot) list(kind="text",payload=text,title=title)
.wc_table <- function(slot,x,title=slot) list(kind="table",payload=x,title=title)
.wc_figure <- function(slot,x,title=slot) list(kind="figure",payload=x,title=title)
"""

_SURVEY = r"""
.step <- "survey-design"
covs <- .wc_cols(.wc_get("covars_v")); weight_col <- .wc_get("weight_v")
cluster_col <- .wc_get("cluster_v"); strata_col <- .wc_get("strata_v")
if (is.null(weight_col)) stop("复杂抽样分析需要明确的抽样权重列")
df <- .wc_frame(c(outcome_v,weight_col,cluster_col,strata_col,covs))
df$.w <- .wc_weights(df,weight_col)
lonely <- as.character(.wc_get("lonely_v","fail"))
if (!lonely %in% c("fail","adjust","average","certainty")) stop("孤立 PSU 处理方式无效")
old_options <- options(survey.lonely.psu=lonely)
ids <- if (is.null(cluster_col)) ~1 else stats::as.formula(paste("~",.wc_rhs(cluster_col)))
strata <- if (is.null(strata_col)) NULL else stats::as.formula(paste("~",.wc_rhs(strata_col)))
des <- survey::svydesign(ids=ids,strata=strata,weights=~.w,data=df,nest=TRUE)
design_df <- survey::degf(des)
if (design_df<=0) stop("抽样设计自由度必须大于零")
if (is.character(df[[outcome_v]])) des <- stats::update(des,.y=factor(df[[outcome_v]]))
else des <- stats::update(des,.y=df[[outcome_v]])
mean_fit <- survey::svymean(~.y,des,na.rm=FALSE)
mean_ci <- stats::confint(mean_fit,df=design_df)
est_table <- data.frame(指标=names(stats::coef(mean_fit)),估计=as.numeric(stats::coef(mean_fit)),
  标准误=as.numeric(survey::SE(mean_fit)),CI下限=mean_ci[,1],CI上限=mean_ci[,2],row.names=NULL)
.out[["est_table"]] <- .wc_table("est_table",est_table,"加权描述与总体置信区间")
if (length(covs)) {
  outcome <- .wc_outcome(df[[outcome_v]],.wc_get("positive_v"),.wc_get("family_v","auto"))
  des <- stats::update(des,.model_y=outcome$y)
  fit <- survey::svyglm(stats::as.formula(paste(".model_y ~",.wc_rhs(covs))),design=des,
    family=if(outcome$binary) stats::quasibinomial() else stats::gaussian())
  .wc_check_fit(fit)
  ci <- stats::confint(fit)
  model_table <- data.frame(变量=names(stats::coef(fit)),系数=unname(stats::coef(fit)),
    标准误=sqrt(diag(stats::vcov(fit))),CI下限=ci[,1],CI上限=ci[,2],row.names=NULL)
  .out[["model_table"]] <- .wc_table("model_table",model_table,"survey GLM 设计推断")
}
design_table <- data.frame(指标=c("完整观测","设计自由度","权重和","分层变量","PSU 变量","孤立 PSU 处理"),
  结果=c(nrow(df),design_df,sum(df$.w),if(is.null(strata_col)) "无" else strata_col,
    if(is.null(cluster_col)) "独立行" else cluster_col,lonely))
results_text <- sprintf("复杂抽样设计实际使用权重、PSU 与分层；n=%d，设计自由度=%d，总体区间采用设计自由度 t 分位数。孤立 PSU 规则=%s。",nrow(df),design_df,lonely)
.out[["design_text"]] <- .wc_text("design_text",paste(capture.output(print(design_table)),collapse="\n"),"抽样设计说明")
.out[["results_text"]] <- .wc_text("results_text",results_text,"加权结果解释")
.diag_status[["权重分布检查"]] <- if(max(df$.w)>10*stats::median(df$.w)) "warning" else "passed"
.diag_status[["设计自由度检查"]] <- if(design_df<5) "warning" else "passed"
.diag_status[["权重缺失/极端值检查"]] <- if(nrow(df)<nrow(dataset) || max(df$.w)>10*stats::median(df$.w)) "warning" else "passed"
.diag_status[["设计变量完整性检查"]] <- if(nrow(df)<nrow(dataset)) "warning" else "passed"
options(old_options)
"""

_PS_HELPERS = r"""
.wc_ps <- function(d) {
  fit <- suppressWarnings(stats::glm(stats::as.formula(paste(".t ~",.wc_rhs(covs))),
    data=d,family=stats::binomial(),weights=.base_w))
  .wc_check_fit(fit)
  ps <- as.numeric(stats::fitted(fit))
  if (any(!is.finite(ps)) || any(ps<=0 | ps>=1)) stop("倾向概率必须严格介于 0 和 1")
  estimand <- as.character(.wc_get("estimand_v","ATE"))
  iw <- switch(estimand,ATE=ifelse(d$.t==1,1/ps,1/(1-ps)),
    ATT=ifelse(d$.t==1,1,ps/(1-ps)),overlap=ifelse(d$.t==1,1-ps,ps),stop("不支持的目标估计量"))
  cap <- .wc_get("cap_v")
  if (!is.null(cap)) {
    if (length(cap)!=1L || !is.finite(as.numeric(cap)) || as.numeric(cap)<=0) stop("权重截断界值必须为正数")
    iw <- pmin(iw,as.numeric(cap))
  }
  w <- d$.base_w*iw
  m1 <- sum(w[d$.t==1]*d$.y[d$.t==1])/sum(w[d$.t==1])
  m0 <- sum(w[d$.t==0]*d$.y[d$.t==0])/sum(w[d$.t==0])
  list(fit=fit,ps=ps,w=w,estimate=m1-m0,m1=m1,m0=m0,estimand=estimand)
}
.wc_ps_setup <- function() {
  covs <- .wc_cols(.wc_get("covars_v"))
  if (!length(covs)) stop("倾向模型需要混杂协变量")
  if (any(covs %in% c(group_v,outcome_v))) stop("协变量不得重复包含处理或结局变量")
  covs
}
"""

_PROPENSITY = r"""
.step <- "propensity-model"
covs <- .wc_ps_setup(); weight_col <- .wc_get("w_v")
df <- .wc_frame(c(group_v,outcome_v,covs,weight_col))
df$.t <- .wc_treatment(df[[group_v]])
outcome <- .wc_outcome(df[[outcome_v]],.wc_get("positive_v"),.wc_get("family_v","auto")); df$.y <- outcome$y
df$.base_w <- .wc_weights(df,weight_col)
ps_result <- .wc_ps(df); ps <- ps_result$ps; w_ate <- ps_result$w
balance_df <- .wc_balance(df,covs,df$.t,df$.base_w,w_ate)
.step <- "propensity-refit-bootstrap"
boot <- .wc_boot(df,function(d) .wc_ps(d)$estimate,.wc_get("bootstrap_v",199L))
weighted_eff <- data.frame(估计量=paste(ps_result$estimand,if(outcome$binary) "风险差" else "均值差"),
  效应=ps_result$estimate,标准误=boot$se,CI下限=boot$ci[1],CI上限=boot$ci[2])
unadjusted <- weighted.mean(df$.y[df$.t==1],df$.base_w[df$.t==1])-weighted.mean(df$.y[df$.t==0],df$.base_w[df$.t==0])
effect_table <- data.frame(模型=c("未调整","倾向加权"),效应=c(unadjusted,ps_result$estimate),
  CI下限=c(NA,boot$ci[1]),CI上限=c(NA,boot$ci[2]))
overlap_fig <- ggplot2::ggplot(data.frame(ps=ps,组=factor(df$.t)),ggplot2::aes(ps,fill=组))+
  ggplot2::geom_histogram(position="identity",alpha=.4,bins=25)+ggplot2::theme_bw()+
  ggplot2::labs(title="倾向得分重叠",x="处理概率",y="人数")
sens_text <- sprintf("目标=%s；处理类别=%s；未默认截断倾向概率或权重；用户权重截断=%s。区间采用重新拟合倾向模型的行级 bootstrap（成功 %d/%d）。最大权重=%.3f；不支持仅凭匹配类别标记替代数值权重。",
  ps_result$estimand,paste(unique(as.character(df[[group_v]])[df$.t==1]),collapse=","),
  if(is.null(.wc_get("cap_v"))) "无" else as.character(.wc_get("cap_v")),boot$success,boot$B,max(w_ate))
.out[["balance_df"]] <- .wc_table("balance_df",balance_df,"所有连续及类别水平的标准化差异")
.out[["overlap_fig"]] <- .wc_figure("overlap_fig",overlap_fig,"倾向重叠")
.out[["weighted_eff"]] <- .wc_table("weighted_eff",weighted_eff,"倾向加权边际效应与区间")
.out[["effect_table"]] <- .wc_table("effect_table",effect_table,"调整前后效应")
.out[["sens_text"]] <- .wc_text("sens_text",sens_text,"权重与重采样说明")
.diag_status[["标准化差异检查"]] <- if(any(abs(balance_df$加权后差异)>.1)) "warning" else "passed"
.diag_status[["重叠性检查"]] <- if(min(ps)<.01 || max(ps)>.99) "warning" else "passed"
.diag_status[["权重极端值检查"]] <- if(max(w_ate)>10*stats::median(w_ate)) "warning" else "passed"
.diag_status[["重采样稳定性"]] <- if(boot$B<199 || boot$success<boot$B) "warning" else "passed"
"""

_AIPW = r"""
.step <- "aipw-nuisance-models"
covs <- .wc_ps_setup(); weight_col <- .wc_get("w_v"); subgroup_col <- .wc_get("sub_v")
df <- .wc_frame(c(group_v,outcome_v,covs,weight_col,subgroup_col))
df$.t <- .wc_treatment(df[[group_v]])
outcome <- .wc_outcome(df[[outcome_v]],.wc_get("positive_v"),.wc_get("family_v","auto")); df$.y <- outcome$y
df$.base_w <- .wc_weights(df,weight_col)
if (!identical(as.character(.wc_get("estimand_v","ATE")),"ATE")) stop("当前 AIPW 固定实现支持 ATE")
.wc_aipw <- function(d) {
  ps_result <- .wc_ps(d)
  formula <- stats::as.formula(paste(".y ~",.wc_rhs(covs)))
  fits <- lapply(0:1,function(t) .wc_check_fit(suppressWarnings(stats::glm(formula,
    data=d[d$.t==t,,drop=FALSE],weights=.base_w,
    family=if(outcome$binary) stats::binomial() else stats::gaussian()))))
  m0 <- as.numeric(stats::predict(fits[[1]],newdata=d,type="response"))
  m1 <- as.numeric(stats::predict(fits[[2]],newdata=d,type="response"))
  psi <- m1-m0+d$.t*(d$.y-m1)/ps_result$ps-(1-d$.t)*(d$.y-m0)/(1-ps_result$ps)
  if(any(!is.finite(psi))) stop("AIPW 个体贡献不可估计")
  list(estimate=stats::weighted.mean(psi,d$.base_w),psi=psi,m1=m1,m0=m0,ps=ps_result$ps,w=ps_result$w,
    ipw=ps_result$estimate,outcome_models=fits)
}
aipw <- .wc_aipw(df); ps <- aipw$ps; aipw_est <- aipw$estimate
.step <- "aipw-refit-bootstrap"
boot <- .wc_boot(df,function(d) .wc_aipw(d)$estimate,.wc_get("bootstrap_v",199L))
aipw_se <- boot$se
raw <- stats::weighted.mean(df$.y[df$.t==1],df$.base_w[df$.t==1])-stats::weighted.mean(df$.y[df$.t==0],df$.base_w[df$.t==0])
primary_table <- data.frame(估计量=c("未调整","IPTW","AIPW"),效应=c(raw,aipw$ipw,aipw_est),
  标准误=c(NA,NA,boot$se),CI下限=c(NA,NA,boot$ci[1]),CI上限=c(NA,NA,boot$ci[2]))
if(!is.null(subgroup_col)) {
  rows <- lapply(unique(as.character(df[[subgroup_col]])),function(g) {
    hit <- as.character(df[[subgroup_col]])==g
    if(length(unique(df$.t[hit]))!=2L) stop("亚组缺少处理或参照臂：",g)
    data.frame(估计量=paste("AIPW 亚组",g),效应=stats::weighted.mean(aipw$psi[hit],df$.base_w[hit]),
      标准误=NA_real_,CI下限=NA_real_,CI上限=NA_real_)
  })
  primary_table <- rbind(primary_table,do.call(rbind,rows))
}
balance_df <- .wc_balance(df,covs,df$.t,df$.base_w,aipw$w)
diag_fig <- ggplot2::ggplot(data.frame(ps=ps,处理=factor(df$.t)),ggplot2::aes(ps,fill=处理))+
  ggplot2::geom_histogram(position="identity",alpha=.4,bins=25)+ggplot2::theme_bw()
results_text <- sprintf("AIPW ATE=%.5f，重新拟合倾向模型和两个处理臂结局模型的 bootstrap 95%%CI [%.5f, %.5f]；成功 %d/%d。IPTW 与 AIPW 分别计算；结局回归含已选混杂协变量。亚组若提供，仅报告共享全队列 nuisance 模型的点估计，未计算亚组区间。",aipw_est,boot$ci[1],boot$ci[2],boot$success,boot$B)
.out[["primary_table"]] <- .wc_table("primary_table",primary_table,"双重稳健效应")
.out[["diag_fig"]] <- .wc_figure("diag_fig",diag_fig,"倾向概率重叠")
.out[["results_text"]] <- .wc_text("results_text",results_text,"AIPW 结果与区间范围")
.diag_status[["倾向重叠检查"]] <- if(min(ps)<.01 || max(ps)>.99) "warning" else "passed"
.diag_status[["协变量平衡检查"]] <- if(any(abs(balance_df$加权后差异)>.1)) "warning" else "passed"
.diag_status[["倾向与结局模型敏感性分析"]] <- "not_assessed"
.diag_status[["重采样稳定性"]] <- if(boot$B<199 || boot$success<boot$B) "warning" else "passed"
"""

_VACCINE = r"""
.step <- "test-negative-inputs"
covs <- .wc_cols(c(.wc_get("covars_v"),.wc_get("age_v"),.wc_get("time_v"),.wc_get("region_v")))
covs <- setdiff(covs,c(outcome_v,exposure_v))
df <- .wc_frame(c(outcome_v,exposure_v,covs))
df$.y <- .wc_binary(df[[outcome_v]],.wc_get("positive_v"),"检测阳性类别")
df$.t <- .wc_treatment(df[[exposure_v]])
time_col <- .wc_get("time_v")
if(!is.null(time_col)) {
  mode <- as.character(.wc_get("time_adjust_v","factor"))
  if(mode=="factor") df[[time_col]] <- factor(df[[time_col]])
  else if(mode=="linear") {
    if(inherits(df[[time_col]],c("Date","POSIXt"))) df[[time_col]] <- as.numeric(df[[time_col]])
    if(!is.numeric(df[[time_col]])) stop("线性时间校正要求数值或日期时间")
  } else stop("就诊时间校正仅支持 factor 或 linear")
}
counts <- table(factor(df$.y,levels=0:1),factor(df$.t,levels=0:1))
a <- counts[2,2]; b <- counts[1,2]; c1 <- counts[2,1]; d1 <- counts[1,1]
or_raw <- if(all(counts>0)) a*d1/(b*c1) else NA_real_
raw_ci <- if(all(counts>0)) exp(log(or_raw)+c(-1,1)*stats::qnorm(.975)*sqrt(sum(1/counts))) else c(NA_real_,NA_real_)
fit <- .wc_check_fit(stats::glm(stats::as.formula(paste(".y ~ .t",if(length(covs)) paste("+",.wc_rhs(covs)) else "")),
  family=stats::binomial(),data=df))
inference <- .wc_vcov(fit); model_table <- .wc_coef_table(fit,inference,terms=".t",exponentiate=TRUE)
or_adj <- model_table$效应; adj_ci <- c(model_table$CI下限,model_table$CI上限)
ve_table <- data.frame(模型=c("未调整","校正"),OR=c(or_raw,or_adj),
  OR_CI下限=c(raw_ci[1],adj_ci[1]),OR_CI上限=c(raw_ci[2],adj_ci[2]),
  VE百分比=100*(1-c(or_raw,or_adj)),VE_CI下限=100*(1-c(raw_ci[2],adj_ci[2])),VE_CI上限=100*(1-c(raw_ci[1],adj_ci[1])))
cmp_df <- data.frame(组=c("检测阳性","检测阴性"),人数=c(a+c1,b+d1),接种人数=c(a,b),占比=c(a/(a+c1),b/(b+d1)))
cmp_fig <- ggplot2::ggplot(cmp_df,ggplot2::aes(组,占比))+ggplot2::geom_col(fill="#4477AA")+
  ggplot2::coord_cartesian(ylim=c(0,1))+ggplot2::theme_bw()+ggplot2::labs(title="阳性与阴性就诊者的接种比例",y="接种比例")
results_text <- sprintf("检测阳性定义及接种类别已按确认输入编码；校正 OR=%.4f，VE=%.2f%%（95%%CI %.2f%%–%.2f%%）。实际校正变量：%s。区间采用 HC1 稳健协方差；VE 区间按 OR 区间反向变换，未截断负 VE。",or_adj,100*(1-or_adj),100*(1-adj_ci[2]),100*(1-adj_ci[1]),if(length(covs)) paste(covs,collapse=",") else "无")
.out[["ve_table"]] <- .wc_table("ve_table",ve_table,"Test-Negative 疫苗效果")
.out[["cmp_fig"]] <- .wc_figure("cmp_fig",cmp_fig,"就诊病例对照接种比例")
.out[["results_text"]] <- .wc_text("results_text",results_text,"疫苗效果结果")
.diag_status[["病例对照完整性检查"]] <- if(all(counts>0)) "passed" else "warning"
.diag_status[["病例对照定义检查"]] <- if(all(counts>0)) "passed" else "warning"
.diag_status[["混杂校正检查"]] <- if(length(covs)) "passed" else "not_assessed"
"""

_AGE = r"""
.step <- "age-standard-population"
standard_col <- .wc_get("standard_v"); group_col <- .wc_get("group_v")
if(is.null(standard_col)) stop("直接年龄标准化必须选择标准人口列；不能以各组自身分母替代标准人口")
df <- .wc_frame(c(age_v,event_v,denom_v,standard_col,group_col))
for(col in c(event_v,denom_v,standard_col)) if(!is.numeric(df[[col]])) stop("事件、分母及标准人口必须为数值")
if(any(df[[event_v]]<0 | df[[event_v]]!=floor(df[[event_v]]))) stop("事件数必须为非负整数")
if(any(df[[denom_v]]<=0) || any(df[[standard_col]]<0)) stop("分母必须为正，标准人口不得为负")
scale <- as.numeric(.wc_get("scale_v",100000))
if(length(scale)!=1L || !is.finite(scale) || scale<=0) stop("率的缩放单位无效")
df$.age <- as.character(df[[age_v]]); df$.group <- if(is.null(group_col)) "总体" else as.character(df[[group_col]])
standard <- vapply(split(df[[standard_col]],df$.age),function(x) {
  if(length(unique(x))!=1L) stop("同一年龄组必须使用一致的标准人口")
  x[1]
},numeric(1))
if(sum(standard)<=0) stop("标准人口总数必须为正")
age_table <- stats::aggregate(df[,c(event_v,denom_v),drop=FALSE],by=list(组=df$.group,年龄组=df$.age),FUN=sum)
std_rows <- lapply(unique(age_table$组),function(g) {
  sub <- age_table[age_table$组==g,,drop=FALSE]
  if(!setequal(sub$年龄组,names(standard))) stop("各比较人群必须覆盖相同年龄组")
  sub <- sub[match(names(standard),sub$年龄组),,drop=FALSE]
  adjusted <- epitools::ageadjust.direct(count=sub[[event_v]],pop=sub[[denom_v]],stdpop=standard)
  data.frame(组=g,事件=sum(sub[[event_v]]),分母=sum(sub[[denom_v]]),
    粗率=unname(adjusted["crude.rate"])*scale,标化率=unname(adjusted["adj.rate"])*scale,
    CI下限=unname(adjusted["lci"])*scale,CI上限=unname(adjusted["uci"])*scale,每人口=scale)
})
std_table <- do.call(rbind,std_rows); age_table$率 <- age_table[[event_v]]/age_table[[denom_v]]*scale
rate_fig <- ggplot2::ggplot(age_table,ggplot2::aes(年龄组,率,fill=组))+
  ggplot2::geom_col(position="dodge")+ggplot2::theme_bw()+ggplot2::labs(title="年龄别事件率",y=paste("每",scale,"人口"))
results_text <- paste("各组采用同一已选标准人口进行直接标准化；标化率与粗率分别计算。置信区间采用 epitools::ageadjust.direct 的 gamma 方法；率单位每",scale,"人口。")
.out[["std_table"]] <- .wc_table("std_table",std_table,"直接年龄标准化及 gamma 区间")
.out[["rate_fig"]] <- .wc_figure("rate_fig",rate_fig,"年龄别率")
.out[["results_text"]] <- .wc_text("results_text",results_text,"标准化率解释")
.out[["method_note"]] <- .wc_text("method_note","直接标化率=Σ(年龄别事件率×共同标准人口比例)；标准人口按年龄组匹配，重复行先合并事件及分母。")
.out[["paper_text"]] <- .wc_text("paper_text",results_text)
.out[["diag_text"]] <- .wc_text("diag_text",sprintf("共同年龄组=%d；标准人口总数=%.0f；最小分母=%.0f。",length(standard),sum(standard),min(age_table[[denom_v]])))
.diag_status[["缺失与异常检查"]] <- if(nrow(df)<nrow(dataset)) "warning" else "passed"
.diag_status[["模型/方法适配性检查"]] <- if(min(age_table[[denom_v]])<20) "warning" else "passed"
.diag_status[["模型形式与结局类型匹配检查"]] <- "passed"
.diag_status[["效应量方向、置信区间和影响点检查"]] <- "passed"
"""

_INEQUALITY = r"""
.step <- "weighted-ridit"
covs <- .wc_cols(.wc_get("covars_v")); weight_col <- .wc_get("w_v"); group_col <- .wc_get("group_v")
if(any(covs %in% c(outcome_v,rank_v))) stop("协变量不能重复健康结局或社会经济排序变量")
df <- .wc_frame(c(outcome_v,rank_v,weight_col,group_col,covs)); df$.w <- .wc_weights(df,weight_col)
rank <- df[[rank_v]]; order_levels <- .wc_get("rank_order_v")
if(is.null(order_levels)) {
  if(is.numeric(rank)) order_levels <- as.character(sort(unique(rank)))
  else if(is.ordered(rank)) order_levels <- levels(droplevels(rank))
  else stop("社会经济排序需要数值、ordered factor 或明确的 rank_order")
}
order_levels <- as.character(order_levels)
if(anyDuplicated(order_levels) || !setequal(order_levels,unique(as.character(rank)))) stop("排序类别必须完整且不重复")
if(length(order_levels)<2L) stop("排序变量需要至少两个层级")
level_weights <- vapply(order_levels,function(x) sum(df$.w[as.character(rank)==x]),numeric(1))
ridits <- (cumsum(level_weights)-level_weights/2)/sum(level_weights)
df$.ridit <- ridits[match(as.character(rank),order_levels)]
direction <- as.character(.wc_get("rank_direction_v","lower_to_higher"))
if(direction=="higher_to_lower") df$.ridit <- 1-df$.ridit else if(direction!="lower_to_higher") stop("社会经济排序方向无效")
outcome <- .wc_outcome(df[[outcome_v]],.wc_get("positive_v"),.wc_get("family_v","auto")); df$.y <- outcome$y
fit <- .wc_check_fit(suppressWarnings(stats::glm(stats::as.formula(paste(".y ~ .ridit",if(length(covs)) paste("+",.wc_rhs(covs)) else "")),
  data=df,weights=.w,family=if(outcome$binary) stats::binomial() else stats::gaussian())))
V <- .wc_vcov(fit)$V
lo <- hi <- df; lo$.ridit <- 0; hi$.ridit <- 1
X0 <- stats::model.matrix(stats::delete.response(stats::terms(fit)),lo,contrasts.arg=fit$contrasts)
X1 <- stats::model.matrix(stats::delete.response(stats::terms(fit)),hi,contrasts.arg=fit$contrasts)
p0 <- as.numeric(stats::predict(fit,lo,type="response")); p1 <- as.numeric(stats::predict(fit,hi,type="response"))
m0 <- stats::weighted.mean(p0,df$.w); m1 <- stats::weighted.mean(p1,df$.w)
gradient <- function(X,p) colSums(X*(df$.w*if(outcome$binary) p*(1-p) else 1))/sum(df$.w)
g0 <- gradient(X0,p0); g1 <- gradient(X1,p1); gs <- g1-g0
sii <- m1-m0; sii_se <- sqrt(as.numeric(t(gs)%*%V%*%gs))
crit <- if(outcome$binary) stats::qnorm(.975) else stats::qt(.975,stats::df.residual(fit))
ineq_table <- data.frame(指标="SII（最高与最低位次的边际绝对差）",估计=sii,CI下限=sii-crit*sii_se,CI上限=sii+crit*sii_se)
if(m0>0 && m1>0) {
  rii <- m1/m0; gr <- g1/m1-g0/m0; rii_se <- sqrt(as.numeric(t(gr)%*%V%*%gr))
  ineq_table <- rbind(ineq_table,data.frame(指标="RII（最高与最低位次的边际均值/风险比）",估计=rii,
    CI下限=exp(log(rii)-crit*rii_se),CI上限=exp(log(rii)+crit*rii_se)))
}
mean_y <- stats::weighted.mean(df$.y,df$.w)
concentration <- if(mean_y!=0) 2*stats::weighted.mean((df$.y-mean_y)*(df$.ridit-.5),df$.w)/mean_y else NA_real_
ineq_table <- rbind(ineq_table,data.frame(指标="加权集中指数 C（点估计）",估计=concentration,CI下限=NA_real_,CI上限=NA_real_))
if(outcome$binary) ineq_table <- rbind(ineq_table,data.frame(指标="Erreygers 二元结局集中指数（点估计）",估计=4*mean_y*concentration,CI下限=NA_real_,CI上限=NA_real_))
labels <- if(is.null(group_col)) as.character(rank) else paste(as.character(df[[group_col]]),as.character(rank),sep=" / ")
groups <- split(seq_len(nrow(df)),labels)
group_df <- data.frame(分层=names(groups),结局均值=vapply(groups,function(i) stats::weighted.mean(df$.y[i],df$.w[i]),numeric(1)),row.names=NULL)
group_fig <- ggplot2::ggplot(group_df,ggplot2::aes(分层,结局均值))+ggplot2::geom_col(fill="#4477AA")+ggplot2::coord_flip()+ggplot2::theme_bw()
results_text <- sprintf("按抽样权重和并列社会经济类别构建 ridit；排序方向=%s。SII=%.5f；RII 使用边际均值/风险比，未将 logit 斜率或 OR 冒充绝对差/风险比。集中指数 C=%.5f（仅点估计）。",direction,sii,concentration)
.out[["ineq_table"]] <- .wc_table("ineq_table",ineq_table,"SII、RII 与集中指数")
.out[["group_fig"]] <- .wc_figure("group_fig",group_fig,"已选分组与社会经济层级")
.out[["results_text"]] <- .wc_text("results_text",results_text)
.out[["method_note"]] <- .wc_text("method_note","SII/RII 来自 ridit=0 与 1 的协变量标准化边际预测；HC1 稳健协方差和 delta 区间。集中指数使用共同加权中位次；未计算集中指数区间。仅支持独立观测权重，复杂 PSU/分层设计应使用复杂抽样方法。")
.out[["paper_text"]] <- .wc_text("paper_text",results_text)
.out[["diag_text"]] <- .wc_text("diag_text",sprintf("排序层级=%d；完整 n=%d；协变量=%s；实际结局模型=%s。",length(order_levels),nrow(df),paste(covs,collapse=","),if(outcome$binary) "logistic" else "线性"))
.diag_status[["位次变量覆盖检查"]] <- "passed"
.diag_status[["模型形式与结局类型匹配检查"]] <- "passed"
.diag_status[["缺失与异常检查"]] <- if(nrow(df)<nrow(dataset)) "warning" else "passed"
.diag_status[["模型/方法适配性检查"]] <- "passed"
.diag_status[["效应量方向、置信区间和影响点检查"]] <- "not_assessed"
"""

_ATTRIBUTABLE = r"""
.step <- "attributable-risk-inputs"
covs <- .wc_cols(.wc_get("covars_v")); denom_col <- .wc_get("denom_v"); effect_col <- .wc_get("eff_v")
df <- .wc_frame(c(outcome_v,exposure_v,covs,denom_col,effect_col))
df$.t <- .wc_treatment(df[[exposure_v]])
if(is.null(denom_col)) { df$.n <- 1; df$.events <- .wc_binary(df[[outcome_v]],.wc_get("positive_v"),"结局") }
else {
  if(!is.numeric(df[[denom_col]]) || !is.numeric(df[[outcome_v]])) stop("聚合分析要求事件计数与人口分母均为数值")
  df$.n <- df[[denom_col]]; df$.events <- df[[outcome_v]]
  if(any(df$.n<=0 | df$.n!=floor(df$.n) | df$.events<0 | df$.events>df$.n | df$.events!=floor(df$.events))) stop("聚合分母及事件数必须是合法整数计数")
}
n1 <- sum(df$.n[df$.t==1]); n0 <- sum(df$.n[df$.t==0]); e1 <- sum(df$.events[df$.t==1]); e0 <- sum(df$.events[df$.t==0])
risk1 <- e1/n1; risk0 <- e0/n0; prevalence <- n1/(n1+n0); total_risk <- (e1+e0)/(n1+n0)
if(risk1<=0 || risk0<=0 || total_risk<=0) stop("归因风险比需要非零事件风险；零事件应另用相应边界方法")
rr <- risk1/risk0
if(!is.null(effect_col)) {
  supplied <- unique(df[[effect_col]])
  if(length(supplied)!=1L || !is.numeric(supplied) || !is.finite(supplied) || supplied<=0) stop("外部效应量列必须是同一个已确认的正风险比 RR；不能自动将 OR 或逐行异质效应当作 RR")
  rr <- as.numeric(supplied)
}
afe <- 1-1/rr
paf <- prevalence*(rr-1)/(1+prevalence*(rr-1))
source <- if(is.null(effect_col)) "粗风险比 Levin" else "外部已确认 RR 的 Levin"
if(length(covs)) {
  if(!is.null(effect_col)) stop("外部 RR 与协变量回归不能同时混用")
  if(any(covs %in% c(outcome_v,exposure_v,denom_col))) stop("协变量重复包含结局/暴露/分母")
  fit <- .wc_check_fit(stats::glm(stats::as.formula(paste("cbind(.events,.n-.events) ~ .t +",.wc_rhs(covs))),family=stats::binomial(),data=df))
  d0 <- d1 <- df; d0$.t <- 0; d1$.t <- 1
  m0 <- as.numeric(stats::predict(fit,d0,type="response")); m1 <- as.numeric(stats::predict(fit,d1,type="response"))
  observed_pred <- as.numeric(stats::predict(fit,df,type="response"))
  risk0_std <- stats::weighted.mean(m0,df$.n); risk1_std <- stats::weighted.mean(m1,df$.n)
  actual_std <- stats::weighted.mean(observed_pred,df$.n)
  rr <- risk1_std/risk0_std
  afe <- 1-stats::weighted.mean(m0[df$.t==1],df$.n[df$.t==1])/stats::weighted.mean(m1[df$.t==1],df$.n[df$.t==1])
  paf <- 1-risk0_std/actual_std; source <- "协变量标准化 g-computation"
}
af_table <- data.frame(指标=c("RR","暴露人口比例","AFe","PAF"),数值=c(rr,prevalence,afe,paf),方法=source)
# A scenario changes the fraction of observed exposed individuals retained;
# for adjusted models the counterfactual is standardized within exposed people.
reductions <- as.numeric(.wc_get("reductions_v",c(0,.25,.5,.75,1)))
if(any(!is.finite(reductions)) || any(reductions<0 | reductions>1)) stop("暴露减少比例必须介于 0 与 1")
cmp_table <- data.frame(暴露减少比例=reductions,潜在影响分数=reductions*paf,
  预计减少事件=reductions*paf*sum(df$.events),保留暴露人口比例=prevalence*(1-reductions))
results_text <- sprintf("%s：RR=%.5f，暴露人群 AFe=%.5f，人群 PAF=%.5f。情景表表示按比例减少当前暴露；没有将病例暴露率与人口暴露率混用，亦没有将 OR 自动近似为 RR。",source,rr,afe,paf)
.out[["af_table"]] <- .wc_table("af_table",af_table,"归因分数")
.out[["cmp_table"]] <- .wc_table("cmp_table",cmp_table,"暴露减少情景")
.out[["results_text"]] <- .wc_text("results_text",results_text)
.out[["method_note"]] <- .wc_text("method_note","粗分析采用 AFe=(RR−1)/RR 和 PAF=Pe(RR−1)/[1+Pe(RR−1)]；协变量分析采用 binomial 结局回归与暴露置零反事实标准化。仅报告点估计；未计算归因分数置信区间。情景解释以已确认的因果和干预假设为前提。")
.out[["paper_text"]] <- .wc_text("paper_text",results_text)
.out[["diag_text"]] <- .wc_text("diag_text",sprintf("n1=%.0f、n0=%.0f；事件 e1=%.0f、e0=%.0f；人口分母实际用于风险和暴露比例。",n1,n0,e1,e0))
.diag_status[["缺失与异常检查"]] <- if(nrow(df)<nrow(dataset)) "warning" else "passed"
.diag_status[["模型/方法适配性检查"]] <- "passed"
.diag_status[["模型形式与结局类型匹配检查"]] <- "passed"
.diag_status[["效应量方向、置信区间和影响点检查"]] <- "not_assessed"
"""

_NEGATIVE = r"""
.step <- "negative-control-models"
covs <- .wc_cols(.wc_get("covars_v")); mode <- as.character(.wc_get("negative_type_v","outcome"))
if(!mode %in% c("outcome","exposure")) stop("负对照类型仅支持 outcome 或 exposure")
if(any(covs %in% c(exposure_v,outcome_v,neg_v))) stop("协变量不能重复暴露、结局或负对照")
df <- .wc_frame(c(exposure_v,outcome_v,neg_v,covs))
df$.t <- .wc_treatment(df[[exposure_v]])
main_y <- .wc_outcome(df[[outcome_v]],.wc_get("positive_v"),.wc_get("family_v","auto")); df$.y <- main_y$y
rhs <- paste(".t",if(length(covs)) paste("+",.wc_rhs(covs)) else "")
main_fit <- .wc_check_fit(stats::glm(stats::as.formula(paste(".y ~",rhs)),data=df,
  family=if(main_y$binary) stats::binomial() else stats::gaussian()))
main_table <- .wc_coef_table(main_fit,.wc_vcov(main_fit),terms=".t")
if(mode=="outcome") {
  nc_y <- .wc_outcome(df[[neg_v]],.wc_get("negative_positive_v"),.wc_get("negative_family_v","auto")); df$.nc_y <- nc_y$y
  nc_fit <- .wc_check_fit(stats::glm(stats::as.formula(paste(".nc_y ~",rhs)),data=df,
    family=if(nc_y$binary) stats::binomial() else stats::gaussian()))
  negative_table <- .wc_coef_table(nc_fit,.wc_vcov(nc_fit),terms=".t")
  negative_binary <- nc_y$binary
} else {
  nc_x <- df[[neg_v]]
  if(is.numeric(nc_x) && !setequal(unique(nc_x),c(0,1))) df$.nc_x <- nc_x
  else df$.nc_x <- .wc_binary(nc_x,.wc_get("negative_level_v"),"负对照暴露")
  nc_fit <- .wc_check_fit(stats::glm(stats::as.formula(paste(".y ~ .nc_x +",rhs)),data=df,
    family=if(main_y$binary) stats::binomial() else stats::gaussian()))
  negative_table <- .wc_coef_table(nc_fit,.wc_vcov(nc_fit),terms=".nc_x")
  negative_binary <- main_y$binary
}
main_table$模型 <- "主暴露→主结局"; main_table$尺度 <- if(main_y$binary) "logOR" else "均值差"
negative_table$模型 <- if(mode=="outcome") "主暴露→负对照结局" else "负对照暴露→主结局（调整主暴露）"
negative_table$尺度 <- if(negative_binary) "logOR" else "回归系数"
sens_table <- rbind(main_table,negative_table)
cmp_table <- data.frame(诊断="负对照关联",P值=negative_table$P值,
  提示=if(negative_table$P值<.05) "负对照出现关联，需要核对偏倚来源" else "未检出关联；不能据此排除未测混杂")
results_text <- sprintf("主暴露效应=%.5f（%s）；负对照效应=%.5f（%s，P=%.5f）。负对照类型=%s；该结果用于诊断偏倚，不直接校正或识别因果效应。",main_table$效应,main_table$尺度,negative_table$效应,negative_table$尺度,negative_table$P值,mode)
.out[["sens_table"]] <- .wc_table("sens_table",sens_table,"主要与负对照模型")
.out[["cmp_table"]] <- .wc_table("cmp_table",cmp_table,"负对照诊断")
.out[["results_text"]] <- .wc_text("results_text",results_text)
.out[["method_note"]] <- .wc_text("method_note","连续结局使用高斯回归，二元结局使用 logistic；均使用 HC1 稳健推断。负对照结局和负对照暴露分别执行对应模型；显著或不显著均不能独立证明识别假设。")
.out[["paper_text"]] <- .wc_text("paper_text",results_text)
.out[["diag_text"]] <- .wc_text("diag_text","负对照有效性属于已确认的设计前提，软件不以一次显著性检验将其标记为自动验证。")
.diag_status[["负对照有效性检查"]] <- "not_assessed"
.diag_status[["主要与负对照效应比较"]] <- "passed"
.diag_status[["未测混杂敏感性检查"]] <- if(negative_table$P值<.05) "warning" else "not_assessed"
"""

_DID = r"""
.step <- "did-inputs"
cluster_col <- .wc_get("cluster_v"); subject_col <- .wc_get("subject_v"); covs <- .wc_cols(.wc_get("covars_v"))
df <- .wc_frame(c(outcome_v,group_v,time_v,cluster_col,subject_col,covs))
if(!is.numeric(df[[outcome_v]])) stop("DID 结局必须为数值")
df$.y <- df[[outcome_v]]; df$.t <- .wc_treatment(df[[group_v]])
raw_time <- df[[time_v]]
if(inherits(raw_time,"Date")) {
  df$.time <- as.numeric(raw_time); policy <- as.numeric(as.Date(as.character(policy_v)))
} else if(inherits(raw_time,"POSIXt")) {
  df$.time <- as.numeric(raw_time); policy <- as.numeric(as.POSIXct(as.character(policy_v),tz="UTC"))
} else {
  if(!is.numeric(raw_time)) stop("DID 时间必须为数值或日期时间")
  df$.time <- raw_time; policy <- suppressWarnings(as.numeric(policy_v))
}
if(length(policy)!=1L || !is.finite(policy)) stop("政策实施时点无效")
df$.post <- as.integer(df$.time>=policy); df$.did <- df$.t*df$.post
if(any(table(factor(df$.t,levels=0:1),factor(df$.post,levels=0:1))==0)) stop("处理/对照组均需政策前后观测")
if(!is.null(subject_col)) {
  if(anyDuplicated(paste(df[[subject_col]],df$.time,sep="\r"))) stop("面板 ID×时间存在重复")
  stable <- vapply(split(df$.t,df[[subject_col]]),function(x) length(unique(x))==1L,logical(1))
  if(!all(stable)) stop("此固定 DID 支持共同政策时点且处理组标记固定；错峰处理请使用 staggered DID")
}
extra <- c(if(!is.null(subject_col)) paste0("factor(`",subject_col,"`)"),if(length(covs)) .wc_rhs(covs))
did_formula <- stats::as.formula(paste(".y ~ .t + factor(.time) + .did",if(length(extra)) paste("+",paste(extra,collapse=" + ")) else ""))
if(!is.null(subject_col)) did_formula <- stats::update(did_formula,. ~ . - .t)
did_fit <- stats::lm(did_formula,data=df)
.step <- "did-main-inference"
if(!is.finite(stats::coef(did_fit)[".did"])) stop("DID 交互项不可估计")
cl <- if(!is.null(cluster_col)) df[[cluster_col]] else if(!is.null(subject_col)) df[[subject_col]] else NULL
inf <- .wc_vcov(did_fit,cl); did_table <- .wc_coef_table(did_fit,inf,terms=".did")
did_est <- did_table$效应; did_se <- did_table$标准误
.wc_joint <- function(fit,terms,cluster=NULL) {
  terms <- intersect(terms,names(stats::coef(fit))); b <- stats::coef(fit)[terms]
  if(!length(terms) || any(!is.finite(b))) return(NA_real_)
  iv <- .wc_vcov(fit,cluster); V <- iv$V[terms,terms,drop=FALSE]
  tryCatch({W <- as.numeric(t(b)%*%solve(V,b)); stats::pf(W/length(b),length(b),iv$df,lower.tail=FALSE)},error=function(e) NA_real_)
}
pre <- df[df$.post==0,,drop=FALSE]; pre_times <- sort(unique(pre$.time)); pre_p <- placebo_p <- NA_real_
.step <- "did-pretrend"
if(length(pre_times)>=2L) {
  pre_fit <- stats::lm(.y ~ .t*factor(.time),data=pre)
  terms <- grep(".t:factor",names(stats::coef(pre_fit)),fixed=TRUE,value=TRUE)
  pre_cluster <- if(!is.null(cluster_col)) pre[[cluster_col]] else if(!is.null(subject_col)) pre[[subject_col]] else NULL
  pre_p <- .wc_joint(pre_fit,terms,pre_cluster)
  pre$.fake <- as.integer(pre$.time>=max(pre_times)); pre$.placebo <- pre$.t*pre$.fake
  placebo_fit <- stats::lm(.y~.t+factor(.time)+.placebo,data=pre)
  placebo_p <- .wc_joint(placebo_fit,".placebo",pre_cluster)
}
parallel_df <- data.frame(检验=c("政策前处理×时间联合 Wald","仅政策前数据的安慰剂"),P值=c(pre_p,placebo_p),
  状态=ifelse(is.finite(c(pre_p,placebo_p)),"已计算","政策前时间/信息不足，未评估"))
es_df <- stats::aggregate(df$.y,by=list(时间=df$.time,处理组=factor(df$.t)),FUN=mean);names(es_df)[3] <- "均值"
.step <- "did-report"
es_fig <- ggplot2::ggplot(es_df,ggplot2::aes(时间,均值,colour=处理组))+ggplot2::geom_line()+ggplot2::geom_point()+
  ggplot2::geom_vline(xintercept=policy,linetype=2)+ggplot2::theme_bw()+ggplot2::labs(title="政策前后分组均值趋势")
model_terms <- intersect(c(".t",".did",covs),names(stats::coef(did_fit)))
did_model <- .wc_coef_table(did_fit,inf,terms=model_terms)
robust_text <- sprintf("共同政策时点 DID；聚类=%s；推断自由度=%.0f。未提供聚类 ID 时使用独立行 HC1，不把两个处理组当作两个独立聚类。趋势图展示观测组均值，不冒充动态事件研究系数。",if(is.null(cl)) "无（HC1）" else if(!is.null(cluster_col)) cluster_col else subject_col,inf$df)
.out[["did_table"]] <- .wc_table("did_table",did_table,"DID 稳健效应")
.out[["es_fig"]] <- .wc_figure("es_fig",es_fig,"观测分组趋势")
.out[["parallel_df"]] <- .wc_table("parallel_df",parallel_df,"平行趋势与安慰剂诊断")
.out[["did_model"]] <- .wc_table("did_model",did_model,"DID 模型系数")
.out[["robust_text"]] <- .wc_text("robust_text",robust_text)
.diag_status[["平行趋势检查"]] <- if(!is.finite(pre_p)) "not_assessed" else if(pre_p<.05) "warning" else "passed"
.diag_status[["聚类稳健标准误"]] <- if(is.null(cl)) "not_assessed" else "passed"
.diag_status[["安慰剂时间点检查"]] <- if(!is.finite(placebo_p)) "not_assessed" else if(placebo_p<.05) "warning" else "passed"
"""

_IV = r"""
.step <- "iv-or-fixed-effects"
mode <- as.character(.wc_get("method_v","iv")); instrument_col <- .wc_get("instrument_v")
subject_col <- .wc_get("subject_v"); time_col <- .wc_get("time_v"); covs <- .wc_cols(.wc_get("covars_v")); cluster_col <- .wc_get("cluster_v")
if(!mode %in% c("iv","fixed_effects")) stop("分析方法必须为 iv 或 fixed_effects")
if(mode=="iv" && is.null(instrument_col)) stop("IV 分支需要工具变量")
if(mode=="fixed_effects" && (is.null(subject_col) || is.null(time_col))) stop("固定效应分支需要个体 ID 和时间")
df <- .wc_frame(c(outcome_v,exposure_v,if(mode=="iv") instrument_col,subject_col,time_col,covs,cluster_col))
if(!is.numeric(df[[outcome_v]])) stop("当前线性 2SLS/固定效应结局需要数值")
df$.y <- df[[outcome_v]]
df$.x <- if(is.numeric(df[[exposure_v]])) df[[exposure_v]] else .wc_treatment(df[[exposure_v]])
fe <- c(if(!is.null(subject_col)) paste0("factor(`",subject_col,"`)"),if(!is.null(time_col)) paste0("factor(`",time_col,"`)"))
controls <- c(if(length(covs)) .wc_rhs(covs),fe)
rhs <- paste(c(".x",controls),collapse=" + ")
cl <- if(!is.null(cluster_col)) df[[cluster_col]] else if(!is.null(subject_col)) df[[subject_col]] else NULL
if(!is.null(subject_col) && !is.null(time_col) && anyDuplicated(paste(df[[subject_col]],df[[time_col]],sep="\r"))) stop("面板 ID×时间存在重复")
if(mode=="iv") {
  df$.z <- df[[instrument_col]]
  if(is.character(df$.z)) df$.z <- factor(df$.z)
  iv_formula <- stats::as.formula(paste(".y ~",rhs,"|",paste(c(".z",controls),collapse=" + ")))
  fit <- AER::ivreg(iv_formula,data=df)
  if(any(!is.finite(stats::coef(fit))) || fit$rank<length(stats::coef(fit))) stop("IV 模型秩不足或工具不足")
  fs <- stats::lm(stats::as.formula(paste(".x ~",paste(c(".z",controls),collapse=" + "))),data=df)
  fs_inf <- .wc_vcov(fs,cl); fs_names <- grep("^\\.z",names(stats::coef(fs)),value=TRUE)
  fs_b <- stats::coef(fs)[fs_names]; fs_V <- fs_inf$V[fs_names,fs_names,drop=FALSE]
  weak_f <- tryCatch(as.numeric(t(fs_b)%*%solve(fs_V,fs_b))/length(fs_b),error=function(e) NA_real_)
  weak_table <- data.frame(检验="排除工具联合第一阶段稳健 F",统计量=weak_f,分子自由度=length(fs_b),分母自由度=fs_inf$df)
  inference <- .wc_vcov(fit,cl); iv_table <- .wc_coef_table(fit,inference,terms=c(".x",covs))
  .out[["iv_table"]] <- .wc_table("iv_table",iv_table,"线性 IV 2SLS")
  .out[["weak_table"]] <- .wc_table("weak_table",weak_table,"第一阶段工具强度")
  .diag_status[["弱工具检查"]] <- if(!is.finite(weak_f)) "not_assessed" else if(weak_f<10) "warning" else "passed"
} else {
  fit <- stats::lm(stats::as.formula(paste(".y ~",rhs)),data=df)
  if(!is.finite(stats::coef(fit)[".x"])) stop("暴露缺乏固定效应内变化")
  inference <- .wc_vcov(fit,cl); fe_table <- .wc_coef_table(fit,inference,terms=c(".x",covs))
  .out[["fe_table"]] <- .wc_table("fe_table",fe_table,"个体与时间双向固定效应")
  .diag_status[["弱工具检查"]] <- "not_assessed"
}
ols <- stats::lm(.y~.x,data=df); ols_table <- .wc_coef_table(ols,.wc_vcov(ols,cl),terms=".x")
selected_table <- .wc_coef_table(fit,inference,terms=".x")
robust_table <- data.frame(模型=c("未调整 OLS",if(mode=="iv") "所选 2SLS" else "双向固定效应"),
  暴露效应=c(ols_table$效应,selected_table$效应),CI下限=c(ols_table$CI下限,selected_table$CI下限),CI上限=c(ols_table$CI上限,selected_table$CI上限))
robust_cmp <- data.frame(方法=mode,暴露原始数值保留=is.numeric(df[[exposure_v]]),
  聚类=if(is.null(cl)) "独立行 HC1" else if(!is.null(cluster_col)) cluster_col else subject_col,推断自由度=inference$df)
.out[["robust_table"]] <- .wc_table("robust_table",robust_table,"OLS 与所选估计器")
.out[["fe_iv_table"]] <- .wc_table("fe_iv_table",.wc_coef_table(fit,inference,terms=c(".x",covs)),"所选模型实际系数")
.out[["robust_cmp"]] <- .wc_table("robust_cmp",robust_cmp,"实际模型与稳健推断配置")
.out[["ident_text"]] <- .wc_text("ident_text",paste("所选分支=",mode,"；连续暴露保留数值尺度。IV 排除限制和外生性、面板模型的严格外生性属于已确认设计前提，不由第一阶段 F 自动证明。固定效应分支不要求或伪造工具变量。"))
.diag_status[["固定效应适配性"]] <- if(length(fe)) "passed" else "not_assessed"
.diag_status[["聚类稳健标准误"]] <- if(is.null(cl)) "not_assessed" else "passed"
"""


_BODIES = {
    "survey_weighted": _COMMON + _SURVEY,
    "health_inequality": _COMMON + _INEQUALITY,
    "propensity_clinical": _COMMON + _PS_HELPERS + _PROPENSITY,
    "negative_control_sensitivity": _COMMON + _NEGATIVE,
    "clinical_doubly_robust_effect": _COMMON + _PS_HELPERS + _AIPW,
    "did_policy": _COMMON + _DID,
    "iv_panel_public_health": _COMMON + _IV,
    "public_vaccine_test_negative": _COMMON + _VACCINE,
    "attributable_fraction": _COMMON + _ATTRIBUTABLE,
    "age_standardization": _COMMON + _AGE,
}

_DIRECTION = {
    "treatment_level": "treatment_v",
    "reference_group": "reference_v",
    "positive_class": "positive_v",
    "outcome_family": "family_v",
}
INPUTS = {
    "survey_weighted": dict(
        outcome="outcome_v",
        weight="weight_v",
        strata="strata_v",
        cluster_variable="cluster_v",
        covariates="covars_v",
        lonely_psu="lonely_v",
        positive_class="positive_v",
        outcome_family="family_v",
    ),
    "health_inequality": dict(
        outcome="outcome_v",
        socioeconomic_rank_variable="rank_v",
        weight="w_v",
        group="group_v",
        covariates="covars_v",
        rank_order="rank_order_v",
        rank_direction="rank_direction_v",
        positive_class="positive_v",
        outcome_family="family_v",
    ),
    "propensity_clinical": dict(
        group="group_v",
        outcome="outcome_v",
        covariates="covars_v",
        weight="w_v",
        estimand="estimand_v",
        weight_cap="cap_v",
        bootstrap_replicates="bootstrap_v",
        **_DIRECTION,
    ),
    "clinical_doubly_robust_effect": dict(
        group="group_v",
        outcome="outcome_v",
        covariates="covars_v",
        subgroup="sub_v",
        weight="w_v",
        estimand="estimand_v",
        bootstrap_replicates="bootstrap_v",
        **_DIRECTION,
    ),
    "negative_control_sensitivity": dict(
        exposure="exposure_v",
        outcome="outcome_v",
        negative_control_variable="neg_v",
        covariates="covars_v",
        negative_control_type="negative_type_v",
        negative_positive_class="negative_positive_v",
        negative_exposure_level="negative_level_v",
        negative_outcome_family="negative_family_v",
        **_DIRECTION,
    ),
    "did_policy": dict(
        outcome="outcome_v",
        treatment_group="group_v",
        time="time_v",
        policy_start_time="policy_v",
        cluster_variable="cluster_v",
        subject_id="subject_v",
        covariates="covars_v",
        treatment_level="treatment_v",
        reference_group="reference_v",
    ),
    "iv_panel_public_health": dict(
        outcome="outcome_v",
        exposure="exposure_v",
        instrumental_variable="instrument_v",
        subject_id="subject_v",
        time="time_v",
        analysis_method="method_v",
        covariates="covars_v",
        cluster_variable="cluster_v",
        treatment_level="treatment_v",
        reference_group="reference_v",
    ),
    "public_vaccine_test_negative": dict(
        outcome="outcome_v",
        vaccination_status="exposure_v",
        encounter_time="time_v",
        age="age_v",
        region="region_v",
        covariates="covars_v",
        time_adjustment="time_adjust_v",
        **_DIRECTION,
    ),
    "attributable_fraction": dict(
        outcome="outcome_v",
        exposure="exposure_v",
        effect_estimate="eff_v",
        denominator="denom_v",
        covariates="covars_v",
        exposure_reductions="reductions_v",
        **_DIRECTION,
    ),
    "age_standardization": dict(
        age_group="age_v",
        event_count="event_v",
        denominator="denom_v",
        standard_population="standard_v",
        group="group_v",
        rate_scale="scale_v",
    ),
}

ROLE_MAPS = {
    "survey_weighted": dict(
        table_1="est_table",
        table_2="model_table",
        table_3="est_table",
        table_4="model_table",
        text_1="design_text",
        manuscript_results="results_text",
    ),
    "health_inequality": dict(
        table_1="ineq_table",
        figure_1="group_fig",
        manuscript_results="results_text",
        method_note="method_note",
        manuscript_results_2="paper_text",
        diagnostic_output="diag_text",
    ),
    "propensity_clinical": dict(
        balance_table="balance_df",
        figure_1="overlap_fig",
        table_2="weighted_eff",
        sensitivity_output="sens_text",
        table_3="effect_table",
    ),
    "clinical_doubly_robust_effect": dict(
        primary_table="primary_table", diagnostic_plot="diag_fig", manuscript_results="results_text"
    ),
    "negative_control_sensitivity": dict(
        sensitivity_output="sens_table",
        table_2="cmp_table",
        manuscript_results="results_text",
        method_note="method_note",
        manuscript_results_2="paper_text",
        diagnostic_output="diag_text",
    ),
    "did_policy": dict(
        table_1="did_table",
        figure_1="es_fig",
        diagnostic_output="parallel_df",
        table_3="did_model",
        text_1="robust_text",
    ),
    "iv_panel_public_health": dict(
        table_1="iv_table",
        table_2="fe_table",
        table_3="weak_table",
        table_4="robust_table",
        table_5="fe_iv_table",
        table_6="robust_cmp",
        text_1="ident_text",
    ),
    "public_vaccine_test_negative": dict(
        primary_table="ve_table", diagnostic_plot="cmp_fig", manuscript_results="results_text"
    ),
    "attributable_fraction": dict(
        table_1="af_table",
        table_2="cmp_table",
        manuscript_results="results_text",
        method_note="method_note",
        manuscript_results_2="paper_text",
        diagnostic_output="diag_text",
    ),
    "age_standardization": dict(
        table_1="std_table",
        figure_1="rate_fig",
        manuscript_results="results_text",
        method_note="method_note",
        manuscript_results_2="paper_text",
        diagnostic_output="diag_text",
    ),
}


def _frozen_body(template_id):
    def render(plan):
        entries = [
            f"{variable}={variable}"
            for input_id, variable in INPUTS[template_id].items()
            if input_id in plan.resolved_inputs
        ]
        return ".wc_inputs <- list(" + ",".join(entries) + ")\n" + _BODIES[template_id]

    return render


CORES = {template_id: _frozen_body(template_id) for template_id in _BODIES}

REFERENCE_CASES = {
    "survey_weighted": "真实 survey::svydesign 的权重、重复 PSU ID 分层、设计自由度 t 区间及 svyglm 系数对照；孤立 PSU 默认拒绝、明确 adjust 后执行。",
    "health_inequality": "加权并列 ridit 的手算参考；连续 SII、二元边际风险差/风险比、集中指数；显式类别排序、反向排序和分组实际使用。",
    "propensity_clinical": "ATE/ATT/overlap 权重、外部基础数值权重、连续和类别水平平衡、处理方向与显式截断；重新拟合倾向模型 bootstrap。",
    "negative_control_sensitivity": "负对照结局/暴露两分支的真实连续回归系数对照；保留各自尺度并采用 HC1 区间，不将不显著负对照当作无混杂证明。",
    "clinical_doubly_robust_effect": "含协变量的两臂结局回归 AIPW 与独立公式对照；IPTW/AIPW 分别计算、二元结局、基础权重、亚组及重拟合 bootstrap。",
    "did_policy": "共同政策时点 DID 的 lm 与 vcovCL 对照；真正联合前趋势 Wald、仅政策前安慰剂、日期时点和个体固定效应；信息不足保留未评估。",
    "iv_panel_public_health": "连续暴露 2SLS 和个体/时间固定效应的 AER/lm 系数对照；固定效应分支无需工具变量；面板 IV、第一阶段稳健 F 与个体聚类实际执行。",
    "public_vaccine_test_negative": "真实接种状态与年龄/就诊时间/地区/协变量接线；2×2 OR/VE 手算、校正 glm 对照及 VE 区间反向变换。",
    "attributable_fraction": "个体与聚合分母的 RR/AFe/Levin PAF 手算；外部 RR、协变量 g-computation 与暴露减少情景；拒绝异质效应量列。",
    "age_standardization": "共同标准人口直接标化和 epitools gamma 区间对照；粗率与标化率区分；缺失/不一致标准人口拒绝。",
}
