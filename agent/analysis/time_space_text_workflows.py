"""Explicit time, geospatial, network and text workflows on frozen R inputs."""

import re

from r_runtime.package_references import _scrub_r_code

from .meta_time_cores import COMMON, TIME_HELPERS
from .trial_evidence_workflows import TRIAL_COMMON

TST_COMMON = r'''
.ts_spec<-function(x,label) .te_json(x,label)
.ts_xy<-function(x,label="坐标") {
 if(is.matrix(x) && ncol(x)==2) xy<-x else {
  parts<-lapply(as.character(x),function(value) {
   if(startsWith(trimws(value),"[")) return(as.character(jsonlite::fromJSON(value)))
   strsplit(value,if(grepl("|",value,fixed=TRUE)) "|" else ",",fixed=TRUE)[[1]]
  })
  if(any(lengths(parts)!=2)) stop(label,"须为每行 x|y、[x,y] 或 x,y 文本（两个数值），不推断坐标系统")
  xy<-t(vapply(parts,function(z) suppressWarnings(as.numeric(trimws(z))),numeric(2)))
 }
 if(any(!is.finite(xy))) stop(label,"不可解析为有限数值")
 colnames(xy)<-c(".x",".y");xy
}
.ts_crs<-function() {
 crs<-.mt_get("crs_v");if(is.null(crs)) stop("空间操作必须明确 crs（EPSG 数字或 WKT 字符串）")
 z<-sf::st_crs(crs);if(is.na(z)) stop("CRS 不可识别");z
}
.ts_sf<-function(d,coordinate_var) {
 xy<-.ts_xy(d[[coordinate_var]]);d$.x<-xy[,1];d$.y<-xy[,2]
 sf::st_as_sf(d,coords=c(".x",".y"),crs=.ts_crs(),remove=FALSE)
}
.ts_weight<-function(ids,adjacency) {
 ids<-as.character(ids);if(anyDuplicated(ids)) stop("空间权重地区 ID 须唯一")
 links<-strsplit(as.character(adjacency),",",fixed=TRUE)
 W<-matrix(0,length(ids),length(ids),dimnames=list(ids,ids))
 for(i in seq_along(ids)) {
  neighbors<-trimws(links[[i]]);neighbors<-neighbors[nzchar(neighbors)]
  if(any(!neighbors %in% ids) || ids[i] %in% neighbors || anyDuplicated(neighbors)) stop("邻接须为已选地区 ID 的逗号列表，不含自身/重复/未知 ID")
  if(length(neighbors)) W[i,match(neighbors,ids)]<-1
 }
 if(any(W!=t(W))) stop("当前空间权重要求双向对称的已确认邻接关系")
 if(any(rowSums(W)==0)) stop("空间权重存在孤立地区，当前模型不静默设零邻居")
 spdep::mat2listw(W,style="W")
}
.ts_docs<-function() {
 extras<-unique(c(.mt_get("id_v"),.mt_get("group_v"),.mt_get("time_v"),.mt_get("label_v"),.mt_get("cluster_v"),as.character(.mt_get("covars_v",character(0)))))
 d<-.mt_data(c(text_v,extras));if(!is.character(d[[text_v]]) && !is.factor(d[[text_v]])) stop("文本列必须真实字符/因子文本，不能把数值编码当文档")
 d$.text<-as.character(d[[text_v]])
 d$.id<-if(length(.mt_get("id_v"))) as.character(d[[id_v]]) else as.character(seq_len(nrow(d)))
 if(anyDuplicated(d$.id) || any(!nzchar(trimws(d$.text)))) stop("文档 ID 唯一且文本非空")
 language<-.mt_select(.mt_get("language_v","english"),c("english","whitespace"),"分词模式")
 if(language=="english" && any(grepl("[\u4e00-\u9fff]",d$.text))) stop("当前英文分词不得用于未分词中文；请提供已确认空格分词文本并选择 whitespace")
 tokens<-quanteda::tokens(tolower(d$.text),what="word",remove_punct=TRUE,remove_numbers=FALSE,remove_symbols=TRUE)
 if(language=="english") tokens<-quanteda::tokens_remove(tokens,quanteda::stopwords("en"))
 names(tokens)<-d$.id
 list(data=d,tokens=tokens,dfm=quanteda::dfm(tokens),language=language)
}
.ts_dtm<-function(dfm) {
 m<-methods::as(dfm,"dgCMatrix")
 if(ncol(m)<3 || any(Matrix::rowSums(m)==0)) stop("至少三个词项且各文档有可用词项")
 m
}
.ts_note<-function(method,text,diagnostics="执行范围、缺失与识别条件已明确，未自动推断因果结论。") {
 .te_text("method",method,"明确统计方法");.te_text("text",text,"实际执行结果");if(is.null(.out[["diagnostics"]])) .te_text("diagnostics",diagnostics,"执行诊断与范围")
}
'''

BAYES_STATE = r'''
.step<-"bayesian-local-level"
spec<-.ts_spec(spec_v,"状态趋势模型")
needed<-c("order","observation_shape","observation_rate","state_shape","state_rate")
if(any(!needed %in% names(spec))) stop("状态模型需明确 order 和观测/状态方差 inverse-gamma 先验 shape/rate")
allowed<-c(needed,"samples","burnin","horizon","seed","covariate_coefficients","future_covariates")
if(length(setdiff(names(spec),allowed))) stop("状态趋势配置含未支持字段")
order<-.mt_integer(spec$order,1,2,"状态趋势阶数");samples<-.mt_integer(if(is.null(spec$samples)) 500 else spec$samples,200,10000,"Gibbs 总次数")
burn<-.mt_integer(if(is.null(spec$burnin)) floor(samples/2) else spec$burnin,50,samples-50,"burnin")
h<-.mt_integer(if(is.null(spec$horizon)) 6 else spec$horizon,1,100,"预测步数")
seed<-.mt_integer(if(is.null(spec$seed)) 20261008 else spec$seed,0,.Machine$integer.max,"随机种子");set.seed(seed)
covars<-as.character(.mt_get("covars_v",character(0)));df<-.mt_data(c(time_v,measure_v,covars));axis<-.mt_time(df[[time_v]]);df<-df[axis$order,,drop=FALSE]
y<-.mt_numeric(df[[measure_v]],"观测序列");if(length(y)<20) stop("至少二十个规则观测")
adjustment<-rep(0,length(y));future_adjustment<-rep(0,h)
if(length(covars)) {
 if(is.null(spec$covariate_coefficients) || !setequal(names(spec$covariate_coefficients),covars)) stop("选入协变量须提供每列已确认固定系数；不冒充估计的动态系数")
 X<-as.matrix(df[,covars,drop=FALSE]);if(!is.numeric(X)) stop("状态模型协变量须数值")
 b<-as.numeric(unlist(spec$covariate_coefficients[covars]));adjustment<-as.numeric(X%*%b)
 F<-as.matrix(spec$future_covariates);if(!identical(dim(F),c(h,length(covars))) || !is.numeric(F)) stop("选协变量预测需 h×p future_covariates 数值数组")
 future_adjustment<-as.numeric(F%*%b)
}
for(k in needed[-1]) if(!is.numeric(spec[[k]]) || length(spec[[k]])!=1 || !is.finite(spec[[k]]) || spec[[k]]<=0) stop("先验 shape/rate 须为正有限数")
mod<-dlm::dlmModPoly(order,dV=1,dW=rep(1,order))
draws<-dlm::dlmGibbsDIG(y-adjustment,mod,shape.y=spec$observation_shape,rate.y=spec$observation_rate,
 shape.theta=spec$state_shape,rate.theta=spec$state_rate,n.sample=samples,save.states=TRUE,progressBar=FALSE)
keep<-seq.int(burn+1,samples);theta<-draws$theta[,,keep,drop=FALSE]
level_draws<-theta[-1,1,,drop=FALSE];dim(level_draws)<-c(length(y),length(keep))
states<-data.frame(time=axis$index,observed=y,posterior_level=rowMeans(level_draws)+adjustment,
 lower=apply(level_draws,1,quantile,.025)+adjustment,upper=apply(level_draws,1,quantile,.975)+adjustment)
dynamic<-data.frame(component=c("observation variance",paste0("state variance ",seq_len(order))),
 posterior_mean=c(mean(draws$dV[keep]),colMeans(draws$dW[keep,,drop=FALSE])))
future<-matrix(NA_real_,h,length(keep));GG<-dlm::GG(mod);FF<-dlm::FF(mod)
for(j in seq_along(keep)) {
 state<-draws$theta[nrow(draws$theta),,keep[j]]
 for(i in seq_len(h)) {state<-as.numeric(GG%*%state)+rnorm(order,sd=sqrt(draws$dW[keep[j],]));future[i,j]<-as.numeric(FF%*%state)+rnorm(1,sd=sqrt(draws$dV[keep[j]]))+future_adjustment[i]}
}
prediction<-data.frame(time=tail(axis$index,1)+axis$step*seq_len(h),mean=rowMeans(future),lower=apply(future,1,quantile,.025),upper=apply(future,1,quantile,.975))
fig<-ggplot2::ggplot(states,ggplot2::aes(time,posterior_level))+ggplot2::geom_ribbon(ggplot2::aes(ymin=lower,ymax=upper),alpha=.2)+ggplot2::geom_line()+ggplot2::geom_point(ggplot2::aes(y=observed),alpha=.3)+ggplot2::theme_bw()
.te_plot("figure",fig,"真实 Gibbs 平滑状态");.te_table("dynamic",dynamic,"方差后验摘要");.te_table("prediction",prediction,"包括状态和观测不确定性的后验预测")
.te_table("states",states,"实际状态后验区间");.te_table("limits",data.frame(method="dlm inverse-gamma Gibbs",samples=samples,burnin=burn,seed=seed,scope="univariate order 1/2 Gaussian dynamic trend; known fixed covariate coefficients"),"模型与MCMC限制")
.te_text("state_interpretation",paste("结果：",length(y),"个观测，末期状态后验均值",signif(tail(states$posterior_level,1),5),
 "，95%后验区间",signif(tail(states$lower,1),5),"至",signif(tail(states$upper,1),5),
 "；首个未来时点的观测预测均值",signif(prediction$mean[1],5),"，95%预测区间",signif(prediction$lower[1],5),"至",signif(prediction$upper[1],5),
 "。不确定性包含状态与观测噪声；这是明确IG方差先验的单序列Gaussian阶数",order,"趋势模型，实际保留",length(keep),
 "个样本，未自动保证MCMC收敛或模型适配。适用范围为规则观测和已知固定外生系数。建议进一步检查链混合、残差与先验敏感性后用于科研解释。"),"真实后验结果与模型范围")
'''

GAM_TIME = r'''
.step<-"mgcv-smooth"
group_v<-.mt_get("group_v");covars<-as.character(.mt_get("covars_v",character(0)));df<-.mt_data(c(exposure_v,outcome_v,group_v,covars))
df$.x<-.mt_numeric(df[[exposure_v]],"平滑自变量");df$.y<-.te_outcome(df[[outcome_v]])
k<-.mt_integer(.mt_get("k_v",10),3,min(50,length(unique(df$.x))-1),"平滑基维度")
binary<-all(df$.y %in% c(0,1));s<-mgcv::s
terms<-c(paste0("s(.x,k=",k,")"),sprintf("`%s`",covars));if(length(group_v)) {df$.group<-factor(df[[group_v]]);terms<-c(paste0("s(.x,by=.group,k=",k,")"),".group",sprintf("`%s`",covars))}
fit<-mgcv::gam(as.formula(paste(".y~",paste(terms,collapse="+"))),data=df,family=if(binary) binomial() else gaussian(),method="REML")
sm<-summary(fit);st<-sm$s.table;model<-data.frame(term=rownames(st),edf=st[,1],reference_df=st[,2],statistic=st[,3],p=st[,4])
diag<-data.frame(test=c("deviance explained","model rank","residual variance"),result=c(sm$dev.expl,fit$rank,sm$scale))
grid<-seq(min(df$.x),max(df$.x),length.out=100);pieces<-list()
groups<-if(length(group_v)) levels(df$.group) else "overall"
for(g in groups) {
 nd<-df[rep(1,100),,drop=FALSE];nd$.x<-grid;if(length(group_v)) nd$.group<-factor(g,levels=levels(df$.group))
 for(v in covars) nd[[v]]<-if(is.numeric(df[[v]])) median(df[[v]]) else factor(levels(factor(df[[v]]))[1],levels=levels(factor(df[[v]])))
 pr<-predict(fit,nd,type="link",se.fit=TRUE);pieces[[length(pieces)+1]]<-data.frame(x=grid,group=g,estimate=fit$family$linkinv(pr$fit),lower=fit$family$linkinv(pr$fit-1.959964*pr$se.fit),upper=fit$family$linkinv(pr$fit+1.959964*pr$se.fit))
}
curve<-do.call(rbind,pieces);fig<-ggplot2::ggplot(curve,ggplot2::aes(x,estimate,colour=group,fill=group))+ggplot2::geom_ribbon(ggplot2::aes(ymin=lower,ymax=upper),alpha=.2,colour=NA)+ggplot2::geom_line()+ggplot2::theme_bw()
.te_plot("figure",fig,"mgcv 条件平滑及点态区间");.te_table("model",model,"实际平滑项与检验");.te_table("diagnostics",diag,"实际模型诊断");.te_table("prediction",curve,"预测网格与区间")
.ts_note("mgcv GAM REML；所选分组独立平滑与固定组项，协变量进入模型；二分类 logit，否则 Gaussian。",paste("平滑项",nrow(model),"；解释偏差",sm$dev.expl,"。区间为条件点态区间，非同时带或因果效应。"))
'''

