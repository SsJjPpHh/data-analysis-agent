"""Clinical fixed workflows, connected to the existing R execution chain."""

_HELPERS = r'''
.cl_opt <- function(name,default=NULL) if(exists(name,inherits=TRUE)) get(name,inherits=TRUE) else default
.cl_columns <- function(cols) {
 cols <- unique(as.character(cols)); if(length(setdiff(cols,names(dataset)))) stop("分析字段不存在：",paste(setdiff(cols,names(dataset)),collapse=","))
 df <- as.data.frame(dataset[,cols,drop=FALSE]); df[stats::complete.cases(df),,drop=FALSE]
}
.cl_numeric <- function(x,label) { if(!is.numeric(x) || any(!is.finite(x))) stop(label,"必须是有限数值"); as.numeric(x) }
.cl_event <- function(x) { v <- as.character(x); if(any(!v %in% c("0","1"))) stop("事件编码必须为0=删失、1=事件"); as.numeric(v) }
.cl_formula <- function(lhs,rhs) stats::as.formula(paste(lhs,"~",rhs),env=environment(survival::coxph))
.cl_coef <- function(fit,exponentiate=FALSE) {
 cf <- stats::coef(fit); se <- sqrt(diag(stats::vcov(fit)))
 if(any(!is.finite(cf)) || any(!is.finite(se))) stop("模型系数或方差无效")
 critical <- if(inherits(fit,"lm") && !inherits(fit,"glm")) stats::qt(.975,stats::df.residual(fit)) else stats::qnorm(.975)
 transform <- if(exponentiate) exp else identity
 data.frame(变量=names(cf),效应=transform(cf),CI下限=transform(cf-critical*se),CI上限=transform(cf+critical*se),check.names=FALSE)
}
.cl_curve <- function(fit) {
 s <- summary(fit,censored=TRUE)
 data.frame(时间=s$time,生存=s$surv,组=if(is.null(s$strata)) "总体" else as.character(s$strata),check.names=FALSE)
}
.cl_covariates <- function() as.character(.cl_opt("covars_v",character(0)))
.cl_time_unit <- function() {
 if(!exists("unit_v",inherits=TRUE)) return("")
 unit <- .cl_opt("unit_v",NULL)
 if(!is.character(unit) || length(unit)!=1L || is.na(unit) || !nzchar(trimws(unit))) stop("时间单位必须为非空字符串标量")
 unit
}
.cl_time_axis <- function(unit) if(nzchar(unit)) paste0("时间（",unit,"）") else "时间"
.cl_unit_note <- function(unit) if(nzchar(unit)) paste0("时间单位：",unit,"；沿用输入时间数值，未进行单位换算。") else ""
.cl_rmst_units <- function(table,unit) {
 if(!nzchar(unit)) return(table)
 labels <- as.character(table[[1]])
 difference <- grepl("RMST",labels,fixed=TRUE) & grepl("-",labels,fixed=TRUE)
 ratio <- grepl("RMST|RMTL",labels) & grepl("/",labels,fixed=TRUE)
 if(any(!difference & !ratio)) stop("RMST输出指标无法可靠标注单位")
 table$单位 <- ifelse(difference,unit,"无量纲")
 table
}
'''

