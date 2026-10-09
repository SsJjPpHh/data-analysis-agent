"""Package-backed trial/evidence workflows on the existing frozen R path."""

from .meta_time_cores import COMMON

TRIAL_COMMON = r'''
.te_table<-function(slot,payload,title) .out[[slot]]<<-list(kind="table",payload=payload,title=title)
.te_text<-function(slot,payload,title) .out[[slot]]<<-list(kind="text",payload=payload,title=title)
.te_plot<-function(slot,payload,title) .out[[slot]]<<-list(kind="figure",payload=payload,title=title)
.te_json<-function(x,label) {
 if(is.character(x) && length(x)==1) x<-jsonlite::fromJSON(x)
 if(!is.list(x)) stop(label,"须为已确认 JSON 对象")
 x
}
.te_meta<-function(extra=character(0)) {
 d<-.mt_data(c(study_v,effect_v,se_v,extra));d$.study<-as.character(d[[study_v]])
 d$.yi<-.mt_numeric(d[[effect_v]],"效应量");d$.sei<-.mt_numeric(d[[se_v]],"标准误")
 if(nrow(d)<3 || anyDuplicated(d$.study) || any(d$.sei<=0)) stop("至少三个独立研究，研究 ID 唯一、标准误为正")
 d
}
.te_effect<-function(fit,term,scale="identity") {
 beta<-coef(fit);idx<-which(startsWith(names(beta),term))
 if(length(idx)!=1) stop("治疗效应不能唯一识别")
 se<-sqrt(diag(vcov(fit)))[idx]
 gaussian<-inherits(fit,"lm") && (!inherits(fit,"glm") || family(fit)$family=="gaussian")
 df<-if(gaussian) df.residual(fit) else Inf
 critical<-if(is.finite(df)) qt(.975,df) else qnorm(.975)
 trans<-if(scale=="log_ratio") exp else identity
 data.frame(estimate=unname(trans(beta[idx])),SE=unname(se),lower=unname(trans(beta[idx]-critical*se)),
  upper=unname(trans(beta[idx]+critical*se)),p=unname(if(is.finite(df)) 2*pt(-abs(beta[idx]/se),df) else 2*pnorm(-abs(beta[idx]/se))),scale=scale)
}
.te_forest<-function(d,label="label",value="estimate",lower="lower",upper="upper",title="效应与置信区间") {
 ggplot2::ggplot(d,ggplot2::aes(x=.data[[value]],y=.data[[label]]))+
  ggplot2::geom_errorbar(ggplot2::aes(xmin=.data[[lower]],xmax=.data[[upper]]),orientation="y",width=.2)+
  ggplot2::geom_point()+ggplot2::theme_bw()+ggplot2::labs(x=NULL,y=NULL,title=title)
}
.te_population<-function(d,pop_var,level=NULL) {
 levels<-unique(as.character(d[[pop_var]]))
 if(is.null(level)) level<-if("ITT" %in% levels) "ITT" else if(length(levels)==1) levels else stop("请明确 analysis_population_level")
 if(!level %in% levels) stop("选定分析集不存在")
 d[as.character(d[[pop_var]])==level,,drop=FALSE]
}
.te_outcome<-function(x) {
 if(is.numeric(x)) return(.mt_numeric(x,"结局"))
 levels<-unique(as.character(x));positive<-as.character(.mt_get("positive_v","1"))
 if(length(levels)!=2 || !positive %in% levels) stop("分类结局仅支持已明确阳性类别的二分类")
 as.integer(as.character(x)==positive)
}
'''

NETWORK = r'''
.step<-"network-arm-data"
df<-.mt_data(c(study_v,arm_v,event_v,treatment_v,n_v))
df$.study<-as.character(df[[study_v]]);df$.arm<-as.character(df[[arm_v]]);df$.treatment<-as.character(df[[treatment_v]])
df$.event<-.mt_numeric(df[[event_v]],"事件数");df$.n<-.mt_numeric(df[[n_v]],"样本量")
if(any(df$.event<0 | df$.event>df$.n | df$.event!=floor(df$.event) | df$.n!=floor(df$.n) | df$.n<=0)) stop("网络 Meta 需要合法事件数和整数臂样本量")
if(anyDuplicated(df[,c(".study",".arm")]) || anyDuplicated(df[,c(".study",".treatment")])) stop("研究内治疗臂 ID 与干预名称须唯一")
if(any(table(df$.study)<2)) stop("各研究至少两个治疗臂")
reference<-.mt_get("reference_v",sort(unique(df$.treatment))[1])
if(!reference %in% df$.treatment) stop("网络参考干预不存在")
direction<-.mt_select(.mt_get("direction_v","lower_better"),c("lower_better","higher_better"),"事件方向")
pw<-meta::pairwise(treat=.treatment,event=.event,n=.n,studlab=.study,data=df,sm="OR",incr=.5,method.incr="only0")
.step<-"network-meta"
fit<-netmeta::netmeta(TE,seTE,treat1,treat2,studlab,data=pw,sm="OR",reference.group=reference,
 common=TRUE,random=TRUE,method.tau="REML",small.values=if(direction=="lower_better") "good" else "bad")
league<-as.data.frame(as.table(fit$TE.random));names(league)<-c("treatment_row","treatment_column","log_OR")
se_league<-as.vector(fit$seTE.random)
league$OR<-exp(league$log_OR);league$lower<-exp(league$log_OR-1.959964*se_league);league$upper<-exp(league$log_OR+1.959964*se_league)
ranking<-netmeta::netrank(fit,small.values=if(direction=="lower_better") "good" else "bad")
rank_table<-data.frame(treatment=names(ranking$Pscore.random),P_score=as.numeric(ranking$Pscore.random),event_direction=direction)
if(!nrow(rank_table)) stop("网络排序未产生可估计 P-score")
rank_table<-rank_table[order(-rank_table$P_score),,drop=FALSE]
diagnostics<-data.frame(test=c("overall_Q","within_design_Q","between_design_Q"),
 statistic=c(fit$Q,fit$Q.het,fit$Q.inc),df=c(fit$df.Q,fit$df.Q.het,fit$df.Q.inc),p=c(fit$pval.Q,fit$pval.Q.het,fit$pval.Q.inc))
nodes<-data.frame(treatment=fit$trts);theta<-seq(0,2*pi,length.out=nrow(nodes)+1)[-1]
nodes$x<-cos(theta);nodes$y<-sin(theta)
edges<-aggregate(rep(1,nrow(pw)),by=list(t1=pw$treat1,t2=pw$treat2),FUN=sum);names(edges)[3]<-"comparisons"
edges$x<-nodes$x[match(edges$t1,nodes$treatment)];edges$y<-nodes$y[match(edges$t1,nodes$treatment)]
edges$xend<-nodes$x[match(edges$t2,nodes$treatment)];edges$yend<-nodes$y[match(edges$t2,nodes$treatment)]
network_fig<-ggplot2::ggplot(nodes,ggplot2::aes(x=x,y=y))+
 ggplot2::geom_segment(data=edges,ggplot2::aes(x=x,y=y,xend=xend,yend=yend,linewidth=comparisons),inherit.aes=FALSE,alpha=.5)+
 ggplot2::geom_point(size=5)+ggplot2::geom_text(ggplot2::aes(label=treatment),nudge_y=.1)+ggplot2::theme_void()+ggplot2::labs(title="实际研究比较网络")
rank_fig<-ggplot2::ggplot(rank_table,ggplot2::aes(x=stats::reorder(treatment,P_score),y=P_score))+
 ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw()+ggplot2::labs(x=NULL,y="P-score",title="随机效应网络排序（非最佳概率）")
.te_plot("network_fig",network_fig,"治疗网络");.te_table("league",league,"随机效应 OR 联赛表")
.te_plot("rank_fig",rank_fig,"P-score 排序");.te_table("diagnostics",diagnostics,"设计内异质性与设计间不一致性")
.te_table("ranking",rank_table,"实际随机效应 P-score")
.te_text("results_text",paste0("网络Meta结果：",length(unique(df$.study)),"项研究，",length(fit$trts),"种干预；参照干预=",reference,"，事件方向=",direction,
 "。随机效应 OR及95%置信区间见实际联赛表；排名首位干预=",rank_table$treatment[1],"，P-score=",format(rank_table$P_score[1]),
 "。设计内异质性Q=",format(fit$Q.het),"，设计间不一致性Q=",format(fit$Q.inc),
 "。局限：P-score不是成为最佳治疗的概率，排序不能证明临床优势；本轮不证明可传递性或比较一致性。下一步核对研究设计、治疗可比性与不一致性诊断。"),"真实网络合并结果及适用限制")
'''

DIAGNOSTIC_META = r'''
.step<-"diagnostic-study-counts"
threshold_v<-.mt_get("threshold_v")
df<-.mt_data(c(study_v,tp_v,fp_v,fn_v,tn_v,threshold_v))
for(v in c(tp_v,fp_v,fn_v,tn_v)) {
 .mt_numeric(df[[v]],v);if(any(df[[v]]<0 | df[[v]]!=floor(df[[v]]))) stop("诊断四格计数须为非负整数")
}
if(nrow(df)<5 || anyDuplicated(df[[study_v]])) stop("双变量诊断 Meta 至少五个独立研究")
d<-data.frame(TP=df[[tp_v]],FN=df[[fn_v]],FP=df[[fp_v]],TN=df[[tn_v]])
if(any(d$TP+d$FN==0 | d$FP+d$TN==0)) stop("各研究须有金标准阳性和阴性")
.step<-"reitsma-bivariate"
if(length(threshold_v)) {
 d$threshold<-df[[threshold_v]]
 if(is.character(d$threshold)) d$threshold<-factor(d$threshold)
 fit<-mada::reitsma(d,formula=cbind(tsens,tfpr)~threshold,correction=.5,correction.control="single")
} else fit<-mada::reitsma(d,correction=.5,correction.control="single")
vc<-vcov(fit);beta<-as.vector(coef(fit));names(beta)<-colnames(vc);se<-sqrt(diag(vc))
coef_table<-data.frame(term=names(beta),estimate=as.numeric(beta),SE=se,
 lower=beta-1.959964*se,upper=beta+1.959964*se,scale="logit sensitivity / logit FPR")
sens_idx<-grep("tsens",names(beta))[1];fpr_idx<-grep("tfpr",names(beta))[1]
if(is.na(sens_idx) || is.na(fpr_idx)) stop("双变量模型系数命名不可识别")
sensitivity<-data.frame(parameter="sensitivity_at_reference_threshold",estimate=plogis(beta[sens_idx]),
 lower=plogis(beta[sens_idx]-1.959964*se[sens_idx]),upper=plogis(beta[sens_idx]+1.959964*se[sens_idx]))
specificity<-data.frame(parameter="specificity_at_reference_threshold",estimate=1-plogis(beta[fpr_idx]),
 lower=1-plogis(beta[fpr_idx]+1.959964*se[fpr_idx]),upper=1-plogis(beta[fpr_idx]-1.959964*se[fpr_idx]))
sroc<-as.data.frame(mada::sroc(fit));names(sroc)[1:2]<-c("FPR","sensitivity")
roc_fig<-ggplot2::ggplot(sroc,ggplot2::aes(x=FPR,y=sensitivity))+ggplot2::geom_line()+
 ggplot2::coord_equal(xlim=c(0,1),ylim=c(0,1))+ggplot2::theme_bw()+ggplot2::labs(title="Reitsma 双变量 SROC")
study_table<-data.frame(study=as.character(df[[study_v]]),sensitivity=d$TP/(d$TP+d$FN),specificity=d$TN/(d$TN+d$FP),TP=d$TP,FN=d$FN,FP=d$FP,TN=d$TN,
 diagnostic_measure="observed 2x2 sensitivity and specificity",result="both gold-standard classes present; valid integer counts")
rows<-list()
for(i in seq_len(nrow(d))) for(metric in c("sensitivity","specificity")) {
 success<-if(metric=="sensitivity") d$TP[i] else d$TN[i];total<-if(metric=="sensitivity") d$TP[i]+d$FN[i] else d$TN[i]+d$FP[i]
 ci<-binom.test(success,total)$conf.int
 rows[[length(rows)+1]]<-data.frame(label=paste(study_table$study[i],metric),estimate=success/total,lower=ci[1],upper=ci[2])
}
forest_data<-do.call(rbind,rows);forest_fig<-.te_forest(forest_data,title="研究层精确二项区间")
heterogeneity<-data.frame(component=c("sensitivity variance","FPR variance","correlation"),value=c(fit$Psi[1,1],fit$Psi[2,2],cov2cor(fit$Psi)[1,2]))
text<-paste("Reitsma 双变量 logit 随机效应模型；零计数研究按 mada 单研究加 0.5。",
 if(length(threshold_v)) "已选阈值进入双变量 Meta 回归；合并值为参考阈值下的截距，系数表记录阈值影响。" else "无阈值协变量。",
 "四格原始计数、研究层精确区间与双变量协方差分别报告。")
.te_plot("roc_fig",roc_fig,"双变量 SROC");.te_table("sensitivity",sensitivity,"合并灵敏度")
.te_table("specificity",specificity,"合并特异度");.te_table("heterogeneity",heterogeneity,"双变量异质性协方差")
.te_table("accuracy",merge(study_table,df[,unique(c(study_v,threshold_v)),drop=FALSE],by.x="study",by.y=study_v),"四格原始计数与实际准确性")
.te_plot("forest_fig",forest_fig,"研究层灵敏度/特异度森林图");.te_text("text",text,"阈值与异质性方法")
'''

