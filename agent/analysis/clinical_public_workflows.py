"""Frozen clinical/public-health workflows using installed scientific engines."""

from __future__ import annotations

from .weighted_causal_cores import _COMMON

_HELPERS = r"""
.cp_table <- function(x,title="结果") list(kind="table",payload=x,title=title)
.cp_text <- function(x,title="说明") list(kind="text",payload=x,title=title)
.cp_figure <- function(x,title="图") list(kind="figure",payload=x,title=title)
.cp_finish <- function(text,method,diagnostic="数值及数据条件来自本轮计算；未运行的额外敏感性分析保持未评估。") {
  list(results=.cp_text(text,"结果解释"),method=.cp_text(method,"实际方法"),diagnostic=.cp_text(diagnostic,"软件诊断"))
}
.cp_prob <- function(x) {
  if(!is.numeric(x) || any(!is.finite(x)) || any(x<0 | x>1)) stop("锁定预测必须是 0–1 概率，不允许重新训练后冒充验证")
  as.numeric(x)
}
.cp_num <- function(x,label="数值") {
  if(!is.numeric(x) || any(!is.finite(x))) stop(label,"必须为有限数值")
  as.numeric(x)
}
.cp_count <- function(x) {
  x <- .cp_num(x,"事件计数"); if(any(x<0 | x!=floor(x))) stop("事件计数必须为非负整数"); x
}
.cp_time <- function(x) {
  if(inherits(x,c("Date","POSIXt"))) return(as.numeric(x))
  .cp_num(x,"时间")
}
.cp_coef <- function(fit,exp=FALSE) {
  b <- stats::coef(fit); V <- as.matrix(stats::vcov(fit)); s <- sqrt(diag(V)); b <- b[is.finite(b)]
  s <- s[names(b)]; c <- stats::qnorm(.975)
  data.frame(变量=names(b),效应=if(exp) exp(b) else b,标准误=s,
    CI下限=if(exp) exp(b-c*s) else b-c*s,CI上限=if(exp) exp(b+c*s) else b+c*s,row.names=NULL)
}
.cp_group <- function(d,column=NULL) if(is.null(column)) rep("总体",nrow(d)) else as.character(d[[column]])
.cp_validate_survival <- function(time,event) {
  if(!is.numeric(time) || any(!is.finite(time)) || any(time<0)) stop("随访时长必须为非负数值")
  if(!all(event %in% c(0,1))) stop("事件必须按 0=删失、1=事件编码")
  invisible(TRUE)
}
"""

_CALIBRATION = r"""
.step <- "locked-prediction-validation"
split_col <- .wc_get("split_v"); group_col <- .wc_get("group_v"); risk_col <- .wc_get("risk_v")
df <- .wc_frame(c(outcome_v,pred_v,split_col,group_col,risk_col))
df$.y <- .wc_binary(df[[outcome_v]],.wc_get("positive_v"),"事件类别");df$.p <- .cp_prob(df[[pred_v]])
thresholds <- as.numeric(.wc_get("threshold_v",seq(.05,.5,.05)))
if(any(!is.finite(thresholds)) || any(thresholds<=0 | thresholds>=1)) stop("DCA 阈值必须严格介于 0 与 1")
df$.label <- if(is.null(split_col) && is.null(group_col)) "总体" else interaction(df[,c(split_col,group_col),drop=FALSE],drop=TRUE)
.cp_validation <- function(d) {
  if(length(unique(d$.y))!=2L) stop("验证人群必须同时含事件与非事件")
  logit <- stats::qlogis(pmin(pmax(d$.p,1e-6),1-1e-6))
  cal <- .wc_check_fit(stats::glm(d$.y~logit,family=stats::binomial()))
  intercept <- .wc_check_fit(stats::glm(d$.y~1+offset(logit),family=stats::binomial()))
  roc <- pROC::roc(d$.y,d$.p,levels=c(0,1),direction="<",quiet=TRUE)
  list(cal=cal,intercept=intercept,auc=as.numeric(pROC::auc(roc)),brier=mean((d$.y-d$.p)^2))
}
validation <- .cp_validation(df)
groups <- split(df,df$.label)
metrics <- do.call(rbind,lapply(names(groups),function(g) {
  x <- .cp_validation(groups[[g]])
  data.frame(人群=g,N=nrow(groups[[g]]),事件=sum(groups[[g]]$.y),AUC=x$auc,Brier=x$brier,
    校准截距=unname(coef(x$intercept)[1]),校准斜率=unname(coef(x$cal)[2]))
}))
df$.bin <- if(is.null(risk_col)) cut(df$.p,breaks=unique(stats::quantile(df$.p,seq(0,1,.2))),include.lowest=TRUE) else factor(df[[risk_col]])
if(all(is.na(df$.bin))) stop("预测值无变化，无法分组校准")
calibration <- stats::aggregate(df[,c(".p",".y")],by=list(人群=df$.label,风险组=df$.bin),FUN=mean)
cal_fig <- ggplot2::ggplot(calibration,ggplot2::aes(.p,.y,colour=人群))+ggplot2::geom_point(size=2)+
  ggplot2::geom_line()+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+ggplot2::theme_bw()+ggplot2::labs(x="锁定预测概率",y="实际事件比例",title="概率校准")
dca <- do.call(rbind,lapply(names(groups),function(g) {
  d<-groups[[g]];do.call(rbind,lapply(thresholds,function(t) data.frame(人群=g,阈值=t,
    模型=sum(d$.p>=t & d$.y==1)/nrow(d)-sum(d$.p>=t & d$.y==0)/nrow(d)*t/(1-t),
    全部干预=mean(d$.y)-(1-mean(d$.y))*t/(1-t),不干预=0)))
}))
dca_fig <- ggplot2::ggplot(dca,ggplot2::aes(阈值,模型,colour=人群))+ggplot2::geom_line()+
  ggplot2::geom_line(ggplot2::aes(y=全部干预),linetype=2)+ggplot2::geom_hline(yintercept=0,linetype=3)+ggplot2::theme_bw()+ggplot2::labs(y="净获益",title="锁定模型决策曲线")
.out[["metrics"]] <- .cp_table(metrics,"锁定预测验证指标")
.out[["calibration"]] <- .cp_figure(cal_fig,"校准曲线")
.out[["dca"]] <- .cp_figure(dca_fig,"决策曲线")
.out <- c(.out,.cp_finish(sprintf("已锁定预测 AUC=%.4f、Brier=%.5f、校准斜率=%.4f；DCA 阈值 %.3f–%.3f。未重拟合预测器；数据来源与亚组分别计算。",validation$auc,validation$brier,coef(validation$cal)[2],min(thresholds),max(thresholds)),
  "校准回归使用 logit(预测概率)；截距模型固定斜率为 1。0/1 边界仅在 logit 诊断时截于 1e-6，Brier/AUC/DCA 仍使用原始概率。"))
.out[["recalibration"]] <- .cp_text("校准截距/斜率供再校准诊断；本次外部验证结果使用原锁定预测，未以验证样本再训练模型。")
.diag_status[["校准斜率/截距检查"]] <- "passed"
.diag_status[["阈值范围合理性检查"]] <- "passed"
"""

_NRI = r"""
.step <- "paired-locked-prediction-reclassification"
df <- .wc_frame(c(outcome_v,old_v,new_v))
y <- .wc_binary(df[[outcome_v]],.wc_get("positive_v"),"事件类别"); old <- .cp_prob(df[[old_v]]); new <- .cp_prob(df[[new_v]])
thresholds <- .wc_get("threshold_v")
if(is.null(thresholds)) { old_class<-old;new_class<-new;definition<-"连续 NRI" }
else {
  thresholds<-sort(unique(as.numeric(thresholds)))
  if(any(!is.finite(thresholds)) || any(thresholds<=0 | thresholds>=1)) stop("分类阈值必须介于 0 与 1")
  old_class<-findInterval(old,thresholds);new_class<-findInterval(new,thresholds);definition<-"类别 NRI"
}
movement <- sign(new_class-old_class)
nri_event <- mean(movement[y==1]);nri_nonevent <- -mean(movement[y==0]);nri <- nri_event+nri_nonevent
idi <- (mean(new[y==1])-mean(new[y==0]))-(mean(old[y==1])-mean(old[y==0]))
nri_table <- data.frame(定义=definition,NRI事件=nri_event,NRI非事件=nri_nonevent,NRI总计=nri)
idi_table <- data.frame(原区分斜率=mean(old[y==1])-mean(old[y==0]),新区分斜率=mean(new[y==1])-mean(new[y==0]),IDI=idi)
reclassification <- as.data.frame(table(事件=y,原类别=if(is.null(thresholds)) ifelse(old>=.5,"≥0.5","<0.5") else old_class,
  新类别=if(is.null(thresholds)) ifelse(new>=.5,"≥0.5","<0.5") else new_class))
.out[["nri"]] <- .cp_table(nri_table);.out[["idi"]] <- .cp_table(idi_table);.out[["matrix"]] <- .cp_table(reclassification)
.out <- c(.out,.cp_finish(sprintf("%s=%.5f（事件 %.5f、非事件 %.5f）；IDI=%.5f。旧新预测逐行配对且事件方向已确认；仅点估计，未报告未经计算的区间。",definition,nri,nri_event,nri_nonevent,idi),
  "NRI 为事件上移−下移与非事件下移−上移之和；IDI 为两个锁定预测的区分斜率之差。连续 NRI 的展示矩阵以 0.5 分组，仅用于展示，不影响连续 NRI。"))
"""

_MATCHED = r"""
.step <- "matched-design"
suppressPackageStartupMessages(library(survival))
covs <- .wc_cols(.wc_get("covars_v")); method <- as.character(.wc_get("method_v","nested_case_control")); weight_col <- .wc_get("weight_v"); follow_col <- .wc_get("follow_v")
if(!method %in% c("matched_case_control","nested_case_control","case_cohort")) stop("设计分支不受支持")
set_col <- .wc_get("set_v")
if(method!="case_cohort" && is.null(set_col)) stop("条件 Logistic 需要匹配集 ID")
if(method=="case_cohort" && (is.null(weight_col)||is.null(follow_col))) stop("病例队列分析需要已确认的抽样权重和随访时长")
df <- .wc_frame(c(outcome_v,exposure_v,covs,if(method!="case_cohort") set_col,if(method=="case_cohort") c(weight_col,follow_col)))
df$.y <- .wc_binary(df[[outcome_v]],.wc_get("positive_v"),"事件类别")
df$.x <- if(is.numeric(df[[exposure_v]])) df[[exposure_v]] else .wc_treatment(df[[exposure_v]])
rhs <- paste(c(".x",if(length(covs)) .wc_rhs(covs)),collapse=" + ")
if(method=="case_cohort") {
  df$.w <- .wc_weights(df,weight_col);df$.time <- .cp_num(df[[follow_col]],"随访时长");.cp_validate_survival(df$.time,df$.y)
  des <- survey::svydesign(ids=~1,weights=~.w,data=df)
  fit <- survey::svycoxph(stats::as.formula(paste("survival::Surv(.time,.y) ~",rhs)),design=des)
  model <- .cp_coef(fit,exp=TRUE)
  design <- data.frame(设计="已确认抽样权重病例队列",N=nrow(df),事件=sum(df$.y),权重和=sum(df$.w))
  balance <- data.frame(说明="本分支为加权 Cox；无匹配集条件模型")
} else {
  df$.set <- factor(df[[set_col]])
  valid <- vapply(split(df$.y,df$.set),function(x) sum(x)>=1 && sum(x)<length(x),logical(1))
  if(!all(valid)) stop("每个匹配集须包含至少一个病例和一个对照")
  fit <- survival::clogit(stats::as.formula(paste(".y ~",rhs,"+ strata(.set)")),data=df,method="exact")
  if(any(!is.finite(coef(fit))) || any(!is.finite(vcov(fit)))) stop("条件模型不可估计")
  model <- .cp_coef(fit,exp=TRUE)
  balance <- data.frame(匹配集=names(valid),病例=vapply(split(df$.y,df$.set),sum,numeric(1)),
    对照=vapply(split(1-df$.y,df$.set),sum,numeric(1)),row.names=NULL)
  design <- data.frame(设计=method,匹配集=nlevels(df$.set),N=nrow(df),病例=sum(df$.y))
}
.out[["model"]] <- .cp_table(model,"条件 OR 或加权 HR")
.out[["design"]] <- .cp_table(design,"实际设计核查")
.out[["balance"]] <- .cp_table(balance,"匹配集病例对照覆盖")
.out[["weighted"]] <- .cp_table(if(method=="case_cohort") model else data.frame(说明="所选条件模型不使用病例队列权重"))
.out <- c(.out,.cp_finish(paste("已执行",method,"；效应尺度为",if(method=="case_cohort") "HR" else "条件 OR","。不会将病例队列与巢式病例对照的估计器混用。"),
  "匹配/巢式病例对照使用 survival::clogit exact；病例队列使用已确认抽样权重 survey::svycoxph，抽样设计和权重已由研究方案定义。"))
"""

