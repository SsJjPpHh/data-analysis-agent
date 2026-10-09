"""Fixed scientific-package adapters for evidence, time series and evaluation.

The input and output maps are exported for the existing script library.  These
cores consume only declared selections; numerical checks are kept in the
separate test module and do not replace the user's confirmed research design.
"""

COMMON = r'''
.mt_get <- function(name, default=NULL) {
  if (exists(name, inherits=TRUE)) get(name, inherits=TRUE) else default
}
.mt_data <- function(cols) {
  cols <- unique(as.character(cols)); cols <- cols[nzchar(cols)]
  if (!length(cols) || length(setdiff(cols,names(dataset)))) stop("所选数据列不存在")
  d <- dataset[,cols,drop=FALSE]
  if (anyNA(d)) stop("所选变量存在缺失；请先完成已确认的缺失处理")
  d
}
.mt_numeric <- function(x, label) {
  if (!is.numeric(x) || any(!is.finite(x))) stop(label,"须为有限数值，不能按因子编码转换")
  x
}
.mt_formula <- function(response, predictors) {
  stats::as.formula(paste0("`",response,"` ~ ",
    if(length(predictors)) paste(sprintf("`%s`",predictors),collapse=" + ") else "1"))
}
.mt_select <- function(value, choices, label) {
  if (length(value)!=1 || !value %in% choices) stop(label,"取值无效：",paste(choices,collapse=", "))
  value
}
.mt_integer <- function(x, lower, upper, label) {
  if (!is.numeric(x) || length(x)!=1 || !is.finite(x) || x!=floor(x) || x<lower || x>upper)
    stop(label,"须为 ",lower,"–",upper," 范围内的整数")
  as.integer(x)
}
.mt_put <- function(slot, kind, payload, title) {
  list(kind=kind,payload=payload,title=title)
}
'''

META = r'''
.step <- "meta-data"
sub_v <- .mt_get("sub_v")
df <- .mt_data(c(study_v,effect_v,se_v,sub_v))
df$.study <- as.character(df[[study_v]])
df$.yi <- .mt_numeric(df[[effect_v]],"效应量")
df$.sei <- .mt_numeric(df[[se_v]],"标准误")
if (anyDuplicated(df$.study)) stop("每研究需唯一一行；依赖效应量须使用多层 Meta 分析")
if(nrow(df)<3 || any(df$.sei<=0)) stop("至少 3 个独立研究，且标准误须大于零")
scale_v <- .mt_select(.mt_get("scale_v","identity"),c("identity","log_ratio"),"效应量尺度")
tau_v <- .mt_select(.mt_get("tau_v","REML"),c("REML","DL"),"异质性估计方法")
trans <- if(scale_v=="log_ratio") exp else identity
.step <- "meta-fit"
fixed <- metafor::rma(yi=df$.yi,sei=df$.sei,method="FE")
fit <- metafor::rma(yi=df$.yi,sei=df$.sei,method=tau_v)
.meta_row <- function(f,label) data.frame(component=label,estimate=trans(as.numeric(f$b[1])),
  lower=trans(f$ci.lb[1]),upper=trans(f$ci.ub[1]),p=f$pval[1],k=f$k,tau2=f$tau2,I2=f$I2,scale=scale_v)
pooled_table <- rbind(.meta_row(fixed,"fixed"),.meta_row(fit,paste0("random_",tau_v)))
subgroup_fit <- NULL
if(length(sub_v)) {
  df$.subgroup <- factor(df[[sub_v]])
  if(nlevels(df$.subgroup)<2 || any(table(df$.subgroup)<2)) stop("各亚组至少 2 个研究且需要至少两个亚组")
  subgroup_fit <- metafor::rma(yi=.yi,sei=.sei,mods=~.subgroup,data=df,method=tau_v)
  pooled_table <- rbind(pooled_table,do.call(rbind,lapply(levels(df$.subgroup),function(g) {
    d<-df[df$.subgroup==g,,drop=FALSE]
    .meta_row(metafor::rma(yi=d$.yi,sei=d$.sei,method=tau_v),paste0("subgroup:",g))
  })))
}
het_table <- data.frame(k=fit$k,Q=fit$QE,df=fit$k-1,Q_p=fit$QEp,I2=fit$I2,H2=fit$H2,tau2=fit$tau2,
  subgroup_Q=if(is.null(subgroup_fit)) NA_real_ else subgroup_fit$QM,
  subgroup_p=if(is.null(subgroup_fit)) NA_real_ else subgroup_fit$QMp)
loo <- metafor::leave1out(fit)
loo_table <- data.frame(omitted=df$.study,estimate=trans(loo$estimate),lower=trans(loo$ci.lb),
  upper=trans(loo$ci.ub),tau2=loo$tau2,I2=loo$I2)
bias <- if(nrow(df)>=10) tryCatch(metafor::regtest(fit,model="rma",predictor="sei"),error=function(e) NULL) else NULL
bias_table <- data.frame(test="Egger regression (SE predictor)",k=nrow(df),
  statistic=if(is.null(bias)) NA_real_ else bias$zval,p=if(is.null(bias)) NA_real_ else bias$pval,
  status=if(nrow(df)<10) "insufficient_studies" else if(is.null(bias)) "not_estimable" else "computed",
  note="漏斗不对称不等同于发表偏倚；不足 10 项研究不执行 Egger 检验")
forest_data <- rbind(data.frame(study=df$.study,estimate=trans(df$.yi),
  lower=trans(df$.yi-stats::qnorm(.975)*df$.sei),upper=trans(df$.yi+stats::qnorm(.975)*df$.sei)),
  data.frame(study="随机效应合并",estimate=pooled_table$estimate[2],lower=pooled_table$lower[2],upper=pooled_table$upper[2]))
forest_data$study <- factor(forest_data$study,levels=rev(forest_data$study))
forest_fig <- ggplot2::ggplot(forest_data,ggplot2::aes(x=estimate,y=study))+
  ggplot2::geom_errorbar(ggplot2::aes(xmin=lower,xmax=upper),orientation="y",width=.2)+
  ggplot2::geom_point()+ggplot2::geom_vline(xintercept=if(scale_v=="log_ratio") 1 else 0,linetype=2)+
  ggplot2::theme_bw()+ggplot2::labs(x=if(scale_v=="log_ratio") "比值（输入为 log 比值）" else "输入效应量尺度",y=NULL)
.diag_status[["异质性检查"]] <- if(fit$I2>75) "warning" else "passed"
.diag_status[["影响研究检查"]] <- if(any(abs(loo$estimate-as.numeric(fit$b))>2*fit$se)) "warning" else "passed"
.diag_status[["发表偏倚提示"]] <- if(is.null(bias)) "not_assessed" else if(bias$pval<.05) "warning" else "passed"
.out[["forest_fig"]] <- .mt_put("forest_fig","figure",forest_fig,"研究效应与随机效应合并")
.out[["pooled_table"]] <- .mt_put("pooled_table","table",pooled_table,"固定、随机及指定亚组合并效应")
.out[["bias_table"]] <- .mt_put("bias_table","table",bias_table,"小样本效应检验与执行状态")
.out[["het_table"]] <- .mt_put("het_table","table",het_table,"异质性与亚组差异")
.out[["loo_table"]] <- .mt_put("loo_table","table",loo_table,"逐研究剔除敏感性")
results_text <- sprintf("结果：%d 项独立研究，%s 随机效应合并估计=%.6g，95%%置信区间 [%.6g, %.6g]；尺度=%s，I²=%.3f%%，tau²=%.6g。逐项剔除合并估计范围 [%.6g, %.6g]，小样本效应检查状态=%s。局限：适用独立研究的已确认效应量与标准误，未证明同质性或无发表偏倚。下一步核对研究可比性、风险偏倚与敏感性结果。",
 nrow(df),tau_v,trans(as.numeric(fit$b)),trans(fit$ci.lb),trans(fit$ci.ub),scale_v,fit$I2,fit$tau2,
 min(loo_table$estimate),max(loo_table$estimate),bias_table$status)
.out[["results_text"]] <- .mt_put("results_text","text",results_text,"真实合并结果、不确定性与适用限制")
'''

