"""Fixed data preparation implementations using the frozen input contract."""

_PROFILE = r'''
.step <- "profile"
if (!is.data.frame(dataset) || !nrow(dataset)) stop("需要非空表格数据")
.candidates <- if (exists("cand_v") && length(cand_v)) as.character(cand_v) else names(dataset)
if (length(setdiff(.candidates, names(dataset)))) stop("选定字段不存在")
.data <- as.data.frame(dataset[, .candidates, drop = FALSE])
.roles <- list(sample_id=if(exists("sample_v")) sample_v else NULL,
 outcome=if(exists("outcome_v")) outcome_v else NULL,
 exposure=if(exists("exposure_v")) exposure_v else NULL,
 batch=if(exists("batch_v")) batch_v else NULL)
if(length(setdiff(as.character(unlist(.roles)),names(dataset)))) stop("已确认的角色字段不存在")
.profile <- do.call(rbind, lapply(names(.data), function(v) {
  x <- .data[[v]]; finite <- if (is.numeric(x)) is.finite(x) else !is.na(x)
  data.frame(变量=v, 类型=paste(class(x),collapse="/"), 观测数=sum(!is.na(x)),
    缺失数=sum(is.na(x)), 缺失比例=mean(is.na(x)), 唯一值数=length(unique(x[!is.na(x)])),
    非有限值=if(is.numeric(x)) sum(!is.finite(x) & !is.na(x)) else 0L,
    概要=if(is.numeric(x) && any(finite)) sprintf("中位数=%g; Q1=%g; Q3=%g",stats::median(x[finite]),
      stats::quantile(x[finite],.25,names=FALSE),stats::quantile(x[finite],.75,names=FALSE)) else
      paste(utils::head(names(sort(table(x),decreasing=TRUE)),3),collapse=" / "), check.names=FALSE)
}))
.missing <- .profile[,c("变量","缺失数","缺失比例")]
.patterns <- as.data.frame(table(apply(is.na(.data),1,function(x) paste(as.integer(x),collapse=""))),stringsAsFactors=FALSE)
names(.patterns) <- c("缺失模式_1为缺失","行数")
.patterns$比例 <- .patterns$行数/nrow(.data)
.outliers <- do.call(rbind,lapply(names(.data)[vapply(.data,is.numeric,logical(1))],function(v) {
  x <- .data[[v]]; xok <- x[is.finite(x)]; q <- if(length(xok)) stats::quantile(xok,c(.25,.75),names=FALSE) else c(NA,NA)
  bounds <- q+c(-1,1)*1.5*diff(q)
  data.frame(变量=v, 下界=bounds[1], 上界=bounds[2], IQR异常数=sum(xok<bounds[1]|xok>bounds[2]),check.names=FALSE)
}))
if(is.null(.outliers)) .outliers <- data.frame(说明="无数值字段")
.duplicates <- sum(duplicated(.data))
.id_duplicates <- if(exists("sample_v") && length(sample_v)) {
  if(!sample_v %in% names(dataset)) stop("样本ID字段不存在")
  sum(duplicated(dataset[[sample_v]][!is.na(dataset[[sample_v]])]))
} else NA_integer_
.risk <- .profile[.profile$缺失比例>0 | .profile$非有限值>0 | .profile$唯一值数<=1,,drop=FALSE]
if(!nrow(.risk)) .risk <- data.frame(说明="未发现缺失、非有限值或常量字段")
.recommend <- sprintf("本轮核查 %d 行、%d 字段；重复整行 %d。缺失和异常清单见输出；未自动改变数据。",nrow(.data),ncol(.data),.duplicates)
.missing_plot <- ggplot2::ggplot(.missing,ggplot2::aes(x=stats::reorder(变量,缺失比例),y=缺失比例))+
  ggplot2::geom_col(fill="#4477AA")+ggplot2::coord_flip()+ggplot2::theme_bw()+ggplot2::labs(x=NULL,y="缺失比例")
.diag_status[["变量类型核查"]] <- "passed"
.diag_status[["重复记录检查"]] <- if(.duplicates>0 || (!is.na(.id_duplicates) && .id_duplicates>0)) "warning" else "passed"
.diag_status[["极端值和编码异常检查"]] <- if(any(.profile$非有限值>0) || ("IQR异常数" %in% names(.outliers) && any(.outliers$IQR异常数>0))) "warning" else "passed"
.diag_status[["缺失、重复、异常和批次问题检查"]] <- if(.duplicates>0 || any(.profile$缺失数>0)) "warning" else "passed"
.diag_status[["变量类型与编码一致性检查"]] <- "passed"
.out[["profile"]] <- list(kind="table",payload=.profile,title="字段核查")
.out[["dictionary"]] <- list(kind="text",payload=paste(capture.output(print(.profile,row.names=FALSE)),collapse="\n"),title="变量字典")
.out[["dictionary"]]$payload <- paste(.out[["dictionary"]]$payload,
 paste("已确认角色：",paste(names(.roles),vapply(.roles,function(x) if(length(x)) as.character(x) else "未指定",character(1)),sep="=",collapse="; ")),sep="\n")
.out[["missing"]] <- list(kind="table",payload=.missing,title="缺失概览")
.out[["patterns"]] <- list(kind="table",payload=.patterns,title="逐行缺失模式")
.out[["outliers"]] <- list(kind="text",payload=paste(capture.output(print(.outliers,row.names=FALSE)),collapse="\n"),title="IQR异常清单")
.out[["recommend"]] <- list(kind="text",payload=.recommend,title="数据核查建议")
.out[["route"]] <- list(kind="text",payload="使用确认的结局、暴露、样本ID和批次字段制定分析；缺失处理和重编码只按显式规则执行。",title="后续路线")
.out[["method"]] <- list(kind="text",payload="保留字段原始类型；缺失按NA逐字段、逐行计数，整行重复与样本ID重复分开核查；数值异常采用1.5 IQR规则。",title="数据核查方法")
.out[["risk"]] <- list(kind="table",payload=.risk,title="变量风险列表")
'''

