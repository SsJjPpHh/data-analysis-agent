"""Dedicated reproducible machine-learning workflows with frozen inputs.

Predictive model preprocessing, tuning and feature selection are confined to
training data. Evaluation artifacts always identify their held-out cohort.
"""
from __future__ import annotations

import json

from .models import AnalysisPlan
from .statistics_behavior_cores import _COMMON

_HELPERS = {}
_HELPERS[".ml_json"] = r'''
.ml_json <- function(x) if(is.character(x)&&length(x)==1L)jsonlite::fromJSON(x,simplifyVector=FALSE) else x
'''
_HELPERS[".ml_binary"] = r'''
.ml_binary <- function(y,positive=.sb_input("positive_class")) {if(is.character(y))y<-factor(y);if(is.factor(y)){if(nlevels(y)!=2L)stop("需要确认的二分类结局");event<-if(is.null(positive))levels(y)[2]else as.character(positive);if(!event%in%levels(y))stop("确认事件类不属于真实结局");.ml_last_event<<-event;return(as.integer(y==event))};if(!is.numeric(y)||anyNA(y)||any(!y%in%c(0,1)))stop("需要明确 0/1 结局或二水平因子");event<-if(is.null(positive))1 else suppressWarnings(as.numeric(positive));if(length(event)!=1||is.na(event)||!event%in%c(0,1))stop("0/1结局事件类需明确0或1");.ml_last_event<<-as.character(event);as.numeric(y==event)}
'''
_HELPERS[".ml_split"] = r'''
.ml_split <- function(dat,split=NULL,cluster=NULL,time=NULL,calibration=FALSE) {
 set.seed(as.integer(.sb_input("seed",1729L)));n<-nrow(dat)
 if(!is.null(split)){s<-tolower(as.character(dat[[split]]));s[s%in%c("0","training","development","reference")]<-"train";s[s%in%c("1","validation","external")]<-"test";s[s%in%c("cal","calibrate")]<-"calibration";allowed<-if(calibration)c("calibration","test")else c("train","test");if(anyNA(s)||any(!s%in%allowed))stop("切分列应明确 train/test 或 calibration/test")}
 else if(!is.null(time)){times<-sort(unique(dat[[time]]));if(length(times)<5L)stop("时间切分至少五个时间点");cut<-times[max(1,floor(length(times)*.75))];s<-ifelse(dat[[time]]<=cut,"train","test")}
 else {units<-if(is.null(cluster))seq_len(n)else unique(dat[[cluster]]);if(length(units)<12L)stop("切分需要至少十二个独立对象");chosen<-sample(units,max(8L,floor(length(units)*.75)));s<-ifelse(if(is.null(cluster))seq_len(n)%in%chosen else dat[[cluster]]%in%chosen,"train","test");if(calibration)s[s=="train"]<-"calibration"}
 if(!is.null(cluster)){shared<-intersect(unique(dat[[cluster]][s=="train"]),unique(dat[[cluster]][s=="test"]));if(length(shared)){if(is.null(time))stop("同一聚类同时出现训练和测试，拒绝泄漏");s[s=="train"&dat[[cluster]]%in%shared]<-"excluded"}}
 if(!is.null(time)&&!calibration&&max(dat[[time]][s=="train"])>=min(dat[[time]][s=="test"]))stop("时间外部测试必须严格晚于全部训练时间");groups<-split(seq_len(n)[s!="excluded"],s[s!="excluded"]);if(any(vapply(groups,length,integer(1))<8L)||length(groups)!=2L)stop("两侧至少八行；联合时间/聚类清除后不满足独立训练量");attr(groups,"purged_rows")<-which(s=="excluded");groups
}
'''
_HELPERS[".ml_design"] = r'''
.ml_design <- function(train,test,features) {
 a<-train[,features,drop=FALSE];b<-test[,features,drop=FALSE];receipt<-list()
 for(v in features) {if(is.numeric(a[[v]])){val<-median(a[[v]],na.rm=TRUE);if(!is.finite(val))stop("训练特征全部缺失：",v);a[[v]][is.na(a[[v]])]<-val;b[[v]][is.na(b[[v]])]<-val;if(any(!is.finite(a[[v]]))||any(!is.finite(b[[v]])))stop("特征含无穷值")}
 else {values<-as.character(a[[v]]);freq<-table(values);if(!length(freq))stop("训练特征全部缺失：",v);val<-names(which.max(freq));values[is.na(values)]<-val;a[[v]]<-factor(values);testvalues<-as.character(b[[v]]);testvalues[is.na(testvalues)]<-val;if(any(!testvalues%in%levels(a[[v]])))stop("测试出现训练中未见类别：",v);b[[v]]<-factor(testvalues,levels=levels(a[[v]]))}
 receipt[[v]]<-data.frame(特征=v,训练填充值=as.character(val),训练行数=nrow(train),测试行数=nrow(test)) }
 f<-as.formula(paste0("~",paste(sprintf("`%s`",features),collapse="+")));x<-model.matrix(f,a)[,-1,drop=FALSE];xt<-model.matrix(f,b)[,-1,drop=FALSE]
 if(!identical(colnames(x),colnames(xt)))stop("训练/测试特征设计不匹配");if(!ncol(x))stop("无有效特征")
 list(x=x,test=xt,receipt=do.call(rbind,receipt),train=a,heldout=b)
}
'''
_HELPERS[".ml_folds"] = r'''
.ml_folds <- function(n,k=4L,cluster=NULL,time=NULL) {
 if(!is.null(time)){u<-sort(unique(time));if(length(u)<k+2L)stop("时间 CV 需要足够波次");chunks<-split(u,cut(seq_along(u),breaks=k+1L,labels=FALSE));return(lapply(seq_len(k),function(j){a<-which(time%in%unlist(chunks[seq_len(j)]));b<-which(time%in%chunks[[j+1L]]);if(!is.null(cluster))a<-a[!cluster[a]%in%cluster[b]];if(length(a)<8||length(b)<8)stop("向前/整组CV清除后独立样本不足八行");list(train=a,test=b)}))}
 units<-if(is.null(cluster))seq_len(n)else unique(cluster);if(length(units)<k*2L)stop("独立对象数不足交叉验证");allocation<-sample(rep(seq_len(k),length.out=length(units)));fold<-if(is.null(cluster))allocation else allocation[match(cluster,units)];lapply(seq_len(k),function(j)list(train=which(fold!=j),test=which(fold==j)))
}
'''
_HELPERS[".ml_metrics"] = r'''
.ml_metrics <- function(y,p,binary=FALSE,threshold=.5,w=rep(1,length(y))) {
 if(anyNA(y)||anyNA(p)||any(!is.finite(p))||anyNA(w)||any(w<=0))stop("性能评估数据无效");w<-w/sum(w)
 if(!binary)return(data.frame(指标=c("RMSE","MAE","R2"),结果=c(sqrt(sum(w*(y-p)^2)),sum(w*abs(y-p)),1-sum(w*(y-p)^2)/sum(w*(y-sum(w*y))^2))))
 if(any(p<0|p>1))stop("预测概率需在 [0,1]");q<-pmin(pmax(p,1e-12),1-1e-12);pos<-y==1;neg<-!pos;if(!any(pos)||!any(neg))stop("性能评估需两种实际结局");hat<-p>=threshold
 # Weighted pairwise AUC and prevalence-correct average precision.
 byprob<-aggregate(data.frame(wpos=w*pos,wneg=w*neg),list(p=p),sum);byprob<-byprob[order(byprob$p),,drop=FALSE];auc<-sum(byprob$wpos*(cumsum(byprob$wneg)-.5*byprob$wneg))/(sum(w[pos])*sum(w[neg]));o<-order(p,decreasing=TRUE);ends<-which(c(diff(p[o])!=0,TRUE));tp<-cumsum(w[o]*y[o])[ends];prec<-tp/cumsum(w[o])[ends];ap<-sum(prec*diff(c(0,tp)))/sum(w[pos])
 data.frame(事件类=if(exists(".ml_last_event")) .ml_last_event else "1",指标=c("ROC_AUC","AveragePrecision","Brier","LogLoss","Sensitivity","Specificity","PPV","Prevalence"),结果=c(auc,ap,sum(w*(y-p)^2),-sum(w*(y*log(q)+(1-y)*log1p(-q))),sum(w[pos]*hat[pos])/sum(w[pos]),sum(w[neg]*!hat[neg])/sum(w[neg]),if(any(hat))sum(w*pos*hat)/sum(w*hat)else NA_real_,sum(w*y)))
}
'''
_HELPERS[".ml_calibration"] = r'''
.ml_calibration <- function(y,p) {bin<-cut(p,breaks=seq(0,1,length.out=11),include.lowest=TRUE);do.call(rbind,lapply(split(seq_along(y),bin,drop=TRUE),function(i)data.frame(事件类=if(exists(".ml_last_event")) .ml_last_event else "1",预测均值=mean(p[i]),实际风险=mean(y[i]),n=length(i))))}
'''
_HELPERS[".ml_fit"] = r'''
.ml_fit <- function(x,y,method="ranger",param=list(),weights=NULL) {
 allowed<-switch(method,glmnet=c("alpha","lambda"),ranger=c("trees","mtry","min_node"),randomForest=c("trees","mtry"),xgboost=c("trees","max_depth","eta"),gbm=c("trees","max_depth","eta"),character(0));if(any(!names(param)%in%allowed))stop("学习器参数未被实际支持：",paste(setdiff(names(param),allowed),collapse=", "));binary<-is.factor(y);if(binary&&nlevels(y)!=2L)stop("该预测器限二分类因子或连续数值");if(binary&&length(unique(y))!=2L)stop("训练集缺少类别");if(!binary&&!is.numeric(y))stop("需确认数值或因子结局")
 yy<-if(binary)as.numeric(y==levels(y)[2])else y
 if(method=="glmnet"){alpha<-if(is.null(param$alpha))1 else param$alpha;lambda<-if(is.null(param$lambda)).05 else param$lambda;model<-glmnet::glmnet(x,yy,family=if(binary)"binomial"else "gaussian",alpha=alpha,lambda=lambda,weights=weights,standardize=TRUE);pred<-function(z)as.numeric(predict(model,newx=z,s=lambda,type="response"));importance<-data.frame(特征=rownames(as.matrix(coef(model)))[-1],重要性=abs(as.numeric(coef(model))[-1]))}
 else if(method=="ranger"){model<-ranger::ranger(x=as.data.frame(x),y=y,probability=binary,num.trees=if(is.null(param$trees))200L else param$trees,mtry=if(is.null(param$mtry))max(1,floor(sqrt(ncol(x))))else param$mtry,min.node.size=if(is.null(param$min_node))5L else param$min_node,case.weights=weights,importance="permutation",num.threads=1L,seed=as.integer(.sb_input("seed",1729L)));pred<-function(z){p<-predict(model,data=as.data.frame(z))$predictions;if(binary)p[,2]else as.numeric(p)};importance<-data.frame(特征=names(model$variable.importance),重要性=as.numeric(model$variable.importance))}
 else if(method=="randomForest"){model<-randomForest::randomForest(x=x,y=y,ntree=if(is.null(param$trees))200L else param$trees,mtry=if(is.null(param$mtry))max(1,floor(sqrt(ncol(x))))else param$mtry,importance=TRUE);pred<-function(z)if(binary)as.numeric(predict(model,z,type="prob")[,2])else as.numeric(predict(model,z));ii<-randomForest::importance(model);importance<-data.frame(特征=rownames(ii),重要性=ii[,1])}
 else if(method=="xgboost"){model<-xgboost::xgb.train(params=list(objective=if(binary)"binary:logistic"else "reg:squarederror",max_depth=if(is.null(param$max_depth))3L else param$max_depth,eta=if(is.null(param$eta)).05 else param$eta,nthread=1L),data=xgboost::xgb.DMatrix(x,label=yy,weight=weights),nrounds=if(is.null(param$trees))100L else param$trees,verbose=0);pred<-function(z)as.numeric(predict(model,z));ii<-xgboost::xgb.importance(model=model);importance<-data.frame(特征=ii$Feature,重要性=ii$Gain)}
 else if(method=="gbm"){d<-as.data.frame(x);d$.outcome<-yy;model<-gbm::gbm(.outcome~.,data=d,distribution=if(binary)"bernoulli"else "gaussian",n.trees=if(is.null(param$trees))100L else param$trees,interaction.depth=if(is.null(param$max_depth))2L else param$max_depth,shrinkage=if(is.null(param$eta)).05 else param$eta,n.minobsinnode=5L,bag.fraction=.8,verbose=FALSE);pred<-function(z)as.numeric(predict(model,as.data.frame(z),n.trees=model$n.trees,type="response"));ii<-summary(model,plotit=FALSE);importance<-data.frame(特征=ii$var,重要性=ii$rel.inf)}
 else stop("未知学习器")
 list(model=model,predict=pred,importance=importance,binary=binary)
}
'''
_HELPERS[".ml_tune"] = r'''
.ml_tune <- function(dat,out,features,method,grid=list(list()),cluster=NULL,time=NULL,weights=NULL) {
 set.seed(as.integer(.sb_input("seed",1729L)));folds<-.ml_folds(nrow(dat),k=3L,cluster=if(is.null(cluster))NULL else dat[[cluster]],time=if(is.null(time))NULL else dat[[time]]);binary<-is.factor(dat[[out]]);scores<-matrix(NA_real_,length(grid),length(folds))
 for(g in seq_along(grid))for(j in seq_along(folds)){f<-folds[[j]];ds<-.ml_design(dat[f$train,,drop=FALSE],dat[f$test,,drop=FALSE],features);fit<-.ml_fit(ds$x,dat[[out]][f$train],method,grid[[g]],if(is.null(weights))NULL else weights[f$train]);p<-fit$predict(ds$test);yy<-if(binary).ml_binary(dat[[out]][f$test])else dat[[out]][f$test];scores[g,j]<-if(binary)mean((yy-p)^2)else mean((yy-p)^2)}
 loss<-rowMeans(scores);chosen<-which.min(loss);list(param=grid[[chosen]],table=data.frame(候选=seq_along(grid),参数=vapply(grid,function(x)jsonlite::toJSON(x,auto_unbox=TRUE),character(1)),内层MSE=loss),folds=folds)
}
'''
_HELPERS[".ml_emit"] = r'''
.ml_emit <- function(text){if(exists(".ml_last_event"))text<-paste0(text,"\n所有二分类概率和性能的事件类=",.ml_last_event);.sb_text("method_note",text,"实际训练与验证设计");.sb_text("manuscript_results",text,"模型结果与限制");.sb_text("diagnostic_output",paste0(text,"\n随机种子=",.sb_input("seed",1729L)),"切分与训练诊断")}
'''

