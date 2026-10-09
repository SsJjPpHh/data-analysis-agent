"""Fixed statistics/measurement engines using confirmed input roles.

These bodies are installed by the existing skeleton engine wrapper.  They
share its frozen-input accessors and artifact emitter, rather than adding a
second execution or result registry.
"""
from __future__ import annotations

import json

from .models import AnalysisPlan

_COMMON = r'''
.sb_input <- function(id, default=NULL) { z <- .sb_inputs[[id]]; if (is.null(z) || !length(z)) default else z }
.sb_cols <- function(cols) {
  cols <- unique(as.character(cols)); cols <- cols[nzchar(cols)]
  if(any(grepl("`",cols,fixed=TRUE))) stop("列名含反引号，需先规范命名")
  bad <- setdiff(cols,names(dataset)); if (length(bad)) stop("缺失数据列：",paste(bad,collapse=", "))
  if (!length(cols)) stop("未指定分析变量")
  dataset[,cols,drop=FALSE]
}
.sb_numeric <- function(dat,cols=names(dat)) {
  for (nm in cols) if (!is.numeric(dat[[nm]]) || is.factor(dat[[nm]])) stop("已确认数值角色需要数值列：",nm)
  if (any(vapply(dat[,cols,drop=FALSE],function(x) any(!is.finite(x) & !is.na(x)),logical(1)))) stop("数值列包含无穷值")
  invisible(TRUE)
}
.sb_complete <- function(dat,min_n=3L) {
  original <- nrow(dat); dat <- dat[stats::complete.cases(dat),,drop=FALSE]
  if (nrow(dat)<min_n) stop("完整病例数量不足：",nrow(dat))
  .diag_status[["缺失与异常检查"]] <<- if (nrow(dat)<original) "warning" else "passed"
  dat
}
.sb_formula <- function(out,terms) stats::reformulate(sprintf("`%s`",terms),response=paste0("`",out,"`"))
.sb_group <- function(x,reference=NULL) {
  x <- droplevels(factor(x)); if (nlevels(x)!=2L) stop("需要恰好两个组")
  if (!is.null(reference)) { if (!as.character(reference)%in%levels(x)) stop("参照组不在数据中"); x<-stats::relevel(x,as.character(reference)) }
  x
}
.sb_check_fit <- function(fit) {
  if (any(!is.finite(stats::coef(fit))) || fit$rank<length(stats::coef(fit))) stop("模型秩不足或系数不可估计")
  if (inherits(fit,"glm") && !isTRUE(fit$converged)) stop("模型未收敛")
  if (stats::df.residual(fit)<1L) stop("模型无残差自由度")
}
.sb_table <- function(slot,payload,title) .out[[slot]] <<- list(kind="table",payload=payload,title=title)
.sb_text <- function(slot,payload,title) .out[[slot]] <<- list(kind="text",payload=payload,title=title)
.sb_plot <- function(slot,payload,title) .out[[slot]] <<- list(kind="figure",payload=payload,title=title)
.sb_reverse <- function(dat,reverse) {
  reverse <- as.character(reverse); if (length(setdiff(reverse,names(dat)))) stop("反向题不在条目集合中")
  for (nm in reverse) dat[[nm]] <- -dat[[nm]]
  dat
}
.sb_coef <- function(fit,variable) {
  nm<-names(stats::coef(fit)); idx<-match(variable,nm); if(is.na(idx)) idx<-match(paste0("`",variable,"`"),nm)
  if(is.na(idx)) stop("路径系数不可识别：",variable)
  unname(stats::coef(fit)[idx])
}
.sb_ci_table <- function(fit) {
  ct<-stats::coef(summary(fit)); critical<-stats::qt(.975,stats::df.residual(fit))
  data.frame(变量=rownames(ct),系数=ct[,1],标准误=ct[,2],CI下限=ct[,1]-critical*ct[,2],CI上限=ct[,1]+critical*ct[,2],P值=ct[,ncol(ct)],row.names=NULL,check.names=FALSE)
}
'''

_CORRELATION = r'''
cont <- as.character(cont_v); if (length(cont)<2L) stop("至少需要两个成对测量变量")
if (!is.null(.sb_input("rater_method_variable"))) stop("本入口接收宽表测量列；长表评分者请使用编码一致性入口")
method <- as.character(.sb_input("analysis_method","correlation")); if (!method%in%c("correlation","agreement")) stop("未知分析方法")
strata <- .sb_input("strata"); dat <- .sb_cols(c(cont,strata)); dat <- .sb_complete(dat,4L)
groups <- if (is.null(strata)) list(总体=dat) else split(dat,droplevels(factor(dat[[strata]])))
corr_rows<-list(); agreement_rows<-list(); difference_rows<-list()
for (gn in names(groups)) {
  d<-groups[[gn]]; if(nrow(d)<4L) stop("分层完整配对少于 4：",gn)
  for (pr in utils::combn(cont,2L,simplify=FALSE)) {
    a<-d[[pr[1]]]; b<-d[[pr[2]]]
    if (method=="correlation") {
      .sb_numeric(d,pr); if (stats::sd(a)==0 || stats::sd(b)==0) stop("常数测量无法计算相关")
      for (cm in c("pearson","spearman","kendall")) {
        ct<-suppressWarnings(stats::cor.test(a,b,method=cm,exact=FALSE))
        ci<-if(cm=="pearson") ct$conf.int else c(NA_real_,NA_real_)
        corr_rows[[length(corr_rows)+1L]]<-data.frame(分层=gn,变量1=pr[1],变量2=pr[2],方法=cm,n=nrow(d),相关系数=unname(ct$estimate),CI下限=ci[1],CI上限=ci[2],P值=ct$p.value)
      }
    } else if (is.numeric(a) && is.numeric(b) && !is.factor(a) && !is.factor(b)) {
      .sb_numeric(d,pr); ic<-irr::icc(data.frame(a=a,b=b),model="twoway",type="agreement",unit="single",conf.level=.95)
      delta<-a-b; n<-length(delta); bias<-mean(delta); s<-stats::sd(delta); z<-stats::qnorm(.975); tcrit<-stats::qt(.975,n-1L)
      lo<-bias-z*s; hi<-bias+z*s; se_bias<-s/sqrt(n); se_loa<-s*sqrt(1/n+z^2/(2*(n-1)))
      agreement_rows[[length(agreement_rows)+1L]]<-data.frame(分层=gn,变量1=pr[1],变量2=pr[2],指标="ICC(A,1) 双向随机单次绝对一致",估计=ic$value,CI下限=ic$lbound,CI上限=ic$ubound,n=n)
      difference_rows[[length(difference_rows)+1L]]<-data.frame(分层=gn,变量1=pr[1],变量2=pr[2],均值差=bias,均值差CI下限=bias-tcrit*se_bias,均值差CI上限=bias+tcrit*se_bias,一致性下限=lo,下限CI下界=lo-tcrit*se_loa,下限CI上界=lo+tcrit*se_loa,一致性上限=hi,上限CI下界=hi-tcrit*se_loa,上限CI上界=hi+tcrit*se_loa,n=n)
    } else {
      if (is.numeric(a)!=is.numeric(b)) stop("一致性变量需为相同测量类型")
      ratings<-data.frame(a=factor(a),b=factor(b)); kp<-irr::kappa2(ratings,weight="unweighted")
      Bk<-as.integer(.sb_input("resamples",1000L)); if(is.na(Bk)||Bk<199L||Bk>100000L) stop("Kappa Bootstrap 次数范围 199–100000")
      set.seed(as.integer(.sb_input("seed",1729L)))
      kdraw<-replicate(Bk,{ii<-sample.int(nrow(ratings),nrow(ratings),replace=TRUE);suppressWarnings(irr::kappa2(ratings[ii,,drop=FALSE],weight="unweighted")$value)})
      valid<-is.finite(kdraw); kci<-if(mean(valid)>=.95) stats::quantile(kdraw[valid],c(.025,.975),names=FALSE) else c(NA_real_,NA_real_)
      agreement_rows[[length(agreement_rows)+1L]]<-data.frame(分层=gn,变量1=pr[1],变量2=pr[2],指标="Cohen Kappa（对象 Bootstrap 区间）",估计=kp$value,CI下限=kci[1],CI上限=kci[2],n=nrow(d))
      tt<-as.data.frame(table(ratings)); names(tt)<-c("类别1","类别2","频数"); tt$分层<-gn; tt$变量1<-pr[1];tt$变量2<-pr[2]
      difference_rows[[length(difference_rows)+1L]]<-tt
    }
  }
}
a<-dat[[cont[1]]]; b<-dat[[cont[2]]]
if(method=="correlation") {
  corr_table<-do.call(rbind,corr_rows); .sb_table("corr_matrix",corr_table,"成对 Pearson / Spearman / Kendall 相关及样本量")
  fig<-ggplot2::ggplot(dat,ggplot2::aes(x=.data[[cont[1]]],y=.data[[cont[2]]]))+ggplot2::geom_point()+ggplot2::theme_bw()+ggplot2::labs(title="成对相关散点图")
  .sb_plot("ba_fig",fig,"成对相关图")
  .sb_text("results_text",sprintf("按确认的数值角色计算相关，完整病例 n=%d；相关系数不表示测量可互换性。",nrow(dat)),"相关性解释")
} else {
  agreement_table<-do.call(rbind,agreement_rows); comparison_table<-do.call(rbind,difference_rows)
  .sb_table("agreement_stats",agreement_table,"测量一致性指标"); .sb_table("agreement_table",comparison_table,"一致性界限与不确定性 / 分类交叉表")
  if(is.numeric(a)&&is.numeric(b)&&!is.factor(a)&&!is.factor(b)) {
    plotdat<-data.frame(均值=(a+b)/2,差值=a-b); limits<-mean(a-b)+c(-1,0,1)*stats::qnorm(.975)*stats::sd(a-b)
    fig<-ggplot2::ggplot(plotdat,ggplot2::aes(x=均值,y=差值))+ggplot2::geom_point()+ggplot2::geom_hline(yintercept=limits,linetype=c(2,1,2))+ggplot2::theme_bw()
    scatter<-ggplot2::ggplot(dat,ggplot2::aes(x=.data[[cont[1]]],y=.data[[cont[2]]]))+ggplot2::geom_point()+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+ggplot2::theme_bw()
  } else {
    plotdat<-as.data.frame(table(a,b)); fig<-ggplot2::ggplot(plotdat,ggplot2::aes(x=a,y=b,fill=Freq))+ggplot2::geom_tile()+ggplot2::theme_bw(); scatter<-fig
  }
  .sb_plot("scatter_fig",scatter,"测量一致性图"); .sb_plot("ba_fig",fig,"Bland–Altman 图 / 分类交叉图")
  .sb_text("results_text",sprintf("完整配对 n=%d；连续测量采用 ICC(A,1) 与 Bland–Altman，分类测量采用 Cohen Kappa。分类 Kappa 采用配对对象 Bootstrap 区间，不稳定时明确留空。分层指标分别报告，图展示总体首对变量。",nrow(dat)),"一致性解释")
}
.diag_status[["散点非线性检查"]]<-"not_assessed"
.diag_status[["离群点敏感性检查"]]<-"not_assessed"
'''

