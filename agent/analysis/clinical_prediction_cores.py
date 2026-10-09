"""Clinical prediction, actual cutpoint searches and spline/interaction cores."""

from __future__ import annotations

import json

from .meta_time_cores import COMMON
from .models import AnalysisPlan

PROGNOSTIC = r'''
.step<-"prediction-design"
features<-as.character(feat_v);event_v<-.mt_get("event_v");split_v<-.mt_get("split_v")
if(!length(features) || anyDuplicated(features) || any(features %in% c(outcome_v,event_v,split_v))) stop("预测因子须互不重复且不包含结局、事件或验证标记")
method<-.mt_select(.mt_get("method_v",if(length(event_v)) "survival" else "binary"),c("binary","survival"),"预后模型")
df<-.mt_data(c(outcome_v,features,event_v,split_v));df$.row_id<-seq_len(nrow(df))
positive<-as.character(.mt_get("positive_v","1"))
if(method=="binary") {
 if(length(event_v) || length(.mt_get("time_v"))) stop("binary 结局不使用随访事件/时间点；请选择 survival 或删除不适用参数")
 gold<-as.character(df[[outcome_v]])
 if(length(unique(gold))!=2 || !positive %in% gold) stop("二分类结局须有两个类别且阳性类别存在")
 df$.y<-as.integer(gold==positive)
} else {
 if(!length(event_v)) stop("生存预后需要明确事件列")
 df$.time<-.mt_numeric(df[[outcome_v]],"随访时长");df$.event<-.mt_numeric(df[[event_v]],"事件状态")
 if(any(df$.time<=0) || any(!df$.event %in% c(0,1))) stop("生存结局须为正数时长和 0/1 事件")
 evaluation_time<-.mt_get("time_v")
 if(!is.numeric(evaluation_time) || length(evaluation_time)!=1 || !is.finite(evaluation_time) || evaluation_time<=0) stop("生存预测必须指定正数评价时间点")
}
seed<-.mt_integer(.mt_get("seed_v",20261008),0,.Machine$integer.max,"随机种子");set.seed(seed)
if(length(split_v)) {
 flag<-df[[split_v]];level<-.mt_get("validation_v")
 if(!is.null(level)) is_test<-as.character(flag)==as.character(level)
 else if(is.logical(flag)) is_test<-flag
 else if(is.numeric(flag) && all(flag %in% c(0,1))) is_test<-flag==1
 else if(all(as.character(flag) %in% c("train","test"))) is_test<-as.character(flag)=="test"
 else stop("验证标记须为 FALSE/TRUE、0/1、train/test，或明确 validation_level")
} else {
 is_test<-rep(FALSE,nrow(df));strata<-if(method=="binary") df$.y else df$.event
 for(level in unique(strata)) {
  indices<-which(strata==level);is_test[sample(indices,max(1,floor(.3*length(indices))))]<-TRUE
 }
}
train<-df[!is_test,,drop=FALSE];test<-df[is_test,,drop=FALSE]
if(nrow(train)<40 || nrow(test)<20) stop("预后模型训练至少 40 人，验证至少 20 人")
for(v in features) {
 if(is.character(train[[v]]) || is.factor(train[[v]])) {
  train[[v]]<-droplevels(factor(train[[v]]));test[[v]]<-factor(test[[v]],levels=levels(train[[v]]))
  if(anyNA(test[[v]])) stop("验证集有训练集中未出现的预测因子水平：",v)
 } else { .mt_numeric(train[[v]],v);.mt_numeric(test[[v]],v) }
}
.step<-"training-only-fit"
if(method=="binary") {
 if(min(table(factor(train$.y,levels=c(0,1))))<10 || min(table(factor(test$.y,levels=c(0,1))))<2) stop("训练各结局至少十例，验证各结局至少两例")
 form<-.mt_formula(".y",features);fit<-rms::lrm(form,data=train,x=TRUE,y=TRUE)
 if(isTRUE(fit$fail) || any(!is.finite(coef(fit)))) stop("Logistic 预后模型拟合失败")
 prediction<-as.numeric(stats::predict(fit,newdata=test,type="fitted"))
 roc<-pROC::roc(test$.y,prediction,levels=c(0,1),direction="<",quiet=TRUE)
 auc_ci<-suppressWarnings(as.numeric(pROC::ci.auc(roc)))
 auc_table<-data.frame(metric="validation AUC",estimate=as.numeric(pROC::auc(roc)),lower=auc_ci[1],upper=auc_ci[3],time=NA_real_)
 brier<-mean((test$.y-prediction)^2)
 clipped<-pmin(1-1e-8,pmax(1e-8,prediction));lp<-qlogis(clipped)
 cal_intercept<-coef(stats::glm(test$.y~offset(lp),family=binomial()))[1]
 cal_model<-stats::glm(test$.y~lp,family=binomial())
 perf_table<-data.frame(method=method,n_train=nrow(train),n_validation=nrow(test),Brier=brier,
  calibration_intercept=cal_intercept,calibration_slope=coef(cal_model)[2],evaluation_time=NA_real_)
 target<-test$.y;weights<-rep(1,nrow(test))
} else {
 if(sum(train$.event)<10) stop("训练集至少需要十个事件")
 form<-stats::as.formula(paste0("survival::Surv(.time,.event) ~ ",paste(sprintf("`%s`",features),collapse=" + ")))
 fit<-survival::coxph(form,data=train,x=TRUE,y=TRUE,model=TRUE)
 if(any(!is.finite(coef(fit))) || any(!is.finite(vcov(fit)))) stop("Cox 预后系数或协方差不可估计")
 prediction<-as.numeric(riskRegression::predictRisk(fit,newdata=test,times=evaluation_time))
 Surv<-survival::Surv
 scores<-riskRegression::Score(list(Cox=fit),formula=Surv(.time,.event)~1,
  data=test,times=evaluation_time,metrics=c("AUC","Brier"),conf.int=TRUE,
  cens.method="ipcw",cens.model="km",null.model=FALSE)
 a<-as.data.frame(scores$AUC$score);a<-a[as.character(a$model)=="Cox",,drop=FALSE]
 b<-as.data.frame(scores$Brier$score);b<-b[as.character(b$model)=="Cox",,drop=FALSE]
 if(!nrow(a) || !is.finite(a$AUC[1]) || !is.finite(b$Brier[1])) stop("指定时间点的 AUC/Brier 不可估计")
 auc_table<-data.frame(metric="validation IPCW time-dependent AUC",estimate=a$AUC[1],lower=a$lower[1],upper=a$upper[1],time=evaluation_time)
 censor<-survival::survfit(survival::Surv(test$.time,1-test$.event)~1)
 .G<-function(t,left=FALSE) {
  idx<-findInterval(t,censor$time,left.open=left)
  ifelse(idx==0,1,censor$surv[pmax(idx,1)])
 }
 cases<-test$.time<=evaluation_time & test$.event==1;controls<-test$.time>evaluation_time
 g_event<-.G(test$.time,TRUE);g_time<-.G(evaluation_time)
 if(g_time<=.05 || any(g_event[cases]<=.05) || !any(cases) || !any(controls)) stop("评价时间点缺少有效病例/对照或删失权重过于极端")
 weights<-ifelse(cases,1/g_event,ifelse(controls,1/g_time,0));target<-as.integer(cases)
 perf_table<-data.frame(method=method,n_train=nrow(train),n_validation=nrow(test),Brier=b$Brier[1],
  calibration_intercept=NA_real_,calibration_slope=NA_real_,evaluation_time=evaluation_time)
}
if(any(!is.finite(prediction)) || any(prediction<0 | prediction>1)) stop("风险预测不在 [0,1]")
.step<-"held-out-calibration-and-decision"
bins<-cut(rank(prediction,ties.method="first"),breaks=seq(0,nrow(test),length.out=min(6,nrow(test))+1),include.lowest=TRUE)
cal_df<-do.call(rbind,lapply(split(seq_len(nrow(test)),bins),function(i) {
 data.frame(predicted=mean(prediction[i]),observed=if(sum(weights[i])==0) NA_real_ else weighted.mean(target[i],weights[i]),n=length(i))
}))
cal_fig<-ggplot2::ggplot(cal_df,ggplot2::aes(x=predicted,y=observed))+
 ggplot2::geom_point(ggplot2::aes(size=n))+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+
 ggplot2::coord_cartesian(xlim=c(0,1),ylim=c(0,1))+ggplot2::theme_bw()+
 ggplot2::labs(x="留出预测风险",y=if(method=="binary") "观测事件比例" else "IPCW 观测时间点风险",title="独立留出样本校准")
pt<-seq(.01,.99,by=.01);prev<-sum(weights*target)/nrow(test)
dca_data<-do.call(rbind,lapply(pt,function(th) {
 decision<-prediction>=th
 tp<-sum(weights*target*decision)/nrow(test);fp<-sum(weights*(1-target)*decision)/nrow(test)
 data.frame(threshold=th,net_benefit=c(tp-fp*th/(1-th),prev-(sum(weights)/nrow(test)-prev)*th/(1-th),0),
  strategy=c("prediction model","treat all","treat none"))
}))
dca_fig<-ggplot2::ggplot(dca_data,ggplot2::aes(x=threshold,y=net_benefit,colour=strategy))+
 ggplot2::geom_line()+ggplot2::coord_cartesian(ylim=c(-.1,max(prev,.1)))+ggplot2::theme_bw()+
 ggplot2::labs(x="干预阈值概率",y="净获益",title=if(method=="binary") "留出样本决策曲线" else paste("IPCW 决策曲线，时间点",evaluation_time))
beta<-coef(fit);se<-sqrt(diag(vcov(fit)))
coef_table<-data.frame(term=names(beta),coefficient=as.numeric(beta),SE=as.numeric(se),
 lower=beta-1.959964*se,upper=beta+1.959964*se,scale=if(method=="binary") "log odds" else "log hazard")
formula_table<-data.frame(formula=paste(deparse(form),collapse=" "),method=method,
 outcome_column=outcome_v,event_column=if(length(event_v)) event_v else "",positive_class=if(method=="binary") positive else "1",
 predictors=paste(features,collapse=", "),fit_population="training only",seed=seed,
 validation_definition=if(length(split_v)) paste("provided column",split_v) else "seeded 70/30 stratified random holdout")
.diag_status[["过拟合检查"]]<-if((if(method=="binary") min(table(train$.y)) else sum(train$.event))<10*length(beta)) "warning" else "passed"
.diag_status[["校准检查"]]<-if(anyNA(cal_df$observed)) "warning" else "passed"
.diag_status[["内部验证"]]<-"passed"
.out[["formula_table"]]<-.mt_put("formula_table","table",formula_table,"训练样本模型公式与验证定义")
.out[["perf_table"]]<-.mt_put("perf_table","table",perf_table,"留出样本 Brier 与校准指标")
.out[["coef_table"]]<-.mt_put("coef_table","table",coef_table,"有名称的预后模型系数及尺度")
.out[["auc_table"]]<-.mt_put("auc_table","table",auc_table,"留出 AUC 或指定时间点 IPCW AUC")
.out[["cal_fig"]]<-.mt_put("cal_fig","figure",cal_fig,"留出校准图")
.out[["dca_fig"]]<-.mt_put("dca_fig","figure",dca_fig,"实际净获益决策曲线")
.out[["validation_performance"]]<-.mt_put("validation_performance","table",
 cbind(perf_table,AUC=auc_table$estimate,AUC_lower=auc_table$lower,AUC_upper=auc_table$upper),
 "独立验证的区分度、不确定性、Brier与校准")
results_text<-sprintf("预后结果：%s 模型仅使用%d个训练观测拟合，%d个独立验证观测；验证AUC=%.6g，95%%置信区间 [%.6g, %.6g]，Brier=%.6g。模型效应系数、尺度及置信区间见系数表。局限：该结果适用确认的预测因子、结局与验证定义，不能证明外部可迁移性；生存分支另依赖删失权重适用条件。下一步在新的外部样本检查区分度、校准和阈值净获益。",
 method,nrow(train),nrow(test),auc_table$estimate,auc_table$lower,auc_table$upper,perf_table$Brier)
.out[["results_text"]]<-.mt_put("results_text","text",results_text,"训练/验证结果与适用限制")
# The optional nomogram is produced only when the fitted rms model supports it.
if(method=="binary") {
 dd<-rms::datadist(train[,features,drop=FALSE])
 fit$Design$limits<-dd$limits;fit$Design$values<-dd$values
 nomogram_object<-rms::nomogram(fit,fun=plogis,funlabel="Predicted probability",lp=TRUE)
 rows<-lapply(names(nomogram_object),function(nm) {
  z<-nomogram_object[[nm]]
  if(!is.list(z) || is.null(z$points)) return(NULL)
  labels<-z[[1]]
  if(length(labels)!=length(z$points)) return(NULL)
  data.frame(axis=nm,points=as.numeric(z$points),label=as.character(labels))
 })
 nom_data<-do.call(rbind,Filter(Negate(is.null),rows))
 if(!is.null(nom_data) && nrow(nom_data)) {
  nom_fig<-ggplot2::ggplot(nom_data,ggplot2::aes(x=points,y=axis,label=label))+
   ggplot2::geom_line(ggplot2::aes(group=axis))+ggplot2::geom_point()+ggplot2::geom_text(nudge_y=.15,size=3)+
   ggplot2::theme_bw()+ggplot2::labs(x="rms nomogram points",y=NULL,title="训练模型预测因子积分轴")
  .out[["nom_fig"]]<-.mt_put("nom_fig","figure",nom_fig,"rms 列线图预测因子积分")
 }
}
'''