_HELPERS[".ml_role_outcomes"] = r'''
.ml_role_outcomes <- function(dat,binary_required=FALSE) {roles<-unique(as.character(unlist(.sb_inputs[c("outcome","class_label","label_set","outcome_2")])));for(v in intersect(roles,names(dat)))if(is.character(dat[[v]]))dat[[v]]<-factor(dat[[v]]);positive<-.sb_input("positive_class");main<-c(.sb_input("outcome"),.sb_input("class_label"));task<-.sb_input("analysis_task",if(binary_required)"classification"else"auto");for(v in intersect(main,names(dat)))if(is.numeric(dat[[v]])&&task=="classification"){levels<-sort(unique(dat[[v]][!is.na(dat[[v]])]));if(length(levels)!=2L)stop("确认二分类任务要求两个真实类别值");dat[[v]]<-factor(dat[[v]],levels=levels)};for(v in intersect(main,names(dat)))if(is.factor(dat[[v]])&&nlevels(dat[[v]])==2L&&!is.null(positive)){if(!as.character(positive)%in%levels(dat[[v]]))stop("事件类不属于确认结局");dat[[v]]<-factor(dat[[v]],levels=c(setdiff(levels(dat[[v]]),as.character(positive)),as.character(positive)))};dat}
'''

_BODIES = {}

_BODIES["tree_boosting"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));splitcol<-.sb_input("test_split");method<-as.character(.sb_input("analysis_method","ranger"));task<-.sb_input("analysis_task","classification");dat<-.sb_cols(c(out,features,splitcol));dat<-dat[!is.na(dat[[out]]),,drop=FALSE];if(out%in%features)stop("结局不能作为特征")
if(task=="classification"&&(!is.factor(dat[[out]])||nlevels(dat[[out]])!=2L))stop("分类任务需确认二分类因子；连续结局请选择regression");if(task=="regression"&&!is.numeric(dat[[out]]))stop("回归任务需确认数值结局");sets<-.ml_split(dat,splitcol);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE];grid<-.ml_json(.sb_input("tuning_grid",list(list(trees=150L))));if(!is.list(grid)||!length(grid))stop("调参网格应为 JSON 候选参数对象数组")
tuning<-.ml_tune(train,out,features,method,grid);design<-.ml_design(train,test,features);fit<-.ml_fit(design$x,train[[out]],method,tuning$param);pred<-fit$predict(design$test);yy<-if(fit$binary).ml_binary(test[[out]])else test[[out]];performance<-.ml_metrics(yy,pred,fit$binary)
.sb_table("table_1",performance,"独立测试集性能");.sb_table("roc_output_2",tuning$table,"仅训练内三折调参")
importance<-fit$importance;.sb_plot("figure_1",ggplot2::ggplot(importance,ggplot2::aes(x=reorder(特征,重要性),y=重要性))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw(),"实际模型特征重要性")
.sb_plot("figure_2",ggplot2::ggplot(data.frame(实际=yy,预测=pred),ggplot2::aes(x=预测,y=实际))+ggplot2::geom_point()+ggplot2::theme_bw(),"独立测试预测对照")
if(fit$binary){cal<-.ml_calibration(yy,pred);roc<-do.call(rbind,lapply(seq(0,1,by=.05),function(t)data.frame(阈值=t,.ml_metrics(yy,pred,TRUE,t)[5:6,,drop=FALSE])));.sb_table("calibration_output",cal,"测试集分箱校准");.sb_table("roc_output",roc,"测试阈值敏感度与特异度")}
.ml_emit(paste0(method,"；训练 n=",nrow(train),"，独立测试 n=",nrow(test),"。填补、编码和调参只在训练内；测试集只评价最终模型。重要性不解释因果。"))
'''

_BODIES["regularized_models"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));splitcol<-.sb_input("validation_split");dat<-.sb_cols(c(out,features,splitcol));dat<-dat[!is.na(dat[[out]]),,drop=FALSE];if(out%in%features)stop("结局不能作为特征");sets<-.ml_split(dat,splitcol);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE]
penalty<-as.character(.sb_input("penalty","lasso"));alpha<-switch(penalty,lasso=1,ridge=0,elasticnet=.5,stop("penalty 应为 lasso/ridge/elasticnet"));lambdas<-as.numeric(.sb_input("lambda_grid",c(.005,.02,.1,.5)));grid<-lapply(lambdas,function(l)list(alpha=alpha,lambda=l));tuning<-.ml_tune(train,out,features,"glmnet",grid);design<-.ml_design(train,test,features);fit<-.ml_fit(design$x,train[[out]],"glmnet",tuning$param);pred<-fit$predict(design$test);yy<-if(fit$binary).ml_binary(test[[out]])else test[[out]];performance<-.ml_metrics(yy,pred,fit$binary)
coef_table<-data.frame(特征=rownames(as.matrix(coef(fit$model))),系数=as.numeric(coef(fit$model)));selected<-subset(coef_table,特征!="(Intercept)" & 系数!=0)
pathfit<-glmnet::glmnet(design$x,if(fit$binary).ml_binary(train[[out]])else train[[out]],family=if(fit$binary)"binomial"else "gaussian",alpha=alpha);path<-as.data.frame(as.table(as.matrix(pathfit$beta)));names(path)<-c("特征","索引","系数");path$lambda<-rep(pathfit$lambda,each=nrow(pathfit$beta))
.sb_plot("figure_1",ggplot2::ggplot(path,ggplot2::aes(x=log(lambda),y=系数,colour=特征))+ggplot2::geom_line()+ggplot2::theme_bw(),"训练集真实正则化系数路径");.sb_table("table_1",coef_table,"最终训练模型系数");.sb_table("table_2",selected,"训练内选择的非零特征")
.sb_plot("figure_2",ggplot2::ggplot(tuning$table,ggplot2::aes(x=候选,y=内层MSE))+ggplot2::geom_line()+ggplot2::geom_point()+ggplot2::theme_bw(),"训练内三折选择损失")
.ml_emit(paste0("glmnet ",penalty,"，alpha=",alpha,"，lambda=",tuning$param$lambda,"；独立测试：",paste(performance$指标,signif(performance$结果,4),collapse="；"),"。每折独立训练填补与编码，测试数据未参与选择。"))
'''

_BODIES["imbalanced_classification"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));splitcol<-.sb_input("data_split");dat<-.sb_cols(c(out,features,splitcol));dat<-dat[!is.na(dat[[out]]),,drop=FALSE];if(!is.factor(dat[[out]])||nlevels(dat[[out]])!=2L)stop("不平衡分类需确认二分类因子");sets<-.ml_split(dat,.sb_input("data_split"));train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE];yytrain<-.ml_binary(train[[out]]);if(min(table(yytrain))<5L)stop("训练少数类不足五例")
raw_weight<-.sb_input("weight","balanced");wparam<-if(identical(raw_weight,"balanced"))raw_weight else .ml_json(raw_weight);weights<-if(is.character(wparam)&&wparam=="balanced")ifelse(yytrain==1,.5/mean(yytrain==1),.5/mean(yytrain==0))else if(is.numeric(wparam)&&length(wparam)==2L&&all(wparam>0))wparam[yytrain+1L]else stop("权重需 balanced 或负/正类两个正数")
design<-.ml_design(train,test,features);fit<-.ml_fit(design$x,train[[out]],"ranger",list(trees=200L),weights);pred<-fit$predict(design$test);yy<-.ml_binary(test[[out]]);threshold<-as.numeric(.sb_input("threshold",.5));if(length(threshold)!=1L||threshold<=0||threshold>=1)stop("阈值需在 (0,1)");performance<-.ml_metrics(yy,pred,TRUE,threshold)
curves<-do.call(rbind,lapply(seq(0,1,by=.025),function(t){m<-.ml_metrics(yy,pred,TRUE,t);data.frame(阈值=t,敏感度=m$结果[5],特异度=m$结果[6],精确率=m$结果[7])}));confusion<-as.data.frame(table(实际=factor(yy,levels=0:1),预测=factor(pred>=threshold,levels=c(FALSE,TRUE))))
.sb_plot("figure_1",ggplot2::ggplot(curves,ggplot2::aes(x=敏感度,y=精确率))+ggplot2::geom_line()+ggplot2::theme_bw(),"独立测试 PR 曲线")
.sb_table("table_1",curves,"测试阈值性能全表");.sb_table("table_2",curves[,c("阈值","敏感度")],"阈值敏感度");.sb_table("table_3",curves[,c("阈值","特异度")],"阈值特异度")
.sb_table("table_4",data.frame(队列=rep(c("train","test"),each=2),类别=rep(levels(train[[out]]),2),数量=c(as.numeric(table(train[[out]])),as.numeric(table(test[[out]])))),"真实切分类别分布");.sb_table("roc_output",performance,"ROC AUC / AP / 校准与指定阈值指标");.sb_table("table_6",confusion,"预先指定阈值混淆矩阵")
counts<-data.frame(指标=c("TP","FP","TN","FN"),结果=c(sum(yy==1&pred>=threshold),sum(yy==0&pred>=threshold),sum(yy==0&pred<threshold),sum(yy==1&pred<threshold)),事件类=.ml_last_event);performance_table<-data.frame(测试数=nrow(test),冻结阈值=threshold,rbind(performance,counts));.sb_table("performance_table",performance_table,"独立测试完整分类性能与冻结阈值混淆计数")
.ml_emit(paste0("ranger 类别平衡权重只作用训练集；阈值 ",threshold," 预先确认，不用测试集最大化阈值。PR 的汇总为平均精确率 AP。"))
'''

_BODIES["feature_selection"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));splitcol<-.sb_input("test_split");dat<-.sb_cols(c(out,features,splitcol));dat<-dat[!is.na(dat[[out]]),,drop=FALSE];sets<-.ml_split(dat,splitcol);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE];spec<-.ml_json(.sb_input("resampling_specification",list(resamples=30L,fraction=.8,lambda=.05)));B<-as.integer(spec$resamples);fraction<-as.numeric(spec$fraction);lambda<-as.numeric(spec$lambda);if(B<5L||B>500L||fraction<=.5||fraction>=1||lambda<=0)stop("稳定性参数需 B=5–500、fraction在(.5,1)、lambda>0")
design<-.ml_design(train,test,features);selection<-matrix(0,B,ncol(design$x),dimnames=list(NULL,colnames(design$x)));coefs<-selection
for(b in seq_len(B)){ii<-sample.int(nrow(train),floor(fraction*nrow(train)));ds<-.ml_design(train[ii,,drop=FALSE],train,features);fitb<-.ml_fit(ds$x,train[[out]][ii],"glmnet",list(lambda=lambda,alpha=1));cb<-as.matrix(coef(fitb$model))[-1,1];coefs[b,match(names(cb),colnames(coefs))]<-cb;selection[b,]<-coefs[b,]!=0}
ranking<-data.frame(特征=colnames(selection),入选频率=colMeans(selection),平均系数=colMeans(coefs));selected<-ranking[ranking$入选频率>=.6,,drop=FALSE]
.sb_table("table_1",ranking,"训练子采样 Lasso 特征排名");.sb_table("table_2",selected,"确认阈值 0.6 的稳定候选集");.sb_table("table_3",data.frame(依据="训练子采样 Lasso 非零系数",B=B,子采样比例=fraction,lambda=lambda,训练行数=nrow(train),保留测试行数=nrow(test)),"实际选择依据")
.sb_plot("figure_1",ggplot2::ggplot(ranking,ggplot2::aes(x=reorder(特征,入选频率),y=入选频率))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw(),"真实子采样选择频率")
.sb_text("scientific_result_interpretation",paste0("结果：训练样本=",nrow(train),"，保留测试样本=",nrow(test),"；",B,"次训练子采样，比例=",fraction,"，lambda=",lambda,"；按频率≥0.6选出",nrow(selected),"项特征。入选频率：",paste(ranking$特征,signif(ranking$入选频率,4),collapse="；"),"。较高频率表示当前抽样方案下重复入选较多。不确定性：选择频率不是效应置信区间，也不能证明独立测试的预测增益。适用范围限制为确认的特征及Lasso参数，相关特征可能相互替代；下一步建议在独立数据评价已冻结候选集的预测性能。"),"实际选择结果、稳定性含义与局限")
'''