PANEL_FORECAST = r'''
.step<-"panel-arimax"
covars<-as.character(.mt_get("covars_v",character(0)));df<-.mt_data(c(time_v,group_v,measure_v,covars))
h<-.mt_integer(.mt_get("horizon_v",6),1,100,"预测步数");frequency<-.mt_integer(.mt_get("freq_v",1),1,365,"序列频率")
future_spec<-.mt_get("future_v");if(length(covars)) {future_spec<-.ts_spec(future_spec,"未来外生变量");if(!setequal(names(future_spec),unique(as.character(df[[group_v]])))) stop("未来外生 JSON 需每个面板对应 h×p 矩阵")}
pieces<-list();trends<-list();errors<-list()
for(g in unique(as.character(df[[group_v]]))) {
 d<-df[as.character(df[[group_v]])==g,,drop=FALSE];axis<-.mt_time(d[[time_v]],frequency);d<-d[axis$order,,drop=FALSE];y<-.mt_numeric(d[[measure_v]],"面板指标")
 if(length(y)<18) stop("各面板至少十八个规则时间点")
 X<-if(length(covars)) as.matrix(d[,covars,drop=FALSE]) else NULL
 if(length(covars) && !is.numeric(X)) stop("外生变量须数值")
 F<-if(length(covars)) as.matrix(future_spec[[g]]) else NULL
 if(length(covars) && (!identical(dim(F),c(h,length(covars))) || !is.numeric(F) || any(!is.finite(F)))) stop("未来外生维度/数值无效")
 hold<-min(h,floor(length(y)/4));train<-seq_len(length(y)-hold);test<-tail(seq_along(y),hold)
 ft<-forecast::auto.arima(ts(y[train],frequency=frequency),xreg=if(length(covars)) X[train,,drop=FALSE] else NULL)
 val<-forecast::forecast(ft,h=hold,xreg=if(length(covars)) X[test,,drop=FALSE] else NULL)
 errors[[length(errors)+1]]<-data.frame(panel=g,holdout_n=hold,RMSE=sqrt(mean((y[test]-as.numeric(val$mean))^2)),MAE=mean(abs(y[test]-as.numeric(val$mean))))
 fit<-forecast::auto.arima(ts(y,frequency=frequency),xreg=X);fc<-forecast::forecast(fit,h=h,xreg=F,level=95)
 pieces[[length(pieces)+1]]<-data.frame(panel=g,time=tail(axis$index,1)+axis$step*seq_len(h),mean=as.numeric(fc$mean),lower=as.numeric(fc$lower),upper=as.numeric(fc$upper))
 trends[[length(trends)+1]]<-data.frame(panel=g,time=axis$index,value=y)
}
predictions<-do.call(rbind,pieces);trends<-do.call(rbind,trends);errors<-do.call(rbind,errors)
fig<-ggplot2::ggplot(trends,ggplot2::aes(time,value,colour=panel))+ggplot2::geom_line()+ggplot2::geom_line(data=predictions,ggplot2::aes(y=mean),linetype=2)+ggplot2::geom_ribbon(data=predictions,ggplot2::aes(x=time,ymin=lower,ymax=upper,fill=panel),inherit.aes=FALSE,alpha=.15)+ggplot2::facet_wrap(~panel,scales="free_y")+ggplot2::theme_bw()
.te_plot("figure",fig,"各面板实际时间趋势与预测");.te_table("prediction",merge(predictions,errors,by="panel"),"实际外生预测与时间留出误差")
.ts_note("各面板独立 forecast auto.arima/ARIMAX；实际末段留出验证，全样本再拟合预测，所有外生列与未来值明确使用。",paste("面板",nrow(errors),"，每个预测",h,"步；地区差异来自各自观测/模型，未冒充共享面板因果系数。"),paste("时间留出 RMSE：",paste(errors$panel,round(errors$RMSE,4),collapse="; ")))
'''

FUNCTIONAL = r'''
.step<-"functional-basis-pca"
group_v<-.mt_get("group_v");df<-.mt_data(c(subject_v,time_v,measure_v,group_v));df$.time<-.mt_numeric(df[[time_v]],"函数时间");df$.value<-.mt_numeric(df[[measure_v]],"函数指标")
ids<-unique(as.character(df[[subject_v]]));times<-sort(unique(df$.time));if(length(ids)<6 || length(times)<8 || anyDuplicated(df[,c(subject_v,time_v)])) stop("函数 PCA 至少六人、八个共同规则点，每人时间唯一")
if(any(table(df[[subject_v]])!=length(times))) stop("当前函数 PCA 要求各受试者完整共同时间网格")
Y<-vapply(ids,function(id) {d<-df[as.character(df[[subject_v]])==id,];d$.value[match(times,d$.time)]},numeric(length(times)))
basis<-fda::create.bspline.basis(rangeval=range(times),nbasis=min(12,length(times)))
smooth<-fda::smooth.basis(times,Y,fda::fdPar(basis,Lfdobj=2,lambda=.mt_get("lambda_v",.01)))
nharm<-.mt_integer(.mt_get("components_v",3),1,min(5,length(ids)-1),"函数主成分数")
pca<-fda::pca.fd(smooth$fd,nharm=nharm);scores<-as.data.frame(pca$scores);names(scores)<-paste0("FPC",seq_len(nharm));scores$subject<-ids
scores$variance_proportions<-paste(signif(pca$varprop[seq_len(nharm)],4),collapse=",")
if(length(group_v)) {
 groups<-vapply(ids,function(id) {z<-unique(as.character(df[as.character(df[[subject_v]])==id,group_v]));if(length(z)!=1) stop("函数分组须受试者内固定");z},character(1));scores$group<-groups
}
values<-fda::eval.fd(times,smooth$fd);curve<-data.frame(time=rep(times,length(ids)),subject=rep(ids,each=length(times)),value=as.numeric(values))
fig<-ggplot2::ggplot(curve,ggplot2::aes(time,value,group=subject))+ggplot2::geom_line(alpha=.4)+ggplot2::theme_bw()
clusters<-.mt_integer(.mt_get("clusters_v",2),2,min(5,length(ids)-1),"曲线聚类数");set.seed(.mt_get("seed_v",20261008))
km<-kmeans(pca$scores,centers=clusters,nstart=20);scores$cluster<-km$cluster
curve$cluster<-factor(km$cluster[match(curve$subject,ids)]);cl_fig<-ggplot2::ggplot(curve,ggplot2::aes(time,value,group=subject,colour=cluster))+ggplot2::geom_line()+ggplot2::theme_bw()
.te_plot("figure",fig,"实际 B-spline 平滑函数");.te_table("pca",scores,"fda 函数主成分与方差解释");.te_plot("clusters",cl_fig,"实际 FPC 分数 kmeans 曲线分型")
.ts_note("fda B-spline 二阶惩罚 smooth.basis、pca.fd；共同网格函数数据，FPC 分数 kmeans。",paste("受试者",length(ids),"、网格",length(times),"；前",nharm,"FPC 方差",paste(round(pca$varprop[seq_len(nharm)],3),collapse=",")),paste("GCV=",paste(signif(smooth$gcv,3),collapse=","),"；分组若选入实际保留于FPC表，不据此虚构组间检验。"))
'''

SPATIOTEMPORAL = r'''
.step<-"space-time-fixed-effects"
coordinate_v<-.mt_get("coordinate_v");covars<-as.character(.mt_get("covars_v",character(0)));df<-.mt_data(c(region_v,time_v,outcome_v,coordinate_v,covars))
if(anyDuplicated(df[,c(region_v,time_v)])) stop("地区×时间记录须唯一")
df$.region<-factor(df[[region_v]]);df$.time<-if(is.character(df[[time_v]])) as.numeric(as.Date(df[[time_v]])) else as.numeric(df[[time_v]]);df$.y<-.mt_numeric(df[[outcome_v]],"时空指标")
if(any(!is.finite(df$.time))) stop("时间须真实数值或可解析日期，不能按字典类别序号替代间隔")
for(v in covars) if(is.character(df[[v]])) df[[v]]<-factor(df[[v]])
fit<-lm(.mt_formula(".y",c(".region",".time",covars)),data=df);if(fit$rank<length(coef(fit))) stop("地区固定效应/协变量模型存在完全共线，所选协变量不可独立识别")
ct<-coef(summary(fit))
model<-data.frame(term=rownames(ct),estimate=ct[,1],SE=ct[,2],p=ct[,4]);model$lower<-model$estimate-qt(.975,df.residual(fit))*model$SE;model$upper<-model$estimate+qt(.975,df.residual(fit))*model$SE
trend<-ggplot2::ggplot(df,ggplot2::aes(.time,.y,colour=.region))+ggplot2::geom_line()+ggplot2::theme_bw()
.te_plot("figure",trend,"实际地区时间趋势");.te_table("model",model,"地区固定截距、共同时间趋势及协变量")
if(length(coordinate_v)) {
 if(any(vapply(split(df[[coordinate_v]],df$.region),function(x) length(unique(x))!=1,logical(1)))) stop("当前每地区坐标须时间内不变")
 points<-.ts_sf(df,coordinate_v);map<-ggplot2::ggplot(points)+ggplot2::geom_sf(ggplot2::aes(colour=.y))+ggplot2::facet_wrap(~.time)+ggplot2::theme_bw()
 .te_plot("map",map,"明确 CRS 的真实地区点位时间地图")
}
.ts_note("地区固定截距+共同时间趋势+选入协变量的 Gaussian lm；可选真实 sf 点位地图，未声称空间自回归模型。",paste("地区",nlevels(df$.region),"，n=",nrow(df),"；地图仅在已提供坐标与 CRS 时输出。"),paste("残差SD",sigma(fit),"；地区/时间相关残差未额外调整，结果不自动推断空间溢出。"))
'''

