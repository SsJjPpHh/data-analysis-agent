"""Deterministic general-statistics and behavioral research workflows.

Every workflow has a dedicated body and its own typed input/output meaning.
Shared helpers only read already accessed frozen inputs and emit artifacts.
"""
from __future__ import annotations

import json

from .models import AnalysisPlan
from .statistics_behavior_cores import _COMMON

_EXTRA = r'''
.gb_json <- function(x) if(is.character(x)&&length(x)==1L) jsonlite::fromJSON(x,simplifyVector=TRUE) else x
.gb_note <- function(text) { .sb_text("method_note",text,"实际执行方法"); .sb_text("manuscript_results",text,"结果与解释范围") }
.gb_sem_table <- function(fit) {
  if(!isTRUE(lavaan::lavInspect(fit,"converged"))) stop("SEM 未收敛")
  pe<-lavaan::parameterEstimates(fit,standardized=TRUE,ci=TRUE);if(is.null(pe$group)) pe$group<-1L
  .diag_status[["收敛检查"]]<<-"passed"
  .diag_status[["Heywood 问题检查"]]<<-if(isTRUE(lavaan::lavInspect(fit,"post.check"))) "passed" else "warning"
  pe
}
.gb_coef_plot <- function(tab,estimate="est",label="rhs") {
  ggplot2::ggplot(tab,ggplot2::aes(x=stats::reorder(.data[[label]],.data[[estimate]]),y=.data[[estimate]]))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw()
}
.gb_network_plot <- function(graph) {
  set.seed(as.integer(.sb_input("seed",1729L)));coords<-igraph::layout_with_fr(graph)
  nodes<-data.frame(name=igraph::V(graph)$name,x=coords[,1],y=coords[,2]);el<-igraph::as_edgelist(graph,names=FALSE)
  edges<-data.frame(x=coords[el[,1],1],y=coords[el[,1],2],xend=coords[el[,2],1],yend=coords[el[,2],2])
  ggplot2::ggplot()+ggplot2::geom_segment(data=edges,ggplot2::aes(x=x,y=y,xend=xend,yend=yend),alpha=.4)+ggplot2::geom_point(data=nodes,ggplot2::aes(x=x,y=y),size=3)+ggplot2::geom_text(data=nodes,ggplot2::aes(x=x,y=y,label=name),vjust=-.7)+ggplot2::theme_void()
}
.gb_text_matrix <- function(text,ids) {
  if(anyNA(text)||any(!nzchar(trimws(text)))) stop("文本为空或缺失")
  tok<-quanteda::tokens(as.character(text),remove_punct=TRUE,remove_numbers=TRUE);tok<-quanteda::tokens_tolower(tok)
  d<-quanteda::dfm(tok);if(quanteda::nfeat(d)<1L) stop("无有效词项")
  quanteda::docnames(d)<-ids;d
}
.gb_panel <- function(id,time,vars,extra=character(0)) {
  dat<-.sb_complete(.sb_cols(c(id,time,vars,extra)),10L); .sb_numeric(dat,c(time,vars))
  if(anyDuplicated(dat[,c(id,time)])) stop("同一对象和时间点存在重复行")
  times<-sort(unique(dat[[time]]));subjects<-unique(as.character(dat[[id]]));if(length(times)<3L) stop("至少三个共同测量时间点")
  wide<-data.frame(id=subjects,stringsAsFactors=FALSE);mapping<-list()
  for(j in seq_along(vars)) for(k in seq_along(times)) {
    col<-paste0("v",j,"t",k);vals<-dat[dat[[time]]==times[k],c(id,vars[j]),drop=FALSE];wide[[col]]<-vals[[vars[j]]][match(subjects,as.character(vals[[id]]))];mapping[[paste(j,k,sep="_")]]<-col
  }
  if(anyNA(wide)) stop("当前固定纵向 SEM 需要共同时间点完整测量；不静默丢弃不完整对象")
  for(v in extra) wide[[v]]<-vapply(subjects,function(s){ z<-unique(dat[[v]][as.character(dat[[id]])==s]);if(length(z)!=1L) stop("对象层协变量在时间内变化：",v);as.character(z) },character(1))
  for(v in extra) if(is.numeric(dat[[v]])) wide[[v]]<-as.numeric(wide[[v]]) else wide[[v]]<-factor(wide[[v]])
  list(wide=wide,times=times,mapping=mapping,long=dat)
}
'''

_HELPER_NAMES = (".gb_json", ".gb_note", ".gb_sem_table", ".gb_coef_plot", ".gb_network_plot", ".gb_text_matrix", ".gb_panel")
_HELPER_PARTS = {}
for _index, _name in enumerate(_HELPER_NAMES):
    _start = _EXTRA.index(_name + " <-")
    _end = _EXTRA.index(_HELPER_NAMES[_index + 1] + " <-") if _index + 1 < len(_HELPER_NAMES) else len(_EXTRA)
    _HELPER_PARTS[_name] = _EXTRA[_start:_end]

_BODIES = {}

_BODIES["distribution_visual"] = r'''
vars<-as.character(.sb_input("continuous_variables",character(0)));group<-.sb_input("group");batch<-.sb_input("batch")
if(!length(vars)) stop("分布探索需要明确选择连续变量")
dat<-.sb_cols(c(vars,group,batch));.sb_numeric(dat,vars)
long<-do.call(rbind,lapply(vars,function(v) data.frame(变量=v,值=dat[[v]],分组=if(is.null(group)) "总体" else as.character(dat[[group]]),批次=if(is.null(batch)) "总体" else as.character(dat[[batch]]))))
rows<-lapply(vars,function(v){x<-dat[[v]];xx<-x[is.finite(x)];if(length(xx)<2L) stop("有效连续值不足");q<-quantile(xx,c(.25,.75));data.frame(变量=v,有效数=length(xx),缺失数=sum(is.na(x)),均值=mean(xx),SD=sd(xx),中位数=median(xx),Q1=q[1],Q3=q[2],IQR外数=sum(xx<q[1]-1.5*diff(q)|xx>q[2]+1.5*diff(q)))})
summary_table<-do.call(rbind,rows)
hist<-ggplot2::ggplot(long,ggplot2::aes(x=值,fill=分组))+ggplot2::geom_histogram(bins=30,alpha=.5,position="identity")+ggplot2::facet_wrap(~变量,scales="free")+ggplot2::theme_bw()
box<-ggplot2::ggplot(long,ggplot2::aes(x=分组,y=值,fill=批次))+ggplot2::geom_boxplot()+ggplot2::facet_wrap(~变量,scales="free_y")+ggplot2::theme_bw()
relation<-if(length(vars)>=2L) ggplot2::ggplot(dat,ggplot2::aes(x=.data[[vars[1]]],y=.data[[vars[2]]]))+ggplot2::geom_point()+ggplot2::theme_bw() else ggplot2::ggplot(long,ggplot2::aes(sample=值))+ggplot2::stat_qq()+ggplot2::stat_qq_line()+ggplot2::theme_bw()
.sb_table("table_1",summary_table,"分布统计与完整样本数");.sb_plot("figure_1",ggplot2::ggplot(long,ggplot2::aes(x=值,colour=分组))+ggplot2::geom_density()+ggplot2::facet_wrap(~变量,scales="free")+ggplot2::theme_bw(),"分组密度探索");.sb_plot("figure_2",relation,"变量关系 / 单变量 QQ 图");.sb_plot("figure_3",hist,"连续变量直方图");.sb_plot("figure_4",box,"分组批次箱线图")
.sb_text("outlier_summary",paste(capture.output(print(summary_table,row.names=FALSE)),collapse="\n"),"缺失与极端分布")
.sb_text("figure_5","各图按确认的变量、分组与批次展示；直方图只展示有限值，统计表单独报告缺失。首对变量用于关系图。","图形题注")
'''

_BODIES["assumption_diagnostics"] = r'''
out<-.sb_input("outcome");group<-.sb_input("group");covs<-as.character(.sb_input("covariates",character(0)));dat<-.sb_complete(.sb_cols(c(out,group,covs)),5L);.sb_numeric(dat,out)
if(!is.null(group)) dat[[group]]<-factor(dat[[group]]);for(nm in covs) if(is.character(dat[[nm]])) dat[[nm]]<-factor(dat[[nm]])
form<-if(length(c(group,covs))) .sb_formula(out,c(group,covs)) else stats::as.formula(paste0("`",out,"` ~ 1"))
fit<-lm(form,data=dat);.sb_check_fit(fit);residual<-resid(fit)
shapiro<-if(nrow(dat)<=5000L&&sd(residual)>0) shapiro.test(residual)$p.value else NA_real_
bp<-if(length(c(group,covs))) lmtest::bptest(fit)$p.value else NA_real_;cook<-cooks.distance(fit)
summary_table<-data.frame(诊断=c("完整样本数","残差自由度","残差 Shapiro P","Breusch–Pagan P","Cook 距离 >4/n 数","设计矩阵条件数"),结果=c(nrow(dat),df.residual(fit),shapiro,bp,sum(cook>4/nrow(dat)),kappa(model.matrix(fit))))
fig<-ggplot2::ggplot(data.frame(拟合值=fitted(fit),残差=residual),ggplot2::aes(x=拟合值,y=残差))+ggplot2::geom_point()+ggplot2::geom_hline(yintercept=0,linetype=2)+ggplot2::theme_bw()
.sb_table("diagnostic_output",summary_table,"实际线性模型诊断");.sb_plot("diagnostic_output_2",fig,"残差–拟合值")
.sb_text("analysis_recommendation","诊断发现异常时选择适合研究设计的稳健推断或模型形式；不自动删除高影响点，不将不拒绝正态检验等同假设已证实。","诊断建议")
.gb_note("诊断对应同一完整病例线性模型；数值角色保持原尺度；秩不足直接停止。")
.sb_text("diagnostic_output_3",paste(capture.output(print(summary_table,row.names=FALSE)),collapse="\n"),"模型诊断说明")
'''

_BODIES["multiple_testing_endpoints"] = r'''
vars<-as.character(.sb_input("outcome"));group<-.sb_input("group");null<-as.numeric(.sb_input("null_value",0));dat<-.sb_cols(c(vars,group));.sb_numeric(dat,vars)
if(!is.null(group)) dat[[group]]<-.sb_group(dat[[group]],.sb_input("reference_group"))
rows<-lapply(vars,function(v){dd<-dat[,c(v,group),drop=FALSE];dd<-dd[complete.cases(dd),,drop=FALSE];if(nrow(dd)<3L) stop("终点完整样本不足：",v)
 if(is.null(group)) {tt<-t.test(dd[[v]],mu=null);estimate<-mean(dd[[v]])-null;method<-paste0("单总体相对 ",null)} else {lv<-levels(dd[[group]]);tt<-t.test(dd[[v]][dd[[group]]==lv[2]],dd[[v]][dd[[group]]==lv[1]]);estimate<-mean(dd[[v]][dd[[group]]==lv[2]])-mean(dd[[v]][dd[[group]]==lv[1]]);method<-paste(lv[2],"减",lv[1])}
 data.frame(终点=v,方向=method,n=nrow(dd),估计=estimate,CI下限=tt$conf.int[1],CI上限=tt$conf.int[2],原始P=tt$p.value) })
summary_table<-do.call(rbind,rows);summary_table$HolmP<-p.adjust(summary_table$原始P,"holm");summary_table$BHP<-p.adjust(summary_table$原始P,"BH")
.sb_table("table_1",summary_table,"同一预设终点族的 Holm / BH 校正")
if(!is.null(group)) .sb_plot("figure_1",ggplot2::ggplot(summary_table,ggplot2::aes(x=估计,y=终点))+ggplot2::geom_pointrange(ggplot2::aes(xmin=CI下限,xmax=CI上限))+ggplot2::theme_bw(),"原尺度终点差异（未校正区间）")
.gb_note("连续终点使用单总体 t / 独立两组 Welch；Holm 控制家族错误率、BH 控制 FDR；报告的区间为各终点未校正区间，未冒称同时置信区间。")
.sb_text("manuscript_results_2",paste(capture.output(print(summary_table,row.names=FALSE)),collapse="\n"),"多终点结果")
.sb_text("diagnostic_output","校正族为本轮明确选择的全部终点；每终点分别报告完整病例数量。","多重检验范围")
'''