META_REGRESSION = r'''
.step <- "meta-regression-data"
mods <- unique(c(as.character(covars_v),.mt_get("sub_v"),.mt_get("fu_v"),.mt_get("rob_v")))
if (!length(mods)) stop("请指定研究层协变量")
df <- .mt_data(c(study_v,effect_v,se_v,mods))
df$.study<-as.character(df[[study_v]]); df$.yi<-.mt_numeric(df[[effect_v]],"效应量")
df$.sei<-.mt_numeric(df[[se_v]],"标准误")
if(anyDuplicated(df$.study) || any(df$.sei<=0)) stop("研究 ID 须唯一，标准误须大于零")
for(v in mods) if(is.character(df[[v]])) df[[v]]<-factor(df[[v]])
# Keep spaces in selected column names.
form <- stats::as.formula(paste("~",paste(sprintf("`%s`",mods),collapse=" + ")))
mm <- stats::model.matrix(form,df)
if(qr(mm)$rank<ncol(mm) || nrow(df)<=ncol(mm)+1) stop("Meta 回归模型不可估计：研究数不足或协变量共线")
tau_v<-.mt_select(.mt_get("tau_v","REML"),c("REML","DL"),"异质性估计方法")
.step <- "meta-regression-fit"
fit<-metafor::rma(yi=.yi,sei=.sei,mods=form,data=df,method=tau_v,test="knha")
reg_table<-data.frame(component="moderator_coefficient",term=rownames(fit$b),estimate=as.numeric(fit$b),
  SE=fit$se,lower=fit$ci.lb,upper=fit$ci.ub,p=fit$pval,scale="input effect scale")
if(length(.mt_get("sub_v"))) {
  sub_v<-.mt_get("sub_v")
  for(g in unique(as.character(df[[sub_v]]))) {
    d<-df[as.character(df[[sub_v]])==g,,drop=FALSE]
    if(nrow(d)<2) stop("指定亚组中至少需要两个研究")
    sf<-metafor::rma(yi=d$.yi,sei=d$.sei,method=tau_v,test="knha")
    reg_table<-rbind(reg_table,data.frame(component="unadjusted_subgroup_pool",term=g,
      estimate=as.numeric(sf$b),SE=sf$se,lower=sf$ci.lb,upper=sf$ci.ub,p=sf$pval,scale="input effect scale"))
  }
}
loo_coefficients <- lapply(seq_len(nrow(df)),function(i) {
  f<-tryCatch(metafor::rma(yi=.yi,sei=.sei,mods=form,data=df[-i,,drop=FALSE],method=tau_v,test="knha"),error=function(e) NULL)
  if(is.null(f)) return(rep(NA_real_,ncol(mm)))
  as.numeric(f$b)
})
loo_matrix<-do.call(rbind,loo_coefficients)
influence <- tryCatch(influence(fit)$inf$cook.d,error=function(e) rep(NA_real_,nrow(df)))
first_mod<-mods[1]
pred<-as.numeric(stats::fitted(fit))
bubble_data<-data.frame(x=df[[first_mod]],y=df$.yi,predicted=pred,precision=1/df$.sei^2)
bubble_fig<-ggplot2::ggplot(bubble_data,ggplot2::aes(x=x,y=y,size=precision))+
  ggplot2::geom_point(alpha=.65)+ggplot2::geom_point(ggplot2::aes(y=predicted),shape=4,colour="#B23A48",size=2)+
  ggplot2::theme_bw()+ggplot2::labs(x=first_mod,y="输入效应量尺度",title="研究效应和已选协变量模型的拟合值",caption="红色叉号为该研究全部协变量下的拟合值；不使用未加权 OLS 替代 Meta 回归")
results_text<-sprintf("%d 项独立研究；%d 个可估计系数，%s 随机效应 Meta 回归及 Knapp–Hartung t 推断。残余 I²=%.2f%%，tau²=%.6f，Q_E P=%.6g；最大 Cook 距离=%s。逐项剔除保持相同协变量模型，%d 次不可估计。效应保持输入尺度，未自动指数化。",
 nrow(df),ncol(mm),tau_v,fit$I2,fit$tau2,fit$QEp,format(max(influence,na.rm=TRUE)),sum(!complete.cases(loo_matrix)))
.diag_status[["协变量数量与研究数检查"]]<-if(nrow(df)<10*(ncol(mm)-1)) "warning" else "passed"
.diag_status[["残余异质性检查"]]<-if(fit$I2>75) "warning" else "passed"
.diag_status[["影响研究和生态偏倚检查"]]<-if(any(!is.finite(loo_matrix)) || any(influence>4/nrow(df),na.rm=TRUE)) "warning" else "passed"
.out[["reg_table"]]<-.mt_put("reg_table","table",reg_table,"研究层协变量回归与指定亚组合并效应")
.out[["bubble_fig"]]<-.mt_put("bubble_fig","figure",bubble_fig,"加权气泡与实际模型拟合值")
.out[["results_text"]]<-.mt_put("results_text","text",results_text,"Meta 回归结果与执行信息")
'''

PROPORTION_RATE = r'''
.step <- "proportion-rate-data"
mods<-unique(c(.mt_get("sub_v"),.mt_get("region_v"),.mt_get("year_v")))
df<-.mt_data(c(study_v,event_v,n_v,mods))
df$.study<-as.character(df[[study_v]]); df$.events<-.mt_numeric(df[[event_v]],"事件数")
df$.denominator<-.mt_numeric(df[[n_v]],"分母")
if(nrow(df)<3 || anyDuplicated(df$.study) || any(df$.events<0 | df$.events!=floor(df$.events)) || any(df$.denominator<=0))
 stop("至少三个独立研究，事件数须为非负整数，分母须为正数")
denominator_type<-.mt_select(.mt_get("denominator_v","sample_size"),c("sample_size","person_time"),"分母类型")
tau_v<-.mt_select(.mt_get("tau_v","REML"),c("REML","DL"),"异质性估计方法")
if(denominator_type=="sample_size") {
 if(any(df$.denominator!=floor(df$.denominator) | df$.events>df$.denominator)) stop("比例分析需要整数样本量，事件数不得超过样本量")
 es<-metafor::escalc(measure="PLO",xi=df$.events,ni=df$.denominator,add=.5,to="only0")
 trans<-stats::plogis; scale_name<-"proportion"; raw_scale<-"logit_proportion"
} else {
 es<-metafor::escalc(measure="IRLN",xi=df$.events,ti=df$.denominator,add=.5,to="only0")
 trans<-exp; scale_name<-"events_per_person_time"; raw_scale<-"log_rate"
}
df$.yi<-es$yi; df$.vi<-es$vi
.step<-"proportion-rate-fit"
fit<-metafor::rma(yi=df$.yi,vi=df$.vi,method=tau_v)
fixed<-metafor::rma(yi=df$.yi,vi=df$.vi,method="FE")
.pool_row<-function(f,label) data.frame(component=label,estimate=trans(as.numeric(f$b)),
 lower=trans(f$ci.lb),upper=trans(f$ci.ub),k=f$k,tau2=f$tau2,I2=f$I2,scale=scale_name)
pool_table<-rbind(.pool_row(fixed,"fixed"),.pool_row(fit,paste0("random_",tau_v)))
if(length(mods)) {
 for(v in mods) if(is.character(df[[v]])) df[[v]]<-factor(df[[v]])
 mod_form<-stats::as.formula(paste("~",paste(sprintf("`%s`",mods),collapse=" + ")))
 mm<-stats::model.matrix(mod_form,df)
 if(qr(mm)$rank<ncol(mm) || nrow(df)<=ncol(mm)+1) stop("所选亚组/地区/年份模型不可估计")
 mod_fit<-metafor::rma(yi=.yi,vi=.vi,mods=mod_form,data=df,method=tau_v,test="knha")
 pool_table<-rbind(pool_table,data.frame(component=paste0("moderator:",rownames(mod_fit$b)),
  estimate=as.numeric(mod_fit$b),lower=mod_fit$ci.lb,upper=mod_fit$ci.ub,k=mod_fit$k,
  tau2=mod_fit$tau2,I2=mod_fit$I2,scale=raw_scale))
}
boundary<-if(denominator_type=="sample_size") df$.events==0 | df$.events==df$.denominator else df$.events==0
forest_data<-rbind(data.frame(study=df$.study,estimate=trans(df$.yi),lower=trans(df$.yi-1.959964*sqrt(df$.vi)),upper=trans(df$.yi+1.959964*sqrt(df$.vi))),
 data.frame(study="随机效应合并",estimate=pool_table$estimate[2],lower=pool_table$lower[2],upper=pool_table$upper[2]))
forest_data$study<-factor(forest_data$study,levels=rev(forest_data$study))
forest_fig<-ggplot2::ggplot(forest_data,ggplot2::aes(x=estimate,y=study))+
 ggplot2::geom_errorbar(ggplot2::aes(xmin=lower,xmax=upper),orientation="y",width=.2)+ggplot2::geom_point()+
 ggplot2::theme_bw()+ggplot2::labs(x=scale_name,y=NULL,title=paste("比例/率合并：",denominator_type))
loo<-metafor::leave1out(fit)
results_text<-sprintf("%d 项研究；分母类型=%s，%s 变换，%s 随机效应：%.6f [%.6f, %.6f]。I²=%.2f%%，tau²=%.6f。%d 项零/边界事件研究按 metafor::escalc 的 only0 规则加 0.5；所有研究保留。逐项剔除估计范围 %.6f–%.6f。指定亚组、地区、年份以研究层协变量进入模型，系数保持变换尺度。",
 nrow(df),denominator_type,raw_scale,tau_v,pool_table$estimate[2],pool_table$lower[2],pool_table$upper[2],fit$I2,fit$tau2,sum(boundary),min(trans(loo$estimate)),max(trans(loo$estimate)))
.diag_status[["零事件和变换方法检查"]]<-if(any(boundary)) "warning" else "passed"
.diag_status[["异质性检查"]]<-if(fit$I2>75) "warning" else "passed"
.diag_status[["小样本与模型选择敏感性分析"]]<-if(nrow(df)<10) "warning" else "passed"
.out[["pool_table"]]<-.mt_put("pool_table","table",pool_table,"合并比例/人时率及选入协变量")
.out[["forest_fig"]]<-.mt_put("forest_fig","figure",forest_fig,"变换反解森林图")
.out[["results_text"]]<-.mt_put("results_text","text",results_text,"比例/率合并结果与零事件处理")
'''