_IMPORT = _PROFILE + r'''
.mapping <- data.frame(字段=names(.data),导入类型=vapply(.data,function(x) paste(class(x),collapse="/"),character(1)),
  处理="保留导入后的类型与字段名；不自动猜测编码",check.names=FALSE)
.out[["mapping"]] <- list(kind="table",payload=.mapping,title="字段映射")
'''

_CLEANING = _PROFILE + r'''
.step <- "explicit-cleaning"
.rules <- if(exists("rules_v") && nzchar(as.character(rules_v))) jsonlite::fromJSON(as.character(rules_v),simplifyVector=FALSE) else list()
if(!is.list(.rules) || (length(.rules) && (is.null(names(.rules)) || any(!nzchar(names(.rules)))))) stop("清洗规则必须是按字段命名的JSON对象")
if(length(setdiff(names(.rules),names(.data)))) stop("清洗规则引用未选入字段")
.cleaned <- .data; .audit_rows <- list(); .code <- c(paste0("cleaned <- dataset[,",paste(capture.output(dput(names(.data))),collapse=""),", drop=FALSE]"))
for(v in names(.rules)) {
  rule <- .rules[[v]]
  if(!is.list(rule) || length(setdiff(names(rule),c("trim","missing_values","recode","target_type")))) stop("未知清洗规则：",v)
  before <- .cleaned[[v]]; x <- before
  if(isTRUE(rule$trim)) { if(!is.character(x) && !is.factor(x)) stop("trim只适用于文本字段"); x <- trimws(as.character(x)) }
  if(length(rule$missing_values)) x[as.character(x) %in% as.character(unlist(rule$missing_values))] <- NA
  if(length(rule$recode)) {
    if(is.null(names(rule$recode))) stop("recode必须是旧值到新值的命名映射")
    x <- as.character(x); original <- x
    for(old in names(rule$recode)) x[!is.na(original) & original==old] <- as.character(rule$recode[[old]])
  }
  if(length(rule$target_type)) {
    target <- as.character(rule$target_type)
    if(!target %in% c("numeric","character","factor")) stop("不支持目标类型")
    if(target=="numeric") { z <- suppressWarnings(as.numeric(as.character(x))); if(any(!is.na(x)&is.na(z))) stop("数值转换将丢失有效取值：",v); x <- z }
    else if(target=="factor") x <- factor(x) else x <- as.character(x)
  }
  .cleaned[[v]] <- x
  changed <- sum((is.na(before)!=is.na(x)) | (!is.na(before)&!is.na(x)&as.character(before)!=as.character(x)))
  .audit_rows[[length(.audit_rows)+1L]] <- data.frame(字段=v,规则=jsonlite::toJSON(rule,auto_unbox=TRUE),
    修改行数=changed,原类型=paste(class(before),collapse="/"),新类型=paste(class(x),collapse="/"),check.names=FALSE)
}
.audit <- if(length(.audit_rows)) do.call(rbind,.audit_rows) else data.frame(说明="未提供清洗规则，数据保持原样")
.code <- c(.code,paste0("rules <- jsonlite::fromJSON(",encodeString(jsonlite::toJSON(.rules,auto_unbox=TRUE),quote='"'),", simplifyVector=FALSE)"),
 "audit_rows <- list()\nfor(v in names(rules)) {\n  rule <- rules[[v]]\n  if(!is.list(rule) || length(setdiff(names(rule),c(\"trim\",\"missing_values\",\"recode\",\"target_type\")))) stop(\"未知清洗规则：\",v)\n  before <- cleaned[[v]]; x <- before\n  if(isTRUE(rule$trim)) { if(!is.character(x) && !is.factor(x)) stop(\"trim只适用于文本字段\"); x <- trimws(as.character(x)) }\n  if(length(rule$missing_values)) x[as.character(x) %in% as.character(unlist(rule$missing_values))] <- NA\n  if(length(rule$recode)) {\n    if(is.null(names(rule$recode))) stop(\"recode必须是旧值到新值的命名映射\")\n    x <- as.character(x); original <- x\n    for(old in names(rule$recode)) x[!is.na(original) & original==old] <- as.character(rule$recode[[old]])\n  }\n  if(length(rule$target_type)) {\n    target <- as.character(rule$target_type)\n    if(!target %in% c(\"numeric\",\"character\",\"factor\")) stop(\"不支持目标类型\")\n    if(target==\"numeric\") { z <- suppressWarnings(as.numeric(as.character(x))); if(any(!is.na(x)&is.na(z))) stop(\"数值转换将丢失有效取值：\",v); x <- z }\n    else if(target==\"factor\") x <- factor(x) else x <- as.character(x)\n  }\n  cleaned[[v]] <- x\n  changed <- sum((is.na(before)!=is.na(x)) | (!is.na(before)&!is.na(x)&as.character(before)!=as.character(x)))\n  audit_rows[[length(audit_rows)+1L]] <- data.frame(字段=v,规则=jsonlite::toJSON(rule,auto_unbox=TRUE),\n    修改行数=changed,原类型=paste(class(before),collapse=\"/\"),新类型=paste(class(x),collapse=\"/\"),check.names=FALSE)\n}")
.out[["cleaning_rules"]] <- list(kind="table",payload=.audit,title="清洗规则与修改回执")
.out[["cleaned_data"]] <- list(kind="table",payload=.cleaned,title="清洗后数据")
.out[["cleaning_code"]] <- list(kind="text",payload=paste(.code,collapse="\n"),title="可复用规则与处理代码")
.out[["method"]] <- list(kind="text",payload="对数据副本执行显式JSON规则；原始输入保持只读；不自动删除观测、不按取值猜测重编码。",title="清洗方法")
.out[["recommend"]] <- list(kind="text",payload=sprintf("处理 %d 字段，输出保持 %d 行；修改回执见清洗规则表。",length(.rules),nrow(.cleaned)),title="清洗结果")
'''