_ANCOVA = r'''
covs<-unique(c(.sb_input("covariates",character(0)),.sb_input("covariates_2",character(0))))
if(!length(covs) || outcome_v%in%c(group_v,covs) || group_v%in%covs) stop("结局、组别与协变量角色需互不重叠")
strata<-.sb_input("strata"); dat<-.sb_cols(c(outcome_v,group_v,covs,strata)); .sb_numeric(dat,outcome_v); dat<-.sb_complete(dat,10L)
for(p in c(group_v,covs,strata)) if(is.character(dat[[p]]) || p==group_v) dat[[p]]<-factor(dat[[p]])
groups<-if(is.null(strata)) list(总体=dat) else split(dat,dat[[strata]])
rows<-list(); diagrows<-list(); plotrows<-list()
for(gn in names(groups)) {
  df<-droplevels(groups[[gn]]); if(nlevels(df[[group_v]])<2L) stop("每层需至少两个组")
  fit<-stats::lm(.sb_formula(outcome_v,c(group_v,covs)),data=df); .sb_check_fit(fit)
  slope_form<-stats::as.formula(paste0("`",outcome_v,"` ~ `",group_v,"` * (",paste(sprintf("`%s`",covs),collapse=" + "),")"))
  slope_fit<-stats::lm(slope_form,data=df); slope_test<-stats::anova(fit,slope_fit); slope_p<-slope_test$`Pr(>F)`[2]
  em<-emmeans::emmeans(fit,specs=group_v,weights="equal")
  means<-as.data.frame(summary(em,infer=c(TRUE,TRUE))); contrasts<-as.data.frame(summary(emmeans::contrast(em,"pairwise",adjust="tukey"),infer=c(TRUE,TRUE)))
  means_rows<-data.frame(分层=gn,类型="调整均值",对比=as.character(means[[group_v]]),估计=means$emmean,标准误=means$SE,df=means$df,CI下限=means$lower.CL,CI上限=means$upper.CL,P值=means$p.value)
  contrasts_rows<-data.frame(分层=gn,类型="组间对比 Tukey",对比=contrasts$contrast,估计=contrasts$estimate,标准误=contrasts$SE,df=contrasts$df,CI下限=contrasts$lower.CL,CI上限=contrasts$upper.CL,P值=contrasts$p.value)
  rows[[length(rows)+1L]]<-rbind(means_rows,contrasts_rows)
  lev<-car::leveneTest(stats::residuals(fit),df[[group_v]],center=stats::median)$`Pr(>F)`[1]
  diagrows[[length(diagrows)+1L]]<-data.frame(分层=gn,斜率交互P=slope_p,残差组间方差P=lev,n=nrow(df))
  plotrows[[length(plotrows)+1L]]<-data.frame(分层=gn,拟合值=stats::fitted(fit),残差=stats::residuals(fit))
}
adjusted_table<-do.call(rbind,rows); diagnostic_table<-do.call(rbind,diagrows)
fig<-ggplot2::ggplot(do.call(rbind,plotrows),ggplot2::aes(x=拟合值,y=残差))+ggplot2::geom_point()+ggplot2::geom_hline(yintercept=0,linetype=2)+ggplot2::facet_wrap(~分层)+ggplot2::theme_bw()
.diag_status[["回归斜率同质性检查"]]<-if(any(is.na(diagnostic_table$斜率交互P))) "not_assessed" else if(any(diagnostic_table$斜率交互P<.05)) "warning" else "passed"
.diag_status[["残差和方差检查"]]<-if(any(diagnostic_table$残差组间方差P<.05,na.rm=TRUE)) "warning" else "passed"
.diag_status[["调整均值与对比检查"]]<-if(all(is.finite(adjusted_table$估计))) "passed" else "failed"
.sb_table("adjusted_table",adjusted_table,"参考网格调整均值与全部成对对比")
.sb_plot("diag_fig",fig,"ANCOVA 分层残差诊断")
.sb_text("results_text",paste0("ANCOVA 采用确认的协变量；连续协变量在均值处、分类协变量等权边际化；全部成对对比采用 Tukey 校正。共同斜率仅检验组别×协变量交互。诊断：",paste(capture.output(print(diagnostic_table,row.names=FALSE)),collapse="\n")),"调整组间比较结果")
'''

