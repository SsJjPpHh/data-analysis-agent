"""Statistical cores with numerical regression checks in test_method_numerics.

The tests cover specified estimators and data shapes, not scientific acceptance
of an arbitrary study. Keep that distinction in runtime diagnostics and UI.
"""

NONINFERIORITY_CORE = r'''
.step <- "confirm-design"
if (!exists("reference_v") || !exists("direction_v") || !exists("population_level_v"))
  stop("请重新准备此模板，确认对照组、结局有利方向及分析集水平后运行。")
method <- if (exists("method_v")) as.character(method_v) else "noninferiority"
direction <- as.character(direction_v)
if (!method %in% c("noninferiority", "equivalence")) stop("不支持的分析方法")
if (!direction %in% c("higher_better", "lower_better")) stop("必须确认结局有利方向")
margin <- as.numeric(margin_v)
alpha <- if (exists("alpha_v")) as.numeric(alpha_v) else 0.025
if (length(margin)!=1L || !is.finite(margin) || margin<=0) stop("界值必须为正数；方向由结局有利方向单独指定")
if (length(alpha)!=1L || !is.finite(alpha) || alpha<=0 || alpha>=0.5) stop("单侧 alpha 必须在 0 与 0.5 之间")
df <- dataset[!is.na(dataset[[pop_v]]) & as.character(dataset[[pop_v]])==as.character(population_level_v), , drop=FALSE]
df <- df[stats::complete.cases(df[, c(outcome_v, group_v), drop=FALSE]), , drop=FALSE]
if (!is.numeric(df[[outcome_v]]) || any(!is.finite(df[[outcome_v]])) || length(unique(df[[outcome_v]]))<3)
  stop("此固定脚本只适用于连续数值终点；二分类、生存或其他终点需按相应效应尺度另行分析。")
df[[group_v]] <- droplevels(factor(df[[group_v]]))
gl <- levels(df[[group_v]])
if (length(gl)!=2L || !as.character(reference_v) %in% gl) stop("所选分析集需恰好两个组，且包含指定对照组")
control <- as.character(reference_v); treatment <- setdiff(gl, control)
g1 <- as.character(df[[group_v]])==control
if (min(sum(g1),sum(!g1))<2L) stop("每组至少需要两个有效观测")
.step <- "welch-interval"
diff <- mean(df[[outcome_v]][!g1])-mean(df[[outcome_v]][g1])
conf_level <- 1-2*alpha
tt <- stats::t.test(x=df[[outcome_v]][!g1], y=df[[outcome_v]][g1], conf.level=conf_level)
ci <- unname(tt$conf.int[1:2])
se <- sqrt(stats::var(df[[outcome_v]][!g1])/sum(!g1)+stats::var(df[[outcome_v]][g1])/sum(g1))
p_lower <- stats::pt((diff+margin)/se, df=unname(tt$parameter), lower.tail=FALSE)
p_upper <- stats::pt((diff-margin)/se, df=unname(tt$parameter))
if (method=="equivalence") {
  accepted <- ci[1]>-margin && ci[2]<margin
  decision <- if (accepted) "等效成立" else "等效未成立"
  test_p <- max(p_lower,p_upper)
  rule <- sprintf("双单侧检验：区间须完全位于 (%.4f, %.4f) 内",-margin,margin)
  boundaries <- c(-margin,margin)
} else {
  accepted <- if (direction=="higher_better") ci[1]>-margin else ci[2]<margin
  decision <- if (accepted) "非劣效成立" else "非劣效未成立"
  test_p <- if (direction=="higher_better") p_lower else p_upper
  rule <- if (direction=="higher_better") sprintf("CI 下限须高于 %.4f",-margin) else sprintf("CI 上限须低于 %.4f",margin)
  boundaries <- if (direction=="higher_better") -margin else margin
}
ci_label <- sprintf("%g%%CI",100*conf_level)
ni_table <- data.frame(
  指标=c("对照组", "试验组", "分析集", "对照组样本量", "试验组样本量", "对照组均值", "试验组均值",
         "均值差(试验-对照)",ci_label,"非劣效界值","判定","检验P值"),
  结果=c(control,treatment,as.character(population_level_v),sum(g1),sum(!g1),
         mean(df[[outcome_v]][g1]),mean(df[[outcome_v]][!g1]),diff,
         sprintf("[%.4f, %.4f]",ci[1],ci[2]),margin,decision,.sk_fmt_p(test_p)), stringsAsFactors=FALSE)
ci_fig <- ggplot2::ggplot(data.frame(estimate=diff,lower=ci[1],upper=ci[2]),ggplot2::aes(x=estimate,y=0)) +
  ggplot2::geom_vline(xintercept=boundaries,colour="#CC3311") + ggplot2::geom_point(size=3) +
  ggplot2::geom_errorbar(ggplot2::aes(xmin=lower,xmax=upper),orientation="y",width=.15) +
  ggplot2::labs(title=paste(ci_label,decision),x=paste0(treatment," - ",control),y=NULL) + ggplot2::theme_bw()
results_txt <- sprintf("分析集 %s：试验组 %s 减对照组 %s，均值差=%.4f（%s %.4f 至 %.4f）；%s；%s，检验 P=%s。",
  population_level_v,treatment,control,diff,ci_label,ci[1],ci[2],rule,decision,.sk_fmt_p(test_p))
margin_txt <- sprintf("界值 %.4f；单侧 alpha=%g；有利方向=%s。采用独立两组 Welch 均值差区间。界值的临床依据、分析集定义及研究设计仍需研究者复核。",
  margin,alpha,if (direction=="higher_better") "数值越大越好" else "数值越小越好")
paper_df <- data.frame(论文句=results_txt,stringsAsFactors=FALSE)
.diag_status[["界值方向检查"]] <- "passed"
.diag_status[["分析集一致性检查"]] <- "passed"
.diag_status[["界值来源、分析集一致性和置信区间判定检查"]] <- "warning"
.out[["ni_table"]] <- list(kind="table",payload=ni_table,title="非劣效/等效判断表")
.out[["ci_fig"]] <- list(kind="figure",payload=ci_fig,title="效应区间与判定界值")
.out[["results_text"]] <- list(kind="text",payload=results_txt,title="结果解释")
.out[["margin_text"]] <- list(kind="text",payload=margin_txt,title="界值与分析范围说明")
.out[["paper_table"]] <- list(kind="table",payload=paper_df,title="论文写法")
'''