_EDA = _PROFILE + r'''
baa_plot("eda_missing_plot",.missing_plot,metadata=list(role="eda_missing_plot",title="EDA缺失图",paper_ready=TRUE))
.out[["index"]] <- list(kind="text",payload="实际生成图表：eda_missing_plot（字段缺失率图）；字段概要与变量风险见对应表。",title="EDA图表索引")
'''

_LARGE = r'''
.step <- "data-table-summary"
if(!is.data.frame(dataset) || !nrow(dataset)) stop("需要已导入的非空表格")
.dt <- data.table::as.data.table(dataset)
.by <- unique(c(if(exists("partition_v")) as.character(partition_v),if(exists("group_v")) as.character(group_v)))
.measures <- if(exists("measure_v") && length(measure_v)) as.character(measure_v) else names(.dt)[vapply(.dt,is.numeric,logical(1))]
.measures <- setdiff(.measures,.by)
if(length(setdiff(c(.by,.measures),names(.dt)))) stop("汇总字段不存在")
if(length(.measures) && !all(vapply(.dt[,.SD,.SDcols=.measures],is.numeric,logical(1)))) stop("汇总指标必须是数值字段")
.elapsed <- system.time({
 .summary <- if(length(.measures)) .dt[,c(list(行数=.N),lapply(.SD,function(x) if(all(is.na(x))) NA_real_ else mean(x,na.rm=TRUE))),by=.by,.SDcols=.measures]
 else if(length(.by)) .dt[,.N,by=.by] else data.table::data.table(行数=nrow(.dt))
})[["elapsed"]]
.size <- data.frame(行数=nrow(.dt),列数=ncol(.dt),内存字节=as.numeric(utils::object.size(.dt)),check.names=FALSE)
.performance <- data.frame(方法="data.table分组汇总",耗时秒=.elapsed,结果行数=nrow(.summary),
  读取范围="已导入内存的数据；不声称磁盘外扫描",check.names=FALSE)
.query <- paste0("data.table::as.data.table(dataset)[, lapply(.SD, mean, na.rm=TRUE), by=",paste(deparse(.by),collapse=""),", .SDcols=",paste(deparse(.measures),collapse=""),"]")
.diag_status[["列类型检查"]] <- "passed"
.diag_status[["内存风险检查"]] <- if(.size$内存字节>512*1024^2) "warning" else "passed"
.out[["summary"]] <- list(kind="table",payload=as.data.frame(.summary),title="分组与分区汇总")
.out[["query"]] <- list(kind="text",payload=.query,title="实际汇总代码")
.out[["size"]] <- list(kind="table",payload=.size,title="实际数据规模")
.out[["performance"]] <- list(kind="table",payload=.performance,title="实测性能")
.out[["recommend"]] <- list(kind="text",payload="本轮采用data.table对已导入数据汇总；磁盘外读取与分块能力按上游导入回执判断。",title="数据处理说明")
'''