_EXTENDED = _HELPERS + r'''
.step <- "extended-cox"
.unit <- .cl_time_unit()
.method <- as.character(.cl_opt("method_v","time_interaction"))
.covs <- .cl_covariates(); .strata <- as.character(.cl_opt("strata_v",character(0)))
.id <- as.character(.cl_opt("subject_v",character(0))); .start <- as.character(.cl_opt("start_v",character(0)))
.df <- .cl_columns(c(followup_v,event_v,tdep_v,.covs,.strata,.id,.start))
.df$.time <- .cl_numeric(.df[[followup_v]],"随访时间"); .df$.event <- .cl_event(.df[[event_v]])
.df$.x <- .cl_numeric(.df[[tdep_v]],"时间依赖变量")
if(any(.df$.time<0) || sum(.df$.event)<2) stop("随访时间或事件数不支持拟合")
.rhs <- c(".x",sprintf("`%s`",.covs))
if(length(.strata)) { .df$.stratum <- factor(.df[[.strata]]); .rhs <- c(.rhs,"strata(.stratum)") }
if(.method=="counting_process") {
 if(!length(.start) || !length(.id)) stop("计数过程需要起始时间与受试者ID")
 .df$.start <- .cl_numeric(.df[[.start]],"起始时间"); .df$.id <- .df[[.id]]
 if(any(.df$.start<0 | .df$.start>=.df$.time)) stop("必须满足0<=起始时间<结束时间")
 for(g in split(.df,.df$.id)) { g <- g[order(g$.start),]; if(nrow(g)>1 && any(g$.start[-1]<g$.time[-nrow(g)])) stop("个体观测区间重叠") }
 .fit <- survival::coxph(.cl_formula("Surv(.start,.time,.event)",paste(c(.rhs,"cluster(.id)"),collapse="+")),data=.df,x=TRUE)
 .base <- survival::coxph(.cl_formula("Surv(.start,.time,.event)",paste(c(sprintf("`%s`",.covs),if(length(.strata)) "strata(.stratum)" else "1"),collapse="+")),data=.df)
 .mt <- .cl_coef(.fit,TRUE)
 .effect <- data.frame(时间=sort(unique(.df$.time)),HR=exp(stats::coef(.fit)[".x"]),check.names=FALSE)
} else if(.method=="time_interaction") {
 .base <- survival::coxph(.cl_formula("Surv(.time,.event)",paste(.rhs,collapse="+")),data=.df,x=TRUE)
 .fit <- survival::coxph(.cl_formula("Surv(.time,.event)",paste(c(.rhs,"tt(.x)"),collapse="+")),data=.df,
   tt=function(x,t,...) x*log1p(t))
 .mt <- .cl_coef(.fit,TRUE); .grid <- seq(0,max(.df$.time),length.out=100)
 .effect <- data.frame(时间=.grid,HR=exp(stats::coef(.fit)[".x"]+stats::coef(.fit)["tt(.x)"]*log1p(.grid)),check.names=FALSE)
} else stop("未知扩展生存方法")
.ph <- tryCatch(survival::cox.zph(if(.method=="time_interaction") .base else .fit),error=function(e) NULL)
.comparison <- data.frame(模型=c("比较模型","扩展模型"),AIC=c(stats::AIC(.base),stats::AIC(.fit)),check.names=FALSE)
.fig <- ggplot2::ggplot(.effect,ggplot2::aes(x=时间,y=HR))+ggplot2::geom_line()+ggplot2::geom_hline(yintercept=1,linetype=2)+ggplot2::labs(x=.cl_time_axis(.unit))+ggplot2::theme_bw()
.diag_status[["PH 假设检查"]] <- if(is.null(.ph)) "not_assessed" else if(.ph$table["GLOBAL","p"]<.05) "warning" else "passed"
.diag_status[["模型比较"]] <- "passed"
.out[["model"]] <- list(kind="table",payload=.mt,title="实际扩展Cox系数")
.out[["effect"]] <- list(kind="figure",payload=.fig,title="时间与协变量效应")
.out[["comparison"]] <- list(kind="table",payload=.comparison,title="实际模型AIC比较")
.out[["notes"]] <- list(kind="text",payload=paste("执行分支：",.method,"；时间交互使用x×log(1+t)，计数过程使用声明的起止区间和个体聚类方差。",.cl_unit_note(.unit)),title="模型说明")
'''