_GEE_FIT = r'''
  fit <- geepack::geeglm(fixed_formula,id=df[[subject_v]],data=df,family=family,corstr="exchangeable",std.err="san.se")
  cf <- stats::coef(fit); variance <- stats::vcov(fit)
  convergence_ok <- identical(as.integer(fit$geese$error),0L)
  rand_table <- data.frame(成分="工作相关系数 alpha",数值=as.numeric(fit$geese$alpha),stringsAsFactors=FALSE)
  rand_txt <- "GEE 使用 exchangeable 工作相关结构与稳健 sandwich 协方差；不估计随机效应方差。"
  singular <- FALSE
'''

_MIXED_FIT = r'''
  groups <- unique(c(subject_v,random_group))
  for (cn in groups) df[[cn]] <- factor(df[[cn]])
  formula <- stats::as.formula(paste0("`",outcome_v,"` ~ ",rhs," + ",
    paste(sprintf("(1 | `%s`)",groups),collapse=" + ")))
  fit <- if (binary) lme4::glmer(formula,data=df,family=family) else lme4::lmer(formula,data=df)
  cf <- lme4::fixef(fit); variance <- stats::vcov(fit)
  convergence_ok <- is.null(fit@optinfo$conv$lme4$messages) && all(fit@optinfo$conv$opt==0)
  singular <- lme4::isSingular(fit)
  vc <- as.data.frame(lme4::VarCorr(fit))
  rand_table <- data.frame(成分=paste(vc$grp,vc$var1),数值=vc$vcov,stringsAsFactors=FALSE)
  rand_txt <- "混合模型拟合随机截距；固定效应报告 Wald 区间。高斯 LMM 不提供未经自由度校正的 P 值。"
'''