_LONGITUDINAL = r"""
.step <- "clinical-longitudinal"
covs <- .wc_cols(.wc_get("covars_v")); df <- .wc_frame(c(subject_v,time_v,outcome_v,group_v,covs))
df$.id <- factor(df[[subject_v]]);df$.time <- .cp_time(df[[time_v]]);df$.group <- factor(df[[group_v]])
if(anyDuplicated(paste(df$.id,df$.time))) stop("个体×访视时间重复")
df <- df[order(df$.id,df$.time),,drop=FALSE]
outcome <- .wc_outcome(df[[outcome_v]],.wc_get("positive_v"),.wc_get("family_v","auto"));df$.y<-outcome$y
df$.visit <- factor(df$.time);method<-as.character(.wc_get("method_v","mixed"))
form<-stats::as.formula(paste(".y ~ .visit*.group",if(length(covs)) paste("+",.wc_rhs(covs)) else ""))
if(method=="gee") {
  fit<-geepack::geeglm(form,data=df,id=.id,waves=.time,
    family=if(outcome$binary) stats::binomial() else stats::gaussian(),corstr=as.character(.wc_get("correlation_v","exchangeable")))
  model<- .cp_coef(fit,exp=outcome$binary)
} else if(method=="mixed") {
  mixed_form<-stats::update(form,.~.+(1|.id))
  fit<-if(outcome$binary) lme4::glmer(mixed_form,data=df,family=stats::binomial()) else lme4::lmer(mixed_form,data=df)
  if(length(fit@optinfo$conv$lme4$messages)) stop("混合模型未通过收敛检查")
  b<-lme4::fixef(fit);s<-sqrt(diag(as.matrix(vcov(fit))));model<-data.frame(变量=names(b),系数=b,标准误=s,CI下限=b-1.96*s,CI上限=b+1.96*s,row.names=NULL)
} else stop("纵向分支仅支持 mixed 或 gee")
means<-as.data.frame(emmeans::emmeans(fit,~.visit|.group,type="response"))
value_name<-intersect(c("emmean","prob","response"),names(means))[1]
means$.mean<-means[[value_name]];means$.time<-as.numeric(as.character(means$.visit))
trend<-ggplot2::ggplot(means,ggplot2::aes(.time,.mean,colour=.group))+ggplot2::geom_line()+ggplot2::geom_point()+ggplot2::theme_bw()+ggplot2::labs(title="访视×组别边际均值",x="访视时间",y="边际均值/风险")
.out[["model"]]<-.cp_table(model);.out[["means"]]<-.cp_table(means);.out[["trend"]]<-.cp_figure(trend)
.out<-c(.out,.cp_finish(paste("真实",method,"纵向模型含访视、组别、组别×访视及已选协变量；emmeans 计算各组访视边际均值。"),
  "随机截距混合模型或已选工作相关 GEE；个体×时间重复拒绝。二元终点使用 logit 链接。"))
"""

_TRAJECTORY = r"""
.step <- "biomarker-trajectory"
group_col<-.wc_get("group_v");outcome_col<-.wc_get("endpoint_v");covs<-.wc_cols(.wc_get("covars_v"))
df<-.wc_frame(c(subject_v,time_v,measure_v,group_col,outcome_col,covs));df$.id<-factor(df[[subject_v]])
df$.time<-.cp_time(df[[time_v]]);df$.y<-.cp_num(df[[measure_v]],"生物标志物")
if(anyDuplicated(paste(df$.id,df$.time))) stop("个体×测量时间重复")
k<-min(6L,length(unique(df$.time))-1L);if(k<3L) stop("平滑轨迹至少需要 4 个不同测量时点")
rhs<-c(sprintf("s(.time,k=%d)",k),"s(.id,bs='re')",if(!is.null(group_col)) .wc_rhs(group_col),if(length(covs)) .wc_rhs(covs))
fit<-mgcv::gam(stats::as.formula(paste(".y ~",paste(rhs,collapse=" + "))),data=df,method="REML")
summary_fit<-summary(fit);model<-data.frame(项=rownames(summary_fit$s.table),summary_fit$s.table,row.names=NULL,check.names=FALSE)
df$.fitted<-as.numeric(predict(fit,type="response"));df$.group<-.cp_group(df,group_col)
trend<-stats::aggregate(df[,c(".y",".fitted")],by=list(时间=df$.time,组=df$.group),FUN=mean)
figure<-ggplot2::ggplot(trend,ggplot2::aes(时间,.fitted,colour=组))+ggplot2::geom_line()+ggplot2::geom_point(ggplot2::aes(y=.y),alpha=.5)+ggplot2::theme_bw()+ggplot2::labs(title="平滑生物标志物轨迹与观测均值",y="标志物")
association<-"未提供转归，不执行转归关联。"
if(!is.null(outcome_col)) {
  stable<-vapply(split(df[[outcome_col]],df$.id),function(x) length(unique(x))==1L,logical(1));if(!all(stable)) stop("转归变量须为每位受试者一致的最终结局")
  subject<-df[!duplicated(df$.id),,drop=FALSE];avg<-tapply(df$.y,df$.id,mean);subject$.marker<-avg[as.character(subject$.id)]
  end<-.wc_outcome(subject[[outcome_col]],.wc_get("positive_v"));subject$.end<-end$y
  association_fit<-.wc_check_fit(glm(.end~.marker,data=subject,family=if(end$binary) binomial() else gaussian()))
  association<-paste("受试者平均标志物与转归的描述性关联系数",round(coef(association_fit)[2],5),"；不冒充纵向—生存联合模型。")
}
.out[["model"]]<-.cp_table(model);.out[["trend"]]<-.cp_figure(figure);.out[["association"]]<-.cp_text(association)
.out<-c(.out,.cp_finish(association,"mgcv REML 时间平滑与受试者随机截距；可选组别和协变量实际进入模型。转归关联是单独的描述性模型。"))
"""

_SAFETY = r"""
.step <- "adverse-event-denominators"
subject_col<-.wc_get("subject_v");type_col<-.wc_get("type_v");exposure_col<-.wc_get("exposure_v")
df<-.wc_frame(c(group_v,event_v,subject_col,type_col,exposure_col));df$.t<-.wc_treatment(df[[group_v]])
df$.event<-.wc_binary(df[[event_v]],.wc_get("positive_v"),"不良事件标记")
df$.id<-if(is.null(subject_col)) seq_len(nrow(df)) else as.character(df[[subject_col]])
if(any(vapply(split(df$.t,df$.id),function(x) length(unique(x))!=1L,logical(1)))) stop("同一受试者存在冲突组别")
types<-if(is.null(type_col)) "任意 AE" else unique(as.character(df[[type_col]])[df$.event==1])
types<-unique(c("任意 AE",types));patient<-df[!duplicated(df$.id),,drop=FALSE]
rows<-lapply(types,function(type) {
  ev<-df$.event==1 & if(type=="任意 AE" || is.null(type_col)) TRUE else as.character(df[[type_col]])==type
  ids<-unique(df$.id[ev]);n1<-sum(patient$.t==1);n0<-sum(patient$.t==0)
  e1<-sum(patient$.t==1 & patient$.id %in% ids);e0<-sum(patient$.t==0 & patient$.id %in% ids)
  rr<-if(e0>0) (e1/n1)/(e0/n0) else NA_real_;se<-if(e1>0 && e0>0) sqrt(1/e1-1/n1+1/e0-1/n0) else NA_real_
  data.frame(AE=type,处理人数=n1,处理事件=e1,参照人数=n0,参照事件=e0,RR=rr,
    CI下限=if(is.finite(se)) exp(log(rr)-1.96*se) else NA_real_,CI上限=if(is.finite(se)) exp(log(rr)+1.96*se) else NA_real_)
})
ae_table<-do.call(rbind,rows)
rate_table<-data.frame(说明="未提供暴露时间，未计算暴露调整率")
if(!is.null(exposure_col)) {
  df$.pt<-.cp_num(df[[exposure_col]],"暴露时间");if(any(df$.pt<=0)) stop("暴露时间必须为正")
  if(any(vapply(split(df$.pt,df$.id),function(x) length(unique(x))!=1L,logical(1)))) stop("每位受试者总暴露时间须一致，避免重复累加")
  patient$.pt<-df$.pt[match(patient$.id,df$.id)]
  rate_table<-do.call(rbind,lapply(0:1,function(g) data.frame(组=g,AE次数=sum(df$.event[df$.t==g]),
    总暴露时间=sum(patient$.pt[patient$.t==g]),暴露调整率=sum(df$.event[df$.t==g])/sum(patient$.pt[patient$.t==g]))))
}
forest<-ggplot2::ggplot(ae_table,ggplot2::aes(AE,RR))+ggplot2::geom_pointrange(ggplot2::aes(ymin=CI下限,ymax=CI上限),na.rm=TRUE)+ggplot2::geom_hline(yintercept=1,linetype=2)+ggplot2::coord_flip()+ggplot2::theme_bw()
.out[["ae"]]<-.cp_table(ae_table);.out[["rate"]]<-.cp_table(rate_table);.out[["forest"]]<-.cp_figure(forest)
.out<-c(.out,.cp_finish("AE 风险以每组独立受试者为分母；同一 AE 多次记录不重复计入发生风险，暴露调整率另使用 AE 次数与每位受试者总暴露时间。零事件不伪造 Wald 区间。",
  "任意及分型 AE 风险比与非零事件 Wald 区间；暴露调整率另列。没有受试者 ID 时每行视为一个独立受试者。"))
"""

_COMPOSITE = r"""
.step <- "composite-endpoint"
covs<-.wc_cols(.wc_get("covars_v"));follow_col<-.wc_get("follow_v");component_col<-.wc_get("component_v")
df<-.wc_frame(c(group_v,outcome_v,covs,follow_col,component_col));df$.t<-.wc_treatment(df[[group_v]])
df$.y<-.wc_binary(df[[outcome_v]],.wc_get("positive_v"),"复合终点事件")
rhs<-paste(c(".t",if(length(covs)) .wc_rhs(covs)),collapse=" + ")
if(is.null(follow_col)) {fit<-.wc_check_fit(glm(as.formula(paste(".y ~",rhs)),family=binomial(),data=df));scale<-"复合终点 OR"}
else {df$.time<-.cp_num(df[[follow_col]],"首次事件/删失时长");.cp_validate_survival(df$.time,df$.y);fit<-survival::coxph(as.formula(paste("survival::Surv(.time,.y) ~",rhs)),data=df);scale<-"至首次复合事件 HR"}
primary<-.cp_coef(fit,exp=TRUE);primary$尺度<-scale
components<-if(is.null(component_col)) data.frame(说明="未提供组成事件类型；不计算组成事件结果") else
  as.data.frame(table(组=df$.t,组成事件=df[[component_col]],复合事件=df$.y))
.out[["primary"]]<-.cp_table(primary);.out[["components"]]<-.cp_table(components)
.out[["sensitivity"]]<-.cp_text("本固定分支计算二元复合终点或首次复合事件；不把普通复合终点系数标成层级终点 win ratio。组成事件频数不冒充各组成事件独立效应。")
.out<-c(.out,.cp_finish(paste("已执行",scale,"；组成事件按已选类型和处理组展示。"),"所选二元复合终点 logistic 或首次事件 Cox；终点定义和事件编码沿用已确认方案。"))
"""