_COMPETING = _HELPERS + r'''
.step <- "competing-risk"
.unit <- .cl_time_unit()
.group_col <- as.character(.cl_opt("group_v",character(0)))
.covs <- unique(c(.cl_covariates(),.group_col)); .df <- .cl_columns(c(followup_v,event_type_v,.covs))
.time <- .cl_numeric(.df[[followup_v]],"随访时间"); if(any(.time<0)) stop("随访时间不能为负")
.raw <- as.character(.df[[event_type_v]]); .target <- as.character(target_v)
.censor <- as.character(.cl_opt("censor_v","0")); .competing <- as.character(.cl_opt("comp_v",setdiff(unique(.raw),c(.censor,.target))))
if(.target==.censor || .target %in% .competing || .censor %in% .competing) stop("目标、竞争与删失编码必须互不相同")
if(any(!.raw %in% c(.target,.censor,.competing))) stop("存在未声明的事件编码")
.status <- ifelse(.raw==.target,1L,ifelse(.raw==.censor,0L,2L))
if(sum(.status==1)<2) stop("目标事件不足")
.group <- if(length(.group_col)) droplevels(factor(.df[[.group_col]])) else factor(rep("总体",nrow(.df)))
if(length(.group_col) && nlevels(.group)<2L) stop("Gray组间比较需要至少两个组")
.ci <- cmprsk::cuminc(.time,.status,group=.group,cencode=0)
.curves <- lapply(names(.ci)[vapply(.ci,is.list,logical(1))],function(nm) data.frame(
 时间=.ci[[nm]]$time,CIF=.ci[[nm]]$est,
 CI下限=pmax(0,.ci[[nm]]$est-1.959964*sqrt(.ci[[nm]]$var)),
 CI上限=pmin(1,.ci[[nm]]$est+1.959964*sqrt(.ci[[nm]]$var)),事件=nm,check.names=FALSE))
.curve <- do.call(rbind,.curves)
if(nzchar(.unit)) .curve$时间单位 <- .unit
.fig <- ggplot2::ggplot(.curve,ggplot2::aes(x=时间,y=CIF,colour=事件))+ggplot2::geom_step()+ggplot2::labs(x=.cl_time_axis(.unit))+ggplot2::theme_bw()
.fg <- NULL; .cause_cox <- NULL
if(length(.covs)) {
 .x <- stats::model.matrix(stats::reformulate(.covs),.df)[,-1,drop=FALSE]
 .fg <- cmprsk::crr(.time,.status,.x,failcode=1,cencode=0)
 if(!isTRUE(.fg$converged)) stop("Fine-Gray模型未收敛")
 .se <- sqrt(diag(.fg$var)); if(any(!is.finite(.fg$coef)) || any(!is.finite(.se))) stop("Fine-Gray模型无效")
 .fg_table <- data.frame(变量=names(.fg$coef),SHR=exp(.fg$coef),CI下限=exp(.fg$coef-1.96*.se),CI上限=exp(.fg$coef+1.96*.se),check.names=FALSE)
 .df$.time <- .time; .df$.target <- as.integer(.status==1L)
 .cause_cox <- survival::coxph(.cl_formula("Surv(.time,.target)",paste(sprintf("`%s`",.covs),collapse="+")),data=.df)
 if(any(!is.finite(stats::coef(.cause_cox))) || any(!is.finite(stats::vcov(.cause_cox)))) stop("原因特异Cox系数不可估计")
 .cause_table <- .cl_coef(.cause_cox,TRUE)
}
.counts <- data.frame(编码=c(.censor,.target,paste(.competing,collapse="/")),类型=c("删失","目标","竞争"),
  人数=c(sum(.status==0),sum(.status==1),sum(.status==2)),check.names=FALSE)
.diag_status[["事件编码检查"]] <- "passed"
.diag_status[["竞争事件分布检查"]] <- if(sum(.status==2)<5) "warning" else "passed"
.diag_status[["模型适配性检查"]] <- if(is.null(.fg)) "not_assessed" else "passed"
.out[["cif"]] <- list(kind="figure",payload=.fig,title="累积发生率CIF")
if(!is.null(.fg)) {
 .out[["fine_gray"]] <- list(kind="table",payload=.fg_table,title="Fine-Gray亚分布风险")
 .out[["cause_cox"]] <- list(kind="table",payload=.cause_table,title="目标事件的原因特异Cox对照")
}
.out[["cif_table"]] <- list(kind="table",payload=.curve,title="实际CIF及点估计Wald区间")
if(!is.null(.ci$Tests)) .out[["gray_tests"]] <- list(kind="table",payload=data.frame(事件=rownames(.ci$Tests),.ci$Tests,check.names=FALSE),title="分组Gray检验")
.out[["counts"]] <- list(kind="table",payload=.counts,title="实际事件编码与计数")
.out[["notes"]] <- list(kind="text",payload=paste("竞争风险结果：cmprsk CIF；目标编码",.target,"，删失编码",.censor,"。目标事件",sum(.status==1),"例，竞争事件",sum(.status==2),"例。CIF置信区间为方差点估计的截断Wald区间。分组时执行Gray检验；有协变量或分组时分别报告crr的SHR与原因特异Cox的HR，二者不互相替代。局限：适用独立个体的已确认事件编码和固定协变量，未证明独立删失或模型假设。下一步核对事件判定、随访支持和模型诊断。",.cl_unit_note(.unit)),title="竞争风险说明")
'''