_CATEGORICAL = r'''
method<-as.character(.sb_input("analysis_method")); if(length(method)!=1L || !method%in%c("multinomial","ordinal","count")) stop("请明确选择 multinomial / ordinal / count")
covs<-as.character(.sb_input("covariates",character(0))); offset_col<-.sb_input("exposure_2")
df<-.sb_cols(c(outcome_v,exposure_v,covs,offset_col)); df<-.sb_complete(df,15L)
for(nm in c(exposure_v,covs)) if(is.character(df[[nm]])) df[[nm]]<-factor(df[[nm]])
if(!is.null(offset_col) && method!="count") stop("偏移量仅用于计数模型")
form<-.sb_formula(outcome_v,c(exposure_v,covs)); po_p<-NA_real_; dispersion<-NA_real_
if(method=="count") {
  .sb_numeric(df,outcome_v); y<-df[[outcome_v]]; if(any(y<0 | y!=floor(y))) stop("计数结局必须为非负整数")
  if(!is.null(offset_col)) { .sb_numeric(df,offset_col); if(any(df[[offset_col]]<=0)) stop("暴露时间必须严格为正"); df$.sb_offset<-log(df[[offset_col]]); form<-stats::update.formula(form,.~.+offset(.sb_offset)) }
  family_choice<-as.character(.sb_input("count_family","poisson"))
  if(family_choice=="poisson") fit<-stats::glm(form,data=df,family=stats::poisson()) else if(family_choice=="negative_binomial") fit<-MASS::glm.nb(form,data=df) else stop("计数分布仅支持 poisson / negative_binomial")
  .sb_check_fit(fit); ct<-stats::coef(summary(fit)); dispersion<-sum(stats::residuals(fit,type="pearson")^2)/stats::df.residual(fit)
  coef_table<-data.frame(变量=rownames(ct),系数=ct[,1],效应=exp(ct[,1]),CI下限=exp(ct[,1]-stats::qnorm(.975)*ct[,2]),CI上限=exp(ct[,1]+stats::qnorm(.975)*ct[,2]),P值=ct[,ncol(ct)],解释="发生率比",row.names=NULL)
  preds<-stats::predict(fit,type="response"); pred_table<-data.frame(行号=as.integer(rownames(df)),预测计数=preds,观察计数=y)
  for(cn in covs) if(!is.null(offset_col)&&cn==offset_col) stop("暴露时间不得重复作为协变量")
  nd0<-df; nd1<-df
} else if(method=="multinomial") {
  if(is.ordered(df[[outcome_v]])) df[[outcome_v]]<-factor(df[[outcome_v]],ordered=FALSE) else df[[outcome_v]]<-droplevels(factor(df[[outcome_v]]))
  if(nlevels(df[[outcome_v]])<3L) stop("多项模型需要至少三个类别")
  fit<-nnet::multinom(form,data=df,Hess=TRUE,trace=FALSE,maxit=1000)
  if(fit$convergence!=0L) stop("多项模型未收敛")
  sm<-summary(fit); cf<-sm$coefficients; se<-sm$standard.errors
  if(any(!is.finite(cf)) || any(!is.finite(se))) stop("多项模型系数不可估计")
  coef_table<-do.call(rbind,lapply(seq_len(nrow(cf)),function(i) data.frame(类别=rownames(cf)[i],参照类别=levels(df[[outcome_v]])[1],变量=colnames(cf),系数=cf[i,],效应=exp(cf[i,]),CI下限=exp(cf[i,]-stats::qnorm(.975)*se[i,]),CI上限=exp(cf[i,]+stats::qnorm(.975)*se[i,]),P值=2*stats::pnorm(-abs(cf[i,]/se[i,])),解释="相对风险比",row.names=NULL)))
  preds<-stats::predict(fit,type="probs"); pred_table<-data.frame(行号=as.integer(rownames(df)),preds,check.names=FALSE)
} else {
  y<-df[[outcome_v]]; if(!is.ordered(y)) stop("有序模型需要已确认顺序的 ordered factor；不按字符串排序推定等级")
  df[[outcome_v]]<-droplevels(y); if(nlevels(df[[outcome_v]])<3L) stop("有序模型需至少三个等级")
  fit<-MASS::polr(form,data=df,Hess=TRUE,method="logistic",control=list(maxit=1000))
  if(fit$convergence!=0L || any(!is.finite(stats::coef(fit)))) stop("有序模型未收敛")
  ct<-stats::coef(summary(fit)); ncoef<-length(stats::coef(fit)); z<-ct[,1]/ct[,2]
  coef_table<-data.frame(变量=rownames(ct),类型=c(rep("斜率 OR",ncoef),rep("等级切点",nrow(ct)-ncoef)),系数=ct[,1],效应=c(exp(ct[seq_len(ncoef),1]),rep(NA_real_,nrow(ct)-ncoef)),CI下限=c(exp(ct[seq_len(ncoef),1]-stats::qnorm(.975)*ct[seq_len(ncoef),2]),rep(NA_real_,nrow(ct)-ncoef)),CI上限=c(exp(ct[seq_len(ncoef),1]+stats::qnorm(.975)*ct[seq_len(ncoef),2]),rep(NA_real_,nrow(ct)-ncoef)),P值=2*stats::pnorm(-abs(z)),row.names=NULL)
  preds<-stats::predict(fit,type="probs"); pred_table<-data.frame(行号=as.integer(rownames(df)),preds,check.names=FALSE)
  po_p<-tryCatch({ po<-ordinal::clm(form,data=df,link="logit"); nt<-ordinal::nominal_test(po); min(nt$`Pr(>Chi)`[-1],na.rm=TRUE) },error=function(e) NA_real_)
  if(!is.finite(po_p)) po_p<-NA_real_
}
if(is.numeric(df[[exposure_v]]) && !is.factor(df[[exposure_v]])) {
  nd0<-df; nd1<-df; change<-as.numeric(.sb_input("exposure_change",1)); nd1[[exposure_v]]<-nd1[[exposure_v]]+change; labels<-paste0("数值暴露增加 ",change)
} else {
  if(nlevels(df[[exposure_v]])!=2L) stop("当前平均预测对比需二元分类暴露；连续暴露保留原始尺度")
  nd0<-df; nd1<-df; nd0[[exposure_v]]<-factor(levels(df[[exposure_v]])[1],levels=levels(df[[exposure_v]])); nd1[[exposure_v]]<-factor(levels(df[[exposure_v]])[2],levels=levels(df[[exposure_v]])); labels<-paste(levels(df[[exposure_v]])[2],"减",levels(df[[exposure_v]])[1])
}
predtype<-if(method=="count") "response" else "probs"
p0<-stats::predict(fit,newdata=nd0,type=predtype); p1<-stats::predict(fit,newdata=nd1,type=predtype)
marginal_table<-if(method=="count") data.frame(对比=labels,平均计数变化=mean(p1-p0)) else data.frame(对比=labels,类别=colnames(p0),平均概率变化=colMeans(p1-p0))
counts<-if(method=="count") table(df[[outcome_v]]) else table(df[[outcome_v]],useNA="no")
assumptions_table<-data.frame(诊断=c("收敛","比例优势联合检验 P","Pearson 离散比"),结果=c("已收敛",if(is.na(po_p)) "不适用或无法估计" else as.character(po_p),if(is.na(dispersion)) "不适用" else as.character(dispersion)))
.sb_table("model_summary",data.frame(方法=method,n=nrow(df),AIC=stats::AIC(fit)),"所选模型概览")
.sb_table("pred_probs",pred_table,"逐观测预测概率 / 预测计数")
.sb_table("assumptions",assumptions_table,"模型诊断")
.sb_table("coef_table",coef_table,"模型斜率与切点")
.sb_table("marginal",marginal_table,"经验总体平均预测变化")
.sb_table("sparsity_table",data.frame(诊断=paste0("结局类别覆盖：",names(counts)),结局值=names(counts),频数=as.integer(counts),状态=ifelse(as.integer(counts)<5L,"warning","passed")),"类别覆盖与稀疏诊断")
.sb_text("results_text",paste0("按明确选择的 ",method," 模型拟合；连续暴露保留原始尺度。预测对比为经验总体平均预测变化，不与逐观测预测表混用。"),"分类/计数模型结果")
.diag_status[["比例优势假设检查"]]<-if(method!="ordinal" || is.na(po_p)) "not_assessed" else if(po_p<.05) "warning" else "passed"
.diag_status[["过度离散检查"]]<-if(method!="count") "not_assessed" else if(dispersion>1.5) "warning" else "passed"
.diag_status[["类别稀疏检查"]]<-if(any(counts<5L)) "warning" else "passed"
'''

_EFFECT = r'''
reference<-.sb_input("reference_group"); pair<-.sb_input("pair_id"); df<-.sb_cols(c(outcome_v,group_v,pair)); df<-.sb_complete(df,4L); df[[group_v]]<-.sb_group(df[[group_v]],reference)
lv<-levels(df[[group_v]]); g<-df[[group_v]]==lv[2]; n1<-sum(!g); n2<-sum(g); y<-df[[outcome_v]]
if(!is.null(pair)) {
  if(!is.numeric(y)||is.factor(y)) stop("配对效应量当前需要连续结局")
  if(anyDuplicated(df[,c(pair,group_v)])) stop("每个配对 ID 在每个组应恰好一行")
  aa<-df[!g,c(pair,outcome_v),drop=FALSE]; bb<-df[g,c(pair,outcome_v),drop=FALSE]; names(aa)<-c("id","a"); names(bb)<-c("id","b"); wide<-merge(aa,bb,by="id")
  if(nrow(wide)!=n1 || nrow(wide)!=n2) stop("配对 ID 未完整覆盖两个组")
  delta<-wide$b-wide$a; n<-length(delta); if(n<3L || stats::sd(delta)==0) stop("配对差值数量或变异不足")
  tt<-stats::t.test(delta); d<-mean(delta)/stats::sd(delta); se_d<-sqrt(1/n+d^2/(2*(n-1))); correction<-exp(lgamma((n-1)/2)-.5*log((n-1)/2)-lgamma((n-2)/2)); hg<-correction*d
  eff_table<-data.frame(指标=c("配对均值差","Cohen dz","Hedges gz"),效应=c(mean(delta),d,hg),CI下限=c(tt$conf.int[1],d-stats::qnorm(.975)*se_d,hg-stats::qnorm(.975)*correction*se_d),CI上限=c(tt$conf.int[2],d+stats::qnorm(.975)*se_d,hg+stats::qnorm(.975)*correction*se_d),区间方法=c("配对 t","Wald 近似","Wald 近似"))
} else if(is.numeric(y) && !is.factor(y)) {
  .sb_numeric(df,outcome_v); if(min(n1,n2)<2L) stop("每组至少两个完整观测")
  a<-y[!g]; b<-y[g]; dof<-n1+n2-2L; pooled<-sqrt(((n1-1)*stats::var(a)+(n2-1)*stats::var(b))/dof); if(pooled==0) stop("组内方差为零")
  d<-(mean(b)-mean(a))/pooled; correction<-exp(lgamma(dof/2)-.5*log(dof/2)-lgamma((dof-1)/2)); hg<-correction*d
  se_d<-sqrt((n1+n2)/(n1*n2)+d^2/(2*dof)); tt<-stats::t.test(b,a)
  eff_table<-data.frame(指标=c("Welch 均值差","Cohen d","Hedges g"),效应=c(mean(b)-mean(a),d,hg),CI下限=c(tt$conf.int[1],d-stats::qnorm(.975)*se_d,hg-stats::qnorm(.975)*correction*se_d),CI上限=c(tt$conf.int[2],d+stats::qnorm(.975)*se_d,hg+stats::qnorm(.975)*correction*se_d),区间方法=c("Welch t","Wald 近似","Wald 近似"))
} else {
  yy<-droplevels(factor(y)); if(nlevels(yy)!=2L) stop("分类结局需要二分类")
  positive<-as.character(.sb_input("positive_class",levels(yy)[2])); if(!positive%in%levels(yy)) stop("阳性类别无效")
  yy<-as.character(yy)==positive; tab<-matrix(c(sum(yy[g]),sum(!yy[g]),sum(yy[!g]),sum(!yy[!g])),nrow=2,byrow=TRUE)
  ft<-stats::fisher.test(tab); orv<-unname(ft$estimate)
  eff_table<-data.frame(指标=paste0("条件最大似然 OR，事件=",positive),效应=orv,CI下限=ft$conf.int[1],CI上限=ft$conf.int[2],区间方法="Fisher 条件精确区间")
}
precision_table<-data.frame(指标=eff_table$指标,CI宽度=eff_table$CI上限-eff_table$CI下限,参照组=lv[1],比较组=lv[2],配对=!is.null(pair))
.sb_table("eff_table",eff_table,"带明确方向与区间方法的效应量")
.sb_table("precision_table",precision_table,"效应估计精度与组别")
.sb_text("results_text",paste0("效应方向均为 ",lv[2]," 相对 ",lv[1],"；数据按已确认数值/分类角色处理，未按数值水平数重分类。"),"效应方向说明")
.sb_text("method_note","连续结局分别报告原尺度差异及标准化效应；Hedges 使用 gamma 函数偏倚校正；分类结局采用 Fisher 条件精确 OR 和区间。实际意义阈值须来自研究方案，不自动套用大小标签。","方法说明")
.sb_text("results_paper",paste(capture.output(print(eff_table,row.names=FALSE)),collapse="\n"),"可复核数值结果")
.diag_status[["模型/方法适配性检查"]]<-"passed"
.diag_status[["样本量、事件数或设计效应充分性检查"]]<-if(min(n1,n2)<20L) "warning" else "passed"
'''