CUTPOINT = r'''
.step<-"cutpoint-settings"
method<-.mt_select(.mt_get("method_v","binary"),c("binary","survival"),"截点方法")
group_v<-.mt_get("group_v");outcome_v<-.mt_get("outcome_v")
followup_v<-.mt_get("followup_v");event_v<-.mt_get("event_v")
positive<-as.character(.mt_get("positive_v","1"));direction<-.mt_select(.mt_get("direction_v","higher_positive"),c("higher_positive","lower_positive"),"评分方向")
B<-.mt_integer(.mt_get("resamples_v",200),50,2000,"重采样次数")
seed<-.mt_integer(.mt_get("seed_v",20261008),0,.Machine$integer.max,"随机种子");set.seed(seed)
minprop<-.mt_get("minprop_v",.1)
if(!is.numeric(minprop) || length(minprop)!=1 || minprop<=0 || minprop>=.5) stop("minprop 须在 (0,.5)")
if(method=="binary" && (!length(outcome_v) || length(followup_v) || length(event_v) || length(.mt_get("time_unit_v")))) stop("二分类截点只使用真实结局；随访时间/事件/时间单位仅用于 survival")
if(method=="survival" && (!length(followup_v) || !length(event_v))) stop("生存截点需要随访时间和 0/1 事件")
df<-.mt_data(c(measure_v,outcome_v,group_v,if(method=="survival") c(followup_v,event_v)))
df$.marker<-.mt_numeric(df[[measure_v]],"连续标志物");df$.stratum<-if(length(group_v)) as.character(df[[group_v]]) else "overall"
if(method=="binary") {
 yy<-as.character(df[[outcome_v]])
 if(length(unique(yy))!=2 || !positive %in% yy) stop("真实二分类结局须有两个类别且阳性类别存在")
 df$.y<-as.integer(yy==positive)
} else {
 df$.time<-.mt_numeric(df[[followup_v]],"随访时间");df$.event<-.mt_numeric(df[[event_v]],"事件")
 if(any(df$.time<=0) || any(!df$.event %in% c(0,1))) stop("生存截点要求正数随访时间和 0/1 事件")
 if(length(outcome_v) && !identical(as.character(df[[outcome_v]]),as.character(df$.event))) stop("survival 已使用事件列；额外 outcome 必须为相同事件定义")
}
.binary_scan<-function(marker,y) {
 u<-sort(unique(marker));if(length(u)<4 || length(unique(y))!=2) stop("截点至少需要四个标志物值和两个结局类别")
 cuts<-(u[-1]+u[-length(u)])/2
 membership<-outer(marker,cuts,if(direction=="higher_positive") ">" else "<")
 size<-colSums(membership);keep<-size>=ceiling(length(y)*minprop) & size<=floor(length(y)*(1-minprop))
 cuts<-cuts[keep];membership<-membership[,keep,drop=FALSE]
 if(!length(cuts)) stop("minprop 范围内没有候选截点")
 sensitivity<-colSums(membership*y)/sum(y);specificity<-1-colSums(membership*(1-y))/sum(1-y)
 youden<-sensitivity+specificity-1;best<-which.max(youden)
 list(cuts=cuts,membership=membership,sensitivity=sensitivity,specificity=specificity,
  score=youden,best=best,threshold=cuts[best])
}
tables<-list();plots<-list();notes<-character(0);ph_p<-numeric(0);successful<-numeric(0)
.step<-"actual-cutpoint-search"
for(g in unique(df$.stratum)) {
 d<-df[df$.stratum==g,,drop=FALSE]
 if(nrow(d)<30) stop("各截点分析组至少三十人")
 if(method=="binary") {
  scan<-.binary_scan(d$.marker,d$.y)
  perm_max<-replicate(B,{
   y<-sample(d$.y);max(colSums(scan$membership*y)/sum(y)-colSums(scan$membership*(1-y))/sum(1-y))
  })
  adjusted_p<-(1+sum(perm_max>=max(scan$score)-1e-12))/(B+1)
  bootstrap<-replicate(B,tryCatch({i<-sample.int(nrow(d),nrow(d),replace=TRUE);.binary_scan(d$.marker[i],d$.y[i])$threshold},error=function(e) NA_real_))
  table<-data.frame(stratum=g,cutoff=scan$cuts,statistic=scan$score,sensitivity=scan$sensitivity,
   specificity=scan$specificity,selected=seq_along(scan$cuts)==scan$best,
   search_adjusted_p=adjusted_p,p_method="Monte Carlo permutation of maximum Youden",n=nrow(d))
  plots[[length(plots)+1]]<-data.frame(stratum=g,x=scan$cuts,y=scan$score,curve="Youden",cutoff=scan$threshold)
 } else {
  if(sum(d$.event)<10) stop("生存截点各组至少十个事件")
  fit<-maxstat::maxstat.test(survival::Surv(.time,.event)~.marker,data=d,smethod="LogRank",
   pmethod="Lau94",minprop=minprop,maxprop=1-minprop)
  threshold<-as.numeric(fit$estimate);adjusted_p<-as.numeric(fit$p.value)
  bootstrap<-replicate(B,tryCatch({i<-sample.int(nrow(d),nrow(d),replace=TRUE);
   as.numeric(maxstat::maxstat.test(survival::Surv(.time,.event)~.marker,data=d[i,,drop=FALSE],smethod="LogRank",pmethod="none",minprop=minprop,maxprop=1-minprop)$estimate)},error=function(e) NA_real_))
  d$.cut_group<-factor(d$.marker>threshold,levels=c(FALSE,TRUE),labels=c("low","high"))
  cox<-survival::coxph(survival::Surv(.time,.event)~.cut_group,data=d)
  ph<-tryCatch(survival::cox.zph(cox)$table["GLOBAL","p"],error=function(e) NA_real_);ph_p<-c(ph_p,ph)
  table<-data.frame(stratum=g,cutoff=threshold,statistic=as.numeric(fit$statistic),sensitivity=NA_real_,specificity=NA_real_,
   selected=TRUE,search_adjusted_p=adjusted_p,p_method="maxstat Lau94",n=nrow(d))
  km<-survival::survfit(survival::Surv(.time,.event)~.cut_group,data=d);s<-summary(km)
  plots[[length(plots)+1]]<-data.frame(stratum=g,x=s$time,y=s$surv,curve=as.character(s$strata),cutoff=threshold)
 }
 tables[[length(tables)+1]]<-table
 successful<-c(successful,sum(is.finite(bootstrap))/B)
 q<-quantile(bootstrap,c(.025,.5,.975),na.rm=TRUE)
 notes<-c(notes,sprintf("%s：%d/%d 次 bootstrap 可估计，截点 P2.5/中位/P97.5=%.6g/%.6g/%.6g；搜索调整 P=%.6g。",g,sum(is.finite(bootstrap)),B,q[1],q[2],q[3],adjusted_p))
}
cut_table<-do.call(rbind,tables);plot_data<-do.call(rbind,plots)
cut_fig<-ggplot2::ggplot(plot_data,ggplot2::aes(x=x,y=y,colour=curve))+
 (if(method=="binary") ggplot2::geom_line() else ggplot2::geom_step())+
 ggplot2::facet_wrap(~stratum,scales="free_x")+ggplot2::theme_bw()+
 ggplot2::labs(x=if(method=="binary") measure_v else .mt_get("time_unit_v","随访时间"),
  y=if(method=="binary") "Youden 指数" else "生存概率",title=if(method=="binary") "实际候选阈值与 Youden 搜索" else "maxstat 截点的 KM 曲线")
cut_text<-paste(notes,collapse="\n")
method_note<-if(method=="binary") "二分类按指定阳性类别与评分方向，在 minprop 支持范围最大化 Youden；标签置换的最大搜索统计量给出 Monte Carlo 搜索调整 P，bootstrap 描述截点稳定性，不把选点后普通 P 当作校正 P。" else
 "生存使用 maxstat::maxstat.test 的 LogRank + Lau94 搜索调整 P；bootstrap 截点及所选组别的 Cox PH 诊断。搜索后 HR/普通 log-rank 显著性不作为独立验证证据。"
results_paper<-paste("截点按实际选定方法执行：",method,"；",cut_text)
diag_text<-paste("随机种子",seed,"；minprop",minprop,"；组内独立搜索，多组搜索 P 未再跨组调整；输出明确为探索性阈值。",
 if(length(ph_p)) paste("Cox PH P：",paste(ph_p,collapse=",")) else "二分类结局无 PH 检验。")
.diag_status[["事件编码与候选阈值检查"]]<-"passed"
.diag_status[["重采样与截点稳定性检查"]]<-if(any(successful<.9)) "warning" else "passed"
.diag_status[["多重搜索和过拟合风险检查"]]<-"warning"
.diag_status[["比例风险检验"]]<-if(!length(ph_p)) "not_assessed" else if(anyNA(ph_p)) "not_assessed" else if(any(ph_p<.05)) "warning" else "passed"
.out[["cut_table"]]<-.mt_put("cut_table","table",cut_table,"实际候选/所选阈值及搜索调整 P")
.out[["cut_fig"]]<-.mt_put("cut_fig","figure",cut_fig,"实际方法的阈值/生存图")
.out[["cut_text"]]<-.mt_put("cut_text","text",cut_text,"重采样截点分布")
.out[["method_note"]]<-.mt_put("method_note","text",method_note,"真实搜索与 P 值校正方法")
.out[["results_paper"]]<-.mt_put("results_paper","text",results_paper,"截点结果")
.out[["diag_text"]]<-.mt_put("diag_text","text",diag_text,"搜索、分组和诊断范围")
'''