_TDROC = _HELPERS + r'''
.step <- "ipcw-time-roc"
suppressPackageStartupMessages(library("survival"))
.df <- .cl_columns(c(followup_v,event_v,pred_v)); .time <- .cl_numeric(.df[[followup_v]],"随访时间")
.event <- .cl_event(.df[[event_v]]); .score <- .cl_numeric(.df[[pred_v]],"预测分数")
.times <- as.numeric(strsplit(as.character(teval_v),"[,，;； ]+")[[1]])
if(any(!is.finite(.times) | .times<=0) || any(.times>max(.time)) || any(.time<0)) stop("评估时间必须在随访支持范围内")
.times <- sort(unique(.times)); .direction <- as.character(.cl_opt("direction_v","higher_positive"))
if(!.direction %in% c("higher_positive","lower_positive")) stop("预测方向不受支持")
.marker <- if(.direction=="lower_positive") -.score else .score
.roc <- timeROC::timeROC(T=.time,delta=.event,marker=.marker,cause=1,weighting="marginal",times=.times,iid=TRUE)
.indices <- match(.times,.roc$times)
.auc <- .roc$AUC[.indices]; if(any(!is.finite(.auc))) stop("评估时间的病例、对照或删失分布不支持AUC")
.ci_all <- stats::confint(.roc,level=.95)$CI_AUC
.ci_indices <- match(names(.roc$AUC)[.indices],rownames(.ci_all))
if(anyNA(.ci_indices)) stop("请求的时间点没有可用AUC置信区间")
.ci <- .ci_all[.ci_indices,,drop=FALSE]/100
.auc_table <- data.frame(评估时间=.times,AUC=.auc,CI下限=.ci[,1],CI上限=.ci[,2],check.names=FALSE)
.roc_rows <- do.call(rbind,lapply(seq_along(.indices),function(i) data.frame(FPR=.roc$FP[,.indices[i]],TPR=.roc$TP[,.indices[i]],评估时间=as.character(.times[i]),check.names=FALSE)))
.fig <- ggplot2::ggplot(.roc_rows,ggplot2::aes(x=FPR,y=TPR,colour=评估时间))+ggplot2::geom_path()+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+ggplot2::theme_bw()
.trend <- ggplot2::ggplot(.auc_table,ggplot2::aes(x=评估时间,y=AUC))+ggplot2::geom_point()+ggplot2::geom_errorbar(ggplot2::aes(ymin=CI下限,ymax=CI上限),width=0)+ggplot2::theme_bw()
.scale <- as.character(.cl_opt("scale_v","risk_score"))
.brier <- data.frame(说明="风险分数没有概率标度；本轮不计算Brier分数")
if(.scale=="risk_probability") {
 if(length(.times)!=1L || any(.score<0|.score>1) || .direction!="higher_positive") stop("Brier需要单时间点0到1风险概率，方向为higher_positive")
 .gfit <- survival::survfit(survival::Surv(.time,1-.event)~1)
 .g <- function(t,left=FALSE) vapply(t,function(z) {j <- which(if(left) .gfit$time<z else .gfit$time<=z); if(length(j)) utils::tail(.gfit$surv[j],1) else 1},numeric(1))
 .case <- .time<=.times & .event==1; .control <- .time>.times
 .denom <- ifelse(.case,.g(.time,TRUE),.g(rep(.times,length(.time))))
 if(any(.denom[.case|.control]<=0)) stop("删失生存概率为零，Brier不受支持")
 .weights <- ifelse(.case|.control,1/.denom,0)
 .brier <- data.frame(评估时间=.times,IPCW_Brier=mean(.weights*(as.numeric(.case)-.score)^2),check.names=FALSE)
} else if(.scale!="risk_score") stop("未知预测标度")
.diag_status[["事件数检查"]] <- if(any(vapply(.times,function(t) sum(.time<=t&.event==1)<10,logical(1)))) "warning" else "passed"
.diag_status[["时间点样本量检查"]] <- "passed"
.diag_status[["删失机制提示"]] <- "not_assessed"
.out[["roc"]] <- list(kind="figure",payload=.fig,title="IPCW时间依赖ROC")
.out[["auc"]] <- list(kind="table",payload=.auc_table,title="实际时间依赖AUC与区间")
.out[["brier"]] <- list(kind="table",payload=.brier,title="概率标度的IPCW Brier")
.out[["trend"]] <- list(kind="figure",payload=.trend,title="实算AUC与时间")
.out[["notes"]] <- list(kind="text",payload="调用timeROC的边际IPCW估计，早期删失者不被当作无事件对照；区间来自iid推断。Brier仅用于单时间点风险概率。",title="时间ROC说明")
'''