VAR_VECM = r'''
.step<-"multivariate-time-design"
covars<-as.character(.mt_get("covars_v",character(0)));season_v<-.mt_get("season_v");series<-as.character(series_v)
df<-.mt_data(c(time_v,series,covars,season_v));axis<-.mt_time(df[[time_v]]);df<-df[axis$order,,drop=FALSE]
Y<-as.matrix(df[,series,drop=FALSE]);if(!is.numeric(Y) || ncol(Y)<2 || nrow(Y)<40) stop("至少两条连续序列与四十规则点")
X<-if(length(covars)) as.matrix(df[,covars,drop=FALSE]) else NULL
if(length(covars) && !is.numeric(X)) stop("外生列须数值")
if(length(season_v)) {S<-model.matrix(~factor(df[[season_v]]))[, -1,drop=FALSE];X<-cbind(X,S)}
method<-.mt_select(.mt_get("method_v","var"),c("var","vecm"),"多变量时间模型")
lag<-.mt_integer(.mt_get("lag_v",2),if(method=="vecm") 2 else 1,min(12,floor(nrow(Y)/10)),"滞后阶数")
if(method=="var") {
 fit<-vars::VAR(Y,p=lag,type="const",exogen=X)
 rows<-lapply(names(fit$varresult),function(v) {c<-coef(summary(fit$varresult[[v]]));data.frame(equation=v,term=rownames(c),estimate=c[,1],SE=c[,2],p=c[,4])})
 primary<-do.call(rbind,rows);serial<-vars::serial.test(fit,lags.pt=16,type="PT.asymptotic")$serial$p.value
} else {
 jo<-urca::ca.jo(Y,type="trace",ecdet="const",K=lag,dumvar=X)
 rank<-.mt_integer(.mt_get("rank_v",sum(jo@teststat>jo@cval[,"5pct"])),1,ncol(Y)-1,"协整秩")
 fit<-vars::vec2var(jo,r=rank)
 primary<-data.frame(test=rownames(jo@cval),statistic=jo@teststat,critical_5pct=jo@cval[,"5pct"],selected_rank=rank)
 serial<-vars::serial.test(fit,lags.pt=16,type="PT.asymptotic")$serial$p.value
}
h<-.mt_integer(.mt_get("horizon_v",10),1,30,"IRF/FEVD 步数")
ir<-vars::irf(fit,n.ahead=h,boot=FALSE,ortho=TRUE);fevd<-vars::fevd(fit,n.ahead=h);pieces<-list()
for(impulse in names(ir$irf)) for(response in colnames(ir$irf[[impulse]])) pieces[[length(pieces)+1]]<-data.frame(impulse=impulse,response=response,horizon=0:h,value=ir$irf[[impulse]][,response],type="orthogonal IRF")
for(response in names(fevd)) for(impulse in colnames(fevd[[response]])) pieces[[length(pieces)+1]]<-data.frame(impulse=impulse,response=response,horizon=1:h,value=fevd[[response]][,impulse],type="FEVD")
curves<-do.call(rbind,pieces);fig<-ggplot2::ggplot(curves,ggplot2::aes(horizon,value,colour=impulse))+ggplot2::geom_line()+ggplot2::facet_grid(type+response~.)+ggplot2::theme_bw()
.te_table("primary",primary,"实际VAR系数或Johansen协整秩检验");.te_plot("figure",fig,"真实 IRF 与 FEVD")
.te_text("text",paste("vars/urca",method,"；滞后",lag,"；外生/季节矩阵",if(is.null(X)) 0 else ncol(X),"列；Portmanteau P=",serial,"。正交IRF依赖当前已确认序列顺序，未提供自助区间。"),"实际多变量时间结果")
'''

HIERARCHICAL = r'''
.step<-"coherent-bottom-up"
covars<-as.character(.mt_get("covars_v",character(0)));split_v<-.mt_get("split_v")
df<-.mt_data(c(time_v,group_v,measure_v,covars,split_v));h<-.mt_integer(.mt_get("horizon_v",6),1,100,"预测步数");frequency<-.mt_integer(.mt_get("freq_v",1),1,365,"频率")
groups<-sort(unique(as.character(df[[group_v]])));if(length(groups)<2) stop("层级预测需至少两个底层组")
times<-sort(unique(df[[time_v]]));if(anyDuplicated(df[,c(group_v,time_v)]) || any(table(df[[group_v]])!=length(times))) stop("底层组需完整共同时间网格")
axis<-.mt_time(times,frequency);times<-times[axis$order]
Y<-vapply(groups,function(g) {z<-df[as.character(df[[group_v]])==g,];.mt_numeric(z[[measure_v]][match(times,z[[time_v]])],"层级指标")},numeric(length(times)))
if(nrow(Y)<18) stop("至少十八个规则点")
object<-hts::hts(Y,nodes=list(length(groups)));aggregation<-hts::aggts(object)
if(max(abs(aggregation[,1]-rowSums(Y)))>1e-8) stop("层级求和结构核验失败")
train<-seq_len(nrow(Y)-min(h,floor(nrow(Y)/4)));test<-setdiff(seq_len(nrow(Y)),train)
if(length(split_v)) {
 marks<-vapply(groups,function(g) {z<-df[as.character(df[[group_v]])==g,];as.integer(z[[split_v]][match(times,z[[time_v]])])},integer(length(times)))
 if(any(!marks %in% c(0,1)) || any(marks!=marks[,1])) stop("验证标记需所有底层组一致的0/1")
 train<-which(marks[,1]==0);test<-which(marks[,1]==1)
 if(!length(test) || !identical(c(train,test),seq_len(nrow(Y)))) stop("验证必须为共同时间末段且训练在前")
}
future_spec<-.mt_get("future_v");if(length(covars)) future_spec<-.ts_spec(future_spec,"未来底层外生变量")
validation<-matrix(NA_real_,length(test),length(groups));final<-matrix(NA_real_,h,length(groups))
for(j in seq_along(groups)) {
 d<-df[as.character(df[[group_v]])==groups[j],];d<-d[match(times,d[[time_v]]),]
 X<-if(length(covars)) as.matrix(d[,covars,drop=FALSE]) else NULL
 F<-if(length(covars)) as.matrix(future_spec[[groups[j]]]) else NULL
 if(length(covars) && (!is.numeric(X) || !is.numeric(F) || !identical(dim(F),c(h,length(covars))))) stop("每底层外生列与未来h×p矩阵须数值且齐全")
 ft<-forecast::auto.arima(ts(Y[train,j],frequency=frequency),xreg=if(length(covars)) X[train,,drop=FALSE] else NULL)
 validation[,j]<-as.numeric(forecast::forecast(ft,h=length(test),xreg=if(length(covars)) X[test,,drop=FALSE] else NULL)$mean)
 f<-forecast::auto.arima(ts(Y[,j],frequency=frequency),xreg=X);final[,j]<-as.numeric(forecast::forecast(f,h=h,xreg=F)$mean)
}
actual<-cbind(total=rowSums(Y[test,,drop=FALSE]),Y[test,,drop=FALSE]);val<-cbind(total=rowSums(validation),validation);pred<-cbind(total=rowSums(final),final);colnames(pred)<-colnames(actual)<-colnames(val)<-c("total",groups)
primary<-do.call(rbind,lapply(seq_len(ncol(pred)),function(j) data.frame(node=colnames(pred)[j],time=tail(axis$index,1)+axis$step*seq_len(h),forecast=pred[,j],holdout_RMSE=sqrt(mean((actual[,j]-val[,j])^2)),reconciliation="bottom-up exact sum",coherence_error=max(abs(pred[,1]-rowSums(pred[,-1,drop=FALSE]))))))
fig<-ggplot2::ggplot(primary,ggplot2::aes(time,forecast,colour=node))+ggplot2::geom_line()+ggplot2::facet_wrap(~node,scales="free_y")+ggplot2::theme_bw()
.te_table("primary",primary,"真实共同末段精度与协调预测");.te_plot("figure",fig,"底层与总量协调预测")
.te_text("text",paste("hts 求和结构，底层 ARIMA/ARIMAX 后 bottom-up 精确协调；总量预测与底层求和一致。验证",length(test),"期，不把分别计算的边际区间相加冒充总量概率扇形区间。"),"层级预测方法与不确定性限制")
'''

ANOMALY = r'''
.step<-"seasonal-residual-anomalies"
group_v<-.mt_get("group_v");intervention_v<-.mt_get("intervention_v");df<-.mt_data(c(time_v,measure_v,group_v,intervention_v))
period<-.mt_integer(.mt_get("period_v",12),2,365,"季节周期");groups<-if(length(group_v)) unique(as.character(df[[group_v]])) else "overall"
threshold<-.mt_get("threshold_v",3.5);if(!is.numeric(threshold) || length(threshold)!=1 || !is.finite(threshold) || threshold<=0) stop("异常MAD阈值须严格正有限数")
pieces<-list();curves<-list()
for(g in groups) {
 d<-if(length(group_v)) df[as.character(df[[group_v]])==g,] else df;axis<-.mt_time(d[[time_v]],period);d<-d[axis$order,];y<-.mt_numeric(d[[measure_v]],"时间指标")
 if(length(y)<3*period) stop("每组至少三个完整季节周期")
 st<-stl(ts(y,frequency=period),s.window="periodic",robust=TRUE)$time.series
 residual<-as.numeric(st[,"remainder"]);scale<-mad(residual);if(scale<=0) stop("残差MAD为零，无法稳健异常标记")
 z<-residual/scale;cp<-changepoint::cpt.mean(as.numeric(st[,"trend"]),method="PELT",penalty="MBIC",minseglen=period)
 points<-changepoint::cpts(cp);an<-which(abs(z)>threshold)
 indexes<-sort(unique(c(an,points)));if(!length(indexes)) indexes<-integer(0)
 pieces[[length(pieces)+1]]<-data.frame(group=g,time=axis$index[indexes],robust_z=z[indexes],anomaly=indexes %in% an,change_point=indexes %in% points,
 intervention=if(length(intervention_v)) as.character(d[[intervention_v]][indexes]) else rep("not supplied",length(indexes)))
 curves[[length(curves)+1]]<-data.frame(group=g,time=axis$index,observed=y,trend=st[,"trend"],seasonal=st[,"seasonal"],residual=residual)
}
primary<-do.call(rbind,pieces);if(!nrow(primary)) primary<-data.frame(group=groups,time=NA_real_,robust_z=NA_real_,anomaly=FALSE,change_point=FALSE,intervention="none detected")
curves<-do.call(rbind,curves);long<-reshape(curves,direction="long",varying=list(c("observed","trend","seasonal","residual")),v.names="value",timevar="component",times=c("observed","trend","seasonal","residual"))
fig<-ggplot2::ggplot(long,ggplot2::aes(time,value))+ggplot2::geom_line()+ggplot2::facet_grid(component~group,scales="free_y")+ggplot2::theme_bw()
.te_table("primary",primary,"实际稳健残差异常与PELT趋势变化点");.te_plot("figure",fig,"真实STL分解")
.te_text("text",paste("稳健 STL 固定季节周期",period,"；残差/MAD 阈值",.mt_get("threshold_v",3.5),"；趋势 PELT MBIC。变化点为探索性检测，所选干预标记仅关联实际时间而未替代变化点或自动作因果推断。"),"异常与变化点真实方法")
'''

GIS = r'''
.step<-"georeferenced-map"
time_v<-.mt_get("time_v");df<-.mt_data(c(region_v,coordinate_v,measure_v,time_v));df$.measure<-.mt_numeric(df[[measure_v]],"地区指标")
points<-.ts_sf(df,coordinate_v)
if(sf::st_is_longlat(points) && (any(abs(points$.x)>180) || any(abs(points$.y)>90))) stop("经纬度超出合法范围")
connection<-sf::st_drop_geometry(points);connection$geometry_WKT<-sf::st_as_text(sf::st_geometry(points));connection$CRS<-sf::st_crs(points)$input
by<-list(region=as.character(df[[region_v]]));if(length(time_v)) by$time<-df[[time_v]]
indicators<-aggregate(df$.measure,by=by,FUN=mean);names(indicators)[ncol(indicators)]<-"observed_mean"
checks<-data.frame(test=c("valid geometry","missing geometry","coordinate duplicates"),result=c(sum(sf::st_is_valid(points)),sum(sf::st_is_empty(points)),sum(duplicated(points[,c(".x",".y")]))),n=nrow(points))
fig<-ggplot2::ggplot(points)+ggplot2::geom_sf(ggplot2::aes(colour=.measure),size=3)+ggplot2::theme_bw()+ggplot2::labs(title="明确 CRS 的实际地理点指标地图")
if(length(time_v)) {points$.time<-df[[time_v]];fig<-fig+ggplot2::facet_wrap(~.time)}
.te_plot("figure",fig,"地理坐标指标地图");.te_table("connection",connection,"ID与真实sf坐标属性关联")
.te_table("indicators",indicators,"实际地区/时间指标");.te_table("checks",checks,"空间数据合法性")
.te_text("caption",paste("sf 地理点地图；CRS=",sf::st_crs(points)$input,"。未提供区域边界时不发明行政多边形或声称完成面域空间连接。"),"实际制图说明")
'''

