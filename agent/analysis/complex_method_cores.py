"""Bounded implementations; study-design assumptions still need human review."""

IMPUTATION_CORE = r'''
.step <- "imputation-design"
predictors <- as.character(predictors_v)
extras <- unlist(lapply(c("miss_v","aux_v","covars_v","group_v","exposure_v"), function(nm) if (exists(nm)) get(nm) else NULL))
used <- unique(c(outcome_v,predictors,extras))
if (!length(predictors) || outcome_v %in% predictors) stop("需要预先指定、不含结局的回归预测变量")
dat <- dataset[,used,drop=FALSE]
dat[] <- lapply(dat,function(x) if (is.character(x)) factor(x) else x)
if (!is.numeric(dat[[outcome_v]]) || length(unique(na.omit(dat[[outcome_v]])))<3) stop("仅支持连续结局线性回归")
if (nrow(dat)<30 || any(vapply(dat,function(x) sum(!is.na(x))<5,logical(1)))) stop("有效观测不足")
if (!anyNA(dat)) stop("所选变量没有缺失，无需多重插补")
if (any(vapply(dat,function(x) is.numeric(x) && any(!is.finite(x) & !is.na(x)),logical(1)))) stop("存在无穷值")
m <- if (exists("m_v")) as.integer(m_v) else 20L
iterations <- if (exists("iterations_v")) as.integer(iterations_v) else 20L
if (m<5 || m>100 || iterations<5 || iterations>100) stop("插补次数和迭代次数须在 5–100 之间")
.step <- "mice"
imp <- mice::mice(dat,m=m,maxit=iterations,printFlag=FALSE)
form <- stats::reformulate(sprintf("`%s`",predictors),response=paste0("`",outcome_v,"`"))
fits <- lapply(seq_len(m),function(i) stats::lm(form,data=mice::complete(imp,i)))
if (any(vapply(fits,function(f) anyNA(coef(f)),logical(1)))) stop("插补后模型存在不可估计系数")
pooled <- mice::pool(mice::as.mira(fits))
pooled_table <- summary(pooled,conf.int=TRUE)
variance_table <- pooled$pooled
cc <- dat[complete.cases(dat[,c(outcome_v,predictors),drop=FALSE]),,drop=FALSE]
cc_fit <- if (nrow(cc)>length(predictors)+3) tryCatch(stats::lm(form,data=cc),error=function(e) NULL) else NULL
comparison <- data.frame(term=pooled_table$term,MI=pooled_table$estimate,
  complete_case=if (is.null(cc_fit)) NA_real_ else unname(coef(cc_fit)[pooled_table$term]),n_complete=nrow(cc))
missing_table <- data.frame(variable=names(dat),missing_rate=vapply(dat,function(x) mean(is.na(x)),numeric(1)))
missing_fig <- ggplot2::ggplot(missing_table,ggplot2::aes(x=variable,y=missing_rate)) +
  ggplot2::geom_col() + ggplot2::coord_flip() + ggplot2::theme_bw()
trace <- as.data.frame.table(imp$chainMean,responseName="mean",stringsAsFactors=FALSE)
names(trace) <- c("variable","iteration","chain","mean")
trace$iteration <- as.integer(trace$iteration)
trace <- trace[is.finite(trace$mean),,drop=FALSE]
trace_fig <- ggplot2::ggplot(trace,ggplot2::aes(x=iteration,y=mean,colour=chain,group=chain)) +
  ggplot2::geom_line() + ggplot2::facet_wrap(~variable,scales="free_y") + ggplot2::theme_bw()
convergence <- tryCatch(mice::convergence(imp),error=function(e) NULL)
if (is.null(convergence)) convergence <- data.frame(note="收敛统计不可计算；需检查链迹")
convergence$诊断项 <- "MICE 插补链收敛统计"
convergence$结果 <- "需结合轨迹人工评估，未自动判定通过"
notes <- sprintf("MICE 随机多重插补 m=%d，迭代 %d；逐份拟合预先指定线性模型，Rubin 合并并使用 Barnard–Rubin 自由度。MAR 假设无法由观测数据证实；链迹和完整病例对照不能证明无缺失偏倚，MNAR 敏感性未评估。记录事件数=%d。",m,iterations,NROW(imp$loggedEvents))
.diag_status[["缺失比例检查"]] <- if (max(missing_table$missing_rate)>.5) "warning" else "passed"
.diag_status[["插补收敛/分布检查"]] <- "not_assessed"
.diag_status[["完整病例对照"]] <- if (is.null(cc_fit) || anyNA(comparison$complete_case)) "warning" else "passed"
.out[["missing_fig"]] <- list(kind="figure",payload=missing_fig,title="缺失比例")
.out[["trace_fig"]] <- list(kind="figure",payload=trace_fig,title="插补链均值轨迹；需人工核对")
.out[["diagnostics"]] <- list(kind="table",payload=convergence,title="收敛统计；需结合轨迹解释")
.out[["pooled"]] <- list(kind="table",payload=pooled_table,title="Rubin 合并估计")
.out[["variance"]] <- list(kind="table",payload=variance_table,title="插补内外方差与缺失信息")
.out[["comparison"]] <- list(kind="table",payload=comparison,title="完整病例对照")
.out[["notes"]] <- list(kind="text",payload=notes,title="插补方法与敏感性边界")
'''