_BODIES["paired_matched_tests"] = r'''
id<-.sb_input("pair_id");out<-.sb_input("outcome");time<-.sb_input("time");strata<-.sb_input("strata");dat<-.sb_complete(.sb_cols(c(id,out,time,strata)),6L);.sb_numeric(dat,out)
if(anyDuplicated(dat[,c(id,time)])) stop("配对对象×时间/条件存在重复")
lv<-if(is.factor(dat[[time]])) levels(droplevels(dat[[time]])) else sort(unique(dat[[time]]));if(length(lv)!=2L) stop("配对比较需要两个已确认条件/时间点")
groups<-if(is.null(strata)) list(总体=dat) else split(dat,dat[[strata]]);rows<-list();plotrows<-list()
for(gn in names(groups)) {d<-groups[[gn]];a<-d[d[[time]]==lv[1],c(id,out),drop=FALSE];b<-d[d[[time]]==lv[2],c(id,out),drop=FALSE];names(a)<-c("id","a");names(b)<-c("id","b");w<-merge(a,b,by="id");if(nrow(w)!=nrow(a)||nrow(w)!=nrow(b)) stop("需要完整一一配对")
 delta<-w$b-w$a;if(length(delta)<3L||sd(delta)==0) stop("配对差值方差或样本不足");tt<-t.test(delta);wt<-suppressWarnings(wilcox.test(delta,conf.int=TRUE,exact=FALSE))
 rows[[length(rows)+1L]]<-data.frame(分层=gn,方向=paste(lv[2],"减",lv[1]),配对数=nrow(w),平均差=mean(delta),CI下限=tt$conf.int[1],CI上限=tt$conf.int[2],配对tP=tt$p.value,WilcoxonP=wt$p.value,Cohen_dz=mean(delta)/sd(delta));plotrows[[length(plotrows)+1L]]<-data.frame(分层=gn,差值=delta) }
summary_table<-do.call(rbind,rows);.sb_table("primary_table",summary_table,"一一配对效应与区间")
.sb_plot("diagnostic_plot",ggplot2::ggplot(do.call(rbind,plotrows),ggplot2::aes(x=差值))+ggplot2::geom_histogram(bins=25)+ggplot2::facet_wrap(~分层)+ggplot2::theme_bw(),"配对差值分布")
.sb_text("manuscript_results","配对通过 ID 对齐，不依赖原行序；并列报告平均差、配对 t、Wilcoxon 与 dz，各检验估计目标不同。","配对比较结果")
'''

_BODIES["stratified_categorical_trend"] = r'''
out<-.sb_input("outcome");expo<-.sb_input("exposure");strata<-.sb_input("strata");score<-.sb_input("ordinal_score");weight<-.sb_input("weight")
dat<-.sb_complete(.sb_cols(c(out,expo,strata,score,weight)),8L);dat[[out]]<-.sb_group(dat[[out]]);dat[[expo]]<-droplevels(factor(dat[[expo]]));dat[[strata]]<-factor(dat[[strata]])
w<-if(is.null(weight)) rep(1,nrow(dat)) else dat[[weight]];if(any(!is.finite(w)|w<0|w!=floor(w))) stop("分层频数权重需非负整数；复杂抽样权重使用 survey")
dat$.freq<-w;tab<-xtabs(.freq~.,data=dat[,c(".freq",out,expo,strata),drop=FALSE]);if(nlevels(dat[[expo]])==2L) {mh<-mantelhaen.test(tab,correct=FALSE);summary_table<-data.frame(方法="CMH 共同 OR（频数）",估计=unname(mh$estimate),CI下限=mh$conf.int[1],CI上限=mh$conf.int[2],P值=mh$p.value)} else {mh<-mantelhaen.test(tab,correct=FALSE);summary_table<-data.frame(方法="CMH 总体关联",估计=NA_real_,CI下限=NA_real_,CI上限=NA_real_,P值=mh$p.value)}
if(!is.null(score)) {.sb_numeric(dat,score);sc<-vapply(levels(dat[[expo]]),function(l){z<-unique(dat[[score]][dat[[expo]]==l]);if(length(z)!=1L) stop("每个暴露等级需要唯一预设趋势分数");z},numeric(1));agg<-xtabs(.freq~.,data=dat[,c(".freq",out,expo),drop=FALSE]);tr<-prop.trend.test(agg[2,],colSums(agg),score=sc);summary_table<-rbind(summary_table,data.frame(方法="Cochran–Armitage 总体趋势（另行报告）",估计=NA_real_,CI下限=NA_real_,CI上限=NA_real_,P值=tr$p.value))}
plotdata<-as.data.frame(tab);names(plotdata)<-c("结局","暴露","分层","频数")
.sb_table("primary_table",summary_table,"分层关联与明确分数趋势检验")
.sb_plot("diagnostic_plot",ggplot2::ggplot(plotdata,ggplot2::aes(x=暴露,y=频数,fill=结局))+ggplot2::geom_col(position="fill")+ggplot2::facet_wrap(~分层)+ggplot2::theme_bw(),"各层结局比例")
.sb_text("manuscript_results","二元结局使用 CMH 分层关联；有明确趋势分数时另行给出总体趋势检验，未将未分层趋势冒称调整后趋势。OR 的阳性为结局 factor 第二水平，暴露第二水平相对第一水平，结果须按明确类别阅读。","分类分层方法")
'''

_BODIES["nonlinear_spline_gam"] = r'''
out<-.sb_input("outcome");expo<-.sb_input("exposure");covs<-as.character(.sb_input("covariates",character(0)));group<-.sb_input("group");time<-.sb_input("time")
dat<-.sb_complete(.sb_cols(c(out,expo,covs,group,time)),20L);.sb_numeric(dat,c(out,expo));for(nm in c(covs,group)) if(is.character(dat[[nm]])||(!is.null(group)&&nm==group)) dat[[nm]]<-factor(dat[[nm]])
if(!is.null(time)&&!is.numeric(dat[[time]])) stop("时间平滑项需数值时间")
k<-as.integer(.sb_input("basis_dimension",min(10L,length(unique(dat[[expo]]))-1L)));.step<-"gam-basis";if(k<3L) stop("平滑暴露至少四个不同值")
rhs<-c(paste0("s(`",expo,"`,k=",k,")"),sprintf("`%s`",c(covs,group)),if(!is.null(time)) paste0("s(`",time,"`)") else character(0));form<-as.formula(paste0("`",out,"` ~ ",paste(rhs,collapse=" + ")))
 .step<-"gam-fit"
fit<-mgcv::gam(form,data=dat,method="REML");if(!isTRUE(fit$converged)) stop("GAM 未收敛")
summary_table<-as.data.frame(summary(fit)$s.table);summary_table$平滑项<-rownames(summary_table);nd<-dat[rep(1,100),,drop=FALSE];nd[[expo]]<-seq(min(dat[[expo]]),max(dat[[expo]]),length.out=100);for(nm in c(covs,time)) if(is.numeric(dat[[nm]])) nd[[nm]]<-mean(dat[[nm]])
.step<-"gam-predict"
pr<-predict(fit,newdata=nd,type="response",se.fit=TRUE);pd<-data.frame(暴露=nd[[expo]],预测=pr$fit,下限=pr$fit-qnorm(.975)*pr$se.fit,上限=pr$fit+qnorm(.975)*pr$se.fit)
.sb_table("primary_table",summary_table,"实际平滑项 EDF 与检验")
.sb_plot("diagnostic_plot",ggplot2::ggplot(pd,ggplot2::aes(x=暴露,y=预测))+ggplot2::geom_ribbon(ggplot2::aes(ymin=下限,ymax=上限),alpha=.2)+ggplot2::geom_line()+ggplot2::theme_bw(),"条件平滑预测与近似置信带")
.sb_text("manuscript_results","使用 mgcv REML 高斯 GAM；连续暴露保留原尺度，时间若提供则作为独立平滑项；图的其他数值变量取均值、分类取首行水平，不冒称总体平均边际效应。","非线性模型范围")
'''

_BODIES["bayesian_robust_stats"] = r'''
out<-.sb_input("outcome");group<-.sb_input("group");covs<-as.character(.sb_input("covariates",character(0)));indicator<-.sb_input("outlier_indicator");method<-as.character(.sb_input("analysis_method","robust"))
dat<-.sb_complete(.sb_cols(c(out,group,covs,indicator)),10L);.sb_numeric(dat,out);dat[[group]]<-factor(dat[[group]]);for(nm in covs) if(is.character(dat[[nm]])) dat[[nm]]<-factor(dat[[nm]])
form<-.sb_formula(out,c(group,covs));ordinary<-lm(form,data=dat);.sb_check_fit(ordinary)
if(method=="robust") {
 fit<-MASS::rlm(form,data=dat,maxit=100);if(!isTRUE(fit$converged)) stop("Huber 回归未收敛")
 coefficients<-data.frame(变量=names(coef(fit)),OLS=unname(coef(ordinary)),Huber=unname(coef(fit)),Huber标准误=summary(fit)$coefficients[,2]);sensitivity<-coefficients
 if(!is.null(indicator)) {flag<-dat[[indicator]];if(!is.logical(flag)&&!all(flag%in%c(0,1))) stop("离群标识需逻辑或 0/1");trimfit<-lm(form,data=dat[!as.logical(flag),,drop=FALSE]);.sb_check_fit(trimfit);sensitivity$预先标记排除后OLS<-unname(coef(trimfit)[coefficients$变量])}
 .sb_table("table_2",data.frame(变量=names(coef(fit)),Huber估计=unname(coef(fit)),标准误=summary(fit)$coefficients[,2]),"Huber 模型估计与标准误");.sb_table("table_3",sensitivity,"OLS / Huber 与预设离群标记敏感性")
 .sb_table("table_4",data.frame(变量=rownames(confint(ordinary)),confint(ordinary),区间对象="OLS t 区间；Huber仅报告标准误",check.names=FALSE),"推断区间所属模型")
} else if(method=="bayesian") {
 prior<-.gb_json(.sb_input("prior_specification"));if(!is.list(prior)||any(!c("b0","B0","c0","d0")%in%names(prior))||prior$B0<=0||prior$c0<=0||prior$d0<=0) stop("先验需 JSON b0/B0/c0/d0，精度和逆 Gamma 参数为正")
 samples<-as.integer(.sb_input("samples",2000L));burn<-as.integer(.sb_input("burnin",1000L));seed<-as.integer(.sb_input("seed",1729L));if(samples<100L||burn<50L) stop("采样量不足")
 args<-list(formula=form,data=dat,burnin=burn,mcmc=samples,seed=seed,b0=prior$b0,B0=prior$B0,c0=prior$c0,d0=prior$d0,marginal.likelihood="Chib95",verbose=0)
 fit<-do.call(MCMCpack::MCMCregress,args);nullform<-if(length(covs)) .sb_formula(out,covs) else as.formula(paste0("`",out,"`~1"));args$formula<-nullform;nullfit<-do.call(MCMCpack::MCMCregress,args)
 draws<-as.matrix(fit);posterior<-data.frame(参数=colnames(draws),均值=colMeans(draws),CI下限=apply(draws,2,quantile,.025),CI上限=apply(draws,2,quantile,.975),ESS=as.numeric(coda::effectiveSize(fit)))
 bf<-exp(as.numeric(attr(fit,"logmarglike")-attr(nullfit,"logmarglike")))
 .sb_text("text_1",paste(capture.output(print(posterior,row.names=FALSE)),collapse="\n"),"MCMC 后验摘要")
 .sb_table("table_1",data.frame(比较="含组别 vs 仅协变量",Bayes因子=bf,log边际似然完整=as.numeric(attr(fit,"logmarglike")),log边际似然对照=as.numeric(attr(nullfit,"logmarglike"))),"相同适当先验的 Chib 边际似然比较")
 .sb_table("table_4",posterior,"实际后验区间与 ESS")
} else stop("未知 robust / bayesian 分支")
.sb_text("sensitivity_output",paste0("已选 ",method,"；所有模型使用同一完整病例。Bayes 因子仅在提供适当 b0/B0/c0/d0 先验时估计；未把稳健频率学模型冒称贝叶斯。"),"敏感性范围")
'''