RCS = r'''
.step<-"rcs-data"
covars<-as.character(.mt_get("covars_v",character(0)));group_v<-.mt_get("group_v")
df<-.mt_data(c(outcome_v,exposure_v,covars,group_v))
df[[exposure_v]]<-.mt_numeric(df[[exposure_v]],"连续暴露")
if(nrow(df)<40 || length(unique(df[[exposure_v]]))<8) stop("样条至少四十人、八个不同暴露值")
for(v in c(covars,group_v)) if(is.character(df[[v]]) || v %in% group_v) df[[v]]<-factor(df[[v]])
outcome<-df[[outcome_v]];binary<-is.character(outcome) || is.factor(outcome) || (is.numeric(outcome) && length(unique(outcome))==2 && all(outcome %in% c(0,1)))
if(binary) {
 positive<-as.character(.mt_get("positive_v","1"))
 if(length(unique(as.character(outcome)))!=2 || !positive %in% as.character(outcome)) stop("二分类阳性类别需存在")
 df[[outcome_v]]<-as.integer(as.character(outcome)==positive)
} else .mt_numeric(outcome,"连续结局")
reference<-.mt_get("ref_v",median(df[[exposure_v]]))
if(!is.numeric(reference) || length(reference)!=1 || !is.finite(reference) || reference<min(df[[exposure_v]]) || reference>max(df[[exposure_v]])) stop("参考值须在观测暴露范围内")
probabilities<-list(c(.1,.5,.9),c(.05,.35,.65,.95),c(.05,.275,.5,.725,.95))
count<-.mt_get("knot_count_v")
if(length(count)) count<-.mt_integer(count,3,5,"结点数")
counts<-if(length(count)) count else 3:5
knot_sets<-lapply(counts,function(k) as.numeric(quantile(df[[exposure_v]],probabilities[[k-2]],names=FALSE)))
if(any(vapply(knot_sets,function(kn) anyDuplicated(kn)>0,logical(1)))) stop("分位数结点重复；需提高暴露支持或明确减少结点数")
fit_one<-function(kn) {
 term<-paste0("rms::rcs(`",exposure_v,"`, c(",paste(sprintf("%.17g",kn),collapse=","),"))")
 f<-stats::as.formula(paste0("`",outcome_v,"` ~ ",paste(c(term,sprintf("`%s`",c(covars,group_v))),collapse=" + ")))
 if(binary) rms::lrm(f,data=df,x=TRUE,y=TRUE) else rms::ols(f,data=df,x=TRUE,y=TRUE)
}
.step<-"rcs-fit"
fits<-lapply(knot_sets,fit_one)
if(any(vapply(fits,function(f) isTRUE(f$fail) || any(!is.finite(coef(f))),logical(1)))) stop("样条模型存在拟合失败或不可估计系数")
aics<-vapply(fits,stats::AIC,numeric(1));best<-which.min(aics);fit<-fits[[best]];knots<-knot_sets[[best]]
an<-stats::anova(fit);nl_row<-which(grepl("Nonlinear",rownames(an),fixed=TRUE))[1]
overall_row<-which(trimws(rownames(an))==exposure_v)[1]
nl_p<-if(is.na(nl_row)) NA_real_ else an[nl_row,"P"]
overall_p<-if(is.na(overall_row)) NA_real_ else an[overall_row,"P"]
.step<-"rcs-reference-contrasts"
grid<-seq(quantile(df[[exposure_v]],.05),quantile(df[[exposure_v]],.95),length.out=100)
base<-lapply(df[,c(covars,group_v),drop=FALSE],function(x) if(is.numeric(x)) median(x) else levels(factor(x))[1])
a<-base;b<-base;a[[exposure_v]]<-grid;b[[exposure_v]]<-reference
contrast<-rms::contrast(fit,a=a,b=b,conf.int=.95)
trans<-if(binary) exp else identity
curve<-data.frame(exposure=grid,estimate=trans(contrast$Contrast),lower=trans(contrast$Lower),upper=trans(contrast$Upper))
ref_a<-base;ref_a[[exposure_v]]<-reference
reference_contrast<-rms::contrast(fit,a=ref_a,b=ref_a)
primary<-data.frame(component=c("model",paste0("AIC_k",counts),"overall_p","nonlinear_p","reference"),
 value=c(if(binary) "logistic" else "Gaussian",as.character(aics),as.character(overall_p),as.character(nl_p),as.character(reference)),
 note=c(paste("selected",counts[best],"knots",paste(knots,collapse=",")),rep("same-sample model AIC",length(counts)),
  "rms exposure overall test","rms nonlinear exposure test",if(binary) "OR=1 at reference" else "mean difference=0 at reference"))
rcs_fig<-ggplot2::ggplot(curve,ggplot2::aes(x=exposure,y=estimate))+
 ggplot2::geom_ribbon(ggplot2::aes(ymin=lower,ymax=upper),alpha=.2)+ggplot2::geom_line()+
 ggplot2::geom_hline(yintercept=if(binary) 1 else 0,linetype=2)+ggplot2::geom_vline(xintercept=reference,linetype=2)+
 ggplot2::theme_bw()+ggplot2::labs(x=exposure_v,y=if(binary) "OR vs reference" else "Mean difference vs reference",title="限制性立方样条：实际结点与参考对比")
text<-sprintf("%d 人，%s rms 模型；%s，所选 %d 结点：%s。参考=%g；总体 P=%s，非线性 P=%s。图以 rms::contrast 和完整协方差求参考对比及区间；仅画观测 P5–P95，不把结点数当成实际指定结点。",
 nrow(df),if(binary) "Logistic" else "Gaussian",if(length(count)) "固定已指定结点数" else "比较 3/4/5 结点的同样本 AIC",counts[best],paste(format(knots),collapse=","),reference,format(overall_p),format(nl_p))
.diag_status[["结点选择检查"]]<-"passed"
.diag_status[["非线性检验"]]<-if(is.finite(nl_p)) "passed" else "not_assessed"
.diag_status[["极端区间样本支持检查"]]<-if(min(sum(df[[exposure_v]]<knots[1]),sum(df[[exposure_v]]>tail(knots,1)))<10) "warning" else "passed"
.out[["primary"]]<-.mt_put("primary","table",primary,"实际结点与总体/非线性检验")
.out[["rcs_fig"]]<-.mt_put("rcs_fig","figure",rcs_fig,"正确参考对比样条曲线")
.out[["text"]]<-.mt_put("text","text",text,"样条模型与参考值执行结果")
'''