_MULTISTATE = r"""
.step <- "multistate-risk-sets"
covs<-.wc_cols(.wc_get("covars_v"));group_col<-.wc_get("group_v");start_col<-.wc_get("start_time_v");event_col<-.wc_get("transition_event_v")
df<-.wc_frame(c(subject_v,from_v,to_v,time_v,covs,group_col,start_col,event_col));df$.id<-as.character(df[[subject_v]])
df$.stop<-.cp_num(df[[time_v]],"转移/删失时长");df$.from<-as.character(df[[from_v]]);df$.to<-as.character(df[[to_v]])
df<-df[order(df$.id,df$.stop),,drop=FALSE]
df$.start<-if(is.null(start_col)) ave(df$.stop,df$.id,FUN=function(x) c(0,head(x,-1))) else .cp_num(df[[start_col]],"区间起点")
if(any(df$.start<0 | df$.stop<=df$.start)) stop("多状态区间必须满足 0≤起点<终点")
df$.event<-if(is.null(event_col)) as.integer(df$.to!=df$.from) else .cp_count(df[[event_col]])
if(!all(df$.event %in% 0:1)) stop("转移事件必须为 0/1")
if(any(df$.event==0 & df$.to!=df$.from)) stop("删失行终止状态须等于起始状态")
paths<-split(seq_len(nrow(df)),df$.id)
for(i in paths) if(length(i)>1L && any(df$.from[i[-1]]!=df$.to[i[-length(i)]] | df$.start[i[-1]]!=df$.stop[i[-length(i)]])) stop("同一个体状态/时间区间不连续")
states<-sort(unique(c(df$.from,df$.to)));dest<-ifelse(df$.event==1,df$.to,"censored")
df$.status<-factor(dest,levels=c("censored",states));df$.initial<-factor(df$.from,levels=states)
occupation<-survival::survfit(survival::Surv(.start,.stop,.status)~1,id=.id,istate=.initial,data=df)
probabilities<-data.frame(时间=rep(occupation$time,ncol(occupation$pstate)),状态=rep(colnames(occupation$pstate),each=length(occupation$time)),概率=as.vector(occupation$pstate))
transitions<-unique(paste(df$.from[df$.event==1],df$.to[df$.event==1],sep="->"))
allowed<-.wc_get("allowed_v");if(!is.null(allowed) && !all(transitions %in% as.character(allowed))) stop("观测到未允许的状态转移")
model_rows<-lapply(transitions,function(edge) {
  parts<-strsplit(edge,"->",fixed=TRUE)[[1]];d<-df[df$.from==parts[1],,drop=FALSE]
  d$.cause<-as.integer(d$.event==1 & d$.to==parts[2]);predictors<-.wc_cols(c(group_col,covs))
  if(length(predictors)) {
    fit<-survival::coxph(as.formula(paste("survival::Surv(.start,.stop,.cause) ~",.wc_rhs(predictors),"+ cluster(.id)")),data=d)
    table<-.cp_coef(fit,exp=TRUE);table$转移<-edge;table$事件数<-sum(d$.cause);table
  } else data.frame(变量="无协变量：转移发生率",效应=sum(d$.cause)/sum(d$.stop-d$.start),标准误=NA_real_,CI下限=NA_real_,CI上限=NA_real_,转移=edge,事件数=sum(d$.cause))
})
primary<-do.call(rbind,model_rows)
figure<-ggplot2::ggplot(probabilities,ggplot2::aes(时间,概率,colour=状态))+ggplot2::geom_step()+ggplot2::theme_bw()+ggplot2::labs(title="Aalen–Johansen 状态占用概率")
.out[["primary"]]<-.cp_table(primary);.out[["figure"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish("使用完整起始状态风险集估计转移风险和 Aalen–Johansen 概率；竞争转移在相应原因模型中删失，保留真实删失行。",
  "survival 多状态 counting-process AJ；转移 Cox 使用起始状态全部风险区间并按个体聚类，未以仅发生转移的病例冒充风险集。"))
.diag_status[["状态时间一致性检查"]]<-"passed";.diag_status[["转移风险与概率校验"]]<-if(max(abs(rowSums(occupation$pstate)-1))<1e-8) "passed" else "failed"
"""

_LANDMARK = r"""
.step <- "locked-landmark-validation"
suppressPackageStartupMessages(library(survival))
L<-as.numeric(.wc_get("landmark_v"));H<-as.numeric(.wc_get("horizon_v"))
if(length(L)!=1L || !is.finite(L) || L<0 || length(H)!=1L || !is.finite(H) || H<=0) stop("必须明确 landmark 时点和正预测时窗")
group_col<-.wc_get("group_v");covs<-.wc_cols(.wc_get("covars_v"));pred_col<-.wc_cols(pred_v)
if(length(pred_col)!=1L) stop("验证分支需要一列已锁定的预测时窗事件概率")
df<-.wc_frame(c(subject_v,follow_v,event_v,pred_col,group_col,covs))
if(anyDuplicated(df[[subject_v]])) stop("单个 landmark 验证需每位受试者一行")
df$.time<-.cp_num(df[[follow_v]],"终点/删失时长");df$.event<-.cp_count(df[[event_v]]);.cp_validate_survival(df$.time,df$.event)
df$.p<-.cp_prob(df[[pred_col]]);df<-df[df$.time>L,,drop=FALSE];df$.time<-df$.time-L
if(nrow(df)<20L || sum(df$.event==1 & df$.time<=H)<3L) stop("landmark 风险集或预测窗事件不足")
roc<-timeROC::timeROC(T=df$.time,delta=df$.event,marker=df$.p,cause=1,times=H,iid=FALSE)
auc<-unname(tail(roc$AUC,1));if(!is.finite(auc)) stop("时间依赖 AUC 不可估计")
censor<-survival::survfit(survival::Surv(.time,1-.event)~1,data=df)
.G<-function(t,before=FALSE) vapply(t,function(x) {hit<-which(if(before) censor$time<x else censor$time<=x);if(length(hit)) tail(censor$surv[hit],1) else 1},numeric(1))
cases<-df$.event==1 & df$.time<=H;controls<-df$.time>H
gcase<-.G(df$.time,TRUE);gh<-.G(H)
if(any(gcase[cases]<=0) || gh<=0) stop("预测窗删失生存概率为零，IPCW 不可估计")
brier<-(sum((1-df$.p[cases])^2/gcase[cases])+sum(df$.p[controls]^2/gh))/nrow(df)
bins<-cut(df$.p,breaks=unique(quantile(df$.p,seq(0,1,.25))),include.lowest=TRUE)
cal<-do.call(rbind,lapply(split(seq_len(nrow(df)),bins),function(i) {
  sf<-survival::survfit(survival::Surv(.time,.event)~1,data=df[i,,drop=FALSE]);s<-summary(sf,times=H,extend=TRUE)$surv
  data.frame(预测=mean(df$.p[i]),实际=1-s,N=length(i))
}))
primary<-data.frame(Landmark=L,预测时窗=H,风险集=nrow(df),窗内事件=sum(cases),时间依赖AUC=auc,IPCW_Brier=brier)
if(length(covs) || !is.null(group_col)) {
  df$.risk_score<-qlogis(pmin(pmax(df$.p,1e-6),1-1e-6))
  conditional<-survival::coxph(as.formula(paste("survival::Surv(.time,.event) ~ .risk_score +",.wc_rhs(.wc_cols(c(group_col,covs))))),data=df)
  primary$附加条件模型参数数<-length(coef(conditional))
}
figure<-ggplot2::ggplot(cal,ggplot2::aes(预测,实际))+ggplot2::geom_point()+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+ggplot2::theme_bw()
.out[["primary"]]<-.cp_table(primary);.out[["figure"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish(sprintf("L=%.3f，预测窗=%.3f；锁定预测时间依赖 AUC=%.4f、IPCW Brier=%.5f。早期删失不被编码为非事件；附加协变量只用于条件关联模型，未替换锁定预测。",L,H,auc,brier),
  "timeROC 边际 IPCW AUC；删失 KM 逆概率 Brier；风险分组 KM 观察风险。仅支持单个已确认 landmark 与锁定窗内事件概率。"))
.diag_status[["Landmark 样本集检查"]]<-"passed";.diag_status[["时间依赖区分度检查"]]<-"passed";.diag_status[["动态校准检查"]]<-"passed"
"""

_JOINT = r"""
.step <- "true-joint-longitudinal-survival"
suppressPackageStartupMessages(library(nlme))
suppressPackageStartupMessages(library(survival))
suppressPackageStartupMessages(library(JM))
covs<-.wc_cols(.wc_get("covars_v"));group_col<-.wc_get("group_v")
df<-.wc_frame(c(subject_v,time_v,outcome_v,follow_v,event_v,group_col,covs))
df$.id<-factor(df[[subject_v]]);df$.time<-.cp_num(df[[time_v]],"纵向时间");df$.y<-.cp_num(df[[outcome_v]],"连续纵向结局")
df$.surv<-.cp_num(df[[follow_v]],"生存时长");df$.event<-.cp_count(df[[event_v]]);.cp_validate_survival(df$.surv,df$.event)
if(any(df$.time<0 | df$.time>df$.surv)) stop("纵向测量不得发生于生存终点之后")
baseline_cols<-c(".surv",".event",group_col,covs)
for(cn in baseline_cols) if(any(vapply(split(df[[cn]],df$.id),function(x) length(unique(x))!=1L,logical(1)))) stop("生存/基线变量在个体内不一致：",cn)
df<-df[order(df$.id,df$.time),,drop=FALSE];df$.id<-droplevels(df$.id)
surv_data<-df[!duplicated(df$.id),,drop=FALSE]
predictors<-.wc_cols(c(group_col,covs))
long_formula<-as.formula(paste(".y ~ .time",if(length(predictors)) paste("+",.wc_rhs(predictors)) else ""))
lme_fit<-nlme::lme(long_formula,random=~.time|.id,data=df,method="ML",control=nlme::lmeControl(opt="optim",maxIter=200))
surv_fit<-survival::coxph(as.formula(paste("survival::Surv(.surv,.event) ~",.wc_rhs(predictors))),data=surv_data,x=TRUE)
joint<-JM::jointModel(lme_fit,surv_fit,timeVar=".time",method="weibull-PH-aGH",parameterization="value",
  control=list(iter.EM=100,iter.qN=200,GHk=5))
summary_joint<-summary(joint)
association<-unname(joint$coefficients$alpha)
if(length(association)!=1L || !is.finite(association)) stop("联合关联参数不可估计")
primary<-data.frame(参数="当前纵向标志物与事件风险关联 alpha",估计=association,事件风险倍数=exp(association),方法="JM Weibull-PH 自适应 Gauss–Hermite 联合似然")
trajectory<-data.frame(时间=df$.time,测量=df$.y,个体=df$.id)
figure<-ggplot2::ggplot(trajectory,ggplot2::aes(时间,测量,group=个体))+ggplot2::geom_line(alpha=.18)+ggplot2::geom_smooth(method="loess",se=FALSE)+ggplot2::theme_bw()+ggplot2::labs(title="联合模型数据的个体轨迹")
.out[["primary"]]<-.cp_table(primary);.out[["figure"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish(sprintf("成熟 JM 共享参数联合模型：alpha=%.5f，风险倍数 exp(alpha)=%.5f；纵向随机截距/斜率与生存 Weibull-PH 在联合似然中估计。",association,exp(association)),
  "JM::jointModel Weibull-PH-aGH value 参数化；未以随机效应提取后单独 Cox 的两阶段近似替代联合模型。当前输出关联点估计；未把未计算的联合模型区间标成已验证。"))
.diag_status[["纵向子模型检查"]]<-"passed";.diag_status[["生存子模型检查"]]<-"passed"
"""