_RMST = _HELPERS + r'''
.step <- "rmst"
.unit <- .cl_time_unit()
.covs <- .cl_covariates(); .df <- .cl_columns(c(followup_v,event_v,group_v,.covs))
.time <- .cl_numeric(.df[[followup_v]],"随访时间"); .event <- .cl_event(.df[[event_v]])
.group <- factor(.df[[group_v]]); if(nlevels(.group)!=2) stop("RMST比较需要两个组")
.reference <- as.character(.cl_opt("reference_v",levels(.group)[1])); if(!.reference %in% levels(.group)) stop("参照组不存在")
.arm <- as.integer(as.character(.group)!=.reference)
.tau <- as.numeric(.cl_opt("tau_v",min(tapply(.time,.arm,max))))
if(length(.tau)!=1 || !is.finite(.tau) || .tau<=0 || .tau>min(tapply(.time,.arm,max)) || any(.time<0)) stop("截断时间超出两组共同随访支持范围")
.x <- if(length(.covs)) stats::model.matrix(stats::reformulate(.covs),.df)[,-1,drop=FALSE] else NULL
.rmst <- survRM2::rmst2(.time,.event,.arm,tau=.tau,covariates=.x)
.matrix <- if(is.null(.x)) .rmst$unadjusted.result else .rmst$adjusted.result
.table <- .cl_rmst_units(data.frame(指标=rownames(.matrix),.matrix,check.names=FALSE,row.names=NULL),.unit)
.km <- survival::survfit(survival::Surv(.time,.event)~.group)
.curve <- .cl_curve(.km); .curve <- .curve[.curve$时间<=.tau,,drop=FALSE]
.fig <- ggplot2::ggplot(.curve,ggplot2::aes(x=时间,y=生存,colour=组))+ggplot2::geom_step()+ggplot2::geom_vline(xintercept=.tau,linetype=2)+ggplot2::labs(x=.cl_time_axis(.unit))+ggplot2::theme_bw()
.sensitivity <- survRM2::rmst2(.time,.event,.arm,tau=.tau*.75,covariates=.x)
.diag_status[["截断时间可支持性检查"]] <- "passed"
.diag_status[["RMST 时间窗敏感性分析"]] <- "passed"
.out[["rmst"]] <- list(kind="table",payload=.table,title="RMST差值、比值与RMTL比值")
.out[["figure"]] <- list(kind="figure",payload=.fig,title="生存曲线与RMST截断时间")
.out[["notes"]] <- list(kind="text",payload=paste("survRM2 RMST；参照组",.reference,"，tau=",.tau,if(nzchar(.unit)) .unit else "","；另在0.75×tau重算敏感性结果。",.cl_unit_note(.unit),if(nzchar(.unit)) paste0("RMST差值及其区间的单位为",.unit,"；RMST与RMTL比值均为无量纲。") else ""),title="RMST说明")
.sensitivity_matrix <- if(is.null(.x)) .sensitivity$unadjusted.result else .sensitivity$adjusted.result
baa_table("rmst_tau_sensitivity",.cl_rmst_units(data.frame(指标=rownames(.sensitivity_matrix),.sensitivity_matrix,check.names=FALSE,row.names=NULL),.unit),metadata=list(role="rmst_tau_sensitivity",title="截断时间敏感性"))
'''