AUTOCORRELATION = r'''
.step<-"explicit-neighbor-weights"
denominator_v<-.mt_get("denominator_v");df<-.mt_data(c(region_v,measure_v,adjacency_v,coordinate_v,denominator_v))
x<-.mt_numeric(df[[measure_v]],"空间指标")
if(length(denominator_v)) {n<-.mt_numeric(df[[denominator_v]],"地区分母");if(any(n<=0)) stop("分母须严格正值");x<-x/n}
listw<-.ts_weight(df[[region_v]],df[[adjacency_v]]);if(length(x)<8 || sd(x)==0) stop("Moran/LISA至少八地区且指标有变异")
global<-spdep::moran.test(x,listw);local<-spdep::localmoran(x,listw)
lag<-spdep::lag.listw(listw,x-mean(x));local_table<-data.frame(region=as.character(df[[region_v]]),value=x,Ii=local[,1],expectation=local[,2],variance=local[,3],z=local[,4],p=local[,5])
local_table$p_BH<-p.adjust(local_table$p,"BH");local_table$cluster<-ifelse(local_table$p_BH>.05,"not significant",ifelse(x>=mean(x),ifelse(lag>=0,"high-high","high-low"),ifelse(lag>=0,"low-high","low-low")))
global_table<-data.frame(test="Moran I, randomisation variance",I=unname(global$estimate[1]),expectation=unname(global$estimate[2]),variance=unname(global$estimate[3]),statistic=unname(global$statistic),p=global$p.value)
points<-.ts_sf(cbind(df,local_table[,c("Ii","p_BH","cluster")]),coordinate_v)
fig<-ggplot2::ggplot(points)+ggplot2::geom_sf(ggplot2::aes(colour=cluster),size=3)+ggplot2::theme_bw()+ggplot2::labs(title="真实坐标 LISA，BH 调整局部检验")
.te_table("global",global_table,"明确权重的总体Moran");.te_table("local",local_table,"真实局部I/热点与调整P");.te_plot("figure",fig,"地理LISA类别")
.te_text("text","spdep 行标准化对称邻接；全局随机化方差与局部 conditional Moran，局部多重比较用 BH。空间聚集不自动等同因果传播；分母选入时指标按明确比率计算。","空间聚集解释")
'''

GEOSTAT_BODY = r'''
.step<-"projected-kriging"
covars<-as.character(.mt_get("covars_v",character(0)));time_v<-.mt_get("time_v");boundary_v<-.mt_get("boundary_v")
df<-.mt_data(c(coordinate_v,measure_v,covars,time_v,boundary_v));df$.value<-.mt_numeric(df[[measure_v]],"空间连续指标")
if(length(time_v) && length(unique(df[[time_v]]))!=1) stop("当前插值每次一个已确认时间切片；不要混合时点")
points<-.ts_sf(df,coordinate_v);if(sf::st_is_longlat(points)) stop("半变异/kriging要求明确投影CRS；请选对应投影坐标，不直接按经纬度作欧氏距离")
if(nrow(points)<15 || anyDuplicated(points[,c(".x",".y")])) stop("至少十五个不同投影采样点")
formula<-.mt_formula(".value",covars)
distance_range<-max(as.numeric(sf::st_distance(points)));vg<-gstat::variogram(formula,points,cutoff=distance_range*.75,width=distance_range*.75/10)
models<-lapply(c("Sph","Exp","Gau"),function(model) tryCatch(gstat::fit.variogram(vg,gstat::vgm(psill=var(df$.value)*.8,model=model,range=max(vg$dist)/3,nugget=var(df$.value)*.2)),error=function(e) NULL))
valid<-which(vapply(models,function(z) !is.null(z) && !isTRUE(attr(z,"singular")) && all(z$psill>=0) && all(z$range>=0),logical(1)))
if(!length(valid)) stop("半变异函数拟合无合法非奇异模型")
best<-valid[which.min(vapply(models[valid],function(z) attr(z,"SSErr"),numeric(1)))];model<-models[[best]]
if(validation) {
 validation_method<-.mt_select(.mt_get("validation_v","spatial_block"),c("spatial_block","leave_one_out"),"空间验证方式")
 if(validation_method=="spatial_block") {
  fold_count<-.mt_integer(.mt_get("folds_v",5),2,min(10,floor(nrow(points)/3)),"空间折数");set.seed(.mt_get("seed_v",20261008))
  folds_vector<-kmeans(cbind(points$.x,points$.y),centers=fold_count,nstart=20)$cluster
 } else folds_vector<-seq_len(nrow(points))
 cv<-gstat::krige.cv(formula,points,model=model,nfold=folds_vector,verbose=FALSE)
 grid<-points;prediction<-as.data.frame(gstat::krige(formula,points,grid,model=model,debug.level=0))
 prediction$validation_prediction<-cv$var1.pred;prediction$validation_residual<-cv$residual
 metrics<-data.frame(method=paste("gstat",validation_method),RMSE=sqrt(mean(cv$residual^2)),MAE=mean(abs(cv$residual)),ME=mean(cv$residual),variogram_model=as.character(model$model[2]))
} else {
 if(!grid_v %in% names(dataset)) stop("预测网格坐标列不存在")
 raw_grid<-dataset[[grid_v]];raw_grid<-raw_grid[!is.na(raw_grid) & nzchar(as.character(raw_grid))];if(!length(raw_grid)) stop("预测网格为空")
 grid_df<-data.frame(coordinate=as.character(raw_grid));grid<-.ts_sf(grid_df,"coordinate")
 if(length(covars)) stop("含趋势协变量的外部网格 kriging 需网格协变量接口；当前 geostatistics 请用普通kriging或插值验证模板")
 prediction<-as.data.frame(gstat::krige(formula,points,grid,model=model,debug.level=0));metrics<-data.frame(method="gstat ordinary kriging",model=as.character(model$model[2]),SSE=attr(model,"SSErr"))
}
prediction$x<-grid$.x;prediction$y<-grid$.y;prediction$lower<-prediction$var1.pred-1.959964*sqrt(pmax(0,prediction$var1.var));prediction$upper<-prediction$var1.pred+1.959964*sqrt(pmax(0,prediction$var1.var))
if(length(boundary_v)) {
 boundary<-sf::st_as_sfc(unique(as.character(df[[boundary_v]])),crs=sf::st_crs(points));if(length(boundary)!=1 || any(!sf::st_is_valid(boundary))) stop("每时间切片需要一个合法研究边界WKT")
 inside<-lengths(sf::st_intersects(grid,boundary))>0;prediction<-prediction[inside,,drop=FALSE];grid<-grid[inside,]
}
map_points<-grid;map_points$.prediction<-prediction$var1.pred;map_points$.SE<-sqrt(pmax(0,prediction$var1.var))
fig<-ggplot2::ggplot(map_points)+ggplot2::geom_sf(ggplot2::aes(colour=.prediction,size=.SE))+ggplot2::theme_bw()+ggplot2::labs(title=if(validation) "实际空间留出点位预测与不确定性" else "实际网格kriging预测及不确定性")
.te_table("variogram",as.data.frame(model),"真实拟合半变异模型");.te_table("prediction",prediction,"实际预测、方差与区间");.te_table("metrics",metrics,"真实模型拟合或空间LOOCV误差");.te_plot("figure",fig,"真实投影点位kriging地图")
if(validation) .te_table("primary",cbind(prediction,validation_method=validation_method,RMSE=metrics$RMSE,MAE=metrics$MAE),"真实空间留出预测、误差与逐点模型区间")
.te_text("text",paste("gstat",if(validation) validation_method else "普通kriging","；Sph/Exp/Gau合法模型按半变异SSE选择。预测区间条件于已拟合变异模型；验证重用全样本拟合变异参数，应明确为空间留出而非完整嵌套调参验证。未发明栅格表面或跨时点插值。"),"空间插值范围与不确定性")
'''

RASTER = r'''
.step<-"provided-raster-linkage"
time_v<-.mt_get("time_v");region_v<-.mt_get("region_v");df<-.mt_data(c(coordinate_v,exposure_v,time_v,region_v))
points<-.ts_sf(df,coordinate_v);raster_spec<-.mt_get("raster_v");if(is.null(raster_spec)) stop("真实栅格提取必须提供 raster_spec；只有已提取暴露列不能冒充栅格提取")
spec<-.ts_spec(raster_spec,"栅格规格")
required<-c("nrows","ncols","extent","crs","values");if(!all(required %in% names(spec)) || length(setdiff(names(spec),required))) stop("栅格JSON须为nrows/ncols/extent/crs/values，不接受未追溯文件路径")
extent<-unlist(spec$extent);values<-unlist(spec$values);nr<-.mt_integer(spec$nrows,1,1000,"栅格行数");nc<-.mt_integer(spec$ncols,1,1000,"栅格列数")
if(length(extent)!=4 || extent[1]>=extent[2] || extent[3]>=extent[4] || length(values)!=nr*nc || !is.numeric(values) || any(!is.finite(values))) stop("栅格范围/值数量无效")
raster<-terra::rast(nrows=nr,ncols=nc,xmin=extent[1],xmax=extent[2],ymin=extent[3],ymax=extent[4],crs=sf::st_crs(spec$crs)$wkt)
terra::values(raster)<-values
projected<-sf::st_transform(points,terra::crs(raster));extracted<-terra::extract(raster,terra::vect(projected))[,2]
observed<-.mt_numeric(df[[exposure_v]],"已提供暴露列")
primary<-data.frame(row=seq_len(nrow(df)),x=points$.x,y=points$.y,provided_exposure=observed,raster_extracted_exposure=extracted,difference=extracted-observed,matched=!is.na(extracted))
if(length(time_v)) primary$time<-df[[time_v]];if(length(region_v)) primary$region<-df[[region_v]]
diag<-data.frame(test=c("inside raster cells","outside/NoData","agreement RMSE"),result=c(sum(!is.na(extracted)),sum(is.na(extracted)),sqrt(mean((extracted-observed)^2,na.rm=TRUE))))
cell<-as.data.frame(raster,xy=TRUE);names(cell)[3]<-"raster_value"
fig<-ggplot2::ggplot(cell,ggplot2::aes(x,y,fill=raster_value))+ggplot2::geom_raster()+ggplot2::geom_point(data=sf::st_drop_geometry(projected),ggplot2::aes(.x,.y),inherit.aes=FALSE)+ggplot2::coord_equal()+ggplot2::theme_bw()+ggplot2::labs(title="已确认栅格与实际投影采样位置")
.te_table("primary",primary,"真实terra栅格提取及原暴露对齐");.te_table("diagnostics",diag,"实际覆盖与对齐误差");.te_plot("figure",fig,"真实栅格值地图")
.ts_note("由冻结栅格JSON构造 terra SpatRaster，sf转换到栅格CRS，terra extract按实际像元提取；列值用来真实核对，不假作重新提取。",paste("采样点",nrow(primary),"；有效匹配",sum(primary$matched),"。时间列仅记录当前栅格的采样标签，不假定时间变换栅格。"))
'''

DISTANCE = r'''
.step<-"geodesic-accessibility"
group_v<-.mt_get("group_v");df<-.mt_data(c(lon_v,lat_v,flon_v,flat_v,group_v))
origin<-cbind(.mt_numeric(df[[lon_v]],"起点经度"),.mt_numeric(df[[lat_v]],"起点纬度"));facilities<-unique(cbind(.mt_numeric(df[[flon_v]],"设施经度"),.mt_numeric(df[[flat_v]],"设施纬度")))
if(any(abs(origin[,1])>180 | abs(origin[,2])>90) || any(abs(facilities[,1])>180 | abs(facilities[,2])>90)) stop("WGS84经纬度越界")
distances<-geosphere::distm(origin,facilities,fun=geosphere::distGeo)
nearest<-apply(distances,1,which.min);threshold<-.mt_get("threshold_v",5000);if(!is.numeric(threshold) || length(threshold)!=1 || threshold<=0) stop("覆盖阈值米须正值")
primary<-data.frame(origin=seq_len(nrow(df)),longitude=origin[,1],latitude=origin[,2],nearest_facility=nearest,distance_m=distances[cbind(seq_len(nrow(df)),nearest)],covered=apply(distances,1,min)<=threshold)
primary$group<-if(length(group_v)) as.character(df[[group_v]]) else "overall"
access<-do.call(rbind,lapply(split(primary,primary$group),function(z) data.frame(group=z$group[1],n=nrow(z),median_distance_m=median(z$distance_m),covered=sum(z$covered),coverage=mean(z$covered),threshold_m=threshold)))
points<-sf::st_as_sf(primary,coords=c("longitude","latitude"),crs=4326);facility_sf<-sf::st_as_sf(data.frame(x=facilities[,1],y=facilities[,2]),coords=c("x","y"),crs=4326)
fig<-ggplot2::ggplot(points)+ggplot2::geom_sf(ggplot2::aes(colour=covered))+ggplot2::geom_sf(data=facility_sf,shape=8,size=3)+ggplot2::theme_bw()+ggplot2::labs(title="WGS84真实最近设施与直线距离覆盖")
.te_table("primary",primary,"实际大地测量最近设施距离");.te_table("access",access,"实际分组距离覆盖");.te_plot("figure",fig,"真实点位覆盖地图")
.ts_note("geosphere distGeo WGS84椭球最短大地线；设施集合实际去重，按所有设施找最近点。",paste("起点",nrow(primary),"；设施",nrow(facilities),"；阈值",threshold,"米。不把直线距离称为道路通达时间。"))
'''