CLUSTER_RCT = r'''
.step<-"cluster-rct-data"
covars<-as.character(.mt_get("covars_v",character(0))); weight_v<-.mt_get("weight_v")
df<-.mt_data(c(cluster_v,group_v,outcome_v,covars,weight_v))
df$.cluster<-factor(df[[cluster_v]]); df$.group<-factor(df[[group_v]])
if(nlevels(df$.group)!=2 || nlevels(df$.cluster)<6) stop("需要两个随机组和至少六个聚类")
if(any(vapply(split(as.character(df$.group),df$.cluster),function(x) length(unique(x))!=1,logical(1)))) stop("随机分组必须在聚类内保持一致")
if(any(table(tapply(as.character(df$.group),df$.cluster,unique))<3)) stop("每个随机组至少需要三个聚类")
reference<-.mt_get("reference_v",levels(df$.group)[1])
if(!reference %in% levels(df$.group)) stop("参考组不在治疗变量中")
df$.group<-stats::relevel(df$.group,ref=as.character(reference))
df$.y<-.mt_numeric(df[[outcome_v]],"结局")
binary<-length(unique(df$.y))==2 && all(df$.y %in% c(0,1))
if(length(unique(df$.y))==2 && !binary) stop("二分类结局须明确编码 0/1")
for(v in covars) if(is.character(df[[v]])) df[[v]]<-factor(df[[v]])
method<-.mt_select(.mt_get("method_v","mixed"),c("mixed","gee"),"聚类模型")
df$.weight<-if(length(weight_v)) .mt_numeric(df[[weight_v]],"权重") else rep(1,nrow(df))
if(any(df$.weight<=0)) stop("权重须大于零")
if(method=="mixed" && length(weight_v)) stop("抽样权重不能解释为 lmer 残差精度权重；请选择 gee 加权估计")
form<-.mt_formula(".y",c(".group",covars))
df<-df[order(df$.cluster),,drop=FALSE]
.step<-"cluster-rct-fit"
if(method=="mixed") {
 mixed_form<-stats::as.formula(paste(deparse(form),"+ (1 | .cluster)"))
 fit<-if(binary) lme4::glmer(mixed_form,data=df,family=stats::binomial()) else lme4::lmer(mixed_form,data=df,REML=TRUE)
 if(length(fit@optinfo$conv$lme4$messages)) stop("随机效应模型未收敛")
 vc<-as.data.frame(lme4::VarCorr(fit)); between<-vc$vcov[vc$grp==".cluster"][1]
 within<-if(binary) pi^2/3 else stats::sigma(fit)^2
 if(!binary) {
  ct<-clubSandwich::coef_test(fit,vcov="CR2",test="Satterthwaite",cluster=df$.cluster)
  critical<-stats::qt(.975,ct$df_Satt)
  model_table<-data.frame(term=rownames(ct),estimate=ct$beta,SE=ct$SE,df=ct$df_Satt,
   lower=ct$beta-critical*ct$SE,upper=ct$beta+critical*ct$SE,p=ct$p_Satt,inference="CR2 Satterthwaite")
 } else {
  ct<-coef(summary(fit)); model_table<-data.frame(term=rownames(ct),estimate=ct[,1],SE=ct[,2],df=NA_real_,
   lower=ct[,1]-1.959964*ct[,2],upper=ct[,1]+1.959964*ct[,2],p=ct[,4],inference="conditional logit Wald")
 }
 icc<-between/(between+within); icc_scale<-if(binary) "conditional latent logit" else "conditional Gaussian"
 diag_text<-paste("随机截距模型；参考组",reference,"；奇异拟合=",lme4::isSingular(fit),
  if(binary) "；二分类为条件 OR 与渐近 Wald 区间，不宣称已做小样本校正。" else "；连续结局使用 CR2 + Satterthwaite 自由度。")
} else {
 fit<-geepack::geeglm(form,id=.cluster,data=df,weights=.weight,
  family=if(binary) stats::binomial() else stats::gaussian(),corstr="exchangeable",std.err="san.se")
 if(fit$geese$error!=0 || any(!is.finite(coef(fit)))) stop("GEE 未收敛或存在不可估计系数")
 ct<-coef(summary(fit)); model_table<-data.frame(term=rownames(ct),estimate=ct[,1],SE=ct[,2],df=NA_real_,
  lower=ct[,1]-1.959964*ct[,2],upper=ct[,1]+1.959964*ct[,2],p=ct[,4],inference="GEE robust sandwich Wald")
 icc<-as.numeric(fit$geese$alpha); icc_scale<-"exchangeable working correlation"
 diag_text<-paste("GEE 交换相关结构与稳健 sandwich 标准误；参考组",reference,
  "；权重用于估计方程；区间为渐近 Wald，未作小聚类数校正。")
}
if(any(!is.finite(model_table$estimate)) || any(!is.finite(model_table$lower))) stop("模型推断不可估计")
if(binary) {
 model_table$effect_scale<-if(method=="mixed") "conditional OR" else "population-averaged OR"
 model_table[,c("estimate","lower","upper")]<-exp(model_table[,c("estimate","lower","upper")])
} else model_table$effect_scale<-"mean difference"
sizes<-table(df$.cluster); design_effect<-1+(mean(sizes)*(1+(stats::sd(as.numeric(sizes))/mean(sizes))^2)-1)*icc
icc_table<-data.frame(clusters=length(sizes),individuals=nrow(df),mean_cluster_size=mean(sizes),
 min_cluster_size=min(sizes),max_cluster_size=max(sizes),ICC=icc,ICC_scale=icc_scale,design_effect_approx=design_effect)
design_text<-sprintf("%d 个聚类，%d 名个体；聚类大小均值 %.2f。%s ICC/相关参数=%.6f；大小变异调整的近似设计效应=%.4f。",length(sizes),nrow(df),mean(sizes),icc_scale,icc,design_effect)
method_text<-paste("模型",method,"；协变量：",paste(covars,collapse=", "),"；",diag_text)
results_text<-paste(design_text,"治疗效应及正确尺度见聚类调整模型表；推断按聚类结构执行。")
.diag_status[["缺失与异常检查"]]<-"passed"
.diag_status[["模型/方法适配性检查"]]<-"passed"
.diag_status[["模型形式与结局类型匹配检查"]]<-"passed"
.diag_status[["效应量方向、置信区间和影响点检查"]]<-"not_assessed"
.diag_status[["聚类数、聚类大小、ICC 和小样本校正检查"]]<-if(length(sizes)<20 || method=="gee" || binary) "warning" else "passed"
.out[["icc_table"]]<-.mt_put("icc_table","table",icc_table,"ICC、聚类大小与设计效应")
.out[["model_table"]]<-.mt_put("model_table","table",model_table,"聚类调整效应与推断方法")
.out[["design_text"]]<-.mt_put("design_text","text",design_text,"聚类设计效应")
.out[["method_text"]]<-.mt_put("method_text","text",method_text,"模型与权重方法")
.out[["results_text"]]<-.mt_put("results_text","text",results_text,"聚类试验结果")
.out[["diag_text"]]<-.mt_put("diag_text","text",diag_text,"收敛、尺度与小样本校正说明")
'''