_BOOTSTRAP = r'''
.sb_numeric(dataset,outcome_v); group<-.sb_input("group"); pair<-.sb_input("pair_id"); cluster<-.sb_input("cluster_id")
if(!is.null(pair)&&!is.null(cluster)) stop("当前不支持同时配对并聚类的嵌套重采样")
df<-.sb_complete(.sb_cols(c(outcome_v,group,pair,cluster)),4L)
B<-as.integer(.sb_input("resamples",2000L)); seed<-as.integer(.sb_input("seed",1729L)); if(B<199L || B>100000L || length(B)!=1L || is.na(B)) stop("重采样次数需在 199–100000")
set.seed(seed); y<-df[[outcome_v]]; perm_p<-NA_real_; classic_p<-NA_real_
if(is.null(group)) {
  if(!is.null(pair)) stop("配对重采样需要分组变量")
  obs<-mean(y); units<-if(is.null(cluster)) as.list(seq_len(nrow(df))) else split(seq_len(nrow(df)),df[[cluster]])
  if(length(units)<3L) stop("至少需要三个独立重采样单位")
  boot<-replicate(B,{ ii<-unlist(units[sample.int(length(units),length(units),replace=TRUE)],use.names=FALSE); mean(y[ii]) })
  description<-"单总体均值"; unit_name<-if(is.null(cluster)) "观测行" else "聚类 ID"
} else {
  df[[group]]<-.sb_group(df[[group]],.sb_input("reference_group")); lv<-levels(df[[group]]); g<-df[[group]]==lv[2]
  if(!is.null(pair)) {
    if(anyDuplicated(df[,c(pair,group)])) stop("每个配对 ID 在每组只能一行")
    aa<-df[!g,c(pair,outcome_v),drop=FALSE]; bb<-df[g,c(pair,outcome_v),drop=FALSE]; names(aa)<-c("id","a");names(bb)<-c("id","b"); w<-merge(aa,bb,by="id")
    if(nrow(w)!=sum(g)||nrow(w)!=sum(!g)) stop("需要完整一一配对")
    delta<-w$b-w$a; n<-length(delta); if(n<3L) stop("至少需要三个配对")
    obs<-mean(delta); boot<-replicate(B,mean(delta[sample.int(n,n,replace=TRUE)])); perm<-replicate(B,mean(delta*sample(c(-1,1),n,replace=TRUE))); classic_p<-tryCatch(stats::t.test(delta)$p.value,error=function(e) NA_real_); unit_name<-"完整配对 ID"
  } else if(!is.null(cluster)) {
    units<-split(seq_len(nrow(df)),df[[cluster]]); ug<-vapply(units,function(ii) { z<-unique(g[ii]); if(length(z)!=1L) stop("聚类置换需每个聚类属于一个组"); z },logical(1))
    n0<-sum(!ug); n1<-sum(ug); if(min(n0,n1)<2L) stop("每组至少两个独立聚类")
    obs<-mean(y[g])-mean(y[!g])
    boot<-replicate(B,{i0<-unlist(units[which(!ug)[sample.int(n0,n0,replace=TRUE)]],use.names=FALSE); i1<-unlist(units[which(ug)[sample.int(n1,n1,replace=TRUE)]],use.names=FALSE);mean(y[i1])-mean(y[i0])})
    perm<-replicate(B,{pg<-sample(ug);mean(y[unlist(units[pg],use.names=FALSE)])-mean(y[unlist(units[!pg],use.names=FALSE)])}); unit_name<-"独立聚类 ID"
  } else {
    if(min(sum(g),sum(!g))<2L) stop("每组至少两个完整观测")
    obs<-mean(y[g])-mean(y[!g]); a<-y[!g]; b<-y[g]
    boot<-replicate(B,mean(b[sample.int(length(b),length(b),replace=TRUE)])-mean(a[sample.int(length(a),length(a),replace=TRUE)]))
    perm<-replicate(B,{ pg<-sample(g);mean(y[pg])-mean(y[!pg]) });classic_p<-tryCatch(stats::t.test(y[g],y[!g])$p.value,error=function(e) NA_real_); unit_name<-"组内观测行"
  }
  perm_p<-(1+sum(abs(perm)>=abs(obs)))/(B+1); description<-paste0(lv[2]," 减 ",lv[1])
}
ci<-stats::quantile(boot,c(.025,.975),names=FALSE); se_boot<-stats::sd(boot); basic_ci<-2*obs-rev(ci)
half<-floor(B/2); mc_delta<-max(abs(stats::quantile(boot[seq_len(half)],c(.025,.975),names=FALSE)-stats::quantile(boot[seq.int(half+1L,B)],c(.025,.975),names=FALSE)))
boot_table<-data.frame(统计量=description,估计=obs,百分位CI下限=ci[1],百分位CI上限=ci[2],基本法CI下限=basic_ci[1],基本法CI上限=basic_ci[2],Bootstrap标准误=se_boot,分半CI最大差异=mc_delta,置换P=perm_p,经典t检验P=classic_p,B=B,随机种子=seed,重采样单位=unit_name)
fig<-ggplot2::ggplot(data.frame(估计=boot),ggplot2::aes(x=估计))+ggplot2::geom_histogram(bins=40)+ggplot2::geom_vline(xintercept=c(obs,ci),linetype=c(1,2,2))+ggplot2::theme_bw()
.sb_table("boot_table",boot_table,"符合独立 / 配对 / 聚类结构的重采样推断")
.sb_plot("boot_hist",fig,"Bootstrap 分布")
.sb_text("results_text",paste0("统计量：",description,"；重采样单位：",unit_name,"；固定种子 ",seed,"，B=",B,"。无分组时仅估计单总体均值，不人为构造组别。置换 P 使用 (极端次数+1)/(B+1)；配对使用差值符号置换，聚类保持整组。配对符号置换及组标签置换的可交换性仍由研究设计决定。"),"重采样方法与结果")
.diag_status[["重采样单位检查"]]<-"passed"
.diag_status[["重复次数与稳定性检查"]]<-if(B<2000L || mc_delta>.2*max(se_boot,.Machine$double.eps)) "warning" else "passed"
.diag_status[["区间方法敏感性比较"]]<-if(max(abs(ci-basic_ci))>max(se_boot,.Machine$double.eps)) "warning" else "passed"
'''

_ROBUST = r'''
cont<-as.character(cont_v); group<-.sb_input("group"); df<-.sb_cols(c(cont,group)); .sb_numeric(df,cont); df<-.sb_complete(df,4L)
if(!is.null(group)) df[[group]]<-factor(df[[group]])
rows<-lapply(cont,function(v){ x<-df[[v]]; sdv<-stats::sd(x); madv<-stats::mad(x); qs<-stats::quantile(x,c(.25,.75),names=FALSE); iq<-diff(qs);data.frame(变量=v,完整数=length(x),中位数=stats::median(x),IQR=iq,MAD=madv,Z超过3=if(sdv>0) sum(abs((x-mean(x))/sdv)>3) else 0L,MAD标准化超过3=if(madv>0) sum(abs((x-stats::median(x))/madv)>3) else NA_integer_,IQR外数=sum(x<qs[1]-1.5*iq|x>qs[2]+1.5*iq)) })
out_table<-do.call(rbind,rows)
if(length(cont)>=2L || !is.null(group)) {
  predictors<-c(cont[-1],group); form<-.sb_formula(cont[1],predictors)
  ols<-stats::lm(form,data=df); .sb_check_fit(ols)
  fit<-MASS::rlm(form,data=df,psi=MASS::psi.huber,maxit=100L)
  if(!isTRUE(fit$converged)||any(!is.finite(stats::coef(fit)))) stop("Huber 回归未收敛")
  cf<-stats::coef(fit); olcf<-stats::coef(ols); cook<-stats::cooks.distance(ols)
  sens_table<-data.frame(变量=names(cf),OLS=unname(olcf[names(cf)]),Huber=unname(cf),差异=unname(cf-olcf[names(cf)]),Huber标准误=unname(stats::coef(summary(fit))[,2]),推断说明="不生成未经验证的 rlm P 值或 t 区间")
  n_influential<-sum(cook>4/nrow(df)); .diag_status[["共线性与影响点检查"]]<-if(n_influential>0) "warning" else "passed"
  .diag_status[["残差/拟合诊断"]]<-if(sum(fit$w<.5)>0L) "warning" else "passed"
} else { sens_table<-data.frame(说明="单变量描述性稳健统计；未指定回归关系"); n_influential<-NA_integer_ }
.sb_table("out_table",out_table,"离群点、MAD 与 IQR 诊断")
.sb_table("sens_table",sens_table,"同一完整样本的 OLS 与 Huber 系数对比")
.sb_text("text_1",paste0("分析连续角色：",paste(cont,collapse=", "),"；未自动删除离群点。"),"分析概要")
.sb_text("method_note","若有多个连续变量，第一列为结局，后续列为连续预测变量；可选组别加入同一模型。比较同一样本 OLS 与 Huber M 估计，不将 rlm 的 t 值误报为 P 值。","稳健统计方法")
.sb_text("results_text",paste0("完整样本 n=",nrow(df),"；OLS Cook 距离 >4/n 的高影响点数：",n_influential,"。",paste(capture.output(print(sens_table,row.names=FALSE)),collapse="\n")),"稳健估计敏感性结果")
.diag_status[["模型/方法适配性检查"]]<-"passed"; .diag_status[["变量类型与编码一致性检查"]]<-"passed"
'''