_BODIES["multivariate_ordination"] = r'''
vars<-as.character(.sb_input("data_object"));id<-.sb_input("sample_id");group<-.sb_input("group");batch<-.sb_input("batch");dat<-.sb_complete(.sb_cols(c(vars,id,group,batch)),5L);x<-dat[,vars,drop=FALSE]
if(!all(vapply(x,is.numeric,logical(1)))) stop("当前明确支持标准化数值 PCA；分类或混合排序需选定 MCA/FAMD 设计")
.sb_numeric(x);if(any(vapply(x,sd,numeric(1))==0)) stop("PCA 不接受常数列")
fit<-prcomp(x,center=TRUE,scale.=TRUE);if(ncol(fit$x)<2L) stop("至少两个可估计成分")
coords<-data.frame(样本ID=if(is.null(id)) seq_len(nrow(dat)) else dat[[id]],fit$x,分组=if(is.null(group)) "总体" else as.character(dat[[group]]),批次=if(is.null(batch)) "总体" else as.character(dat[[batch]]),check.names=FALSE)
loads<-data.frame(变量=rownames(fit$rotation),fit$rotation,check.names=FALSE);contribution<-data.frame(变量=rownames(fit$rotation),PC1贡献=100*fit$rotation[,1]^2,PC2贡献=100*fit$rotation[,2]^2)
fig<-ggplot2::ggplot(coords,ggplot2::aes(x=PC1,y=PC2,colour=分组,shape=批次))+ggplot2::geom_point()+ggplot2::theme_bw();loadingfig<-ggplot2::ggplot(loads,ggplot2::aes(x=PC1,y=PC2,label=变量))+ggplot2::geom_point()+ggplot2::geom_text(vjust=-.5)+ggplot2::theme_bw()
.sb_plot("figure_1",fig,"标准化 PCA 样本坐标");.sb_table("table_1",loads,"PCA 载荷");.sb_table("table_2",coords,"完整样本降维坐标与元数据");.sb_plot("figure_2",loadingfig,"变量载荷排序");.sb_table("table_3",contribution,"变量对前两成分贡献")
.gb_note(paste0("标准化 PCA；解释方差比例 ",paste(signif(fit$sdev^2/sum(fit$sdev^2),4),collapse=", "),"。分组和批次仅作为已确认元数据展示，不自动校正批次或宣称聚类形成。"));.sb_text("manuscript_results_2","变量贡献为标准化载荷平方占比；无监督降维的图形距离不能替代组间推断。","降维解释")
'''

_BODIES["irt_models"] = r'''
vars<-as.character(.sb_input("item"));subject<-.sb_input("subject_id");group<-.sb_input("group");ability<-.sb_input("total_score_ability_estimate");dat<-.sb_complete(.sb_cols(c(vars,subject,group,ability)),50L);x<-dat[,vars,drop=FALSE];.sb_numeric(x)
if(any(vapply(x,function(z) any(z!=floor(z)),logical(1)))) stop("IRT 响应应为已确认整数类别")
method<-as.character(.sb_input("irt_model",if(all(vapply(x,function(z)length(unique(z))==2L,logical(1)))) "2PL" else "graded"));if(!method%in%c("Rasch","2PL","graded")) stop("IRT 支持 Rasch / 2PL / graded")
fit<-mirt::mirt(x,1,itemtype=method,verbose=FALSE,technical=list(NCYCLES=1000))
if(!mirt::extract.mirt(fit,"converged")) stop("IRT 未收敛")
parameters<-as.data.frame(mirt::coef(fit,IRTpars=TRUE,simplify=TRUE)$items);parameters$条目<-rownames(parameters)
.theta<-matrix(seq(-4,4,length.out=101),ncol=1);info<-data.frame(theta=.theta[,1],信息=mirt::testinfo(fit,.theta));score<-mirt::fscores(fit,method="EAP");parameters$样本数<-nrow(x)
.sb_table("table_1",parameters,"实际 IRT 条目参数")
.sb_plot("figure_1",ggplot2::ggplot(info,ggplot2::aes(x=theta,y=信息))+ggplot2::geom_line()+ggplot2::theme_bw(),"测验信息函数")
if(!is.null(group)) {groups<-factor(dat[[group]]);if(nlevels(groups)!=2L) stop("当前 DIF 对比需两个组")
 mg<-mirt::multipleGroup(x,1,group=groups,itemtype=method,invariance=c(vars,"free_means","free_var"),verbose=FALSE,technical=list(NCYCLES=1000));whichpar<-if(method=="graded") c("a1",paste0("d",seq_len(max(vapply(x,function(z)length(unique(z)),integer(1)))-1L))) else c("a1","d")
 dif<-as.data.frame(mirt::DIF(mg,which.par=whichpar,scheme="drop",p.adjust="holm",verbose=FALSE));dif$条目<-rownames(dif);dif$诊断<-paste0("条目DIF：",dif$条目);pcol<-intersect(c("adj_p","p"),names(dif));if(!length(pcol))stop("DIF 未返回真实检验P值");dif$状态<-ifelse(is.na(dif[[pcol[1]]]),"warning",ifelse(dif[[pcol[1]]]<.05,"warning","passed"))
 .sb_table("diagnostic_output",dif,"多组逐条目 DIF 联合检验");.sb_table("table_3",data.frame(条目=dif$条目,HolmP=dif[[pcol[1]]],DIF标记=dif$状态=="warning",检验说明="逐条目释放参数，其他条目锚定；Holm 校正"),"实际 DIF 筛查结果") }
.gb_note(paste0("一维 ",method," 测验模型；theta 网格为 [-4,4]；EAP 能力均值=",mean(score[,1]),if(is.null(ability)) "；未提供外部能力分数。" else paste0("；EAP 与外部得分相关=",cor(score[,1],dat[[ability]]))))
'''

_BODIES["latent_class"] = r'''
vars<-as.character(.sb_input("categorical_variables"));id<-.sb_input("sample_id");covs<-as.character(.sb_input("covariates",character(0)));dat<-.sb_complete(.sb_cols(c(vars,id,covs)),30L)
for(v in vars){z<-factor(dat[[v]]);if(nlevels(z)<2L||nlevels(z)>20L) stop("LCA 条目需 2–20 个已确认类别");dat[[v]]<-as.integer(z)}
for(v in covs) if(is.character(dat[[v]])) dat[[v]]<-factor(dat[[v]])
maxclass<-as.integer(.sb_input("candidate_class_count",3L));if(length(maxclass)!=1L||maxclass<1L||maxclass>8L) stop("候选最大类别数 1–8")
form<-as.formula(paste0("cbind(",paste(sprintf("`%s`",vars),collapse=","),") ~ ",if(length(covs)) paste(sprintf("`%s`",covs),collapse="+") else "1"));set.seed(as.integer(.sb_input("seed",1729L)))
fits<-lapply(seq_len(maxclass),function(k)poLCA::poLCA(form,dat,nclass=k,nrep=5,maxiter=2000,verbose=FALSE,calc.se=TRUE))
comparison<-data.frame(类别数=seq_len(maxclass),AIC=sapply(fits,`[[`,"aic"),BIC=sapply(fits,`[[`,"bic"),logLik=sapply(fits,`[[`,"llik"));chosen<-which.min(comparison$BIC);fit<-fits[[chosen]]
profiles<-do.call(rbind,lapply(names(fit$probs),function(v){p<-fit$probs[[v]];tt<-as.data.frame(as.table(p));names(tt)<-c("类别","响应类别","条件概率");tt$条目<-v;tt}))
posterior<-data.frame(样本ID=if(is.null(id)) seq_len(nrow(dat)) else dat[[id]],分配类别=fit$predclass,fit$posterior,check.names=FALSE)
.sb_table("table_1",comparison,"相同样本候选 LCA 的 AIC / BIC");.sb_table("table_2",profiles,"条目类别条件概率画像");.sb_table("table_3",posterior,"逐样本后验类别概率")
.sb_plot("figure_1",ggplot2::ggplot(profiles,ggplot2::aes(x=响应类别,y=条件概率,colour=类别,group=类别))+ggplot2::geom_line()+ggplot2::facet_wrap(~条目)+ggplot2::theme_bw(),"潜类别响应概率")
.sb_plot("figure_2",ggplot2::ggplot(data.frame(类别=factor(seq_along(fit$P)),比例=fit$P),ggplot2::aes(x=类别,y=比例))+ggplot2::geom_col()+ggplot2::theme_bw(),"估计潜类别比例")
.sb_text("manuscript_results",paste0("BIC 选择 ",chosen," 类，实际条件概率与后验概率均报告；类别不自动命名为临床或社会类型。多起点 nrep=5。"),"潜类别模型解释")
'''

_BODIES["psych_network"] = r'''
vars<-as.character(.sb_input("categorical_variables"));group<-.sb_input("group");time<-.sb_input("time_point_variable");dat<-.sb_complete(.sb_cols(c(vars,group,time)),20L);.sb_numeric(dat,vars)
if(length(vars)<3L) stop("至少三个网络节点")
groups<-if(is.null(group)&&is.null(time)) list(总体=dat) else split(dat,do.call(interaction,c(dat[,c(group,time),drop=FALSE],list(drop=TRUE))));rows<-list();communities<-list();stability<-list()
for(gn in names(groups)){d<-groups[[gn]][,vars,drop=FALSE];if(nrow(d)<20L) stop("各网络分组至少 20 个完整样本");pc<-qgraph::EBICglasso(cor(d),n=nrow(d),gamma=.5);diag(pc)<-0
 graph<-igraph::graph_from_adjacency_matrix(pc,mode="undirected",weighted=TRUE,diag=FALSE);if(igraph::ecount(graph)==0L) stop("EBICglasso 未识别非零边，不构造假网络")
 if(!exists("first_graph")) first_graph<-graph
 absgraph<-graph;igraph::E(absgraph)$weight<-abs(igraph::E(graph)$weight)
 rows[[length(rows)+1L]]<-data.frame(分组=gn,节点=vars,强度=igraph::strength(absgraph),期望影响=rowSums(pc))
 cc<-igraph::cluster_louvain(absgraph);communities[[length(communities)+1L]]<-data.frame(分组=gn,节点=vars,社区=igraph::membership(cc))
 B<-as.integer(.sb_input("resamples",100L));set.seed(as.integer(.sb_input("seed",1729L)));strengths<-replicate(B,{ii<-sample.int(nrow(d),nrow(d),replace=TRUE);xx<-tryCatch(qgraph::EBICglasso(cor(d[ii,,drop=FALSE]),n=nrow(d),gamma=.5),error=function(e) matrix(NA_real_,length(vars),length(vars)));diag(xx)<-0;rowSums(abs(xx))})
 stability[[length(stability)+1L]]<-data.frame(分组=gn,节点=vars,强度CI下限=apply(strengths,1,quantile,.025,na.rm=TRUE),强度CI上限=apply(strengths,1,quantile,.975,na.rm=TRUE),B=B)
}
.sb_plot("figure_1",.gb_network_plot(first_graph),"首个分组 EBICglasso 网络")
.sb_table("table_1",do.call(rbind,rows),"各网络强度与期望影响");.sb_table("table_2",do.call(rbind,communities),"绝对边权 Louvain 社区");.sb_table("table_3",do.call(rbind,stability),"完整对象 Bootstrap 强度区间")
.sb_text("text_1","真实 EBICglasso 正则化部分相关网络；Bootstrap 强度区间不是 case-dropping CS 系数，不冒称网络稳定性已成立。分组分别拟合，未自动作组间显著性检验。","稳定性范围")
'''