SPATIAL_REGRESSION = r'''
.step<-"explicit-SAR-SEM"
adjacency_v<-.mt_get("adjacency_v");weights<-as.character(.mt_get("weights_v",character(0)));df<-.mt_data(c(region_v,outcome_v,exposure_v,coordinate_v,adjacency_v,weights))
df$.y<-.mt_numeric(df[[outcome_v]],"连续空间终点");df$.x<-.mt_numeric(df[[exposure_v]],"空间暴露")
if(length(weights)) {
 W<-as.matrix(df[,weights,drop=FALSE]);if(!is.numeric(W) || ncol(W)!=nrow(W) || any(W<0) || any(diag(W)!=0) || any(abs(W-t(W))>1e-8) || any(rowSums(W)==0)) stop("权重列须组成地区行顺序对应的非负对称零对角非孤立n×n矩阵")
 listw<-spdep::mat2listw(W,style="W")
 if(length(adjacency_v)) stop("权重矩阵与邻接列表只能选其一，不能静默覆盖")
} else {if(is.null(adjacency_v)) stop("空间模型需要实际邻接列表或完整权重矩阵");listw<-.ts_weight(df[[region_v]],df[[adjacency_v]])}
ols<-lm(.y~.x,df);sar<-spatialreg::lagsarlm(.y~.x,data=df,listw=listw);sem<-spatialreg::errorsarlm(.y~.x,data=df,listw=listw)
models<-list(OLS=ols,SAR=sar,SEM=sem);comparison<-data.frame(model=names(models),AIC=vapply(models,AIC,numeric(1)),logLik=vapply(models,function(z) as.numeric(logLik(z)),numeric(1)))
tables<-lapply(names(models),function(n) {
 z<-models[[n]];c<-if(n=="OLS") coef(summary(z)) else summary(z)$Coef
 data.frame(model=n,term=rownames(c),estimate=c[,1],SE=c[,2],p=c[,ncol(c)])
});primary<-do.call(rbind,tables);points<-.ts_sf(df,coordinate_v);points$.residual<-residuals(sar)
fig<-ggplot2::ggplot(points)+ggplot2::geom_sf(ggplot2::aes(colour=.residual),size=3)+ggplot2::theme_bw()+ggplot2::labs(title="实际SAR残差地理点地图")
moran<-spdep::moran.test(residuals(sar),listw)
.te_table("primary",primary,"实际OLS/SAR/SEM系数");.te_table("comparison",comparison,"同样本模型AIC比较");.te_plot("figure",fig,"真实SAR残差地图")
.ts_note("spatialreg Gaussian SAR lag 与 SEM error、已确认行标准化邻接；OLS同样本比较。",paste("SAR rho=",sar$rho,"，SEM lambda=",sem$lambda,"；SAR暴露系数非总边际影响，未冒称溢出/因果效应。"),paste("SAR残差Moran P=",moran$p.value))
'''

POINT_PATTERN = r'''
.step<-"explicit-planar-window"
covars<-as.character(.mt_get("covars_v",character(0)));mark_v<-.mt_get("mark_v");time_v<-.mt_get("time_v")
df<-.mt_data(c(x_v,y_v,covars,mark_v,time_v));x<-.mt_numeric(df[[x_v]],"投影X");y<-.mt_numeric(df[[y_v]],"投影Y")
window<-.ts_spec(window_v,"研究窗")
if(!all(c("xrange","yrange","units") %in% names(window)) || length(setdiff(names(window),c("xrange","yrange","units")))) stop("研究窗JSON需xrange/yrange两有限数范围与units，平面投影坐标")
w<-spatstat.geom::owin(xrange=unlist(window$xrange),yrange=unlist(window$yrange));if(any(!spatstat.geom::inside.owin(x,y,w))) stop("采样点超出已确认研究窗")
if(length(time_v) && length(unique(df[[time_v]]))!=1) stop("点过程每次一个时间切片")
if(length(covars)) stop("点位属性不等同空间全域协变量表面；当前点过程不静默用点属性拟合全域强度")
marks<-if(length(mark_v)) factor(df[[mark_v]]) else NULL
pattern<-spatstat.geom::ppp(x,y,window=w,marks=marks,checkdup=TRUE)
if(nrow(df)<15) stop("至少十五个研究窗内点")
fit<-spatstat.model::ppm(spatstat.geom::unmark(pattern),~1);K<-spatstat.explore::Kest(pattern,correction="isotropic")
primary<-data.frame(test="homogeneous Poisson intensity",points=nrow(df),window_area=spatstat.geom::area.owin(w),intensity=exp(coef(fit)),SE_log_intensity=sqrt(diag(vcov(fit))),units=as.character(window$units))
if(length(mark_v)) {n<-table(marks);primary<-rbind(primary,data.frame(test=paste0("observed mark:",names(n)),points=as.numeric(n),window_area=spatstat.geom::area.owin(w),intensity=as.numeric(n)/spatstat.geom::area.owin(w),SE_log_intensity=NA_real_,units=as.character(window$units)))}
curve<-data.frame(r=K$r,estimate=K$iso,theoretical=K$theo)
fig<-ggplot2::ggplot(curve,ggplot2::aes(r,estimate))+ggplot2::geom_line()+ggplot2::geom_line(ggplot2::aes(y=theoretical),linetype=2)+ggplot2::theme_bw()+ggplot2::labs(title="研究窗边缘校正 Ripley K 与 Poisson参考")
.te_table("primary",primary,"实际点数/研究窗强度模型");.te_plot("figure",fig,"真实K函数")
.te_text("text",paste("spatstat ppp 与明确平面研究窗；齐次Poisson强度与 isotropic 边缘校正K。坐标单位",window$units,"；K偏离不自动作显著聚集结论，未计算CSR模拟包络。"),"真实点过程解释")
'''

NETWORK_GRAPH = r'''
.step<-"actual-edge-network"
weight_v<-.mt_get("weight_v");attributes<-as.character(attributes_v);df<-.mt_data(c(node_v,edge_v,attributes,weight_v))
source<-as.character(df[[node_v]]);target<-as.character(df[[edge_v]]);weight<-if(length(weight_v)) .mt_numeric(df[[weight_v]],"边强度") else rep(1,nrow(df))
if(any(weight<=0)) stop("网络强度权重严格正值；最短路径采用1/强度")
vertices<-unique(c(source,target));g<-igraph::graph_from_data_frame(data.frame(from=source,to=target,weight=weight),directed=FALSE,vertices=vertices)
g<-igraph::simplify(g,remove.multiple=TRUE,remove.loops=FALSE,edge.attr.comb=list(weight="sum"))
community<-igraph::cluster_louvain(g,weights=igraph::E(g)$weight)
central<-data.frame(node=igraph::V(g)$name,degree=igraph::degree(g),strength=igraph::strength(g),betweenness=igraph::betweenness(g,weights=1/igraph::E(g)$weight),eigenvector=igraph::eigen_centrality(g,weights=igraph::E(g)$weight)$vector,community=as.integer(igraph::membership(community)))
for(v in attributes) {
 values<-vapply(central$node,function(id) {x<-unique(as.character(df[source==id,v]));if(length(x)>1) stop("节点属性在同一源节点必须一致");if(!length(x)) NA_character_ else x},character(1));central[[paste0("attribute_",v)]]<-values
}
set.seed(.mt_get("seed_v",20261008));layout<-igraph::layout_with_fr(g,weights=igraph::E(g)$weight);nodes<-cbind(central,x=layout[,1],y=layout[,2]);edges<-igraph::as_data_frame(g,what="edges")
edges$x<-nodes$x[match(edges$from,nodes$node)];edges$y<-nodes$y[match(edges$from,nodes$node)];edges$xend<-nodes$x[match(edges$to,nodes$node)];edges$yend<-nodes$y[match(edges$to,nodes$node)]
fig<-ggplot2::ggplot(nodes,ggplot2::aes(x,y))+ggplot2::geom_segment(data=edges,ggplot2::aes(xend=xend,yend=yend,linewidth=weight),alpha=.3)+ggplot2::geom_point(ggplot2::aes(colour=factor(community),size=strength))+ggplot2::geom_text(ggplot2::aes(label=node),nudge_y=.05)+ggplot2::theme_void()
metrics<-data.frame(vertices=igraph::vcount(g),edges=igraph::ecount(g),density=igraph::edge_density(g),components=igraph::components(g)$no,modularity=igraph::modularity(community))
.te_plot("figure",fig,"真实边强度网络");.te_table("central",central,"实际中心性、属性与Louvain社区");.te_table("metrics",metrics,"实际网络规模/密度/模块度")
.ts_note("igraph无向网络；重复边强度合并、权重最短路为1/强度；Louvain社区，实际节点属性按源节点确认并保留。",paste("节点",igraph::vcount(g),"、边",igraph::ecount(g),"；最高强度节点",central$node[which.max(central$strength)],"。拓扑中心性不自动代表因果或研究质量。"))
'''

KNOWLEDGE_GRAPH = r'''
.step<-"provided-relational-graph"
weight_v<-.mt_get("weight_v");df<-.mt_data(c(source_v,target_v,relation_v,weight_v))
edges<-data.frame(source=as.character(df[[source_v]]),target=as.character(df[[target_v]]),relation=as.character(df[[relation_v]]),weight=if(length(weight_v)) .mt_numeric(df[[weight_v]],"关系强度") else 1)
if(any(edges$weight<=0) || any(!nzchar(edges$relation))) stop("关系非空、强度须正值")
edges<-aggregate(edges$weight,list(source=edges$source,target=edges$target,relation=edges$relation),sum);names(edges)[4]<-"weight"
g<-igraph::graph_from_data_frame(edges,directed=TRUE);set.seed(.mt_get("seed_v",20261008));lay<-igraph::layout_with_fr(g)
nodes<-data.frame(node=igraph::V(g)$name,x=lay[,1],y=lay[,2],in_degree=igraph::degree(g,mode="in"),out_degree=igraph::degree(g,mode="out"),betweenness=igraph::betweenness(g,weights=1/igraph::E(g)$weight,directed=TRUE))
edges$x<-nodes$x[match(edges$source,nodes$node)];edges$y<-nodes$y[match(edges$source,nodes$node)];edges$xend<-nodes$x[match(edges$target,nodes$node)];edges$yend<-nodes$y[match(edges$target,nodes$node)]
fig<-ggplot2::ggplot(nodes,ggplot2::aes(x,y))+ggplot2::geom_segment(data=edges,ggplot2::aes(xend=xend,yend=yend,colour=relation),arrow=grid::arrow(length=grid::unit(.15,"cm")),alpha=.5)+ggplot2::geom_point(size=3)+ggplot2::geom_text(ggplot2::aes(label=node),nudge_y=.08)+ggplot2::theme_void()
edges$source_in_degree<-nodes$in_degree[match(edges$source,nodes$node)];edges$target_out_degree<-nodes$out_degree[match(edges$target,nodes$node)]
.te_table("primary",edges,"用户提供关系与真实有向拓扑");.te_plot("figure",fig,"真实有向实体关系图")
.ts_note("igraph有向关系多图，按source/target/relation聚合真实边强度，未从文本自动发明关系。",paste("实体",nrow(nodes),"、关系",nrow(edges),"；最高介数节点",nodes$node[which.max(nodes$betweenness)],"。仅确认存在的方向关系，未推断语义真伪或隐藏路径。"))
'''

EVENT_SEQUENCE = r'''
.step<-"observed-event-paths"
group_v<-.mt_get("group_v");df<-.mt_data(c(subject_v,time_v,event_v,group_v))
time<-df[[time_v]];if(is.character(time)) time<-as.POSIXct(time,tz="UTC")
df$.time<-as.numeric(time);if(any(!is.finite(df$.time)) || anyDuplicated(df[,c(subject_v,time_v)])) stop("事件时间须可解析且受试者内唯一；同时事件需先明确顺序")
paths<-list();transitions<-list()
for(id in unique(as.character(df[[subject_v]]))) {
 d<-df[as.character(df[[subject_v]])==id,];d<-d[order(d$.time),];group<-if(length(group_v)) unique(as.character(d[[group_v]])) else "overall"
 if(length(group)!=1) stop("事件分组须受试者内固定")
 events<-as.character(d[[event_v]]);paths[[length(paths)+1]]<-data.frame(subject=id,group=group,path=paste(events,collapse="→"),events=length(events))
 if(length(events)>1) transitions[[length(transitions)+1]]<-data.frame(group=group,from=head(events,-1),to=tail(events,-1),elapsed=diff(d$.time))
}
paths<-do.call(rbind,paths);frequency<-aggregate(rep(1,nrow(paths)),list(group=paths$group,path=paths$path),sum);names(frequency)[3]<-"subjects"
frequency<-frequency[order(-frequency$subjects),]
if(!length(transitions)) stop("至少一个受试者有两个事件才能估计转移")
tr<-do.call(rbind,transitions);counts<-aggregate(rep(1,nrow(tr)),list(group=tr$group,from=tr$from,to=tr$to),sum);names(counts)[4]<-"transitions"
counts$conditional_probability<-counts$transitions/ave(counts$transitions,counts$group,counts$from,FUN=sum)
durations<-aggregate(tr$elapsed,list(group=tr$group,from=tr$from,to=tr$to),median);names(durations)[4]<-"median_elapsed"
counts<-merge(counts,durations,by=c("group","from","to"))
.te_table("frequency",frequency,"实际完整路径频率");.te_table("transitions",counts,"观察转移频率/条件比例/间隔");.te_table("paths",paths,"实际逐受试者序列")
.ts_note("先逐受试者按真实时间排序，再计算邻接事件转移与完整路径；同一时间事件不擅自排序。",paste("受试者",nrow(paths),"、不同路径",nrow(frequency),"；最常见",frequency$path[1],"。转移比例为描述性，未冒充Markov因果机制。"))
'''