BAYESIAN_META = r'''
.step<-"confirmed-prior"
df<-.te_meta();prior<-.te_json(prior_v,"先验")
allowed<-c("mu_mean","mu_sd","tau_prior","tau_scale","sensitivity_tau_scales")
if(length(setdiff(names(prior),allowed)) || any(!c("mu_mean","mu_sd","tau_prior","tau_scale") %in% names(prior))) stop("先验需明确 mu_mean/mu_sd/tau_prior/tau_scale，不能忽略未知字段")
if(!is.numeric(prior$mu_mean) || !is.numeric(prior$mu_sd) || prior$mu_sd<=0 || !is.numeric(prior$tau_scale) || prior$tau_scale<=0) stop("先验均值须有限、SD/异质性尺度须为正")
tau_prior<-.mt_select(prior$tau_prior,c("half_normal","half_cauchy"),"异质性先验")
.fit_bayes<-function(scale) bayesmeta::bayesmeta(y=df$.yi,sigma=df$.sei,labels=df$.study,
 mu.prior.mean=prior$mu_mean,mu.prior.sd=prior$mu_sd,tau.prior=if(tau_prior=="half_normal")
 function(tau) ifelse(tau>=0,sqrt(2/pi)/scale*exp(-tau^2/(2*scale^2)),0) else
 function(tau) ifelse(tau>=0,2/(pi*scale*(1+(tau/scale)^2)),0))
.step<-"normal-normal-hierarchical-posterior"
fit<-.fit_bayes(prior$tau_scale)
posterior<-data.frame(parameter=c("mu","tau"),mean=fit$summary["mean",c("mu","tau")],median=fit$summary["median",c("mu","tau")],
 lower=fit$summary["95% lower",c("mu","tau")],upper=fit$summary["95% upper",c("mu","tau")])
prediction<-data.frame(parameter="new study true effect",lower=fit$qposterior(mu.p=.025,predict=TRUE),
 median=fit$qposterior(mu.p=.5,predict=TRUE),upper=fit$qposterior(mu.p=.975,predict=TRUE))
scales<-if(is.null(prior$sensitivity_tau_scales)) c(prior$tau_scale/2,prior$tau_scale,prior$tau_scale*2) else unlist(prior$sensitivity_tau_scales)
if(!is.numeric(scales) || any(!is.finite(scales)|scales<=0)) stop("先验敏感性尺度须为正数")
sensitivity<-do.call(rbind,lapply(scales,function(scale) {
 f<-.fit_bayes(scale);data.frame(tau_prior=tau_prior,tau_scale=scale,mu_mean=f$summary["mean","mu"],
  lower=f$summary["95% lower","mu"],upper=f$summary["95% upper","mu"])
}))
forest_data<-rbind(data.frame(label=df$.study,estimate=df$.yi,lower=df$.yi-1.959964*df$.sei,upper=df$.yi+1.959964*df$.sei),
 data.frame(label="posterior pooled mu",estimate=posterior$mean[1],lower=posterior$lower[1],upper=posterior$upper[1]))
forest_fig<-.te_forest(forest_data,title="研究似然区间与合并后验可信区间")
text<-sprintf("正常–正常层级模型，由 bayesmeta 积分计算；mu~N(%g,%g²)，tau 为 %s(scale=%g)。效应保持输入尺度。预测区间针对新研究真实效应；敏感性仅改变明确列出的异质性先验尺度。",prior$mu_mean,prior$mu_sd,tau_prior,prior$tau_scale)
.te_plot("forest_fig",forest_fig,"合并后验森林图");.te_table("prediction",prediction,"新研究效应后验预测区间")
.te_table("sensitivity",sensitivity,"明确先验尺度敏感性");.te_table("posterior",posterior,"mu 与 tau 后验摘要");.te_text("text",text,"实际先验与贝叶斯结果")
'''

META_BIAS = r'''
.step<-"meta-bias-data"
df<-.te_meta(rob_v);df$.risk<-factor(df[[rob_v]])
fit<-metafor::rma(yi=df$.yi,sei=df$.sei,method="REML")
egger<-if(nrow(df)>=10) metafor::regtest(fit,model="rma",predictor="sei") else NULL
bias<-data.frame(test="Egger SE regression",statistic=if(is.null(egger)) NA_real_ else egger$zval,
 p=if(is.null(egger)) NA_real_ else egger$pval,status=if(is.null(egger)) "fewer than ten studies" else "computed")
if(nlevels(df$.risk)<2) stop("偏倚风险需至少两个已确认类别，才能作风险亚组和回归")
reg<-metafor::rma(yi=.yi,sei=.sei,mods=~.risk,data=df,method="REML",test="knha")
regression<-data.frame(term=rownames(reg$b),estimate=as.numeric(reg$b),SE=reg$se,lower=reg$ci.lb,upper=reg$ci.ub,p=reg$pval)
subgroups<-do.call(rbind,lapply(levels(df$.risk),function(g) {
 d<-df[df$.risk==g,,drop=FALSE];if(nrow(d)<2) stop("风险各亚组至少两个研究")
 f<-metafor::rma(yi=d$.yi,sei=d$.sei,method="REML");data.frame(risk=g,k=f$k,estimate=as.numeric(f$b),lower=f$ci.lb,upper=f$ci.ub,I2=f$I2)
}))
loo<-metafor::leave1out(fit);sensitivity<-data.frame(study_omitted=df$.study,estimate=loo$estimate,lower=loo$ci.lb,upper=loo$ci.ub,tau2=loo$tau2)
inf<-influence(fit)$inf;influence_data<-data.frame(study=df$.study,cook=inf$cook.d,hat=inf$hat,rstudent=inf$rstudent)
influence_fig<-ggplot2::ggplot(influence_data,ggplot2::aes(x=study,y=cook))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw()+ggplot2::labs(title="研究 Cook 距离")
funnel_data<-data.frame(effect=df$.yi,SE=df$.sei)
funnel_fig<-ggplot2::ggplot(funnel_data,ggplot2::aes(x=effect,y=SE))+ggplot2::geom_point()+ggplot2::scale_y_reverse()+
 ggplot2::geom_vline(xintercept=as.numeric(fit$b),linetype=2)+ggplot2::theme_bw()+ggplot2::labs(title="输入效应尺度漏斗图")
text<-paste("REML 主模型；已确认偏倚风险作为研究层分类协变量和亚组。逐项剔除重拟合相同主模型，影响图为真实 Cook 距离；",
 if(is.null(egger)) "不足十个研究不执行 Egger 检验。" else sprintf("Egger P=%g。",egger$pval),"漏斗不对称不自动等同发表偏倚。")
.te_table("bias",bias,"小样本效应检验状态");.te_plot("influence_fig",influence_fig,"实际影响诊断")
.te_table("subgroups",subgroups,"指定偏倚风险亚组合并");.te_table("regression",regression,"偏倚风险 Meta 回归")
.te_table("sensitivity",sensitivity,"逐研究剔除");.te_plot("funnel_fig",funnel_fig,"漏斗图");.te_text("text",text,"偏倚与敏感性实际结果")
'''

SAFETY_META = r'''
.step<-"safety-two-arm-counts"
sub_v<-.mt_get("sub_v");df<-.mt_data(c(study_v,et_v,nt_v,ec_v,nc_v,sub_v))
for(v in c(et_v,nt_v,ec_v,nc_v)) {
 .mt_numeric(df[[v]],v);if(any(df[[v]]<0 | df[[v]]!=floor(df[[v]]))) stop("安全性事件与样本量须为非负整数")
}
if(nrow(df)<3 || anyDuplicated(df[[study_v]]) || any(df[[nt_v]]<=0 | df[[nc_v]]<=0 | df[[et_v]]>df[[nt_v]] | df[[ec_v]]>df[[nc_v]])) stop("安全性 Meta 需要至少三项独立合法两臂研究")
args<-list(event.e=df[[et_v]],n.e=df[[nt_v]],event.c=df[[ec_v]],n.c=df[[nc_v]],studlab=as.character(df[[study_v]]),
 sm="RR",method="MH",common=TRUE,random=TRUE,method.tau="DL",incr=.5,method.incr="only0")
if(length(sub_v)) args$subgroup<-df[[sub_v]]
fit<-do.call(meta::metabin,args)
rd_args<-args;rd_args$sm<-"RD";rd_args$method<-"Inverse"
rd<-do.call(meta::metabin,rd_args)
sensitivity<-data.frame(model=c("MH fixed RR","DL random RR","fixed RD","DL random RD"),
 estimate=c(exp(fit$TE.common),exp(fit$TE.random),rd$TE.common,rd$TE.random),
 lower=c(exp(fit$lower.common),exp(fit$lower.random),rd$lower.common,rd$lower.random),
 upper=c(exp(fit$upper.common),exp(fit$upper.random),rd$upper.common,rd$upper.random),
 double_zero_studies=sum(df[[et_v]]==0 & df[[ec_v]]==0))
forest_data<-rbind(data.frame(label=as.character(df[[study_v]]),estimate=exp(fit$TE),lower=exp(fit$lower),upper=exp(fit$upper)),
 data.frame(label="random RR",estimate=exp(fit$TE.random),lower=exp(fit$lower.random),upper=exp(fit$upper.random)))
forest_fig<-.te_forest(forest_data,title="安全性 RR：meta 真实零事件处理")
method<-"meta::metabin，MH RR 和逆方差 RD，DL 随机效应；仅边界研究加 0.5，双零研究对 RR 不提供相对信息，RD 敏感性另行纳入。"
text<-sprintf("%d 项研究，治疗事件=%d/%d，对照=%d/%d；随机 RR=%.6f [%.6f,%.6f]。双零研究 %d 项；I²=%.2f%%。%s",
 nrow(df),sum(df[[et_v]]),sum(df[[nt_v]]),sum(df[[ec_v]]),sum(df[[nc_v]]),exp(fit$TE.random),exp(fit$lower.random),exp(fit$upper.random),sum(df[[et_v]]==0 & df[[ec_v]]==0),fit$I2*100,
 if(length(sub_v)) paste("已选亚组",sub_v,"进入 meta 的亚组计算。") else "")
.te_plot("forest_fig",forest_fig,"安全性森林图");.te_table("sensitivity",sensitivity,"RR/RD 与固定/随机敏感性")
.te_text("bias",paste("稀有事件与双零处理：",method),"稀有事件解释");.te_text("method",method,"安全性统计方法")
.te_text("text",text,"安全性 Meta 实际结果");.te_text("diagnostics",paste("Q P=",fit$pval.Q,"；tau²=",fit$tau2,"；零单元格不强制产生可估计 RR。"),"零事件与异质性")
'''