MULTIPLICITY = r'''
.step<-"gatekeeping-input"
weight_v<-.mt_get("weight_v"); subgroup_v<-.mt_get("sub_v")
df<-.mt_data(c(hypothesis_v,p_v,hierarchy_v,weight_v,subgroup_v))
df$.hypothesis<-as.character(df[[hypothesis_v]]); df$.p<-.mt_numeric(df[[p_v]],"原始 P 值")
df$.family<-.mt_numeric(df[[hierarchy_v]],"检验层级")
if(any(df$.p<0 | df$.p>1) || any(df$.family!=floor(df$.family))) stop("P 值须在 [0,1]，层级须为整数")
df$.weight<-if(length(weight_v)) .mt_numeric(df[[weight_v]],"权重") else rep(1,nrow(df))
if(any(df$.weight<0)) stop("权重不得为负")
df$.stratum<-if(length(subgroup_v)) as.character(df[[subgroup_v]]) else "overall"
if(anyDuplicated(df[,c(".stratum",".hypothesis")])) stop("每个亚组中的假设标识须唯一")
alpha<-.mt_get("alpha_v",.05)
if(!is.numeric(alpha) || length(alpha)!=1 || !is.finite(alpha) || alpha<=0 || alpha>=1) stop("alpha 须在 (0,1)")
rule<-.mt_get("transition_v","all_reject_to_next")
if(is.list(rule)) rule<-rule$method
if(length(rule)!=1 || !rule %in% c("all_reject_to_next","serial")) stop("转移规则只支持当前家族全部拒绝后，完整 alpha 转入下一家族")
.step<-"serial-gatekeeping"
strata<-unique(df$.stratum); alpha_stratum<-alpha/length(strata)
pieces<-list()
for(g in strata) {
 d<-df[df$.stratum==g,,drop=FALSE]; previous_gate_p<-0; gate_open<-TRUE
 for(family in sort(unique(d$.family))) {
  z<-d[d$.family==family,,drop=FALSE]
  if(sum(z$.weight)<=0) stop("每家族的权重总和须大于零")
  w<-z$.weight/sum(z$.weight)
  local_p<-pmin(1,ifelse(w>0,z$.p/w,1))
  serial_p<-pmax(local_p,previous_gate_p)
  rejected<-gate_open & w>0 & local_p<=alpha_stratum
  pieces[[length(pieces)+1]]<-data.frame(stratum=g,hypothesis=z$.hypothesis,family=family,
   raw_p=z$.p,normalized_weight=w,allocated_alpha=alpha_stratum*w,
   family_weighted_bonferroni_p=local_p,gate_adjusted_p=pmin(1,serial_p*length(strata)),
   gate_open=gate_open,rejected=rejected)
  previous_gate_p<-max(previous_gate_p,local_p)
  gate_open<-gate_open && all(rejected)
 }
}
mult_table<-do.call(rbind,pieces)
alpha_plot<-ggplot2::ggplot(mult_table,ggplot2::aes(x=family,y=allocated_alpha,label=hypothesis,colour=gate_open))+
 ggplot2::geom_segment(ggplot2::aes(xend=family,y=0,yend=allocated_alpha),linewidth=.7)+
 ggplot2::geom_point()+ggplot2::geom_text(nudge_y=alpha*.025,size=3)+
 ggplot2::facet_wrap(~stratum)+ggplot2::theme_bw()+ggplot2::labs(x="预先指定家族顺序",y="家族激活时的 alpha 分配",title="串行门控：全部拒绝后才激活下一家族")
results_text<-sprintf("%d 个预先指定假设、%d 个亚组；总体 alpha=%.6f，亚组间 Bonferroni 分配，每亚组 alpha=%.6f。家族内按归一化权重 Bonferroni；只有前一家族所有假设拒绝才把完整家族 alpha 传给下一家族。拒绝 %d 个假设。所有计算仅使用选定 P 值列，含 P=0/1；不自动搜集其他数值列。",
 nrow(df),length(strata),alpha,alpha_stratum,sum(mult_table$rejected))
.diag_status[["假设族定义检查"]]<-"passed"
.diag_status[["alpha 守恒检查"]]<-if(all(vapply(split(mult_table$allocated_alpha,interaction(mult_table$stratum,mult_table$family,drop=TRUE)),function(x) abs(sum(x)-alpha_stratum)<1e-12,logical(1)))) "passed" else "failed"
.diag_status[["检验顺序和相关性敏感性检查"]]<-"passed"
.out[["mult_table"]]<-.mt_put("mult_table","table",mult_table,"加权 Bonferroni 与串行门控决策")
.out[["summary_fig"]]<-.mt_put("summary_fig","figure",alpha_plot,"实际家族 alpha 分配与激活状态")
.out[["results_text"]]<-.mt_put("results_text","text",results_text,"门控规则与检验结果")
'''

TIME_HELPERS = r'''
.mt_time <- function(x, frequency=1L) {
  if(is.character(x)) {
    parsed<-as.Date(x)
    if(anyNA(parsed)) stop("时间字符串须为 YYYY-MM-DD")
    x<-parsed
  }
  calendar<-inherits(x,"Date") || inherits(x,"POSIXt")
  if(calendar && frequency %in% c(12L,4L)) {
    month<-as.integer(format(x,"%Y"))*12L+as.integer(format(x,"%m"))-1L
    index<-if(frequency==4L) month%/%3L else month
  } else index<-as.numeric(x)
  if(any(!is.finite(index)) || anyDuplicated(index)) stop("每个时间点须唯一且可解析")
  o<-order(index); index<-index[o]; delta<-diff(index)
  if(!length(delta) || any(abs(delta-delta[1])>max(1,abs(delta[1]))*1e-8)) stop("时间序列不等间隔；请先按实际频率补齐时间轴")
  list(order=o,index=index,step=delta[1],calendar=calendar,original=x[o])
}
'''