TEXT_MINING = r'''
.step<-"document-token-features"
docs<-.ts_docs();df<-docs$data;m<-.ts_dtm(docs$dfm);if(nrow(m)>500) stop("全对全文相似度当前最多500文档")
frequency<-data.frame(term=colnames(m),count=as.numeric(Matrix::colSums(m)),documents=as.numeric(Matrix::colSums(m>0)));frequency<-frequency[order(-frequency$count),]
ngrams<-quanteda::dfm(quanteda::tokens_ngrams(docs$tokens,n=2));bigrams<-data.frame(phrase=quanteda::featnames(ngrams),count=as.numeric(Matrix::colSums(ngrams)));bigrams<-bigrams[order(-bigrams$count),]
tf<-quanteda::dfm_tfidf(docs$dfm);features<-data.frame(document=df$.id,term_count=as.numeric(Matrix::rowSums(m)),distinct_terms=as.numeric(Matrix::rowSums(m>0)),top_tfidf_term=apply(as.matrix(tf),1,function(x) colnames(tf)[which.max(x)]))
for(v in unique(c(.mt_get("group_v"),.mt_get("time_v")))) features[[v]]<-df[[v]]
sim<-text2vec::sim2(methods::as(tf,"dgCMatrix"),method="cosine",norm="l2");idx<-which(upper.tri(sim),arr.ind=TRUE)
similarity<-data.frame(document1=df$.id[idx[,1]],document2=df$.id[idx[,2]],cosine=sim[idx])
fig<-ggplot2::ggplot(head(frequency,20),ggplot2::aes(reorder(term,count),count))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw()
.te_table("frequency",frequency,"真实词频与文档频率");.te_table("bigrams",bigrams,"实际相邻双词短语");.te_table("similarity",similarity,"真实TF-IDF余弦文档相似度");.te_table("features",features,"实际文档词项特征与已选分组/时间")
.te_plot("figure",fig,"真实词频图");.te_text("summary",paste("最高频词：",paste(head(frequency$term,10),collapse=","),"；这是实际词项摘要，未冒充主题模型或监督分类。"),"实际词项摘要")
.te_text("cleaning",paste("quanteda小写词分词，去标点符号，保留数字；模式",docs$language,"；english仅删除英文停用词，whitespace要求输入已按研究规则空格分词。不自动伪造中文词边界。"),"明确清洗规则")
'''

TOPIC_EMBEDDING = r'''
.step<-"actual-latent-semantic-space"
docs<-.ts_docs();df<-docs$data;m<-.ts_dtm(docs$dfm)
rank<-.mt_integer(.mt_get("dimensions_v",3),2,min(nrow(m)-1,ncol(m)-1,50),"LSA维度")
tf<-text2vec::TfIdf$new();weighted<-tf$fit_transform(m);lsa<-text2vec::LatentSemanticAnalysis$new(n_topics=rank)
embedding<-lsa$fit_transform(weighted);clusters<-.mt_integer(.mt_get("clusters_v",2),2,min(20,nrow(m)-1),"文档聚类数")
set.seed(.mt_get("seed_v",20261008));km<-kmeans(embedding,centers=clusters,nstart=20)
documents<-data.frame(document=df$.id,cluster=km$cluster,LSA1=embedding[,1],LSA2=embedding[,2])
for(v in unique(c(.mt_get("group_v"),.mt_get("time_v")))) documents[[v]]<-df[[v]]
topics<-do.call(rbind,lapply(seq_len(clusters),function(k) {freq<-Matrix::colMeans(m[km$cluster==k,,drop=FALSE]);top<-head(order(freq,decreasing=TRUE),10);data.frame(cluster=k,term=colnames(m)[top],mean_count=as.numeric(freq[top]))}))
similarity<-text2vec::sim2(embedding,method="cosine",norm="l2");fig<-ggplot2::ggplot(documents,ggplot2::aes(LSA1,LSA2,colour=factor(cluster)))+ggplot2::geom_point()+ggplot2::theme_bw()+ggplot2::labs(title="实际TF-IDF LSA文档嵌入")
.te_table("topics",topics,"实际LSA聚类代表词");.te_table("documents",documents,"实际降维、聚类及元数据");.te_plot("figure",fig,"实际潜在语义嵌入图")
.ts_note("text2vec TF-IDF+LatentSemanticAnalysis 截断SVD，实际嵌入kmeans；保留已选分组/时间。",paste("文档",nrow(m),"，LSA维度",rank,"，聚类",clusters,"。这是本地语料LSA主题近似，未称为预训练Transformer语义或生成式主题解释。"),paste("kmeans total within SS=",km$tot.withinss,"；重复文本的距离可为0，不发明聚类稳定性。"))
'''

SENTIMENT = r'''
.step<-"lexicon-coverage"
docs<-.ts_docs();df<-docs$data
if(docs$language!="english") stop("当前syuzhet英文Bing词典；其他语言须明确词典接口，不能使用英文未覆盖结果")
dictionary<-syuzhet::get_sentiment_dictionary("bing");tokens<-as.list(docs$tokens)
coverage<-vapply(tokens,function(x) sum(x %in% dictionary$word),integer(1));scores<-syuzhet::get_sentiment(df$.text,method="bing")
scores[coverage==0]<-NA_real_;if(all(is.na(scores))) stop("没有任何情感词典覆盖，无法返回有效情感结论")
primary<-data.frame(document=df$.id,score=scores,matched_tokens=coverage,token_count=lengths(tokens),lexicon="English Bing via syuzhet",sentiment=ifelse(is.na(scores),"uncovered",ifelse(scores>0,"positive",ifelse(scores<0,"negative","neutral"))))
for(v in unique(c(.mt_get("group_v"),.mt_get("time_v")))) primary[[v]]<-df[[v]]
if(length(.mt_get("label_v"))) {primary$manual_label<-as.character(df[[label_v]]);if(any(!primary$manual_label %in% c("positive","negative","neutral"))) stop("人工标签须positive/negative/neutral");primary$correct<-ifelse(is.na(scores),NA,primary$manual_label==primary$sentiment)}
fig<-if(length(.mt_get("time_v"))) ggplot2::ggplot(primary,ggplot2::aes(x=.data[[time_v]],y=score,colour=if(length(.mt_get("group_v"))) .data[[group_v]] else sentiment))+ggplot2::geom_point()+ggplot2::theme_bw() else ggplot2::ggplot(primary,ggplot2::aes(score,fill=sentiment))+ggplot2::geom_histogram(bins=20)+ggplot2::theme_bw()
.te_table("primary",primary,"真实词典情感与覆盖/人工标签");.te_plot("figure",fig,"实际有效情感分布/时间")
.te_text("text",paste("syuzhet English Bing词典正负词加总；文档",nrow(primary),"，有效覆盖",sum(coverage>0),"，未覆盖明确NA；不把词典0分和未覆盖混为一谈。",if(length(.mt_get("label_v"))) paste("已覆盖人工标签符合率",mean(primary$correct,na.rm=TRUE)) else "没有人工标签验证。","否定、讽刺、领域语言是当前词典方法限制。"),"实际词典范围与有效覆盖")
'''

TEXT_CLASSIFICATION = r'''
.step<-"train-only-text-vocabulary"
docs<-.ts_docs();df<-docs$data;classes<-factor(df[[label_v]]);if(nlevels(classes)!=2 || any(table(classes)<15)) stop("当前文本分类为至少每类十五文档的二分类Elastic-Net")
seed<-.mt_get("seed_v",20261008);set.seed(seed)
if(length(.mt_get("time_v"))) {
 order<-order(df[[time_v]]);train<-head(order,floor(nrow(df)*.7));test<-setdiff(seq_len(nrow(df)),train)
 if(length(.mt_get("cluster_v")) && length(intersect(unique(df[[cluster_v]][train]),unique(df[[cluster_v]][test])))) stop("时间拆分跨相同cluster，须调整验证设计")
} else if(length(.mt_get("cluster_v"))) {
 groups<-sample(unique(df[[cluster_v]]));train<-which(df[[cluster_v]] %in% head(groups,floor(length(groups)*.7)));test<-setdiff(seq_len(nrow(df)),train)
} else {train<-unlist(lapply(split(seq_len(nrow(df)),classes),function(i) sample(i,floor(length(i)*.7))));test<-setdiff(seq_len(nrow(df)),train)}
if(length(unique(classes[train]))!=2 || length(unique(classes[test]))!=2) stop("训练与留出都需两个类别")
if(length(intersect(df$.text[train],df$.text[test]))) stop("训练与留出存在完全重复文本，需去重或提供能隔离重复文本的cluster设计")
training_dfm<-quanteda::dfm(docs$tokens[train]);test_dfm<-quanteda::dfm_match(quanteda::dfm(docs$tokens[test]),features=quanteda::featnames(training_dfm))
train_m<-.ts_dtm(training_dfm);test_m<-methods::as(test_dfm,"dgCMatrix");transform<-text2vec::TfIdf$new();X<-transform$fit_transform(train_m);Xt<-transform$transform(test_m)
foldid<-if(length(.mt_get("cluster_v"))) {g<-as.factor(df[[cluster_v]][train]);assignment<-sample(rep(1:5,length.out=nlevels(g)));assignment[as.integer(g)]} else sample(rep(1:5,length.out=length(train)))
if(length(unique(foldid))<3) stop("训练集cluster至少三组才能按组CV")
alpha<-.mt_get("alpha_v",1);if(!is.numeric(alpha) || length(alpha)!=1 || !is.finite(alpha) || alpha<0 || alpha>1) stop("Elastic-Net混合权重须在[0,1]")
fit<-glmnet::cv.glmnet(X,as.integer(classes[train])-1,family="binomial",alpha=alpha,foldid=foldid)
prob<-as.numeric(predict(fit,newx=Xt,s="lambda.min",type="response"));pred<-ifelse(prob>=.5,levels(classes)[2],levels(classes)[1]);truth<-as.character(classes[test])
confusion<-as.data.frame(table(truth=factor(truth,levels=levels(classes)),prediction=factor(pred,levels=levels(classes))));names(confusion)[3]<-"n"
auc<-as.numeric(pROC::auc(pROC::roc(as.integer(classes[test])-1,prob,levels=c(0,1),direction="<",quiet=TRUE)))
metrics<-data.frame(section="holdout metrics",item=c("accuracy","AUC","Brier","train n","test n"),value=c(mean(pred==truth),auc,mean((prob-(as.integer(classes[test])-1))^2),length(train),length(test)))
cf<-as.matrix(coef(fit,s="lambda.min"));features<-data.frame(section="train-only vocabulary coefficient",item=rownames(cf),value=cf[,1]);features<-features[features$value!=0,]
primary<-rbind(metrics,features);fig<-ggplot2::ggplot(confusion,ggplot2::aes(truth,prediction,fill=n))+ggplot2::geom_tile()+ggplot2::geom_text(ggplot2::aes(label=n))+ggplot2::theme_bw()+ggplot2::labs(title="真实独立留出混淆矩阵")
.te_table("primary",primary,"实际留出性能与训练特征权重");.te_plot("figure",fig,"实际二分类混淆矩阵")
.te_text("text",paste("仅训练文档建词表、IDF与glmnet CV；留出词表对齐不重拟合IDF。训练",length(train),"、留出",length(test),"；AUC",auc,"。提供time时采用时间末段，提供cluster时防同cluster跨拆分并按clusterCV；未使用标签拟合全数据特征。"),"实际文本分类与泄漏边界")
'''