_BODIES["social_network"] = r'''
source<-.sb_input("node_id");target<-.sb_input("edge_relation");attrs<-as.character(.sb_input("node_attributes",character(0)));weight<-.sb_input("weight");dat<-.sb_complete(.sb_cols(c(source,target,attrs,weight)),2L)
edges<-data.frame(from=as.character(dat[[source]]),to=as.character(dat[[target]]));if(!is.null(weight)){.sb_numeric(dat,weight);if(any(dat[[weight]]<=0)) stop("边权需为正");edges$weight<-dat[[weight]]}else edges$weight<-1
edgekeys<-paste(pmin(edges$from,edges$to),pmax(edges$from,edges$to),sep="\r");if(any(edges$from==edges$to)||anyDuplicated(edgekeys)) stop("当前无向简单网络不接收自环或重复边")
graph<-igraph::graph_from_data_frame(edges,directed=FALSE);nodes<-igraph::V(graph)$name
for(v in attrs){a<-vapply(nodes,function(n){z<-unique(as.character(dat[[v]][edges$from==n]));if(length(z)>1L) stop("节点属性不一致：",n);if(!length(z)) NA_character_ else z},character(1));igraph::vertex_attr(graph,v)<-a}
centrality<-data.frame(节点=nodes,度=igraph::degree(graph),强度=igraph::strength(graph),介数=igraph::betweenness(graph,weights=1/igraph::E(graph)$weight,normalized=TRUE));community<-igraph::cluster_louvain(graph)
.sb_plot("figure_1",.gb_network_plot(graph),"真实关系网络");.sb_table("table_1",centrality,"度 / 强度 / 介数")
.sb_table("table_3",data.frame(节点=nodes,分量=igraph::components(graph)$membership),"节点连通分量");.sb_table("table_4",data.frame(节点=nodes,社区=igraph::membership(community)),"Louvain 社区")
if(.sb_input("fit_ergm","no")=="yes"){if(any(edges$weight!=1)) stop("当前 ERGM 是二元网络，非单位边权需明确 valued ERGM 设计")
 net<-network::network(igraph::as_adjacency_matrix(graph,sparse=FALSE),directed=FALSE);for(v in attrs) network::set.vertex.attribute(net,v,igraph::vertex_attr(graph,v))
 terms<-as.character(.sb_input("ergm_formula"));if(!nzchar(terms)) stop("需明确 ERGM 右侧公式");form<-as.formula(paste("net ~",terms));fit<-ergm::ergm(form,control=ergm::control.ergm(seed=as.integer(.sb_input("seed",1729L))));.sb_table("table_2",data.frame(参数=rownames(summary(fit)$coefficients),summary(fit)$coefficients,check.names=FALSE),"实际 ERGM 系数")}
.sb_text("manuscript_results","无向简单关系网络；边权用于强度与逆权重最短路径；节点属性按来源节点映射且拒绝冲突。ERGM 仅在明确开启和提供公式时拟合，不把中心性结果当 ERGM。","关系网络解释")
'''

_BODIES["text_social_science"] = r'''
text<-.sb_input("text");id<-.sb_input("document_id");group<-.sb_input("group");time<-.sb_input("time");dat<-.sb_cols(c(text,id,group,time));ids<-if(is.null(id)) paste0("D",seq_len(nrow(dat))) else as.character(dat[[id]]);if(anyDuplicated(ids)) stop("文档 ID 需唯一")
dfm<-.gb_text_matrix(dat[[text]],ids);freq<-sort(Matrix::colSums(dfm),decreasing=TRUE);frequencies<-data.frame(词项=names(freq),频数=as.numeric(freq));tfidf<-as.matrix(quanteda::dfm_tfidf(dfm));norm<-sqrt(rowSums(tfidf^2));if(any(norm==0)) stop("文档无有效词项");similarity<-tcrossprod(tfidf/norm)
clusters<-if(nrow(dat)>=4L) cutree(hclust(as.dist(1-similarity)),k=min(as.integer(.sb_input("clusters",2L)),nrow(dat)-1L)) else rep(1L,nrow(dat))
classification<-data.frame(文档ID=ids,无监督词汇聚类=clusters,分组=if(is.null(group)) "未指定" else as.character(dat[[group]]),时间=if(is.null(time)) "未指定" else as.character(dat[[time]]))
keywords<-do.call(rbind,lapply(unique(clusters),function(k){v<-sort(colMeans(tfidf[clusters==k,,drop=FALSE]),decreasing=TRUE);data.frame(词汇簇=k,关键词=names(head(v,10)),平均TFIDF=as.numeric(head(v,10))) }))
fig<-ggplot2::ggplot(head(frequencies,20),ggplot2::aes(x=reorder(词项,频数),y=频数))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw()
coords<-if(nrow(dat)>=3L) cmdscale(as.dist(1-similarity),k=2,add=TRUE)$points else cbind(seq_len(nrow(dat)),0);points<-data.frame(文档ID=ids,维度1=coords[,1],维度2=coords[,2],词汇簇=factor(clusters))
.sb_table("table_1",frequencies,"实际词频");.sb_plot("figure_1",fig,"高频词");.sb_table("table_2",data.frame(文档ID=ids,similarity,check.names=FALSE),"TF–IDF 余弦相似度");.sb_table("table_3",classification,"无监督词汇聚类与元数据");.sb_table("table_4",keywords,"各词汇簇关键词")
.sb_plot("figure_2",ggplot2::ggplot(points,ggplot2::aes(x=维度1,y=维度2,colour=词汇簇))+ggplot2::geom_point()+ggplot2::theme_bw(),"余弦距离文档排序")
.sb_text("manuscript_results","quanteda 原生分词、TF–IDF、余弦距离与层次词汇聚类；未训练监督分类器，词汇簇不冒称主题模型或人工语义编码。中文语义分词词典和复杂主题建模需明确另选方法。","文本量化范围")
'''

_BODIES["mixed_methods_open_text"] = r'''
text<-.sb_input("text");coding<-.sb_input("manual_coding");rater<-.sb_input("rater_id");id<-.sb_input("respondent_id");group<-.sb_input("group");dat<-.sb_cols(c(text,coding,rater,id,group));ids<-paste0("R",seq_len(nrow(dat)));dfm<-.gb_text_matrix(dat[[text]],ids)
freq<-sort(Matrix::colSums(dfm),decreasing=TRUE);summary_table<-data.frame(关键词=names(freq),频数=as.numeric(freq));.sb_table("table_1",summary_table,"开放题关键词频数")
if(!is.null(coding)&&!is.null(rater)&&!is.null(id)){
 d<-dat[complete.cases(dat[,c(coding,rater,id),drop=FALSE]),,drop=FALSE];if(anyDuplicated(d[,c(id,rater)])) stop("重复受访者×编码者记录")
 raters<-sort(unique(as.character(d[[rater]])));if(length(raters)<2L) stop("至少两个编码者")
 agreement<-lapply(combn(raters,2L,simplify=FALSE),function(pr){a<-d[d[[rater]]==pr[1],c(id,coding),drop=FALSE];b<-d[d[[rater]]==pr[2],c(id,coding),drop=FALSE];names(a)<-c("id","a");names(b)<-c("id","b");w<-merge(a,b,by="id");if(nrow(w)<3L) stop("编码对齐对象不足");kp<-irr::kappa2(w[,c("a","b")]);data.frame(编码者对=paste(pr,collapse=" vs "),完整对象数=nrow(w),CohenKappa=kp$value,观察一致率=mean(w$a==w$b))})
 .sb_table("table_2",do.call(rbind,agreement),"真实对象对齐的人工编码一致性") }
.sb_text("text_1",paste0("开放题记录 ",nrow(dat),"，有效词项 ",ncol(dfm),"；关键词只支持文本量化，不替代人工主题解释。"),"开放题摘要")
.gb_note("关键词与人工编码分别报告；仅同时提供对象、评分者、编码时计算一致性；不把词频冒称主题模型。")
'''

_BODIES["measurement_invariance"] = r'''
vars<-as.character(.sb_input("item_variables"));group<-.sb_input("group");covs<-as.character(.sb_input("covariates",character(0)));model<-paste(.sb_input("latent_dimension_specification"),collapse="\n");dat<-.sb_complete(.sb_cols(c(vars,group,covs)),40L);dat[[group]]<-factor(dat[[group]]);if(nlevels(dat[[group]])<2L) stop("至少两个组")
ordered<-vars[vapply(dat[,vars,drop=FALSE],is.ordered,logical(1))];if(!length(ordered)) .sb_numeric(dat,vars)
if(length(covs)){pt<-lavaan::lavaanify(model);latents<-unique(pt$lhs[pt$op=="=~"]);model<-paste(model,paste(sprintf("%s",latents),"~",paste(sprintf("%s",covs),collapse="+"),collapse="\n"),sep="\n")}
constraints<-if(length(ordered)) list(character(0),"loadings",c("loadings","thresholds"),c("loadings","thresholds","residuals")) else list(character(0),"loadings",c("loadings","intercepts"),c("loadings","intercepts","residuals"))
fits<-lapply(constraints,function(eq){args<-list(model=model,data=dat,group=group,std.lv=TRUE,group.equal=eq);if(length(ordered)) args$ordered<-ordered;do.call(lavaan::cfa,args)})
if(any(!vapply(fits,function(f) lavaan::lavInspect(f,"converged"),logical(1)))) stop("等值性候选模型未全部收敛")
comparison<-do.call(rbind,lapply(seq_along(fits),function(i){fm<-lavaan::fitMeasures(fits[[i]],c("cfi","tli","rmsea","srmr","chisq","df"));data.frame(模型=c("配置","载荷","强等值","严格等值")[i],as.list(stats::setNames(as.numeric(fm),names(fm))),check.names=FALSE)}));changes<-data.frame(相邻比较=paste(comparison$模型[-1],"vs",head(comparison$模型,-1)),deltaCFI=diff(comparison$cfi),deltaRMSEA=diff(comparison$rmsea),deltaSRMR=diff(comparison$srmr))
.sb_table("table_1",comparison,"实际多组等值性拟合");.sb_table("table_2",changes,"相邻模型拟合变化")
.gb_note("逐步拟合配置/载荷/强/严格等值模型；有序条目用阈值约束，连续条目用截距约束。拟合变化报告，不自动把经验阈值当等值性证实。")
.sb_text("manuscript_results_2",model,"实际测量和协变量语法")
.sb_text("diagnostic_output",paste0("收敛模型 ",length(fits),"；各组样本：",paste(table(dat[[group]]),collapse=", ")),"多组收敛覆盖")
'''

_BODIES["latent_growth_sem"] = r'''
id<-.sb_input("subject_id");time<-.sb_input("time");vars<-as.character(.sb_input("repeated_measure_variables"));expo<-.sb_input("exposure");group<-.sb_input("group");panel<-.gb_panel(id,time,vars,c(expo,group));wide<-panel$wide;times<-panel$times-min(panel$times)
syntax<-character(0)
for(j in seq_along(vars)){obs<-paste0("v",j,"t",seq_along(times));syntax<-c(syntax,paste0("i",j," =~ ",paste(paste0("1*",obs),collapse=" + ")),paste0("s",j," =~ ",paste(paste0(times,"*",obs),collapse=" + ")))
 if(!is.null(expo)) syntax<-c(syntax,paste0("i",j," + s",j," ~ `",expo,"`"))}
args<-list(model=paste(syntax,collapse="\n"),data=wide);if(!is.null(group)) args$group<-group;fit<-do.call(lavaan::growth,args);pe<-.gb_sem_table(fit)
summary_table<-pe[pe$op%in%c("~","~1","~~"),,drop=FALSE];.sb_table("table_1",summary_table,"实际截距、增长斜率与预测路径")
long<-do.call(rbind,lapply(vars,function(v)data.frame(时间=panel$long[[time]],测量=v,值=panel$long[[v]],对象=panel$long[[id]])))
.sb_plot("figure_1",ggplot2::ggplot(long,ggplot2::aes(x=时间,y=值,group=对象))+ggplot2::geom_line(alpha=.15)+ggplot2::facet_wrap(~测量)+ggplot2::theme_bw(),"实际重复测量轨迹")
.gb_note("lavaan 潜增长模型；增长载荷为实际时间相对首访的数值，不任意用等间隔序号；平行测量各有截距和斜率。对象层预测变量必须时间内不变。")
.sb_text("manuscript_results_2",paste(syntax,collapse="\n"),"实际增长模型语法");.sb_text("diagnostic_output",paste0("完整对象 n=",nrow(wide),"，时间点=",length(times)),"纵向覆盖")
'''