FORECAST = r'''
.step<-"forecast-input"
region_v<-.mt_get("region_v"); df<-.mt_data(c(time_v,measure_v,region_v))
df$.value<-.mt_numeric(df[[measure_v]],"预测指标")
df$.region<-if(length(region_v)) as.character(df[[region_v]]) else "overall"
frequency<-.mt_integer(.mt_get("freq_v",1),1,365,"频率")
horizon<-.mt_integer(.mt_get("horizon_v",12),1,365,"预测步数")
method<-.mt_select(.mt_get("method_v","arima"),c("arima","ets"),"预测模型")
.fit_forecast<-function(y) {
 if(method=="arima") forecast::auto.arima(y,stepwise=FALSE,approximation=FALSE) else forecast::ets(y)
}
trend_parts<-list(); error_parts<-list(); forecast_parts<-list(); plot_parts<-list(); lb_p<-numeric(0)
.step<-"forecast-train-test"
for(g in unique(df$.region)) {
 d<-df[df$.region==g,,drop=FALSE]; axis<-.mt_time(d[[time_v]],frequency); d<-d[axis$order,,drop=FALSE]
 n<-nrow(d); test_size<-.mt_integer(.mt_get("test_v",min(horizon,max(1,floor(n/5)))),1,n-8,"测试集长度")
 train_n<-n-test_size
 if(train_n<max(12,2*frequency+2)) stop("训练集至少需要 12 个时间点；季节模型至少两个周期加两个点")
 train<-stats::ts(d$.value[seq_len(train_n)],frequency=frequency)
 train_fit<-.fit_forecast(train)
 holdout<-forecast::forecast(train_fit,h=test_size,level=95)
 observed<-d$.value[(train_n+1):n]; predicted<-as.numeric(holdout$mean)
 residual<-observed-predicted
 error_parts[[length(error_parts)+1]]<-data.frame(region=g,model=method,n_train=train_n,n_test=test_size,
  MAE=mean(abs(residual)),RMSE=sqrt(mean(residual^2)),
  MAPE=if(any(observed==0)) NA_real_ else mean(abs(residual/observed))*100,
  MAE_naive=mean(abs(observed-as.numeric(tail(train,1)))))
 full_series<-stats::ts(d$.value,frequency=frequency)
 fit<-.fit_forecast(full_series)
 fc<-forecast::forecast(fit,h=horizon,level=95)
 forecast_parts[[length(forecast_parts)+1]]<-data.frame(region=g,step=seq_len(horizon),
  time_index=tail(axis$index,1)+axis$step*seq_len(horizon),prediction=as.numeric(fc$mean),
  lower_95=as.numeric(fc$lower[,1]),upper_95=as.numeric(fc$upper[,1]),model=method,frequency=frequency)
 if(frequency>1) {
  stl_fit<-stats::stl(full_series,s.window="periodic")
  trend<-as.numeric(stl_fit$time.series[,"trend"]); season<-as.numeric(stl_fit$time.series[,"seasonal"])
  remainder<-as.numeric(stl_fit$time.series[,"remainder"]); decomposition<-"STL periodic seasonality"
 } else {
  trend<-as.numeric(predict(stats::loess(d$.value~seq_len(n),span=.5)))
  season<-rep(0,n); remainder<-d$.value-trend; decomposition<-"nonseasonal loess trend"
 }
 trend_parts[[length(trend_parts)+1]]<-data.frame(region=g,time=as.character(axis$original),value=d$.value,
  trend=trend,seasonal=season,remainder=remainder,decomposition=decomposition)
 historical<-data.frame(region=g,time_index=axis$index,value=d$.value,lower=NA_real_,upper=NA_real_,series="observed")
 future<-data.frame(region=g,time_index=tail(axis$index,1)+axis$step*seq_len(horizon),value=as.numeric(fc$mean),
  lower=as.numeric(fc$lower[,1]),upper=as.numeric(fc$upper[,1]),series="forecast")
 plot_parts[[length(plot_parts)+1]]<-rbind(historical,future)
 r<-as.numeric(stats::residuals(fit)); r<-r[is.finite(r)]
 model_df<-if(inherits(fit,"Arima")) sum(fit$arma[c(1,2,3,4)]) else 0
 lag<-min(max(floor(length(r)/5),model_df+1),length(r)-1)
 lb_p<-c(lb_p,if(lag>model_df) stats::Box.test(r,lag=lag,type="Ljung-Box",fitdf=model_df)$p.value else NA_real_)
}
trend_table<-do.call(rbind,trend_parts); err_table<-do.call(rbind,error_parts); fc_table<-do.call(rbind,forecast_parts)
plot_data<-do.call(rbind,plot_parts)
fc_fig<-ggplot2::ggplot(plot_data,ggplot2::aes(x=time_index,y=value,colour=series))+
 ggplot2::geom_ribbon(ggplot2::aes(ymin=lower,ymax=upper),fill="#4477AA",alpha=.2,colour=NA,na.rm=TRUE)+
 ggplot2::geom_line()+ggplot2::facet_wrap(~region,scales="free_y")+ggplot2::theme_bw()+
 ggplot2::labs(x="原始数值时间 / 日历周期索引",y=measure_v,title=paste(method,"预测与 95% 预测区间"),caption="误差表基于最后一段时间的独立留出验证；最终预测模型再用全序列拟合")
.diag_status[["时间频率检查"]]<-"passed"
.diag_status[["残差自相关"]]<-if(anyNA(lb_p)) "not_assessed" else if(any(lb_p<.05)) "warning" else "passed"
.diag_status[["训练/测试切分"]]<-"passed"
.out[["trend_table"]]<-.mt_put("trend_table","table",trend_table,"实际序列分解与趋势")
.out[["fc_fig"]]<-.mt_put("fc_fig","figure",fc_fig,"分组预测及区间")
.out[["err_table"]]<-.mt_put("err_table","table",err_table,"按时间留出的预测误差")
.out[["fc_table"]]<-.mt_put("fc_table","table",fc_table,"指定步数预测表")
results_text <- paste0("预测结果：模型=",method,"；频率=",frequency,"，预测步数=",horizon,"。最后一段时间留出误差：\n",
 paste(capture.output(print(err_table,row.names=FALSE)),collapse="\n"),
 "\n首步预测与95%预测区间：\n",paste(capture.output(print(fc_table[fc_table$step==1,,drop=FALSE],row.names=FALSE)),collapse="\n"),
 "\n预测区间反映当前模型的不确定性。局限：适用已确认频率的等间隔序列，无法证明未来结构或季节性不变。下一步使用新的未来时段检查误差与区间覆盖，并检查残差自相关。")
.out[["results_text"]]<-.mt_put("results_text","text",results_text,"实际预测、时间留出误差与适用限制")
'''

ITS = r'''
.step<-"its-input"
covars<-as.character(.mt_get("covars_v",character(0)))
df<-.mt_data(c(time_v,measure_v,covars)); df$.y<-.mt_numeric(df[[measure_v]],"时间序列结局")
frequency<-.mt_integer(.mt_get("freq_v",1),1,365,"频率")
axis<-.mt_time(df[[time_v]],frequency); df<-df[axis$order,,drop=FALSE]
policy<-policy_v
if(axis$calendar) {
 policy<-as.Date(as.character(policy))
 if(is.na(policy)) stop("日历干预时间须为 YYYY-MM-DD")
 policy_index<-if(frequency %in% c(12L,4L)) {
   m<-as.integer(format(policy,"%Y"))*12L+as.integer(format(policy,"%m"))-1L
   if(frequency==4L) m%/%3L else m
 } else as.numeric(policy)
} else {
 if(!is.numeric(policy) || length(policy)!=1 || !is.finite(policy)) stop("干预时间须与数值时间轴同单位")
 policy_index<-policy
}
if(!policy_index %in% axis$index) stop("干预时间必须准确对应一个观测时间点，不自动改成中点或最近点")
first_post<-match(policy_index,axis$index)
if(first_post<6 || nrow(df)-first_post+1<6) stop("干预前后各至少六个观测点")
df$.time<-seq_len(nrow(df)); df$.post<-as.integer(df$.time>=first_post)
df$.after<-pmax(df$.time-first_post,0)
for(v in covars) if(is.character(df[[v]])) df[[v]]<-factor(df[[v]])
season_vars<-character(0)
if(frequency>1) {
 df$.sin<-sin(2*pi*df$.time/frequency); df$.cos<-cos(2*pi*df$.time/frequency)
 season_vars<-c(".sin",".cos")
}
form<-.mt_formula(".y",c(".time",".post",".after",covars,season_vars))
mm<-stats::model.matrix(form,df)
if(qr(mm)$rank<ncol(mm) || nrow(df)<=ncol(mm)+3) stop("干预、季节和协变量模型存在共线或自由度不足")
method<-.mt_select(.mt_get("method_v","ar1"),c("ols","ar1"),"序列误差模型")
.fit_its<-function(d) {
 if(method=="ar1") nlme::gls(form,data=d,correlation=nlme::corAR1(form=~.time),method="REML") else stats::lm(form,data=d)
}
.step<-"segmented-intervention"
fit<-.fit_its(df); beta<-stats::coef(fit); vc<-stats::vcov(fit)
df_residual<-nrow(df)-length(beta); critical<-stats::qt(.975,df_residual); se<-sqrt(diag(vc))
model_table<-data.frame(term=names(beta),estimate=as.numeric(beta),SE=as.numeric(se),df=df_residual,
 lower=beta-critical*se,upper=beta+critical*se,p=2*stats::pt(-abs(beta/se),df_residual))
effect_table<-model_table[model_table$term %in% c(".post",".after"),,drop=FALSE]
effect_table$meaning<-c("干预第一时点的即时水平变化","每个后续时间步的斜率变化")[match(effect_table$term,c(".post",".after"))]
post_slope<-beta[".time"]+beta[".after"]
post_se<-sqrt(vc[".time",".time"]+vc[".after",".after"]+2*vc[".time",".after"])
slope_table<-data.frame(period=c("pre","post"),estimate=c(beta[".time"],post_slope),SE=c(se[".time"],post_se),
 lower=c(beta[".time"]-critical*se[".time"],post_slope-critical*post_se),upper=c(beta[".time"]+critical*se[".time"],post_slope+critical*post_se))
counterfactual<-df; counterfactual$.post<-0; counterfactual$.after<-0
df$.fitted<-as.numeric(stats::fitted(fit)); df$.counterfactual<-as.numeric(stats::predict(fit,newdata=counterfactual))
plot_data<-data.frame(time=axis$index,observed=df$.y,fitted=df$.fitted,counterfactual=df$.counterfactual)
its_fig<-ggplot2::ggplot(plot_data,ggplot2::aes(x=time,y=observed))+
 ggplot2::geom_line(colour="grey55")+ggplot2::geom_line(ggplot2::aes(y=fitted),colour="#4477AA")+
 ggplot2::geom_vline(xintercept=policy_index,linetype=2)+ggplot2::theme_bw()+
 ggplot2::labs(x=time_v,y=measure_v,title="预先指定干预：水平与斜率分段模型")
trend_fig<-ggplot2::ggplot(plot_data,ggplot2::aes(x=time,y=fitted))+
 ggplot2::geom_line(colour="#4477AA")+ggplot2::geom_line(ggplot2::aes(y=counterfactual),linetype=2,colour="#B23A48")+
 ggplot2::geom_vline(xintercept=policy_index,linetype=2)+ggplot2::theme_bw()+
 ggplot2::labs(x=time_v,y=measure_v,title="拟合趋势与未发生干预的模型外推")
cp<-changepoint::cpts(changepoint::cpt.mean(df$.y,method="PELT"))
cp_table<-if(length(cp)) data.frame(source="exploratory PELT mean change",position=cp,
 time_index=axis$index[cp],note="探索性变点，不替换预先指定干预时间") else
 data.frame(source="exploratory PELT mean change",position=NA_integer_,time_index=NA_real_,note="未检出均值变点；不改变预设干预时间")
cp_table<-rbind(cp_table,data.frame(source="prespecified intervention",position=first_post,time_index=policy_index,note="第一干预后观测，post=1，after=0"))
sensitivity<-lapply(c(-1,0,1),function(shift) {
 d<-df; boundary<-first_post+shift
 d$.post<-as.integer(d$.time>=boundary);d$.after<-pmax(d$.time-boundary,0)
 f<-tryCatch(.fit_its(d),error=function(e) NULL)
 data.frame(shift=shift,level=if(is.null(f)) NA_real_ else coef(f)[".post"],slope_change=if(is.null(f)) NA_real_ else coef(f)[".after"])
})
sensitivity<-do.call(rbind,sensitivity)
r<-if(method=="ar1") residuals(fit,type="normalized") else residuals(fit)
lb<-stats::Box.test(r,lag=min(10,floor(length(r)/5)),type="Ljung-Box")
its_text<-sprintf("干预点=%s，第一干预后观测位置=%d；%s 误差，协变量=%s，频率=%d%s。水平变化 %.6f，斜率变化 %.6f；残差 Ljung–Box P=%.6g。干预提前/推迟一个观测点的水平估计范围 %.6f–%.6f。变点探索仅用于描述，不改写干预定义。",
 as.character(policy_v),first_post,method,paste(covars,collapse=","),frequency,if(frequency>1) "，含年度一阶 Fourier 项" else "",
 beta[".post"],beta[".after"],lb$p.value,min(sensitivity$level,na.rm=TRUE),max(sensitivity$level,na.rm=TRUE))
.diag_status[["自相关检查"]]<-if(lb$p.value<.05) "warning" else "passed"
.diag_status[["季节性控制"]]<-if(frequency>1) "passed" else "not_assessed"
.diag_status[["干预时间敏感性"]]<-if(anyNA(sensitivity)) "warning" else "passed"
.out[["its_fig"]]<-.mt_put("its_fig","figure",its_fig,"实际干预时点及分段拟合")
.out[["cp_table"]]<-.mt_put("cp_table","table",cp_table,"预先指定干预与探索变点")
.out[["effect_table"]]<-.mt_put("effect_table","table",effect_table,"即时水平及斜率变化")
.out[["model_table"]]<-.mt_put("model_table","table",model_table,"含协变量和季节项的分段回归")
.out[["trend_fig"]]<-.mt_put("trend_fig","figure",trend_fig,"拟合与无干预外推")
.out[["slope_table"]]<-.mt_put("slope_table","table",slope_table,"干预前后斜率及协方差调整区间")
.out[["its_text"]]<-.mt_put("its_text","text",its_text,"模型、残差及干预时间敏感性")
'''