_RECURRENT = _HELPERS + r'''
.step <- "recurrent-intervals"
.covs <- .cl_covariates(); .start_col <- as.character(.cl_opt("start_v",character(0)))
.order_col <- as.character(.cl_opt("order_v",character(0))); .terminal <- as.character(.cl_opt("terminal_v",character(0)))
.df <- .cl_columns(c(subject_v,followup_v,event_v,group_v,.covs,.start_col,.order_col,.terminal))
.df$.id <- as.character(.df[[subject_v]]); .df$.stop <- .cl_numeric(.df[[followup_v]],"事件/结束时间")
.df$.event <- .cl_event(.df[[event_v]]); .df$.group <- factor(.df[[group_v]])
.df <- .df[order(.df$.id,.df$.stop),,drop=FALSE]
.df$.start <- if(length(.start_col)) .cl_numeric(.df[[.start_col]],"起始时间") else ave(.df$.stop,.df$.id,FUN=function(x)c(0,head(x,-1)))
if(any(.df$.start<0 | .df$.start>=.df$.stop)) stop("复发事件需要有效、不重叠的起止区间")
for(g in split(.df,.df$.id)) if(nrow(g)>1 && any(g$.start[-1]<g$.stop[-nrow(g)])) stop("个体区间重叠")
if(length(.terminal)) {
 .df$.terminal <- .cl_event(.df[[.terminal]])
 for(g in split(.df,.df$.id)) if(any(g$.terminal==1) && which(g$.terminal==1)[1]!=nrow(g)) stop("终末事件后仍有观测")
 .df$.event[.df$.terminal==1] <- 0
}
.method <- as.character(.cl_opt("method_v","ag"))
.rhs <- c(".group",sprintf("`%s`",.covs),"cluster(.id)")
if(.method=="pwp_total_time") {
 .df$.order <- if(length(.order_col)) .cl_numeric(.df[[.order_col]],"事件序号") else ave(.df$.event,.df$.id,FUN=function(x)cumsum(c(0,head(x,-1)))+1)
 if(any(.df$.order<1 | .df$.order!=floor(.df$.order))) stop("事件序号须为正整数")
 .rhs <- c(.rhs,"strata(.order)")
} else if(.method!="ag") stop("不支持的复发事件方法")
.fit <- survival::coxph(.cl_formula("Surv(.start,.stop,.event)",paste(.rhs,collapse="+")),data=.df,x=TRUE)
.table <- .cl_coef(.fit,TRUE)
.mcf <- do.call(rbind,lapply(levels(.df$.group),function(g) {
 d <- .df[.df$.group==g,,drop=FALSE]; times <- sort(unique(d$.stop[d$.event==1]))
 if(!length(times)) return(data.frame(时间=0,平均累积事件=0,组=g,check.names=FALSE))
 increments <- vapply(times,function(t) sum(d$.stop==t & d$.event==1)/length(unique(d$.id[d$.start<t & d$.stop>=t])),numeric(1))
 data.frame(时间=c(0,times),平均累积事件=c(0,cumsum(increments)),组=g,check.names=FALSE)
}))
.fig <- ggplot2::ggplot(.mcf,ggplot2::aes(x=时间,y=平均累积事件,colour=组))+ggplot2::geom_step()+ggplot2::theme_bw()
.diag_status[["个体内相关检查"]] <- "passed"
.diag_status[["事件计数分布检查"]] <- "passed"
.out[["model"]] <- list(kind="table",payload=.table,title="复发事件Cox与个体聚类方差")
.out[["figure"]] <- list(kind="figure",payload=.fig,title="平均累积事件函数")
.out[["notes"]] <- list(kind="text",payload=paste("执行",.method,"；起止区间按个体核查，方差按个体聚类；终末事件存在时在终末处截尾。未对暴露时间取pmax(1)。"),title="复发事件说明")
'''