_BODIES["automl_tabular"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));splitcol<-.sb_input("data_split");dat<-.sb_cols(c(out,features,splitcol));dat<-dat[!is.na(dat[[out]]),,drop=FALSE];sets<-.ml_split(dat,splitcol);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE]
candidates<-list(lasso=list(method="glmnet",grid=list(list(alpha=1,lambda=.01),list(alpha=1,lambda=.1))),ridge=list(method="glmnet",grid=list(list(alpha=0,lambda=.1),list(alpha=0,lambda=1))),forest=list(method="ranger",grid=list(list(trees=150,min_node=5),list(trees=150,min_node=15))))
models<-list();ranking<-list();tuning_tables<-list();design<-.ml_design(train,test,features)
for(n in names(candidates)){c<-candidates[[n]];t<-.ml_tune(train,out,features,c$method,c$grid);models[[n]]<-.ml_fit(design$x,train[[out]],c$method,t$param);p<-models[[n]]$predict(design$test);m<-.ml_metrics(if(models[[n]]$binary).ml_binary(test[[out]])else test[[out]],p,models[[n]]$binary);ranking[[n]]<-data.frame(模型=n,训练内MSE=min(t$table$内层MSE),m);tuning_tables[[n]]<-data.frame(模型=n,t$table)}
leaderboard<-do.call(rbind,ranking);winner<-names(which.min(sapply(ranking,function(r)r$训练内MSE[1])))
.sb_table("table_1",leaderboard,"预先候选集独立测试性能");.sb_table("table_2",do.call(rbind,tuning_tables),"实际训练内候选调参")
.sb_text("text_1",paste0("仅根据训练内 CV 选择：",winner,"；测试集评估所有预先候选，测试名次不触发选择。"),"模型选择与测试表现")
.ml_emit("有限 AutoML 候选集：Lasso、Ridge、ranger；每候选训练内三折调参，独立测试保留至选择完成。")
'''

_BODIES["stacking_ensemble_ml"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));splitcol<-.sb_input("data_split");dat<-.sb_cols(c(out,features,splitcol));dat<-dat[!is.na(dat[[out]]),,drop=FALSE];sets<-.ml_split(dat,splitcol);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE];binary<-is.factor(train[[out]]);folds<-.ml_folds(nrow(train),4L);base_methods<-c("glmnet","ranger");oof<-matrix(NA_real_,nrow(train),2,dimnames=list(NULL,base_methods));held<-matrix(NA_real_,nrow(test),2,dimnames=list(NULL,base_methods))
for(j in seq_along(folds)){f<-folds[[j]];d<-.ml_design(train[f$train,,drop=FALSE],train[f$test,,drop=FALSE],features);for(m in seq_along(base_methods)){model<-.ml_fit(d$x,train[[out]][f$train],base_methods[m],if(base_methods[m]=="glmnet")list(lambda=.05,alpha=.5)else list(trees=150L));oof[f$test,m]<-model$predict(d$test)}}
if(anyNA(oof))stop("OOF 元学习输入不完整");meta<-glmnet::glmnet(oof,if(binary).ml_binary(train[[out]])else train[[out]],family=if(binary)"binomial"else "gaussian",alpha=0,lambda=.01,lower.limits=0)
design<-.ml_design(train,test,features);for(m in seq_along(base_methods)){model<-.ml_fit(design$x,train[[out]],base_methods[m],if(base_methods[m]=="glmnet")list(lambda=.05,alpha=.5)else list(trees=150L));held[,m]<-model$predict(design$test)}
pred<-as.numeric(predict(meta,newx=held,s=.01,type="response"));yy<-if(binary).ml_binary(test[[out]])else test[[out]];performance<-rbind(data.frame(模型="stacking",.ml_metrics(yy,pred,binary)),do.call(rbind,lapply(seq_along(base_methods),function(m)data.frame(模型=base_methods[m],.ml_metrics(yy,held[,m],binary)))))
meta_coef<-data.frame(模型=rownames(as.matrix(coef(meta))),元模型系数=as.numeric(coef(meta)))
.sb_table("table_1",performance,"真正 OOF Stacking 的独立测试性能");.sb_table("table_2",meta_coef,"非负 Ridge 元模型系数")
.sb_text("text_1",paste0("OOF行数=",nrow(oof),"；独立测试行数=",nrow(held),"；各训练行基模型预测均来自排除该行的折。"),"实际 Stacking 结构")
.ml_emit("训练内四折 OOF 预测拟合非负 Ridge 元模型；基模型在全训练集重拟合后预测独立测试，再应用已冻结元模型。元系数不等于归一化投票占比。")
'''

_BODIES["probability_calibration_ml"] = r'''
out<-.sb_input("outcome");score<-.sb_input("prediction");splitcol<-.sb_input("data_split");dat<-.sb_complete(.sb_cols(c(out,score,splitcol)),30L);y<-.ml_binary(dat[[out]]);p<-dat[[score]];if(!is.numeric(p)||any(p<0|p>1))stop("概率需在 [0,1]");sets<-.ml_split(dat,splitcol);i<-sets$train;j<-sets$test;q<-qlogis(pmin(pmax(p,1e-6),1-1e-6));fit<-glm(y[i]~q[i],family=binomial());if(!isTRUE(fit$converged))stop("校准模型未收敛");co<-coef(fit);calibrated<-plogis(co[1]+co[2]*q[j]);performance<-rbind(data.frame(模型="原始",.ml_metrics(y[j],p[j],TRUE)),data.frame(模型="训练内Platt校准",.ml_metrics(y[j],calibrated,TRUE)))
curve<-rbind(data.frame(模型="原始",.ml_calibration(y[j],p[j])),data.frame(模型="校准后",.ml_calibration(y[j],calibrated)))
.sb_plot("calibration_output",ggplot2::ggplot(curve,ggplot2::aes(x=预测均值,y=实际风险,colour=模型))+ggplot2::geom_point()+ggplot2::geom_line()+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+ggplot2::theme_bw(),"独立测试校准对照")
.sb_table("table_1",subset(performance,指标=="Brier"),"独立测试 Brier 对照");.sb_table("calibration_output_2",performance,"校准后独立测试性能")
.ml_emit(paste0("只用 train 队列拟合 Platt 截距与斜率；测试 n=",length(j),"。基础预测本身须由独立训练过程产生，当前输入预测列不能证明其上游不存在泄漏。"))
'''

_BODIES["cost_sensitive_threshold"] = r'''
out<-.sb_input("outcome");score<-.sb_input("prediction");group<-.sb_input("group");splitcol<-.sb_input("data_split");dat<-.sb_complete(.sb_cols(c(out,score,group,splitcol)),30L);y<-.ml_binary(dat[[out]]);p<-dat[[score]];if(!is.numeric(p)||any(p<0|p>1))stop("概率需在 [0,1]");cfp<-as.numeric(.sb_input("false_positive_cost"));cfn<-as.numeric(.sb_input("false_negative_cost"));if(length(cfp)!=1||length(cfn)!=1||!is.finite(cfp)||!is.finite(cfn)||cfp<0||cfn<0||cfp+cfn==0)stop("成本需有限非负且不同时为零")
sets<-.ml_split(dat,splitcol);i<-sets$train;j<-sets$test;thresholds<-sort(unique(c(0,p[i],1,1+1e-10)));costcurve<-data.frame(阈值=thresholds,训练平均成本=vapply(thresholds,function(t)mean(cfp*(p[i]>=t&y[i]==0)+cfn*(p[i]<t&y[i]==1)),numeric(1)));chosen<-costcurve$阈值[which.min(costcurve$训练平均成本)];hat<-p[j]>=chosen
performance<-.ml_metrics(y[j],p[j],TRUE,chosen);costs<-ifelse(hat&y[j]==0,cfp,ifelse(!hat&y[j]==1,cfn,0));summary_table<-data.frame(分组="总体",测试数=length(j),冻结阈值=chosen,测试平均成本=mean(costs),理论校准概率阈值=cfp/(cfp+cfn))
if(!is.null(group))summary_table<-rbind(summary_table,do.call(rbind,lapply(split(seq_along(j),dat[[group]][j]),function(k)data.frame(分组=as.character(dat[[group]][j[k[1]]]),测试数=length(k),冻结阈值=chosen,测试平均成本=mean(costs[k]),理论校准概率阈值=cfp/(cfp+cfn)))))
.sb_table("table_1",costcurve,"仅训练内成本阈值曲线");.sb_plot("figure_1",ggplot2::ggplot(costcurve,ggplot2::aes(x=阈值,y=训练平均成本))+ggplot2::geom_line()+ggplot2::geom_vline(xintercept=chosen,linetype=2)+ggplot2::theme_bw(),"训练成本与冻结阈值");.sb_table("table_2",summary_table,"冻结阈值的独立测试成本")
cost_performance_table<-rbind(data.frame(分组="总体",测试数=length(j),冻结阈值=chosen,performance),data.frame(分组=summary_table$分组,测试数=summary_table$测试数,冻结阈值=chosen,指标="MeanCost",结果=summary_table$测试平均成本,事件类=.ml_last_event));.sb_table("cost_performance_table",cost_performance_table,"独立测试冻结阈值下的完整预测性能及总体/分组平均成本")
.ml_emit(paste0("在训练内按指定 FP/FN 成本选阈值 ",chosen,"，测试集只评价。理论阈值要求概率已校准；分组仅审核同一冻结阈值，不用测试组调参。"))
'''

_BODIES["model_drift_shift"] = r'''
source<-.sb_input("data_source");features<-as.character(.sb_input("features"));out<-.sb_input("outcome");score<-.sb_input("predicted_score");dat<-.sb_complete(.sb_cols(c(source,features,out,score)),20L);sources<-sort(unique(as.character(dat[[source]])));reference<-as.character(.sb_input("reference_source",sources[1]));if(!reference%in%sources||length(sources)<2L)stop("需要参考来源和至少一个其他来源");a<-dat[dat[[source]]==reference,,drop=FALSE];rows<-list();visual<-list()
for(g in setdiff(sources,reference)){b<-dat[dat[[source]]==g,,drop=FALSE];for(v in features){if(is.numeric(a[[v]])){test<-suppressWarnings(ks.test(a[[v]],b[[v]]));br<-unique(quantile(a[[v]],seq(0,1,.1)));if(length(br)<3)stop("漂移数值特征参考变异不足");br[c(1,length(br))]<-c(-Inf,Inf);ac<-table(cut(a[[v]],br));bc<-table(cut(b[[v]],br));distance<-mean(abs(quantile(a[[v]],seq(0,1,length.out=101))-quantile(b[[v]],seq(0,1,length.out=101))));method<-"KS；参考分位数分箱PSI";stat<-unname(test$statistic);pp<-test$p.value;visual[[length(visual)+1L]]<-data.frame(来源=c(rep(reference,nrow(a)),rep(g,nrow(b))),特征=v,数值=c(a[[v]],b[[v]]))}else{levels<-union(unique(as.character(a[[v]])),unique(as.character(b[[v]])));ac<-table(factor(a[[v]],levels=levels));bc<-table(factor(b[[v]],levels=levels));test<-suppressWarnings(chisq.test(rbind(ac,bc)));method<-"分类Pearson分布检验与PSI";stat<-unname(test$statistic);pp<-test$p.value;distance<-NA_real_}
 pa<-(as.numeric(ac)+.5)/(sum(ac)+.5*length(ac));pb<-(as.numeric(bc)+.5)/(sum(bc)+.5*length(bc));rows[[length(rows)+1L]]<-data.frame(来源=g,参考=reference,特征=v,方法=method,统计量=stat,原始P=pp,PSI=sum((pb-pa)*log(pb/pa)),分位数距离=distance)}}
summary_table<-do.call(rbind,rows);summary_table$HolmP<-p.adjust(summary_table$原始P,"holm")
.sb_table("table_1",summary_table,"真实来源分布漂移与同族校正")
if(length(visual)){v<-do.call(rbind,visual);fig<-ggplot2::ggplot(v,ggplot2::aes(x=数值,colour=来源))+ggplot2::geom_density()+ggplot2::facet_wrap(~特征,scales="free")+ggplot2::theme_bw()}else fig<-ggplot2::ggplot(summary_table,ggplot2::aes(x=特征,y=PSI,fill=来源))+ggplot2::geom_col(position="dodge")+ggplot2::theme_bw()
.sb_plot("figure_1",fig,"实际来源分布对照");score_note<-""
if(!is.null(out)&&!is.null(score)){perf<-do.call(rbind,lapply(split(dat,dat[[source]]),function(d)data.frame(来源=as.character(d[[source]][1]),.ml_metrics(.ml_binary(d[[out]]),d[[score]],TRUE))));score_note<-paste(capture.output(print(perf,row.names=FALSE)),collapse="\n")}
.sb_text("text_1",paste0("分布漂移不直接证明性能退化或因果迁移失败。若同时给出真实结局和预测则另报来源性能：\n",score_note),"迁移风险与实际性能")
.ml_emit("每个目标来源对确认参考来源比较；PSI 使用参考分位数边界与 0.5 伪计数，P值在本轮全部比较中 Holm 校正。")
'''

_BODIES["ml_external_transportability"] = r'''
out<-.sb_input("outcome");score<-.sb_input("prediction");source<-.sb_input("data_source");sub<-.sb_input("subgroup");time<-.sb_input("time");center<-.sb_input("center");dat<-.sb_complete(.sb_cols(c(out,score,source,sub,time,center)),15L);y<-.ml_binary(dat[[out]]);p<-dat[[score]];if(!is.numeric(p)||any(p<0|p>1))stop("预测需真实概率");keys<-c(source,sub,time,center);strata<-do.call(interaction,c(dat[,keys,drop=FALSE],list(drop=TRUE)));rows<-list();calrows<-list()
for(g in levels(strata)){i<-which(strata==g);if(length(i)<8||length(unique(y[i]))!=2)stop("各外部评估层需至少八例且两种结局");rows[[g]]<-data.frame(评估层=g,n=length(i),.ml_metrics(y[i],p[i],TRUE));calrows[[g]]<-data.frame(评估层=g,.ml_calibration(y[i],p[i]))}
summary_table<-do.call(rbind,rows);cal<-do.call(rbind,calrows)
.sb_table("primary_table",summary_table,"真实来源/中心/时间/亚组外部性能");.sb_plot("diagnostic_plot",ggplot2::ggplot(cal,ggplot2::aes(x=预测均值,y=实际风险,colour=评估层))+ggplot2::geom_point()+ggplot2::geom_line()+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+ggplot2::theme_bw(),"来源分层实际校准")
.sb_text("manuscript_results","评估输入冻结预测的来源、中心、时间和亚组分层性能；不重训练、不用外部结局重新选择阈值。输入预测上游必须独立，单一预测列无法证明训练来源。","外部验证范围")
'''

_BODIES["ml_fairness_subgroup"] = r'''
out<-.sb_input("outcome");score<-.sb_input("prediction");sub<-.sb_input("subgroup");thresholdcol<-.sb_input("threshold");weight<-.sb_input("weight");dat<-.sb_complete(.sb_cols(c(out,score,sub,thresholdcol,weight)),15L);y<-.ml_binary(dat[[out]]);p<-dat[[score]];threshold<-if(is.null(thresholdcol))rep(.5,nrow(dat))else dat[[thresholdcol]];w<-if(is.null(weight))rep(1,nrow(dat))else dat[[weight]];if(any(!is.finite(threshold))||any(threshold<=0|threshold>=1))stop("确认阈值列须在(0,1)")
rows<-lapply(split(seq_len(nrow(dat)),dat[[sub]]),function(i){m<-.ml_metrics(y[i],p[i],TRUE,threshold[i],w[i]);hat<-p[i]>=threshold[i];data.frame(亚组=as.character(dat[[sub]][i[1]]),n=length(i),m,阳性预测率=weighted.mean(hat,w[i]))});summary_table<-do.call(rbind,rows)
contrast<-subset(summary_table,指标%in%c("Sensitivity","Specificity","ROC_AUC","Brier"))
.sb_table("primary_table",summary_table,"加权亚组性能和实际阳性预测率");.sb_plot("diagnostic_plot",ggplot2::ggplot(contrast,ggplot2::aes(x=亚组,y=结果,fill=亚组))+ggplot2::geom_col()+ggplot2::facet_wrap(~指标,scales="free_y")+ggplot2::theme_bw(),"实际亚组性能差异")
.sb_text("manuscript_results","真实敏感度、特异度、阳性预测率、AUC 和 Brier 分组审核；行权重进入全部指标，阈值来自确认列或预先固定0.5。性能差异不能单独证明伦理公平或不公平。","公平性审计范围")
'''