_BODIES["multilevel_behavior_model"] = r'''
out<-.sb_input("outcome");id<-.sb_input("observation_unit_id");cluster<-.sb_input("cluster_id");within<-as.character(.sb_input("exposure",character(0)));between<-as.character(.sb_input("exposure_2",character(0)));dat<-.sb_complete(.sb_cols(c(out,id,cluster,within,between)),20L)
if(length(unique(dat[[cluster]]))<3L) stop("至少三个聚类")
for(v in between) if(any(vapply(split(dat[[v]],dat[[cluster]]),function(z)length(unique(z))!=1L,logical(1)))) stop("层间预测变量在聚类内不恒定：",v)
for(v in c(within,between)) if(is.character(dat[[v]])) dat[[v]]<-factor(dat[[v]])
rhs<-sprintf("`%s`",c(within,between));if(length(within)&&length(between)) rhs<-c(rhs,as.vector(outer(sprintf("`%s`",within),sprintf("`%s`",between),paste,sep=":")))
random<-paste0("(1|`",cluster,"`)");if(anyDuplicated(dat[,c(cluster,id)])) random<-paste0("(1|`",cluster,"`/`",id,"`)")
form<-as.formula(paste0("`",out,"` ~ ",paste(c(if(length(rhs)) rhs else "1",random),collapse=" + ")))
binary<-is.factor(dat[[out]])||is.character(dat[[out]]);if(binary) {dat[[out]]<-.sb_group(dat[[out]]);fit<-lme4::glmer(form,data=dat,family=binomial())}else{.sb_numeric(dat,out);fit<-lme4::lmer(form,data=dat)}
if(length(fit@optinfo$conv$lme4$messages)) .diag_status[["收敛检查"]]<-"warning" else .diag_status[["收敛检查"]]<-"passed"
cf<-summary(fit)$coefficients;summary_table<-data.frame(变量=rownames(cf),cf,check.names=FALSE);vc<-as.data.frame(lme4::VarCorr(fit));variance<-sum(vc$vcov[vc$grp==cluster]);denom<-if(binary)sum(vc$vcov)+pi^2/3 else sum(vc$vcov);icc<-variance/denom
.sb_table("table_1",summary_table,"实际多层及跨层交互系数");.sb_table("table_2",data.frame(聚类ICC=icc,尺度=if(binary) "Logistic 潜在尺度" else "Gaussian 方差尺度",奇异拟合=lme4::isSingular(fit)),"明确尺度的 ICC")
.sb_plot("figure_1",.gb_coef_plot(data.frame(变量=rownames(cf),效应=cf[,1]),"效应","变量"),"实际跨层模型系数")
.gb_note("按 cluster 建随机截距；重复观察单位存在时增加嵌套随机截距；层内/层间变量均被实际纳入，跨层交互逐对构建；不把方差分量冒称独立回归结果。")
.sb_text("diagnostic_output",paste0("聚类数=",length(unique(dat[[cluster]])),"；奇异拟合=",lme4::isSingular(fit)),"层次模型诊断")
'''

_BODIES["weighted_survey_scale"] = r'''
vars<-as.character(.sb_input("item_variables"));weight<-.sb_input("weight");strata<-.sb_input("strata");cluster<-.sb_input("cluster_id");group<-.sb_input("group");dat<-.sb_complete(.sb_cols(c(vars,weight,strata,cluster,group)),10L);.sb_numeric(dat,c(vars,weight));if(any(dat[[weight]]<=0)) stop("调查权重需为正")
args<-list(ids=if(is.null(cluster)) ~1 else as.formula(paste0("~`",cluster,"`")),weights=as.formula(paste0("~`",weight,"`")),data=dat,nest=TRUE);if(!is.null(strata)) args$strata<-as.formula(paste0("~`",strata,"`"));design<-do.call(survey::svydesign,args)
means<-survey::svymean(as.formula(paste0("~",paste(sprintf("`%s`",vars),collapse="+"))),design);ci<-confint(means);summary_table<-data.frame(条目=names(coef(means)),均值=unname(coef(means)),标准误=unname(survey::SE(means)),CI下限=ci[,1],CI上限=ci[,2])
.sb_table("table_1",summary_table,"实际设计权重下条目均值及区间")
if(!is.null(group)){by<-survey::svyby(as.formula(paste0("~",paste(sprintf("`%s`",vars),collapse="+"))),as.formula(paste0("~`",group,"`")),design,survey::svymean,vartype=c("se","ci"));.sb_table("table_2",as.data.frame(by),"实际分组设计均值")}
.sb_text("text_1",paste0("ids=",if(is.null(cluster)) "1" else cluster,"；strata=",if(is.null(strata)) "未指定" else strata,"；权重=",weight,"；设计自由度=",survey::degf(design)),"抽样设计说明")
.gb_note("survey svydesign 和 svymean；保留已确认 PSU/分层/权重。孤立 PSU 按包默认报错，不暗中更改调查设计。")
.sb_text("diagnostic_output",paste0("权重 min=",min(dat[[weight]]),"，max=",max(dat[[weight]]),"；完整样本 n=",nrow(dat)),"调查输入诊断")
'''

_CHOICE = r'''
id<-.sb_input("respondent_id");out<-.sb_input("outcome");attrs<-as.character(.sb_input("attributes_variable"));group<-.sb_input("group");task<-.sb_input("task_id");alt<-.sb_input("alternative")
dat<-.sb_complete(.sb_cols(c(id,out,attrs,group,task,alt)),12L);if(!is.numeric(dat[[out]])||any(!dat[[out]]%in%c(0,1))) stop("选择结局需明确 0/1")
setid<-if(is.null(task)) factor(dat[[id]]) else interaction(dat[[id]],dat[[task]],drop=TRUE);if(any(tapply(dat[[out]],setid,sum)!=1L)) stop("每个选择集恰好一个被选择备选项")
if(!is.null(alt)&&anyDuplicated(data.frame(set=setid,alt=dat[[alt]]))) stop("选择集内备选项重复；重复任务需 task_id")
for(v in c(attrs,alt,group)) if(is.character(dat[[v]])||v%in%c(alt,group)) dat[[v]]<-factor(dat[[v]])
dat$.choice_set<-setid;rhs<-sprintf("`%s`",c(attrs,alt));if(!is.null(group)) rhs<-c(rhs,paste(sprintf("`%s`",attrs),paste0("`",group,"`"),sep=":"))
form<-as.formula(paste0("`",out,"` ~ ",paste(c(rhs,"strata(.choice_set)",paste0("cluster(`",id,"`)")),collapse="+")))
strata<-survival::strata;cluster<-survival::cluster;coxph<-survival::coxph
fit<-survival::clogit(form,data=dat,method="efron",model=TRUE,x=TRUE);if(any(!is.finite(coef(fit)))) stop("选择模型系数不可估计")
ct<-summary(fit)$coefficients;coefficients<-data.frame(参数=rownames(ct),ct,check.names=FALSE);lp<-as.numeric(predict(fit,type="lp",reference="zero"));prob<-ave(lp,setid,FUN=function(z)exp(z-max(z))/sum(exp(z-max(z))))
predictions<-data.frame(受访者=dat[[id]],选择集=as.character(setid),实际选择=dat[[out]],预测概率=prob,备选项=if(is.null(alt)) seq_len(nrow(dat)) else as.character(dat[[alt]]))
'''
_BODIES["conjoint_choice"] = _CHOICE + r'''
terms<-attr(terms(fit),"term.labels");assign<-attr(model.matrix(fit),"assign");mm<-model.matrix(fit);beta<-coef(fit);importance<-lapply(attrs,function(v){term<-which(terms%in%c(v,paste0("`",v,"`")));ix<-which(assign%in%term);if(!length(ix)) stop("属性未进入效用模型");part<-mm[,ix,drop=FALSE]%*%beta[ix];data.frame(属性=v,设计内效用范围=diff(range(part))) });importance<-do.call(rbind,importance);importance$设计内相对重要性<-importance$设计内效用范围/sum(importance$设计内效用范围)
.sb_table("table_1",coefficients,"条件选择模型效用系数");.sb_table("table_2",importance,"观测设计范围内属性重要性")
.gb_note("重复偏好任务采用 respondent×task 选择集的条件 Logistic，受访者聚类稳健方差；每选择集必须恰选一项。属性重要性只对应本实验的属性水平/范围。")
.sb_text("manuscript_results_2",paste(capture.output(print(importance,row.names=FALSE)),collapse="\n"),"偏好重要性结果")
'''
_BODIES["discrete_choice_model"] = _CHOICE + r'''
marginals<-lapply(attrs,function(v){if(is.numeric(dat[[v]]) && v%in%names(coef(fit))) data.frame(属性=v,自身备选项平均概率导数=mean(coef(fit)[v]*prob*(1-prob))) else data.frame(属性=v,自身备选项平均概率导数=NA_real_)})
.sb_table("table_1",coefficients,"实际条件多项选择系数");.sb_table("table_2",do.call(rbind,marginals),"数值属性的自身备选项概率导数")
.sb_plot("figure_1",ggplot2::ggplot(predictions,ggplot2::aes(x=备选项,y=预测概率))+ggplot2::geom_boxplot()+ggplot2::theme_bw(),"选择集内归一化预测概率")
.gb_note("条件选择模型包含备选项常数、所选属性与明确组别交互；无 task_id 时一位受访者为一个选择集。概率导数是改变单个备选项属性的自身导数，分类属性不伪造数值导数。")
.sb_text("diagnostic_output",paste0("选择集数=",nlevels(setid),"；最大概率归一化误差=",max(abs(tapply(prob,setid,sum)-1))),"选择模型概率诊断")
'''

_BODIES["behavior_scale_development"] = r'''
vars<-as.character(.sb_input("item_variables"));external<-.sb_input("external");group<-.sb_input("group");reverse<-as.character(.sb_input("reverse_item_indicator",character(0)));dat<-.sb_complete(.sb_cols(c(vars,external,group)),40L);.sb_numeric(dat,vars);x<-.sb_reverse(dat[,vars,drop=FALSE],reverse)
if(length(vars)<4L) stop("至少四个条目")
set.seed(as.integer(.sb_input("seed",1729L)));parallel<-psych::fa.parallel(x,fa="fa",fm="pa",n.iter=100,plot=FALSE);nf<-as.integer(.sb_input("factors",parallel$nfact));if(nf<1||nf>=length(vars)) stop("因子数不可识别")
fit<-psych::fa(x,nfactors=nf,fm="pa",rotate=if(nf==1) "none" else "oblimin",scores="regression");alpha<-psych::alpha(x,check.keys=FALSE,warnings=FALSE)$total$raw_alpha;loads<-unclass(fit$loadings)
summary_table<-data.frame(条目=vars,loads,共同度=fit$communality,check.names=FALSE);summary_table$整体alpha<-alpha;summary_table$外部总分相关<-if(is.null(external)) NA_real_ else cor(rowSums(x),dat[[external]],use="complete.obs")
.sb_table("primary_table",summary_table,"实际项目、载荷、共同度与外部关联")
.sb_plot("diagnostic_plot",ggplot2::ggplot(data.frame(因子=seq_along(parallel$fa.values),观测=parallel$fa.values,模拟=parallel$fa.sim),ggplot2::aes(x=因子))+ggplot2::geom_line(ggplot2::aes(y=观测,colour="观测"))+ggplot2::geom_line(ggplot2::aes(y=模拟,colour="模拟"))+ggplot2::theme_bw(),"真实并行分析")
.sb_text("manuscript_results",paste0("开发阶段采用主轴 EFA 和 alpha / 外部效度关联；未在同一开发样本上冒称独立 CFA 验证。",if(is.null(group)) "" else paste0("已报告分组元数据；分组样本数=",paste(table(dat[[group]]),collapse=","))),"量表开发范围")
'''