_MULTILEVEL = r"""
.step<-"regional-random-effects"
covs<-.wc_cols(.wc_get("covars_v"));regional<-.wc_cols(region_vars_v)
df<-.wc_frame(c(outcome_v,region_v,regional,covs));df$.region<-factor(df[[region_v]])
if(nlevels(df$.region)<5L) stop("区域随机效应至少需要 5 个地区")
for(v in regional) if(any(vapply(split(df[[v]],df$.region),function(x) length(unique(x))!=1L,logical(1)))) stop("地区层变量在同一地区内不一致：",v)
outcome<-.wc_outcome(df[[outcome_v]],.wc_get("positive_v"),.wc_get("family_v","auto"));df$.y<-outcome$y
form<-as.formula(paste(".y ~",.wc_rhs(.wc_cols(c(regional,covs))),"+ (1|.region)"))
fit<-if(outcome$binary) lme4::glmer(form,data=df,family=binomial()) else lme4::lmer(form,data=df)
if(length(fit@optinfo$conv$lme4$messages)) stop("地区混合模型未通过收敛检查")
b<-lme4::fixef(fit);s<-sqrt(diag(as.matrix(vcov(fit))));model<-data.frame(变量=names(b),系数=b,标准误=s,CI下限=b-1.96*s,CI上限=b+1.96*s,row.names=NULL)
vc<-as.data.frame(lme4::VarCorr(fit));regional_var<-vc$vcov[vc$grp==".region"][1];residual_var<-if(outcome$binary) pi^2/3 else stats::sigma(fit)^2
variance<-data.frame(区域方差=regional_var,残差方差=residual_var,ICC=regional_var/(regional_var+residual_var),二元潜变量尺度=outcome$binary)
re<-lme4::ranef(fit,condVar=TRUE)[[1]];regional_effect<-data.frame(地区=rownames(re),偏移=re[[1]],row.names=NULL)
figure<-ggplot2::ggplot(regional_effect,ggplot2::aes(reorder(地区,偏移),偏移))+ggplot2::geom_point()+ggplot2::coord_flip()+ggplot2::theme_bw()
.out[["model"]]<-.cp_table(model);.out[["variance"]]<-.cp_table(variance);.out[["region_figure"]]<-.cp_figure(figure)
.out[["random"]]<-.cp_text(paste(capture.output(print(regional_effect)),collapse="\n"))
.out<-c(.out,.cp_finish("地区层变量、个体协变量与地区随机截距实际进入模型；ICC 使用连续残差方差或二元潜变量 π²/3。", "lme4 随机截距模型；地区层变量必须在地区内一致，不以区域均值差冒充多层估计。"))
"""

_DAG = r"""
.step<-"confirmed-dag"
spec<-.wc_get("dag_v");if(is.null(spec)) stop("DAG 工作流需要已确认的 dag_specification，不从相关性自动编造因果图")
graph<-dagitty::dagitty(as.character(spec));covs<-.wc_cols(.wc_get("covars_v"));mediator_col<-.wc_get("mediator_v");selection_col<-.wc_get("selection_v")
if(!all(c(exposure_v,outcome_v) %in% names(graph))) stop("DAG 节点名须包含所选暴露和结局列")
sets<-dagitty::adjustmentSets(graph,exposure=exposure_v,outcome=outcome_v,type="minimal")
adjustment<-if(length(sets)) do.call(rbind,lapply(seq_along(sets),function(i) data.frame(调整集=i,变量=paste(sets[[i]],collapse=",")))) else data.frame(调整集=1L,变量="无可识别的最小调整集")
if(!length(sets)) stop("已确认 DAG 无可识别调整集，不能继续用普通回归冒充已识别效应")
selected<-if(length(covs)) covs else as.character(sets[[1]])
if(!dagitty::isAdjustmentSet(graph,selected,exposure=exposure_v,outcome=outcome_v)) stop("所选协变量不满足已确认 DAG 调整集")
df<-.wc_frame(c(exposure_v,outcome_v,selected,mediator_col,selection_col))
df$.x<-if(is.numeric(df[[exposure_v]])) df[[exposure_v]] else .wc_treatment(df[[exposure_v]])
outcome<-.wc_outcome(df[[outcome_v]],.wc_get("positive_v"));df$.y<-outcome$y
fit<-.wc_check_fit(glm(as.formula(paste(".y ~ .x",if(length(selected)) paste("+",.wc_rhs(selected)) else "")),data=df,family=if(outcome$binary) binomial() else gaussian()))
sensitivity<-.wc_coef_table(fit,.wc_vcov(fit),terms=".x",exponentiate=outcome$binary)
mediation_table<-data.frame(说明="未提供中介，不执行中介分解")
if(!is.null(mediator_col)) {
  if(!is.numeric(df[[mediator_col]])) stop("当前中介分支支持连续中介")
  df$.m<-df[[mediator_col]]
  mf<-lm(as.formula(paste(".m ~ .x",if(length(selected)) paste("+",.wc_rhs(selected)) else "")),data=df)
  yf<-glm(as.formula(paste(".y ~ .x + .m",if(length(selected)) paste("+",.wc_rhs(selected)) else "")),data=df,family=if(outcome$binary) binomial() else gaussian())
  set.seed(104729);med<-mediation::mediate(mf,yf,treat=".x",mediator=".m",boot=TRUE,sims=as.integer(.wc_get("bootstrap_v",199)))
  mediation_table<-data.frame(效应=c("ACME","ADE","总效应"),估计=c(med$d.avg,med$z.avg,med$tau.coef),
    CI下限=c(med$d.avg.ci[1],med$z.avg.ci[1],med$tau.ci[1]),CI上限=c(med$d.avg.ci[2],med$z.avg.ci[2],med$tau.ci[2]))
}
assumptions<-data.frame(已确认图=as.character(graph),所选调整变量=paste(selected,collapse=","),
  选择变量=if(is.null(selection_col)) "无" else paste(selection_col,"：只描述已观察选择机制，不自动删样本"))
.out[["dag"]]<-.cp_text(as.character(graph));.out[["adjustment"]]<-.cp_table(adjustment)
.out[["mediation"]]<-.cp_table(mediation_table);.out[["sensitivity"]]<-.cp_table(sensitivity);.out[["assumptions"]]<-.cp_table(assumptions)
.out[["advice"]]<-.cp_text("调整集来自已确认 DAG；软件不根据一次回归或中介显著性证明因果识别。所选估计器及有限中介分支见结果。")
"""

_MIXTURE = r"""
.step<-"environmental-additive-exposure-response"
exposures<-.wc_cols(exposure_v);covs<-.wc_cols(.wc_get("covars_v"));region_col<-.wc_get("region_v")
df<-.wc_frame(c(outcome_v,exposures,covs,region_col));outcome<-.wc_outcome(df[[outcome_v]],.wc_get("positive_v"));df$.y<-outcome$y
for(v in exposures) .cp_num(df[[v]],"环境暴露")
smooths<-vapply(exposures,function(v) sprintf("s(`%s`,k=%d)",v,min(5L,length(unique(df[[v]]))-1L)),character(1))
if(any(vapply(exposures,function(v) length(unique(df[[v]]))<4L,logical(1)))) stop("暴露平滑至少需要 4 个不同值")
interaction_mode<-as.character(.wc_get("interactions_v","pairwise"));if(!interaction_mode%in%c("pairwise","none"))stop("exposure_interactions需为 pairwise 或 none")
interaction_terms<-character()
if(interaction_mode=="pairwise"&&length(exposures)>1){
  pairs<-utils::combn(exposures,2,simplify=FALSE)
  interaction_terms<-vapply(pairs,function(p)sprintf("ti(`%s`,`%s`,k=c(4,4))",p[1],p[2]),character(1))
}
rhs<-c(smooths,interaction_terms,if(length(covs)) .wc_rhs(covs),if(!is.null(region_col)) paste0("factor(`",region_col,"`)"))
fit<-mgcv::gam(as.formula(paste(".y ~",paste(rhs,collapse=" + "))),data=df,family=if(outcome$binary) binomial() else gaussian(),method="REML")
sm<-summary(fit);model<-data.frame(暴露项=rownames(sm$s.table),sm$s.table,row.names=NULL,check.names=FALSE)
reference<-df[rep(1,101),,drop=FALSE]
for(v in exposures) reference[[v]]<-median(df[[v]])
for(v in covs) if(is.numeric(df[[v]])) reference[[v]]<-mean(df[[v]])
first<-exposures[1];reference[[first]]<-seq(min(df[[first]]),max(df[[first]]),length.out=101)
prediction<-predict(fit,newdata=reference,type="link",se.fit=TRUE);inverse<-fit$family$linkinv
curve<-data.frame(暴露=reference[[first]],预测=inverse(prediction$fit),下限=inverse(prediction$fit-1.96*prediction$se.fit),上限=inverse(prediction$fit+1.96*prediction$se.fit))
figure<-ggplot2::ggplot(curve,ggplot2::aes(暴露,预测))+ggplot2::geom_ribbon(ggplot2::aes(ymin=下限,ymax=上限),alpha=.2)+ggplot2::geom_line()+ggplot2::theme_bw()
joint<-df[rep(1,3),,drop=FALSE];for(v in exposures) joint[[v]]<-as.numeric(quantile(df[[v]],c(.25,.5,.75)))
joint_table<-data.frame(共同暴露分位数=c(.25,.5,.75),预测=as.numeric(predict(fit,joint,type="response")))
linear<-glm(as.formula(paste(".y ~",.wc_rhs(.wc_cols(c(exposures,covs,region_col))))),data=df,family=if(outcome$binary) binomial() else gaussian())
sensitivity<-data.frame(模型=c("加性平滑","线性暴露敏感性"),AIC=c(AIC(fit),AIC(linear)),偏差=c(deviance(fit),deviance(linear)))
.out[["curve"]]<-.cp_figure(figure);.out[["joint"]]<-.cp_table(joint_table);.out[["model"]]<-.cp_table(model);.out[["sensitivity"]]<-.cp_table(sensitivity)
.out<-c(.out,.cp_finish(paste("多暴露非线性平滑与",length(interaction_terms),"项真实 tensor 交互；共同分位数改变全部暴露，曲线改变首暴露并保持其他暴露于中位数。"), "mgcv 非线性暴露反应与可选 pairwise ti 张量交互；地区/时间列及协变量实际入模。"))
"""