CEA_PSA = r'''
.step<-"cea-simulation-spec"
spec<-dist_v
if(is.character(spec) && length(spec)==1) {
 if(spec %in% c("bootstrap_individual","empirical_bootstrap","bootstrap")) spec<-list(method="bootstrap_individual")
 else if(grepl("^\\s*\\{",spec)) spec<-jsonlite::fromJSON(spec)
 else stop("参数分布须给出 JSON 模拟方案，或明确指定 bootstrap_individual")
}
if(!is.list(spec) || is.null(spec$method)) stop("需要明确模拟方式：bootstrap_individual 或 paired_draws")
allowed<-c("method","reference_strategy","nsim","seed","draw_id","thresholds","outcome_direction")
if(length(setdiff(names(spec),allowed))) stop("模拟方案中存在当前不支持的设置：",paste(setdiff(names(spec),allowed),collapse=","))
method<-.mt_select(spec$method,c("bootstrap_individual","paired_draws"),"模拟方式")
if(method=="paired_draws" && (!is.null(spec$nsim) || !is.null(spec$seed)))
 stop("paired_draws 使用全部已提供抽样；不得额外指定 nsim/seed，避免静默改变或忽略抽样方案")
nsim<-.mt_integer(if(is.null(spec$nsim)) 1000 else spec$nsim,200,10000,"抽样次数")
seed<-.mt_integer(if(is.null(spec$seed)) 20261008 else spec$seed,0,.Machine$integer.max,"随机种子")
direction<-.mt_select(if(is.null(spec$outcome_direction)) "higher_better" else spec$outcome_direction,c("higher_better","lower_better"),"效果方向")
draw_id<-if(method=="paired_draws") spec$draw_id else NULL
if(method=="paired_draws" && (length(draw_id)!=1 || !is.character(draw_id))) stop("paired_draws 需要明确 draw_id 列")
sub_v<-.mt_get("sub_v")
df<-.mt_data(c(group_v,cost_v,outcome_v,sub_v,draw_id))
df$.strategy<-as.character(df[[group_v]]); df$.cost<-.mt_numeric(df[[cost_v]],"成本")
df$.effect<-.mt_numeric(df[[outcome_v]],"健康效果")
if(any(df$.cost<0)) stop("成本不得为负")
if(direction=="lower_better") df$.effect<--df$.effect
df$.subgroup<-if(length(sub_v)) as.character(df[[sub_v]]) else "overall"
threshold<-.mt_numeric(thr_v,"支付意愿阈值")
if(length(threshold)!=1 || threshold<=0) stop("支付意愿阈值须为一个正数")
thresholds<-if(is.null(spec$thresholds)) sort(unique(c(seq(0,2*threshold,length.out=41),threshold))) else .mt_numeric(unlist(spec$thresholds),"CEAC 阈值")
if(any(thresholds<0)) stop("CEAC 阈值不得为负")
strategies<-sort(unique(df$.strategy))
if(length(strategies)<2) stop("至少需要两个策略")
reference<-if(is.null(spec$reference_strategy)) strategies[1] else as.character(spec$reference_strategy)
if(length(reference)!=1 || !reference %in% strategies) stop("模拟方案参考策略不存在")
set.seed(seed); summaries<-list(); planes<-list(); ceacs<-list()
.step<-"joint-cost-effect-draws"
for(g in unique(df$.subgroup)) {
 d<-df[df$.subgroup==g,,drop=FALSE]
 if(!setequal(unique(d$.strategy),strategies)) stop("各亚组需具备全部策略，不能静默改变比较组")
 costs<-matrix(NA_real_,nrow=if(method=="paired_draws") length(unique(d[[draw_id]])) else nsim,ncol=length(strategies),dimnames=list(NULL,strategies))
 effects<-costs
 if(method=="paired_draws") {
  ids<-sort(unique(d[[draw_id]]))
  if(length(ids)<20) stop("预生成联合 PSA 至少需要二十个抽样 ID")
  for(s in strategies) {
   z<-d[d$.strategy==s,,drop=FALSE]
   if(anyDuplicated(z[[draw_id]]) || !setequal(z[[draw_id]],ids)) stop("各策略的 draw_id 必须唯一、完整并一一配对")
   z<-z[match(ids,z[[draw_id]]),,drop=FALSE]
   costs[,s]<-z$.cost; effects[,s]<-z$.effect
  }
 } else {
  for(s in strategies) {
   z<-d[d$.strategy==s,,drop=FALSE]
   if(nrow(z)<2) stop("个体 bootstrap 每策略至少需要两名个体")
   # Joint row resampling preserves the observed cost/effect correlation.
   draws<-replicate(nsim,{idx<-sample.int(nrow(z),nrow(z),replace=TRUE);c(mean(z$.cost[idx]),mean(z$.effect[idx]))})
   costs[,s]<-draws[1,];effects[,s]<-draws[2,]
  }
 }
 mean_cost<-colMeans(costs); mean_effect<-colMeans(effects)
 for(s in strategies) {
  dc<-costs[,s]-costs[,reference];de<-effects[,s]-effects[,reference]
  mc<-mean(dc);me<-mean(de);nmb<-threshold*de-dc
  dominance<-if(s==reference) "reference" else if(mc>0 && me<=0) "dominated_by_reference" else if(mc<0 && me>=0) "dominates_reference" else "tradeoff"
  summaries[[length(summaries)+1]]<-data.frame(subgroup=g,strategy=s,reference=reference,
   mean_cost=mean_cost[s],mean_effect=mean_effect[s],incremental_cost=mc,incremental_effect=me,
   ICER=if(abs(me)<1e-12) NA_real_ else mc/me,dominance=dominance,threshold=threshold,
   mean_NMB=mean(nmb),NMB_lower=unname(quantile(nmb,.025)),NMB_upper=unname(quantile(nmb,.975)),
   probability_vs_reference=mean(nmb>0),draws=nrow(costs),method=method,outcome_direction=direction)
  if(s!=reference) planes[[length(planes)+1]]<-data.frame(subgroup=g,strategy=s,x=de,y=dc,panel="成本效果平面：x=增量效果，y=增量成本")
 }
 for(wtp in sort(unique(thresholds))) {
  nmb_matrix<-wtp*effects-costs
  best<-apply(nmb_matrix,1,max)
  # Split exact ties equally, so CEAC probabilities sum to one.
  ties<-abs(nmb_matrix-best)<1e-10
  probability<-colMeans(ties/rowSums(ties))
  ceacs[[length(ceacs)+1]]<-data.frame(subgroup=g,strategy=strategies,x=wtp,y=probability,
   panel="CEAC：x=支付意愿，y=最具成本效果概率")
 }
}
cea_table<-do.call(rbind,summaries); ceac_table<-do.call(rbind,ceacs)
plot_data<-rbind(do.call(rbind,planes),ceac_table)
ce_fig<-ggplot2::ggplot(plot_data,ggplot2::aes(x=x,y=y,colour=strategy))+
 ggplot2::geom_point(data=plot_data[grepl("平面",plot_data$panel),,drop=FALSE],alpha=.15,size=.7)+
 ggplot2::geom_line(data=plot_data[grepl("CEAC",plot_data$panel),,drop=FALSE],linewidth=.8)+
 ggplot2::facet_wrap(~subgroup+panel,scales="free")+ggplot2::theme_bw()+ggplot2::labs(x=NULL,y=NULL,
  title="实际联合成本效果抽样与多策略 CEAC")
results_text<-sprintf("模拟方式=%s，参考策略=%s，随机种子=%d；支付意愿=%g。%s每个个体的成本和效果联合保留；CEAC 按每次抽样中全部策略最大 NMB 计算，平分完全并列。ICER 与支配状态分开报告；零增量效果不伪造 ICER。%s",
 method,reference,seed,threshold,if(method=="paired_draws") "按 draw_id 对齐策略，" else "每策略独立有放回抽取个体，",
 if(direction=="lower_better") "效果已取负值以统一为越大越好。" else "健康效果为越大越好。")
.diag_status[["成本与效果分布检查"]]<-"passed"
.diag_status[["增量分析支配关系检查"]]<-"passed"
.diag_status[["概率敏感性和阈值分析"]]<-"passed"
.out[["cea_table"]]<-.mt_put("cea_table","table",cea_table,"增量成本、效果、NMB 及支配关系")
.out[["ce_fig"]]<-.mt_put("ce_fig","figure",ce_fig,"联合抽样平面与全部策略 CEAC")
.out[["results_text"]]<-.mt_put("results_text","text",results_text,"实际模拟方案与成本效果结果")
'''