LIVING = r'''
.step<-"versioned-evidence"
time_v<-.mt_get("time_v");version_v<-.mt_get("version_v");sub_v<-.mt_get("sub_v")
df<-.mt_data(c(study_v,effect_v,se_v,time_v,version_v,sub_v))
df$.study<-as.character(df[[study_v]]);df$.yi<-.mt_numeric(df[[effect_v]],"效应量");df$.sei<-.mt_numeric(df[[se_v]],"标准误")
if(any(df$.sei<=0)) stop("标准误须为正")
if(length(time_v)) {
 x<-df[[time_v]];if(is.character(x)) x<-as.Date(x)
 if(anyNA(x)) stop("更新时间不可解析")
 df$.version<-as.numeric(x)
} else if(length(version_v)) df$.version<-.mt_numeric(df[[version_v]],"研究版本")
else {if(anyDuplicated(df$.study)) stop("重复研究必须提供时间或版本");df$.version<-seq_len(nrow(df))}
if(length(version_v) && length(time_v)) {
 df$.declared_version<-df[[version_v]]
 if(anyDuplicated(df[,c(".study",".declared_version")])) stop("同一研究版本重复")
}
if(anyDuplicated(df[,c(".study",".version")])) stop("同一研究在同一更新时间出现重复记录")
versions<-sort(unique(df$.version));pieces<-list();forest_rows<-list()
for(v in versions) {
 d<-df[df$.version<=v,,drop=FALSE];d<-d[order(d$.version,decreasing=TRUE),,drop=FALSE]
 d<-d[!duplicated(d$.study),,drop=FALSE]
 if(nrow(d)<3) next
 f<-metafor::rma(yi=d$.yi,sei=d$.sei,method="REML")
 pieces[[length(pieces)+1]]<-data.frame(update=v,k=f$k,estimate=as.numeric(f$b),lower=f$ci.lb,upper=f$ci.ub,
 tau2=f$tau2,I2=f$I2,current_study_versions=paste(paste(d$.study,d$.version,sep="@"),collapse=";"))
 if(length(sub_v)) {
  for(g in unique(as.character(d[[sub_v]]))) {
   z<-d[as.character(d[[sub_v]])==g,,drop=FALSE];if(nrow(z)<2) next
   sf<-metafor::rma(yi=z$.yi,sei=z$.sei,method="REML")
   forest_rows[[length(forest_rows)+1]]<-data.frame(label=paste(v,"subgroup",g),estimate=as.numeric(sf$b),lower=sf$ci.lb,upper=sf$ci.ub)
  }
 }
}
if(!length(pieces)) stop("每次更新至少需要三个不同研究")
updates<-do.call(rbind,pieces)
forest_data<-data.frame(label=paste0("update:",updates$update," (k=",updates$k,")"),estimate=updates$estimate,lower=updates$lower,upper=updates$upper)
if(length(forest_rows)) forest_data<-rbind(forest_data,do.call(rbind,forest_rows))
forest_fig<-.te_forest(forest_data,title="真实累计更新：每研究仅纳入当时最新版本")
method<-"按已提供更新时间优先、否则数字研究版本、否则明确输入行顺序更新；同一研究在每次更新只取最新版本，绝不重复计权。REML 模型保持输入效应尺度。"
text<-sprintf("计算 %d 个可估计更新，最初 k=%d、效应 %.6f；最终 k=%d、效应 %.6f。更新时间来源=%s。",
 nrow(updates),updates$k[1],updates$estimate[1],tail(updates$k,1),tail(updates$estimate,1),if(length(time_v)) time_v else if(length(version_v)) version_v else "input row order")
.te_table("updates",updates,"实际版本合并对照");.te_plot("forest_fig",forest_fig,"新旧合并森林图")
.te_text("text",text,"更新变化");.te_text("method",method,"版本与合并规则")
.te_text("caption","图为各实际更新的合并效应和区间；同一研究旧版本在后续更新被替换。","图题注")
'''

GRADE = r'''
.step<-"confirmed-certainty"
risk_v<-.mt_get("risk_v");study_v<-.mt_get("study_v")
df<-.mt_data(c(outcome_v,effect_v,se_v,certainty_v,risk_v,study_v))
df$.yi<-.mt_numeric(df[[effect_v]],"效应量");df$.sei<-.mt_numeric(df[[se_v]],"标准误")
if(any(df$.sei<=0)) stop("标准误须为正")
certainty_map<-c(high="high",moderate="moderate",low="low",very_low="very_low",高="high",中="moderate",低="low",极低="very_low")
certainty<-unname(certainty_map[as.character(df[[certainty_v]])])
if(anyNA(certainty)) stop("证据等级须为用户已确认 high/moderate/low/very_low（或 高/中/低/极低）")
scale<-.mt_select(.mt_get("scale_v","identity"),c("identity","log_rr","log_or"),"证据效应尺度")
if(length(risk_v) && any(.mt_numeric(df[[risk_v]],"对照绝对风险")<0 | df[[risk_v]]>1)) stop("对照事件风险须在 [0,1]")
pieces<-list()
for(endpoint in unique(as.character(df[[outcome_v]]))) {
 z<-df[as.character(df[[outcome_v]])==endpoint,,drop=FALSE];c<-certainty[as.character(df[[outcome_v]])==endpoint]
 if(length(unique(c))!=1) stop("同一结局必须提供一致的已确认总体证据等级，不自动降级")
 if(length(study_v) && anyDuplicated(z[[study_v]])) stop("同一结局研究 ID 重复")
 if(nrow(z)>1) {
  f<-metafor::rma(yi=z$.yi,sei=z$.sei,method="REML");est<-as.numeric(f$b);lo<-f$ci.lb;hi<-f$ci.ub
 } else {est<-z$.yi;lo<-est-1.959964*z$.sei;hi<-est+1.959964*z$.sei}
 trans<-if(scale=="identity") identity else exp
 baseline<-if(length(risk_v)) unique(z[[risk_v]]) else NA_real_
 if(length(baseline)!=1) stop("同一结局对照风险须明确一致，不自动选择")
 exposed<-if(is.na(baseline) || scale=="identity") NA_real_ else if(scale=="log_rr") baseline*exp(est) else baseline*exp(est)/(1-baseline+baseline*exp(est))
 if(!is.na(exposed) && exposed>1) stop("提供的 RR 与基线风险组合产生大于一的风险，请核对参数")
 pieces[[length(pieces)+1]]<-data.frame(outcome=endpoint,studies=nrow(z),estimate=trans(est),lower=trans(lo),upper=trans(hi),
  scale=scale,certainty=c[1],certainty_source="user supplied; no automatic downgrade",baseline_risk=baseline,
  estimated_risk=exposed,risk_difference_per_1000=if(is.na(exposed)) NA_real_ else (exposed-baseline)*1000)
}
sof<-do.call(rbind,pieces)
method<-"按结局整理已确认 GRADE 等级；多个独立效应使用 REML，单个效应直接保留与 SE 区间。不给出自动风险偏倚/间接性降级判断；绝对转换仅用于明确 log_rr/log_or 和一致对照风险。"
text<-paste("Summary of Findings：",nrow(sof),"个结局；证据等级逐项保留用户输入。",paste(sof$outcome,sof$certainty,collapse="; "))
.te_table("sof",sof,"已确认等级与效应的 SoF")
.te_text("certainty",paste(sof$outcome,sof$certainty,collapse="\n"),"用户已确认等级")
.te_text("text",text,"实际证据摘要");.te_text("method",method,"SoF 计算及人工输入来源")
.te_text("caption","SoF 表含结局、效应/区间、研究数、用户已确认等级；不自动推断等级。","表题注")
'''

IPD = r'''
.step<-"ipd-design"
covars<-as.character(.mt_get("covars_v",character(0)));sub_v<-.mt_get("sub_v")
df<-.mt_data(c(study_v,subject_v,group_v,outcome_v,covars,sub_v))
if(anyDuplicated(df[,c(study_v,subject_v)])) stop("研究内每受试者只能一行；重复测量需另设模型")
df$.study<-factor(df[[study_v]]);df$.trt<-factor(df[[group_v]])
if(nlevels(df$.study)<5 || nlevels(df$.trt)!=2) stop("一阶段 IPD 至少五个研究、两个治疗组")
reference<-.mt_get("reference_v",levels(df$.trt)[1]);df$.trt<-relevel(df$.trt,reference)
df$.y<-.te_outcome(df[[outcome_v]]);binary<-all(df$.y %in% c(0,1))
for(v in c(covars,sub_v)) if(is.character(df[[v]]) || v %in% sub_v) df[[v]]<-factor(df[[v]])
if(any(vapply(split(df$.trt,df$.study),function(x) length(unique(x))!=2,logical(1)))) stop("各研究须有两个治疗组")
rhs<-c(".trt",sprintf("`%s`",covars),"(1 | .study)")
form<-as.formula(paste(".y ~",paste(rhs,collapse=" + ")))
fit<-if(binary) lme4::glmer(form,data=df,family=binomial()) else lme4::lmer(form,data=df,REML=TRUE)
if(length(fit@optinfo$conv$lme4$messages)) stop("IPD 混合模型未收敛")
ct<-coef(summary(fit));critical<-qnorm(.975);trans<-if(binary) exp else identity
model_table<-data.frame(term=rownames(ct),estimate=trans(ct[,1]),lower=trans(ct[,1]-critical*ct[,2]),
 upper=trans(ct[,1]+critical*ct[,2]),SE=ct[,2],effect_scale=if(binary) "conditional OR" else "mean difference")
heterogeneity<-as.data.frame(lme4::VarCorr(fit));heterogeneity$interpretation<-"研究随机截距变异；不等同治疗效应异质性"
subgroups<-list()
if(length(sub_v)) {
 for(g in levels(df[[sub_v]])) {
  z<-df[df[[sub_v]]==g,,drop=FALSE]
  f<-if(binary) lme4::glmer(form,data=z,family=binomial()) else lme4::lmer(form,data=z,REML=TRUE)
  c<-coef(summary(f));subgroups[[length(subgroups)+1]]<-data.frame(subgroup=g,estimate=trans(c[2,1]),lower=trans(c[2,1]-critical*c[2,2]),upper=trans(c[2,1]+critical*c[2,2]))
 }
}
method<-"一阶段研究随机截距 lmer/glmer；每研究受试者唯一，治疗、协变量固定效应。二分类为条件 OR，连续为均值差；Wald 区间明确使用大样本近似。"
text<-sprintf("%d 项研究、%d 名个体，参考组 %s，%s 结局；随机截距方差=%.6f。",nlevels(df$.study),nrow(df),reference,if(binary) "二分类" else "连续",heterogeneity$vcov[1])
.te_table("model",model_table,"一阶段 IPD 模型");.te_table("heterogeneity",heterogeneity,"实际研究随机截距变异")
if(length(sub_v)) .te_table("subgroups",do.call(rbind,subgroups),"指定亚组独立一阶段模型")
.te_text("method",method,"IPD 统计方法");.te_text("text",text,"IPD 结果")
.te_text("diagnostics",paste("奇异拟合=",lme4::isSingular(fit),"；亚组之间差异不自动作交互推断。"),"IPD 收敛与亚组边界")
'''