SIMON_CORE = r'''
.step <- "simon-search"
p0 <- as.numeric(p0_v); p1 <- as.numeric(p1_v)
alpha <- as.numeric(alpha_v); power <- as.numeric(power_v)
if (!(0<p0 && p0<p1 && p1<1 && 0<alpha && alpha<.5 && .5<power && power<1)) stop("无效的 Simon 设计参数")
criterion <- as.character(criterion_v)
if (!criterion %in% c("optimal","minimax")) stop("请选择 optimal 或 minimax")
designs <- clinfun::ph2simon(p0,p1,alpha,1-power,nmax=200)$out
if (is.null(designs) || !NROW(designs)) stop("n<=200 内未找到可行设计；请另行扩大搜索，不可用固定阈值代替")
design <- designs[if (criterion=="optimal") order(designs[,"EN(p0)"],designs[,"n"])[1] else order(designs[,"n"],designs[,"EN(p0)"])[1],]
n1 <- as.integer(design["n1"]); n <- as.integer(design["n"])
r1 <- as.integer(design["r1"]); r <- as.integer(design["r"])
reject <- function(p) {
 x <- seq.int(r1+1L,n1)
 sum(stats::dbinom(x,n1,p)*stats::pbinom(r-x,n-n1,p,lower.tail=FALSE))
}
actual_alpha <- reject(p0); actual_power <- reject(p1)
operating <- data.frame(p=seq(.01,.99,length.out=99))
operating$reject <- vapply(operating$p,reject,numeric(1))
fig <- ggplot2::ggplot(operating,ggplot2::aes(x=p,y=reject)) + ggplot2::geom_line() +
 ggplot2::geom_vline(xintercept=c(p0,p1),linetype=2) + ggplot2::theme_bw() +
 ggplot2::labs(x="真实响应率",y="拒绝无效假设的概率",title=paste("Simon",criterion))
result_table <- data.frame(criterion=criterion,n1=n1,r1=r1,n=n,r=r,alpha=actual_alpha,power=actual_power,
 expected_n_p0=unname(design["EN(p0)"]),early_stop_p0=unname(design["PET(p0)"]))
actual_note <- "未提供可核对的入组顺序，不作实际阶段决策。"
stage_note <- ""; subgroup_note <- ""; observed_order <- NULL
if (exists("stage_v") && length(stage_v)) {
 stage_values <- as.character(dataset[[stage_v]])
 if (anyNA(stage_values) || any(!stage_values %in% c("1","2"))) stop("实际阶段须完整编码为 1 或 2")
 stage_n1 <- sum(stage_values=="1"); stage_n2 <- sum(stage_values=="2")
 if (stage_n1>n1 || stage_n2>n-n1 || (stage_n2>0 && stage_n1!=n1)) stop("实际阶段人数与 Simon 第一/第二阶段计划不一致")
 stage_note <- sprintf("已核对阶段列：第一阶段 %d/%d，第二阶段 %d/%d。",stage_n1,n1,stage_n2,n-n1)
 observed_order <- order(stage_values)
}
if (exists("subgroup_v") && length(subgroup_v)) {
 subgroup_values <- as.character(dataset[[subgroup_v]])
 subgroup_y <- dataset[[outcome_v]]
 if (anyNA(subgroup_values) || any(!nzchar(trimws(subgroup_values)))) stop("亚组标签须完整且非空")
 if (!is.numeric(subgroup_y) || anyNA(subgroup_y) || any(!subgroup_y %in% c(0,1))) stop("响应需明确编码 0/1，且无缺失")
 subgroup_summary <- vapply(split(subgroup_y,subgroup_values),function(z) sprintf("n=%d，响应=%d，响应率=%.4f",length(z),sum(z),mean(z)),character(1))
 subgroup_note <- paste("亚组仅作观察响应描述（不改变总体 Simon 设计或停止规则）：",paste(names(subgroup_summary),subgroup_summary,collapse="；"))
}
if (exists("order_v") && length(order_v)) {
 if (anyNA(dataset[[order_v]]) || anyDuplicated(dataset[[order_v]])) stop("入组顺序须完整且唯一")
 observed_order <- order(dataset[[order_v]])
 if (exists("stage_values") && !identical(stage_values[observed_order],ifelse(seq_along(observed_order)<=n1,"1","2"))) stop("入组顺序与实际阶段标记不一致")
}
if (!is.null(observed_order)) {
 y <- dataset[[outcome_v]][observed_order]
 if (!is.numeric(y) || anyNA(y) || any(!y %in% c(0,1))) stop("响应需明确编码 0/1，且无缺失")
 if (length(y)>n) stop("实际入组超出设计样本量，需重新核对方案")
 actual_note <- if (length(y)<n1) "第一阶段尚未达到计划人数。" else if (sum(y[seq_len(n1)])<=r1) "第一阶段达到无效停止规则。" else if (length(y)<n) "第一阶段可继续，第二阶段尚未完成。" else if (sum(y)>r) "最终响应数超过界值，按预先指定规则拒绝无效假设。" else "最终响应数未超过界值，不拒绝无效假设。"
 if (length(y)>n1 && sum(y[seq_len(n1)])<=r1) actual_note <- paste(actual_note,"实际仍继续入组，存在方案偏离。")
}
notes <- sprintf("单臂二项响应 Simon %s 设计；p0=%s、p1=%s。第一阶段 n1=%d，响应数 <=%d 停止；否则补至总 n=%d，最终响应数 >%d 才拒绝无效假设。实际 alpha=%.6f，power=%.6f。%s %s %s",criterion,p0,p1,n1,r1,n,r,actual_alpha,actual_power,actual_note,stage_note,subgroup_note)
.diag_status[["设计参数重现检查"]] <- if (actual_alpha<=alpha+1e-8 && actual_power>=power-1e-8) "passed" else "failed"
.diag_status[["阶段界值检查"]] <- if (0<=r1 && r1<n1 && n1<n && r<n) "passed" else "failed"
.diag_status[["实际与计划样本量对照"]] <- if (is.null(observed_order)) "not_assessed" else if (length(y)>n1 && sum(y[seq_len(n1)])<=r1) "warning" else "passed"
.out[["primary"]] <- list(kind="table",payload=result_table,title="设计与精确操作特征")
.out[["figure"]] <- list(kind="figure",payload=fig,title="精确拒绝概率")
.out[["notes"]] <- list(kind="text",payload=notes,title="阶段规则与实际数据边界")
'''