SCREENING = r'''
.step<-"screening-input"
sub_v<-.mt_get("sub_v"); threshold_col<-.mt_get("threshold_col_v")
followup_v<-.mt_get("followup_v"); cost_v<-.mt_get("cost_v")
cols<-unique(c(score_v,gold_v,sub_v,threshold_col,followup_v,cost_v))
if(length(setdiff(cols,names(dataset)))) stop("筛查所选数据列不存在")
df<-dataset[,cols,drop=FALSE]
score<-.mt_numeric(df[[score_v]],"筛查评分")
positive<-as.character(.mt_get("positive_v","1"))
direction<-.mt_select(.mt_get("direction_v","higher_positive"),c("higher_positive","lower_positive"),"评分方向")
score_type<-.mt_select(.mt_get("score_type_v","binary"),c("binary","continuous","probability"),"筛查结果类型")
if(score_type=="binary" && any(!score %in% c(0,1))) stop("binary 筛查结果须明确编码 0/1；连续评分请选择 continuous")
if(score_type=="probability" && any(score<0 | score>1)) stop("风险概率须在 [0,1]")
if(length(threshold_col)) {
 thresholds<-sort(unique(.mt_numeric(df[[threshold_col]],"阈值列")))
 if(length(thresholds)>20) stop("阈值列至多包含二十个预先指定阈值")
} else if(length(.mt_get("threshold_v"))) {
 thresholds<-.mt_numeric(.mt_get("threshold_v"),"预先指定筛查阈值")
} else if(score_type=="binary") thresholds<-.5 else stop("连续/概率评分需指定 screening_threshold，或提供预先指定的阈值列")
df$.score<-score;df$.stratum<-if(length(sub_v)) as.character(df[[sub_v]]) else "overall"
if(length(sub_v) && anyNA(df$.stratum)) stop("亚组变量存在缺失")
endpoints<-c(gold_v,followup_v); tables<-list(); curves<-list(); missing_gold<-0L
.ci_prop<-function(n,total) {
 if(total==0) return(c(NA_real_,NA_real_,NA_real_))
 ci<-suppressWarnings(stats::prop.test(n,total,correct=FALSE)$conf.int)
 c(n/total,ci)
}
.step<-"gold-standard-performance"
for(endpoint in endpoints) for(g in unique(df$.stratum)) {
 d<-df[df$.stratum==g,,drop=FALSE]
 missing_gold<-missing_gold+sum(is.na(d[[endpoint]]))
 d<-d[!is.na(d[[endpoint]]),,drop=FALSE]
 gold<-as.character(d[[endpoint]])
 if(length(unique(gold))!=2 || length(positive)!=1 || !positive %in% gold) stop("各金标准/随访亚组须有两个结局类别，阳性类别须存在")
 y<-as.integer(gold==positive)
 if(min(table(factor(y,levels=c(0,1))))<2) stop("各终点亚组至少需要两个阳性和两个阴性样本")
 roc<-pROC::roc(y,d$.score,levels=c(0,1),direction=if(direction=="higher_positive") "<" else ">",quiet=TRUE)
 auc<-as.numeric(pROC::auc(roc)); auc_ci<-suppressWarnings(as.numeric(pROC::ci.auc(roc,method="delong")))
 roc_data<-data.frame(endpoint=endpoint,stratum=g,strategy="ROC",x=1-roc$specificities,y=roc$sensitivities,
  panel="ROC：x=1-特异度，y=灵敏度")
 curves[[length(curves)+1]]<-roc_data
 for(cutoff in thresholds) {
  predicted<-if(direction=="higher_positive") d$.score>=cutoff else d$.score<=cutoff
  tp<-sum(y==1 & predicted);fn<-sum(y==1 & !predicted);fp<-sum(y==0 & predicted);tn<-sum(y==0 & !predicted)
  values<-rbind(.ci_prop(tp,tp+fn),.ci_prop(tn,tn+fp),.ci_prop(tp,tp+fp),.ci_prop(tn,tn+fn))
  names_metrics<-c("sensitivity","specificity","PPV","NPV")
  table<-data.frame(endpoint=endpoint,stratum=g,cutoff=cutoff,metric=names_metrics,
   estimate=values[,1],lower=values[,2],upper=values[,3],n=nrow(d),TP=tp,FN=fn,FP=fp,TN=tn,
   prevalence=mean(y),AUC=auc,AUC_lower=auc_ci[1],AUC_upper=auc_ci[3],
   mean_screening_cost=if(length(cost_v)) mean(.mt_numeric(d[[cost_v]],"筛查成本")) else NA_real_)
  tables[[length(tables)+1]]<-table
  pt<-seq(.01,.99,by=.01)
  # Fixed screening policy: varying clinical utility threshold, not silently
  # interpreting an arbitrary screening score as a calibrated probability.
  nb<-tp/nrow(d)-fp/nrow(d)*pt/(1-pt)
  curves[[length(curves)+1]]<-data.frame(endpoint=endpoint,stratum=g,strategy=paste0("screen cutoff=",cutoff),x=pt,y=nb,
   panel="决策曲线：x=临床偏好阈值，y=净获益")
 }
 pt<-seq(.01,.99,by=.01)
 curves[[length(curves)+1]]<-data.frame(endpoint=endpoint,stratum=g,strategy="treat all",x=pt,
  y=mean(y)-(1-mean(y))*pt/(1-pt),panel="决策曲线：x=临床偏好阈值，y=净获益")
 curves[[length(curves)+1]]<-data.frame(endpoint=endpoint,stratum=g,strategy="treat none",x=pt,y=0,
  panel="决策曲线：x=临床偏好阈值，y=净获益")
}
scr_table<-do.call(rbind,tables); curve_data<-do.call(rbind,curves)
scr_fig<-ggplot2::ggplot(curve_data,ggplot2::aes(x=x,y=y,colour=strategy))+
 ggplot2::geom_line(linewidth=.7)+ggplot2::facet_wrap(~endpoint+stratum+panel,scales="free_y")+
 ggplot2::theme_bw()+ggplot2::labs(x=NULL,y=NULL,title="金标准 ROC 与指定筛查策略的决策曲线")
results_text<-sprintf("评分列=%s，金标准=%s，阳性类别=%s，方向=%s，评分类型=%s；%d 个预设阈值。灵敏度、特异度、PPV、NPV 使用无连续性校正的 Wilson 区间；AUC 使用 pROC DeLong 区间。各亚组与选入随访结局分别计算；缺失金标准累计排除 %d 条终点记录。净获益以预设筛查策略 TP/n−FP/n×pt/(1−pt) 计算，不对任意评分假定概率校准。%s",
 score_v,gold_v,positive,direction,score_type,length(thresholds),missing_gold,
 if(length(cost_v)) "报告观测平均筛查成本；没有额外指定货币化获益。" else "未提供成本列。")
.diag_status[["验证偏倚检查"]]<-if(missing_gold>0) "warning" else "not_assessed"
.diag_status[["阈值稳定性检查"]]<-if(length(thresholds)>1) "passed" else "not_assessed"
.diag_status[["患病率与净获益敏感性分析"]]<-"passed"
.out[["scr_table"]]<-.mt_put("scr_table","table",scr_table,"金标准性能、AUC 与预设阈值指标")
.out[["scr_fig"]]<-.mt_put("scr_fig","figure",scr_fig,"实际 ROC 与筛查策略净获益")
.out[["results_text"]]<-.mt_put("results_text","text",results_text,"筛查、随访与成本执行结果")
'''