DOSE_RESPONSE = r'''
.step<-"dose-response-data"
tox_v<-.mt_get("tox_v");covars<-as.character(.mt_get("covars_v",character(0)))
df<-.mt_data(c(dose_v,outcome_v,tox_v,covars));df$.dose<-.mt_numeric(df[[dose_v]],"剂量");df$.y<-.te_outcome(df[[outcome_v]])
if(length(unique(df$.dose))<5 || nrow(df)<30) stop("剂量反应至少五个不同剂量、三十人")
binary<-all(df$.y %in% c(0,1));fam<-if(binary) binomial() else gaussian()
for(v in covars) if(is.character(df[[v]])) df[[v]]<-factor(df[[v]])
terms<-sprintf("`%s`",covars);f1<-as.formula(paste(".y ~",paste(c(".dose",terms),collapse=" + ")))
f2<-as.formula(paste(".y ~",paste(c(".dose + I(.dose^2)",terms),collapse=" + ")))
fg<-as.formula(paste(".y ~",paste(c(paste0("s(.dose,k=",min(6,length(unique(df$.dose))-1),")"),terms),collapse=" + ")))
s<-mgcv::s
fits<-list(linear=glm(f1,data=df,family=fam),quadratic=glm(f2,data=df,family=fam),GAM=mgcv::gam(fg,data=df,family=fam,method="REML"))
comparison<-data.frame(model=names(fits),AIC=vapply(fits,AIC,numeric(1)))
fit<-fits[[which.min(comparison$AIC)]]
grid<-seq(min(df$.dose),max(df$.dose),length.out=100);newdata<-df[rep(1,100),,drop=FALSE];newdata$.dose<-grid
for(v in covars) newdata[[v]]<-if(is.numeric(df[[v]])) median(df[[v]]) else levels(factor(df[[v]]))[1]
pr<-predict(fit,newdata=newdata,type="link",se.fit=TRUE);inv<-fam$linkinv
curve<-data.frame(dose=grid,prediction=inv(pr$fit),lower=inv(pr$fit-1.959964*pr$se.fit),upper=inv(pr$fit+1.959964*pr$se.fit))
direction<-.mt_select(.mt_get("direction_v","higher_better"),c("higher_better","lower_better"),"疗效方向")
best<-if(direction=="higher_better") which.max(curve$prediction) else which.min(curve$prediction)
recommendation<-data.frame(dose=grid[best],predicted_response=curve$prediction[best],lower=curve$lower[best],upper=curve$upper[best],criterion=paste("observed-range maximum fitted efficacy:",direction),selected_model=comparison$model[which.min(comparison$AIC)])
safety<-if(length(tox_v)) {
 .mt_numeric(df[[tox_v]],"安全性指标")
 aggregate(df[[tox_v]],by=list(dose=df$.dose),FUN=function(x) mean(x))
} else data.frame(dose=NA_real_,safety_indicator=NA_real_,status="no safety endpoint supplied")
fig<-ggplot2::ggplot(curve,ggplot2::aes(x=dose,y=prediction))+ggplot2::geom_ribbon(ggplot2::aes(ymin=lower,ymax=upper),alpha=.2)+
 ggplot2::geom_line()+ggplot2::geom_point(data=df,ggplot2::aes(x=.dose,y=.y),inherit.aes=FALSE,alpha=.25)+ggplot2::theme_bw()
text<-paste("比较线性、二次与 mgcv GAM 的同样本 AIC；选择模型",recommendation$selected_model,"。",
 "推荐表仅表示已观测剂量范围内的拟合疗效最优点，未凭空加入毒性阈值或外推剂量；安全性按已选列与剂量分别报告。")
.te_plot("curve",fig,"实际剂量反应与区间");.te_table("comparison",comparison,"不同剂量反应模型 AIC")
.te_table("recommendation",recommendation,"模型支持范围内的拟合疗效最优剂量");.te_text("text",text,"推荐标准及边界")
if(length(tox_v)) .te_table("safety",safety,"选入安全性指标的剂量汇总")
'''

RCT = r'''
.step<-"rct-population"
covars<-as.character(.mt_get("covars_v",character(0)));strata<-as.character(.mt_get("strata_v",character(0)));sub_v<-.mt_get("sub_v")
all_data<-.mt_data(c(group_v,outcome_v,pop_v,covars,strata,sub_v))
df<-.te_population(all_data,pop_v,.mt_get("population_v"))
if(nrow(df)<20) stop("选定分析集至少二十人")
df$.trt<-factor(df[[group_v]]);if(nlevels(df$.trt)!=2) stop("主终点需要两个随机组")
reference<-.mt_get("reference_v",levels(df$.trt)[1]);df$.trt<-relevel(df$.trt,reference)
df$.y<-.te_outcome(df[[outcome_v]]);binary<-all(df$.y %in% c(0,1));fam<-if(binary) binomial() else gaussian()
for(v in c(covars,strata)) if(is.character(df[[v]]) || v %in% strata) df[[v]]<-factor(df[[v]])
form<-.mt_formula(".y",c(".trt",covars,strata));fit<-glm(form,data=df,family=fam)
if(!fit$converged || any(!is.finite(coef(fit))) || fit$rank<length(coef(fit))) stop("预定调整模型不可估计")
primary<-.te_effect(fit,".trt",if(binary) "log_ratio" else "identity");primary$analysis_population<-unique(as.character(df[[pop_v]]))[1]
primary$reference<-reference;primary$n<-nrow(df)
unadjusted<-glm(.y~.trt,data=df,family=fam)
sensitivity<-rbind(data.frame(model="prespecified adjusted",primary[,c("estimate","SE","lower","upper","p","scale")]),
 data.frame(model="unadjusted same population",.te_effect(unadjusted,".trt",if(binary) "log_ratio" else "identity")))
ct<-coef(summary(fit));model_table<-data.frame(term=rownames(ct),coefficient=ct[,1],SE=ct[,2],p=ct[,ncol(ct)])
forest_data<-data.frame(label="overall",estimate=primary$estimate,lower=primary$lower,upper=primary$upper)
if(length(sub_v)) for(g in unique(as.character(df[[sub_v]]))) {
 d<-df[as.character(df[[sub_v]])==g,,drop=FALSE];f<-glm(form,data=d,family=fam)
 if(any(!is.finite(coef(f)))) stop("指定亚组模型不可估计")
 e<-.te_effect(f,".trt",if(binary) "log_ratio" else "identity")
 forest_data<-rbind(forest_data,data.frame(label=g,estimate=e$estimate,lower=e$lower,upper=e$upper))
}
forest_fig<-.te_forest(forest_data,title="预定主终点及指定亚组效应")
population<-paste("实际分析集",primary$analysis_population,"，n=",nrow(df),"；排除其他分析集",nrow(all_data)-nrow(df),"人。")
text<-paste(population,"结局模型",if(binary) "Logistic OR" else "Gaussian mean difference","；参考随机组",reference,"；未调整与调整效应在相同分析样本上比较，亚组内效应不替代联合交互检验。")
.te_table("primary",primary,"已选分析集主终点");.te_plot("forest",forest_fig,"实际主终点/亚组效应")
.te_table("sensitivity",sensitivity,"同样本调整敏感性");.te_table("model",model_table,"预先指定协变量模型")
.te_text("population",population,"分析集人数与排除");.te_text("text",text,"CONSORT 参数与效应结果")
'''

SAP = r'''
.step<-"confirmed-sap-specification"
spec<-.te_json(spec_v,"SAP 方案")
needed<-c("estimand","primary_method","alpha","missing_data_strategy")
if(any(!needed %in% names(spec))) stop("SAP 需明确 estimand/primary_method/alpha/missing_data_strategy")
if(!is.numeric(spec$alpha) || length(spec$alpha)!=1 || spec$alpha<=0 || spec$alpha>=1) stop("SAP alpha 无效")
covars<-as.character(.mt_get("covars_v",character(0)));pop_v<-.mt_get("pop_v");missing_v<-.mt_get("missing_v")
cols<-unique(c(group_v,outcome_v,covars,pop_v,missing_v))
if(length(setdiff(cols,names(dataset)))) stop("SAP 数据角色不在数据中")
d<-dataset[,cols,drop=FALSE]
outline<-data.frame(section=c("estimand","primary endpoint","randomized treatment","primary method","alpha","missing data strategy","covariates","analysis population","observed rows"),
 confirmed_value=c(as.character(spec$estimand),outcome_v,group_v,as.character(spec$primary_method),as.character(spec$alpha),as.character(spec$missing_data_strategy),paste(covars,collapse=","),if(length(pop_v)) pop_v else "not supplied",as.character(nrow(d))),source="confirmed frozen inputs")
for(name in setdiff(names(spec),needed)) outline<-rbind(outline,data.frame(section=name,
 confirmed_value=as.character(jsonlite::toJSON(spec[[name]],auto_unbox=TRUE)),source="additional confirmed SAP setting"))
shell<-data.frame(treatment=names(table(d[[group_v]],useNA="ifany")),available_rows=as.numeric(table(d[[group_v]],useNA="ifany")),
 result_field="effect, SE, confidence interval, P",result_status="analysis table shell; no hypothesis result computed")
if(length(missing_v)) {
 if(!all(na.omit(d[[missing_v]]) %in% c(0,1))) stop("缺失标记须为 0/1")
 outline<-rbind(outline,data.frame(section="declared missing count",confirmed_value=as.character(sum(d[[missing_v]]==1,na.rm=TRUE)),source="selected missing indicator"))
}
method<-paste("已确认 SAP：",spec$primary_method,"；估计目标：",spec$estimand,"；alpha=",spec$alpha,"；缺失策略：",spec$missing_data_strategy,"。这里只建立方案大纲和分析表壳，未自动改写策略或生成研究结果。")
.te_table("outline",outline,"已确认 SAP 章节与数据角色");.te_table("shell",shell,"实际组别人数与分析表壳")
.te_text("method",method,"已确认统计方法");.te_text("text",paste("SAP 已结构化为",nrow(outline),"项；观察到",nrow(d),"行，统计假设结果尚未执行。"),"方案构建状态")
'''