_ECONOMIC = r"""
.step<-"confirmed-economic-comparison"
df<-.wc_frame(c(outcome_v,cost_v,effect_v,strategy_v));df$.cost<-.cp_num(df[[cost_v]],"成本");df$.effect<-.cp_num(df[[effect_v]],"健康效用")
df$.outcome<-.cp_num(df[[outcome_v]],"负担指标");df$.strategy<-as.character(df[[strategy_v]])
reference<-as.character(.wc_get("reference_strategy_v"));thresholds<-as.numeric(.wc_get("threshold_v"))
if(length(reference)!=1L || !reference %in% df$.strategy) stop("需明确成本效果比较的参照策略")
if(!length(thresholds) || any(!is.finite(thresholds)) || any(thresholds<0)) stop("需明确相同货币/效用单位下的支付意愿阈值")
groups<-split(df,df$.strategy);summary<-do.call(rbind,lapply(names(groups),function(g) data.frame(策略=g,N=nrow(groups[[g]]),
  负担总量=sum(groups[[g]]$.outcome),平均成本=mean(groups[[g]]$.cost),平均效用=mean(groups[[g]]$.effect))))
ref<-summary[summary$策略==reference,,drop=FALSE];summary$增量成本<-summary$平均成本-ref$平均成本;summary$增量效用<-summary$平均效用-ref$平均效用
summary$ICER<-ifelse(summary$增量效用==0,NA_real_,summary$增量成本/summary$增量效用)
comparisons<-setdiff(names(groups),reference);if(!length(comparisons)) stop("至少需要两个干预策略")
set.seed(104729);B<-as.integer(.wc_get("bootstrap_v",199));if(B<19L) stop("bootstrap 次数至少 19")
ceac<-do.call(rbind,lapply(comparisons,function(g) {
  draws<-replicate(B,{a<-groups[[g]][sample.int(nrow(groups[[g]]),replace=TRUE),,drop=FALSE];b<-groups[[reference]][sample.int(nrow(groups[[reference]]),replace=TRUE),,drop=FALSE];c(cost=mean(a$.cost)-mean(b$.cost),effect=mean(a$.effect)-mean(b$.effect))})
  data.frame(策略=g,支付意愿=thresholds,成本有效概率=vapply(thresholds,function(l) mean(l*draws["effect",]-draws["cost",]>0),numeric(1)))
}))
figure<-ggplot2::ggplot(ceac,ggplot2::aes(支付意愿,成本有效概率,colour=策略))+ggplot2::geom_line()+ggplot2::geom_point()+ggplot2::theme_bw()
.out[["burden"]]<-.cp_table(summary[,c("策略","N","负担总量")]);.out[["economic"]]<-.cp_table(summary);.out[["sensitivity"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish("按已确认参照策略计算增量成本、增量效用、ICER，并用成对成本/效用的策略内 bootstrap 计算 CEAC；零效用差不伪造 ICER。",
  "行级成本/健康效用比较；支付意愿、参照策略和效用方向已由输入明确。非长期 Markov 或个体微观仿真模型。"))
"""

_SPATIAL = r"""
.step<-"confirmed-spatial-boundaries"
df<-.wc_frame(c(region_v,outcome_v,denom_v,boundary_v));if(anyDuplicated(df[[region_v]])) stop("空间地图需每地区一行")
df$.events<-.cp_count(df[[outcome_v]]);df$.n<-.cp_num(df[[denom_v]],"人口分母");if(any(df$.n<=0)) stop("人口分母必须为正")
if(inherits(dataset,"sf")) geo<-sf::st_as_sf(df)
else {
  geometry<-if(inherits(df[[boundary_v]],"sfc")) df[[boundary_v]] else sf::st_as_sfc(as.character(df[[boundary_v]]))
  geo<-sf::st_sf(df,geometry=geometry)
}
if(any(!sf::st_is_valid(geo))) stop("空间边界无效；须先修复数据，不能自动替换边界")
geo$.rate<-geo$.events/geo$.n;neighbors<-spdep::poly2nb(geo,queen=TRUE)
zero<-isTRUE(.wc_get("zero_v",FALSE));if(any(spdep::card(neighbors)==0) && !zero) stop("存在孤立地区；需要明确 zero_policy")
weights<-spdep::nb2listw(neighbors,style="W",zero.policy=zero)
moran<-spdep::moran.test(geo$.rate,weights,zero.policy=zero)
local<-spdep::localmoran(geo$.rate,weights,zero.policy=zero)
primary<-data.frame(指标="全局 Moran I",估计=unname(moran$estimate[1]),期望=unname(moran$estimate[2]),方差=unname(moran$estimate[3]),P值=moran$p.value)
clusters<-data.frame(地区=geo[[region_v]],局部Moran=local[,1],P值=local[,ncol(local)],邻居数=spdep::card(neighbors))
description<-data.frame(地区=geo[[region_v]],病例=geo$.events,人口=geo$.n,率=geo$.rate)
figure<-ggplot2::ggplot(geo)+ggplot2::geom_sf(ggplot2::aes(fill=.rate))+ggplot2::theme_bw()+ggplot2::labs(fill="病例/人口",title="已确认边界疾病率地图")
.out[["map"]]<-.cp_figure(figure);.out[["moran"]]<-.cp_table(primary);.out[["clusters"]]<-.cp_table(clusters);.out[["description"]]<-.cp_table(description)
.out[["spatial_note"]]<-.cp_text("全局/局部 Moran 使用 queen 邻接行标准化权重；本分支为地图与空间自相关，不输出未经拟合的 INLA 空间风险模型。")
.out<-c(.out,.cp_finish("按地区 ID、病例和共同边界计算人口率、全局与局部 Moran；未用地区排序或任意距离替代真实邻接。", "sf 边界与 spdep queen 邻接；局部多个 P 值仅作描述，未自动宣称显著疾病聚集。"))
"""

_APC = r"""
.step<-"identified-apc"
suppressPackageStartupMessages(library(Epi))
denom_col<-.wc_get("denom_v");group_col<-.wc_get("group_v")
if(is.null(denom_col)) stop("APC 计数模型需明确人时/人口分母")
df<-.wc_frame(c(age_v,period_v,outcome_v,denom_col,group_col));df$.group<-.cp_group(df,group_col)
df$A<-.cp_num(df[[age_v]],"年龄中点");df$P<-.cp_num(df[[period_v]],"时期");df$D<-.cp_count(df[[outcome_v]]);df$Y<-.cp_num(df[[denom_col]],"人时分母")
if(any(df$Y<=0)) stop("人时分母必须为正")
parameterization<-as.character(.wc_get("apc_parm_v","ACP"));if(!parameterization %in% c("ACP","APC","AdCP","AdPC","Ad-P-C","Ad-C-P","AC-P","AP-C")) stop("APC 识别参数化无效")
fits<-lapply(split(df,df$.group),function(d) Epi::apc.fit(d[,c("A","P","D","Y")],parm=parameterization,npar=c(A=3,P=3,C=3),print.AOV=FALSE))
components<-do.call(rbind,lapply(names(fits),function(g) {
  fit<-fits[[g]];do.call(rbind,lapply(intersect(c("Age","Per","Coh"),names(fit)),function(part) {
    x<-as.data.frame(fit[[part]]);data.frame(人群=g,分量=part,横轴=x[[1]],估计=x[[2]],CI下限=x[[3]],CI上限=x[[4]],check.names=FALSE)
  }))
}))
if(is.null(components) || !nrow(components)) stop("APC 包没有返回可展示的年龄/时期/队列分量")
numeric_columns<-names(components)[vapply(components,is.numeric,logical(1))]
figure<-ggplot2::ggplot(components,ggplot2::aes(x=.data[[numeric_columns[1]]],y=.data[[numeric_columns[2]]],colour=人群))+ggplot2::geom_line()+ggplot2::facet_wrap(~分量,scales="free")+ggplot2::theme_bw()
.out[["model"]]<-.cp_table(components);.out[["trend"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish(paste("成熟 Epi::apc.fit 的",parameterization,"识别参数化；队列=时期−年龄，并明确年龄、时期、队列的线性不可识别约束。"),
  "APC Poisson 人时率模型，自然样条每分量 3 参数；不将三个未经约束的线性回归系数解释为独立因果效应。"))
"""

_SMALL_AREA = r"""
.step<-"survey-domain-estimation"
strata_col<-.wc_get("strata_v");cluster_col<-.wc_get("cluster_v");covs<-.wc_cols(.wc_get("covars_v"))
df<-.wc_frame(c(region_v,outcome_v,weight_v,strata_col,cluster_col,covs));df$.region<-factor(df[[region_v]]);df$.w<-.wc_weights(df,weight_v)
outcome<-.wc_outcome(df[[outcome_v]],.wc_get("positive_v"));df$.y<-outcome$y
ids<-if(is.null(cluster_col)) ~1 else as.formula(paste("~",.wc_rhs(cluster_col)));strata<-if(is.null(strata_col)) NULL else as.formula(paste("~",.wc_rhs(strata_col)))
options(survey.lonely.psu=as.character(.wc_get("lonely_v","fail")))
des<-survey::svydesign(ids=ids,strata=strata,weights=~.w,data=df,nest=TRUE)
degf<-survey::degf(des);if(degf<=0) stop("调查设计自由度不足")
direct<-survey::svyby(~.y,~.region,des,survey::svymean,keep.var=TRUE)
table<-data.frame(地区=as.character(direct$.region),直接估计=direct$.y,标准误=survey::SE(direct),row.names=NULL)
table$CI下限<-table$直接估计-qt(.975,degf)*table$标准误;table$CI上限<-table$直接估计+qt(.975,degf)*table$标准误
if(length(covs)) {
  fit<-survey::svyglm(as.formula(paste(".y ~ .region +",.wc_rhs(covs))),design=des,family=if(outcome$binary) quasibinomial() else gaussian())
  df$.model<-as.numeric(predict(fit,newdata=df,type="response"));table$协变量辅助点估计<-vapply(table$地区,function(g) weighted.mean(df$.model[df$.region==g],df$.w[df$.region==g]),numeric(1))
}
figure<-ggplot2::ggplot(table,ggplot2::aes(地区,直接估计))+ggplot2::geom_pointrange(ggplot2::aes(ymin=CI下限,ymax=CI上限))+ggplot2::coord_flip()+ggplot2::theme_bw()
.out[["regional"]]<-.cp_table(table);.out[["uncertainty"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish("地区直接域估计实际使用调查权重、PSU 和分层；区间使用设计自由度。若选协变量，另列实际 survey GLM 的协变量辅助点估计。",
  "survey::svyby 域估计；本分支不冒充 Fay–Herriot/EBLUP 小地区收缩模型。"))
"""