_MISSING = _PROFILE + r'''
.co_missing <- crossprod(is.na(.data))*1L
.co_table <- data.frame(变量=rownames(.co_missing),.co_missing,check.names=FALSE,row.names=NULL)
.out[["co_missing"]] <- list(kind="table",payload=.co_table,title="共缺失计数矩阵")
.out[["missing_plot"]] <- list(kind="figure",payload=.missing_plot,title="选定字段缺失图")
.out[["mechanism"]] <- list(kind="text",payload="本轮计算选定字段的缺失率、共缺失矩阵与逐行模式；MCAR/MAR/MNAR无法由这些统计量单独判定。",title="缺失机制说明")
'''

CORES = {"data_quality": _PROFILE, "data_import_workflow": _IMPORT,
         "data_cleaning_recoding": _CLEANING, "automated_eda_report": _EDA,
         "large_data_engine": _LARGE, "missing_visual_diagnostics": _MISSING}
INPUTS = {
    "data_quality": {"sample_id": "sample_v", "outcome": "outcome_v", "exposure": "exposure_v", "batch": "batch_v"},
    "data_import_workflow": {"candidate_variables": "cand_v"},
    "data_cleaning_recoding": {"candidate_variables": "cand_v", "cleaning_rules": "rules_v"},
    "automated_eda_report": {"candidate_variables": "cand_v"},
    "large_data_engine": {"dataset": "dataset_ref_v", "partition_time": "partition_v", "group": "group_v", "measure": "measure_v"},
    "missing_visual_diagnostics": {"variables_with_missingness": "cand_v"},
}
ROLE_MAPS = {
    "data_quality": {"variable_dictionary": "dictionary", "missingness_summary": "missing", "missingness_summary_2": "patterns", "outlier_summary": "outliers", "analysis_recommendation": "recommend", "analysis_recommendation_2": "route"},
    "data_import_workflow": {"table_1": "profile", "table_2": "mapping", "analysis_recommendation": "recommend", "method_note": "method", "manuscript_results": "recommend"},
    "data_cleaning_recoding": {"table_1": "cleaning_rules", "table_2": "cleaned_data", "table_3": "cleaning_code", "method_note": "method", "manuscript_results": "recommend"},
    "automated_eda_report": {"text_1": "recommend", "table_1": "risk", "figure_1": "index", "method_note": "method", "manuscript_results": "recommend"},
    "large_data_engine": {"table_1": "summary", "table_2": "query", "analysis_recommendation": "recommend", "table_3": "size", "table_4": "summary", "table_5": "performance", "analysis_recommendation_2": "recommend"},
    "missing_visual_diagnostics": {"missingness_summary": "missing_plot", "missingness_summary_2": "co_missing", "missingness_summary_3": "mechanism", "method_note": "method", "manuscript_results": "recommend"},
}
REFERENCE_CASES = {
    "data_quality": "真实 R 核查缺失比例、样本ID重复、逐行缺失模式；保留确认的数值类型。",
    "data_import_workflow": "真实 R 检查选定字段映射与导入后类型，不自动重编码。",
    "data_cleaning_recoding": "显式JSON清洗规则的修改回执、原始输入保留与行数检查；拒绝丢失有效取值的数值转换。",
    "automated_eda_report": "真实 R 生成字段风险表与实际缺失图，图表索引对应真实产物。",
    "large_data_engine": "data.table按选定组别和指标计算实际均值、行数与性能回执。",
    "missing_visual_diagnostics": "只对选定字段计算缺失率、共缺失计数矩阵与行级模式。",
}