_BODIES["ml_conformal_prediction"] = r'''
out<-.sb_input("outcome");score<-.sb_input("prediction");splitcol<-.sb_input("calibration_split");sub<-.sb_input("subgroup");weight<-.sb_input("weight");dat<-.sb_complete(.sb_cols(c(out,score,splitcol,sub,weight)),30L);alpha<-as.numeric(.sb_input("alpha",.1));if(alpha<=0||alpha>=1)stop("alpha在(0,1)");sets<-.ml_split(dat,splitcol,calibration=TRUE);i<-sets$calibration;j<-sets$test;binary<-is.factor(dat[[out]]);y<-if(binary).ml_binary(dat[[out]])else dat[[out]];p<-dat[[score]];if(!is.numeric(y)||!is.numeric(p))stop("需数值预测或二分类概率");if(binary&&any(p<0|p>1))stop("二分类概率需在[0,1]");s<-if(binary)ifelse(y==1,1-p,p)else abs(y-p);w<-if(is.null(weight))rep(1,nrow(dat))else dat[[weight]];if(any(!is.finite(w))||any(w<=0))stop("权重需正且有限");groups<-if(is.null(sub))rep("总体",nrow(dat))else as.character(dat[[sub]])
quantile_for<-function(ii,testweight){if(length(ii)<8)stop("每个校准组至少八例");o<-order(s[ii]);cw<-cumsum(w[ii][o])/(sum(w[ii])+testweight);hit<-which(cw>=1-alpha);if(!length(hit))Inf else s[ii][o[min(hit)]]}
q<-vapply(j,function(row)quantile_for(i[groups[i]==groups[row]],w[row]),numeric(1))
if(binary){set0<-p[j]<=q;set1<-1-p[j]<=q;covered<-ifelse(y[j]==1,set1,set0);efficiency<-as.numeric(set0)+as.numeric(set1);predictions<-data.frame(行=j,亚组=groups[j],真实=y[j],概率=p[j],含类别0=set0,含类别1=set1,集合大小=efficiency,校准阈值=q,覆盖=covered)}else{covered<-abs(y[j]-p[j])<=q;efficiency<-2*q;predictions<-data.frame(行=j,亚组=groups[j],真实=y[j],预测=p[j],区间下限=p[j]-q,区间上限=p[j]+q,区间宽度=efficiency,校准阈值=q,覆盖=covered)}
summary_table<-do.call(rbind,lapply(split(seq_along(j),groups[j]),function(k)data.frame(亚组=groups[j[k[1]]],测试数=length(k),实际覆盖率=mean(covered[k]),平均效率=mean(efficiency[k]),目标覆盖率=1-alpha)))
summary_table$事件类<-if(binary).ml_last_event else "连续结局";.sb_table("primary_table",summary_table,"独立测试 Conformal 覆盖与效率")
fig<-if(binary)ggplot2::ggplot(predictions,ggplot2::aes(x=行,y=集合大小,colour=覆盖))+ggplot2::geom_point()+ggplot2::theme_bw()else ggplot2::ggplot(predictions,ggplot2::aes(x=行,y=预测,ymin=区间下限,ymax=区间上限,colour=覆盖))+ggplot2::geom_pointrange()+ggplot2::theme_bw();.sb_plot("diagnostic_plot",fig,"实际测试预测区间 / 二分类集合")
.sb_text("manuscript_results",paste0("分离校准/测试，有限样本上取整分位数；",if(binary)paste0("编码1对应事件类=",.ml_last_event,"；")else "连续结局区间；",if(is.null(sub))"总体 split conformal。"else"按确认亚组 Mondrian 校准。",if(is.null(weight))"有效性依赖交换性和上游预测独立。"else"加权分位数包含测试点在无穷大的权重；保证需权重为已知目标/校准密度比，不把任意调查权重宣称为分布无关保证。","实际逐行预测数=",nrow(predictions)),"真实 Conformal 估计与假设")
'''

_BODIES["unsupervised_ml"] = r'''
features<-as.character(.sb_input("data_object"));id<-.sb_input("sample_id");scalevars<-as.character(.sb_input("standardization_variables",features));external<-.sb_input("external_validation_variable");dat<-.sb_complete(.sb_cols(c(features,id,external)),20L);.sb_numeric(dat,features);if(!all(scalevars%in%features))stop("标准化列需属于特征");x<-as.matrix(dat[,features,drop=FALSE]);for(v in scalevars){if(sd(x[,v])==0)stop("标准化特征常数");x[,v]<-scale(x[,v])};method<-.sb_input("analysis_method","kmeans");set.seed(as.integer(.sb_input("seed",1729L)))
if(method=="kmeans"){ks<-2:min(6L,nrow(x)-1L);fits<-lapply(ks,function(k)kmeans(x,k,nstart=20));quality<-data.frame(K=ks,平均轮廓=sapply(fits,function(f)mean(cluster::silhouette(f$cluster,dist(x))[,3])));fit<-fits[[which.max(quality$平均轮廓)]];membership<-fit$cluster;score<-rowSums((x-fit$centers[membership,,drop=FALSE])^2);noise<-rep(FALSE,nrow(x))}
else if(method=="mclust"){mclustBIC<-mclust::mclustBIC;fit<-mclust::Mclust(x,G=1:6,verbose=FALSE);membership<-fit$classification;quality<-data.frame(模型=rep(rownames(fit$BIC),times=ncol(fit$BIC)),结构=rep(colnames(fit$BIC),each=nrow(fit$BIC)),BIC=as.numeric(fit$BIC));score<-1-apply(fit$z,1,max);noise<-rep(FALSE,nrow(x))}
else if(method=="dbscan"){eps<-as.numeric(.sb_input("eps",.8));minpts<-as.integer(.sb_input("min_pts",5L));fit<-dbscan::dbscan(x,eps=eps,minPts=minpts);membership<-fit$cluster;noise<-membership==0;score<-dbscan::kNNdist(x,k=minpts);quality<-data.frame(eps=eps,minPts=minpts,噪声数=sum(noise),类别数=length(unique(membership[membership>0])))}else stop("支持 kmeans/mclust/dbscan")
if(length(unique(membership))>1L&&length(unique(membership))<nrow(x)){sil<-cluster::silhouette(membership,dist(x));sil_table<-data.frame(样本=seq_len(nrow(x)),类别=membership,轮廓系数=sil[,3])}else sil_table<-data.frame(样本=seq_len(nrow(x)),类别=membership,轮廓系数=NA_real_)
profile<-aggregate(dat[,features,drop=FALSE],list(类别=membership),mean);sizes<-data.frame(类别=as.integer(names(table(membership))),数量=as.numeric(table(membership)));pc<-prcomp(x);coords<-data.frame(样本ID=if(is.null(id))seq_len(nrow(x))else dat[[id]],PC1=pc$x[,1],PC2=pc$x[,2],类别=factor(membership));anomalies<-data.frame(样本ID=coords$样本ID,类别=membership,噪声=noise,模型分数=score)
.sb_plot("figure_1",ggplot2::ggplot(coords,ggplot2::aes(x=PC1,y=PC2,colour=类别))+ggplot2::geom_point()+ggplot2::theme_bw(),"真实聚类的 PCA 投影")
.sb_table("table_1",sil_table,"逐样本轮廓系数");.sb_table("table_2",profile,"原始尺度类别特征画像");.sb_table("outlier_summary",anomalies,"模型定义的距离 / 不确定性 / 噪声")
if(!is.null(external))quality$外部ARI<-mclust::adjustedRandIndex(membership,dat[[external]]);.sb_table("table_4",quality,"实际模型选择质量");.sb_table("table_5",sizes,"实际类别大小")
.sb_plot("figure_2",ggplot2::ggplot(sil_table,ggplot2::aes(x=factor(类别),y=轮廓系数))+ggplot2::geom_boxplot()+ggplot2::theme_bw(),"类别轮廓系数分布")
.sb_text("scientific_result_interpretation",paste0("结果：实际算法=",method,"，完整样本=",nrow(x),"，非噪声类别数=",length(unique(membership[membership>0])),"，噪声样本=",sum(noise),"；类别大小：",paste(sizes$类别,sizes$数量,collapse="；"),"。模型分数含义：",switch(method,kmeans="到确认类别中心的平方距离",mclust="1减最大类别后验概率",dbscan="确认k的近邻距离，噪声由DBSCAN类别0定义"),"。质量估计：\n",paste(capture.output(print(quality,row.names=FALSE)),collapse="\n"),"\n不确定性：当前质量统计未提供重抽样置信区间，类别与距离依赖确认的特征、尺度及随机初始化。适用范围是描述样本结构，聚类不证明因果或生物学分类；PCA图仅显示实际类别，不是选类依据。下一步建议在独立数据及确认的替代预处理中评估类别稳定性。"),"真实聚类结果、质量与解释范围")

'''

_BODIES["dimensionality_reduction_ml"] = r'''
features<-as.character(.sb_input("features"));group<-.sb_input("group");dat<-.sb_complete(.sb_cols(c(features,group)),12L);.sb_numeric(dat,features);x<-scale(as.matrix(dat[,features,drop=FALSE]));if(any(!is.finite(x)))stop("降维特征不能常数");method<-.sb_input("analysis_method","pca");set.seed(as.integer(.sb_input("seed",1729L)))
if(method=="pca"){fit<-prcomp(x);coords<-fit$x[,1:2,drop=FALSE];details<-paste0("PCA解释方差=",paste(signif(fit$sdev[1:2]^2/sum(fit$sdev^2),4),collapse=", "))}
else if(method=="umap"){coords<-uwot::umap(x,n_neighbors=min(as.integer(.sb_input("neighbors",15L)),nrow(x)-1L),n_components=2,n_threads=1,n_sgd_threads=1,verbose=FALSE);details<-"UMAP 没有 PCA 式解释方差比例"}
else if(method=="tsne"){perplexity<-as.numeric(.sb_input("perplexity",min(30,floor((nrow(x)-1)/3)-1)));fit<-Rtsne::Rtsne(x,perplexity=perplexity,check_duplicates=TRUE,num_threads=1,pca=TRUE,verbose=FALSE);coords<-fit$Y;details<-"t-SNE 非线性邻域投影没有 PCA 式解释方差比例"}else stop("支持 pca/umap/tsne")
projection_table<-data.frame(源数据行=rownames(dat),方法=method,维度1=coords[,1],维度2=coords[,2],组=if(is.null(group))"总体"else as.character(dat[[group]]));.sb_table("scientific_result_table",projection_table,paste0("实际 ",method," 样本投影坐标；行标识保留完整病例在源数据中的位置"))
if(method=="pca"){variance<-fit$sdev^2/sum(fit$sdev^2);pca_structure<-data.frame(特征=rep(rownames(fit$rotation),times=ncol(fit$rotation)),主成分=rep(colnames(fit$rotation),each=nrow(fit$rotation)),载荷=as.numeric(fit$rotation),标准差=rep(fit$sdev,each=nrow(fit$rotation)),解释方差比例=rep(variance,each=nrow(fit$rotation)),累计解释方差比例=rep(cumsum(variance),each=nrow(fit$rotation)));.sb_table("pca_structure_table",pca_structure,"标准化输入的真实 PCA 载荷、主成分标准差与解释方差；不适用于 UMAP/t-SNE")}
points<-data.frame(维度1=coords[,1],维度2=coords[,2],组=if(is.null(group))"总体"else as.character(dat[[group]]));.sb_plot("figure_1",ggplot2::ggplot(points,ggplot2::aes(x=维度1,y=维度2,colour=组))+ggplot2::geom_point()+ggplot2::theme_bw(),paste0("实际 ",method," 投影"))
.sb_text("text_2",paste0("完整样本 n=",nrow(x),"；分组仅作投影标注；",details),"实际样本结构与方差意义");.ml_emit(paste0(method,"；确认数值特征标准化后投影；",details,"。投影距离不代替推断检验。"));.sb_text("manuscript_results_2",details,"降维解释")
'''

_BODIES["anomaly_detection_ml"] = r'''
features<-as.character(.sb_input("features"));known<-.sb_input("known_anomaly_label");group<-.sb_input("group");dat<-.sb_complete(.sb_cols(c(features,known,group)),20L);.sb_numeric(dat,features);x<-scale(as.matrix(dat[,features,drop=FALSE]));if(any(!is.finite(x)))stop("异常检测特征不能常数");k<-as.integer(.sb_input("neighbors",10L));if(k<3||k>=nrow(dat))stop("LOF邻居数需3至n-1");groups<-if(is.null(group))rep("总体",nrow(dat))else as.character(dat[[group]]);score<-rep(NA_real_,nrow(dat))
for(g in unique(groups)){i<-which(groups==g);if(length(i)<=k)stop("每组样本需超过邻居数");score[i]<-dbscan::lof(x[i,,drop=FALSE],minPts=k)};cut<-as.numeric(.sb_input("threshold",1.5));flags<-score>cut;anomalies<-data.frame(行=seq_len(nrow(dat)),分组=groups,LOF=score,质量预警=flags)
.sb_table("outlier_summary",anomalies,"真实 LOF 异常分数与预先阈值");.sb_plot("outlier_summary_2",ggplot2::ggplot(anomalies,ggplot2::aes(x=行,y=LOF,colour=分组))+ggplot2::geom_point()+ggplot2::geom_hline(yintercept=cut,linetype=2)+ggplot2::theme_bw(),"真实样本 LOF")
evaluation<-if(is.null(known))"未提供已知异常标签，不报告识别性能。"else paste(capture.output(print(.ml_metrics(.ml_binary(dat[[known]]),score/(1+score),TRUE,cut/(1+cut)),row.names=FALSE)),collapse="\n")
.sb_text("analysis_recommendation","预警仅定位质量复核，不自动删除；分组分别比较局部邻域。","异常复核建议");.ml_emit(paste0("dbscan::lof，minPts=",k,"；阈值=",cut,"。\n",evaluation))
'''