RANDOMIZATION = r'''
.step<-"randomization-plan"
N<-.mt_integer(n_v,2,100000,"随机化样本量")
ratio<-.mt_get("ratio_v",1)
if(!is.numeric(ratio) || length(ratio)!=1 || !is.finite(ratio) || ratio<=0) stop("治疗/对照分配比须为正数")
den<-which(abs(ratio*(1:100)-round(ratio*(1:100)))<1e-8)[1]
if(is.na(den)) stop("分配比需可表达为分母不超过一百的整数比例")
num<-as.integer(round(ratio*den));unit<-num+den
blocks<-as.numeric(.mt_get("block_v",2*unit))
if(any(blocks!=floor(blocks) | blocks<unit | blocks%%unit!=0)) stop("区组大小须为分配比例整数和的倍数")
seed<-.mt_integer(.mt_get("seed_v",20261008),0,.Machine$integer.max,"随机种子");set.seed(seed)
strata_v<-.mt_get("strata_v")
if(length(strata_v)) {
 if(!strata_v %in% names(dataset) || nrow(dataset)!=N || anyNA(dataset[[strata_v]])) stop("分层随机化的登记框须恰好 N 行，且分层列完整")
 stratum<-as.character(dataset[[strata_v]])
} else stratum<-rep("overall",N)
alloc<-rep(NA_character_,N);block_id<-integer(N);block_counter<-0L
for(g in unique(stratum)) {
 rows<-which(stratum==g);sequence<-character(0);ids<-integer(0)
 while(length(sequence)<length(rows)) {
  size<-blocks[sample.int(length(blocks),1)];block_counter<-block_counter+1L
  z<-sample(rep(c("T","C"),times=c(num,den)*size/unit))
  sequence<-c(sequence,z);ids<-c(ids,rep(block_counter,size))
 }
 alloc[rows]<-sequence[seq_along(rows)];block_id[rows]<-ids[seq_along(rows)]
}
allocation<-data.frame(enrollment_position=seq_len(N),stratum=stratum,block=block_id,assigned_treatment=alloc)
rules<-data.frame(N=N,treatment_ratio=ratio,block_sizes=paste(blocks,collapse=","),seed=seed,strata=if(length(strata_v)) strata_v else "none",T=sum(alloc=="T"),C=sum(alloc=="C"))
text<-paste("按预定整数比例置换区组，随机选择允许区组大小；末区组可截断。种子",seed,"，实际 T/C",sum(alloc=="T"),"/",sum(alloc=="C"),"；分层列来自登记框，未把随机分配列表当作既有治疗记录。")
.te_table("rules",rules,"实际随机化参数");.te_table("allocation",allocation,"可重现实际分配列表")
.te_text("method",text,"置换区组方法");.te_text("text",text,"分配实施与人数")
'''

CROSSOVER_BODY = r'''
.step<-"two-period-crossover"
covars<-as.character(.mt_get("covars_v",character(0)));sequence_v<-.mt_get("sequence_v")
df<-.mt_data(c(subject_v,group_v,period_v,outcome_v,sequence_v,covars))
df$.subject<-factor(df[[subject_v]]);df$.trt<-factor(df[[group_v]]);df$.period<-factor(df[[period_v]])
if(nlevels(df$.trt)!=2 || nlevels(df$.period)!=2 || any(table(df$.subject)!=2) || anyDuplicated(df[,c(".subject",".period")])) stop("当前实现要求完整二治疗、二周期、每受试者每周期一行的 2×2 交叉设计")
reference<-.mt_get("reference_v",if(bioequivalence && "R" %in% levels(df$.trt)) "R" else levels(df$.trt)[1]);df$.trt<-relevel(df$.trt,reference)
if(any(vapply(split(as.character(df$.trt),df$.subject),function(x) length(unique(x))!=2,logical(1)))) stop("每受试者必须接受两个不同治疗")
if(length(sequence_v)) {
 if(any(vapply(split(as.character(df[[sequence_v]]),df$.subject),function(x) length(unique(x))!=1,logical(1)))) stop("序列在受试者内必须一致")
 df$.sequence<-factor(df[[sequence_v]])
} else {
 sequence<-vapply(split(df,df$.subject),function(d) paste(as.character(d$.trt[order(d$.period)]),collapse="→"),character(1))
 df$.sequence<-factor(sequence[as.character(df$.subject)])
}
if(nlevels(df$.sequence)!=2) stop("2×2 设计须同时有两个治疗序列")
df$.original<-.mt_numeric(df[[outcome_v]],"交叉/PK 终点")
if(bioequivalence && any(df$.original<=0)) stop("PK log 分析要求严格正值终点")
df$.y<-if(bioequivalence) log(df$.original) else df$.original
for(v in covars) if(is.character(df[[v]])) df[[v]]<-factor(df[[v]])
form<-.mt_formula(".y",c(".trt",".period",".sequence",covars))
fit<-nlme::lme(form,data=df,random=~1|.subject,method="REML")
ct<-summary(fit)$tTable;model<-data.frame(term=rownames(ct),estimate=ct[,"Value"],SE=ct[,"Std.Error"],df=ct[,"DF"],p=ct[,"p-value"])
means<-emmeans::emmeans(fit,~.trt)
contrast<-as.data.frame(summary(emmeans::contrast(means,list("second vs reference"=c(-1,1)),adjust="none"),infer=TRUE,level=if(bioequivalence) .9 else .95))
if(bioequivalence) {
 lower<-.mt_get("lower_v",.8);upper<-.mt_get("upper_v",1.25)
 if(!is.numeric(lower) || !is.numeric(upper) || length(lower)!=1 || length(upper)!=1 || lower<=0 || lower>=upper) stop("生物等效界限无效")
 equivalence<-data.frame(reference=reference,test=levels(df$.trt)[2],geometric_mean_ratio=exp(contrast$estimate),
  lower_90=exp(contrast$lower.CL),upper_90=exp(contrast$upper.CL),lower_margin=lower,upper_margin=upper,
  equivalent=exp(contrast$lower.CL)>=lower & exp(contrast$upper.CL)<=upper,df=contrast$df)
 pk_raw<-aggregate(df$.original,by=list(formulation=df$.trt),FUN=function(x) c(n=length(x),arithmetic_mean=mean(x),geometric_mean=exp(mean(log(x)))))
 pk<-data.frame(formulation=pk_raw$formulation,n=pk_raw$x[,"n"],arithmetic_mean=pk_raw$x[,"arithmetic_mean"],geometric_mean=pk_raw$x[,"geometric_mean"])
 plot_data<-data.frame(label="geometric mean ratio",estimate=equivalence$geometric_mean_ratio,lower=equivalence$lower_90,upper=equivalence$upper_90)
 fig<-.te_forest(plot_data,title="2×2 PK：90% GMR 区间")+ggplot2::geom_vline(xintercept=c(lower,upper),linetype=2)
 .te_table("pk",as.data.frame(pk),"实际 PK 终点汇总");.te_table("equivalence",equivalence,"90% 区间与已确认等效界限")
} else {
 plot_data<-data.frame(label="treatment difference",estimate=contrast$estimate,lower=contrast$lower.CL,upper=contrast$upper.CL)
 fig<-.te_forest(plot_data,title="受试者随机截距交叉治疗效应")
 .te_table("model",model,"治疗、周期、序列及协变量交叉模型")
}
method<-paste("完整 2×2 交叉，nlme::lme 受试者随机截距；固定治疗、周期、序列及已选协变量。",
 if(bioequivalence) "终点 log 变换，emmeans 对比的 90% CI 指数化为 T/R；不套用平行组独立检验。" else "治疗对比使用 emmeans 的模型自由度与 95% CI。")
text<-paste("受试者",nlevels(df$.subject),"名；参考治疗",reference,"；实际治疗对比",contrast$estimate,"，df=",contrast$df,"。")
diagnostics<-paste("模型残差 SD=",sigma(fit),"；周期和序列项见模型；2×2 模型未独立识别与治疗混杂的差异携带效应。")
.te_plot("figure",fig,"真实治疗对比区间");.te_text("method",method,"交叉/PK 统计方法")
.te_text("text",text,"交叉/PK 实际结果");.te_text("diagnostics",diagnostics,"设计与相关结构说明")
'''

SURVIVAL_DESIGN = r'''
.step<-"schoenfeld-event-design"
hr<-.mt_numeric(effect_v,"预期风险比");alpha<-.mt_get("alpha_v",.05);power<-.mt_get("power_v",.8);ratio<-.mt_get("ratio_v",1)
event_probability<-.mt_get("probability_v");duration<-.mt_get("duration_v")
if(length(hr)!=1 || hr<=0 || hr==1 || alpha<=0 || alpha>=1 || power<=.5 || power>=1 || ratio<=0) stop("风险比、alpha、power、分配比无效")
if(length(event_probability) && (!is.numeric(event_probability) || length(event_probability)!=1 || !is.finite(event_probability) || event_probability<=0 || event_probability>1)) stop("样本量所用事件概率须在 (0,1]")
if(length(duration) && (!is.numeric(duration) || length(duration)!=1 || duration<=0)) stop("随访长度须为正数")
events<-gsDesign::nEvents(hr=hr,alpha=alpha,beta=1-power,ratio=ratio,sided=2)
event_table<-data.frame(HR=hr,alpha=alpha,power=power,allocation_ratio_T_C=ratio,events_unrounded=events,events=ceiling(events),method="Schoenfeld / gsDesign nEvents, two-sided")
event_table$followup_duration<-if(length(duration)) duration else NA_real_
if(length(event_probability)) {
 N<-ceiling(ceiling(events)/event_probability);nT<-ceiling(N*ratio/(1+ratio));nC<-N-nT
 sample_table<-data.frame(total=N,treatment=nT,control=nC,event_probability=event_probability,
  followup_duration=if(length(duration)) duration else NA_real_)
}
sensitivity<-do.call(rbind,lapply(sort(unique(c(.7,.8,.9,power))),function(p) {
 d<-gsDesign::nEvents(hr=hr,alpha=alpha,beta=1-p,ratio=ratio,sided=2)
 data.frame(power=p,events=ceiling(d),total=if(length(event_probability)) ceiling(ceiling(d)/event_probability) else NA_real_)
}))
method<-"预期 HR 的 Schoenfeld log-rank 事件设计，gsDesign::nEvents 双侧；样本量由已确认研究期事件概率反推。随访长度仅记录该概率对应的设计时间窗，未凭空推断指数发生率。"
text<-sprintf("HR=%g，双侧 alpha=%g，power=%g；需要 %d 个事件。%s",hr,alpha,power,ceiling(events),if(length(event_probability)) sprintf("按事件概率 %g 得到总样本量 %d（T=%d、C=%d）。",event_probability,N,nT,nC) else "未提供事件概率，仅报告事件数设计，不生成未知样本量。")
.te_table("events",event_table,"事件数与设计参数");if(length(event_probability)) .te_table("sample",sample_table,"事件概率对应样本量")
.te_table("sensitivity",sensitivity,"把握度敏感性");.te_text("method",method,"生存终点设计方法");.te_text("text",text,"实际设计结果")
'''