DID_CORE = r'''
.step <- "panel-preflight"
covars <- if (exists("covars_v")) as.character(covars_v) else character(0)
df <- dataset[,unique(c(subject_v,time_v,cohort_v,outcome_v,covars)),drop=FALSE]
if (anyNA(df)) stop("此实现要求完整的平衡面板；请先明确缺失处理")
if (!all(vapply(df[,c(time_v,cohort_v,outcome_v),drop=FALSE],is.numeric,logical(1)))) stop("时间、首次处理期和结局须为数值")
if (anyDuplicated(df[,c(subject_v,time_v)])) stop("个体-时间组合必须唯一")
if (any(table(df[[subject_v]])!=length(unique(df[[time_v]])))) stop("面板不平衡，不允许静默丢弃个体")
if (any(vapply(split(df[[cohort_v]],df[[subject_v]]),function(x) length(unique(x))!=1L,logical(1)))) stop("首次处理期须在个体内保持一致")
if (!any(df[[cohort_v]]==0) || any(df[[cohort_v]]<0) || any(df[[cohort_v]]>0 & !df[[cohort_v]] %in% df[[time_v]])) stop("必须存在从未处理组(0)，其他首次处理期须在观测时间中")
if (any(df[[cohort_v]]>0 & df[[cohort_v]]<=min(df[[time_v]]))) stop("每个处理队列必须有处理前观测")
for (v in covars) if (any(vapply(split(df[[v]],df[[subject_v]]),function(x) length(unique(x))>1L,logical(1)))) stop("当前范围仅支持基线不随时间变化的协变量")
df$.id <- as.integer(factor(df[[subject_v]]))
df$.time <- df[[time_v]]; df$.cohort <- df[[cohort_v]]; df$.y <- df[[outcome_v]]
xform <- if (length(covars)) stats::reformulate(sprintf("`%s`",covars)) else ~1
fit_did <- function(control) did::att_gt(yname=".y",tname=".time",idname=".id",gname=".cohort",
  xformla=xform,data=df,panel=TRUE,allow_unbalanced_panel=FALSE,control_group=control,
  est_method="dr",bstrap=FALSE,cband=FALSE,print_details=FALSE)
.step <- "group-time-att"
fit <- fit_did("nevertreated")
agg <- did::aggte(fit,type="simple",bstrap=FALSE,cband=FALSE)
dynamic <- did::aggte(fit,type="dynamic",bstrap=FALSE,cband=FALSE)
att_table <- data.frame(group=fit$group,time=fit$t,ATT=fit$att,SE=fit$se)
if (any(!is.finite(att_table$ATT)) || !is.finite(agg$overall.att)) stop("存在不可估计的 ATT 单元；需要检查支持和队列规模")
event_table <- data.frame(event_time=dynamic$egt,ATT=dynamic$att.egt,SE=dynamic$se.egt)
alt <- tryCatch(did::aggte(fit_did("notyettreated"),type="simple",bstrap=FALSE,cband=FALSE),error=function(e) NULL)
sensitivity <- data.frame(control=c("nevertreated","notyettreated"),ATT=c(agg$overall.att,if (is.null(alt)) NA_real_ else alt$overall.att),SE=c(agg$overall.se,if (is.null(alt)) NA_real_ else alt$overall.se))
fig <- ggplot2::ggplot(event_table,ggplot2::aes(x=event_time,y=ATT)) + ggplot2::geom_point() +
 ggplot2::geom_errorbar(ggplot2::aes(ymin=ATT-1.96*SE,ymax=ATT+1.96*SE),width=.1) +
 ggplot2::geom_hline(yintercept=0,linetype=2) + ggplot2::theme_bw() + ggplot2::labs(title="动态 ATT：点态 95% 区间（非同时置信带）")
notes <- sprintf("Callaway–Sant'Anna group-time ATT，DR 估计、从未处理组对照；整体 ATT=%.4f，SE=%.4f。按个体面板结构推断；无额外地区聚类。平行趋势预检 P=%s；不显著不能证明平行趋势。无提前反应假设与政策赋值机制仍需研究者确认。",agg$overall.att,agg$overall.se,format(fit$Wpval))
.diag_status[["平行趋势检查"]] <- if (length(fit$Wpval)==1 && is.finite(fit$Wpval)) if (fit$Wpval<.05) "warning" else "passed" else "not_assessed"
.out[["att"]] <- list(kind="table",payload=att_table,title="分组-时间 ATT")
.out[["sensitivity"]] <- list(kind="table",payload=sensitivity,title="对照组定义敏感性")
.out[["figure"]] <- list(kind="figure",payload=fig,title="动态效应")
.out[["notes"]] <- list(kind="text",payload=notes,title="方法和识别假设")
'''