LONGITUDINAL_CORE = r'''
.step <- "setup"
method_v <- if (exists("method_v")) as.character(method_v) else "mixed"
if (!method_v %in% c("mixed","gee")) stop("纵向方法必须为 mixed 或 gee")
fx <- if (exists("fixed_v")) as.character(fixed_v) else character(0)
fx <- setdiff(fx,c(outcome_v,time_v,subject_v))
random_group <- if (exists("rand_v") && length(rand_v)) as.character(rand_v) else subject_v
needed <- unique(c(outcome_v,time_v,subject_v,fx,if (method_v=="mixed") random_group))
df <- dataset[,needed,drop=FALSE]
df <- df[stats::complete.cases(df),,drop=FALSE]
df <- df[order(df[[subject_v]],df[[time_v]]),,drop=FALSE]
df[[subject_v]] <- factor(df[[subject_v]])
df[[time_v]] <- factor(df[[time_v]])
for (cn in fx) if (is.character(df[[cn]])) df[[cn]] <- factor(df[[cn]])
n_sub <- nlevels(df[[subject_v]])
if (n_sub<5 || nlevels(df[[time_v]])<2) stop("至少需要 5 名受试者和两个时间点")
if (anyDuplicated(df[,c(subject_v,time_v),drop=FALSE])) stop("同一受试者同一时间点存在重复观测，请确认数据结构")
binary <- length(unique(df[[outcome_v]]))==2L
if (binary) {
  outcome_levels <- levels(factor(df[[outcome_v]]))
  df[[outcome_v]] <- as.numeric(factor(df[[outcome_v]],levels=outcome_levels))-1
} else if (!is.numeric(df[[outcome_v]])) stop("此脚本支持连续或二分类结局；其他结局需要相应分布模型")
if (any(!is.finite(df[[outcome_v]]))) stop("结局存在非有限值")
family <- if (binary) stats::binomial() else stats::gaussian()
rhs <- paste(sprintf("`%s`",c(time_v,fx)),collapse=" + ")
fixed_formula <- stats::as.formula(paste0("`",outcome_v,"` ~ ",rhs))
.step <- "fit"
__LONGITUDINAL_FIT__
se <- sqrt(diag(as.matrix(variance)))
finite_fit <- all(is.finite(cf)) && all(is.finite(se)) && all(se>0)
if (!finite_fit) stop("系数或标准误不可用，不能生成有效推断")
p <- if (method_v=="mixed" && !binary) rep(NA_real_,length(cf)) else 2*stats::pnorm(-abs(cf/se))
time_table <- data.frame(变量=names(cf),效应=unname(cf),标准误=unname(se),
  CI下限=unname(cf-1.959964*se),CI上限=unname(cf+1.959964*se),
  P值=vapply(p,.sk_fmt_p,character(1)),stringsAsFactors=FALSE)
.step <- "marginal-means"
emm <- emmeans::emmeans(fit,specs=time_v,type="response")
marg <- as.data.frame(summary(emm,infer=c(TRUE,FALSE)))
mean_col <- intersect(c("emmean","prob","response"),names(marg))[1]
low_col <- intersect(c("lower.CL","asymp.LCL"),names(marg))[1]
high_col <- intersect(c("upper.CL","asymp.UCL"),names(marg))[1]
if (anyNA(c(mean_col,low_col,high_col))) stop("无法提取模型边际均值与区间")
trend <- data.frame(time=marg[[time_v]],estimate=marg[[mean_col]],lower=marg[[low_col]],upper=marg[[high_col]])
trend_fig <- ggplot2::ggplot(trend,ggplot2::aes(x=time,y=estimate,group=1)) +
  ggplot2::geom_line() + ggplot2::geom_pointrange(ggplot2::aes(ymin=lower,ymax=upper)) +
  ggplot2::labs(title="模型边际均值与 95%CI",x=time_v,y=outcome_v) + ggplot2::theme_bw()
profile_fig <- ggplot2::ggplot(data.frame(fitted=as.numeric(stats::fitted(fit)),residual=as.numeric(stats::residuals(fit))),
  ggplot2::aes(x=fitted,y=residual)) + ggplot2::geom_point(alpha=.5) +
  ggplot2::geom_hline(yintercept=0,linetype=2) + ggplot2::labs(title="残差与拟合值") + ggplot2::theme_bw()
diag_df <- data.frame(检验=c("实际方法","模型类别","受试者数","收敛","相关结构适用性"),
  结果=c(method_v,class(fit)[1],n_sub,if (convergence_ok) "通过" else "未通过",
    "已拟合指定结构；结构适用性需结合研究设计与残差复核"),stringsAsFactors=FALSE)
.diag_status[["相关结构检查"]] <- "warning"
if (method_v=="mixed") .diag_status[["随机效应方差检查"]] <- if (singular) "warning" else "passed"
.diag_status[["收敛检查"]] <- if (convergence_ok) "passed" else "failed"
if (binary) rand_txt <- paste0(rand_txt," 事件编码：",outcome_levels[1],"=0，",outcome_levels[2],"=1；固定效应为 log-odds。")
.out[["time_effect"]] <- list(kind="table",payload=time_table,title="纵向模型固定效应")
.out[["random_eff"]] <- list(kind="table",payload=rand_table,title=if (method_v=="mixed") "随机效应方差" else "GEE 工作相关结构")
.out[["marginal_means"]] <- list(kind="table",payload=marg,title="模型边际均值")
.out[["profile_fig"]] <- list(kind="figure",payload=profile_fig,title="模型诊断图")
.out[["random_text"]] <- list(kind="text",payload=rand_txt,title="模型与推断说明")
.out[["trend_fig"]] <- list(kind="figure",payload=trend_fig,title="模型边际均值趋势")
.out[["model_diag"]] <- list(kind="table",payload=diag_df,title="实际模型与诊断")
'''