ESTIMAND = r'''
.step<-"confirmed-estimand"
spec<-.te_json(spec_v,"估计目标")
if(!all(c("intercurrent_strategy","population_level") %in% names(spec))) stop("估计目标需明确 intercurrent_strategy 和 population_level")
strategy<-.mt_select(spec$intercurrent_strategy,c("treatment_policy","composite"),"中间事件策略")
if(length(setdiff(names(spec),c("intercurrent_strategy","population_level","composite_event_value","summary_measure")))) stop("当前估计目标包含不支持的设置")
ice_v<-.mt_get("ice_v");baseline_v<-.mt_get("baseline_v");strata_v<-.mt_get("strata_v")
df<-.mt_data(c(group_v,outcome_v,pop_v,ice_v,baseline_v,strata_v));df<-.te_population(df,pop_v,spec$population_level)
df$.trt<-factor(df[[group_v]]);if(nlevels(df$.trt)!=2) stop("估计目标需要两个随机组")
reference<-.mt_get("reference_v",levels(df$.trt)[1]);df$.trt<-relevel(df$.trt,reference)
df$.y<-.te_outcome(df[[outcome_v]]);df$.ice<-if(length(ice_v)) .mt_numeric(df[[ice_v]],"中间事件标记") else rep(0,nrow(df))
if(any(!df$.ice %in% c(0,1))) stop("中间事件须为 0/1")
if(strategy=="composite") {
 if(!all(df$.y %in% c(0,1)) || is.null(spec$composite_event_value) || !spec$composite_event_value %in% c(0,1)) stop("composite 需二分类终点和明确的 composite_event_value=0/1")
 df$.y[df$.ice==1]<-spec$composite_event_value
}
binary<-all(df$.y %in% c(0,1));fam<-if(binary) binomial() else gaussian()
if(!is.null(spec$summary_measure) && !spec$summary_measure %in% if(binary) c("odds_ratio","OR") else c("mean_difference","MD")) stop("当前模型支持连续均值差或二分类 OR，与所选 summary_measure 不符")
covars<-c(baseline_v,strata_v);for(v in covars) if(is.character(df[[v]]) || v %in% strata_v) df[[v]]<-factor(df[[v]])
form<-.mt_formula(".y",c(".trt",covars));fit<-glm(form,data=df,family=fam)
if(!fit$converged || any(!is.finite(coef(fit)))) stop("估计目标模型不可估计")
primary<-.te_effect(fit,".trt",if(binary) "log_ratio" else "identity");primary$strategy<-strategy;primary$population<-spec$population_level
primary$intercurrent_events<-sum(df$.ice);primary$reference<-reference
unadjusted<-.te_effect(glm(.y~.trt,data=df,family=fam),".trt",if(binary) "log_ratio" else "identity")
comparison<-rbind(data.frame(label="prespecified adjusted",primary[,c("estimate","lower","upper")]),data.frame(label="unadjusted same estimand",unadjusted[,c("estimate","lower","upper")]))
fig<-.te_forest(comparison,title="相同已确认估计目标下的调整敏感性")
text<-paste("估计目标策略",strategy,"；人群",spec$population_level,"，n=",nrow(df),"；中间事件",sum(df$.ice),"人。",
 if(strategy=="treatment_policy") "无论是否中间事件均保留已确认分析集；不按观察到的中间事件事后剔除。" else paste("复合策略中间事件结局明确置为",spec$composite_event_value),"模型使用全部已选基线/分层变量。")
.te_table("primary",primary,"实际估计目标与效应");.te_plot("figure",fig,"调整敏感性")
.te_text("text",text,"ICH E9 估计目标执行结果")
'''

TIPPING = r'''
.step<-"missingness-confirmation"
covars<-unique(c(as.character(.mt_get("covars_v",character(0))),.mt_get("baseline_v"),.mt_get("visit_v")))
cols<-unique(c(group_v,outcome_v,missing_v,covars));if(length(setdiff(cols,names(dataset)))) stop("所选缺失敏感性列不存在")
df<-dataset[,cols,drop=FALSE]
df$.missing<-df[[missing_v]]
if(anyNA(df$.missing) || any(!df$.missing %in% c(0,1)) || !identical(as.logical(df$.missing),is.na(df[[outcome_v]]))) stop("0/1 缺失标记必须准确对应本轮结局 NA")
if(!any(df$.missing)) stop("结局无缺失，不执行 Tipping-Point")
if(!is.numeric(df[[outcome_v]]) || all(na.omit(df[[outcome_v]]) %in% c(0,1))) stop("当前 delta-PMM 仅支持连续终点")
if(anyNA(df[,c(group_v,covars),drop=FALSE])) stop("当前结局 delta-PMM 要求治疗、基线/协变量完整")
df$.trt<-factor(df[[group_v]]);if(nlevels(df$.trt)!=2) stop("需要两个随机组")
reference<-.mt_get("reference_v",levels(df$.trt)[1]);df$.trt<-relevel(df$.trt,reference);df$.y<-df[[outcome_v]]
for(v in covars) if(is.character(df[[v]])) df[[v]]<-factor(df[[v]])
m<-.mt_integer(.mt_get("m_v",10),5,50,"插补份数");iterations<-.mt_integer(.mt_get("iterations_v",10),5,100,"插补迭代数")
seed<-.mt_integer(.mt_get("seed_v",20261008),0,.Machine$integer.max,"随机种子")
grid<-.mt_get("delta_v")
if(is.null(grid)) {sd<-sd(df$.y,na.rm=TRUE);grid<-list(control=c(-1,-.5,0,.5,1)*sd,treatment=c(-1,-.5,0,.5,1)*sd)}
else grid<-.te_json(grid,"delta 网格")
if(!all(c("control","treatment") %in% names(grid))) stop("delta 网格须有 control 与 treatment 数值数组")
control<-.mt_numeric(unlist(grid$control),"对照 delta");treatment<-.mt_numeric(unlist(grid$treatment),"治疗 delta")
if(length(control)*length(treatment)>100) stop("delta 组合不得超过一百")
imp_data<-df[,c(".y",".trt",covars),drop=FALSE]
.step<-"mice-pmm-and-delta"
imp<-mice::mice(imp_data,m=m,maxit=iterations,method="pmm",printFlag=FALSE,seed=seed)
.step<-"delta-imputation-completion"
completed<-lapply(seq_len(m),function(i) mice::complete(imp,i));form<-.mt_formula(".y",c(".trt",covars));pieces<-list()
for(dc in control) for(dt in treatment) {
 fits<-lapply(completed,function(d) {
  d$.y[df$.missing==1 & df$.trt==levels(df$.trt)[1]]<-d$.y[df$.missing==1 & df$.trt==levels(df$.trt)[1]]+dc
  d$.y[df$.missing==1 & df$.trt==levels(df$.trt)[2]]<-d$.y[df$.missing==1 & df$.trt==levels(df$.trt)[2]]+dt
  lm(form,data=d)
 })
 .step<-"Rubin-pool"
 pooled<-summary(mice::pool(mice::as.mira(fits)),conf.int=TRUE)
 z<-pooled[startsWith(as.character(pooled$term),".trt"),,drop=FALSE];if(nrow(z)!=1) stop("插补后治疗效应不可唯一识别")
 pieces[[length(pieces)+1]]<-data.frame(delta_control=dc,delta_treatment=dt,estimate=z$estimate,SE=z$std.error,
  lower=z[["2.5 %"]],upper=z[["97.5 %"]],p=z$p.value,m=m,missing=sum(df$.missing),CI_excludes_zero=z[["2.5 %"]]>0 | z[["97.5 %"]]<0)
}
primary<-do.call(rbind,pieces)
fig<-ggplot2::ggplot(primary,ggplot2::aes(x=delta_control,y=delta_treatment,fill=estimate))+
 ggplot2::geom_tile()+ggplot2::geom_point(ggplot2::aes(shape=CI_excludes_zero),colour="white")+ggplot2::theme_bw()+
 ggplot2::labs(title="真实 delta-MI Tipping-Point 网格",x="对照缺失结局偏移",y="治疗缺失结局偏移")
text<-sprintf("mice PMM %d 份、%d 迭代；%d 个实际缺失结局，仅这些插补结局按组加 delta；每格重新拟合同一线性调整模型，Rubin 合并（含插补内外方差及自由度）。%d 个 delta 组合，%d 个区间排除零。默认网格为观测 SD 的 -1/-0.5/0/0.5/1，不把敏感性范围当作已识别 MNAR 机制。",m,iterations,sum(df$.missing),nrow(primary),sum(primary$CI_excludes_zero))
.te_table("primary",primary,"实际 delta 组合与 Rubin 合并效应");.te_plot("figure",fig,"实际缺失敏感性热图")
.te_text("text",text,"缺失敏感性计算结果")
'''

PHASE1 = r'''
.step<-"completed-dlt-cohorts"
cohort_v<-.mt_get("cohort_v");subject_v<-.mt_get("subject_v");signal_v<-.mt_get("signal_v");complete_v<-.mt_get("complete_v")
df<-.mt_data(c(dose_v,outcome_v,cohort_v,subject_v,signal_v,complete_v))
df$.dose<-.mt_numeric(df[[dose_v]],"剂量");df$.dlt<-.mt_numeric(df[[outcome_v]],"DLT")
if(any(!df$.dlt %in% c(0,1))) stop("DLT 必须为 0/1")
if(length(subject_v) && anyDuplicated(df[[subject_v]])) stop("每受试者只允许一条当前 DLT 记录")
window<-.mt_get("window_v")
if(length(window) && (!is.numeric(window) || length(window)!=1 || window<=0)) stop("DLT 观察窗须为正数")
if(length(complete_v) && any(!df[[complete_v]] %in% c(0,1))) stop("观察窗完成标记须为 0/1")
if(length(complete_v) && any(df[[complete_v]]!=1)) stop("3+3 当前决策要求所有纳入者完成 DLT 观察窗；存在待定受试者")
if(length(cohort_v)) {
 df$.cohort<-.mt_numeric(df[[cohort_v]],"按时间递增的队列号")
 if(any(df$.cohort!=floor(df$.cohort) | df$.cohort<1)) stop("队列号须按时间递增的正整数")
 if(any(vapply(split(df$.dose,df$.cohort),function(x) length(unique(x))!=1,logical(1)))) stop("同一队列不能混合剂量")
} else df$.cohort<-ceiling(seq_len(nrow(df))/3)
cohorts<-aggregate(df$.dlt,by=list(cohort=df$.cohort,dose=df$.dose),FUN=function(x) c(n=length(x),DLT=sum(x)))
cohorts<-data.frame(cohort=cohorts$cohort,dose=cohorts$dose,n=cohorts$x[,"n"],DLT=cohorts$x[,"DLT"])
if(any(!cohorts$n %in% c(3,6))) stop("3+3 完成队列必须为三人或六人；不对未完成队列给升级决策")
levels<-.mt_get("levels_v",sort(unique(df$.dose)));levels<-.mt_numeric(unlist(levels),"预设剂量级别")
if(anyDuplicated(levels) || is.unsorted(levels,strictly=TRUE) || any(!df$.dose %in% levels)) stop("预设剂量级别须严格递增并包含观测剂量")
current<-tail(cohorts,1)$dose
dose_table<-do.call(rbind,lapply(sort(unique(df$.dose)),function(v) {
 z<-df[df$.dose==v,,drop=FALSE];ci<-binom.test(sum(z$.dlt),nrow(z))$conf.int
 data.frame(dose=v,n=nrow(z),DLT=sum(z$.dlt),rate=mean(z$.dlt),lower=ci[1],upper=ci[2])
}))
z<-dose_table[dose_table$dose==current,,drop=FALSE]
if(!z$n %in% c(3,6)) stop("同一剂量累计完成者须三人或六人，超出标准 3+3 设计")
action<-if(z$DLT>=2) "stop escalation; previous planned dose is MTD candidate" else if(z$n==3 && z$DLT==1) "expand current dose to six" else "escalate to next planned dose"
position<-match(current,levels)
next_dose<-if(z$DLT>=2) {if(position>1) levels[position-1] else NA_real_} else if(z$n==3 && z$DLT==1) current else if(position<length(levels)) levels[position+1] else NA_real_
dose_table$current<-dose_table$dose==current;dose_table$decision<-ifelse(dose_table$current,action,"completed historical dose")
dose_table$next_or_candidate_dose<-ifelse(dose_table$current,next_dose,NA_real_)
if(length(signal_v)) {df$.signal<-.mt_numeric(df[[signal_v]],"辅助安全信号");ss<-aggregate(df$.signal,list(dose=df$.dose),mean);dose_table$auxiliary_signal_mean<-ss$x[match(dose_table$dose,ss$dose)]}
fig<-ggplot2::ggplot(dose_table,ggplot2::aes(x=dose,y=rate))+ggplot2::geom_errorbar(ggplot2::aes(ymin=lower,ymax=upper),width=0)+ggplot2::geom_point()+ggplot2::theme_bw()+ggplot2::labs(title="已完成观察的 DLT：精确二项 95% CI",y="DLT 比例")
text<-paste("标准 3+3，当前剂量",current,"：",z$DLT,"/",z$n,"DLT；",action,"。下一剂量/候选",next_dose,
 "；图为 Clopper–Pearson 区间，不冒充毒性后验。",if(length(window)) paste("确认观察窗",window,"；") else "",
 if(length(complete_v)) "已根据实际观察窗完成列确认完整。" else "未提供观察窗完成列，输入约定为已完成 DLT；没有从观察窗长度凭空推断已完成状态。")
.te_table("primary",dose_table,"实际剂量/DLT 与 3+3 决策");.te_plot("figure",fig,"真实 DLT 精确区间");.te_text("text",text,"3+3 实际规则与观察窗")
'''