def subgroup_core(plan: AnalysisPlan) -> str:
    from .statistics_behavior_cores import CORES as statistics_cores

    keys = ",".join(json.dumps(key, ensure_ascii=False) for key in plan.resolved_inputs)
    body = statistics_cores["subgroup_sensitivity_basic"]
    if callable(body):
        body = body(plan)
    body = body.replace("z <- baa_plan_input(id)", "z <- if(id %in% .cp_resolved_ids) baa_plan_input(id) else NULL")
    return COMMON + f"\n.cp_resolved_ids<-c({keys})\n" + r'''
if(is.numeric(dataset[[outcome_v]]) && length(unique(na.omit(dataset[[outcome_v]])))==2 &&
 all(na.omit(dataset[[outcome_v]]) %in% c(0,1))) dataset[[outcome_v]]<-factor(dataset[[outcome_v]])
''' + body + r'''
ct<-coef(summary(f1));interaction_rows<-grepl(":",rownames(ct),fixed=TRUE)
interaction_table<-data.frame(term=rownames(ct)[interaction_rows],estimate=ct[interaction_rows,1],
 SE=ct[interaction_rows,2],p=ct[interaction_rows,ncol(ct)],global_interaction_p=interaction_p,
 effect_scale=if(binary) "log odds interaction" else "mean difference interaction")
.out[["interaction_table"]]<-list(kind="table",payload=interaction_table,title="实际交互项系数及联合交互检验")
'''