_BODIES["behavior_riclpm_cross_lagged"] = r'''
id<-.sb_input("subject_id");time<-.sb_input("time");vars<-as.character(.sb_input("repeated_measure_variables"));covs<-as.character(.sb_input("covariates",character(0)));group<-.sb_input("group");if(length(vars)!=2L) stop("当前 RI-CLPM 支持两个纵向构念")
panel<-.gb_panel(id,time,vars,c(covs,group));wide<-panel$wide;T<-length(panel$times);syntax<-c(paste0("RIx =~ ",paste(paste0("1*v1t",1:T),collapse="+")),paste0("RIy =~ ",paste(paste0("1*v2t",1:T),collapse="+")),"RIx ~~ RIy")
for(t in 1:T) syntax<-c(syntax,paste0("wx",t," =~ 1*v1t",t),paste0("wy",t," =~ 1*v2t",t),paste0("v1t",t," ~~ 0*v1t",t),paste0("v2t",t," ~~ 0*v2t",t),paste0("RIx ~~ 0*wx",t," + 0*wy",t),paste0("RIy ~~ 0*wx",t," + 0*wy",t),paste0("wx",t," ~~ wy",t))
for(t in 2:T) syntax<-c(syntax,paste0("wx",t," ~ wx",t-1," + wy",t-1),paste0("wy",t," ~ wx",t-1," + wy",t-1))
if(length(covs)) syntax<-c(syntax,paste0("RIx + RIy ~ ",paste(sprintf("`%s`",covs),collapse="+")))
args<-list(model=paste(syntax,collapse="\n"),data=wide,meanstructure=TRUE,int.ov.free=TRUE,int.lv.free=FALSE,auto.fix.first=FALSE,auto.cov.lv.x=FALSE);if(!is.null(group))args$group<-group
fit<-do.call(lavaan::sem,args);pe<-.gb_sem_table(fit);paths<-pe[pe$op=="~",,drop=FALSE]
.sb_table("primary_table",paths,"真实随机截距分离后的层内交叉滞后路径")
.sb_plot("diagnostic_plot",.gb_coef_plot(paths),"RI-CLPM 标准化路径系数")
.sb_text("manuscript_results",paste0("实际 RI-CLPM 包含跨人随机截距、各波次层内构念、零观测残差、滞后与同期关联，不用普通 CLPM 冒充。时间点=",T,"。语法：\n",paste(syntax,collapse="\n")),"RI-CLPM 结果范围")
'''

_BODIES["behavior_apim_dyadic"] = r'''
dyad<-.sb_input("dyad_id");role<-.sb_input("member_role");expo<-.sb_input("exposure");out<-.sb_input("outcome");covs<-as.character(.sb_input("covariates",character(0)));time<-.sb_input("time");if(!is.null(time)) stop("纵向 APIM 需明确重复协方差模型；本固定实现为单次可区分二元组")
dat<-.sb_complete(.sb_cols(c(dyad,role,expo,out,covs)),20L);.sb_numeric(dat,c(expo,out,covs));roles<-sort(unique(as.character(dat[[role]])));if(length(roles)!=2L||anyDuplicated(dat[,c(dyad,role)])) stop("每个 dyad 恰需两种可区分成员角色且无重复")
a<-dat[dat[[role]]==roles[1],c(dyad,expo,out,covs),drop=FALSE];b<-dat[dat[[role]]==roles[2],c(dyad,expo,out,covs),drop=FALSE];names(a)<-c("id","xA","yA",if(length(covs)) paste0("cA",seq_along(covs)) else character(0));names(b)<-c("id","xB","yB",if(length(covs)) paste0("cB",seq_along(covs)) else character(0));wide<-merge(a,b,by="id");if(nrow(wide)!=nrow(a)||nrow(wide)!=nrow(b)) stop("所有 dyad 应有完整两位成员")
syntax<-c("yA ~ actorA*xA + partnerA*xB","yB ~ actorB*xB + partnerB*xA","yA ~~ yB","xA ~~ xB")
if(length(covs)){syntax<-c(syntax,paste0("yA ~ ",paste(if(length(covs)) paste0("cA",seq_along(covs)) else character(0),collapse="+")),paste0("yB ~ ",paste(if(length(covs)) paste0("cB",seq_along(covs)) else character(0),collapse="+")))}
fit<-lavaan::sem(paste(syntax,collapse="\n"),data=wide,meanstructure=TRUE);pe<-.gb_sem_table(fit);paths<-pe[pe$op=="~",,drop=FALSE];paths$接收角色<-ifelse(paths$lhs=="yA",roles[1],roles[2])
.sb_table("primary_table",paths,"可区分二元组行动者与伙伴路径")
.sb_plot("diagnostic_plot",.gb_coef_plot(paths),"实际 APIM 路径系数")
.sb_text("manuscript_results",paste0("APIM 保留两成员结果残差相关与预测变量相关，不把成员行当独立个体。完整 dyad n=",nrow(wide),"。\n",paste(syntax,collapse="\n")),"二元互依方法")
'''

_BODIES["behavior_multilevel_sem"] = r'''
cluster<-.sb_input("cluster_id");id<-.sb_input("observation_unit_id");vars<-as.character(.sb_input("measure_variable"));within<-.sb_input("within_level_predictor");between<-.sb_input("between_level_predictor");covs<-as.character(.sb_input("covariates",character(0)));dat<-.sb_complete(.sb_cols(c(cluster,id,vars,within,between,covs)),50L);.sb_numeric(dat,c(vars,within,between,covs))
if(length(vars)<3L||length(unique(dat[[cluster]]))<5L) stop("至少三个测量指标、五个聚类")
if(anyDuplicated(dat[,c(cluster,id)])) stop("当前两层 SEM 需要每个聚类内观察单位一行")
if(!is.null(between)&&any(vapply(split(dat[[between]],dat[[cluster]]),function(z)length(unique(z))!=1L,logical(1)))) stop("层间预测变量需聚类内恒定")
model<-paste0("level: 1\n fw =~ ",paste(sprintf("%s",vars),collapse="+"),if(length(c(within,covs))) paste0("\n fw ~ ",paste(sprintf("%s",c(within,covs)),collapse="+")) else "","\nlevel: 2\n fb =~ ",paste(sprintf("%s",vars),collapse="+"),if(!is.null(between)) paste0("\n fb ~ ",between) else "")
fit<-lavaan::sem(model,data=dat,cluster=cluster);pe<-.gb_sem_table(fit);summary_table<-pe[pe$op%in%c("=~","~"),,drop=FALSE]
.sb_table("primary_table",summary_table,"实际层内 / 层间测量与结构路径")
.sb_plot("diagnostic_plot",.gb_coef_plot(summary_table),"实际两层 SEM 路径系数")
.sb_text("manuscript_results",paste0("lavaan 真正 level:1 / level:2 模型；层间预测已检查组内恒定。\n",model),"多层 SEM 语法与层级")
'''

_BODIES["behavior_common_method_bias"] = r'''
vars<-as.character(.sb_input("item_variables"));construct<-.gb_json(.sb_input("construct_grouping"));marker<-.sb_input("marker_variable");source<-.sb_input("source_variable");dat<-.sb_complete(.sb_cols(c(vars,marker,source)),40L);.sb_numeric(dat,c(vars,marker))
if(length(vars)<4L) stop("至少四个题项")
one<-paste0("Common =~ ",paste(sprintf("%s",vars),collapse="+"));models<-list(单因子=one)
if(!is.null(construct)){if(!is.list(construct)||is.null(names(construct))) stop("构念分组需 JSON 构念 → 条目数组")
 used<-unlist(construct,use.names=FALSE);if(anyDuplicated(used)||!setequal(used,vars)) stop("构念映射需覆盖所有已选题项且不重叠")
 substantive<-paste(vapply(names(construct),function(n)paste0(n," =~ ",paste(sprintf("%s",construct[[n]]),collapse="+")),character(1)),collapse="\n");models$构念模型<-substantive
 method<-paste0("Method =~ ",paste(paste0("m*",vars),collapse="+"));orthogonal<-paste0("Method ~~ ",paste(paste0("0*",names(construct)),collapse=" + "))
 models$等载荷正交方法因子<-paste(substantive,method,orthogonal,sep="\n") }
if(!is.null(marker)&&!is.null(construct)) models$标记变量调整<-paste(models$构念模型,paste(sprintf("%s",vars),paste0("~ ",marker),collapse="\n"),sep="\n")
fits<-lapply(models,function(m)lavaan::cfa(m,data=dat,std.lv=TRUE));if(any(!vapply(fits,function(f)lavaan::lavInspect(f,"converged"),logical(1)))) stop("方法偏差候选模型未全部收敛")
summary_table<-do.call(rbind,lapply(names(fits),function(n)data.frame(模型=n,as.list(lavaan::fitMeasures(fits[[n]],c("cfi","rmsea","srmr","aic","bic"))),check.names=FALSE)))
.sb_table("primary_table",summary_table,"实际单因子 / 构念 / 明确识别方法因子模型")
pe<-lavaan::parameterEstimates(fits[[length(fits)]],standardized=TRUE);.sb_plot("diagnostic_plot",.gb_coef_plot(pe[pe$op=="=~",,drop=FALSE]),"实际测量 / 方法因子载荷")
.sb_text("manuscript_results",paste0("单因子拟合本身不能证明有无共同方法偏差；提供构念时比较等载荷正交方法因子识别模型；标记变量作为已确认观测控制纳入，不声称等同潜在 marker 设计。来源变量 ",if(is.null(source)) "未指定" else paste(table(dat[[source]]),collapse=", ")," 仅作元数据报告。"),"方法偏差诊断范围")
'''

_BODIES["behavior_response_style_nonresponse"] = r'''
vars<-as.character(.sb_input("item_variables"));id<-.sb_input("respondent_id");out<-.sb_input("outcome");weight<-.sb_input("weight");aux<-.sb_input("auxiliary_variable");dat<-.sb_cols(c(vars,id,out,weight,aux));.sb_numeric(dat,vars);x<-as.matrix(dat[,vars,drop=FALSE]);observed<-rowSums(!is.na(x));row_sd<-apply(x,1,sd,na.rm=TRUE);response_style<-data.frame(受访者=if(is.null(id)) seq_len(nrow(dat)) else dat[[id]],观测条目数=observed,缺失比例=1-observed/length(vars),条目内SD=row_sd,直线作答=observed>=2L & row_sd==0,个人均值=rowMeans(x,na.rm=TRUE))
if(any(!is.finite(response_style$个人均值))) response_style$个人均值[!is.finite(response_style$个人均值)]<-NA_real_
summary_table<-response_style;response_note<-""
if(!is.null(out)&&!is.null(aux)){status<-dat[[out]];if(!is.numeric(status)||any(!status%in%c(0,1))) stop("无应答评估的 outcome 必须为确认的 0/1 响应状态")
 fit<-if(is.null(weight)) glm(.sb_formula(out,aux),data=dat,family=binomial()) else {if(any(dat[[weight]]<=0,na.rm=TRUE))stop("响应权重需为正");design<-survey::svydesign(ids=~1,weights=as.formula(paste0("~`",weight,"`")),data=dat);survey::svyglm(.sb_formula(out,aux),design=design,family=quasibinomial())}
 response_style$响应状态<-status;summary_table<-response_style;.diag_status[["响应机制检查"]]<-if(isTRUE(fit$converged)) "passed" else "warning"
 response_note<-paste0("\n实际响应状态模型系数：\n",paste(capture.output(print(summary(fit)$coefficients)),collapse="\n")) }
.sb_table("primary_table",summary_table,"响应风格与真实响应状态")
.sb_plot("diagnostic_plot",ggplot2::ggplot(response_style,ggplot2::aes(x=缺失比例,y=条目内SD))+ggplot2::geom_point()+ggplot2::theme_bw(),"缺失与条目内变化")
.sb_text("manuscript_results",paste0("直线作答仅为可复核标记，不自动排除；只有提供真实响应状态与辅助变量才估计无应答关联，缺失题项比例不能冒充总体无应答偏差。",response_note),"调查质量评估范围")
'''