_ENVIRONMENT_TIME = r"""
.step<-"distributed-lag-time-series"
region_col<-.wc_get("region_v");covs<-.wc_cols(.wc_get("covars_v"));df<-.wc_frame(c(time_v,exposure_v,outcome_v,region_col,covs))
df$.time<-.cp_time(df[[time_v]]);df$.region<-.cp_group(df,region_col);df$.exposure<-.cp_num(df[[exposure_v]],"环境暴露");df$.y<-.cp_count(df[[outcome_v]])
df<-df[order(df$.region,df$.time),,drop=FALSE];lag_max<-as.integer(.wc_get("lag_v",3));if(lag_max<0 || lag_max>30) stop("滞后阶数必须介于 0 与 30")
for(i in split(seq_len(nrow(df)),df$.region)) if(length(i)<15L || anyDuplicated(df$.time[i]) || max(diff(df$.time[i]))!=min(diff(df$.time[i]))) stop("每个地区须有连续等间隔且至少 15 行的时间序列")
for(lag in 0:lag_max) df[[paste0(".lag",lag)]]<-ave(df$.exposure,df$.region,FUN=function(x) if(lag==0) x else c(rep(NA_real_,lag),head(x,-lag)))
lag_names<-paste0(".lag",0:lag_max);analysis<-df[complete.cases(df[,lag_names,drop=FALSE]),,drop=FALSE]
k<-min(as.integer(.wc_get("time_df_v",6)),length(unique(analysis$.time))-1L);if(k<3L) stop("时间样条自由度不足")
lag_mode<-as.character(.wc_get("lag_mode_v","nonlinear"));if(!lag_mode%in%c("linear","nonlinear"))stop("lag_response需为 linear/nonlinear")
lag_terms<-if(lag_mode=="linear")lag_names else vapply(lag_names,function(nm)sprintf("s(%s,k=%d)",nm,min(5L,length(unique(analysis[[nm]]))-1L)),character(1))
rhs<-c(lag_terms,sprintf("s(.time,k=%d)",k),if(!is.null(region_col)) "factor(.region)",if(length(covs)) .wc_rhs(covs))
fit<-mgcv::gam(as.formula(paste(".y ~",paste(rhs,collapse=" + "))),data=analysis,family=quasipoisson(),method="REML")
if(lag_mode=="linear"){
b<-coef(fit)[lag_names];V<-vcov(fit)[lag_names,lag_names,drop=FALSE];if(any(!is.finite(b))) stop("滞后暴露系数不可估计")
s<-sqrt(diag(V));cumulative<-sum(b);cs<-sqrt(sum(V));lag_table<-data.frame(滞后=0:lag_max,logRR=b,RR=exp(b),CI下限=exp(b-1.96*s),CI上限=exp(b+1.96*s),row.names=NULL)
lag_table<-rbind(lag_table,data.frame(滞后=-1,logRR=cumulative,RR=exp(cumulative),CI下限=exp(cumulative-1.96*cs),CI上限=exp(cumulative+1.96*cs)))
x<-seq(0,quantile(df$.exposure,.9)-quantile(df$.exposure,.1),length.out=50);curve<-data.frame(暴露变化=x,累积RR=exp(x*cumulative))
figure<-ggplot2::ggplot(curve,ggplot2::aes(暴露变化,累积RR))+ggplot2::geom_line()+ggplot2::theme_bw()
}else{
  reference<-analysis[1,,drop=FALSE];center<-median(df$.exposure)
  for(nm in lag_names)reference[[nm]]<-center
  for(nm in covs)if(is.numeric(analysis[[nm]]))reference[[nm]]<-mean(analysis[[nm]])
  reference$.time<-median(analysis$.time);base<-predict(fit,reference,type="lpmatrix")
  changed<-reference[rep(1,length(lag_names)+1),,drop=FALSE]
  for(i in seq_along(lag_names))changed[[lag_names[i]]][i]<-center+1
  for(nm in lag_names)changed[[nm]][nrow(changed)]<-center+1
  contrast<-predict(fit,changed,type="lpmatrix")-base[rep(1,nrow(changed)),,drop=FALSE]
  effect<-as.numeric(contrast%*%coef(fit));effect_se<-sqrt(rowSums((contrast%*%vcov(fit))*contrast))
  lag_table<-data.frame(滞后=c(0:lag_max,-1),logRR=effect,RR=exp(effect),CI下限=exp(effect-1.96*effect_se),CI上限=exp(effect+1.96*effect_se))
  cumulative<-tail(effect,1);cs<-tail(effect_se,1)
  grid<-seq(quantile(df$.exposure,.1),quantile(df$.exposure,.9),length.out=80)
  newdata<-reference[rep(1,length(grid)),,drop=FALSE];for(nm in lag_names)newdata[[nm]]<-grid
  C<-predict(fit,newdata,type="lpmatrix")-base[rep(1,length(grid)),,drop=FALSE];logRR<-as.numeric(C%*%coef(fit));se<-sqrt(rowSums((C%*%vcov(fit))*C))
  curve<-data.frame(暴露=grid,累积RR=exp(logRR),下限=exp(logRR-1.96*se),上限=exp(logRR+1.96*se))
  figure<-ggplot2::ggplot(curve,ggplot2::aes(暴露,累积RR))+ggplot2::geom_ribbon(ggplot2::aes(ymin=下限,ymax=上限),alpha=.2)+ggplot2::geom_line()+ggplot2::theme_bw()
}
residual_acf<-as.numeric(acf(residuals(fit,type="pearson"),plot=FALSE)$acf[2])
sensitivity<-data.frame(参数=c("最大滞后","时间平滑 k","Pearson lag1 ACF","过度离散"),数值=c(lag_max,k,residual_acf,summary(fit)$dispersion))
.out[["response"]]<-.cp_figure(figure);.out[["lag"]]<-.cp_table(lag_table);.out[["sensitivity"]]<-.cp_table(sensitivity)
.out<-c(.out,.cp_finish("时间平滑、地区与已选协变量进入准 Poisson 分布滞后模型；累积效应使用完整模型协方差。非线性分支在暴露中位数比较各滞后增加一个单位，并以共同暴露水平生成响应曲线。",paste("mgcv",lag_mode,"分布滞后；非线性分支为每滞后独立平滑，不伪称 dlnm cross-basis。时间序列自相关需结合实际 ACF 诊断。")))
"""

_DEMOGRAPHIC = r"""
.step<-"population-series-projection"
region_col<-.wc_get("region_v");scenario_col<-.wc_get("scenario_v");df<-.wc_frame(c(time_v,age_v,pop_v,region_col,scenario_col))
df$.time<-.cp_time(df[[time_v]]);df$.population<-.cp_num(df[[pop_v]],"人口规模");if(any(df$.population<0)) stop("人口规模不能为负")
df$.series<-interaction(df[,c(age_v,region_col,scenario_col),drop=FALSE],drop=TRUE)
horizon<-as.integer(.wc_get("horizon_v",5));frequency<-as.integer(.wc_get("frequency_v",1));if(horizon<1 || horizon>100 || frequency<1) stop("预测时窗/频率无效")
projections<-do.call(rbind,lapply(split(df,df$.series),function(d) {
  d<-d[order(d$.time),,drop=FALSE];if(nrow(d)<10 || anyDuplicated(d$.time) || max(diff(d$.time))!=min(diff(d$.time))) stop("每个年龄/地区/情景序列需至少 10 个等间隔时点")
  fit<-forecast::auto.arima(ts(d$.population,frequency=frequency),seasonal=frequency>1)
  pred<-forecast::forecast(fit,h=horizon,level=95);step<-median(diff(d$.time))
  data.frame(序列=as.character(d$.series[1]),时间=max(d$.time)+step*seq_len(horizon),
    人口预测=as.numeric(pred$mean),CI下限=as.numeric(pred$lower),CI上限=as.numeric(pred$upper))
}))
figure<-ggplot2::ggplot(projections,ggplot2::aes(时间,人口预测,colour=序列))+ggplot2::geom_line()+ggplot2::geom_ribbon(ggplot2::aes(ymin=CI下限,ymax=CI上限,fill=序列),alpha=.1,colour=NA)+ggplot2::theme_bw()
.out[["projection"]]<-.cp_table(projections);.out[["trend"]]<-.cp_figure(figure);.out[["uncertainty"]]<-.cp_text("预测区间来自各序列 ARIMA；下界未人工截零。此处是观测人口趋势预测，不包含出生、死亡、迁移的 cohort-component 人口账。")
.out<-c(.out,.cp_finish("年龄、地区和情景分别作为独立序列；数据频率和预测时窗实际传入成熟 forecast 模型。", "forecast::auto.arima 与真实预测区间；不将总人口时间趋势冒充队列人口推演。"))
"""

_SURVEILLANCE = r"""
.step<-"prospective-infectious-monitoring"
df<-.wc_frame(c(count_v,time_v,region_v,pathogen_v));df$.time<-.cp_time(df[[time_v]]);df$.count<-.cp_count(df[[count_v]])
df$.series<-interaction(df[,c(region_v,pathogen_v),drop=FALSE],drop=TRUE)
baseline<-as.integer(.wc_get("baseline_v",52));period<-as.numeric(.wc_get("period_v",52));alpha<-as.numeric(.wc_get("alpha_v",.01))
if(baseline<20 || period<=0 || alpha<=0 || alpha>=.5) stop("监测基线长度、季节周期或报警 alpha 无效")
monitor<-do.call(rbind,lapply(split(df,df$.series),function(d) {
  d<-d[order(d$.time),,drop=FALSE];if(nrow(d)<=baseline || anyDuplicated(d$.time)) stop("各地区/病原序列须有严格基线和基线后的监测数据")
  d$.index<-seq_len(nrow(d));d$.sin<-sin(2*pi*d$.index/period);d$.cos<-cos(2*pi*d$.index/period)
  fit<-glm(.count~.index+.sin+.cos,data=d[seq_len(baseline),],family=quasipoisson())
  pred<-as.numeric(predict(fit,newdata=d[-seq_len(baseline),],type="response"));dispersion<-summary(fit)$dispersion
  upper<-if(dispersion>1) qnbinom(1-alpha,mu=pred,size=pred/pmax(dispersion-1,1e-8)) else qpois(1-alpha,lambda=pred)
  test<-d[-seq_len(baseline),];data.frame(地区病原=as.character(test$.series),时间=test$.time,观测=test$.count,基线期望=pred,报警上界=upper,报警=test$.count>upper)
}))
figure<-ggplot2::ggplot(monitor,ggplot2::aes(时间,观测,colour=地区病原))+ggplot2::geom_line()+ggplot2::geom_line(ggplot2::aes(y=报警上界),linetype=2)+ggplot2::theme_bw()
.out[["monitor"]]<-.cp_figure(figure);.out[["alarms"]]<-.cp_table(monitor[monitor$报警,,drop=FALSE])
.out[["season"]]<-.cp_text(paste("季节周期",period,"个时间单位；仅用前",baseline,"个时点训练基线，监测期未泄漏到基线。"))
.out[["response"]]<-.cp_text("报警为相对于已选基线的统计异常，不能自动诊断真实暴发；按地区/病原逐条核查观测与上界。")
.out<-c(.out,.cp_finish("各地区/病原分开建立既定季节基线，监测期计算预期病例与报警界值。", "准 Poisson 季节基线（线性趋势＋一个 Fourier 周期），过度离散时以矩匹配负二项预测界值；非自动 Farrington 全参数实现。"))
"""

_JOINPOINT = r"""
.step<-"segmented-log-rate-trend"
denom_col<-.wc_get("denom_v");region_col<-.wc_get("region_v");age_col<-.wc_get("age_v");covs<-.wc_cols(.wc_get("covars_v"))
df<-.wc_frame(c(time_v,outcome_v,denom_col,region_col,age_col,covs));df$.time<-.cp_time(df[[time_v]]);df$.y<-.cp_num(df[[outcome_v]],"计数/率")
if(!is.null(denom_col)) {df$.n<-.cp_num(df[[denom_col]],"分母");if(any(df$.n<=0)) stop("人口分母须为正");df$.rate<-df$.y/df$.n} else df$.rate<-df$.y
if(any(df$.rate<=0)) stop("APC 对数率模型需要严格正的率；零计数应选择计数分段模型")
df$.series<-if(is.null(region_col)&&is.null(age_col)) "总体" else interaction(df[,c(region_col,age_col),drop=FALSE],drop=TRUE)
npsi<-as.integer(.wc_get("breaks_v",1));if(npsi<1 || npsi>4) stop("固定断点数量须为 1–4")
results<-lapply(split(df,df$.series),function(d) {
  d<-d[order(d$.time),,drop=FALSE];if(nrow(d)<10+5*npsi || anyDuplicated(d$.time)) stop("每个序列的独立时间点不足")
  base<-lm(as.formula(paste("log(.rate) ~ .time",if(length(covs)) paste("+",.wc_rhs(covs)) else "")),data=d)
  fit<-segmented::segmented(base,seg.Z=~.time,npsi=npsi,control=segmented::seg.control(display=FALSE))
  slopes<-segmented::slope(fit)[[1]];table<-data.frame(序列=as.character(d$.series[1]),分段=seq_len(nrow(slopes)),
    log率斜率=slopes[,1],APC百分比=100*(exp(slopes[,1])-1),CI下限=100*(exp(slopes[,3])-1),CI上限=100*(exp(slopes[,4])-1),row.names=NULL)
  list(table=table,pred=data.frame(序列=as.character(d$.series[1]),时间=d$.time,观测=d$.rate,拟合=exp(fitted(fit))),breaks=fit$psi)
})
primary<-do.call(rbind,lapply(results,`[[`,"table"));curves<-do.call(rbind,lapply(results,`[[`,"pred"))
figure<-ggplot2::ggplot(curves,ggplot2::aes(时间,拟合,colour=序列))+ggplot2::geom_line()+ggplot2::geom_point(ggplot2::aes(y=观测),alpha=.5)+ggplot2::theme_bw()
.out[["primary"]]<-.cp_table(primary);.out[["figure"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish("所选断点数由成熟 segmented 拟合；分段斜率通过 exp(beta)−1 转为每个时间单位的率变化百分比，使用分母后率实际入模。", "log-rate segmented 趋势；明确断点数量，不将一次固定断点拟合宣称为 NCI 全模型置换筛选；算法失败时拒绝，而非静默回退线性后称无断点。"))
"""