ENTITY_RELATION = r'''
.step<-"confirmed-extraction-rules"
docs<-.ts_docs();df<-docs$data;spec<-.ts_spec(dictionary_v,"实体/关系规则")
if(!all(c("entities","relations") %in% names(spec)) || length(setdiff(names(spec),c("entities","relations")))) stop("实体JSON需entities（类型→文本列表）及relations（关系→带source/target命名捕获的正则）")
entities<-list();edges<-list()
for(i in seq_len(nrow(df))) {
 for(type in names(spec$entities)) for(term in unlist(spec$entities[[type]])) {
  hits<-gregexpr(as.character(term),df$.text[i],fixed=TRUE)[[1]]
  if(hits[1]>0) for(pos in hits) entities[[length(entities)+1]]<-data.frame(document=df$.id[i],record="entity",source=as.character(term),target="",relation=type,position=pos)
 }
 for(relation in names(spec$relations)) {
  pattern<-as.character(spec$relations[[relation]]);hits<-gregexpr(pattern,df$.text[i],perl=TRUE)[[1]]
  capture<-attr(hits,"capture.start");lengths<-attr(hits,"capture.length");names<-attr(hits,"capture.names")
  if(hits[1]>0) {
   if(!all(c("source","target") %in% names)) stop("关系正则须有source与target命名捕获")
   for(j in seq_along(hits)) {
    source<-substr(df$.text[i],capture[j,"source"],capture[j,"source"]+lengths[j,"source"]-1)
    target<-substr(df$.text[i],capture[j,"target"],capture[j,"target"]+lengths[j,"target"]-1)
    edges[[length(edges)+1]]<-data.frame(document=df$.id[i],record="relation",source=source,target=target,relation=relation,position=hits[j])
   }
  }
 }
}
if(!length(entities) && !length(edges)) stop("文本未命中已确认实体/关系规则，不生成虚构实体")
primary<-do.call(rbind,c(entities,edges))
if(length(.mt_get("time_v"))) primary$time<-df[[time_v]][match(primary$document,df$.id)]
if(length(.mt_get("annotation_v"))) {
 gold<-vapply(as.character(df[[annotation_v]]),function(x) {z<-.ts_spec(x,"人工标注");if(is.null(z$entities)) stop("人工标注JSON需entities文本数组");paste(sort(unique(unlist(z$entities))),collapse="|")},character(1))
 extracted<-vapply(df$.id,function(id) paste(sort(unique(primary$source[primary$document==id & primary$record=="entity"])),collapse="|"),character(1))
 primary$manual_entity_exact_match<-gold[match(primary$document,df$.id)]==extracted[match(primary$document,df$.id)]
}
links<-primary[primary$record=="relation",];if(!nrow(links)) stop("未命中关系规则，不用共现边冒充关系图")
g<-igraph::graph_from_data_frame(links[,c("source","target","relation")],directed=TRUE);set.seed(.mt_get("seed_v",20261008));layout<-igraph::layout_with_fr(g)
nodes<-data.frame(node=igraph::V(g)$name,x=layout[,1],y=layout[,2]);links$x<-nodes$x[match(links$source,nodes$node)];links$y<-nodes$y[match(links$source,nodes$node)];links$xend<-nodes$x[match(links$target,nodes$node)];links$yend<-nodes$y[match(links$target,nodes$node)]
fig<-ggplot2::ggplot(nodes,ggplot2::aes(x,y))+ggplot2::geom_segment(data=links,ggplot2::aes(xend=xend,yend=yend,colour=relation),arrow=grid::arrow(length=grid::unit(.15,"cm")))+ggplot2::geom_point()+ggplot2::geom_text(ggplot2::aes(label=node),nudge_y=.05)+ggplot2::theme_void()
.te_table("primary",primary,"规则命中的实体位置与真实关系边");.te_plot("figure",fig,"明确关系正则命中的实体关系")
.te_text("text",paste("使用用户已确认词典字面实体与命名source/target捕获关系规则，实际关系",nrow(links),"条。未用关键词共现伪装语义关系；未提供UDPipe训练模型时不假装模型NER。人工标注若选入仅做实际实体集合精确核对。"),"明确可复现抽取规则")
'''

TOPIC_STABILITY = r'''
.step<-"actual-LDA-repeated-fits"
docs<-.ts_docs();df<-docs$data;m<-.ts_dtm(docs$dfm)
K<-.mt_integer(.mt_get("topics_v",3),2,min(20,ncol(m)-1,nrow(m)-1),"主题数");runs<-.mt_integer(.mt_get("runs_v",3),2,10,"重复拟合数");iterations<-.mt_integer(.mt_get("iterations_v",300),100,3000,"LDA迭代数")
seed<-.mt_get("seed_v",20261008);models<-list();proportions<-list()
for(i in seq_len(runs)) {
 set.seed(seed+i);model<-text2vec::LDA$new(n_topics=K,doc_topic_prior=.1,topic_word_prior=.01)
 proportions[[i]]<-model$fit_transform(m,n_iter=iterations,progressbar=FALSE);models[[i]]<-model
}
phi<-models[[1]]$topic_word_distribution;top_words<-models[[1]]$get_top_words(n=10,lambda=1)
stability<-list()
for(i in 2:runs) {
 other<-models[[i]]$topic_word_distribution;similarity<-text2vec::sim2(phi,other,method="cosine",norm="l2")
 assignment<-as.integer(clue::solve_LSAP(similarity,maximum=TRUE));stability[[length(stability)+1]]<-data.frame(record="topic stability",topic=seq_len(K),term=paste0("run",i," matched topic",assignment),value=similarity[cbind(seq_len(K),assignment)])
}
keywords<-do.call(rbind,lapply(seq_len(K),function(k) data.frame(record="topic keyword",topic=k,term=top_words[,k],value=phi[k,match(top_words[,k],colnames(phi))])))
primary<-rbind(keywords,do.call(rbind,stability));topic_docs<-data.frame(document=df$.id,topic=max.col(proportions[[1]]),max_proportion=apply(proportions[[1]],1,max))
metadata<-unique(c(.mt_get("time_v"),.mt_get("group_v"),as.character(.mt_get("covars_v",character(0)))))
for(v in metadata) topic_docs[[v]]<-df[[v]]
if(length(metadata)) {meta_summary<-aggregate(topic_docs$max_proportion,by=topic_docs[,metadata,drop=FALSE],FUN=mean);primary<-rbind(primary,data.frame(record="observed metadata proportion summary",topic=NA_integer_,term=apply(meta_summary[,metadata,drop=FALSE],1,paste,collapse="|"),value=meta_summary$x))}
fig<-ggplot2::ggplot(do.call(rbind,stability),ggplot2::aes(factor(topic),value,colour=term))+ggplot2::geom_point()+ggplot2::theme_bw()+ggplot2::labs(title="独立LDA拟合的匹配主题词分布余弦稳定性",y="cosine similarity",x="reference topic")
.te_table("primary",primary,"真实LDA关键词/重复匹配与元数据比例");.te_plot("figure",fig,"真实主题拟合稳定性")
.te_text("text",paste("text2vec collapsed Gibbs LDA；",K,"主题、",runs,"次不同冻结种子拟合、每次",iterations,"迭代；Hungarian匹配主题词分布计算稳定性。元数据仅真实描述性汇总，未假装STM协变量效应；未提供主题质量人工判断。"),"实际主题模型与稳定性")
'''

_BODIES = {
    'bayesian_state_space': BAYES_STATE,
    'gam_splines_time': GAM_TIME,
    'gis_mapping': GIS,
    'spatial_autocorrelation': AUTOCORRELATION,
    'geostatistics': "validation<-FALSE\n" + GEOSTAT_BODY,
    'network_graphs': NETWORK_GRAPH,
    'text_mining_nlp': TEXT_MINING,
    'panel_forecast': PANEL_FORECAST,
    'functional_data_analysis': FUNCTIONAL,
    'spatiotemporal_panel': SPATIOTEMPORAL,
    'raster_exposure_linkage': RASTER,
    'distance_accessibility': DISTANCE,
    'spatial_regression_models': SPATIAL_REGRESSION,
    'topic_embedding_text': TOPIC_EMBEDDING,
    'knowledge_graph': KNOWLEDGE_GRAPH,
    'event_sequence_patterns': EVENT_SEQUENCE,
    'time_var_vecm_cointegration': VAR_VECM,
    'time_hierarchical_grouped_forecast': HIERARCHICAL,
    'time_seasonal_anomaly_changepoint': ANOMALY,
    'spatial_point_pattern': POINT_PATTERN,
    'spatial_interpolation_validation': "validation<-TRUE\n" + GEOSTAT_BODY,
    'text_sentiment_analysis': SENTIMENT,
    'text_supervised_classification': TEXT_CLASSIFICATION,
    'text_entity_relation_extraction': ENTITY_RELATION,
    'text_topic_stability': TOPIC_STABILITY,
}
_HELPER_DEFINITION = re.compile(r"(?m)^(\.(?:mt|te|ts)_[A-Za-z0-9_]+)\s*<-\s*function\b")
_HELPER_REFERENCE = re.compile(r"(?<![\w.])(\.(?:mt|te|ts)_[A-Za-z0-9_]+)\b")


def _helper_definitions():
    """Index the shared, top-level R functions without changing their bodies."""
    definitions = {}
    for source in (COMMON, TIME_HELPERS, TRIAL_COMMON, TST_COMMON):
        matches = list(_HELPER_DEFINITION.finditer(source))
        if not matches or source[:matches[0].start()].strip():
            raise ValueError("Time/space/text helper source must start with a function definition")
        for index, match in enumerate(matches):
            end = matches[index+1].start() if index+1 < len(matches) else len(source)
            name = match.group(1)
            if name in definitions:
                raise ValueError("Duplicate time/space/text helper: " + name)
            definitions[name] = source[match.start():end]
    return definitions


_HELPER_BODIES = _helper_definitions()


def _helper_references(source):
    return set(_HELPER_REFERENCE.findall(_scrub_r_code(source, keep_strings=False)))


def _core_with_required_helpers(body):
    """Keep the transitive function closure used by this scientific method."""
    required = _helper_references(body)
    pending = list(required)
    while pending:
        name = pending.pop()
        if name not in _HELPER_BODIES:
            raise ValueError("Undefined time/space/text helper: " + name)
        dependencies = _helper_references(_HELPER_BODIES[name]) - {name}
        pending.extend(dependencies - required)
        required.update(dependencies)
    helpers = "\n".join(source for name,source in _HELPER_BODIES.items() if name in required)
    return helpers + "\n" + body


CORES = {key: _core_with_required_helpers(body) for key, body in _BODIES.items()}