_BODIES["behavior_latent_transition"] = r'''
id<-.sb_input("subject_id");time<-.sb_input("time");vars<-as.character(.sb_input("categorical_variables"));covs<-as.character(.sb_input("covariates",character(0)));out<-.sb_input("outcome");weight<-.sb_input("weight");dat<-.sb_complete(.sb_cols(c(id,time,vars,covs,out,weight)),30L)
if(anyDuplicated(dat[,c(id,time)]))stop("同一对象和波次重复");if(length(unique(dat[[time]]))<2L)stop("至少两个测量波次")
for(v in vars) {z<-factor(dat[[v]]);if(nlevels(z)<2L||nlevels(z)>20L) stop("潜在转变测量需分类响应");dat[[v]]<-as.integer(z)-1L}
if(!is.null(out)) stop("远端结局需明确三步法/BCH 等估计设计，不将结局静默改为潜状态预测变量")
k<-as.integer(.sb_input("states",2L));if(k<2L||k>8L)stop("潜状态数 2–8")
args<-list(responsesFormula=as.formula(paste0(paste(sprintf("`%s`",vars),collapse="+")," ~ NULL")),data=dat,index=c(id,time),k=k,modBasic=1L,output=TRUE,maxit=1000,seed=as.integer(.sb_input("seed",1729L)),ntry=3L)
if(length(covs)) args$latentFormula<-as.formula(paste0("~ ",paste(sprintf("`%s`",covs),collapse="+")))
if(!is.null(weight)) {w<-vapply(split(dat[[weight]],dat[[id]]),function(z){u<-unique(z);if(length(u)!=1||u<=0) stop("权重需在对象内恒定且为正");u},numeric(1));args$weights<-unname(w)}
fit<-do.call(LMest::lmest,args)
initial<-as.data.frame(as.table(fit$piv));names(initial)[ncol(initial)]<-"概率";initial$类型<-"初始状态概率"
transitions<-as.data.frame(as.table(fit$Pi));names(transitions)[ncol(transitions)]<-"概率";transitions$类型<-"潜状态转变概率"
measurement<-as.data.frame(as.table(fit$Psi));names(measurement)[ncol(measurement)]<-"概率"
prob_tables<-list(initial,transitions);all_names<-unique(unlist(lapply(prob_tables,names)));probability_table<-do.call(rbind,lapply(prob_tables,function(d){for(n in setdiff(all_names,names(d)))d[[n]]<-NA;d[,all_names,drop=FALSE]}))
.sb_table("primary_table",probability_table,"LMest 初始状态与潜状态转变概率")
pi_array<-fit$Pi;nd<-length(dim(pi_array));pmat<-if(nd==3L) pi_array[,,2] else if(nd==4L) apply(pi_array,c(1,2),mean) else as.matrix(pi_array);if(any(!is.finite(pmat)))stop("潜转移矩阵不可估计")
graph<-igraph::graph_from_adjacency_matrix(pmat,mode="directed",weighted=TRUE,diag=TRUE);igraph::V(graph)$name<-paste0("状态",seq_len(k))
.sb_plot("diagnostic_plot",.gb_network_plot(graph),"真实潜状态转变网络")
.sb_text("manuscript_results",paste0("LMest 联合潜 Markov 测量/转变模型，时间齐性转变，状态数 ",k,"；不是逐波硬分类转移计数。logLik=",fit$lk,"，BIC=",fit$bic,"。远端结局设计不明时拒绝，不把结局改作预测变量。"),"潜在转变模型范围")
'''

_BODIES["item_equating"] = r'''
id<-.sb_input("subject_id");item<-.sb_input("item_id");response<-.sb_input("item");form<-.sb_input("test_form");anchor<-.sb_input("anchor_item_indicator");dat<-.sb_complete(.sb_cols(c(id,item,response,form,anchor)),40L)
if(anyDuplicated(dat[,c(id,item)]))stop("受试者×题目重复");if(!is.numeric(dat[[response]])||any(!dat[[response]]%in%c(0,1))) stop("当前锚题等值为二元 Rasch 共同校准")
if(is.null(anchor))stop("非等组题库链接需要明确共同锚题标记")
items<-sort(unique(as.character(dat[[item]])));subjects<-unique(as.character(dat[[id]]));forms<-vapply(subjects,function(s){z<-unique(as.character(dat[[form]][as.character(dat[[id]])==s]));if(length(z)!=1L)stop("受试者测验形式不唯一");z},character(1));if(length(unique(forms))!=2L)stop("当前支持两种测验形式")
anchors<-unique(as.character(dat[[item]][dat[[anchor]]%in%c(TRUE,1)]));if(length(anchors)<2L)stop("至少两个已确认共同锚题")
for(a in anchors) if(length(unique(dat[[form]][as.character(dat[[item]])==a]))!=2L)stop("锚题需覆盖两种形式")
x<-matrix(NA_real_,length(subjects),length(items),dimnames=list(subjects,items));x[cbind(match(as.character(dat[[id]]),subjects),match(as.character(dat[[item]]),items))]<-dat[[response]]
fit<-mirt::multipleGroup(as.data.frame(x),1,group=factor(forms),itemtype="Rasch",invariance=c(anchors,"free_means","free_var"),verbose=FALSE,technical=list(NCYCLES=1000));if(!mirt::extract.mirt(fit,"converged"))stop("锚题共同校准未收敛")
coeff<-mirt::coef(fit,IRTpars=TRUE,simplify=TRUE);parameters<-do.call(rbind,lapply(names(coeff),function(g){t<-as.data.frame(coeff[[g]]$items);t$题目<-rownames(t);t$形式<-g;t$共同锚题<-t$题目%in%anchors;t}))
grid<-matrix(seq(-4,4,length.out=161),ncol=1);namesforms<-levels(factor(forms));curves<-lapply(namesforms,function(g){admin<-unique(as.character(dat[[item]][dat[[form]]==g]));expected<-rowSums(sapply(admin,function(it)mirt::probtrace(mirt::extract.item(fit,match(it,items),group=g),grid)[,2]));data.frame(theta=grid[,1],期望原分=expected,形式=g)})
reference<-curves[[1]];mapping<-do.call(rbind,lapply(seq_along(curves),function(j){cv<-curves[[j]];scores<-0:length(unique(dat[[item]][dat[[form]]==namesforms[j]]));theta<-approx(cv$期望原分,cv$theta,xout=scores,rule=2)$y;data.frame(原形式=namesforms[j],原始分=scores,共同尺度theta=theta,参考形式期望分=approx(reference$theta,reference$期望原分,xout=theta,rule=2)$y)}))
.sb_table("table_1",mapping,"Rasch 共同尺度真分等值");.sb_table("table_2",parameters,"两形式共同校准题目参数")
.sb_table("table_3",do.call(rbind,curves),"各形式共同 theta 尺度测验特征曲线")
.gb_note("共同锚题 Rasch 多组校准；映射为模型期望真分等值，极端原分按有限 theta 网格截尾，不冒称等百分位等值。")
.sb_text("diagnostic_output",paste0("共同锚题：",paste(anchors,collapse=", "),"；每个受试者仅一形式；结构性未施测题保留 NA。"),"锚题设计与覆盖")
'''

_BODIES["bayesian_sem"] = r'''
vars<-as.character(.sb_input("categorical_variables"));model<-paste(.sb_input("latent_variables"),collapse="\n");path<-paste(.sb_input("path_specification"),collapse="\n");prior<-.gb_json(.sb_input("prior"));dat<-.sb_complete(.sb_cols(vars),40L);.sb_numeric(dat,vars)
if(!is.list(prior)||is.null(names(prior)))stop("贝叶斯 SEM 先验需 JSON 参数族 → blavaan 分布语法")
if(any(!names(prior)%in%names(blavaan::dpriors(target="stan"))))stop("不支持的 blavaan 先验参数族")
args<-list(model=paste(model,path,sep="\n"),data=dat,dp=do.call(blavaan::dpriors,prior),n.chains=2L,burnin=as.integer(.sb_input("burnin",500L)),sample=as.integer(.sb_input("samples",1000L)),seed=as.integer(.sb_input("seed",1729L)),target="stan",bcontrol=list(refresh=0))
if(args$burnin<50L||args$sample<100L)stop("至少 burnin=50、sample=100，实际收敛另行报告")
suppressPackageStartupMessages(library(blavaan))
fit<-blavaan::bsem(model=args$model,data=dat,dp=args$dp,n.chains=args$n.chains,burnin=args$burnin,sample=args$sample,seed=args$seed,target="stan",bcontrol=list(refresh=0));pe<-lavaan::parameterEstimates(fit,standardized=TRUE);if(is.null(pe$group))pe$group<-1L
rhat<-blavaan::blavInspect(fit,"rhat");ess<-blavaan::blavInspect(fit,"n_eff");diagnostics<-data.frame(诊断=paste0("参数：",names(rhat)),参数=names(rhat),Rhat=as.numeric(rhat),ESS=as.numeric(ess),状态=ifelse(is.na(rhat)|rhat>1.05|is.na(ess)|ess<100,"warning","passed"))
.sb_table("table_1",pe[pe$op%in%c("=~","~",":="),,drop=FALSE],"真正 blavaan 后验测量与结构路径")
.sb_table("diagnostic_output",diagnostics,"真实 Stan Rhat 与 ESS")
.sb_table("table_3",pe[,intersect(c("lhs","op","rhs","est","se","ci.lower","ci.upper"),names(pe)),drop=FALSE],"后验不确定性区间")
.sb_text("manuscript_results",paste0("真实 Bayesian SEM，先验来自确认输入；最大 Rhat=",max(rhat,na.rm=TRUE),"，不将短链工程检查冒称收敛接受。"),"贝叶斯 SEM 拟合")
.sb_text("text_2",paste0("连续观测指标；实际模型：\n",args$model,"\n先验：",paste(capture.output(str(prior)),collapse="\n")),"实际先验与模型范围")
.diag_status[["收敛检查"]]<-if(anyNA(rhat)||any(rhat>1.05)) "warning" else "passed"
'''