_BODIES["association_recommender"] = r'''
id<-.sb_input("transaction_id");item<-.sb_input("item_id");time<-.sb_input("time");dat<-.sb_complete(.sb_cols(c(id,item,time)),10L);if(anyDuplicated(dat[,c(id,item)]))stop("重复交易×物品需先明确去重规则");if(!is.null(time)){v<-vapply(split(dat[[time]],dat[[id]]),function(t){u<-unique(t);if(length(u)!=1)stop("交易时间不唯一");as.character(u)},character(1));orderedids<-names(sort(v));cut<-floor(length(orderedids)*.75);trainids<-orderedids[seq_len(cut)];testids<-setdiff(orderedids,trainids)}else{ids<-unique(dat[[id]]);set.seed(as.integer(.sb_input("seed",1729L)));trainids<-sample(ids,floor(length(ids)*.75));testids<-setdiff(ids,trainids)}
trainitems<-split(as.character(dat[[item]][dat[[id]]%in%trainids]),as.character(dat[[id]][dat[[id]]%in%trainids]));suppressPackageStartupMessages(library(arules));transactions<-methods::as(trainitems,"transactions");rules<-arules::apriori(transactions,parameter=list(supp=as.numeric(.sb_input("support",.05)),conf=as.numeric(.sb_input("confidence",.5)),minlen=2L,maxlen=3L,target="rules"),control=list(verbose=FALSE));if(!length(rules))stop("确认阈值下无关联规则，不伪造规则")
rule_table<-as(rules,"data.frame");rule_table<-rule_table[order(-rule_table$lift),,drop=FALSE];recommendations<-list()
for(tid in as.character(testids)){basket<-unique(as.character(dat[[item]][as.character(dat[[id]])==tid]));if(length(basket)<2)next;visible<-head(sort(basket),max(1,floor(length(basket)/2)));hidden<-setdiff(basket,visible);lhs<-as(arules::lhs(rules),"list");rhs<-as(arules::rhs(rules),"list");eligible<-which(vapply(lhs,function(a)all(a%in%visible),logical(1)));candidates<-unique(unlist(rhs[eligible]));candidates<-setdiff(candidates,visible);if(length(candidates))recommendations[[length(recommendations)+1L]]<-data.frame(交易=tid,可见物品=paste(visible,collapse=","),推荐物品=candidates,真实留出命中=candidates%in%hidden)}
rec_table<-if(length(recommendations))do.call(rbind,recommendations)else data.frame(交易=character(0),可见物品=character(0),推荐物品=character(0),真实留出命中=logical(0))
.sb_table("table_1",rule_table,"仅训练交易 Apriori 支持度/置信度/lift");.sb_table("table_2",rec_table,"测试交易遮盖物品的实际推荐候选")
plotdata<-head(rule_table,20);.sb_plot("figure_1",ggplot2::ggplot(plotdata,ggplot2::aes(x=support,y=confidence,size=lift))+ggplot2::geom_point()+ggplot2::theme_bw(),"实际关联规则质量")
.ml_emit(paste0("Apriori 仅训练交易产生规则；测试篮子确定性遮盖一半物品，候选不含可见项。训练交易=",length(trainids),"，测试交易=",length(testids),"；关联不能解释因果。时间存在时按交易时间留出后段。"))
'''

_BODIES["sparse_text_ml"] = r'''
text<-.sb_input("text");out<-.sb_input("class_label");splitcol<-.sb_input("data_split");dat<-.sb_complete(.sb_cols(c(text,out,splitcol)),30L);if(!is.factor(dat[[out]])||nlevels(dat[[out]])!=2L)stop("当前稀疏文本模型为确认二分类因子");sets<-.ml_split(dat,splitcol);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE]
make_dfm<-function(z)quanteda::dfm(quanteda::tokens_tolower(quanteda::tokens(z,remove_punct=TRUE,remove_numbers=TRUE)));train_dfm<-make_dfm(as.character(train[[text]]));train_dfm<-quanteda::dfm_trim(train_dfm,min_docfreq=2);if(quanteda::nfeat(train_dfm)<2)stop("训练语料有效词项不足");test_dfm<-quanteda::dfm_match(make_dfm(as.character(test[[text]])),features=quanteda::featnames(train_dfm))
x<-methods::as(train_dfm,"dgCMatrix");xt<-methods::as(test_dfm,"dgCMatrix");lambdas<-as.numeric(.sb_input("lambda_grid",c(.01,.05,.2)));folds<-.ml_folds(nrow(train),3L);loss<-matrix(NA_real_,length(lambdas),length(folds))
for(j in seq_along(folds)){f<-folds[[j]];a<-quanteda::dfm_trim(make_dfm(as.character(train[[text]][f$train])),min_docfreq=2);b<-quanteda::dfm_match(make_dfm(as.character(train[[text]][f$test])),features=quanteda::featnames(a));xx<-methods::as(a,"dgCMatrix");zz<-methods::as(b,"dgCMatrix");for(g in seq_along(lambdas)){mod<-glmnet::glmnet(xx,.ml_binary(train[[out]][f$train]),family="binomial",alpha=1,lambda=lambdas[g],standardize=FALSE);pp<-as.numeric(predict(mod,newx=zz,type="response"));loss[g,j]<-mean((.ml_binary(train[[out]][f$test])-pp)^2)}}
chosenlambda<-lambdas[which.min(rowMeans(loss))];model<-glmnet::glmnet(x,.ml_binary(train[[out]]),family="binomial",alpha=1,lambda=chosenlambda,standardize=FALSE);pred<-as.numeric(predict(model,newx=xt,type="response"));performance<-.ml_metrics(.ml_binary(test[[out]]),pred,TRUE);coef_table<-data.frame(词项=rownames(as.matrix(coef(model))),系数=as.numeric(coef(model)));coef_table<-coef_table[coef_table$词项!="(Intercept)",,drop=FALSE]
.sb_table("table_1",coef_table,"训练词表与真实稀疏文本系数");.sb_table("table_2",performance,"独立测试文本分类性能")
.sb_text("manuscript_results",paste0("入选词项：",paste(coef_table$词项[coef_table$系数!=0],collapse=", "),"；关键词系数依赖语料与共线性，不解释因果。"),"实际关键词解释")
.sb_text("method_note","训练词表固定后映射测试；训练内每折重新估计词表、三折选择 lambda；计数特征不从测试估计 IDF。","真实文本训练设计");.sb_text("manuscript_results_2",paste0("训练文档=",nrow(train),"，独立测试文档=",nrow(test),"，训练词表=",ncol(x)),"文本模型结果");.sb_text("diagnostic_output","测试出现训练外新词被投影掉；基础分词与计数不冒称语义模型。","文本特征诊断")
'''

_BODIES["ml_explainability"] = r'''
bundle<-.sb_input("trained_model");if(!is.list(bundle)||is.null(bundle$model)||!is.data.frame(bundle$data)||!is.data.frame(bundle$training_data))stop("解释输入需原生模型 bundle：model、data（独立解释数据）、training_data（训练背景）")
model<-bundle$model;dat<-bundle$data;background<-bundle$training_data;features<-as.character(.sb_input("features"));out<-.sb_input("outcome");id<-.sb_input("subject_id");if(any(!c(features,out,id)%in%names(dat))||any(!features%in%names(background)))stop("模型bundle缺确认列");if(anyDuplicated(dat[[id]]))stop("个体解释ID需唯一");if(anyNA(dat[,features,drop=FALSE])||anyNA(background[,features,drop=FALSE]))stop("当前解释器需模型已训练预处理后的完整特征")
x<-dat[,features,drop=FALSE];bx<-background[,features,drop=FALSE];if(is.character(dat[[out]]))dat[[out]]<-factor(dat[[out]]);model_classes<-if(inherits(model,"ranger")&&model$treetype=="Probability estimation")model$forest$levels else if(inherits(model,"randomForest")&&model$type=="classification")model$classes else if(inherits(model,"glm")&&model$family$family%in%c("binomial","quasibinomial")){response<-if(is.null(model$model))NULL else model$model[[1]];if(is.factor(response))levels(response)else c("0","1")}else NULL;binary<-!is.null(model_classes);if(binary&&length(model_classes)!=2L)stop("当前SHAP预测目标需二分类模型或连续模型");event<-if(binary){positive<-.sb_input("positive_class");if(is.null(positive))model_classes[2]else as.character(positive)}else NULL;if(binary&&!event%in%model_classes)stop("确认事件类不属于已训练模型类别");prediction<-function(object,newdata){if(inherits(object,"ranger")){if(!requireNamespace("ranger",quietly=TRUE))stop("ranger模型预测依赖不可用");p<-predict(object,data=newdata)$predictions;if(is.matrix(p)){if(!event%in%colnames(p))stop("模型概率列未覆盖确认事件类");as.numeric(p[,event])}else as.numeric(p)}else if(inherits(object,"randomForest")){if(!requireNamespace("randomForest",quietly=TRUE))stop("randomForest模型预测依赖不可用");if(binary)as.numeric(predict(object,newdata,type="prob")[,event])else as.numeric(predict(object,newdata))}else if(inherits(object,"glm")||inherits(object,"lm")){p<-as.numeric(predict(object,newdata,type="response"));if(binary&&event!=model_classes[2])p<-1-p;p}else stop("明确支持 ranger/randomForest/glm/lm 原生模型；其他对象需提供兼容预测器")}
set.seed(as.integer(.sb_input("seed",1729L)));nsim<-as.integer(.sb_input("resamples",100L));if(nsim<10||nsim>1000)stop("SHAP Monte Carlo次数10–1000");shap<-fastshap::explain(model,X=bx,pred_wrapper=prediction,newdata=x,nsim=nsim,adjust=TRUE);baseline<-mean(prediction(model,bx));pred<-prediction(model,x);shap_table<-data.frame(个体ID=rep(dat[[id]],each=length(features)),特征=rep(features,times=nrow(dat)),SHAP=as.numeric(t(shap)),事件类=if(binary)event else "连续预测",预测=rep(pred,each=length(features)),训练背景均值=baseline)
importance<-data.frame(特征=features,平均绝对SHAP=colMeans(abs(shap)));perm<-vapply(features,function(v){z<-x;z[[v]]<-sample(z[[v]]);mean((prediction(model,z)-pred)^2)},numeric(1));permutation<-data.frame(特征=features,预测变化MSE=perm)
v<-as.character(.sb_input("pdp_feature",features[1]));if(!v%in%features||!is.numeric(bx[[v]]))stop("当前PDP需明确数值特征");grid<-unique(as.numeric(quantile(bx[[v]],seq(.05,.95,length.out=15))));pdp<-data.frame(特征值=grid,训练背景平均预测=vapply(grid,function(t){z<-bx;z[[v]]<-t;mean(prediction(model,z))},numeric(1)))
.sb_table("table_1",shap_table,"真实 fastshap 个体 SHAP");.sb_plot("figure_1",ggplot2::ggplot(permutation,ggplot2::aes(x=reorder(特征,预测变化MSE),y=预测变化MSE))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw(),"真实打乱特征预测变化")
.sb_plot("figure_2",ggplot2::ggplot(pdp,ggplot2::aes(x=特征值,y=训练背景平均预测))+ggplot2::geom_line()+ggplot2::theme_bw(),paste0(v," 实际训练背景 PDP"));.sb_plot("figure_3",ggplot2::ggplot(importance,ggplot2::aes(x=reorder(特征,平均绝对SHAP),y=平均绝对SHAP))+ggplot2::geom_col()+ggplot2::coord_flip()+ggplot2::theme_bw(),"真实全局平均绝对 SHAP")
.sb_plot("figure_4",ggplot2::ggplot(shap_table,ggplot2::aes(x=特征,y=SHAP))+ggplot2::geom_boxplot()+ggplot2::theme_bw(),"真实个体 SHAP 分布")
.sb_text("manuscript_results",paste0("fastshap 独立训练背景 Monte Carlo Shapley，nsim=",nsim,if(binary)paste0("；模型解释事件类=",event)else "；模型解释连续预测", "；最大加和误差=",max(abs(rowSums(shap)+baseline-pred))),"实际解释与加和检查");.sb_text("manuscript_results_2",paste0("个体 ID 与实际 SHAP 长表逐一对应；解释对象 n=",nrow(dat)),"个体解释覆盖")
.sb_table("table_2",data.frame(限制=c("相关特征扰动可能产生训练分布外组合","背景数据必须来自训练流程","SHAP和PDP均是模型解释而非因果效应")),"解释偏倚限制")
'''