REESTIMATION = r'''
.step<-"blinded-nuisance-variance"
baseline_v<-.mt_get("baseline_v");strata_v<-.mt_get("strata_v");event_v<-.mt_get("event_v")
if(length(event_v)) stop("当前实现为连续终点盲态方差重估；生存事件终点需独立事件驱动重估设计")
df<-.mt_data(c(group_v,outcome_v,stage_v,baseline_v,strata_v))
df$.y<-.mt_numeric(df[[outcome_v]],"连续终点")
if(all(df$.y %in% c(0,1))) stop("连续终点盲态方差重估不支持二分类终点")
level<-.mt_get("stage_level_v",tail(sort(unique(df[[stage_v]])),1))
if(!level %in% df[[stage_v]]) stop("选定中期阶段不存在")
df<-df[df[[stage_v]]==level,,drop=FALSE]
df$.trt<-factor(df[[group_v]]);if(nlevels(df$.trt)!=2 || nrow(df)<20) stop("当前中期阶段需两个随机组、至少二十人")
for(v in c(baseline_v,strata_v)) if(is.character(df[[v]]) || v %in% strata_v) df[[v]]<-factor(df[[v]])
form<-.mt_formula(".y",c(baseline_v,strata_v));fit<-lm(form,data=df)
if(fit$rank<length(coef(fit))) stop("盲态协变量模型不可估计")
sd<-sigma(fit);difference<-.mt_get("difference_v");alpha<-.mt_get("alpha_v",.05);power<-.mt_get("power_v",.8)
ratio<-.mt_get("ratio_v",1);cap<-.mt_get("cap_v")
if(!is.numeric(difference) || length(difference)!=1 || !is.finite(difference) || difference==0) stop("必须提供已确认、非零 target_difference")
if(alpha<=0 || alpha>=1 || power<=.5 || power>=1 || ratio<=0 || !is.finite(sd) || sd<=0) stop("重估参数无效")
design<-rpact::getSampleSizeMeans(alpha=alpha,beta=1-power,sided=2,alternative=abs(difference),stDev=sd,allocationRatioPlanned=ratio)
planned<-ceiling(design$nFixed);minimum<-nrow(df)
if(length(cap) && (!is.numeric(cap) || length(cap)!=1 || cap<minimum || cap!=floor(cap))) stop("最大样本量须为不小于当前人数的整数")
reestimated<-max(minimum,planned);if(length(cap)) reestimated<-min(reestimated,cap)
primary<-data.frame(interim_stage=as.character(level),interim_n=minimum,residual_df=df.residual(fit),blinded_pooled_SD=sd,target_difference=difference,
 alpha=alpha,target_power=power,allocation_ratio=ratio,rpact_total_unrounded=design$nFixed,uncapped_total=max(minimum,planned),reestimated_total=reestimated,
 cap=if(length(cap)) cap else NA_real_,treatment_effect_used=FALSE)
curve<-do.call(rbind,lapply(seq(.6,.95,length.out=12),function(p) {
 d<-rpact::getSampleSizeMeans(alpha=alpha,beta=1-p,sided=2,alternative=abs(difference),stDev=sd,allocationRatioPlanned=ratio)
 data.frame(target_power=p,required_total=ceiling(d$nFixed))
}))
fig<-ggplot2::ggplot(curve,ggplot2::aes(x=target_power,y=required_total))+ggplot2::geom_line()+ggplot2::geom_point()+ggplot2::theme_bw()+ggplot2::labs(title="盲态 SD 对应固定设计样本量与目标把握度",y="所需总样本量")
text<-paste("rpact 双侧连续均值设计；盲态模型不含治疗组，只根据当前阶段结局与预定基线/分层残差 SD=",sd,"重估。预设差值",difference,"；总 N=",reestimated,
 "。图为目标把握度设计曲线；没有把它称为条件把握度，也未使用观察组间差异。该盲态 pooled SD 可能含组间差异成分；用户选择的协议需允许此具体估计策略。")
.te_table("primary",primary,"实际盲态方差与重估样本量");.te_plot("figure",fig,"固定设计把握度/样本量曲线");.te_text("text",text,"具体重估方法与中期来源")
'''

ADAPTIVE = r'''
.step<-"recorded-allocation-evaluation"
strata<-as.character(.mt_get("strata_v",character(0)));stage_v<-.mt_get("stage_v");outcome_v<-.mt_get("outcome_v");prob_v<-.mt_get("prob_v")
df<-.mt_data(c(subject_v,group_v,order_v,strata,stage_v,outcome_v,prob_v))
if(anyDuplicated(df[[subject_v]]) || anyDuplicated(df[[order_v]])) stop("受试者及入组顺序必须唯一")
df$.order<-.mt_numeric(df[[order_v]],"入组顺序");df<-df[order(df$.order),,drop=FALSE]
df$.trt<-factor(df[[group_v]]);if(nlevels(df$.trt)!=2) stop("当前记录评估需要两个治疗组")
reference<-.mt_get("reference_v",levels(df$.trt)[1]);df$.trt<-relevel(df$.trt,reference)
target<-.mt_get("target_v",.5);if(!is.numeric(target) || length(target)!=1 || target<=0 || target>=1) stop("目标治疗组占比须在 (0,1)")
df$.stratum<-if(length(strata)) interaction(df[,strata,drop=FALSE],drop=TRUE,sep="|") else factor("overall")
df$.stage<-if(length(stage_v)) as.character(df[[stage_v]]) else "all"
summary<-do.call(rbind,lapply(split(df,interaction(df$.stratum,df$.stage,drop=TRUE)),function(z) {
 nT<-sum(z$.trt==levels(df$.trt)[2]);nC<-nrow(z)-nT
 data.frame(stratum=as.character(z$.stratum[1]),stage=z$.stage[1],n=nrow(z),control=nC,treatment=nT,
 treatment_fraction=nT/nrow(z),target_fraction=target,absolute_fraction_deviation=abs(nT/nrow(z)-target))
}))
trajectory<-data.frame(enrollment_order=df$.order,treated=as.integer(df$.trt==levels(df$.trt)[2]))
trajectory$cumulative_n<-seq_len(nrow(df));trajectory$treatment_fraction<-cumsum(trajectory$treated)/trajectory$cumulative_n
if(length(prob_v)) {
 df$.prob<-.mt_numeric(df[[prob_v]],"已分配组的真实概率")
 if(any(df$.prob<=0 | df$.prob>=1)) stop("实际分配概率须在 (0,1)")
 summary$weight_source<-"user supplied assigned-treatment probability"
}
effect_text<-"未提供结局，仅评估真实分配记录。"
if(length(outcome_v)) {
 df$.y<-.te_outcome(df[[outcome_v]])
 by_arm<-aggregate(df$.y,list(treatment=as.character(df$.trt)),mean)
 summary$observed_control_mean<-by_arm$x[match(levels(df$.trt)[1],by_arm$treatment)]
 summary$observed_treatment_mean<-by_arm$x[match(levels(df$.trt)[2],by_arm$treatment)]
 effect_text<-"结局均值为描述性，未自动把非均衡自适应样本的普通差异作无偏因果估计。"
 if(length(prob_v)) {
  weighted<-vapply(levels(df$.trt),function(g) {z<-df[df$.trt==g,];sum(z$.y/z$.prob)/sum(1/z$.prob)},numeric(1))
  summary$IPW_control_hajek<-weighted[1];summary$IPW_treatment_hajek<-weighted[2]
  effect_text<-"使用实际分配给该组的概率，另报 Hájek IPW 均值；未凭入组顺序反推概率或自动推断分配算法。"
 }
}
fig<-ggplot2::ggplot(trajectory,ggplot2::aes(x=enrollment_order,y=treatment_fraction))+ggplot2::geom_line()+ggplot2::geom_hline(yintercept=target,linetype=2)+ggplot2::theme_bw()+ggplot2::labs(title="真实入组记录的累积分配比例",y="治疗组累积占比")
text<-paste("按实际入组顺序核验",nrow(df),"条唯一记录，参考组",reference,"；逐分层/阶段报告分配人数与预定目标偏离。",effect_text,"此模板评估记录，未生成新的治疗分配或声称验证未提供的适应算法。")
.te_table("primary",summary,"真实分层/阶段分配与已提供结局");.te_plot("figure",fig,"实际累积分配轨迹");.te_text("text",text,"分配记录评估边界")
'''

_BODIES = {
    "network_meta": NETWORK, "diagnostic_meta": DIAGNOSTIC_META,
    "bayesian_meta": BAYESIAN_META, "meta_bias_sensitivity": META_BIAS,
    "safety_meta_analysis": SAFETY_META, "living_review_update": LIVING,
    "grade_evidence_summary": GRADE, "ipd_meta_mixed": IPD,
    "dose_response_trial": DOSE_RESPONSE, "rct_primary_analysis": RCT,
    "sap_protocol_stats": SAP, "randomization_allocation": RANDOMIZATION,
    "crossover_trial_analysis": "bioequivalence<-FALSE\n" + CROSSOVER_BODY,
    "bioequivalence_pk": "bioequivalence<-TRUE\n" + CROSSOVER_BODY,
    "survival_trial_design": SURVIVAL_DESIGN, "trial_estimand_ich_e9": ESTIMAND,
    "trial_missing_tipping_point": TIPPING, "trial_phase1_dose_escalation": PHASE1,
    "trial_sample_size_reestimation": REESTIMATION, "trial_adaptive_randomization": ADAPTIVE,
}
CORES = {key: COMMON + TRIAL_COMMON + body for key, body in _BODIES.items()}