def longitudinal_core(method: str) -> str:
    """Emit only the selected fit so exact dependency checks stay branch-specific."""
    if method not in {"mixed", "gee"}:
        raise ValueError("Unsupported longitudinal method")
    return LONGITUDINAL_CORE.replace("__LONGITUDINAL_FIT__", _GEE_FIT if method == "gee" else _MIXED_FIT)


MACHINE_LEARNING_CORE = r'''
.step <- "split"
feats <- unique(as.character(feat_v))
if (!length(feats) || outcome_v %in% feats) stop("特征不得包含结局变量")
df <- dataset[,unique(c(outcome_v,feats)),drop=FALSE]
keep <- stats::complete.cases(df)
df <- df[keep,,drop=FALSE]
binary <- length(unique(df[[outcome_v]]))==2L
if (!binary && !is.numeric(df[[outcome_v]])) stop("此固定脚本支持连续或二分类结局")
if (binary) df[[outcome_v]] <- factor(df[[outcome_v]])
n <- nrow(df)
if (n<40) stop("有效样本至少需要 40 行")
strata <- if (exists("strata_v") && length(strata_v)) factor(dataset[[strata_v]][keep]) else factor(rep("all",n))
if (length(strata)!=n || anyNA(strata)) stop("分层变量必须存在且没有缺失值")
split_key <- if (binary) interaction(strata,df[[outcome_v]],drop=TRUE) else strata
set.seed(1000+sum(as.integer(charToRaw(substr(.plan_id,1,12)))) %% 90000)
if (exists("split_v") && length(split_v) && split_v %in% names(dataset)) {
  split <- dataset[[split_v]][keep]
  if (anyNA(split) || !all(split %in% c(TRUE,FALSE,0,1))) stop("测试集标记必须为无缺失的逻辑值或 0/1；1 表示测试集")
  is_test <- as.logical(split)
} else {
  is_test <- rep(FALSE,n)
  split_groups <- split(seq_len(n),split_key)
  if (any(lengths(split_groups)<2L)) stop("每个切分层至少需要两个观测")
  for (ids in split_groups) is_test[ids[sample.int(length(ids),max(1L,floor(.3*length(ids))))]] <- TRUE
}
train <- df[!is_test,,drop=FALSE]; test <- df[is_test,,drop=FALSE]
if (nrow(train)<25 || nrow(test)<5) stop("训练集至少需 25 行，测试集至少需 5 行")
if (binary && (min(table(train[[outcome_v]]))<10 || min(table(test[[outcome_v]]))<1))
  stop("训练集每类至少需 10 行，测试集必须包含两类")
for (cn in feats) {
  if (is.character(train[[cn]]) || is.factor(train[[cn]]) || is.logical(train[[cn]])) {
    train[[cn]] <- droplevels(factor(train[[cn]]))
    if (any(!as.character(test[[cn]]) %in% levels(train[[cn]]))) stop(paste("测试集出现训练中未见的分类水平：",cn))
    test[[cn]] <- factor(test[[cn]],levels=levels(train[[cn]]))
  }
}
feature_formula <- stats::reformulate(feats)
feature_terms <- stats::terms(feature_formula,data=train)
x <- stats::model.matrix(feature_terms,train)[,-1,drop=FALSE]
x_test_mm <- stats::model.matrix(feature_terms,test)[,-1,drop=FALSE]
if (!identical(colnames(x),colnames(x_test_mm))) stop("训练和测试的特征编码不一致")
y <- if (binary) as.numeric(train[[outcome_v]])-1 else train[[outcome_v]]
if (any(!is.finite(x)) || any(!is.finite(y))) stop("训练数据含非有限数值")
cv_folds <- integer(nrow(train))
cv_key <- droplevels(split_key[!is_test])
fold_groups <- split(seq_len(nrow(train)),cv_key)
if (any(lengths(fold_groups)<10L)) stop("每个训练分层（分类任务含结局类别）至少需要 10 行，以支持嵌套交叉验证")
for (ids in fold_groups) cv_folds[ids] <- sample(rep(seq_len(5),length.out=length(ids)))
fold_pred <- rep(NA_real_,length(y)); rf_fold_pred <- fold_pred
forest_formula <- stats::reformulate(feats,response=outcome_v)
.step <- "training-cv"
for (kk in seq_len(5)) {
  vi <- cv_folds!=kk
  inner_folds <- integer(sum(vi))
  inner_groups <- split(seq_len(sum(vi)),droplevels(cv_key[vi]))
  for (ids in inner_groups) inner_folds[ids] <- sample(rep(seq_len(5),length.out=length(ids)))
  gfit <- glmnet::cv.glmnet(x[vi,,drop=FALSE],y[vi],family=if (binary) "binomial" else "gaussian",
    alpha=.5,foldid=inner_folds)
  fold_pred[!vi] <- as.numeric(stats::predict(gfit,newx=x[!vi,,drop=FALSE],s="lambda.min",type="response"))
  rf_cv <- ranger::ranger(forest_formula,data=train[vi,,drop=FALSE],num.trees=400,
    probability=binary,seed=42+kk,num.threads=1)
  rf_raw <- stats::predict(rf_cv,data=train[!vi,,drop=FALSE])$predictions
  rf_fold_pred[!vi] <- if (binary) rf_raw[,2] else rf_raw
}
score <- if (binary) function(actual,pred) as.numeric(pROC::auc(pROC::roc(actual,pred,levels=c(0,1),direction="<",quiet=TRUE)))
  else function(actual,pred) sqrt(mean((actual-pred)^2))
cv_glm <- score(y,fold_pred); cv_rf <- score(y,rf_fold_pred)
best_is_rf <- if (binary) cv_rf>cv_glm else cv_rf<cv_glm
model_cmp <- data.frame(模型=c("glmnet","ranger"),评估集="训练集外层5折交叉验证",
  指标=if (binary) "AUC" else "RMSE",数值=c(cv_glm,cv_rf),stringsAsFactors=FALSE)
cv_metrics <- model_cmp
best_row <- model_cmp[if (best_is_rf) 2 else 1,,drop=FALSE]
.step <- "locked-model-test"
if (best_is_rf) {
  fit <- ranger::ranger(forest_formula,data=train,num.trees=400,probability=binary,seed=42,num.threads=1)
  raw_prediction <- stats::predict(fit,data=test)$predictions
  test_prediction <- if (binary) raw_prediction[,2] else as.numeric(raw_prediction)
} else {
  fit <- glmnet::cv.glmnet(x,y,family=if (binary) "binomial" else "gaussian",alpha=.5,foldid=cv_folds)
  test_prediction <- as.numeric(stats::predict(fit,newx=x_test_mm,s="lambda.min",type="response"))
}
yte <- if (binary) as.numeric(test[[outcome_v]])-1 else test[[outcome_v]]
if (binary) {
  pred <- test_prediction>=.5
  test_perf <- data.frame(模型=best_row$模型,AUC=score(yte,test_prediction),准确率=mean(pred==yte),
    灵敏度=sum(pred & yte==1)/sum(yte==1),特异度=sum(!pred & yte==0)/sum(yte==0))
  roc_obj <- pROC::roc(yte,test_prediction,levels=c(0,1),direction="<",quiet=TRUE)
  roc_df <- data.frame(fpr=1-roc_obj$specificities,tpr=roc_obj$sensitivities)
  roc_fig <- ggplot2::ggplot(roc_df,ggplot2::aes(x=fpr,y=tpr)) + ggplot2::geom_step() +
    ggplot2::coord_equal() + ggplot2::labs(title="训练集选定模型的测试集 ROC",x="1-特异度",y="灵敏度") + ggplot2::theme_bw()
} else {
  test_perf <- data.frame(模型=best_row$模型,RMSE=score(yte,test_prediction),MAE=mean(abs(yte-test_prediction)))
  roc_fig <- ggplot2::ggplot(data.frame(obs=yte,pred=test_prediction),ggplot2::aes(x=obs,y=pred)) +
    ggplot2::geom_point() + ggplot2::geom_abline(slope=1,intercept=0) + ggplot2::labs(title="锁定模型的测试集预测") + ggplot2::theme_bw()
}
overfit_table <- data.frame(指标=c("模型选择依据","最终评估","训练/测试样本数"),
  结果=c("训练集交叉验证","测试集仅评估已选定模型",paste(nrow(train),nrow(test),sep=" / ")))
leak_txt <- "已排除结局列直接进入特征，特征编码仅从训练集确定，模型选择仅使用训练集交叉验证。特征采集时间、结局派生信息及同一受试者跨集合等风险仍需研究者复核。"
reuse_txt <- "模型由训练集交叉验证选定；本次测试集不得继续用于选模型或调参。复用前需核对特征编码与外部数据适用性。"
.diag_status[["数据切分检查"]] <- "passed"
.diag_status[["重采样稳定性"]] <- "warning"
.diag_status[["类别不平衡检查"]] <- if (binary && min(table(y))/length(y)<.2) "warning" else "passed"
.diag_status[["交叉验证设置检查"]] <- "passed"
.diag_status[["特征泄漏检查"]] <- "warning"
.diag_status[["任务类型检查"]] <- "passed"
.diag_status[["重采样一致性"]] <- "passed"
.diag_status[["调参预算说明"]] <- "passed"
.out[["model_cmp"]] <- list(kind="table",payload=model_cmp,title="训练集交叉验证模型比较")
.out[["roc_fig"]] <- list(kind="figure",payload=roc_fig,title="锁定模型的测试性能")
.out[["test_perf"]] <- list(kind="table",payload=test_perf,title="测试集性能")
.out[["pred_table"]] <- list(kind="table",payload=data.frame(真实=yte,预测=test_prediction),title="测试集预测文件")
.out[["cv_table"]] <- list(kind="table",payload=cv_metrics,title="交叉验证性能")
.out[["best_table"]] <- list(kind="table",payload=best_row,title="训练集选定模型")
.out[["overfit_table"]] <- list(kind="table",payload=overfit_table,title="选择与评估范围")
.out[["leak_text"]] <- list(kind="text",payload=leak_txt,title="泄漏风险复核")
.out[["reuse_text"]] <- list(kind="text",payload=reuse_txt,title="复用流程")
'''