_SUBGROUP = r'''
covs<-as.character(.sb_input("covariates",character(0))); if(anyDuplicated(c(outcome_v,exposure_v,subgroup_v,covs))) stop("变量角色不可重叠")
df<-.sb_complete(.sb_cols(c(outcome_v,exposure_v,subgroup_v,covs)),12L); df[[exposure_v]]<-.sb_group(df[[exposure_v]],.sb_input("reference_group"));df[[subgroup_v]]<-droplevels(factor(df[[subgroup_v]]))
if(nlevels(df[[subgroup_v]])<2L) stop("至少需要两个亚组")
for(nm in covs) if(is.character(df[[nm]])) df[[nm]]<-factor(df[[nm]])
binary<-is.factor(df[[outcome_v]]) || is.character(df[[outcome_v]])
if(binary) { yy<-.sb_group(df[[outcome_v]]); positive<-as.character(.sb_input("positive_class",levels(yy)[2])); if(!positive%in%levels(yy)) stop("阳性类别无效"); df[[outcome_v]]<-as.integer(as.character(yy)==positive) } else .sb_numeric(df,outcome_v)
fam<-if(binary) stats::binomial() else stats::gaussian(); effect_name<-if(binary) "OR" else "均值差"
.estimate <- function(dat,covset) {
  fit<-stats::glm(.sb_formula(outcome_v,c(exposure_v,covset)),data=droplevels(dat),family=fam); .sb_check_fit(fit)
  design<-stats::model.matrix(fit); terms_obj<-stats::terms(fit); exposure_term<-match(paste0("`",exposure_v,"`"),attr(terms_obj,"term.labels")); if(is.na(exposure_term)) exposure_term<-match(exposure_v,attr(terms_obj,"term.labels"))
  col<-which(attr(design,"assign")==exposure_term); if(length(col)!=1L) stop("二元暴露效应无法唯一识别")
  beta<-stats::coef(fit)[col]; se<-sqrt(diag(stats::vcov(fit)))[col]; cr<-if(binary) stats::qnorm(.975) else stats::qt(.975,stats::df.residual(fit)); trans<-if(binary) exp else identity
  data.frame(效应=unname(trans(beta)),CI下限=unname(trans(beta-cr*se)),CI上限=unname(trans(beta+cr*se)),样本量=nrow(dat))
}
rows<-list(); overall<-.estimate(df,covs); overall$亚组<-"总体";overall$调整<-"预定协变量";rows[[1]]<-overall
for(gl in levels(df[[subgroup_v]])) {
  sub<-droplevels(df[df[[subgroup_v]]==gl,,drop=FALSE]); rr<-tryCatch(.estimate(sub,covs),error=function(e) data.frame(效应=NA_real_,CI下限=NA_real_,CI上限=NA_real_,样本量=nrow(sub)))
  rr$亚组<-gl;rr$调整<-"预定协变量";rows[[length(rows)+1L]]<-rr
}
if(length(covs)) { unadjusted<-.estimate(df,character(0));unadjusted$亚组<-"总体";unadjusted$调整<-"未调整（同一样本）";rows[[length(rows)+1L]]<-unadjusted }
sens_table<-do.call(rbind,rows)
form0<-.sb_formula(outcome_v,c(exposure_v,subgroup_v,covs)); rhs1<-c(paste0("`",exposure_v,"`*`",subgroup_v,"`"),sprintf("`%s`",covs)); form1<-stats::as.formula(paste0("`",outcome_v,"` ~ ",paste(rhs1,collapse=" + ")))
f0<-stats::glm(form0,data=df,family=fam); f1<-stats::glm(form1,data=df,family=fam);.sb_check_fit(f0);.sb_check_fit(f1)
interaction_test<-stats::anova(f0,f1,test=if(binary) "Chisq" else "F"); interaction_p<-interaction_test[2,ncol(interaction_test)]
fig<-ggplot2::ggplot(sens_table,ggplot2::aes(x=效应,y=interaction(亚组,调整)))+ggplot2::geom_pointrange(ggplot2::aes(xmin=CI下限,xmax=CI上限))+ggplot2::geom_vline(xintercept=if(binary) 1 else 0,linetype=2)+ggplot2::theme_bw()
.sb_table("sens_table",sens_table,"亚组效应及同一样本调整集敏感性")
.sb_plot("forest_fig",fig,"亚组效应森林图")
.sb_text("results_text",sprintf("效应类型 %s；暴露×亚组联合交互检验 P=%.8g。比较亚组效应须以交互检验为准，不以两组 P 值差异代替。",effect_name,interaction_p),"亚组结果")
.sb_text("method_note","数值结局保留连续角色，分类二元结局采用 Logistic；连续模型使用残差自由度 t 区间，Logistic 使用 Wald 区间。当前支持二元暴露，各层失败会明确留空。","亚组方法")
.sb_text("paper_text",paste(capture.output(print(sens_table,row.names=FALSE)),collapse="\n"),"亚组完整效应表说明")
.sb_text("diag_text",paste0("未估计亚组：",paste(sens_table$亚组[is.na(sens_table$效应)],collapse=", "),"；多重亚组结果作探索性解释，未自动构造替代变量定义。"),"亚组覆盖诊断")
.diag_status[["亚组样本量、交互项和多重比较检查"]]<-if(any(sens_table$样本量<20L)||anyNA(sens_table$效应)) "warning" else "passed"
.diag_status[["模型/方法适配性检查"]]<-"passed"
'''

_RELIABILITY = r'''
items<-as.character(cat_v); dat<-.sb_cols(items); .sb_numeric(dat); keep<-stats::complete.cases(dat); dat<-.sb_complete(dat,10L)
reverse<-as.character(.sb_input("reverse_item_indicator",character(0)));dat<-.sb_reverse(dat,reverse)
if(length(items)<2L || any(vapply(dat,stats::sd,numeric(1))==0)) stop("至少两个非常数条目")
dimensions<-.sb_input("dimension_group"); sets<-list(整体=items)
if(!is.null(dimensions)) {
  if(is.character(dimensions) && length(dimensions)==1L) dimensions<-jsonlite::fromJSON(dimensions,simplifyVector=TRUE)
  if(!is.list(dimensions)||is.null(names(dimensions))) stop("维度映射应为 JSON 对象：维度名 → 条目数组")
  for(dn in names(dimensions)) { ss<-as.character(dimensions[[dn]]);if(length(ss)<2L || any(!ss%in%items)) stop("各维度至少两个已选条目");sets[[dn]]<-ss }
}
relrows<-list(); itemrows<-list(); droprows<-list()
for(dn in names(sets)) {
  ss<-sets[[dn]]; dd<-dat[,ss,drop=FALSE]; k<-length(ss); covmat<-stats::cov(dd); alpha<-k/(k-1)*(1-sum(diag(covmat))/sum(covmat)); if(!is.finite(alpha)) stop("总分方差不足")
  omega<-NA_real_; if(k>=3L) omega<-tryCatch({ ff<-psych::fa(dd,nfactors=1L,fm="minres",rotate="none",scores="none"); ll<-as.numeric(ff$loadings); if(any(ff$uniquenesses<0)) NA_real_ else sum(ll)^2/(sum(ll)^2+sum(ff$uniquenesses)) },error=function(e) NA_real_)
  relrows[[length(relrows)+1L]]<-data.frame(维度=dn,条目数=k,n=nrow(dd),Cronbach_alpha=alpha,单因子omega_total=omega)
  total<-rowSums(dd)
  for(it in ss) {
    rest<-total-dd[[it]]; r<-if(stats::sd(rest)>0) stats::cor(dd[[it]],rest) else NA_real_
    itemrows[[length(itemrows)+1L]]<-data.frame(维度=dn,条目=it,校正条目总分相关=r,条目方差=stats::var(dd[[it]]),反向编码=it%in%reverse)
    alpha_del<-if(k==2L) NA_real_ else { cm<-stats::cov(dd[,setdiff(ss,it),drop=FALSE]); (k-1)/(k-2)*(1-sum(diag(cm))/sum(cm)) }
    droprows[[length(droprows)+1L]]<-data.frame(维度=dn,删除条目=it,删除后alpha=alpha_del,alpha变化=alpha_del-alpha)
  }
}
rel_table<-do.call(rbind,relrows);it_table<-do.call(rbind,itemrows);del_table<-do.call(rbind,droprows)
cor_table<-as.data.frame(as.table(stats::cor(dat)));names(cor_table)<-c("条目1","条目2","Pearson相关")
external<-.sb_input("total_score_subscale"); if(!is.null(external)) {
  .sb_numeric(dataset,external); yy<-dataset[[external]][keep];external_cor<-stats::cor(rowSums(dat),yy,use="complete.obs")
} else external_cor<-NA_real_
.sb_table("rel_table",rel_table,"整体 / 维度 alpha 与单因子 omega")
.sb_table("it_table",it_table,"校正项目–总分相关与反向项")
.sb_table("corr_table",cor_table,"条目间相关矩阵长表")
.sb_table("del_table",del_table,"删题后 alpha（两条目时不伪造单条目信度）")
.sb_text("drop_text",paste0("删题后 alpha 增加 >.02 的条目：",paste(del_table$删除条目[which(del_table$alpha变化>.02)],collapse=", "),"。仅提供复核信息，不自动删除题项。"),"项目分析建议")
.sb_text("results_text",paste0("完整病例 n=",nrow(dat),"；反向项按负向编码计算协方差，不导出未经量表界值定义的原尺度反向得分。omega 为单因子 omega_total，无法估计留空；不是 omega_h。外部总分相关=",external_cor,"。",paste(capture.output(print(rel_table,row.names=FALSE)),collapse="\n")),"信度结果与计分范围")
.diag_status[["反向题检查"]]<-if(any(it_table$校正条目总分相关<0,na.rm=TRUE)) "warning" else "passed"
.diag_status[["缺失和极端反应检查"]]<-if(sum(!keep)>0 || any(it_table$校正条目总分相关<.3,na.rm=TRUE)) "warning" else "passed"
'''