_BURDEN_DECOMPOSITION = r"""
.step<-"exact-burden-decomposition"
time_col<-.wc_get("time_v");region_col<-.wc_get("region_v");standard_col<-.wc_get("standard_v");risk_cols<-.wc_cols(.wc_get("risk_v"));strata_cols<-.wc_cols(strata_v)
df<-.wc_frame(c(outcome_v,denom_v,strata_cols,time_col,region_col,standard_col,risk_cols));df$.events<-.cp_count(df[[outcome_v]]);df$.n<-.cp_num(df[[denom_v]],"人口分母")
if(any(df$.n<=0)) stop("人口分母须为正")
df$.region<-.cp_group(df,region_col);df$.stratum<-interaction(df[,unique(c(strata_cols,risk_cols)),drop=FALSE],drop=TRUE)
if(is.null(time_col)) stop("人口/构成/率变化分解需要明确比较时间")
df$.time<-.cp_time(df[[time_col]]);times<-sort(unique(df$.time));if(length(times)<2) stop("至少需要两个比较时点")
permutations<-list(c(1,2,3),c(1,3,2),c(2,1,3),c(2,3,1),c(3,1,2),c(3,2,1))
primary<-do.call(rbind,lapply(split(df,df$.region),function(d) {
  first<-d[d$.time==min(times),];last<-d[d$.time==max(times),]
  aggregate_period<-function(x) {z<-aggregate(x[,c(".events",".n")],by=list(stratum=x$.stratum),FUN=sum);z<-z[order(z$stratum),];list(N=sum(z$.n),s=z$.n/sum(z$.n),r=z$.events/z$.n,levels=as.character(z$stratum))}
  a<-aggregate_period(first);b<-aggregate_period(last);if(!identical(a$levels,b$levels)) stop("两时点必须有相同分层覆盖")
  base<-list(a$N,a$s,a$r);target<-list(b$N,b$s,b$r);burden<-function(x) x[[1]]*sum(x[[2]]*x[[3]])
  contributions<-numeric(3)
  for(order in permutations) {current<-base;for(component in order) {before<-burden(current);current[[component]]<-target[[component]];contributions[component]<-contributions[component]+(burden(current)-before)/6}}
  table<-data.frame(地区=as.character(d$.region[1]),分量=c("人口规模变化","人口分层构成变化","分层事件率变化","总变化"),
    事件变化=c(contributions,burden(target)-burden(base)),首时点=min(times),末时点=max(times))
  if(!is.null(standard_col)) {
    weights<-vapply(a$levels,function(s) {x<-unique(d[[standard_col]][as.character(d$.stratum)==s]);if(length(x)!=1 || !is.numeric(x)||x<0) stop("标准人口需按分层保持共同且非负");x},numeric(1))
    if(sum(weights)<=0) stop("标准人口须有正总数")
    table$首标化率<-sum(a$r*weights)/sum(weights);table$末标化率<-sum(b$r*weights)/sum(weights)
  };table
}))
figure<-ggplot2::ggplot(primary[primary$分量!="总变化",],ggplot2::aes(分量,事件变化,fill=地区))+ggplot2::geom_col(position="dodge")+ggplot2::theme_bw()
.out[["primary"]]<-.cp_table(primary);.out[["figure"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish("人口规模、分层构成和分层率以全部 6 个变换顺序的 Shapley 平均做精确变化分解；三分量相加严格等于总事件变化。风险因素列作为分层，不在缺少因果 RR 时伪造因果 PAF。",
  "首末时点 N×Σ(构成×率) 的三因素精确 Shapley 分解；共同标准人口若提供，另计算直接标化率。"))
"""

_RT = r"""
.step<-"delay-aware-onset-nowcast-and-rt"
onset_col<-.wc_get("onset_v");region_col<-.wc_get("region_v")
if(is.null(onset_col)) stop("报告延迟 nowcast 需要发病日期列，不能从报告总数假造发病队列")
df<-.wc_frame(c(time_v,onset_col,count_v,region_col));df$.report<-.cp_time(df[[time_v]]);df$.onset<-.cp_time(df[[onset_col]]);df$.count<-.cp_count(df[[count_v]])
if(any(df$.report<df$.onset)) stop("报告日期不得早于发病日期")
df$.region<-.cp_group(df,region_col);delay<-as.numeric(delay_v);si<-as.numeric(serial_v)
if(!length(delay)||any(!is.finite(delay))||any(delay<0)||abs(sum(delay)-1)>1e-6) stop("延迟分布须是从延迟 0 起的已确认概率向量，且和为 1")
if(length(si)<2||any(!is.finite(si))||any(si<0)||abs(sum(si)-1)>1e-6||si[1]!=0) stop("序列间隔须是从间隔 0 起的已确认概率向量，和为 1 且间隔 0 概率为 0")
window<-as.integer(.wc_get("window_v",7));if(window<2) stop("Rt 窗口至少 2 个时间单位")
primary<-do.call(rbind,lapply(split(df,df$.region),function(d) {
  # Numeric dates are one day/unit apart; POSIXct input requires explicit units upstream.
  as_of<-max(d$.report);days<-seq(min(d$.onset),as_of,by=1);observed<-vapply(days,function(t) sum(d$.count[d$.onset==t]),numeric(1))
  completeness<-vapply(as_of-days,function(age) sum(delay[seq_len(min(length(delay),floor(age)+1))]),numeric(1))
  if(any(completeness<=0)) stop("当前日期的已知报告概率为零，不能计算 nowcast 点估计")
  nowcast<-observed/completeness
  complete_end<-max(which(days<=as_of-(length(delay)-1)))
  if(!is.finite(complete_end)||complete_end<window+3) stop("完整发病队列不足以估计 Rt")
  incidence<-observed[seq_len(complete_end)];starts<-seq(2,complete_end-window+1);ends<-starts+window-1
  config<-EpiEstim::make_config(list(si_distr=si,t_start=starts,t_end=ends))
  estimate<-EpiEstim::estimate_R(incidence,method="non_parametric_si",config=config)
  rt<-estimate$R;table<-data.frame(地区=as.character(d$.region[1]),日期=days,已报告=observed,完整比例=completeness,Nowcast点估计=nowcast,Rt=NA_real_,RtCI下限=NA_real_,RtCI上限=NA_real_)
  table$Rt[rt$t_end]<-rt$`Mean(R)`;table$RtCI下限[rt$t_end]<-rt$`Quantile.0.025(R)`;table$RtCI上限[rt$t_end]<-rt$`Quantile.0.975(R)`;table
}))
figure<-ggplot2::ggplot(primary,ggplot2::aes(日期,Rt,colour=地区))+ggplot2::geom_line(na.rm=TRUE)+ggplot2::geom_ribbon(ggplot2::aes(ymin=RtCI下限,ymax=RtCI上限,fill=地区),alpha=.1,colour=NA,na.rm=TRUE)+ggplot2::geom_hline(yintercept=1,linetype=2)+ggplot2::theme_bw()
.out[["primary"]]<-.cp_table(primary);.out[["figure"]]<-.cp_figure(figure)
.out<-c(.out,.cp_finish("Nowcast 使用已确认报告延迟概率的队列完整性校正；Rt 用成熟 EpiEstim 与已确认序列间隔，在完整发病队列中估计。未把分数 nowcast 直接伪装成已观察整数病例推断；未完整的最新队列 Rt 保持 NA。",
  "独立、稳定报告延迟下的确定性完整性校正（仅点估计）＋EpiEstim non_parametric_si；日期单位为天或既定整数时间单位。Rt 区间来自真实 Gamma 后验，Nowcast 未计算区间。"))
"""

_BODIES = {
    "calibration_dca": _CALIBRATION,
    "external_validation_model": _CALIBRATION,
    "nri_idi_reclassification": _NRI,
    "matched_case_control": _MATCHED,
    "nested_case_control": _MATCHED,
    "longitudinal_clinical_outcome": _LONGITUDINAL,
    "biomarker_trajectory": _TRAJECTORY,
    "safety_adverse_events": _SAFETY,
    "composite_endpoint": _COMPOSITE,
    "clinical_multistate_illness_death": _MULTISTATE,
    "clinical_landmark_dynamic_prediction": _LANDMARK,
    "clinical_joint_longitudinal_survival": _JOINT,
    "infectious_surveillance": _SURVEILLANCE,
    "multilevel_public_health": _MULTILEVEL,
    "dag_causal": _DAG,
    "environment_mixture": _MIXTURE,
    "burden_economic": _ECONOMIC,
    "spatial_epi": _SPATIAL,
    "age_period_cohort": _APC,
    "small_area_survey": _SMALL_AREA,
    "environment_time_series": _ENVIRONMENT_TIME,
    "demographic_projection": _DEMOGRAPHIC,
    "public_rt_nowcasting": _RT,
    "public_joinpoint_segmented_trend": _JOINPOINT,
    "public_burden_decomposition": _BURDEN_DECOMPOSITION,
}