INPUTS = {
    "network_meta": {"study_id":"study_v","treatment_arm":"arm_v","event_count":"event_v","intervention":"treatment_v","sample_size":"n_v","reference_treatment":"reference_v","outcome_direction":"direction_v"},
    "diagnostic_meta": {"study_id":"study_v","true_positive":"tp_v","false_positive":"fp_v","false_negative":"fn_v","true_negative":"tn_v","threshold":"threshold_v"},
    "bayesian_meta": {"study_id":"study_v","effect_estimate":"effect_v","standard_error":"se_v","prior":"prior_v"},
    "meta_bias_sensitivity": {"study_id":"study_v","effect_estimate":"effect_v","standard_error":"se_v","risk_of_bias":"rob_v"},
    "safety_meta_analysis": {"study_id":"study_v","treatment_event_count":"et_v","treatment_sample_size":"nt_v","control_event_count":"ec_v","control_sample_size":"nc_v","subgroup":"sub_v"},
    "living_review_update": {"study_id":"study_v","effect_estimate":"effect_v","standard_error":"se_v","time":"time_v","study_version":"version_v","subgroup":"sub_v"},
    "grade_evidence_summary": {"outcome":"outcome_v","effect_estimate":"effect_v","standard_error":"se_v","evidence_certainty":"certainty_v","absolute_risk":"risk_v","study_id":"study_v","effect_scale":"scale_v"},
    "ipd_meta_mixed": {"study_id":"study_v","subject_id":"subject_v","group":"group_v","outcome":"outcome_v","covariates":"covars_v","subgroup":"sub_v","reference_group":"reference_v","positive_class":"positive_v"},
    "dose_response_trial": {"dose":"dose_v","outcome":"outcome_v","outcome_2":"tox_v","covariates":"covars_v","outcome_direction":"direction_v","positive_class":"positive_v"},
    "rct_primary_analysis": {"group":"group_v","outcome":"outcome_v","analysis_population":"pop_v","covariates":"covars_v","strata":"strata_v","subgroup":"sub_v","analysis_population_level":"population_v","reference_group":"reference_v","positive_class":"positive_v"},
    "sap_protocol_stats": {"treatment":"group_v","outcome":"outcome_v","analysis_population":"pop_v","missingness_indicator":"missing_v","covariates":"covars_v","sap_spec":"spec_v"},
    "randomization_allocation": {"sample_size":"n_v","strata":"strata_v","allocation_ratio":"ratio_v","block_size":"block_v","seed":"seed_v"},
    "crossover_trial_analysis": {"subject_id":"subject_v","group":"group_v","period_variable":"period_v","outcome":"outcome_v","series_variable":"sequence_v","covariates":"covars_v","reference_group":"reference_v"},
    "bioequivalence_pk": {"subject_id":"subject_v","formulation_variable":"group_v","period_variable":"period_v","outcome":"outcome_v","series_variable":"sequence_v","covariates":"covars_v","reference_group":"reference_v","equivalence_lower":"lower_v","equivalence_upper":"upper_v"},
    "survival_trial_design": {"effect_size":"effect_v","alpha":"alpha_v","power":"power_v","allocation_ratio":"ratio_v","followup_duration":"duration_v","event_probability":"probability_v"},
    "trial_estimand_ich_e9": {"randomized_group":"group_v","outcome":"outcome_v","analysis_population":"pop_v","intercurrent_event":"ice_v","baseline_value":"baseline_v","strata":"strata_v","estimand_spec":"spec_v","reference_group":"reference_v","positive_class":"positive_v"},
    "trial_missing_tipping_point": {"randomized_group":"group_v","outcome":"outcome_v","missingness_indicator":"missing_v","baseline_value":"baseline_v","visit_time":"visit_v","covariates":"covars_v","delta_grid":"delta_v","imputations":"m_v","max_iterations":"iterations_v","seed":"seed_v","reference_group":"reference_v"},
    "trial_phase1_dose_escalation": {"dose":"dose_v","outcome":"outcome_v","cohort":"cohort_v","subject_id":"subject_v","signal":"signal_v","observation_window":"window_v","observation_window_complete":"complete_v","dose_levels":"levels_v"},
    "trial_sample_size_reestimation": {"randomized_group":"group_v","outcome":"outcome_v","interim_stage":"stage_v","baseline_value":"baseline_v","event_time":"event_v","strata":"strata_v","target_difference":"difference_v","alpha":"alpha_v","power":"power_v","allocation_ratio":"ratio_v","max_sample_size":"cap_v","interim_stage_level":"stage_level_v"},
    "trial_adaptive_randomization": {"subject_id":"subject_v","treatment_assignment":"group_v","enrollment_order":"order_v","strata":"strata_v","stage":"stage_v","outcome":"outcome_v","allocation_probability":"prob_v","target_treatment_fraction":"target_v","reference_group":"reference_v","positive_class":"positive_v"},
}

ROLE_MAPS = {
    "network_meta": {"figure_1":"network_fig","table_1":"league","figure_2":"rank_fig","diagnostic_output":"diagnostics","table_3":"ranking","scientific_result_interpretation":"results_text"},
    "diagnostic_meta": {"roc_output":"roc_fig","table_1":"sensitivity","table_2":"specificity","table_3":"heterogeneity","diagnostic_output":"accuracy","forest_plot":"forest_fig","text_1":"text"},
    "bayesian_meta": {"forest_plot":"forest_fig","table_1":"prediction","sensitivity_output":"sensitivity","table_3":"posterior","manuscript_results":"text"},
    "meta_bias_sensitivity": {"table_1":"bias","figure_1":"influence_fig","table_2":"subgroups","table_3":"regression","sensitivity_output":"sensitivity","funnel_plot":"funnel_fig","figure_3":"influence_fig","manuscript_results":"text"},
    "safety_meta_analysis": {"forest_plot":"forest_fig","sensitivity_output":"sensitivity","text_1":"bias","method_note":"method","manuscript_results":"text","diagnostic_output":"diagnostics"},
    "living_review_update": {"table_1":"updates","forest_plot":"forest_fig","manuscript_results":"text","method_note":"method","manuscript_results_2":"text","method_note_2":"method","manuscript_results_3":"text","figure_2":"caption"},
    "grade_evidence_summary": {"table_1":"sof","text_1":"certainty","text_2":"text","method_note":"method","manuscript_results":"text","method_note_2":"method","manuscript_results_2":"text","figure_1":"caption"},
    "ipd_meta_mixed": {"table_1":"model","table_2":"heterogeneity","table_3":"subgroups","method_note":"method","manuscript_results":"text","diagnostic_output":"diagnostics"},
    "dose_response_trial": {"figure_1":"curve","table_1":"comparison","table_2":"recommendation","table_3":"comparison","text_1":"text","table_4":"safety"},
    "rct_primary_analysis": {"table_1":"primary","forest_plot":"forest","sensitivity_output":"sensitivity","table_3":"primary","table_4":"model","text_1":"population","manuscript_results":"text"},
    "sap_protocol_stats": {"table_1":"outline","table_2":"shell","method_note":"method","method_note_2":"method","manuscript_results":"text"},
    "randomization_allocation": {"table_1":"rules","table_2":"allocation","text_1":"text","method_note":"method","manuscript_results":"text"},
    "crossover_trial_analysis": {"table_1":"model","figure_1":"figure","manuscript_results":"text","method_note":"method","manuscript_results_2":"text","diagnostic_output":"diagnostics"},
    "bioequivalence_pk": {"table_1":"pk","table_2":"equivalence","figure_1":"figure","method_note":"method","manuscript_results":"text","diagnostic_output":"diagnostics"},
    "survival_trial_design": {"table_1":"events","table_2":"sample","sensitivity_output":"sensitivity","method_note":"method","manuscript_results":"text"},
    "trial_estimand_ich_e9": {"primary_table":"primary","diagnostic_plot":"figure","manuscript_results":"text"},
    "trial_missing_tipping_point": {"primary_table":"primary","diagnostic_plot":"figure","manuscript_results":"text"},
    "trial_phase1_dose_escalation": {"primary_table":"primary","diagnostic_plot":"figure","manuscript_results":"text"},
    "trial_sample_size_reestimation": {"primary_table":"primary","diagnostic_plot":"figure","manuscript_results":"text"},
    "trial_adaptive_randomization": {"primary_table":"primary","diagnostic_plot":"figure","manuscript_results":"text"},
}

REFERENCE_CASES = {
    "network_meta": "netmeta 实现接受两臂/多臂二分类 OR；当前真实参考验证九项两臂研究组成的三治疗网络及联赛表、P-score、比较图。",
    "diagnostic_meta": "mada Reitsma 双变量真实四格；灵敏度/特异度、协方差、SROC 及精确二项区间核验。",
    "bayesian_meta": "实现正态均值及半正态/半Cauchy异质性先验；当前真实参考使用 half_normal，验证 bayesmeta 后验/新研究预测区间及未知先验字段拒绝。",
    "meta_bias_sensitivity": "metafor REML 独立参考合并；真实 Egger、风险 Meta 回归、LOO、Cook 距离。",
    "safety_meta_analysis": "meta MH RR 与 DL/逆方差 RD；零单元格和双零研究真实处理，参考 RR 一致。",
    "living_review_update": "按明确数字版本更新，同研究旧版本替换；最终十二独立研究不重复计权，REML 参考一致。",
    "grade_evidence_summary": "真实效应合并与 log-RR 绝对风险换算；逐项保留用户确认等级，不自动发明降级判断。",
    "ipd_meta_mixed": "lmer 一阶段研究随机截距与调整治疗均值差参考；未选亚组不生成亚组占位表。",
    "dose_response_trial": "线性/二次/mgcv GAM 同样本 AIC，实际曲线与已观察范围推荐；仅已选毒性终点输出安全表。",
    "rct_primary_analysis": "实际 ITT 筛选、协变量/分层 Gaussian 模型和主效应独立参考；同样本调整敏感性。",
    "sap_protocol_stats": "明确冻结 SAP JSON 方案，真实人数表壳；缺必需策略拒绝，不把表壳伪作研究终点结果。",
    "randomization_allocation": "真实分层置换区组、可变4/8区组、冻结种子；完整48人组间24/24，未反推既有分配。",
    "crossover_trial_analysis": "完整2×2 nlme 随机受试者、治疗/周期/序列；emmeans 模型自由度区间和独立治疗效应参考。",
    "bioequivalence_pk": "完整2×2 正值PK log模型，90% T/R 几何均值区间、明确0.8/1.25界限，独立 nlme 参考。",
    "survival_trial_design": "gsDesign 双侧 Schoenfeld HR 事件设计参考；无概率仅事件设计，有已确认事件概率才转样本量。",
    "trial_estimand_ich_e9": "已确认 treatment-policy 分析集调整参考；composite 中间事件按明确值真实重编码，不虚构 hypothetical estimand。",
    "trial_missing_tipping_point": "真实 mice PMM delta-MI，Rubin 方差/自由度；仅缺失行平移、四格delta效应方向与区间核验。",
    "trial_phase1_dose_escalation": "标准3+3完成三/六人队列，1/6升级至明确下一级，精确二项区间；观察窗未完成拒绝决策。",
    "trial_sample_size_reestimation": "连续终点盲态 pooled 调整残差SD、rpact固定设计重估N独立参考；生存分支明确拒绝。",
    "trial_adaptive_randomization": "实际顺序/分层/阶段分配轨迹；使用提供的真实assigned-treatment概率计算Hájek均值，参考手算一致。",
}