_BODIES["survival_ml"] = r'''
time<-.sb_input("followup_time");event<-.sb_input("event");features<-as.character(.sb_input("features"));splitcol<-.sb_input("validation_split");dat<-.sb_complete(.sb_cols(c(time,event,features,splitcol)),40L);.sb_numeric(dat,time);e<-.ml_binary(dat[[event]]);if(any(dat[[time]]<=0))stop("随访时间应正值");dat$.event<-e;sets<-.ml_split(dat,splitcol);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE];if(sum(train$.event)<8||sum(test$.event)<5)stop("生存模型训练至少八个、测试至少五个事件")
design<-.ml_design(train,test,features);tr<-design$train;te<-design$heldout;tr$.time<-train[[time]];tr$.event<-train$.event;te$.time<-test[[time]];te$.event<-test$.event;formula<-as.formula(paste("survival::Surv(.time,.event) ~",paste(sprintf("`%s`",features),collapse="+")))
Surv<-survival::Surv;fit<-ranger::ranger(formula,data=tr,num.trees=as.integer(.sb_input("trees",200L)),importance="permutation",num.threads=1,seed=as.integer(.sb_input("seed",1729L)));forestpred<-predict(fit,data=te);risk<-rowSums(forestpred$chf);cox<-survival::coxph(formula,data=tr,x=TRUE,y=TRUE);coxrisk<-as.numeric(predict(cox,newdata=te,type="lp"));ci_forest<-survival::concordance(survival::Surv(te$.time,te$.event)~risk,reverse=TRUE);ci_cox<-survival::concordance(survival::Surv(te$.time,te$.event)~coxrisk,reverse=TRUE)
summary_table<-data.frame(事件类=.ml_last_event,模型=c("生存随机森林","训练Cox对照"),测试Cindex=c(ci_forest$concordance,ci_cox$concordance),SE=c(sqrt(ci_forest$var),sqrt(ci_cox$var)))
horizon<-as.numeric(.sb_input("prediction_times",unname(quantile(train[[time]][train$.event==1],c(.25,.5,.75)))));if(any(horizon<=0)||any(horizon>=max(te$.time)))stop("预测时间须在测试观察窗口内")
roc<-timeROC::timeROC(T=te$.time,delta=te$.event,marker=risk,cause=1,weighting="marginal",times=horizon,iid=TRUE);auc_table<-data.frame(时间=roc$times,IPCW动态AUC=roc$AUC)
# Estimate censoring weights on training only, then evaluate IPCW calibration.
censor<-survival::survfit(survival::Surv(tr$.time,1-tr$.event)~1);gfun<-function(t){j<-findInterval(t,censor$time);ifelse(j==0,1,censor$surv[pmax(j,1)])};tstar<-horizon[min(2,length(horizon))];which_t<-findInterval(tstar,forestpred$unique.death.times);survp<-if(which_t==0)rep(1,nrow(te))else forestpred$survival[,which_t];obs<-as.numeric(te$.time<=tstar&te$.event==1);weights<-ifelse(te$.time<=tstar&te$.event==1,1/pmax(gfun(te$.time-1e-8),1e-6),ifelse(te$.time>tstar,1/pmax(gfun(tstar),1e-6),0));caldata<-data.frame(风险=1-survp,事件=obs,权重=weights);caldata$组<-cut(caldata$风险,unique(quantile(caldata$风险,seq(0,1,length.out=6))),include.lowest=TRUE)
cal<-do.call(rbind,lapply(split(caldata,caldata$组,drop=TRUE),function(d)data.frame(预测风险=mean(d$风险),IPCW风险=mean(d$事件*d$权重),n=nrow(d))))
# Risk-group threshold is a training-prediction median, never the test median.
trainrisk<-rowSums(predict(fit,data=tr)$chf);riskgroup<-factor(ifelse(risk>median(trainrisk),"训练阈值高风险","训练阈值低风险"));km<-survival::survfit(survival::Surv(te$.time,te$.event)~riskgroup);ks<-summary(km);kmdata<-data.frame(时间=ks$time,生存=ks$surv,分层=ks$strata)
.sb_table("table_1",summary_table,"独立测试真实 C-index");.sb_table("table_2",auc_table,"独立测试 IPCW 动态 AUC");.sb_plot("figure_1",ggplot2::ggplot(kmdata,ggplot2::aes(x=时间,y=生存,colour=分层))+ggplot2::geom_step()+ggplot2::theme_bw(),"冻结训练阈值测试风险分层")
.sb_plot("calibration_output",ggplot2::ggplot(cal,ggplot2::aes(x=预测风险,y=IPCW风险))+ggplot2::geom_point()+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+ggplot2::theme_bw(),"训练估计删失权重的测试生存校准")
cox_beta<-coef(cox);cox_se<-sqrt(diag(vcov(cox)));cox_effect_table<-data.frame(模型="仅训练Cox对照",参数=names(cox_beta),logHR=cox_beta,HR=exp(cox_beta),logHR标准误=cox_se,lower=exp(cox_beta-qnorm(.975)*cox_se),upper=exp(cox_beta+qnorm(.975)*cox_se));.sb_table("table_3",cox_effect_table,"仅训练 Cox 对照：HR及其95% Wald置信区间，标准误属于logHR尺度；不将Cox效应冒称森林效应")
survival_performance_table<-rbind(data.frame(模型=summary_table$模型,指标="C-index",预测时间=NA_real_,估计值=summary_table$测试Cindex,SE=summary_table$SE,测试数=nrow(te),事件类=.ml_last_event),data.frame(模型="生存随机森林",指标="IPCW动态AUC",预测时间=auc_table$时间,估计值=auc_table$IPCW动态AUC,SE=NA_real_,测试数=nrow(te),事件类=.ml_last_event));.sb_table("survival_performance_table",survival_performance_table,"独立测试C-index及指定时间动态AUC；时间NA表示整个测试随访，动态AUC未报告SE")
.sb_text("scientific_result_interpretation",paste0("结果：训练样本=",nrow(tr),"，独立测试样本=",nrow(te),"，事件类=",.ml_last_event,"；森林与训练Cox对照的测试C-index估计为",paste(signif(summary_table$测试Cindex,4),collapse="、"),"，对应标准误为",paste(signif(summary_table$SE,4),collapse="、"),"；IPCW动态AUC：",paste(auc_table$时间,signif(auc_table$IPCW动态AUC,4),collapse="；"),"。数值更高表示当前测试数据中区分能力更强。不确定性：C-index保留真实标准误，动态AUC未报告区间，不据点估计宣布显著优于对照。适用范围受测试随访窗口和删失独立性限制；下一步建议在独立外部队列评价相同冻结模型的区分度与校准。"),"实际生存预测结果、不确定性及外部适用范围")
'''

_BODIES["ml_nested_cv_leakage"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));cluster<-.sb_input("cluster_id");time<-.sb_input("time");splitcol<-.sb_input("external_validation_split");dat<-.sb_cols(c(out,features,cluster,time,splitcol));dat<-dat[!is.na(dat[[out]]),,drop=FALSE];if(out%in%features||any(c(cluster,time,splitcol)%in%features))stop("结局、切分/聚类/时间角色不应混入预测特征")
sets<-.ml_split(dat,splitcol,cluster,time);train<-dat[sets$train,,drop=FALSE];external<-dat[sets$test,,drop=FALSE];outer<-.ml_folds(nrow(train),3L,if(is.null(cluster))NULL else train[[cluster]],if(is.null(time))NULL else train[[time]]);grid<-lapply(as.numeric(.sb_input("lambda_grid",c(.01,.05,.2))),function(l)list(alpha=.5,lambda=l));rows<-list();audit<-list()
for(j in seq_along(outer)){f<-outer[[j]];a<-train[f$train,,drop=FALSE];b<-train[f$test,,drop=FALSE];tuning<-.ml_tune(a,out,features,"glmnet",grid,cluster,time);d<-.ml_design(a,b,features);fit<-.ml_fit(d$x,a[[out]],"glmnet",tuning$param);p<-fit$predict(d$test);m<-.ml_metrics(if(fit$binary).ml_binary(b[[out]])else b[[out]],p,fit$binary);rows[[j]]<-data.frame(评估=paste0("outer",j),训练数=nrow(a),测试数=nrow(b),lambda=tuning$param$lambda,m);audit[[j]]<-list(train=f$train,test=f$test,inner=tuning$folds)}
tuning<-.ml_tune(train,out,features,"glmnet",grid,cluster,time);d<-.ml_design(train,external,features);fit<-.ml_fit(d$x,train[[out]],"glmnet",tuning$param);p<-fit$predict(d$test);m<-.ml_metrics(if(fit$binary).ml_binary(external[[out]])else external[[out]],p,fit$binary);rows[[length(rows)+1]]<-data.frame(评估="external",训练数=nrow(train),测试数=nrow(external),lambda=tuning$param$lambda,m);summary_table<-do.call(rbind,rows)
.sb_table("primary_table",summary_table,"真实三层：内调参/外CV/最终独立测试")
.sb_plot("diagnostic_plot",ggplot2::ggplot(summary_table,ggplot2::aes(x=评估,y=结果,colour=指标))+ggplot2::geom_point()+ggplot2::facet_wrap(~指标,scales="free_y")+ggplot2::theme_bw(),"实际外层与独立测试性能")
.sb_text("manuscript_results",paste0("外层每折只在对应训练子集内做三折调参并独立拟合填补/编码；最终开发集调参后仅一次评价独立外部队列。",if(!is.null(cluster)&&!is.null(time))paste0("所有划分向前，并从训练中清除出现在测试波次的cluster；最终清除行数=",length(attr(sets,"purged_rows")),"。")else if(!is.null(cluster))"所有划分按cluster整组。"else if(!is.null(time))"所有划分为向前时间验证。"else"随机独立行切分。","真实外层审计记录数=",length(audit)),"实际嵌套验证与泄漏约束")
'''

_BODIES["ml_multiclass_multilabel_ordinal"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));labels<-as.character(.sb_input("label_set",character(0)));weight<-.sb_input("weight");cluster<-.sb_input("cluster_id");splitcol<-.sb_input("data_split");dat<-.sb_cols(c(out,features,labels,weight,cluster,splitcol));dat<-dat[complete.cases(dat[,c(out,labels,weight,cluster,splitcol),drop=FALSE]),,drop=FALSE];if(any(c(out,labels)%in%features))stop("标签不能混入特征");sets<-.ml_split(dat,splitcol,cluster);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE];order_spec<-as.character(.sb_input("label_order",character(0)));if(length(order_spec)){if(length(labels))stop("label_order只用于单个有序结局");if(any(!as.character(dat[[out]])%in%order_spec)||anyDuplicated(order_spec))stop("确认类别顺序未覆盖真实结局或含重复");train[[out]]<-ordered(as.character(train[[out]]),levels=order_spec);test[[out]]<-ordered(as.character(test[[out]]),levels=order_spec)};design<-.ml_design(train,test,features);tr<-as.data.frame(design$x);te<-as.data.frame(design$test);w<-if(is.null(weight))rep(1,nrow(train))else train[[weight]];if(any(w<=0))stop("训练权重须正");rows<-list();confusions<-list()
if(length(labels)){labels<-unique(c(out,labels));for(v in labels){label_positive<-if(v==out).sb_input("positive_class")else NULL;yy<-.ml_binary(train[[v]],label_positive);tr$.out<-yy;fit<-glm(.out~.,data=tr,family=binomial(),weights=w);p<-as.numeric(predict(fit,newdata=te,type="response"));rows[[v]]<-data.frame(任务=v,.ml_metrics(.ml_binary(test[[v]],label_positive),p,TRUE));confusions[[v]]<-data.frame(任务=v,as.data.frame(table(实际=.ml_binary(test[[v]],label_positive),预测=p>=.5)))};method<-"多标签逐标签二项模型"}
else {tr$.out<-train[[out]];if(is.ordered(tr$.out)){fit<-MASS::polr(.out~.,data=tr,weights=w,Hess=TRUE,method="logistic");prob<-predict(fit,newdata=te,type="probs");method<-"有序比例优势预测"}else if(is.factor(tr$.out)&&nlevels(tr$.out)>=3){fit<-nnet::multinom(.out~.,data=tr,weights=w,trace=FALSE,maxit=500);prob<-predict(fit,newdata=te,type="probs");method<-"多项softmax预测"}else stop("未提供label_set时需要确认多类别因子或有序因子")
 actual<-test[[out]];levels<-levels(train[[out]]);if(any(!as.character(actual)%in%levels))stop("测试有训练外标签");hat<-levels[max.col(prob)];logloss<--mean(log(pmax(prob[cbind(seq_len(nrow(test)),match(actual,levels))],1e-12)));brier<-mean(rowSums((prob-diag(length(levels))[match(actual,levels),,drop=FALSE])^2));rows[[1]]<-data.frame(任务=method,指标=c("Accuracy","LogLoss","MulticlassBrier"),结果=c(mean(hat==actual),logloss,brier));confusions[[1]]<-data.frame(任务=method,as.data.frame(table(实际=actual,预测=factor(hat,levels=levels)))) }
summary_table<-do.call(rbind,rows);confusion<-do.call(rbind,confusions)
.sb_table("primary_table",summary_table,"真实独立测试结构化标签性能");.sb_plot("diagnostic_plot",ggplot2::ggplot(confusion,ggplot2::aes(x=预测,y=实际,fill=Freq))+ggplot2::geom_tile()+ggplot2::facet_wrap(~任务)+ggplot2::theme_bw(),"实际标签混淆矩阵")
.sb_text("manuscript_results",paste0(method,"；训练 n=",nrow(train),"，独立测试 n=",nrow(test),"。权重用于训练拟合；cluster按独立对象留出；多标签不把标签组合硬改为单一类别。"),"实际标签结构与验证")
'''