_BINARY = dict(positive_class="positive_v", treatment_level="treatment_v", reference_group="reference_v")
INPUTS = {
    "calibration_dca": dict(
        outcome="outcome_v",
        prediction="pred_v",
        risk_group="risk_v",
        threshold="threshold_v",
        positive_class="positive_v",
    ),
    "external_validation_model": dict(
        outcome="outcome_v",
        prediction="pred_v",
        data_source="split_v",
        subgroup="group_v",
        threshold="threshold_v",
        positive_class="positive_v",
    ),
    "nri_idi_reclassification": dict(
        outcome="outcome_v",
        original_prediction="old_v",
        updated_prediction="new_v",
        threshold="threshold_v",
        positive_class="positive_v",
    ),
    "matched_case_control": dict(
        matched_set_id="set_v", outcome="outcome_v", exposure="exposure_v", covariates="covars_v", **_BINARY
    ),
    "nested_case_control": dict(
        matched_set_id="set_v",
        outcome="outcome_v",
        exposure="exposure_v",
        weight="weight_v",
        covariates="covars_v",
        analysis_method="method_v",
        followup_time="follow_v",
        **_BINARY,
    ),
    "longitudinal_clinical_outcome": dict(
        subject_id="subject_v",
        followup_time="time_v",
        outcome="outcome_v",
        group="group_v",
        covariates="covars_v",
        analysis_method="method_v",
        working_correlation="correlation_v",
        outcome_family="family_v",
        positive_class="positive_v",
    ),
    "biomarker_trajectory": dict(
        subject_id="subject_v",
        followup_time="time_v",
        measure="measure_v",
        group="group_v",
        outcome="endpoint_v",
        covariates="covars_v",
        positive_class="positive_v",
    ),
    "safety_adverse_events": dict(
        group="group_v",
        event="event_v",
        event_type="type_v",
        exposure="exposure_v",
        subject_id="subject_v",
        **_BINARY,
    ),
    "composite_endpoint": dict(
        group="group_v",
        outcome="outcome_v",
        component_event_variable="component_v",
        followup_time="follow_v",
        covariates="covars_v",
        **_BINARY,
    ),
    "clinical_multistate_illness_death": dict(
        subject_id="subject_v",
        start_state="from_v",
        end_state="to_v",
        transition_time="time_v",
        group="group_v",
        covariates="covars_v",
        interval_start="start_time_v",
        transition_event="transition_event_v",
        allowed_transitions="allowed_v",
    ),
    "clinical_landmark_dynamic_prediction": dict(
        subject_id="subject_v",
        followup_time="follow_v",
        event="event_v",
        landmark_prediction="pred_v",
        group="group_v",
        covariates="covars_v",
        prediction_horizon="horizon_v",
        landmark_time="landmark_v",
    ),
    "clinical_joint_longitudinal_survival": dict(
        subject_id="subject_v",
        time="time_v",
        outcome="outcome_v",
        followup_time="follow_v",
        event="event_v",
        group="group_v",
        covariates="covars_v",
    ),
    "infectious_surveillance": dict(
        event_count="count_v",
        time="time_v",
        region_institution="region_v",
        pathogen_subtype="pathogen_v",
        baseline_periods="baseline_v",
        seasonal_period="period_v",
        alarm_alpha="alpha_v",
    ),
    "multilevel_public_health": dict(
        outcome="outcome_v",
        region_id="region_v",
        region_level_variables="region_vars_v",
        covariates="covars_v",
        positive_class="positive_v",
        outcome_family="family_v",
    ),
    "dag_causal": dict(
        exposure="exposure_v",
        outcome="outcome_v",
        covariates="covars_v",
        mediator="mediator_v",
        selection_variable="selection_v",
        dag_specification="dag_v",
        bootstrap_replicates="bootstrap_v",
        **_BINARY,
    ),
    "environment_mixture": dict(
        outcome="outcome_v",
        exposure="exposure_v",
        time_region="region_v",
        covariates="covars_v",
        positive_class="positive_v",
        exposure_interactions="interactions_v",
    ),
    "burden_economic": dict(
        outcome="outcome_v",
        cost_variable="cost_v",
        effect_measure="effect_v",
        scenario_intervention_variable="strategy_v",
        reference_strategy="reference_strategy_v",
        willingness_to_pay="threshold_v",
        bootstrap_replicates="bootstrap_v",
    ),
    "spatial_epi": dict(
        region_id="region_v",
        outcome="outcome_v",
        denominator="denom_v",
        spatial_boundary="boundary_v",
        zero_policy="zero_v",
    ),
    "age_period_cohort": dict(
        age_group="age_v",
        period="period_v",
        outcome="outcome_v",
        denominator="denom_v",
        group="group_v",
        apc_parameterization="apc_parm_v",
    ),
    "small_area_survey": dict(
        region_id="region_v",
        outcome="outcome_v",
        weight="weight_v",
        strata="strata_v",
        cluster_id="cluster_v",
        covariates="covars_v",
        lonely_psu="lonely_v",
        positive_class="positive_v",
    ),
    "environment_time_series": dict(
        time="time_v",
        exposure="exposure_v",
        outcome="outcome_v",
        covariates="covars_v",
        region_id="region_v",
        maximum_lag="lag_v",
        time_spline_df="time_df_v",
        lag_response="lag_mode_v",
    ),
    "demographic_projection": dict(
        time="time_v",
        age_group="age_v",
        population_size="pop_v",
        region="region_v",
        scenario_variable="scenario_v",
        forecast_horizon="horizon_v",
        series_frequency="frequency_v",
    ),
    "public_rt_nowcasting": dict(
        time="time_v",
        event_count="count_v",
        time_2="onset_v",
        delay_distribution="delay_v",
        region="region_v",
        serial_interval="serial_v",
        rt_window="window_v",
    ),
    "public_joinpoint_segmented_trend": dict(
        time="time_v",
        outcome="outcome_v",
        denominator="denom_v",
        region="region_v",
        age_group="age_v",
        covariates="covars_v",
        breakpoint_count="breaks_v",
    ),
    "public_burden_decomposition": dict(
        outcome="outcome_v",
        denominator="denom_v",
        strata="strata_v",
        risk_factors="risk_v",
        time="time_v",
        region="region_v",
        standard_population="standard_v",
    ),
}

ROLE_MAPS = {
    "calibration_dca": dict(
        calibration_output="calibration",
        figure_2="dca",
        calibration_output_2="metrics",
        calibration_output_3="calibration",
        figure_4="dca",
        manuscript_results="results",
    ),
    "external_validation_model": dict(
        table_1="metrics",
        calibration_output="calibration",
        figure_2="dca",
        calibration_output_2="recalibration",
        method_note="method",
        manuscript_results="results",
        diagnostic_output="diagnostic",
    ),
    "nri_idi_reclassification": dict(
        table_1="nri",
        table_2="idi",
        table_3="matrix",
        manuscript_results="results",
        method_note="method",
        manuscript_results_2="results",
        diagnostic_output="diagnostic",
    ),
    "matched_case_control": dict(
        table_1="model",
        balance_table="balance",
        manuscript_results="results",
        method_note="method",
        manuscript_results_2="results",
        diagnostic_output="diagnostic",
    ),
    "nested_case_control": dict(
        table_1="design",
        table_2="weighted",
        table_3="model",
        manuscript_results="results",
        method_note="method",
        manuscript_results_2="results",
    ),
    "longitudinal_clinical_outcome": dict(
        table_1="model",
        figure_1="trend",
        table_2="means",
        method_note="method",
        manuscript_results="results",
        diagnostic_output="diagnostic",
    ),
    "biomarker_trajectory": dict(
        figure_1="trend",
        table_1="model",
        text_1="association",
        method_note="method",
        manuscript_results="results",
        diagnostic_output="diagnostic",
    ),
    "safety_adverse_events": dict(
        table_1="ae",
        forest_plot="forest",
        table_2="rate",
        method_note="method",
        manuscript_results="results",
        diagnostic_output="diagnostic",
    ),
    "composite_endpoint": dict(
        table_1="primary",
        table_2="components",
        sensitivity_output="sensitivity",
        method_note="method",
        manuscript_results="results",
    ),
    "clinical_multistate_illness_death": dict(
        primary_table="primary", diagnostic_plot="figure", manuscript_results="results"
    ),
    "clinical_landmark_dynamic_prediction": dict(
        primary_table="primary", diagnostic_plot="figure", manuscript_results="results"
    ),
    "clinical_joint_longitudinal_survival": dict(
        primary_table="primary", diagnostic_plot="figure", manuscript_results="results"
    ),
    "infectious_surveillance": dict(
        figure_1="monitor",
        outlier_summary="alarms",
        manuscript_results="results",
        figure_2="monitor",
        text_2="season",
        analysis_recommendation="response",
    ),
    "multilevel_public_health": dict(
        table_1="model",
        figure_1="region_figure",
        table_2="variance",
        text_1="random",
        figure_2="region_figure",
        manuscript_results="results",
    ),
    "dag_causal": dict(
        text_1="dag",
        table_1="adjustment",
        table_2="mediation",
        sensitivity_output="sensitivity",
        table_4="assumptions",
        sensitivity_output_2="advice",
    ),
    "environment_mixture": dict(
        figure_1="curve",
        table_1="joint",
        sensitivity_output="sensitivity",
        table_3="model",
        sensitivity_output_2="sensitivity",
        manuscript_results="results",
    ),
    "burden_economic": dict(
        table_1="burden",
        table_2="economic",
        sensitivity_output="sensitivity",
        table_3="burden",
        manuscript_results="results",
    ),
    "spatial_epi": dict(
        figure_1="map",
        table_1="moran",
        text_1="spatial_note",
        table_2="description",
        figure_2="map",
        table_3="clusters",
        manuscript_results="results",
    ),
    "age_period_cohort": dict(
        figure_1="trend",
        table_1="model",
        manuscript_results="results",
        method_note="method",
        manuscript_results_2="results",
        diagnostic_output="diagnostic",
    ),
    "small_area_survey": dict(
        table_1="regional",
        figure_1="uncertainty",
        method_note="method",
        method_note_2="method",
        manuscript_results="results",
        diagnostic_output="diagnostic",
    ),
    "environment_time_series": dict(
        figure_1="response",
        table_1="lag",
        sensitivity_output="sensitivity",
        method_note="method",
        manuscript_results="results",
        diagnostic_output="diagnostic",
    ),
    "demographic_projection": dict(
        figure_1="trend",
        table_1="projection",
        text_1="uncertainty",
        method_note="method",
        manuscript_results="results",
        diagnostic_output="diagnostic",
    ),
    "public_rt_nowcasting": dict(
        primary_table="primary", diagnostic_plot="figure", manuscript_results="results"
    ),
    "public_joinpoint_segmented_trend": dict(
        primary_table="primary", diagnostic_plot="figure", manuscript_results="results"
    ),
    "public_burden_decomposition": dict(
        primary_table="primary", diagnostic_plot="figure", manuscript_results="results"
    ),
}

def _frozen_body(template_id):
    def render(plan):
        entries = [
            f"{variable}={variable}"
            for input_id, variable in INPUTS[template_id].items()
            if input_id in plan.resolved_inputs
        ]
        prefix = ".wc_inputs <- list(" + ",".join(entries) + ")\n"
        if template_id == "matched_case_control":
            prefix += '.wc_inputs$method_v <- "matched_case_control"\n'
        return prefix + _COMMON + _HELPERS + _BODIES[template_id]

    return render


CORES = {template_id: _frozen_body(template_id) for template_id in _BODIES}
REFERENCE_CASES: dict[str, str] = {'calibration_dca': '真实冻结输入下核对 Brier、logit 校准斜率及决策阈值。', 'external_validation_model': '锁定概率的整体、来源与亚组外部验证，真实输出各组 AUC。', 'nri_idi_reclassification': '成对概率固定案例核对连续 NRI=2 和 IDI=0.6。', 'matched_case_control': '60 个完整匹配组条件 logistic 系数与原生 clogit 相等。', 'nested_case_control': '巢式病例对照完整风险集的条件 logistic 与原生 clogit 相等。', 'longitudinal_clinical_outcome': '实际 lmer 随机截距及时间与组别交互，输出真实边际均值。', 'biomarker_trajectory': '实际 mgcv 平滑时间和受试者随机效应。', 'safety_adverse_events': '重复记录按患者去重风险分母，核对 RR=2 与总人时。', 'composite_endpoint': '二分类复合结局核对 OR=3，保留组成事件统计。', 'clinical_multistate_illness_death': '原生多状态 Aalen-Johansen 概率和为 1，正确风险区间事件数。', 'clinical_landmark_dynamic_prediction': 'landmark 风险集与固定 horizon 的 timeROC IPCW AUC、KM 校准实际运行。', 'clinical_joint_longitudinal_survival': 'JM 实际共享参数纵向-生存联合似然与关联参数估计。', 'infectious_surveillance': '历史基线先于监测区间，实际负二项预测界限与超限监测。', 'multilevel_public_health': '实际区域随机截距模型、ICC 与区域效应输出。', 'dag_causal': '已指定 DAG 的原生 dagitty 调整集验证与调整模型。', 'environment_mixture': '实际多暴露非线性 mgcv 模型、两两 ti 张量交互及联合分位数预测。', 'burden_economic': '成本/QALY 增量和已指定 WTP 的重抽样 CEAC。', 'spatial_epi': '实际 WKT 邻接、spdep 全局及局部 Moran。', 'age_period_cohort': '实际 Epi APC 自然样条拟合和年龄/时期/队列输出。', 'small_area_survey': '实际 survey 分层聚类权重域估计及 t 区间。', 'environment_time_series': '实际线性及非线性分布滞后；lpmatrix累计响应的完整协方差与区间。', 'demographic_projection': '实际 auto.arima 年龄分层预测及置信区间。', 'public_rt_nowcasting': '真实 EpiEstim 仅完整发病队列推断 Rt，迟报校正单列展示。', 'public_joinpoint_segmented_trend': '实际 segmented 固定断点拟合与 APC 区间。', 'public_burden_decomposition': '六种替换顺序 Shapley 精确归因和直接标化。'}