TARGET_TRIAL_CORE = r'''
.step <- "target-trial-protocol"
if (exists("timevarying_v") && length(timevarying_v)) stop("时间变化协变量不在当前基线策略实现范围；请选择基线协变量或使用自由分析")
covars <- if (exists("covars_v")) as.character(covars_v) else character(0)
if (!length(covars)) stop("需预先指定治疗前混杂变量")
if (exists("event_v") && !is.null(event_v)) stop("当前范围不支持生存/删失终点")
used <- unique(c(subject_v,zero_v,treatment_v,outcome_v,covars))
df <- dataset[,used,drop=FALSE]
if (anyNA(df) || anyDuplicated(df[[subject_v]])) stop("仅支持无缺失、单人单行的基线策略比较")
if (!is.numeric(df[[outcome_v]]) || !is.numeric(df[[treatment_v]]) ||
    any(!df[[outcome_v]] %in% c(0,1)) || any(!df[[treatment_v]] %in% c(0,1))) stop("策略和固定时窗结局须明确编码 0/1")
if (min(table(factor(df[[treatment_v]],levels=c(0,1))))<20) stop("每策略至少需要 20 人")
df$.a <- df[[treatment_v]]; df$.y <- df[[outcome_v]]
ps_formula <- stats::reformulate(sprintf("`%s`",covars),response=".a")
estimate <- function(d) {
 psfit <- stats::glm(ps_formula,data=d,family=stats::binomial())
 ps <- stats::fitted(psfit)
 if (!psfit$converged || any(!is.finite(ps)) || any(ps<.01 | ps>.99)) stop("倾向分数近乎无重叠；不自动截断后声称完成 ATE")
 w <- ifelse(d$.a==1,1/ps,1/(1-ps))
 risk1 <- weighted.mean(d$.y[d$.a==1],w[d$.a==1]); risk0 <- weighted.mean(d$.y[d$.a==0],w[d$.a==0])
 list(effect=risk1-risk0,w=w,ps=ps,risks=c(risk0,risk1))
}
.step <- "iptw"
est <- estimate(df)
boot <- replicate(200,tryCatch(estimate(df[sample.int(nrow(df),replace=TRUE),])$effect,error=function(e) NA_real_))
if (sum(is.finite(boot))<180) stop("超过 10% bootstrap 重拟合失败；不报告不稳定区间")
ci <- stats::quantile(boot,c(.025,.975),na.rm=TRUE,names=FALSE)
x <- stats::model.matrix(stats::reformulate(sprintf("`%s`",covars)),data=df)[,-1,drop=FALSE]
smd <- function(z,w) {
 den <- sqrt((stats::var(z[df$.a==1])+stats::var(z[df$.a==0]))/2)
 if (!is.finite(den) || den==0) return(NA_real_)
 (weighted.mean(z[df$.a==1],w[df$.a==1])-weighted.mean(z[df$.a==0],w[df$.a==0]))/den
}
balance <- data.frame(variable=colnames(x),before=apply(x,2,smd,w=rep(1,nrow(df))),after=apply(x,2,smd,w=est$w))
fig <- ggplot2::ggplot(balance,ggplot2::aes(x=abs(after),y=variable)) + ggplot2::geom_point() +
 ggplot2::geom_vline(xintercept=.1,linetype=2) + ggplot2::theme_bw() + ggplot2::labs(x="加权后绝对 SMD",y="基线变量")
ess <- vapply(c(0,1),function(a) {w<-est$w[df$.a==a];sum(w)^2/sum(w^2)},numeric(1))
result <- data.frame(estimand="baseline ATE risk difference (strategy 1 minus 0)",estimate=est$effect,lower=ci[1],upper=ci[2],risk0=est$risks[1],risk1=est$risks[2],ess0=ess[1],ess1=ess[2],max_weight=max(est$w),bootstrap_success=sum(is.finite(boot)))
notes <- "限定为已确认目标试验方案下的基线策略、固定时窗二分类结局 ATE；logistic PS 的 Hájek IPTW 风险差，200 次按个体 bootstrap 重估 PS 的百分位区间。时间零点列仅核查存在与完整性；资格、分配时点、测量先后和无失访须由研究者确认。未测混杂未评估；不支持克隆-删失-加权、动态策略或时间变化暴露。"
.diag_status[["协变量平衡检查"]] <- if (anyNA(balance$after) || any(abs(balance$after)>.1)) "warning" else "passed"
.diag_status[["倾向重叠检查"]] <- if (min(est$ps)<.05 || max(est$ps)>.95) "warning" else "passed"
.out[["primary"]] <- list(kind="table",payload=result,title="基线策略 ATE 与权重信息")
.out[["figure"]] <- list(kind="figure",payload=fig,title="协变量平衡")
.out[["notes"]] <- list(kind="text",payload=notes,title="协议范围与未验证假设")
'''