INPUTS = {'distribution_visual': {'continuous_variables': 'continuous_variables_v', 'group': 'group_v', 'batch': 'batch_v'}, 'bayesian_robust_stats': {'outcome': 'outcome_v', 'group': 'group_v', 'outlier_indicator': 'outlier_indicator_v', 'covariates': 'covariates_v', 'analysis_method': 'analysis_method_v', 'prior_specification': 'prior_specification_v', 'samples': 'samples_v', 'burnin': 'burnin_v', 'seed': 'seed_v'}, 'bayesian_sem': {'categorical_variables': 'categorical_variables_v', 'latent_variables': 'latent_variables_v', 'path_specification': 'path_specification_v', 'prior': 'prior_v', 'burnin': 'burnin_v', 'samples': 'samples_v', 'seed': 'seed_v'}, 'irt_models': {'item': 'item_v', 'subject_id': 'subject_id_v', 'group': 'group_v', 'total_score_ability_estimate': 'total_score_ability_estimate_v', 'irt_model': 'irt_model_v'}, 'latent_class': {'categorical_variables': 'categorical_variables_v', 'sample_id': 'sample_id_v', 'candidate_class_count': 'candidate_class_count_v', 'covariates': 'covariates_v', 'seed': 'seed_v'}, 'psych_network': {'categorical_variables': 'categorical_variables_v', 'group': 'group_v', 'time_point_variable': 'time_point_variable_v', 'resamples': 'resamples_v', 'seed': 'seed_v'}, 'social_network': {'node_id': 'node_id_v', 'edge_relation': 'edge_relation_v', 'node_attributes': 'node_attributes_v', 'weight': 'weight_v', 'fit_ergm': 'fit_ergm_v', 'ergm_formula': 'ergm_formula_v', 'seed': 'seed_v'}, 'text_social_science': {'text': 'text_v', 'document_id': 'document_id_v', 'group': 'group_v', 'time': 'time_v', 'clusters': 'clusters_v'}, 'multivariate_ordination': {'data_object': 'data_object_v', 'sample_id': 'sample_id_v', 'group': 'group_v', 'batch': 'batch_v'}, 'assumption_diagnostics': {'outcome': 'outcome_v', 'group': 'group_v', 'covariates': 'covariates_v'}, 'multiple_testing_endpoints': {'outcome': 'outcome_v', 'group': 'group_v', 'null_value': 'null_value_v', 'reference_group': 'reference_group_v'}, 'measurement_invariance': {'item_variables': 'item_variables_v', 'group': 'group_v', 'latent_dimension_specification': 'latent_dimension_specification_v', 'covariates': 'covariates_v'}, 'latent_growth_sem': {'subject_id': 'subject_id_v', 'time': 'time_v', 'repeated_measure_variables': 'repeated_measure_variables_v', 'exposure': 'exposure_v', 'group': 'group_v'}, 'multilevel_behavior_model': {'outcome': 'outcome_v', 'observation_unit_id': 'observation_unit_id_v', 'cluster_id': 'cluster_id_v', 'exposure': 'exposure_v', 'exposure_2': 'exposure_2_v'}, 'weighted_survey_scale': {'item_variables': 'item_variables_v', 'weight': 'weight_v', 'strata': 'strata_v', 'cluster_id': 'cluster_id_v', 'group': 'group_v'}, 'conjoint_choice': {'respondent_id': 'respondent_id_v', 'outcome': 'outcome_v', 'task_id': 'task_id_v', 'attributes_variable': 'attributes_variable_v', 'group': 'group_v'}, 'discrete_choice_model': {'respondent_id': 'respondent_id_v', 'outcome': 'outcome_v', 'alternative': 'alternative_v', 'attributes_variable': 'attributes_variable_v', 'group': 'group_v', 'task_id': 'task_id_v'}, 'item_equating': {'subject_id': 'subject_id_v', 'item_id': 'item_id_v', 'item': 'item_v', 'test_form': 'test_form_v', 'anchor_item_indicator': 'anchor_item_indicator_v'}, 'mixed_methods_open_text': {'text': 'text_v', 'manual_coding': 'manual_coding_v', 'rater_id': 'rater_id_v', 'respondent_id': 'respondent_id_v', 'group': 'group_v'}, 'paired_matched_tests': {'pair_id': 'pair_id_v', 'outcome': 'outcome_v', 'time': 'time_v', 'strata': 'strata_v'}, 'stratified_categorical_trend': {'outcome': 'outcome_v', 'exposure': 'exposure_v', 'strata': 'strata_v', 'ordinal_score': 'ordinal_score_v', 'weight': 'weight_v'}, 'nonlinear_spline_gam': {'outcome': 'outcome_v', 'exposure': 'exposure_v', 'covariates': 'covariates_v', 'group': 'group_v', 'time': 'time_v', 'basis_dimension': 'basis_dimension_v'}, 'behavior_scale_development': {'item_variables': 'item_variables_v', 'external': 'external_v', 'group': 'group_v', 'reverse_item_indicator': 'reverse_item_indicator_v', 'factors': 'factors_v', 'seed': 'seed_v'}, 'behavior_riclpm_cross_lagged': {'subject_id': 'subject_id_v', 'time': 'time_v', 'repeated_measure_variables': 'repeated_measure_variables_v', 'covariates': 'covariates_v', 'group': 'group_v'}, 'behavior_apim_dyadic': {'dyad_id': 'dyad_id_v', 'member_role': 'member_role_v', 'exposure': 'exposure_v', 'outcome': 'outcome_v', 'covariates': 'covariates_v', 'time': 'time_v'}, 'behavior_multilevel_sem': {'cluster_id': 'cluster_id_v', 'observation_unit_id': 'observation_unit_id_v', 'measure_variable': 'measure_variable_v', 'within_level_predictor': 'within_level_predictor_v', 'between_level_predictor': 'between_level_predictor_v', 'covariates': 'covariates_v'}, 'behavior_common_method_bias': {'item_variables': 'item_variables_v', 'construct_grouping': 'construct_grouping_v', 'marker_variable': 'marker_variable_v', 'source_variable': 'source_variable_v'}, 'behavior_latent_transition': {'subject_id': 'subject_id_v', 'time': 'time_v', 'categorical_variables': 'categorical_variables_v', 'covariates': 'covariates_v', 'outcome': 'outcome_v', 'weight': 'weight_v', 'states': 'states_v', 'seed': 'seed_v'}, 'behavior_response_style_nonresponse': {'item_variables': 'item_variables_v', 'respondent_id': 'respondent_id_v', 'outcome': 'outcome_v', 'weight': 'weight_v', 'auxiliary_variable': 'auxiliary_variable_v'}}

ROLE_MAPS = {'distribution_visual': {'figure_1': 'figure_1', 'figure_2': 'figure_2', 'outlier_summary': 'outlier_summary', 'table_1': 'table_1', 'figure_3': 'figure_3', 'figure_4': 'figure_4', 'figure_5': 'figure_5'}, 'bayesian_robust_stats': {'text_1': 'text_1', 'table_1': 'table_1', 'table_2': 'table_2', 'table_3': 'table_3', 'table_4': 'table_4', 'sensitivity_output': 'sensitivity_output'}, 'bayesian_sem': {'table_1': 'table_1', 'diagnostic_output': 'diagnostic_output', 'manuscript_results': 'manuscript_results', 'table_3': 'table_3', 'text_2': 'text_2'}, 'irt_models': {'table_1': 'table_1', 'figure_1': 'figure_1', 'diagnostic_output': 'diagnostic_output', 'table_3': 'table_3', 'manuscript_results': 'manuscript_results'}, 'latent_class': {'table_1': 'table_1', 'table_2': 'table_2', 'figure_1': 'figure_1', 'table_3': 'table_3', 'figure_2': 'figure_2', 'manuscript_results': 'manuscript_results'}, 'psych_network': {'figure_1': 'figure_1', 'table_1': 'table_1', 'text_1': 'text_1', 'table_2': 'table_2', 'table_3': 'table_3'}, 'social_network': {'figure_1': 'figure_1', 'table_1': 'table_1', 'table_2': 'table_2', 'table_3': 'table_3', 'table_4': 'table_4', 'manuscript_results': 'manuscript_results'}, 'text_social_science': {'table_1': 'table_1', 'figure_1': 'figure_1', 'table_2': 'table_2', 'table_3': 'table_3', 'table_4': 'table_4', 'figure_2': 'figure_2', 'manuscript_results': 'manuscript_results'}, 'multivariate_ordination': {'figure_1': 'figure_1', 'table_1': 'table_1', 'manuscript_results': 'manuscript_results', 'table_2': 'table_2', 'figure_2': 'figure_2', 'table_3': 'table_3', 'manuscript_results_2': 'manuscript_results_2'}, 'assumption_diagnostics': {'diagnostic_output': 'diagnostic_output', 'diagnostic_output_2': 'diagnostic_output_2', 'analysis_recommendation': 'analysis_recommendation', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output_3': 'diagnostic_output_3'}, 'multiple_testing_endpoints': {'table_1': 'table_1', 'manuscript_results': 'manuscript_results', 'figure_1': 'figure_1', 'method_note': 'method_note', 'manuscript_results_2': 'manuscript_results_2', 'diagnostic_output': 'diagnostic_output'}, 'measurement_invariance': {'table_1': 'table_1', 'table_2': 'table_2', 'manuscript_results': 'manuscript_results', 'method_note': 'method_note', 'manuscript_results_2': 'manuscript_results_2', 'diagnostic_output': 'diagnostic_output'}, 'latent_growth_sem': {'table_1': 'table_1', 'figure_1': 'figure_1', 'manuscript_results': 'manuscript_results', 'method_note': 'method_note', 'manuscript_results_2': 'manuscript_results_2', 'diagnostic_output': 'diagnostic_output'}, 'multilevel_behavior_model': {'table_1': 'table_1', 'table_2': 'table_2', 'figure_1': 'figure_1', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'weighted_survey_scale': {'table_1': 'table_1', 'table_2': 'table_2', 'text_1': 'text_1', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'conjoint_choice': {'table_1': 'table_1', 'table_2': 'table_2', 'manuscript_results': 'manuscript_results', 'method_note': 'method_note', 'manuscript_results_2': 'manuscript_results_2'}, 'discrete_choice_model': {'table_1': 'table_1', 'table_2': 'table_2', 'figure_1': 'figure_1', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'item_equating': {'table_1': 'table_1', 'table_2': 'table_2', 'table_3': 'table_3', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'mixed_methods_open_text': {'table_1': 'table_1', 'table_2': 'table_2', 'text_1': 'text_1', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results'}, 'paired_matched_tests': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'stratified_categorical_trend': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'nonlinear_spline_gam': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'behavior_scale_development': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'behavior_riclpm_cross_lagged': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'behavior_apim_dyadic': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'behavior_multilevel_sem': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'behavior_common_method_bias': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'behavior_latent_transition': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'behavior_response_style_nonresponse': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}}


# Canonical portfolios retain explicit statistical meaning when several
# historical artifacts have matching labels.
ROLE_MAPS["bayesian_robust_stats"]["robustness_comparison"] = "table_3"
ROLE_MAPS["discrete_choice_model"]["effect_estimate_table"] = "table_1"


def _core_for(template_id: str):
    def render(plan: AnalysisPlan) -> str:
        entries = [
            f"{json.dumps(key)}={value}"
            for key, value in INPUTS[template_id].items()
            if key in plan.resolved_inputs
        ]
        body = _BODIES[template_id]
        helpers = "\n".join(text for name, text in _HELPER_PARTS.items() if name + "(" in body)
        return ".sb_inputs <- list(" + ",".join(entries) + ")\n" + _COMMON + helpers + body
    return render


CORES = {key: _core_for(key) for key in _BODIES}
REFERENCE_CASES = {'distribution_visual': '真实 R：连续变量分组密度、直方图、箱线图和有效/缺失数量。', 'assumption_diagnostics': '真实 R：同一 OLS 模型的数值系数及残差假设诊断。', 'multiple_testing_endpoints': '真实 R：同族连续终点 Welch 检验及 Holm/BH 校正。', 'paired_matched_tests': '真实 R：对象对齐的配对检验和实际均值差。', 'stratified_categorical_trend': '真实 R：实际频数加权分层 CMH 共同优势比。', 'nonlinear_spline_gam': '真实 R：mgcv Gaussian REML 平滑模型、edf 和条件曲线。', 'bayesian_robust_stats': '真实 R：Huber 系数参考值、MCMC 正规先验后验抽样及 Chib Bayes 因子。', 'multivariate_ordination': '真实 R：标准化数值 PCA 特征值、完整坐标和贡献。', 'irt_models': '真实 R：一维 Rasch 条目模型、实际信息函数和多组 DIF。', 'latent_class': '真实 R：poLCA 多起点候选 BIC 与归一化类别后验概率。', 'psych_network': '真实 R：EBICglasso 实际非零边、中心性和对象 Bootstrap。', 'social_network': '真实 R：无向简单关系网络及实际边数、ERGM 边项模型。', 'mixed_methods_open_text': '真实 R：quanteda 实际词频及两评定者人工编码一致性。', 'text_social_science': '真实 R：quanteda TF-IDF 余弦相似度和无监督词汇聚类。', 'measurement_invariance': '真实 R：lavaan 构型、度量、标量与严格不变性模型。', 'latent_growth_sem': '真实 R：对象对齐四波次实际时间载荷的潜增长模型。', 'multilevel_behavior_model': '真实 R：lme4 层内×层间交互系数参考值及聚类 ICC。', 'weighted_survey_scale': '真实 R：survey 设计均值和分组均值与加权均值参考值。', 'conjoint_choice': '真实 R：respondent×task 条件选择模型及设计内相对重要性。', 'discrete_choice_model': '真实 R：含备选项常数的条件模型，各选择集概率归一化。', 'item_equating': '真实 R：两形式共同锚题 Rasch 校准及共同尺度真分映射。', 'behavior_scale_development': '真实 R：量表条目 EFA 和 alpha 与 psych 参考值。', 'behavior_riclpm_cross_lagged': '真实 R：四波次双特征 RI-CLPM 随机截距及十二条滞后路径。', 'behavior_apim_dyadic': '真实 R：可区分成员 APIM 四条实际 actor/partner 路径。', 'behavior_multilevel_sem': '真实 R：lavaan 两层潜变量测量及层内/层间结构路径。', 'behavior_common_method_bias': '真实 R：真实单因子 CFA 拟合诊断，不把该诊断当作偏差成立证明。', 'behavior_latent_transition': '真实 R：LMest 联合潜 Markov 模型及状态转变概率归一化。', 'behavior_response_style_nonresponse': '真实 R：对象缺失/直线作答标记及真实响应状态逻辑模型。', 'bayesian_sem': '真实 R：blavaan Stan 实际抽样、先验绑定、后验路径及 Rhat/ESS。'}