INPUTS = {'bayesian_state_space': {'time': 'time_v', 'observed_measure': 'measure_v', 'state_trend_specification': 'spec_v', 'covariates': 'covars_v'}, 'gam_splines_time': {'exposure': 'exposure_v', 'outcome': 'outcome_v', 'group': 'group_v', 'covariates': 'covars_v', 'basis_dimension': 'k_v', 'positive_class': 'positive_v'}, 'gis_mapping': {'region_id': 'region_v', 'coordinates': 'coordinate_v', 'measure': 'measure_v', 'time': 'time_v', 'crs': 'crs_v'}, 'spatial_autocorrelation': {'region_id': 'region_v', 'measure': 'measure_v', 'adjacency': 'adjacency_v', 'denominator': 'denominator_v', 'coordinates': 'coordinate_v', 'crs': 'crs_v'}, 'geostatistics': {'coordinates': 'coordinate_v', 'measure': 'measure_v', 'prediction_grid': 'grid_v', 'covariates': 'covars_v', 'crs': 'crs_v'}, 'network_graphs': {'node_id': 'node_v', 'edge': 'edge_v', 'node_attributes': 'attributes_v', 'weight': 'weight_v', 'seed': 'seed_v'}, 'text_mining_nlp': {'text': 'text_v', 'document_id': 'id_v', 'group': 'group_v', 'time': 'time_v', 'tokenization': 'language_v'}, 'panel_forecast': {'time': 'time_v', 'panel_id': 'group_v', 'measure': 'measure_v', 'exogenous_variables': 'covars_v', 'horizon': 'horizon_v', 'frequency': 'freq_v', 'future_exogenous': 'future_v'}, 'functional_data_analysis': {'subject_id': 'subject_v', 'time': 'time_v', 'measure': 'measure_v', 'group': 'group_v', 'smoothing_lambda': 'lambda_v', 'components': 'components_v', 'clusters': 'clusters_v', 'seed': 'seed_v'}, 'spatiotemporal_panel': {'region_id': 'region_v', 'time': 'time_v', 'outcome': 'outcome_v', 'coordinates': 'coordinate_v', 'covariates': 'covars_v', 'crs': 'crs_v'}, 'raster_exposure_linkage': {'coordinates': 'coordinate_v', 'exposure': 'exposure_v', 'time': 'time_v', 'region_id': 'region_v', 'crs': 'crs_v', 'raster_spec': 'raster_v'}, 'distance_accessibility': {'origin_longitude': 'lon_v', 'origin_latitude': 'lat_v', 'facility_longitude': 'flon_v', 'facility_latitude': 'flat_v', 'group': 'group_v', 'coverage_distance_m': 'threshold_v'}, 'spatial_regression_models': {'region_id': 'region_v', 'outcome': 'outcome_v', 'exposure': 'exposure_v', 'weight': 'weights_v', 'adjacency': 'adjacency_v', 'coordinates': 'coordinate_v', 'crs': 'crs_v'}, 'topic_embedding_text': {'text': 'text_v', 'document_id': 'id_v', 'group': 'group_v', 'time': 'time_v', 'tokenization': 'language_v', 'dimensions': 'dimensions_v', 'clusters': 'clusters_v', 'seed': 'seed_v'}, 'knowledge_graph': {'source_entity': 'source_v', 'target_entity': 'target_v', 'relation': 'relation_v', 'weight': 'weight_v', 'seed': 'seed_v'}, 'event_sequence_patterns': {'subject_id': 'subject_v', 'event_time': 'time_v', 'event_type': 'event_v', 'group': 'group_v'}, 'time_var_vecm_cointegration': {'time': 'time_v', 'multiple_continuous_series': 'series_v', 'exogenous_variables': 'covars_v', 'season_variable': 'season_v', 'analysis_method': 'method_v', 'lag_order': 'lag_v', 'cointegration_rank': 'rank_v', 'horizon': 'horizon_v'}, 'time_hierarchical_grouped_forecast': {'time': 'time_v', 'measure': 'measure_v', 'group': 'group_v', 'exogenous_variables': 'covars_v', 'validation_split': 'split_v', 'horizon': 'horizon_v', 'frequency': 'freq_v', 'future_exogenous': 'future_v'}, 'time_seasonal_anomaly_changepoint': {'time': 'time_v', 'measure': 'measure_v', 'seasonal_period': 'period_v', 'intervention_indicator': 'intervention_v', 'group': 'group_v', 'robust_threshold': 'threshold_v'}, 'spatial_point_pattern': {'coordinates': 'x_v', 'coordinates_2': 'y_v', 'study_window': 'window_v', 'point_indicator': 'mark_v', 'covariates': 'covars_v', 'time': 'time_v'}, 'spatial_interpolation_validation': {'coordinates': 'coordinate_v', 'continuous_measure': 'measure_v', 'covariates': 'covars_v', 'time': 'time_v', 'region_boundary': 'boundary_v', 'crs': 'crs_v', 'validation_method': 'validation_v', 'folds': 'folds_v', 'seed': 'seed_v'}, 'text_sentiment_analysis': {'text': 'text_v', 'time': 'time_v', 'group': 'group_v', 'document_id': 'id_v', 'manual_label': 'label_v', 'tokenization': 'language_v'}, 'text_supervised_classification': {'text': 'text_v', 'class_label': 'label_v', 'document_id': 'id_v', 'time': 'time_v', 'cluster_id': 'cluster_v', 'tokenization': 'language_v', 'seed': 'seed_v', 'elastic_net_alpha': 'alpha_v'}, 'text_entity_relation_extraction': {'text': 'text_v', 'document_id': 'id_v', 'entity_dictionary': 'dictionary_v', 'manual_annotation': 'annotation_v', 'time': 'time_v', 'tokenization': 'language_v', 'seed': 'seed_v'}, 'text_topic_stability': {'text': 'text_v', 'document_id': 'id_v', 'time': 'time_v', 'group': 'group_v', 'covariates': 'covars_v', 'tokenization': 'language_v', 'topics': 'topics_v', 'runs': 'runs_v', 'iterations': 'iterations_v', 'seed': 'seed_v'}}

ROLE_MAPS = {'bayesian_state_space': {'figure_1': 'figure', 'table_1': 'dynamic', 'table_2': 'prediction', 'figure_2': 'figure', 'table_3': 'states', 'table_4': 'limits', 'scientific_result_interpretation': 'state_interpretation'}, 'gam_splines_time': {'figure_1': 'figure', 'diagnostic_output': 'diagnostics', 'manuscript_results': 'text', 'table_2': 'model', 'table_3': 'prediction', 'manuscript_results_2': 'text'}, 'gis_mapping': {'figure_1': 'figure', 'table_1': 'connection', 'table_2': 'indicators', 'table_3': 'checks', 'table_4': 'indicators', 'figure_2': 'caption'}, 'spatial_autocorrelation': {'table_1': 'global', 'figure_1': 'figure', 'table_2': 'local', 'table_3': 'global', 'figure_2': 'figure', 'manuscript_results': 'text'}, 'geostatistics': {'table_1': 'variogram', 'figure_1': 'figure', 'table_2': 'prediction', 'table_3': 'variogram', 'text_1': 'text'}, 'network_graphs': {'figure_1': 'figure', 'table_1': 'central', 'manuscript_results': 'text', 'table_2': 'metrics', 'table_3': 'central', 'manuscript_results_2': 'text'}, 'text_mining_nlp': {'table_1': 'frequency', 'table_2': 'bigrams', 'table_3': 'similarity', 'table_4': 'features', 'table_5': 'frequency', 'text_1': 'summary', 'figure_1': 'figure', 'text_2': 'cleaning'}, 'panel_forecast': {'figure_1': 'figure', 'table_1': 'prediction', 'manuscript_results': 'text', 'method_note': 'method', 'manuscript_results_2': 'text', 'diagnostic_output': 'diagnostics'}, 'functional_data_analysis': {'figure_1': 'figure', 'table_1': 'pca', 'figure_2': 'clusters', 'method_note': 'method', 'manuscript_results': 'text', 'diagnostic_output': 'diagnostics'}, 'spatiotemporal_panel': {'figure_1': 'figure', 'figure_2': 'map', 'table_1': 'model', 'method_note': 'method', 'manuscript_results': 'text', 'diagnostic_output': 'diagnostics'}, 'raster_exposure_linkage': {'table_1': 'primary', 'diagnostic_output': 'diagnostics', 'figure_1': 'figure', 'method_note': 'method', 'manuscript_results': 'text'}, 'distance_accessibility': {'table_1': 'primary', 'figure_1': 'figure', 'table_2': 'access', 'method_note': 'method', 'manuscript_results': 'text', 'diagnostic_output': 'diagnostics'}, 'spatial_regression_models': {'table_1': 'primary', 'figure_1': 'figure', 'table_2': 'comparison', 'method_note': 'method', 'manuscript_results': 'text', 'diagnostic_output': 'diagnostics'}, 'topic_embedding_text': {'table_1': 'topics', 'table_2': 'documents', 'figure_1': 'figure', 'manuscript_results': 'text', 'method_note': 'method', 'manuscript_results_2': 'text', 'diagnostic_output': 'diagnostics'}, 'knowledge_graph': {'table_1': 'primary', 'figure_1': 'figure', 'manuscript_results': 'text', 'method_note': 'method', 'manuscript_results_2': 'text', 'diagnostic_output': 'diagnostics'}, 'event_sequence_patterns': {'table_1': 'frequency', 'table_2': 'transitions', 'table_3': 'paths', 'method_note': 'method', 'manuscript_results': 'text', 'diagnostic_output': 'diagnostics'}, 'time_var_vecm_cointegration': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}, 'time_hierarchical_grouped_forecast': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}, 'time_seasonal_anomaly_changepoint': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}, 'spatial_point_pattern': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}, 'spatial_interpolation_validation': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}, 'text_sentiment_analysis': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}, 'text_supervised_classification': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}, 'text_entity_relation_extraction': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}, 'text_topic_stability': {'primary_table': 'primary', 'diagnostic_plot': 'figure', 'manuscript_results': 'text'}}

REFERENCE_CASES = {
    'bayesian_state_space': '真实 dlm Gaussian 趋势 Gibbs、IG 方差先验；72点/200次Gibbs与100个保留样本状态后验和含观测噪声的未来预测区间，明确固定外生系数。',
    'gam_splines_time': 'mgcv REML 平滑及调整协变量系数独立参考，真实 edf/p、曲线点区间；二分类采用 logit。',
    'gis_mapping': '真实 sf EPSG3857 点几何、WKT/坐标连接、有效性与指标汇总；点地图，不冒充行政区多边形。',
    'spatial_autocorrelation': '明确对称邻接/行标准化 spdep 全局 Moran 独立参考、真实局部 Moran BH 与点位 LISA。',
    'geostatistics': '真实投影 CRS、gstat Sph/Exp/Gau 经验变差函数拟合与普通克里金；预测均值独立参考及非负方差核验。',
    'network_graphs': '真实 igraph 加权无向网络、平行边合并、Louvain、中心性与独立 degree 参考；边强度倒数用于路径。',
    'text_mining_nlp': 'quanteda 真实词频/双词/TF-IDF 文档特征与1770文档对余弦；真实总词频一致，明确英文或已分词空格输入。',
    'panel_forecast': '两面板真实 ARIMA、末段留出 MAE/RMSE 和 refit 预测独立参考；外生分支必须有真实未来矩阵。',
    'functional_data_analysis': 'fda B样条平滑与 FPCA 解释率独立参考，12受试者曲线/分数/聚类；完整共同观测网格。',
    'spatiotemporal_panel': '真实区域固定截距/时间趋势/协变量 Gaussian 同样本系数参考；有坐标及 CRS 才输出 sf 地图，无坐标不造地图。',
    'raster_exposure_linkage': '真实 terra 2×2 Raster 与 CRS 变换后逐点提取四值、暴露差值/覆盖率；明确冻结栅格JSON。',
    'distance_accessibility': '真实 WGS84 椭球距离 geosphere 独立参考、最近设施与阈值覆盖；距离单位米，不伪作路网时间。',
    'spatial_regression_models': '实际 OLS/SAR lag/SEM error、同样本 AIC、SAR rho 独立参考及残差 Moran/空间点图。',
    'topic_embedding_text': 'text2vec TF-IDF/LSA-SVD 真实二维嵌入、聚类和代表词；不冒充 Transformer 语义向量。',
    'knowledge_graph': '真实已提供的有向关系多重图和实体/边属性，方向/四边/三类关系核验；不发明关系语义。',
    'event_sequence_patterns': '真实八受试者按时间序列路径、条件转移计数/概率和实际间隔；重复时点不明确顺序时拒绝。',
    'time_var_vecm_cointegration': 'vars VAR 系数独立参考、真实 IRF/FEVD；额外 urca Johansen 秩1 VECM 分支核验，秩不合法明确拒绝。',
    'time_hierarchical_grouped_forecast': '真实 hts 层级与底层 ARIMA、bottom-up 完全一致求和；真实留出65/7；选入外生变量明确要求未来输入，未伪造聚合区间。',
    'time_seasonal_anomaly_changepoint': '真实 robust STL 残差独立参考、MAD 检测第45点异常及 PELT/MBIC 趋势断点；干预标记仅观测关联。',
    'spatial_point_pattern': '真实平面窗口 spatstat Poisson ppm 强度50/10000与 isotropic K；总体过程明确 unmark，不拿标记频率冒充强度。',
    'spatial_interpolation_validation': '真实 gstat 五块空间交叉验证 RMSE 独立参考及预测残差；留出法可选，变差模型复用训练全样本，非嵌套调参验收。',
    'text_sentiment_analysis': '真实 syuzhet English Bing 分值独立参考和词典覆盖；无覆盖保留NA、全无覆盖拒绝，不捏造中文情感。',
    'text_supervised_classification': '真实二分类 glmnet ElasticNet 42/18留出、训练词表/IDF隔离、真实 AUC/Brier/混淆矩阵；重复文本跨集拒绝。',
    'text_entity_relation_extraction': '明确词典字面实体及 source/target 命名捕获关系规则；四实体/两 treats 关系及位置核验，无关系不以共现伪作关系。',
    'text_topic_stability': '真实 text2vec LDA 两次独立拟合和 clue Hungarian 主题词分布余弦匹配；真实元数据描述汇总，不冒充 STM 因果/协变量效应。',
}