_EFA = r'''
items<-as.character(cat_v); dat<-.sb_cols(items);.sb_numeric(dat);keep<-stats::complete.cases(dat);dat<-.sb_complete(dat,20L);dat<-.sb_reverse(dat,.sb_input("reverse_item_indicator",character(0)))
if(length(items)<4L || any(vapply(dat,stats::sd,numeric(1))==0)) stop("EFA 需要至少四个非常数条目")
if(any(eigen(stats::cor(dat),symmetric=TRUE,only.values=TRUE)$values<1e-8)) stop("条目相关矩阵奇异")
seed<-as.integer(.sb_input("seed",1729L));set.seed(seed)
parallel<-suppressWarnings(psych::fa.parallel(dat,fa="fa",fm="pa",n.iter=100L,plot=FALSE,error.bars=FALSE))
requested<-.sb_input("dimension"); nfact<-if(is.null(requested)) parallel$nfact else as.integer(requested)
if(length(nfact)!=1L || is.na(nfact) || nfact<1L || nfact>=length(items)) stop("因子数需在 1 与条目数−1 之间；未确认时使用并行分析，不能任意设两因子")
rotation<-as.character(.sb_input("rotation","varimax")); if(!rotation%in%c("varimax","oblimin","none")) stop("旋转支持 varimax / oblimin / none")
fa_res<-psych::fa(dat,nfactors=nfact,rotate=if(nfact==1L) "none" else rotation,fm="pa",scores="regression")
loads<-unclass(fa_res$loadings);load_table<-data.frame(条目=rownames(loads),loads,check.names=FALSE,row.names=NULL)
adequacy<-psych::KMO(stats::cor(dat));bart<-psych::cortest.bartlett(stats::cor(dat),n=nrow(dat));kmo<-adequacy$MSA
communality_table<-data.frame(条目=items,共同度=fa_res$communality,独特性=fa_res$uniquenesses,交叉载荷=rowSums(abs(loads)>=.35)>1L)
score_table<-as.data.frame(fa_res$scores);subject<-.sb_input("sample_id");score_table$样本ID<-if(is.null(subject)) which(keep) else as.character(dataset[[subject]][keep]);score_table<-score_table[,c(ncol(score_table),seq_len(ncol(score_table)-1)),drop=FALSE]
scree<-data.frame(因子=seq_along(parallel$fa.values),观测特征值=parallel$fa.values,随机参考=parallel$fa.sim)
fig<-ggplot2::ggplot(scree,ggplot2::aes(x=因子))+ggplot2::geom_line(ggplot2::aes(y=观测特征值,colour="观测"))+ggplot2::geom_line(ggplot2::aes(y=随机参考,colour="模拟"))+ggplot2::theme_bw()
adequacy_table<-data.frame(检验=c("总体 KMO","Bartlett 卡方","Bartlett df","Bartlett P","并行分析建议因子数","实际因子数"),结果=c(kmo,bart$chisq,bart$df,bart$p.value,parallel$nfact,nfact))
.sb_table("loadings",load_table,"PA 旋转载荷")
.sb_plot("scree_fig",fig,"因子并行分析（固定种子）")
.sb_table("scores_table",score_table,"完整病例因子得分与样本 ID")
.sb_table("adequacy_table",adequacy_table,"KMO / Bartlett / 因子数依据")
.sb_table("loadings_table",communality_table,"共同度、独特性与交叉载荷")
.sb_text("results_text",sprintf("EFA 使用主轴因子法 pa，旋转 %s，因子数 %d（%s），完整病例 n=%d。反向项只改变协方差方向；得分是标准化因子得分。因子命名由研究者根据载荷给定。",if(nfact==1L) "none" else rotation,nfact,if(is.null(requested)) "并行分析" else "预先指定",nrow(dat)),"EFA 方法与因子数")
.diag_status[["KMO 适配性"]]<-if(kmo<.6) "warning" else "passed"
.diag_status[["共同度检查"]]<-if(any(communality_table$共同度<.4)||any(communality_table$独特性<0)) "warning" else "passed"
.diag_status[["交叉载荷检查"]]<-if(any(communality_table$交叉载荷)) "warning" else "passed"
'''

_CFA = r'''
items<-as.character(cat_v); group<-.sb_input("group"); dat<-.sb_complete(.sb_cols(c(items,group)),30L)
model<-paste(as.character(.sb_input("latent_variables")),collapse="\n");path<-paste(as.character(.sb_input("path_specification","")),collapse="\n");method<-as.character(.sb_input("analysis_method","cfa"))
if(!method%in%c("cfa","sem") || !nzchar(model)) stop("选择 cfa / sem 并提供测量模型语法")
if(method=="cfa"&&nzchar(path)) stop("结构路径请明确选择 sem，不将 CFA 静默替换成 SEM")
if(method=="sem"&&!nzchar(path)) stop("SEM 需要明确结构路径")
full_model<-paste(model,if(method=="sem") path else "",sep="\n")
ordered_items<-items[vapply(dat[,items,drop=FALSE],is.ordered,logical(1))]
for(nm in setdiff(items,ordered_items)) if(!is.numeric(dat[[nm]]) || is.factor(dat[[nm]])) stop("连续条目需数值列；有序条目需 ordered factor：",nm)
if(!is.null(group)) dat[[group]]<-factor(dat[[group]])
args<-list(model=full_model,data=dat,std.lv=TRUE,estimator=if(length(ordered_items)) "WLSMV" else "ML")
if(length(ordered_items)) args$ordered<-ordered_items
if(!is.null(group)) args$group<-group
fit<-do.call(if(method=="sem") lavaan::sem else lavaan::cfa,args)
if(!isTRUE(lavaan::lavInspect(fit,"converged"))) stop("模型未收敛")
postcheck<-isTRUE(lavaan::lavInspect(fit,"post.check")); est<-lavaan::parameterEstimates(fit,standardized=TRUE,ci=TRUE)
if(is.null(est$group)) est$group<-1L
load_table<-est[est$op=="=~",c("lhs","rhs","group","est","se","ci.lower","ci.upper","pvalue","std.all"),drop=FALSE]
struct_table<-est[est$op%in%c("~",":="),c("lhs","op","rhs","group","est","se","ci.lower","ci.upper","pvalue","std.all"),drop=FALSE]
fm<-lavaan::fitMeasures(fit,c("cfi","tli","rmsea","srmr","chisq","df","pvalue"));fit_table<-data.frame(指标=names(fm),值=unname(fm))
all_paths<-est[est$op%in%c("=~","~","~~",":="),c("lhs","op","rhs","group","est","std.all"),drop=FALSE]
figdata<-est[est$op%in%c("=~","~"),,drop=FALSE];figdata$路径<-paste(figdata$lhs,figdata$op,figdata$rhs,"组",figdata$group)
fig<-ggplot2::ggplot(figdata,ggplot2::aes(x=stats::reorder(路径,std.all),y=std.all))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw()+ggplot2::labs(title="标准化路径系数（非拓扑路径图）")
mi<-tryCatch(lavaan::modindices(fit,sort.=TRUE,maximum.number=10L),error=function(e) NULL)
.sb_plot("path_fig",fig,"标准化载荷与结构系数")
.sb_table("load_table",load_table,"测量载荷、区间与组别")
.sb_table("fit_table",fit_table,"拟合统计量（不将缺失值视为通过）")
if(method=="sem") .sb_table("struct_table",struct_table,"结构路径及自定义间接效应")
.sb_text("syntax_table",full_model,"实际执行模型语法")
.sb_table("load_path_table",all_paths,"载荷、路径、协方差与定义参数")
.sb_text("path_note_text",paste0("图展示标准化载荷和实际结构系数；方法=",method,"，估计器=",args$estimator,"，分组数=",lavaan::lavInspect(fit,"ngroups"),"。按组展示系数，不将未指定的中介/调节路径加入模型。"),"路径与估计方式说明")
.diag_status[["CFI/TLI/RMSEA/SRMR"]]<-if(anyNA(fm[c("cfi","tli","rmsea","srmr")])) "not_assessed" else if(fm[["cfi"]]<.95||fm[["rmsea"]]>.08) "warning" else "passed"
.diag_status[["Heywood 问题检查"]]<-if(!postcheck || any(est$op=="~~" & est$lhs==est$rhs & est$est<0)) "warning" else "passed"
.diag_status[["修正指数审慎解释"]]<-"not_assessed"
'''