CORES = {
    "prognostic_model": COMMON + PROGNOSTIC,
    "clinical_cutpoint_maxstat": COMMON + CUTPOINT,
    "clinical_subgroup_interaction": subgroup_core,
    "clinical_restricted_cubic_spline": COMMON + RCS,
}
INPUTS = {
    "prognostic_model": {"outcome": "outcome_v", "features": "feat_v", "event": "event_v", "validation_split": "split_v", "time_point": "time_v", "analysis_method": "method_v", "validation_level": "validation_v", "positive_class": "positive_v", "seed": "seed_v"},
    "clinical_cutpoint_maxstat": {"measure": "measure_v", "outcome": "outcome_v", "followup_time": "followup_v", "event": "event_v", "group": "group_v", "analysis_method": "method_v", "time_unit": "time_unit_v", "positive_class": "positive_v", "score_direction": "direction_v", "resamples": "resamples_v", "seed": "seed_v", "minprop": "minprop_v"},
    "clinical_subgroup_interaction": {"outcome": "outcome_v", "group": "exposure_v", "subgroup": "subgroup_v", "covariates": "covars_v", "reference_group": "reference_v", "positive_class": "positive_v"},
    "clinical_restricted_cubic_spline": {"outcome": "outcome_v", "exposure": "exposure_v", "covariates": "covars_v", "reference_value": "ref_v", "group": "group_v", "knot_count": "knot_count_v", "positive_class": "positive_v"},
}
ROLE_MAPS = {
    "prognostic_model": {"table_1": "formula_table", "figure_1": "nom_fig", "calibration_output": "cal_fig", "table_2": "perf_table", "table_3": "coef_table", "roc_output": "auc_table", "figure_3": "dca_fig", "survival_time_dependent_performance": "validation_performance", "scientific_result_interpretation": "results_text"},
    "clinical_cutpoint_maxstat": {"table_1": "cut_table", "figure_1": "cut_fig", "text_1": "cut_text", "method_note": "method_note", "manuscript_results": "results_paper", "diagnostic_output": "diag_text"},
    "clinical_subgroup_interaction": {"forest_plot": "forest_fig", "table_1": "interaction_table", "manuscript_results": "results_text", "method_note": "method_note", "manuscript_results_2": "paper_text", "diagnostic_output": "diag_text"},
    "clinical_restricted_cubic_spline": {"primary_table": "primary", "diagnostic_plot": "rcs_fig", "manuscript_results": "text"},
}
REFERENCE_CASES = {
    "prognostic_model": "固定训练/验证标记的 Logistic 与 Cox 系数/风险参考；翻转验证结局不影响模型；真实 DCA 净获益手算、指定时间 IPCW AUC/Brier 与删失权重。",
    "clinical_cutpoint_maxstat": "二分类候选 Youden 与阳性方向手算、最大统计量标签置换 P；生存 maxstat LogRank + Lau94 搜索调整 P 参考一致。",
    "clinical_subgroup_interaction": "连续与数值 0/1 二分类的实际交互项系数及联合 F/LR 检验；输出交互表而非仅亚组内效应。",
    "clinical_restricted_cubic_spline": "rms Gaussian/Logistic 的真实四分位结点、非线性检验及参考值零对比；实际 rms::contrast 全协方差区间。",
}