_BODIES["ml_causal_uplift"] = r'''
treatment<-.sb_input("group");out<-.sb_input("outcome");features<-as.character(.sb_input("features"));propensity<-.sb_input("propensity_score");weight<-.sb_input("weight");sub<-.sb_input("subgroup");splitcol<-.sb_input("data_split");dat<-.sb_complete(.sb_cols(c(treatment,out,features,propensity,weight,sub,splitcol)),80L);.sb_numeric(dat,features);W<-.ml_binary(dat[[treatment]],.sb_input("treated_class"));Y<-if(is.factor(dat[[out]])||(!is.null(.sb_input("positive_class"))&&all(dat[[out]]%in%c(0,1)))) .ml_binary(dat[[out]])else dat[[out]];if(!is.numeric(Y))stop("因果森林结局需数值或二分类")
if(any(c(treatment,out,propensity,weight,sub,splitcol)%in%features))stop("特征包含结局/治疗/权重或切分角色");sets<-.ml_split(dat,splitcol);i<-sets$train;j<-sets$test;x<-as.matrix(dat[,features,drop=FALSE]);w<-if(is.null(weight))rep(1,nrow(dat))else dat[[weight]];if(any(w<=0))stop("样本权重需正");if(min(table(W[i]))<15)stop("两治疗组训练各至少十五例")
if(is.null(propensity)){pf<-grf::regression_forest(x[i,,drop=FALSE],W[i],sample.weights=w[i],num.trees=200,num.threads=1,seed=1729);ehat_train<-predict(pf)$predictions;ehat_test<-predict(pf,x[j,,drop=FALSE])$predictions}else{ehat_train<-dat[[propensity]][i];ehat_test<-dat[[propensity]][j]}
if(any(ehat_train<=.01|ehat_train>=.99)||any(ehat_test<=.01|ehat_test>=.99))stop("倾向评分缺重叠(0.01,0.99)，拒绝稳定策略值冒充")
mu<-grf::regression_forest(x[i,,drop=FALSE],Y[i],sample.weights=w[i],num.trees=200,num.threads=1,seed=1729);mtrain<-predict(mu)$predictions;mtest<-predict(mu,x[j,,drop=FALSE])$predictions
forest<-grf::causal_forest(x[i,,drop=FALSE],Y[i],W[i],Y.hat=mtrain,W.hat=ehat_train,sample.weights=w[i],num.trees=as.integer(.sb_input("trees",400L)),honesty=TRUE,num.threads=1,seed=as.integer(.sb_input("seed",1729L)));cate<-predict(forest,x[j,,drop=FALSE],estimate.variance=TRUE);tau<-cate$predictions;policy<-as.numeric(tau>0)
# Held-out DR value uses treatment-specific outcome models fitted only on training.
f0<-grf::regression_forest(x[i[W[i]==0],,drop=FALSE],Y[i[W[i]==0]],sample.weights=w[i[W[i]==0]],num.trees=200,num.threads=1,seed=1729);f1<-grf::regression_forest(x[i[W[i]==1],,drop=FALSE],Y[i[W[i]==1]],sample.weights=w[i[W[i]==1]],num.trees=200,num.threads=1,seed=1729);m0<-predict(f0,x[j,,drop=FALSE])$predictions;m1<-predict(f1,x[j,,drop=FALSE])$predictions;dr1<-m1+W[j]*(Y[j]-m1)/ehat_test;dr0<-m0+(1-W[j])*(Y[j]-m0)/(1-ehat_test);drtau<-dr1-dr0;drpolicy<-ifelse(policy==1,dr1,dr0);sw<-w[j]/sum(w[j]);ate<-sum(sw*drtau);pv<-sum(sw*drpolicy)
summary_table<-data.frame(分层="总体",测试n=length(j),DR_ATE=ate,策略值=pv,全治疗值=sum(sw*dr1),无治疗值=sum(sw*dr0),治疗推荐比例=sum(sw*policy))
if(!is.null(sub))summary_table<-rbind(summary_table,do.call(rbind,lapply(split(seq_along(j),dat[[sub]][j]),function(k){wk<-w[j][k]/sum(w[j][k]);data.frame(分层=as.character(dat[[sub]][j[k[1]]]),测试n=length(k),DR_ATE=sum(wk*drtau[k]),策略值=sum(wk*drpolicy[k]),全治疗值=sum(wk*dr1[k]),无治疗值=sum(wk*dr0[k]),治疗推荐比例=sum(wk*policy[k]))})))
individual<-data.frame(行=j,CATE=tau,SE=sqrt(cate$variance.estimates),DR效应=drtau,冻结策略=policy);o<-order(tau,decreasing=TRUE);uplift<-data.frame(份额=seq_along(o)/length(o),累计DR增益=cumsum(w[j][o]*drtau[o])/sum(w[j]))
.sb_table("primary_table",summary_table,"独立测试双重稳健策略价值与异质效应");.sb_plot("diagnostic_plot",ggplot2::ggplot(uplift,ggplot2::aes(x=份额,y=累计DR增益))+ggplot2::geom_line()+ggplot2::theme_bw(),"实际按CATE排序的测试DR增益")
.sb_text("manuscript_results",paste0("真实诚实 causal_forest；治疗对照=",if(is.factor(dat[[treatment]]))paste0(if(is.null(.sb_input("treated_class")))levels(dat[[treatment]])[2]else .sb_input("treated_class")," vs 其余类别")else paste0(if(is.null(.sb_input("treated_class")))1 else .sb_input("treated_class")," vs 其余类别"),"；训练 OOB nuisance，独立测试 CATE、DR策略值。需一致性、条件可交换性与正值性；不由随机森林自动保证因果识别。总体测试 DR ATE=",ate,"，策略值=",pv,"；测试决策未用测试结局调整。"),"真实因果机器学习估计")
'''

_BODIES["ml_clinical_score_simplification"] = r'''
out<-.sb_input("outcome");features<-as.character(.sb_input("features"));splitcol<-.sb_input("external_validation_split");out2<-.sb_input("outcome_2");sub<-.sb_input("subgroup");dat<-.sb_cols(c(out,features,splitcol,out2,sub));dat<-dat[!is.na(dat[[out]]),,drop=FALSE];if(!is.factor(dat[[out]])||nlevels(dat[[out]])!=2L)stop("评分简化需确认二分类因子");.sb_numeric(dat,features);sets<-.ml_split(dat,splitcol);train<-dat[sets$train,,drop=FALSE];test<-dat[sets$test,,drop=FALSE]
grid<-lapply(as.numeric(.sb_input("lambda_grid",c(.02,.1,.3))),function(l)list(alpha=1,lambda=l));tuning<-.ml_tune(train,out,features,"glmnet",grid);
medians<-vapply(train[,features,drop=FALSE],median,numeric(1),na.rm=TRUE);for(v in features){train[[v]][is.na(train[[v]])]<-medians[v];test[[v]][is.na(test[[v]])]<-medians[v]};means<-vapply(train[,features,drop=FALSE],mean,numeric(1));sds<-vapply(train[,features,drop=FALSE],sd,numeric(1));if(any(sds==0))stop("评分特征常数");x<-scale(as.matrix(train[,features]),center=means,scale=sds);xt<-scale(as.matrix(test[,features]),center=means,scale=sds)
model<-glmnet::glmnet(x,.ml_binary(train[[out]]),family="binomial",alpha=1,lambda=tuning$param$lambda,standardize=FALSE);coeff<-as.numeric(coef(model))[-1];intercept<-as.numeric(coef(model))[1];nonzero<-which(coeff!=0);if(!length(nonzero))stop("训练选择无特征，不构造评分规则");unit<-as.numeric(.sb_input("point_unit",.25));if(unit<=0)stop("point_unit应正")
points<-round(coeff/unit);train_score<-as.numeric(x%*%points);test_score<-as.numeric(xt%*%points);score_model<-glm(.ml_binary(train[[out]])~train_score,family=binomial());score_prob<-plogis(coef(score_model)[1]+coef(score_model)[2]*test_score);full_prob<-as.numeric(predict(model,newx=xt,type="response"));performance<-rbind(data.frame(项目="原始稀疏模型",.ml_metrics(.ml_binary(test[[out]]),full_prob,TRUE)),data.frame(项目="整数系数评分",.ml_metrics(.ml_binary(test[[out]]),score_prob,TRUE)))
rule<-data.frame(项目=features,指标="每个训练SD的整数权重",结果=points,训练均值=means,训练SD=sds,填补值=medians);table<-rbind(data.frame(项目=rule$项目,指标=rule$指标,结果=rule$结果,说明=paste0("z=(x-",signif(rule$训练均值,5),")/",signif(rule$训练SD,5))),data.frame(项目=performance$项目,指标=performance$指标,结果=performance$结果,说明="独立测试性能"))
primary_event<-levels(train[[out]])[2];extra_note<-"";if(!is.null(out2))extra_note<-paste0("第二结局外部相关评估：\n",paste(capture.output(print(.ml_metrics(.ml_binary(test[[out2]],.sb_input("secondary_positive_class")),score_prob,TRUE),row.names=FALSE)),collapse="\n"));if(!is.null(sub))extra_note<-paste0(extra_note,"\n亚组 Brier：",paste(tapply((.ml_binary(test[[out]])-score_prob)^2,test[[sub]],mean),collapse=", "))
cal<-.ml_calibration(.ml_binary(test[[out]]),score_prob);table$事件类<-primary_event;.sb_table("primary_table",table,"训练确定评分规则与独立测试性能")
.sb_plot("diagnostic_plot",ggplot2::ggplot(cal,ggplot2::aes(x=预测均值,y=实际风险))+ggplot2::geom_point()+ggplot2::geom_abline(slope=1,intercept=0,linetype=2)+ggplot2::theme_bw(),"整数权重评分独立校准")
.sb_text("manuscript_results",paste0("仅训练选择/标准化/系数取整和评分概率映射；评分=sum(integer_weight × training_z)。连续特征仍需标准化，不冒称已离散化床旁加分表。概率=logistic(",coef(score_model)[1]," + ",coef(score_model)[2]," ×score)。\n",extra_note),"真实评分简化规则与限制")
'''

INPUTS = {'tree_boosting': {'outcome': 'outcome_v', 'features': 'features_v', 'test_split': 'test_split_v', 'tuning_grid': 'tuning_grid_v', 'analysis_method': 'analysis_method_v', 'seed': 'seed_v', 'analysis_task': 'analysis_task_v', 'positive_class': 'positive_class_v'}, 'regularized_models': {'outcome': 'outcome_v', 'features': 'features_v', 'penalty': 'penalty_v', 'validation_split': 'validation_split_v', 'lambda_grid': 'lambda_grid_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v', 'analysis_task': 'analysis_task_v'}, 'imbalanced_classification': {'outcome': 'outcome_v', 'features': 'features_v', 'threshold': 'threshold_v', 'weight': 'weight_v', 'data_split': 'data_split_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v'}, 'survival_ml': {'positive_class': 'positive_class_v', 'followup_time': 'followup_time_v', 'event': 'event_v', 'features': 'features_v', 'validation_split': 'validation_split_v', 'trees': 'trees_v', 'prediction_times': 'prediction_times_v', 'seed': 'seed_v'}, 'ml_explainability': {'trained_model': 'trained_model_v', 'features': 'features_v', 'outcome': 'outcome_v', 'subject_id': 'subject_id_v', 'resamples': 'resamples_v', 'pdp_feature': 'pdp_feature_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v'}, 'feature_selection': {'outcome': 'outcome_v', 'features': 'features_v', 'test_split': 'test_split_v', 'resampling_specification': 'resampling_specification_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v', 'analysis_task': 'analysis_task_v'}, 'unsupervised_ml': {'data_object': 'data_object_v', 'sample_id': 'sample_id_v', 'standardization_variables': 'standardization_variables_v', 'external_validation_variable': 'external_validation_variable_v', 'analysis_method': 'analysis_method_v', 'eps': 'eps_v', 'min_pts': 'min_pts_v', 'seed': 'seed_v'}, 'automl_tabular': {'outcome': 'outcome_v', 'features': 'features_v', 'data_split': 'data_split_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v', 'analysis_task': 'analysis_task_v'}, 'probability_calibration_ml': {'outcome': 'outcome_v', 'prediction': 'prediction_v', 'data_split': 'data_split_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v'}, 'model_drift_shift': {'data_source': 'data_source_v', 'features': 'features_v', 'outcome': 'outcome_v', 'predicted_score': 'predicted_score_v', 'reference_source': 'reference_source_v', 'positive_class': 'positive_class_v'}, 'sparse_text_ml': {'text': 'text_v', 'class_label': 'class_label_v', 'data_split': 'data_split_v', 'lambda_grid': 'lambda_grid_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v'}, 'anomaly_detection_ml': {'features': 'features_v', 'known_anomaly_label': 'known_anomaly_label_v', 'group': 'group_v', 'neighbors': 'neighbors_v', 'threshold': 'threshold_v', 'positive_class': 'positive_class_v'}, 'association_recommender': {'transaction_id': 'transaction_id_v', 'item_id': 'item_id_v', 'time': 'time_v', 'support': 'support_v', 'confidence': 'confidence_v', 'seed': 'seed_v'}, 'dimensionality_reduction_ml': {'features': 'features_v', 'group': 'group_v', 'analysis_method': 'analysis_method_v', 'neighbors': 'neighbors_v', 'perplexity': 'perplexity_v', 'seed': 'seed_v'}, 'stacking_ensemble_ml': {'outcome': 'outcome_v', 'features': 'features_v', 'data_split': 'data_split_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v', 'analysis_task': 'analysis_task_v'}, 'cost_sensitive_threshold': {'outcome': 'outcome_v', 'prediction': 'prediction_v', 'false_positive_cost': 'false_positive_cost_v', 'false_negative_cost': 'false_negative_cost_v', 'group': 'group_v', 'data_split': 'data_split_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v'}, 'ml_nested_cv_leakage': {'outcome': 'outcome_v', 'features': 'features_v', 'cluster_id': 'cluster_id_v', 'time': 'time_v', 'external_validation_split': 'external_validation_split_v', 'lambda_grid': 'lambda_grid_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v', 'analysis_task': 'analysis_task_v'}, 'ml_external_transportability': {'outcome': 'outcome_v', 'prediction': 'prediction_v', 'data_source': 'data_source_v', 'subgroup': 'subgroup_v', 'time': 'time_v', 'center': 'center_v', 'positive_class': 'positive_class_v'}, 'ml_fairness_subgroup': {'outcome': 'outcome_v', 'prediction': 'prediction_v', 'subgroup': 'subgroup_v', 'threshold': 'threshold_v', 'weight': 'weight_v', 'positive_class': 'positive_class_v'}, 'ml_conformal_prediction': {'outcome': 'outcome_v', 'prediction': 'prediction_v', 'calibration_split': 'calibration_split_v', 'subgroup': 'subgroup_v', 'weight': 'weight_v', 'alpha': 'alpha_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v', 'analysis_task': 'analysis_task_v'}, 'ml_multiclass_multilabel_ordinal': {'label_order': 'label_order_v', 'outcome': 'outcome_v', 'features': 'features_v', 'label_set': 'label_set_v', 'weight': 'weight_v', 'cluster_id': 'cluster_id_v', 'data_split': 'data_split_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v'}, 'ml_causal_uplift': {'group': 'group_v', 'outcome': 'outcome_v', 'features': 'features_v', 'propensity_score': 'propensity_score_v', 'weight': 'weight_v', 'subgroup': 'subgroup_v', 'data_split': 'data_split_v', 'trees': 'trees_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v', 'treated_class': 'treated_class_v'}, 'ml_clinical_score_simplification': {'secondary_positive_class':'secondary_positive_class_v','outcome': 'outcome_v', 'features': 'features_v', 'external_validation_split': 'external_validation_split_v', 'outcome_2': 'outcome_2_v', 'subgroup': 'subgroup_v', 'lambda_grid': 'lambda_grid_v', 'point_unit': 'point_unit_v', 'seed': 'seed_v', 'positive_class': 'positive_class_v'}}