_MEDIATION = r'''
covs<-as.character(.sb_input("covariates",character(0))); moderator<-.sb_input("moderator"); method<-as.character(.sb_input("analysis_method",if(is.null(moderator)) "mediation" else "moderated_mediation"))
if(!method%in%c("mediation","moderation","moderated_mediation")) stop("方法支持 mediation / moderation / moderated_mediation")
mediator<-.sb_input("mediator"); if(method!="moderation"&&is.null(mediator)) stop("中介分支需要中介变量")
if(method!="mediation"&&is.null(moderator)) stop("调节分支需要调节变量")
df<-.sb_cols(c(predictor_v,mediator,outcome_v,moderator,covs));.sb_numeric(df,c(predictor_v,outcome_v,mediator,moderator));df<-.sb_complete(df,20L)
for(nm in covs) if(is.character(df[[nm]])) df[[nm]]<-factor(df[[nm]])
B<-as.integer(.sb_input("resamples",1000L));if(is.na(B)||B<199L||B>100000L) stop("Bootstrap 次数范围 199–100000")
set.seed(as.integer(.sb_input("seed",1729L)));q<-function(x) paste0("`",x,"`");crhs<-if(length(covs)) paste0(" + ",paste(q(covs),collapse=" + ")) else ""
if(method=="moderation") {
  form<-stats::as.formula(paste0(q(outcome_v)," ~ ",q(predictor_v)," * ",q(moderator),crhs));fit<-stats::lm(form,data=df);.sb_check_fit(fit)
  paths_table<-.sb_ci_table(fit); values<-mean(df[[moderator]])+c(-1,0,1)*stats::sd(df[[moderator]])
  mform<-stats::delete.response(stats::terms(fit)); slopes<-lapply(values,function(w){nd0<-df;nd1<-df;nd0[[moderator]]<-w;nd1[[moderator]]<-w;nd1[[predictor_v]]<-nd0[[predictor_v]]+1; contrast<-colMeans(stats::model.matrix(mform,nd1))-colMeans(stats::model.matrix(mform,nd0));est<-sum(contrast*stats::coef(fit));se<-sqrt(as.numeric(t(contrast)%*%stats::vcov(fit)%*%contrast));data.frame(调节值=w,条件效应=est,CI下限=est-stats::qt(.975,stats::df.residual(fit))*se,CI上限=est+stats::qt(.975,stats::df.residual(fit))*se)})
  decomp_table<-do.call(rbind,slopes);figdata<-data.frame(路径=paste0("W=",signif(values,3)),效应=decomp_table$条件效应,lo=decomp_table$CI下限,hi=decomp_table$CI上限)
} else {
  a_rhs<-if(method=="moderated_mediation") paste0(q(predictor_v)," * ",q(moderator)) else q(predictor_v)
  faform<-stats::as.formula(paste0(q(mediator)," ~ ",a_rhs,crhs));fbform<-stats::as.formula(paste0(q(outcome_v)," ~ ",q(predictor_v)," + ",q(mediator),if(method=="moderated_mediation") paste0(" + ",q(moderator)) else "",crhs))
  fit_a<-stats::lm(faform,data=df);fit_b<-stats::lm(fbform,data=df);.sb_check_fit(fit_a);.sb_check_fit(fit_b)
  a<-.sb_coef(fit_a,predictor_v);b<-.sb_coef(fit_b,mediator);direct<-.sb_coef(fit_b,predictor_v);if(any(!is.finite(c(a,b,direct)))) stop("路径效应无法唯一识别；当前需数值预测、中介和结局")
  interaction<-if(method=="moderated_mediation") { nm<-names(stats::coef(fit_a)); ix<-which(grepl(":",nm,fixed=TRUE));if(length(ix)!=1L) stop("仅支持第一阶段 X×W 调节");unname(stats::coef(fit_a)[ix]) } else 0
  values<-if(method=="moderated_mediation") mean(df[[moderator]])+c(-1,0,1)*stats::sd(df[[moderator]]) else 0
  indirect<-(a+interaction*values)*b
  draws<-replicate(B,{ii<-sample.int(nrow(df),nrow(df),replace=TRUE); aa<-stats::lm(faform,data=df[ii,,drop=FALSE]);bb<-stats::lm(fbform,data=df[ii,,drop=FALSE]); ax<-.sb_coef(aa,predictor_v);bx<-.sb_coef(bb,mediator);ix<-if(method=="moderated_mediation") unname(stats::coef(aa)[which(grepl(":",names(stats::coef(aa)),fixed=TRUE))]) else 0; (ax+ix*values)*bx})
  if(length(values)==1L) draws<-matrix(draws,nrow=1L)
  if(any(!is.finite(draws))) stop("Bootstrap 出现不可估计路径；未丢弃失败重采样")
  intervals<-t(apply(draws,1,stats::quantile,probs=c(.025,.975),names=FALSE))
  paths_table<-rbind(data.frame(模型="中介回归",.sb_ci_table(fit_a)),data.frame(模型="结局回归",.sb_ci_table(fit_b)))
  decomp_table<-data.frame(调节值=values,直接效应=direct,间接效应=indirect,条件总效应=direct+indirect,间接CI下限=intervals[,1],间接CI上限=intervals[,2],B=B)
  figdata<-data.frame(路径=if(method=="mediation") "a×b" else paste0("W=",signif(values,3)),效应=indirect,lo=intervals[,1],hi=intervals[,2])
}
fig<-ggplot2::ggplot(figdata,ggplot2::aes(x=路径,y=效应))+ggplot2::geom_pointrange(ggplot2::aes(ymin=lo,ymax=hi))+ggplot2::geom_hline(yintercept=0,linetype=2)+ggplot2::theme_bw()
.sb_table("paths_table",paths_table,"实际回归路径与 t 区间")
.sb_plot("coef_fig",fig,"条件效应及区间")
.sb_table("decomp_table",decomp_table,"条件效应分解")
.sb_text("method_text",paste0("已选方法 ",method,"；连续线性模型。调节中介实现第一阶段 X×W，第二阶段保留 X、M、W 主效应；其他调节位置不得冒用本分支。Bootstrap 按完整观测重采样。"),"模型形式与限制")
.sb_text("results_text",paste(capture.output(print(decomp_table,row.names=FALSE)),collapse="\n"),"条件过程结果")
.sb_text("diag_text",paste0("完整病例 n=",nrow(df),"；Bootstrap 次数 ",if(method=="moderation") "不适用" else B,"；未依据区间自动断言因果成立。"),"路径诊断")
.diag_status[["模型形式与结局类型匹配检查"]]<-"passed"
.diag_status[["效应量方向、置信区间和影响点检查"]]<-"not_assessed"
'''

_INTERRATER = r'''
group<-.sb_input("group"); df<-.sb_complete(.sb_cols(c(subject_v,rater_v,measure_v,group)),4L)
if(anyDuplicated(df[,c(subject_v,rater_v)])) stop("同一评价对象和评分者只能有一次评分；不得合并重复记录")
raters<-sort(unique(as.character(df[[rater_v]])));if(length(raters)<2L) stop("至少两位评分者")
subjects<-unique(as.character(df[[subject_v]]));wide<-matrix(NA,nrow=length(subjects),ncol=length(raters),dimnames=list(subjects,raters))
if(is.numeric(df[[measure_v]])&&!is.factor(df[[measure_v]])) storage.mode(wide)<-"double"
wide[cbind(match(as.character(df[[subject_v]]),subjects),match(as.character(df[[rater_v]]),raters))]<-if(is.factor(df[[measure_v]])) as.character(df[[measure_v]]) else df[[measure_v]]
method<-as.character(.sb_input("agreement_method",if(is.numeric(df[[measure_v]])&&!is.factor(df[[measure_v]])) "icc" else "kappa"))
if(!method%in%c("icc","kappa","weighted_kappa")) stop("一致性方法仅支持 icc / kappa / weighted_kappa")
if(method=="icc" && (!is.numeric(df[[measure_v]])||is.factor(df[[measure_v]]))) stop("ICC 需要已确认连续评分")
if(method=="weighted_kappa"&&!is.ordered(df[[measure_v]])) stop("加权 Kappa 需要已确认等级顺序的 ordered factor")
if(is.null(group)) subject_group<-rep("总体",length(subjects)) else {
  subject_group<-vapply(subjects,function(s){z<-unique(as.character(df[[group]][as.character(df[[subject_v]])==s]));if(length(z)!=1L) stop("同一对象的组别不一致");z},character(1))
}
rows<-list();coverage<-list();B<-as.integer(.sb_input("resamples",1000L));if(B<199L||B>100000L) stop("Bootstrap 次数范围 199–100000")
set.seed(as.integer(.sb_input("seed",1729L)))
for(gn in unique(subject_group)) {
  ww<-wide[subject_group==gn,,drop=FALSE]
  coverage[[length(coverage)+1L]]<-data.frame(分层=gn,对象数=nrow(ww),完整全评分对象数=sum(stats::complete.cases(ww)))
  if(method=="icc") {
    wwc<-ww[stats::complete.cases(ww),,drop=FALSE];if(nrow(wwc)<3L) stop("ICC 至少三个完整全评分对象")
    ic<-irr::icc(as.data.frame(wwc),model="twoway",type="agreement",unit="single",conf.level=.95)
    rows[[length(rows)+1L]]<-data.frame(分层=gn,评分者对="全部评分者",指标="ICC(A,1)",估计=ic$value,CI下限=ic$lbound,CI上限=ic$ubound,n=nrow(wwc),观察一致率=NA_real_)
  } else for(pr in utils::combn(raters,2L,simplify=FALSE)) {
    wp<-ww[,pr,drop=FALSE];wp<-wp[stats::complete.cases(wp),,drop=FALSE];if(nrow(wp)<3L) stop("评分者对完整数据不足")
    lv<-if(method=="weighted_kappa") levels(df[[measure_v]]) else sort(unique(as.character(wp)))
    wp<-as.data.frame(lapply(as.data.frame(wp),factor,levels=lv));weight<-if(method=="weighted_kappa") "equal" else "unweighted"
    kp<-irr::kappa2(wp,weight=weight,sort.levels=FALSE)
    if(!is.finite(kp$value)) stop("Kappa 无法估计，边际类别可能恒定")
    draws<-replicate(B,{ii<-sample.int(nrow(wp),nrow(wp),replace=TRUE);suppressWarnings(irr::kappa2(wp[ii,,drop=FALSE],weight=weight,sort.levels=FALSE)$value)})
    valid<-is.finite(draws);if(mean(valid)<.95) stop("Kappa Bootstrap 超过 5% 不可估计；不发布不稳定区间")
    ci<-stats::quantile(draws[valid],c(.025,.975),names=FALSE)
    rows[[length(rows)+1L]]<-data.frame(分层=gn,评分者对=paste(pr,collapse=" vs "),指标=if(method=="kappa") "Cohen Kappa" else "线性加权 Cohen Kappa",估计=kp$value,CI下限=ci[1],CI上限=ci[2],n=nrow(wp),观察一致率=mean(wp[[1]]==wp[[2]]))
  }
}
kappa_table<-do.call(rbind,rows);coverage_table<-do.call(rbind,coverage)
plotdat<-data.frame(评分者=as.character(df[[rater_v]]),评分=if(is.numeric(df[[measure_v]])&&!is.factor(df[[measure_v]])) df[[measure_v]] else as.integer(factor(df[[measure_v]],levels=levels(factor(df[[measure_v]])))))
fig<-ggplot2::ggplot(plotdat,ggplot2::aes(x=评分者,y=评分))+ggplot2::geom_boxplot()+ggplot2::theme_bw()
.sb_table("kappa_table",kappa_table,"评分者一致性及区间（所有评分者对）")
.sb_plot("rater_fig",fig,"各评分者分布")
.sb_text("rec_text","各评分者对及分层均已报告；不自动用固定阈值宣布编码可接受，按预定编码质量标准判断。","编码质量建议")
.sb_text("method_text",paste0("所选 ",method,"；连续评分为 ICC(A,1)，分类评分遍历全部评分者组合；Kappa 区间按评价对象 Bootstrap，等级顺序以 ordered factor 为准。"),"一致性计算方式")
.sb_text("results_text",paste(capture.output(print(kappa_table,row.names=FALSE)),collapse="\n"),"一致性结果")
.sb_text("diag_text",paste(capture.output(print(coverage_table,row.names=FALSE)),collapse="\n"),"对象与评分覆盖")
.diag_status[["评分者和类别覆盖检查"]]<-if(any(coverage_table$完整全评分对象数<coverage_table$对象数)) "warning" else "passed"
.diag_status[["患病率与偏倚效应检查"]]<-"not_assessed"
.diag_status[["一致性置信区间检查"]]<-if(all(is.finite(kappa_table$CI下限))&&all(is.finite(kappa_table$CI上限))) "passed" else "warning"
'''