CORES = {"extended_survival": _EXTENDED, "competing_risk": _COMPETING,
         "time_dependent_roc": _TDROC, "clinical_rmst_non_ph": _RMST,
         "clinical_recurrent_events": _RECURRENT}
INPUTS = {
 "extended_survival": {"followup_time":"followup_v","event":"event_v","time_dependent_variable":"tdep_v","strata":"strata_v","subject_id":"subject_v","start_time":"start_v","covariates":"covars_v","analysis_method":"method_v","time_unit":"unit_v"},
 "competing_risk": {"followup_time":"followup_v","event_type":"event_type_v","target_event_level":"target_v","competing_event_level":"comp_v","censor_event_level":"censor_v","covariates":"covars_v","group":"group_v","time_unit":"unit_v"},
 "time_dependent_roc": {"followup_time":"followup_v","event":"event_v","prediction":"pred_v","evaluation_time":"teval_v","prediction_scale":"scale_v","score_direction":"direction_v"},
 "clinical_rmst_non_ph": {"followup_time":"followup_v","event":"event_v","group":"group_v","covariates":"covars_v","truncation_time":"tau_v","reference_group":"reference_v","time_unit":"unit_v"},
 "clinical_recurrent_events": {"subject_id":"subject_v","event_time":"followup_v","event":"event_v","group":"group_v","event_order":"order_v","covariates":"covars_v","terminal_event":"terminal_v","start_time":"start_v","analysis_method":"method_v"},
}
ROLE_MAPS = {
 "extended_survival": {"table_1":"model","figure_1":"effect","table_2":"comparison","table_3":"model","text_1":"notes"},
 "competing_risk": {"figure_1":"cif","table_1":"fine_gray","manuscript_results":"notes","figure_2":"cif","table_2":"counts","text_2":"notes","manuscript_results_2":"notes","survival_effect_estimates":"fine_gray","cif_estimates":"cif_table","gray_tests":"gray_tests","cause_specific_cox":"cause_cox","group_fine_gray":"fine_gray","group_cause_specific_cox":"cause_cox"},
 "time_dependent_roc": {"roc_output":"roc","table_1":"auc","table_2":"brier","table_3":"auc","roc_output_2":"trend","table_4":"auc","manuscript_results":"notes","survival_time_dependent_roc_plot":"roc"},
 "clinical_rmst_non_ph": {"primary_table":"rmst","diagnostic_plot":"figure","manuscript_results":"notes"},
 "clinical_recurrent_events": {"primary_table":"model","diagnostic_plot":"figure","manuscript_results":"notes"},
}
REFERENCE_CASES = {
 "extended_survival": "真实 R：时间交互 Cox 的 survival 系数参考；计数过程缺少区间起始字段时拒绝。",
 "competing_risk": "真实 R：cmprsk crr 的 Fine–Gray 系数参考；CSV 生产链验证分组 CIF/Gray、独立原因别 Cox 及未调整 CIF，未提供分组/协变量时不生成占位模型。",
 "time_dependent_roc": "真实 R：timeROC 边际 IPCW AUC 与早期删失；无删失的概率 Brier 与均方误差参考。",
 "clinical_rmst_non_ph": "真实 R：显式 tau、参照方向、协变量调整的 survRM2 参考。",
 "clinical_recurrent_events": "真实 R：实际亚单位时间区间、个体聚类稳健方差及重复时间拒绝。",
}