ROLE_MAPS = {'tree_boosting': {'table_1': 'table_1', 'figure_1': 'figure_1', 'calibration_output': 'calibration_output', 'roc_output': 'roc_output', 'figure_2': 'figure_2', 'roc_output_2': 'roc_output_2', 'manuscript_results': 'manuscript_results'}, 'regularized_models': {'figure_1': 'figure_1', 'table_1': 'table_1', 'figure_2': 'figure_2', 'table_2': 'table_2', 'manuscript_results': 'manuscript_results'}, 'imbalanced_classification': {'figure_1': 'figure_1', 'table_1': 'table_1', 'table_2': 'table_2', 'table_3': 'table_3', 'table_4': 'table_4', 'roc_output': 'roc_output', 'table_6': 'table_6', 'manuscript_results': 'manuscript_results'}, 'survival_ml': {'table_1': 'table_1', 'table_2': 'table_2', 'figure_1': 'figure_1', 'calibration_output': 'calibration_output', 'table_3': 'table_3'}, 'ml_explainability': {'figure_1': 'figure_1', 'table_1': 'table_1', 'figure_2': 'figure_2', 'manuscript_results': 'manuscript_results', 'figure_3': 'figure_3', 'figure_4': 'figure_4', 'manuscript_results_2': 'manuscript_results_2', 'table_2': 'table_2'}, 'feature_selection': {'table_1': 'table_1', 'figure_1': 'figure_1', 'table_2': 'table_2', 'table_3': 'table_3'}, 'unsupervised_ml': {'figure_1': 'figure_1', 'table_1': 'table_1', 'table_2': 'table_2', 'outlier_summary': 'outlier_summary', 'table_4': 'table_4', 'table_5': 'table_5', 'figure_2': 'figure_2'}, 'automl_tabular': {'table_1': 'table_1', 'table_2': 'table_2', 'text_1': 'text_1', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'probability_calibration_ml': {'calibration_output': 'calibration_output', 'table_1': 'table_1', 'calibration_output_2': 'calibration_output_2', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'model_drift_shift': {'table_1': 'table_1', 'figure_1': 'figure_1', 'text_1': 'text_1', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'sparse_text_ml': {'table_1': 'table_1', 'table_2': 'table_2', 'manuscript_results': 'manuscript_results', 'method_note': 'method_note', 'manuscript_results_2': 'manuscript_results_2', 'diagnostic_output': 'diagnostic_output'}, 'anomaly_detection_ml': {'outlier_summary': 'outlier_summary', 'outlier_summary_2': 'outlier_summary_2', 'analysis_recommendation': 'analysis_recommendation', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'association_recommender': {'table_1': 'table_1', 'figure_1': 'figure_1', 'table_2': 'table_2', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'dimensionality_reduction_ml': {'figure_1': 'figure_1', 'manuscript_results': 'manuscript_results', 'text_2': 'text_2', 'method_note': 'method_note', 'manuscript_results_2': 'manuscript_results_2'}, 'stacking_ensemble_ml': {'table_1': 'table_1', 'table_2': 'table_2', 'text_1': 'text_1', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'cost_sensitive_threshold': {'table_1': 'table_1', 'figure_1': 'figure_1', 'table_2': 'table_2', 'method_note': 'method_note', 'manuscript_results': 'manuscript_results', 'diagnostic_output': 'diagnostic_output'}, 'ml_nested_cv_leakage': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'ml_external_transportability': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'ml_fairness_subgroup': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'ml_conformal_prediction': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'ml_multiclass_multilabel_ordinal': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'ml_causal_uplift': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}, 'ml_clinical_score_simplification': {'primary_table': 'primary_table', 'diagnostic_plot': 'diagnostic_plot', 'manuscript_results': 'manuscript_results'}}

_METHODS = {"regularized_models":("glmnet",),"feature_selection":("glmnet",),"imbalanced_classification":("ranger",),"automl_tabular":("glmnet","ranger"),"stacking_ensemble_ml":("glmnet","ranger"),"ml_nested_cv_leakage":("glmnet",),"ml_clinical_score_simplification":("glmnet",)}

# The profile consolidates legacy ROC fragments. Its resampling contract is
# explicitly backed by the training CV table, never by test threshold metrics.
ROLE_MAPS["tree_boosting"].update(ml_resampling_metrics="roc_output_2", threshold_performance_table="threshold_performance_table")
ROLE_MAPS["dimensionality_reduction_ml"].update(scientific_result_table="scientific_result_table", pca_structure_table="pca_structure_table")
ROLE_MAPS["imbalanced_classification"].update(prediction_performance_table="performance_table", threshold_curve_table="threshold_curve_table", threshold_sensitivity_table="threshold_sensitivity_table", threshold_specificity_table="threshold_specificity_table", frozen_threshold_confusion_table="frozen_threshold_confusion_table")
ROLE_MAPS["survival_ml"].update(survival_time_dependent_performance="survival_performance_table", survival_concordance_table="survival_concordance_table", survival_auc_table="survival_auc_table", scientific_result_interpretation="scientific_result_interpretation")
ROLE_MAPS["survival_ml"]["survival_effect_estimates"]="table_3"
ROLE_MAPS["feature_selection"]["scientific_result_interpretation"]="scientific_result_interpretation"
ROLE_MAPS["unsupervised_ml"]["scientific_result_interpretation"]="scientific_result_interpretation"
ROLE_MAPS["probability_calibration_ml"].update(prediction_performance_table="calibration_output_2", brier_comparison_table="brier_comparison_table")
ROLE_MAPS["cost_sensitive_threshold"].update(prediction_performance_table="cost_performance_table", training_cost_curve_table="training_cost_curve_table", frozen_threshold_cost_summary="frozen_threshold_cost_summary")

_CANONICAL_AUXILIARY_ROLES = {
    "tree_boosting":("ml_resampling_metrics", {"roc_output":"threshold_performance_table"}),
    "imbalanced_classification":("prediction_performance_table", {"table_1":"threshold_curve_table", "table_2":"threshold_sensitivity_table", "table_3":"threshold_specificity_table", "table_6":"frozen_threshold_confusion_table"}),
    "survival_ml":("survival_time_dependent_performance", {"table_1":"survival_concordance_table", "table_2":"survival_auc_table"}),
    "probability_calibration_ml":("prediction_performance_table", {"table_1":"brier_comparison_table"}),
    "cost_sensitive_threshold":("prediction_performance_table", {"table_1":"training_cost_curve_table", "table_2":"frozen_threshold_cost_summary"}),
}


def _fit_helper(methods):
    source = _HELPERS[".ml_fit"]
    names = ("glmnet", "ranger", "randomForest", "xgboost", "gbm")
    starts = [source.index(('if' if i == 0 else 'else if') + '(method=="' + n + '")') for i, n in enumerate(names)]
    end = source.index('else stop("未知学习器")')
    chunks = [source[starts[i]:starts[i+1] if i+1<len(names) else end].strip() for i in range(len(names)) if names[i] in methods]
    if not chunks:
        raise ValueError("No supported learner selected")
    chunks[0] = chunks[0].removeprefix("else ")
    return source[:starts[0]] + "\n".join(chunks) + "\n " + source[end:]


def _core_for(template_id: str):
    def render(plan: AnalysisPlan):
        body = _BODIES[template_id]
        canonical = _CANONICAL_AUXILIARY_ROLES.get(template_id)
        if canonical and any(o.role == canonical[0] for o in plan.required_outputs):
            # Separate supporting tables from aliases consolidated by the
            # production portfolio, so metadata cannot claim partial tables
            # provide the dedicated complete performance result.
            for original, auxiliary in canonical[1].items():
                body = body.replace('.sb_table("'+original+'",', '.sb_table("'+auxiliary+'",')
        if template_id != "ml_explainability":
            body = r'features_for_audit<-as.character(.sb_input("features",.sb_input("data_object",character(0))));for(r in c("outcome","prediction","class_label","group","test_split","validation_split","data_split","external_validation_split","cluster_id","time","subgroup","weight","propensity_score","event","followup_time","known_anomaly_label","external_validation_variable")){v<-.sb_inputs[[r]];if(!is.null(v)&&any(as.character(v)%in%features_for_audit))stop("预测特征与保护角色重叠：",r)}' + "\n" + body
        if template_id != "ml_explainability":
            for marker in ("dat<-.sb_cols(", "dat<-.sb_complete("):
                offset = body.find(marker)
                if offset < 0:
                    continue
                cursor = offset + len(marker)
                depth = 1
                quote = ""
                while depth:
                    char = body[cursor]
                    if quote:
                        if char == quote and body[cursor-1] != "\\":
                            quote = ""
                    elif char in "\"'":
                        quote = char
                    elif char == "(":
                        depth += 1
                    elif char == ")":
                        depth -= 1
                    cursor += 1
                body = body[:cursor] + ";dat<-.ml_role_outcomes(dat,binary_required=" + ("TRUE" if template_id in {"tree_boosting","imbalanced_classification","sparse_text_ml","ml_clinical_score_simplification"} else "FALSE") + ")" + body[cursor:]
                break
        needed = {n for n in _HELPERS if n + "(" in body}
        for _ in range(len(_HELPERS)):
            expanded = needed | {n for n in _HELPERS if any(n + "(" in _HELPERS[x] for x in needed)}
            if expanded == needed:
                break
            needed = expanded
        if template_id in {"unsupervised_ml", "dimensionality_reduction_ml"}:
            names = ("kmeans","mclust","dbscan") if template_id == "unsupervised_ml" else ("pca","umap","tsne")
            item = plan.resolved_inputs.get("analysis_method")
            selected = str(item.value) if item else names[0]
            if selected not in names:
                raise ValueError("Unsupported algorithm: " + selected)
            first = body.index('if(method=="' + names[0] + '")')
            starts = [first] + [body.index('else if(method=="' + n + '")') for n in names[1:]]
            end = body.index('else stop("支持 ', first)
            index = names.index(selected)
            chunk = body[starts[index]:starts[index+1] if index+1<len(names) else end].strip().removeprefix("else ")
            body = body[:first] + chunk + " " + body[end:]
            if template_id == "unsupervised_ml" and "external_validation_variable" not in plan.resolved_inputs:
                body = body.replace('if(!is.null(external))quality$外部ARI<-mclust::adjustedRandIndex(membership,dat[[external]]);', "")
        methods = _METHODS.get(template_id, ())
        if template_id == "tree_boosting":
            item = plan.resolved_inputs.get("analysis_method")
            methods = (str(item.value) if item else "ranger",)
        helpers = "\n".join(_fit_helper(methods) if n == ".ml_fit" else code for n, code in _HELPERS.items() if n in needed)
        pairs = [json.dumps(k) + " = " + INPUTS[template_id][k] for k in INPUTS[template_id] if k in plan.resolved_inputs]
        return ".sb_inputs <- list(" + ",".join(pairs) + ")\n" + _COMMON + "\n" + helpers + "\n" + body
    return render


CORES = {template_id: _core_for(template_id) for template_id in _BODIES}
REFERENCE_CASES = {'tree_boosting': '真实R：ranger/randomForest/gbm/xgboost四种实际学习器，训练内调参及独立分类/回归测试，命名事件类。', 'regularized_models': '真实R：训练内glmnet选择、真实系数路径；显式分类和保留数值0/1回归角色分别验证。', 'imbalanced_classification': '真实R：仅训练平衡类别权重、预先阈值、独立测试PR/AP、ROC与混淆矩阵。', 'survival_ml': '真实R：生存随机森林、训练Cox对照、独立测试C-index/动态AUC与IPCW校准。', 'ml_explainability': '真实R：原生已训练lm/ranger模型bundle、训练背景fastshap、PDP与命名事件类；不重新拟合。', 'feature_selection': '真实R：仅训练子采样Lasso选择频率与完整保留测试集。', 'unsupervised_ml': '真实R：kmeans/mclust/dbscan各自实际模型，轮廓/BIC/噪声保持方法含义。', 'automl_tabular': '真实R：预先Lasso/Ridge/ranger候选集，训练内CV选择与独立测试。', 'probability_calibration_ml': '真实R：仅训练Platt系数及独立测试校准/Brier参考值。', 'model_drift_shift': '真实R：确认参考来源的KS、参考分箱PSI及同族Holm校正。', 'sparse_text_ml': '真实R：每个训练折独立词表、稀疏glmnet选择与独立测试AP。', 'anomaly_detection_ml': '真实R：dbscan LOF、预先异常阈值及逐行实际分数。', 'association_recommender': '真实R：训练交易Apriori，测试交易遮盖物品实际推荐与命中。', 'dimensionality_reduction_ml': '真实R：PCA/UMAP/t-SNE三种真实投影；仅PCA报告解释方差。', 'stacking_ensemble_ml': '真实R：四折OOF基模型预测、非负Ridge元模型、全训练重拟合和独立测试。', 'cost_sensitive_threshold': '真实R：仅训练FP/FN成本选择阈值及冻结阈值独立测试成本。', 'ml_nested_cv_leakage': '真实R：内调参/外CV/独立测试，整组、向前时间及联合清除cluster；拒绝特征与结构角色泄漏。', 'ml_external_transportability': '真实R：冻结预测按来源/亚组独立评估与实际校准，不用外部结局调参。', 'ml_fairness_subgroup': '真实R：行权重、确认阈值及亚组敏感度与加权参考值。', 'ml_conformal_prediction': '真实R：有限样本校准分位数、独立测试区间与二分类Mondrian/密度比加权集合。', 'ml_multiclass_multilabel_ordinal': '真实R：多项softmax、逐标签二项和有序polr预测，保持标签结构与独立测试。', 'ml_causal_uplift': '真实R：诚实grf causal_forest、训练OOB nuisance、独立测试CATE和DR策略值。', 'ml_clinical_score_simplification': '真实R：仅训练稀疏选择、整数系数×训练标准化特征评分、训练概率映射和独立测试。'}

REFERENCE_CASES["dimensionality_reduction_ml"] = "真实R：PCA/UMAP/t-SNE实际坐标与源数据行对应；仅PCA导出rotation/sdev/解释方差结构，含缺失病例行映射校验。"
REFERENCE_CASES["survival_ml"] = "真实R：生存森林/Cox独立测试C-index/SE、动态AUC与IPCW校准；仅训练Cox对照HR及95%Wald区间，模型含义明确。"
for _id in ("tree_boosting", "stacking_ensemble_ml", "ml_nested_cv_leakage", "dimensionality_reduction_ml", "imbalanced_classification", "survival_ml", "feature_selection", "unsupervised_ml", "probability_calibration_ml", "cost_sensitive_threshold"):
    REFERENCE_CASES[_id] += " 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。"