_BODIES = {
    "correlation_agreement": _CORRELATION,
    "ancova_adjusted_groups": _ANCOVA,
    "categorical_ordinal_models": _CATEGORICAL,
    "effect_size_precision": _EFFECT,
    "bootstrap_permutation_inference": _BOOTSTRAP,
    "robust_outlier_analysis": _ROBUST,
    "subgroup_sensitivity_basic": _SUBGROUP,
    "scale_reliability": _RELIABILITY,
    "efa_factor": _EFA,
    "cfa_sem": _CFA,
    "mediation_moderation": _MEDIATION,
    "interrater_agreement": _INTERRATER,
}
INPUTS = {
    "correlation_agreement": {"continuous_variables":"cont_v","rater_method_variable":"rater_v","strata":"strata_v","analysis_method":"method_v","resamples":"resamples_v","seed":"seed_v"},
    "ancova_adjusted_groups": {"outcome":"outcome_v","group":"group_v","covariates":"covars_v","covariates_2":"covars2_v","strata":"strata_v"},
    "categorical_ordinal_models": {"outcome":"outcome_v","exposure":"exposure_v","exposure_2":"exposure2_v","covariates":"covars_v","analysis_method":"method_v","count_family":"count_family_v","exposure_change":"exposure_change_v"},
    "effect_size_precision": {"outcome":"outcome_v","group":"group_v","pair_id":"pair_v","reference_group":"reference_v","positive_class":"positive_v"},
    "bootstrap_permutation_inference": {"outcome":"outcome_v","group":"group_v","pair_id":"pair_v","cluster_id":"cluster_v","reference_group":"reference_v","resamples":"resamples_v","seed":"seed_v"},
    "robust_outlier_analysis": {"continuous_variables":"cont_v","group":"group_v"},
    "subgroup_sensitivity_basic": {"outcome":"outcome_v","exposure":"exposure_v","subgroup":"subgroup_v","covariates":"covars_v","reference_group":"reference_v","positive_class":"positive_v"},
    "scale_reliability": {"categorical_variables":"cat_v","reverse_item_indicator":"reverse_v","dimension_group":"dim_v","total_score_subscale":"total_v"},
    "efa_factor": {"categorical_variables":"cat_v","sample_id":"sample_v","dimension":"dim_v","reverse_item_indicator":"reverse_v","seed":"seed_v","rotation":"rotation_v"},
    "cfa_sem": {"categorical_variables":"cat_v","latent_variables":"latent_v","path_specification":"path_v","group":"group_v","analysis_method":"method_v"},
    "mediation_moderation": {"predictor":"predictor_v","mediator":"mediator_v","outcome":"outcome_v","moderator":"moderator_v","covariates":"covars_v","analysis_method":"method_v","resamples":"resamples_v","seed":"seed_v"},
    "interrater_agreement": {"data_object":"subject_v","rater_id":"rater_v","measure":"measure_v","group":"group_v","agreement_method":"agreement_method_v","resamples":"resamples_v","seed":"seed_v"},
}
def _core_for(template_id: str):
    def render(plan: AnalysisPlan) -> str:
        entries = [
            f"{json.dumps(input_id)}={var}"
            for input_id, var in INPUTS[template_id].items()
            if input_id in plan.resolved_inputs
        ]
        return ".sb_inputs <- list(" + ",".join(entries) + ")\n" + _COMMON + _BODIES[template_id]
    return render


CORES = {key: _core_for(key) for key in _BODIES}

ROLE_MAPS = {
    "correlation_agreement": dict(table_1="corr_matrix",table_2="agreement_stats",figure_1="scatter_fig",table_3="agreement_table",figure_2="ba_fig",manuscript_results="results_text"),
    "ancova_adjusted_groups": dict(primary_table="adjusted_table",diagnostic_plot="diag_fig",manuscript_results="results_text"),
    "categorical_ordinal_models": dict(table_1="model_summary",table_2="pred_probs",table_3="assumptions",table_4="coef_table",table_5="marginal",diagnostic_output="sparsity_table",manuscript_results="results_text",effect_estimate_table="coef_table",model_assumption_diagnostics="assumptions"),
    "effect_size_precision": dict(table_1="eff_table",table_2="precision_table",manuscript_results="results_text",method_note="method_note",manuscript_results_2="results_paper"),
    "bootstrap_permutation_inference": dict(primary_table="boot_table",diagnostic_plot="boot_hist",manuscript_results="results_text"),
    "robust_outlier_analysis": dict(text_1="text_1",table_1="out_table",sensitivity_output="sens_table",method_note="method_note",manuscript_results="results_text"),
    "subgroup_sensitivity_basic": dict(forest_plot="forest_fig",sensitivity_output="sens_table",manuscript_results="results_text",method_note="method_note",manuscript_results_2="paper_text",diagnostic_output="diag_text"),
    "scale_reliability": dict(table_1="rel_table",table_2="it_table",analysis_recommendation="drop_text",table_3="corr_table",table_4="del_table",manuscript_results="results_text"),
    "efa_factor": dict(table_1="loadings",figure_1="scree_fig",manuscript_results="results_text",table_2="scores_table",survival_plot="adequacy_table",table_4="loadings_table"),
    "cfa_sem": dict(figure_1="path_fig",table_1="load_table",table_2="fit_table",table_3="struct_table",table_4="syntax_table",table_5="load_path_table",figure_2="path_note_text"),
    "mediation_moderation": dict(table_1="paths_table",figure_1="coef_fig",table_2="decomp_table",method_note="method_text",manuscript_results="results_text",diagnostic_output="diag_text"),
    "interrater_agreement": dict(table_1="kappa_table",figure_1="rater_fig",analysis_recommendation="rec_text",method_note="method_text",manuscript_results="results_text",diagnostic_output="diag_text"),
}

# Filled only with methods exercised by the companion real-R reference cases.
REFERENCE_CASES = {
    "correlation_agreement": "真实 R：Pearson/Spearman/Kendall；ICC(A,1) 与 Bland–Altman 界限；分类 Kappa；保留数值低基数角色。",
    "ancova_adjusted_groups": "真实 R：三组 emmeans 调整均值、全部成对 Tukey 对比、只含组别×协变量的斜率联合检验。",
    "categorical_ordinal_models": "真实 R：multinom/polr/Poisson/NB 分支与 log 暴露时间偏移；连续暴露不分箱；未确认有序等级拒绝。",
    "effect_size_precision": "真实 R：Welch 原尺度差值、完整配对差值、低基数数值保持连续；零单元格 Fisher 条件精确 OR/CI。",
    "bootstrap_permutation_inference": "真实 R：单总体、独立两组、完整配对、聚类采样单位；无分组不造组别；置换 P 加一与不完整配对拒绝。",
    "robust_outlier_analysis": "真实 R：同样本 OLS 与 Huber rlm 系数对比；含高影响点合成数据的参考拟合。",
    "subgroup_sensitivity_basic": "真实 R：连续结局联合交互检验、调整/未调整同一样本对比；效应与残差自由度 t 区间。",
    "scale_reliability": "真实 R：确认反向项 alpha、维度映射、单因子 omega_total、条目相关；两条目删题后信度留空。",
    "efa_factor": "真实 R：主轴因子 pa 的载荷及 KMO 参考；完整 150 个样本 ID 的得分保留；固定种子并行分析。",
    "cfa_sem": "真实 R：cfa/sem 分支语法、多组模型及 WLSMV 有序条目；实际载荷与参数估计对照。",
    "mediation_moderation": "真实 R：连续线性中介、调节、第一阶段调节中介及 Bootstrap；含空格列名参数映射。",
    "interrater_agreement": "真实 R：全部评分者 ICC(A,1)、全部评分者对 Cohen Kappa 与线性加权 Kappa；对象 Bootstrap 及重复记录拒绝。",
}