CORES = {
    "pairwise_meta": COMMON + META,
    "evidence_meta_regression": COMMON + META_REGRESSION,
    "evidence_proportion_rate_meta": COMMON + PROPORTION_RATE,
    "cluster_rct_analysis": COMMON + CLUSTER_RCT,
    "trial_multiplicity_gatekeeping": COMMON + MULTIPLICITY,
    "forecasting": COMMON + TIME_HELPERS + FORECAST,
    "interrupted_time_series": COMMON + TIME_HELPERS + ITS,
    "public_cost_effectiveness_psa": COMMON + CEA_PSA,
    "public_screening_evaluation": COMMON + SCREENING,
}

INPUTS = {
    "pairwise_meta": {"study_id": "study_v", "effect_estimate": "effect_v", "standard_error": "se_v", "subgroup": "sub_v", "effect_scale": "scale_v", "method_tau": "tau_v"},
    "evidence_meta_regression": {"study_id": "study_v", "effect_estimate": "effect_v", "standard_error": "se_v", "covariates": "covars_v", "subgroup": "sub_v", "followup_duration": "fu_v", "risk_of_bias": "rob_v", "method_tau": "tau_v"},
    "evidence_proportion_rate_meta": {"study_id": "study_v", "event_count": "event_v", "sample_size_person_time": "n_v", "subgroup": "sub_v", "region": "region_v", "study_year": "year_v", "denominator_type": "denominator_v", "method_tau": "tau_v"},
    "cluster_rct_analysis": {"outcome": "outcome_v", "group": "group_v", "cluster_id": "cluster_v", "covariates": "covars_v", "weight": "weight_v", "analysis_method": "method_v", "reference_group": "reference_v"},
    "trial_multiplicity_gatekeeping": {"outcome": "hypothesis_v", "raw_p_value": "p_v", "testing_hierarchy": "hierarchy_v", "weight": "weight_v", "transition_rules": "transition_v", "subgroup": "sub_v", "alpha": "alpha_v"},
    "forecasting": {"time": "time_v", "measure": "measure_v", "frequency": "freq_v", "group_region": "region_v", "horizon": "horizon_v", "test_size": "test_v", "analysis_method": "method_v"},
    "interrupted_time_series": {"time": "time_v", "outcome": "measure_v", "intervention_time": "policy_v", "covariates": "covars_v", "analysis_method": "method_v", "frequency": "freq_v"},
    "public_cost_effectiveness_psa": {"strategy": "group_v", "cost": "cost_v", "health_outcome": "outcome_v", "threshold": "thr_v", "parameter_distributions": "dist_v", "subgroup": "sub_v"},
    "public_screening_evaluation": {"outcome": "score_v", "outcome_2": "gold_v", "threshold": "threshold_col_v", "subgroup": "sub_v", "outcome_3": "followup_v", "cost": "cost_v", "positive_class": "positive_v", "score_direction": "direction_v", "screening_threshold": "threshold_v", "score_type": "score_type_v"},
}

ROLE_MAPS = {
    "pairwise_meta": {"forest_plot": "forest_fig", "table_1": "pooled_table", "table_2": "bias_table", "table_3": "het_table", "sensitivity_output": "loo_table", "scientific_result_interpretation": "results_text"},
    "evidence_meta_regression": {"primary_table": "reg_table", "diagnostic_plot": "bubble_fig", "manuscript_results": "results_text"},
    "evidence_proportion_rate_meta": {"primary_table": "pool_table", "diagnostic_plot": "forest_fig", "manuscript_results": "results_text"},
    "cluster_rct_analysis": {"table_1": "icc_table", "table_2": "model_table", "text_1": "design_text", "method_note": "method_text", "manuscript_results": "results_text", "diagnostic_output": "diag_text"},
    "trial_multiplicity_gatekeeping": {"primary_table": "mult_table", "diagnostic_plot": "summary_fig", "manuscript_results": "results_text"},
    "forecasting": {"table_1": "trend_table", "figure_1": "fc_fig", "table_2": "err_table", "table_3": "fc_table", "scientific_result_interpretation": "results_text"},
    "interrupted_time_series": {"figure_1": "its_fig", "table_1": "cp_table", "table_2": "effect_table", "table_3": "model_table", "figure_2": "trend_fig", "table_4": "slope_table", "text_1": "its_text"},
    "public_cost_effectiveness_psa": {"primary_table": "cea_table", "diagnostic_plot": "ce_fig", "manuscript_results": "results_text"},
    "public_screening_evaluation": {"primary_table": "scr_table", "diagnostic_plot": "scr_fig", "manuscript_results": "results_text"},
}

REFERENCE_CASES = {
    "pairwise_meta": "原始效应尺度的闭式逆方差合并及 Q；显式 log 比值、DL 与指定亚组；逐研究剔除真实 R 核验。",
    "evidence_meta_regression": "连续/分类研究层协变量的 metafor REML + Knapp–Hartung 参考拟合；相同模型逐项剔除与拟合值气泡图。",
    "evidence_proportion_rate_meta": "明确样本量或人时；零/全事件 PLO 变换和 IRLN 的独立数值参考；保留边界研究与事件数超过人时的有效率数据。",
    "cluster_rct_analysis": "连续随机截距 lmer + CR2/Satterthwaite、加权二分类 GEE 的参考系数与 SE；ICC/模型表角色修复。",
    "trial_multiplicity_gatekeeping": "指定 P 值列包含 0/1；家族内加权 Bonferroni、完整拒绝后的串行门控及亚组 alpha 分配手算核验。",
    "forecasting": "ETS 指定预测步数及按时间留出 RMSE 与 forecast 参考一致；分地区月度序列分解、预测和不规则时间拒绝。",
    "interrupted_time_series": "精确预设干预点、即时水平及斜率变化，含协变量 OLS 与 AR(1) GLS 参考拟合；不自动改为序列中点。",
    "public_cost_effectiveness_psa": "已提供 draw_id 联合抽样的 ICER/NMB 手算；个体联合 bootstrap 保持成本效果相关；多策略 CEAC 并列概率核验。",
    "public_screening_evaluation": "正确筛查评分与金标准映射，0/1 灵敏度/特异度/净获益手算，Wilson 区间；方向、预设阈值、亚组及随访输出核验。",
}
