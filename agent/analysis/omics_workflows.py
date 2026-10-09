"""Native omics workflows with explicit matrix, annotation, and contrast contracts."""

from __future__ import annotations

import re

from .weighted_causal_cores import _COMMON


_HELPERS = r'''
.om_annotation <- function(value) {
  if(is.character(value)&&length(value)==1&&grepl("^[[:space:]]*[\\[{]",value))return(as.data.frame(jsonlite::fromJSON(value),stringsAsFactors=FALSE))
  if(is.character(value)&&length(value)==2&&all(value%in%names(dataset)))return(dataset[,value,drop=FALSE])
  if(is.character(value)){rows<-strsplit(value,"\t",fixed=TRUE);if(!all(lengths(rows)==2))stop("注释需 JSON 表格或 TAB 分隔的两列平坦向量");return(as.data.frame(do.call(rbind,rows),stringsAsFactors=FALSE))}
  as.data.frame(value,stringsAsFactors=FALSE)
}
.om_matrix <- function(object, columns=NULL) {
  cell_observations <- isTRUE(.wc_get("cell_observations_v",FALSE))
  observation_id <- if(cell_observations).wc_get("observation_v")else .wc_get("sample_v")
  if(inherits(object,"ExpressionSet")||inherits(object,"MSnSet")){
    x<-as.matrix(Biobase::exprs(object));meta<-Biobase::pData(object);annotation<-Biobase::fData(object)
  }else if(inherits(object,"phyloseq")){
    if(!requireNamespace("phyloseq",quietly=TRUE))stop("原生phyloseq对象需要其成熟包")
    x<-methods::as(phyloseq::otu_table(object),"matrix");if(!phyloseq::taxa_are_rows(object))x<-t(x)
    meta<-methods::as(phyloseq::sample_data(object),"data.frame");annotation<-as.data.frame(methods::as(phyloseq::tax_table(object),"matrix"))
  }else if (inherits(object,"SummarizedExperiment")) {
  assay <- .wc_get("assay_v",1);x <- as.matrix(SummarizedExperiment::assay(object,assay))
    meta <- as.data.frame(SummarizedExperiment::colData(object)); annotation <- as.data.frame(SummarizedExperiment::rowData(object))
  } else if (inherits(object,"Seurat")) {
    x <- as.matrix(Seurat::GetAssayData(object,assay=Seurat::DefaultAssay(object),layer=.wc_get("assay_v","counts")))
    meta <- object[[]]; annotation <- data.frame(row.names=rownames(x))
  } else if (is.data.frame(object)) {
    columns <- .wc_cols(columns)
    if (!length(columns) || !all(columns %in% names(object))) stop("表格表达矩阵需要明确 feature_columns")
    if (!all(vapply(object[,columns,drop=FALSE],is.numeric,logical(1)))) stop("特征矩阵必须全部为数值列")
    x <- t(as.matrix(object[,columns,drop=FALSE])); meta <- object[,setdiff(names(object),columns),drop=FALSE]
    if (!is.null(observation_id)) {
      if (!observation_id %in% names(meta) || anyNA(meta[[observation_id]]) || anyDuplicated(meta[[observation_id]])) stop("矩阵观测ID缺失或不唯一；细胞/spot ID与供体sample_id分开")
      colnames(x) <- as.character(meta[[observation_id]]); rownames(meta) <- colnames(x)
    } else {colnames(x)<-paste0(if(cell_observations)"observation"else"sample",seq_len(ncol(x)));rownames(meta)<-colnames(x)}
    annotation <- data.frame(row.names=rownames(x))
  } else if (is.matrix(object) || inherits(object,"Matrix")) {
    orientation <- .wc_get("orientation_v")
    if (is.null(orientation) || !orientation %in% c("features_by_samples","samples_by_features")) stop("独立矩阵需要明确 matrix_orientation")
    x <- as.matrix(object); if (orientation=="samples_by_features") x <- t(x)
    raw_meta <- .wc_get("metadata_v");if (is.null(raw_meta)) stop("独立矩阵需要 sample_metadata")
    meta <- .om_annotation(raw_meta)
    if(nrow(meta)!=ncol(x)) stop("矩阵与样本注释数量不一致")
    sid <- observation_id
    if(cell_observations&&is.null(sid))stop("独立细胞/spot矩阵需要observation_id与样本注释严格对齐；sample_id为可重复生物学供体")
    if (!is.null(sid)) {
      if (!sid %in% names(meta) || is.null(colnames(x)) || anyNA(meta[[sid]]) || anyDuplicated(meta[[sid]])) stop("矩阵观测 ID 无法对齐")
      ix <- match(colnames(x),as.character(meta[[sid]]));if(anyNA(ix))stop("矩阵样本未在注释出现")
      meta<-meta[ix,,drop=FALSE]
    }
    annotation <- data.frame(row.names=rownames(x))
  } else stop("需要原生 SummarizedExperiment/Seurat 或已确认表达矩阵")
  if (!is.numeric(x) || nrow(x)<2 || ncol(x)<3 || any(is.infinite(x))) stop("表达矩阵维度或数值无效")
  if (is.null(rownames(x)) || anyDuplicated(rownames(x))) stop("特征ID必须存在且唯一")
  if (is.null(colnames(x))) colnames(x) <- paste0("sample",seq_len(ncol(x)))
  if (anyDuplicated(colnames(x))) stop("样本ID不能重复")
  rownames(meta)<-colnames(x)
  list(x=x,meta=meta,annotation=annotation)
}
.om_read <- function() {
  object <- .wc_get("object_v",dataset);columns <- .wc_get("features_v")
  if (is.character(object) && all(object %in% names(dataset))) {columns<-object;object<-dataset}
  om<-.om_matrix(object,columns)
  sample<-.wc_get("sample_v");if(!is.null(sample)&&(!sample%in%names(om$meta)||anyNA(om$meta[[sample]])))stop("指定 sample_id 未在样本/细胞注释中或含缺失")
  if(!is.null(sample)&&!isTRUE(.wc_get("cell_observations_v",FALSE))&&anyDuplicated(om$meta[[sample]]))stop("矩阵样本ID必须唯一；配对供体请另指定pair_id，不把重复测量当独立样本")
  observation<-.wc_get("observation_v");if(!is.null(observation)&&(!observation%in%names(om$meta)||anyNA(om$meta[[observation]])||anyDuplicated(om$meta[[observation]])||!identical(as.character(om$meta[[observation]]),colnames(om$x))))stop("observation_id必须唯一且与矩阵细胞/spot轴逐项一致")
  cell_annotations<-.wc_cols(.wc_get("cell_annotations_v"));if(length(cell_annotations)&&!all(cell_annotations%in%names(om$meta)))stop("明确选择的细胞注释不存在")
  external_annotation<-.wc_get("annotation_table_v");if(!is.null(external_annotation)){
    external_annotation<-.om_annotation(external_annotation);if(!"feature"%in%names(external_annotation)||anyDuplicated(external_annotation$feature))stop("feature_annotation_table需要唯一 feature ID")
    ix<-match(rownames(om$x),as.character(external_annotation$feature));if(anyNA(ix))stop("特征注释表未覆盖矩阵全部特征")
    om$annotation<-external_annotation[ix,setdiff(names(external_annotation),"feature"),drop=FALSE];rownames(om$annotation)<-rownames(om$x)
  }
  requested<-.wc_cols(.wc_get("sample_annotations_v"));if(length(requested)&&!all(requested%in%names(om$meta)))stop("明确选择的样本注释列不存在")
  annotation_cols<-.wc_cols(.wc_get("feature_annotations_v"));if(length(annotation_cols)){
    if(!all(annotation_cols%in%names(om$annotation)))stop("特征注释必须存在于原生 rowData/fData/tax_table，不能当作样本列")
    om$annotation<-om$annotation[,annotation_cols,drop=FALSE]
  }
  om
}
.om_frame <- function(columns,annotations=NULL) {
  columns<-.wc_cols(columns);annotations<-setdiff(.wc_cols(annotations),columns)
  if(!length(columns)||!all(c(columns,annotations)%in%names(dataset)))stop("确认模型/注释列不存在")
  if(any(grepl("`",columns,fixed=TRUE)))stop("当前公式接口不支持含反引号的列名")
  d<-as.data.frame(dataset[,c(columns,annotations),drop=FALSE])
  d<-d[stats::complete.cases(d[,columns,drop=FALSE]),,drop=FALSE]
  if(nrow(d)<2)stop("完整模型观测不足")
  for(nm in columns)if(is.numeric(d[[nm]])&&any(!is.finite(d[[nm]])))stop("模型列含非有限值：",nm)
  d
}
.om_preserve_annotations <- function(tab,df,annotations,gene=NULL) {
  for(nm in .wc_cols(annotations)){
    groups<-if(is.null(gene))rep(list(df[[nm]]),nrow(tab))else lapply(as.character(tab$gene),function(g)df[[nm]][df[[gene]]==g])
    tab[[paste0("annotation_",nm)]]<-vapply(groups,function(v)paste(unique(as.character(v[!is.na(v)])),collapse=";"),character(1))
    tab[[paste0("missing_",nm)]]<-vapply(groups,function(v)sum(is.na(v)),integer(1))
  }
  tab
}
.om_counts <- function(x) {
  if (anyNA(x) || any(x<0) || any(x!=floor(x))) stop("计数模型要求非负原始整数计数，禁止取整伪造")
  if (any(colSums(x)==0)) stop("存在零文库样本")
  x
}
.om_design <- function(meta,group=NULL) {
  if(is.null(group)) group <- .wc_get("group_v")
  covs <- .wc_cols(c(.wc_get("batch_v"),.wc_get("pair_v"),.wc_get("covariates_v")))
  if (!group %in% names(meta) || !all(covs %in% names(meta))) stop("组别/批次/协变量未在样本注释中")
  d <- meta[,unique(c(group,covs)),drop=FALSE]
  if(anyNA(d)) stop("样本设计变量不能缺失")
  levels <- unique(as.character(d[[group]]))
  if(length(levels)!=2)stop("当前差异分析需要恰好两组；其他比较请提供独立计划")
  reference <- .wc_get("reference_v");comparison <- .wc_get("comparison_v")
  if (is.null(reference) && setequal(levels,c("0","1"))) reference<-"0"
  if (is.null(reference) || !as.character(reference) %in% levels)stop("必须指定 ref_group")
  if (is.null(comparison)) comparison<-setdiff(levels,as.character(reference))
  if (!as.character(comparison) %in% levels || as.character(comparison)==as.character(reference))stop("比较组无效")
  d$.group <- factor(as.character(d[[group]]),levels=c(as.character(reference),as.character(comparison)))
  d[[group]]<-NULL
  pair <- .wc_get("pair_v");if(!is.null(pair))d[[pair]]<-factor(d[[pair]])
  batch <- .wc_get("batch_v");if(!is.null(batch))d[[batch]]<-factor(d[[batch]])
  form<-stats::as.formula(paste("~",paste(c(if(length(covs)).wc_rhs(covs) else NULL,".group"),collapse="+")))
  design<-stats::model.matrix(form,d)
  if(qr(design)$rank<ncol(design) || nrow(design)<=ncol(design))stop("设计矩阵混淆或没有剩余自由度")
  list(data=d,design=design,coef=ncol(design),reference=reference,comparison=comparison,formula=form)
}
.om_pca <- function(x,meta) {
  keep<-apply(x,1,stats::sd)>0; if(sum(keep)<2)stop("PCA至少需要两个非恒定特征")
  pc<-stats::prcomp(t(x[keep,,drop=FALSE]),center=TRUE,scale.=FALSE)
  pca<-data.frame(样本=colnames(x),PC1=pc$x[,1],PC2=pc$x[,2],row.names=NULL)
  requested<-.wc_cols(.wc_get("sample_annotations_v"));if(length(requested))pca<-cbind(pca,meta[,requested,drop=FALSE])
  group<-.wc_get("group_v");pca$分组<-if(!is.null(group)&&group %in%names(meta))as.character(meta[[group]])else"all"
  list(model=pc,table=pca,figure=ggplot2::ggplot(pca,ggplot2::aes(PC1,PC2,color=分组))+ggplot2::geom_point()+ggplot2::theme_minimal())
}
.om_volcano <- function(tab) ggplot2::ggplot(tab,ggplot2::aes(logFC,-log10(pmax(P.Value,.Machine$double.xmin))))+ggplot2::geom_point(ggplot2::aes(color=adj.P.Val<.05))+ggplot2::theme_minimal()+ggplot2::labs(y="-log10(P)")
.om_heat <- function(x) {
  z<-x[seq_len(min(nrow(x),30)),,drop=FALSE]; z<-t(scale(t(z)));z[!is.finite(z)]<-0
  heat<-data.frame(特征=rep(rownames(z),ncol(z)),样本=rep(colnames(z),each=nrow(z)),标准化值=as.vector(z))
  ggplot2::ggplot(heat,ggplot2::aes(样本,特征,fill=标准化值))+ggplot2::geom_tile()+ggplot2::theme_minimal()+ggplot2::theme(axis.text.x=ggplot2::element_text(angle=90,hjust=1))
}
.om_finish <- function(method,detail) {
  list(results=.wc_text("results",detail),method=.wc_text("method",method),diagnostic=.wc_text("diagnostic",paste(method,detail,sep="\n")))
}
.om_enrich <- function(genes,universe,mapping) {
  terms<-.om_annotation(mapping)
  if(ncol(terms)!=2 || anyNA(terms))stop("term_gene_mapping需要两列已确认通路ID和特征ID")
  terms<-terms[,1:2];names(terms)<-c("term","gene")
  if(!length(universe)||!all(genes %in%universe))stop("富集背景必须包含所有输入基因")
  old_options<-options(enrichment_force_universe=TRUE);on.exit(options(old_options))
  fit<-clusterProfiler::enricher(unique(genes),universe=unique(universe),TERM2GENE=terms,pvalueCutoff=1,qvalueCutoff=1,minGSSize=1,maxGSSize=5000)
  if(is.null(fit))return(list(fit=NULL,table=data.frame(ID=character(),pvalue=numeric(),p.adjust=numeric(),Count=integer())))
  list(fit=fit,table=as.data.frame(fit))
}
.om_enrich_plot <- function(tab) {
  if(!nrow(tab))return(ggplot2::ggplot(data.frame(x=0,y=0),ggplot2::aes(x,y))+ggplot2::geom_text(label="无可计算富集通路")+ggplot2::theme_void())
  ggplot2::ggplot(utils::head(tab,20),ggplot2::aes(reorder(ID,-pvalue),-log10(pmax(pvalue,.Machine$double.xmin))))+ggplot2::geom_point(ggplot2::aes(size=Count))+ggplot2::coord_flip()+ggplot2::theme_minimal()
}
'''

_BODIES: dict[str,str] = {}
INPUTS: dict[str,dict[str,str]] = {}
ROLE_MAPS: dict[str,dict[str,str]] = {}
REFERENCE_CASES: dict[str,str] = {'bulk_rnaseq': '原始整数计数SE与真实DESeq2比较；拒绝取整伪计数，正常化及全角色验收。', 'microarray_expression': '真实limma特征检验；独立矩阵JSON注释严格ID对齐和反向比较等于均值差。', 'proteomics_ms': '60强度特征实际limma模型、PCA/火山及缺失记录。', 'methylation_expression_proxy': '明确log2矩阵真实limma；PCA与标准化热图，未自动猜测beta/M。', 'metabolomics_xcms': '已形成峰表的明确强度尺度limma与PCA，不虚构原始峰识别。', 'omics_metabolomics_pathway': '实际limma差异代谢物与确认映射clusterProfiler ORA；背景为检测特征。', 'omics_atac_chip_binding': '真实整数peak count的DESeq2差异结合和火山；批次设计实际入模。', 'rnaseq_batch_qc': '真实edgeR TMM因子积为1，计数深度/PCA/设计矩阵秩诊断通过验收。', 'wgcna_network': 'WGCNA blockwiseModules signed真网络、模块/特征向量相关/kME hub，60特征。', 'pathway_enrichment': 'ORA P与完整背景超几何精确值相等；真实GSEA对象、NES与运行曲线。', 'genomic_ranges': '明确坐标体系及确认版本/参考区间；原生findOverlaps核对4命中和宽度。', 'peak_region_analysis': '实际重叠与注释；BED半开区间明确转换为闭区间，邻接区间无假重叠。', 'microbiome_amplicon': '真实vegan多样性、Bray PCoA/NMDS/PERMANOVA与CLR-limma；注释保留。', 'omics_microbiome_compositional': '原始丰度CLR按样本均值为零，limma实际组成关联与ordination。', 'multiassay_clinical_bridge': '唯一样本ID桥接，已知特征临床效应约3，HC1关联与BH。', 'immune_repertoire_summary': '聚合克隆Shannon与手算相等、真实共享克隆3个。', 'omics_eqtl': '剂量eQTL调整系数与原生lm逐项相等。', 'omics_gwas_prs': '真实逐剂量GLM和锁定外部PRS精确加权，不在评估数据训练权重。', 'omics_variant_burden_annotation': '显式零剂量基因负担，调整系数与原生lm精确相等。', 'omics_somatic_mutation_cnv': '确认中性类别后的显式检测分母，变异频率0.3，真实状态矩阵。', 'single_cell': '原生SCE计数进入Seurat实际Normalize/PCA/邻接聚类/UMAP与markers，100细胞。', 'scrna_marker_enrichment': 'Seurat真实组别marker检验和检测背景clusterProfiler富集。', 'scrna_sample_integration': '真实FindIntegrationAnchors/IntegrateData integrated assay；样本组成和为1。', 'omics_scrna_trajectory': 'slingshot真实谱系/伪时序与tradeSeq负二项GAM associationTest，24基因。', 'omics_scrna_differential_abundance': '12供体×2细胞类型真实计数edgeR QL，供体为独立重复。', 'omics_spatial_transcriptomics': '二维真实坐标；样本×区域整数pseudobulk DESeq2，每组独立样本下限检查。', 'flow_cytometry': '确认门控事件按12独立样本计数，quasibinomial比例差异及HC1推断。', 'multiomics_integration': '真实MFA与跨组学关联；原生MAE逆序sampleMap严格对齐误差0。', 'omics_alternative_splicing': 'edgeR diffSpliceDGE真差异剪接；12基因/36事件Simes与exon检验。'}

REFERENCE_CASES.update({'bulk_rnaseq': '原始整数SE与实际CSV经inspect/preparer/冻结链DESeq2；规范化、方向及全角色验收。', 'proteomics_ms': '真实limma差异/PCA；实际CSV+JSON records确认映射经preparer执行clusterProfiler ORA，无映射不造通路。', 'metabolomics_xcms': '明确尺度峰表limma；确认映射时实际clusterProfiler ORA；模型文字独立，不冒称原始峰识别。', 'wgcna_network': 'WGCNA signed真网络、实际模块性状热图/相关/kME hubs；hub角色正确，真实CSV准备链通过。', 'genomic_ranges': '确认版本/区间坐标findOverlaps核对4命中与宽度；实际基因组start/end轨迹。', 'pathway_enrichment': 'ORA P与完整背景超几何一致；原生共享基因emapplot、真实GSEA/NES/运行曲线，分支输出条件化。', 'microbiome_amplicon': '实际vegan/CLR-limma、原生phyloseq导入注释；已确认协变量与pair限制置换进入PERMANOVA。', 'omics_eqtl': '单对兼容与真实CSV多对调整lm/HC1；明确cis/trans/未分类、全族BH/真实QQ；注释缺失不删样本，冲突拒绝。', 'omics_gwas_prs': '逐剂量GLM与锁定外部PRS精确加权；真实二分类AUC在性能表，未在评估集训练权重。', 'omics_scrna_trajectory': 'slingshot实际谱系/伪时序、tradeSeq NB GAM/associationTest/predictSmooth；实际表达趋势与轨迹组合。', 'omics_spatial_transcriptomics': '真实坐标和独立样本区域pseudobulk DESeq2；完整配对入模，部分配对/设计冲突拒绝，真实基因表达地图。', 'omics_alternative_splicing': 'edgeR diffSpliceDGE真剪接；12基因/36事件，primary输出真实exon事件，保留Simes与事件检验。', 'single_cell': '真实SCE RDS导入经准备/冻结链Seurat Normalize/PCA/聚类/UMAP与markers，100细胞。', 'omics_scrna_differential_abundance': '供体级cellcounts edgeR QL；table/独立matrix的唯一观测轴与重复供体分开；实际cells CSV准备链通过。', 'multiomics_integration': '真实MFA跨组学关联；MAE sampleMap逆序对齐误差0，确认colData样本ID与primary映射，批次保留表型。'})

_MATRIX_INPUTS = dict(data_object="object_v",feature_columns="features_v",matrix_orientation="orientation_v",sample_metadata="metadata_v",assay_name="assay_v",sample_id="sample_v",sample_annotations="sample_annotations_v",feature_annotations="feature_annotations_v",feature_annotation_table="annotation_table_v",group="group_v",batch="batch_v",pair_id="pair_v",covariates="covariates_v",ref_group="reference_v",comparison_group="comparison_v",input_scale="scale_v",term_gene_mapping="terms_v",background_gene_set="background_v",internal_standard="standard_v")

_EXPRESSION = r'''
om <- .om_read();x<-om$x;meta<-om$meta;design_info<-.om_design(meta)
standard<-.wc_get("standard_v");if(!is.null(standard)){
 if(!standard%in%names(meta)||!is.numeric(meta[[standard]])||any(!is.finite(meta[[standard]]))||any(meta[[standard]]<=0))stop("internal_standard需为每样本严格正数值")
 if(.wc_get("scale_v")!="linear")stop("内标校正要求原始 linear 强度，再进入log2模型")
 x<-sweep(x,2,meta[[standard]],"/")
}
missingness<-data.frame(特征=rownames(x),缺失率=rowMeans(is.na(x)))
keep<-rowSums(is.na(x))==0; x<-x[keep,,drop=FALSE]
if(nrow(x)<2)stop("完整特征不足；未自动填补缺失值")
if(.wc_get("engine_v")=="DESeq2") {
  x<-.om_counts(x);keep<-rowSums(x)>0;x<-x[keep,,drop=FALSE]
  dds<-DESeq2::DESeqDataSetFromMatrix(x,colData=design_info$data,design=design_info$formula)
  dds<-DESeq2::DESeq(dds,quiet=TRUE)
  res<-as.data.frame(DESeq2::results(dds,contrast=c(".group",design_info$comparison,design_info$reference)))
  tab<-data.frame(feature=rownames(res),logFC=res$log2FoldChange,P.Value=res$pvalue,adj.P.Val=res$padj,baseMean=res$baseMean,row.names=NULL)
  normalized<-log2(DESeq2::counts(dds,normalized=TRUE)+1)
} else {
  scale<-.wc_get("scale_v");if(is.null(scale)||!scale %in%c("linear","log2","beta","M"))stop("必须明确 input_scale")
  if(scale=="linear") {if(any(x<=0))stop("线性强度 log2 转换要求严格正值");x<-log2(x)}
  if(scale=="beta") {if(any(x<=0|x>=1))stop("Beta 转 M 值要求 (0,1) 内数值");x<-log2(x/(1-x))}
  fit<-limma::eBayes(limma::lmFit(x,design_info$design),trend=TRUE)
  res<-limma::topTable(fit,coef=design_info$coef,number=Inf,sort.by="none")
  tab<-data.frame(feature=rownames(res),res,row.names=NULL);normalized<-x
}
tab<-tab[order(tab$P.Value,na.last=TRUE),,drop=FALSE];pca<-.om_pca(normalized,meta)
annotation<-data.frame(feature=rownames(om$x),om$annotation,row.names=NULL)
if(ncol(om$annotation))tab<-cbind(tab,om$annotation[match(tab$feature,rownames(om$x)),,drop=FALSE])
.out$diff<-.wc_table("diff",tab);.out$pca<-.wc_table("pca",pca$table);.out$annotation<-.wc_table("annotation",annotation)
.out$volcano<-.wc_figure("volcano",.om_volcano(tab));.out$heatmap<-.wc_figure("heatmap",.om_heat(normalized));.out$pca_fig<-.wc_figure("pca_fig",pca$figure)
.out$qc_fig<-.wc_figure("qc_fig",ggplot2::ggplot(missingness,ggplot2::aes(缺失率))+ggplot2::geom_histogram(bins=20)+ggplot2::theme_minimal())
.out<-c(.out,.om_finish(paste(.wc_get("engine_v"),"实际设计矩阵；比较",design_info$comparison,"对",design_info$reference),paste(nrow(tab),"个特征完成检验；BH FDR<0.05:",sum(tab$adj.P.Val<.05,na.rm=TRUE),"；完整特征筛选，无自动插补。")))
.out$missing<-.wc_text("missing",paste("原始特征",nrow(om$x),"；缺失特征",sum(!keep),"。未运行未经指定的缺失插补。"))
if(.wc_get("template_v")=="omics_metabolomics_pathway"){
  mapping<-.wc_get("terms_v");if(is.null(mapping))stop("代谢物通路分析需要已确认 term_gene_mapping，不能根据峰ID猜通路")
  selected<-tab$feature[!is.na(tab$adj.P.Val)&tab$adj.P.Val<.05];enrichment<-.om_enrich(selected,rownames(normalized),mapping)
  pathway_table<-data.frame(type="metabolite",identifier=tab$feature,effect=tab$logFC,P=tab$P.Value,FDR=tab$adj.P.Val,stringsAsFactors=FALSE)
  if(nrow(enrichment$table))pathway_table<-rbind(pathway_table,data.frame(type="pathway",identifier=enrichment$table$ID,effect=NA_real_,P=enrichment$table$pvalue,FDR=enrichment$table$p.adjust))
  .out$diff<-.wc_table("diff",pathway_table);.out$volcano<-.wc_figure("volcano",.om_enrich_plot(enrichment$table))
  .out$results<-.wc_text("results",paste(nrow(tab),"代谢物实际limma；",nrow(enrichment$table),"已确认通路实际ORA；背景为所有纳入特征，未在线伪造峰注释。"))
}
'''

for _id in ("bulk_rnaseq","microarray_expression","proteomics_ms","methylation_expression_proxy","metabolomics_xcms","omics_metabolomics_pathway","omics_atac_chip_binding"):
    _BODIES[_id]=_EXPRESSION
    INPUTS[_id]=dict(_MATRIX_INPUTS)
    if _id in ("methylation_expression_proxy","metabolomics_xcms"):
        INPUTS[_id]["data_object"]="features_v";INPUTS[_id]["group_condition"]="group_v"
        INPUTS[_id].pop("feature_columns")
        INPUTS[_id].pop("group")
    if _id=="omics_atac_chip_binding": INPUTS[_id]["sample_group"]="group_v"
    if _id=="omics_atac_chip_binding": INPUTS[_id].pop("group")
    if _id=="omics_atac_chip_binding": INPUTS[_id]["peak_annotations"]="feature_annotations_v"
    if _id=="omics_atac_chip_binding": INPUTS[_id].pop("feature_annotations")
    if _id=="omics_metabolomics_pathway": INPUTS[_id]["metabolite_annotations"]="feature_annotations_v"
    if _id=="omics_metabolomics_pathway": INPUTS[_id].pop("feature_annotations")

ROLE_MAPS.update({
 "bulk_rnaseq":dict(table_1="diff",table_2="pca",figure_1="volcano",figure_2="heatmap",figure_3="pca_fig",text_1="method"),
 "microarray_expression":dict(figure_1="qc_fig",table_1="diff",table_2="annotation",figure_2="pca_fig",figure_3="volcano",text_1="method"),
 "proteomics_ms":dict(table_1="diff",table_2="pca",figure_1="volcano",manuscript_results="pathway_results",figure_2="pca_fig",missingness_summary="missing",model_results="results",pathway_table="pathway",pathway_plot="pathway_figure"),
 "methylation_expression_proxy":dict(table_1="diff",table_2="pca",figure_1="heatmap",manuscript_results="results",method_note="method",manuscript_results_2="results",diagnostic_output="diagnostic"),
 "metabolomics_xcms":dict(table_1="diff",table_2="pca",figure_1="volcano",analysis_recommendation="pathway_results",method_note="method",manuscript_results="results",diagnostic_output="diagnostic",pathway_table="pathway",pathway_plot="pathway_figure"),
 "omics_metabolomics_pathway":dict(primary_table="diff",diagnostic_plot="volcano",manuscript_results="results"),
 "omics_atac_chip_binding":dict(primary_table="diff",diagnostic_plot="volcano",manuscript_results="results"),
})

_PROTEOMICS_ENRICHMENT = r'''
mapping<-.wc_get("terms_v")
if(!is.null(mapping)){
 selected<-tab$feature[!is.na(tab$adj.P.Val)&tab$adj.P.Val<.05]
 enrichment<-.om_enrich(selected,rownames(normalized),mapping)
 .out$pathway<-.wc_table("pathway",enrichment$table)
 .out$pathway_figure<-.wc_figure("pathway_figure",.om_enrich_plot(enrichment$table))
 label<-if(.wc_get("template_v")=="proteomics_ms")"蛋白"else"代谢物"
 .out$pathway_results<-.wc_text("pathway_results",paste(length(selected),"个BH FDR<0.05差异",label,"；",nrow(enrichment$table),"个确认映射通路实际clusterProfiler ORA；背景为全部纳入检验",label,"，未猜测ID转换。"))
}
'''

def _frozen_body(template_id):
    def render(plan):
        entries=[f"{variable}={variable}" for input_id,variable in INPUTS[template_id].items() if input_id in plan.resolved_inputs]
        prefix=".wc_inputs <- list("+",".join(dict.fromkeys(entries))+")\n"
        if "__matrix_orientation__" in plan.resolved_inputs:
            prefix+='matrix_confirmation_v <- baa_plan_input("__matrix_orientation__")\n.wc_inputs$matrix_confirmation_v <- matrix_confirmation_v\nif(!identical(.wc_inputs$matrix_confirmation_v,"samples_by_features")) stop("已冻结表格矩阵方向不兼容")\nif(!is.null(.wc_inputs$orientation_v)&&!identical(.wc_inputs$orientation_v,.wc_inputs$matrix_confirmation_v))stop("显式矩阵方向与冻结科学确认冲突")\n.wc_inputs$orientation_v <- .wc_inputs$matrix_confirmation_v\n'
        engine="DESeq2" if template_id in ("bulk_rnaseq","omics_atac_chip_binding") else "limma"
        prefix+=f'.wc_inputs$engine_v <- "{engine}"\n'
        prefix+=f'.wc_inputs$template_v <- "{template_id}"\n'
        if template_id in {"single_cell","scrna_marker_enrichment","scrna_sample_integration","omics_scrna_trajectory","omics_scrna_differential_abundance","omics_spatial_transcriptomics"}:
            prefix+='.wc_inputs$cell_observations_v <- TRUE\n'
        return prefix+_method_helpers(template_id)+_method_body(template_id)
    return render

_BODIES["rnaseq_batch_qc"] = r'''
om<-.om_read();x<-.om_counts(om$x);meta<-om$meta
y<-edgeR::calcNormFactors(edgeR::DGEList(x));normalized<-edgeR::cpm(y,log=TRUE,prior.count=2);pca<-.om_pca(normalized,meta)
qc<-data.frame(样本=colnames(x),总计数=colSums(x),检测特征数=colSums(x>0),TMM因子=y$samples$norm.factors,pca$table[,c("PC1","PC2")])
batch<-.wc_get("batch_v");group<-.wc_get("group_v");design_note<-"未指定批次/分组；仅进行计数质控。"
if(!is.null(batch)&&!batch %in%names(meta))stop("批次注释缺失")
if(!is.null(group)&&!group %in%names(meta))stop("分组注释缺失")
if(!is.null(batch)&&!is.null(group)){
  design<-stats::model.matrix(stats::as.formula(paste("~",.wc_rhs(c(batch,group)))),meta)
  design_note<-paste("批次与分组设计矩阵秩",qr(design)$rank,"/",ncol(design),if(qr(design)$rank<ncol(design))"：存在混淆，不能独立估计批次/组别效应"else"：可独立估计")
}
if(!is.null(batch))qc$批次<-meta[[batch]]
diagnostics<-do.call(rbind,lapply(c("总计数","检测特征数","TMM因子","PC1","PC2"),function(nm)data.frame(样本=qc$样本,指标=nm,值=qc[[nm]],状态="measured")))
if(!is.null(batch)&&!is.null(group))diagnostics<-rbind(diagnostics,data.frame(样本="design",指标="设计矩阵秩",值=qr(design)$rank,状态=if(qr(design)$rank<ncol(design))"confounded"else"estimable"))
.out$qc<-.wc_table("qc",qc);.out$diagnostics<-.wc_table("diagnostics",diagnostics);.out$figure<-.wc_figure("figure",pca$figure)
.out$design<-.wc_text("design",design_note);.out<-c(.out,.om_finish("edgeR 原始计数质控、TMM 与 logCPM PCA",design_note))
'''
INPUTS["rnaseq_batch_qc"]=dict(_MATRIX_INPUTS)
ROLE_MAPS["rnaseq_batch_qc"]=dict(figure_1="figure",diagnostic_output="diagnostics",text_1="design",method_note="method",manuscript_results="results")

_BODIES["wgcna_network"] = r'''
suppressPackageStartupMessages(library(WGCNA))
om<-.om_read();x<-om$x;meta<-om$meta
if(anyNA(x))stop("WGCNA 输入不得缺失，未自动填补")
scale<-.wc_get("scale_v");if(is.null(scale)||!scale %in%c("linear","log2"))stop("WGCNA需要明确输入尺度")
if(scale=="linear"){if(any(x<=0))stop("log2转换要求正值");x<-log2(x)}
batch<-.wc_get("batch_v");group<-.wc_get("group_v");if(!is.null(batch)){
 if(!batch %in%names(meta))stop("批次列不存在");preserve<-if(!is.null(group)){if(!group%in%names(meta))stop("分组列不存在");stats::model.matrix(~factor(meta[[group]]))}else matrix(1,nrow(meta),1)
 x<-limma::removeBatchEffect(x,batch=meta[[batch]],design=preserve)
}
trait<-.wc_get("trait_v");if(!trait %in%names(meta)||!is.numeric(meta[[trait]])||anyNA(meta[[trait]]))stop("WGCNA trait需要明确数值注释")
datExpr<-t(x);gsg<-WGCNA::goodSamplesGenes(datExpr,verbose=0)
if(!all(gsg$goodSamples)||!all(gsg$goodGenes))stop("WGCNA 存在不合格样本/特征，请明确预处理后重试")
power<-as.numeric(.wc_get("power_v",6));if(length(power)!=1||power<=0)stop("soft_power必须为正数")
set.seed(104729)
network<-WGCNA::blockwiseModules(datExpr,power=power,networkType="signed",TOMType="signed",minModuleSize=as.integer(.wc_get("module_size_v",30)),mergeCutHeight=.25,numericLabels=FALSE,verbose=0,nThreads=1)
modules<-data.frame(feature=colnames(datExpr),module=network$colors)
me<-network$MEs
module_cor<-data.frame(module=colnames(me),correlation=as.numeric(stats::cor(me,meta[[trait]])),P=as.numeric(WGCNA::corPvalueStudent(stats::cor(me,meta[[trait]]),nrow(meta))))
module_cor$FDR<-stats::p.adjust(module_cor$P,"BH")
membership<-stats::cor(datExpr,me);hubs<-data.frame(feature=colnames(datExpr),module=network$colors,kME=vapply(seq_len(ncol(datExpr)),function(i){nm<-paste0("ME",network$colors[i]);if(nm %in%colnames(membership))membership[i,nm]else NA_real_},numeric(1)))
hubs<-hubs[order(-abs(hubs$kME)),,drop=FALSE]
fig<-ggplot2::ggplot(module_cor,ggplot2::aes(module,correlation,fill=correlation))+ggplot2::geom_col()+ggplot2::theme_minimal()
.out$modules<-.wc_table("modules",modules);.out$traits<-.wc_table("traits",module_cor);.out$hubs<-.wc_table("hubs",hubs);.out$figure<-.wc_figure("figure",fig)
.out<-c(.out,.om_finish("WGCNA blockwiseModules signed 网络；实际模块特征向量及 kME",paste(nrow(modules),"特征；模块",length(unique(network$colors)),"；soft power",power)))
'''
INPUTS["wgcna_network"]=dict(_MATRIX_INPUTS,sample_trait="trait_v",soft_power="power_v",min_module_size="module_size_v")
ROLE_MAPS["wgcna_network"]=dict(table_1="modules",figure_1="figure",table_2="traits",figure_2="figure",table_3="hubs",manuscript_results="results")

_BODIES["pathway_enrichment"] = r'''
genes_col<-.wc_get("genes_v");ranking<-.wc_get("ranking_v");method<-.wc_get("analysis_v","ORA");species<-.wc_get("species_v")
if(is.null(species)||!length(species))stop("必须明确物种与ID体系；本流程不隐式转换ID")
mapping<-.wc_get("terms_v");if(is.null(mapping))stop("需要已确认 term_gene_mapping；不在线猜测基因集")
if(!genes_col %in%names(dataset))stop("基因ID列缺失")
universe<-as.character(.wc_get("background_v"));background_column<-.wc_get("background_column_v")
if(!is.null(background_column)){if(!background_column%in%names(dataset))stop("background_gene_column不存在");universe<-unique(as.character(dataset[[background_column]]));universe<-universe[!is.na(universe)]}
genes<-unique(as.character(dataset[[genes_col]]));genes<-genes[!is.na(genes)]
if(method %in%c("ORA","ora","enricher")){
  enrichment<-.om_enrich(genes,universe,mapping);tab<-enrichment$table
} else if(method %in%c("GSEA","gsea")){
  if(is.null(ranking)||!ranking %in%names(dataset))stop("GSEA需要 ranking_statistic")
  if(anyDuplicated(dataset[[genes_col]])||anyNA(dataset[[ranking]])||!is.numeric(dataset[[ranking]]))stop("GSEA基因ID必须唯一且排名值为完整数值")
  rank_vector<-stats::setNames(dataset[[ranking]],as.character(dataset[[genes_col]]));rank_vector<-sort(rank_vector,decreasing=TRUE)
  if(anyDuplicated(rank_vector))stop("GSEA排名存在并列，请明确解决并列后提交")
  terms<-.om_annotation(mapping);if(ncol(terms)!=2)stop("通路映射需要两列")
  set.seed(104729);enrichment<-clusterProfiler::GSEA(rank_vector,TERM2GENE=terms,pvalueCutoff=1,minGSSize=2,maxGSSize=5000,verbose=FALSE,seed=TRUE)
  tab<-as.data.frame(enrichment);if(nrow(tab))tab$Count<-lengths(strsplit(tab$core_enrichment,"/",fixed=TRUE))
}else stop("analysis_method仅支持实际 ORA/GSEA")
fig<-.om_enrich_plot(tab)
terms<-.om_annotation(mapping);ids<-if(nrow(tab))tab$ID else character()
edges<-list();if(length(ids)>1)for(i in seq_len(length(ids)-1))for(j in (i+1):length(ids)){
  a<-as.character(terms[terms[[1]]==ids[i],2]);b<-as.character(terms[terms[[1]]==ids[j],2]);overlap<-length(intersect(a,b));if(overlap>0)edges[[length(edges)+1]]<-data.frame(from=ids[i],to=ids[j],shared=overlap,jaccard=overlap/length(union(a,b)))
}
overlap_table<-if(length(edges))do.call(rbind,edges)else data.frame(from=character(),to=character(),shared=integer(),jaccard=numeric())
if(nrow(overlap_table)){
 network_fit<-if(inherits(enrichment,"gseaResult"))enrichment else enrichment$fit
 term_similarity<-enrichplot::pairwise_termsim(network_fit,method="JC",showCategory=min(20,nrow(tab)))
 network_fig<-enrichplot::emapplot(term_similarity,showCategory=min(20,nrow(tab)))+ggplot2::expand_limits(x=c(-1,1),y=c(-1,1))
}else network_fig<-ggplot2::ggplot(data.frame(x=0,y=0),ggplot2::aes(x,y))+ggplot2::geom_text(label="没有至少两个共享基因的通路；emapplot没有可连接关系")+ggplot2::theme_void()
extra_fig<-fig
if(method %in%c("GSEA","gsea")){
 gsea_fig<-if(nrow(tab))enrichplot::gseaplot2(enrichment,geneSetID=tab$ID[1],pvalue_table=FALSE)else .om_enrich_plot(tab)
 .out$gsea<-.wc_figure("gsea",gsea_fig);extra_fig<-gsea_fig
}
.out$enrichment<-.wc_table("enrichment",tab);.out$overlap<-.wc_table("overlap",overlap_table);.out$figure<-.wc_figure("figure",fig);.out$network<-.wc_figure("network",network_fig);.out$extra_plot<-.wc_figure("extra_plot",extra_fig)
.out<-c(.out,.om_finish(paste("clusterProfiler",method,"；ID体系",paste(species,collapse="/"),"；基因集为用户确认映射"),paste(nrow(tab),"条通路完成真实检验；不自动转换或替换背景。")))
'''
INPUTS["pathway_enrichment"]=dict(gene_feature_list="genes_v",ranking_statistic="ranking_v",species_id_type="species_v",background_gene_set="background_v",background_gene_column="background_column_v",analysis_method="analysis_v",term_gene_mapping="terms_v")
ROLE_MAPS["pathway_enrichment"]=dict(table_1="enrichment",figure_1="figure",figure_2="network",figure_3="gsea",table_2="enrichment",figure_4="extra_plot",text_1="method",manuscript_results="results")

_RANGES = r'''
chr<-.wc_get("chr_v");start<-.wc_get("start_v");end<-.wc_get("end_v")
df<-.wc_frame(c(chr,start,end,.wc_get("signal_v"),.wc_get("group_v"),.wc_get("annotations_v")))
coordinate_system<-.wc_get("coordinate_system_v");if(is.null(coordinate_system)||!coordinate_system%in%c("one_based_closed","zero_based_half_open"))stop("必须明确 coordinate_system，不能猜测 BED/闭区间坐标")
first<-df[[start]]+if(coordinate_system=="zero_based_half_open")1 else 0
if(any(first<1)||any(df[[end]]<first)||any(df[[start]]!=floor(df[[start]]))||any(df[[end]]!=floor(df[[end]])))stop("区间坐标无效")
ranges<-GenomicRanges::GRanges(seqnames=as.character(df[[chr]]),ranges=IRanges::IRanges(start=first,end=df[[end]]))
annotation<-data.frame(region=seq_len(nrow(df)),chromosome=as.character(df[[chr]]),start=first,end=df[[end]],width=GenomicRanges::width(ranges))
selected_annotations<-.wc_cols(c(.wc_get("signal_v"),.wc_get("group_v"),.wc_get("annotations_v")));if(length(selected_annotations))annotation<-cbind(annotation,df[,selected_annotations,drop=FALSE])
reference<-.wc_get("reference_ranges_v");annotation_version<-.wc_get("reference_version_v")
if(.wc_get("template_v")=="genomic_ranges"&&(is.null(annotation_version)||is.null(reference)))stop("基因组注释需要确认的注释库/基因组版本与对应 annotation_intervals")
if(!is.null(reference)){
  reference<-.om_annotation(reference);if(!all(c("chromosome","start","end","id")%in%names(reference)))stop("annotation_reference需明确 chromosome/start/end/id 表格")
  reference_system<-.wc_get("reference_coordinates_v",coordinate_system);if(!reference_system%in%c("one_based_closed","zero_based_half_open"))stop("参考注释坐标体系无效")
  reference_start<-reference$start+if(reference_system=="zero_based_half_open")1 else 0
  if(any(reference_start<1)||any(reference$end<reference_start))stop("参考注释区间无效")
  ref<-GenomicRanges::GRanges(reference$chromosome,IRanges::IRanges(reference_start,reference$end))
  hits<-GenomicRanges::findOverlaps(ranges,ref);overlap<-data.frame(region=S4Vectors::queryHits(hits),annotation=as.character(reference$id[S4Vectors::subjectHits(hits)]))
}else {hits<-GenomicRanges::findOverlaps(ranges,ranges);keep<-S4Vectors::queryHits(hits)<S4Vectors::subjectHits(hits);overlap<-data.frame(region=S4Vectors::queryHits(hits)[keep],overlap_region=S4Vectors::subjectHits(hits)[keep])}
fig<-ggplot2::ggplot(annotation,ggplot2::aes(chromosome,width))+ggplot2::geom_boxplot()+ggplot2::theme_minimal()
.out$annotation<-.wc_table("annotation",annotation);.out$overlap<-.wc_table("overlap",overlap);.out$figure<-.wc_figure("figure",fig)
.out<-c(.out,.om_finish(paste("GenomicRanges findOverlaps；输入",coordinate_system,"明确转换为1-based closed；仅使用提供的参考注释，库/版本",annotation_version),paste(nrow(annotation),"区间；",nrow(overlap),"重叠关系。")))
'''
for _id in ("genomic_ranges","peak_region_analysis"):
 _BODIES[_id]=_RANGES;INPUTS[_id]=dict(chromosome="chr_v",start_position="start_v",end_position="end_v",annotation_reference="reference_version_v",annotation_intervals="reference_ranges_v",group="group_v",peak_signal="signal_v",feature_annotations="annotations_v",coordinate_system="coordinate_system_v",annotation_coordinate_system="reference_coordinates_v")
ROLE_MAPS["genomic_ranges"]=dict(table_1="annotation",table_2="overlap",figure_1="figure",text_1="method")
INPUTS["genomic_ranges"]={k:v for k,v in INPUTS["genomic_ranges"].items() if k in {"chromosome","start_position","end_position","annotation_reference","annotation_intervals","coordinate_system","annotation_coordinate_system"}}
ROLE_MAPS["peak_region_analysis"]=dict(table_1="overlap",table_2="annotation",manuscript_results="results",method_note="method",manuscript_results_2="results",diagnostic_output="diagnostic")

_BODIES["multiassay_clinical_bridge"] = r'''
features<-.wc_cols(.wc_get("features_v"));sid<-.wc_get("sample_v");outcome<-.wc_get("outcome_v");covs<-.wc_cols(c(.wc_get("covariates_v"),.wc_get("group_v")))
df<-.wc_frame(c(sid,features,outcome,covs));if(anyDuplicated(df[[sid]]))stop("桥接样本ID必须唯一；不自动聚合重复样本")
y_info<-.wc_outcome(df[[outcome]],.wc_get("positive_v"),.wc_get("family_v","auto"));df$.y<-y_info$y
rows<-lapply(features,function(f){fit<-.wc_check_fit(stats::glm(stats::as.formula(paste(".y~",.wc_rhs(c(f,covs)))),df,family=if(y_info$binary)stats::binomial()else stats::gaussian()));tab<-.wc_coef_table(fit,.wc_vcov(fit),exponentiate=y_info$binary);tab<-tab[tab$变量==f,,drop=FALSE];tab$feature<-f;tab})
model<-do.call(rbind,rows);model$FDR<-stats::p.adjust(model$P值,"BH")
alignment<-data.frame(样本=as.character(df[[sid]]),纳入=TRUE);candidates<-model[order(model$FDR),,drop=FALSE]
.out$alignment<-.wc_table("alignment",alignment);.out$model<-.wc_table("model",model);.out$candidates<-.wc_table("candidates",candidates)
.out<-c(.out,.om_finish("逐特征调整临床 GLM；BH 多重比较；唯一样本ID桥接",paste(nrow(df),"样本；",length(features),"候选特征。结果为关联，不自动声称已验证预测标志物。")))
'''
INPUTS["multiassay_clinical_bridge"]=dict(sample_id="sample_v",data_object="features_v",outcome="outcome_v",group="group_v",covariates="covariates_v",positive_class="positive_v",outcome_family="family_v")
ROLE_MAPS["multiassay_clinical_bridge"]=dict(table_1="alignment",table_2="model",table_3="candidates",method_note="method",manuscript_results="results",diagnostic_output="diagnostic")

_BODIES["immune_repertoire_summary"] = r'''
sid<-.wc_get("sample_v");clone<-.wc_get("clone_v");measure<-.wc_get("measure_v");group<-.wc_get("group_v")
df<-.wc_frame(c(sid,clone,measure,group));if(!is.numeric(df[[measure]])||any(df[[measure]]<0))stop("克隆丰度必须非负数值")
ab<-stats::aggregate(df[[measure]],list(sample=as.character(df[[sid]]),clone=as.character(df[[clone]])),sum);names(ab)[3]<-"count"
mat<-stats::xtabs(count~sample+clone,ab);if(any(rowSums(mat)<=0))stop("存在零总丰度样本")
diversity<-data.frame(sample=rownames(mat),richness=rowSums(mat>0),shannon=vegan::diversity(mat),simpson=vegan::diversity(mat,"simpson"),clonality=1-vegan::diversity(mat)/log(pmax(rowSums(mat>0),2)))
if(!is.null(group)){
 if(any(vapply(split(df[[group]],df[[sid]]),function(x)length(unique(x))!=1,logical(1))))stop("样本组别不一致")
 lookup<-df[!duplicated(df[[sid]]),c(sid,group),drop=FALSE];diversity$group<-lookup[[group]][match(diversity$sample,as.character(lookup[[sid]]))]
}
ab$frequency<-ab$count/as.numeric(rowSums(mat)[ab$sample]);shared<-crossprod(mat>0);shared_count<-sum(shared[upper.tri(shared)]>0)
fig<-ggplot2::ggplot(ab,ggplot2::aes(reorder(clone,-frequency),frequency,color=sample))+ggplot2::geom_point()+ggplot2::theme_minimal()+ggplot2::theme(axis.text.x=ggplot2::element_blank())
.out$diversity<-.wc_table("diversity",diversity);.out$figure<-.wc_figure("figure",fig);.out$shared<-.wc_text("shared",paste("在至少两样本出现的克隆：",sum(colSums(mat>0)>1),"；存在共享的样本对：",shared_count))
.out<-c(.out,.om_finish("聚合克隆后 vegan Shannon/Simpson；实际共享克隆与频率",paste(nrow(mat),"样本；",ncol(mat),"克隆。")))
'''
INPUTS["immune_repertoire_summary"]=dict(sample_id="sample_v",clone_sequence_id="clone_v",measure="measure_v",group_condition="group_v")
ROLE_MAPS["immune_repertoire_summary"]=dict(table_1="diversity",figure_1="figure",text_1="shared",method_note="method",manuscript_results="results",diagnostic_output="diagnostic")

_MICROBIOME = r'''
om<-.om_read();x<-t(.om_counts(om$x));meta<-om$meta;design_info<-.om_design(meta)
alpha<-data.frame(sample=rownames(x),richness=rowSums(x>0),shannon=vegan::diversity(x),simpson=vegan::diversity(x,"simpson"))
phylogeny<-.wc_get("phylogeny_v");if(!is.null(phylogeny)){
  if(!requireNamespace("ape",quietly=TRUE))stop("已提供phylogeny需要ape包")
  tree<-ape::read.tree(text=as.character(phylogeny));if(is.null(tree$edge.length)||any(tree$edge.length<0)||!all(colnames(x)%in%tree$tip.label))stop("Newick树须包含全部taxon且具有非负枝长")
  alpha$Faith_PD<-vapply(seq_len(nrow(x)),function(i){present<-colnames(x)[x[i,]>0];if(length(present)<2)return(NA_real_);sum(ape::keep.tip(tree,present)$edge.length)},numeric(1))
}
relative<-x/rowSums(x);distance<-vegan::vegdist(relative,"bray")
pcoa<-stats::cmdscale(distance,k=2,eig=TRUE,add=TRUE);ordination<-data.frame(sample=rownames(x),PCoA1=pcoa$points[,1],PCoA2=pcoa$points[,2],group=design_info$data$.group)
set.seed(104729);nmds<-vegan::metaMDS(relative,k=2,trymax=30,autotransform=FALSE,trace=0);nmds_table<-data.frame(sample=rownames(x),vegan::scores(nmds,display="sites"))
adonis_formula<-stats::update(design_info$formula,distance~.)
permutation_blocks<-if(is.null(.wc_get("pair_v")))NULL else factor(design_info$data[[.wc_get("pair_v")]])
set.seed(104729);permutation_control<-permute::how(nperm=199,blocks=permutation_blocks)
adonis<-vegan::adonis2(adonis_formula,data=design_info$data,permutations=permutation_control,by="margin")
pseudo<-as.numeric(.wc_get("pseudocount_v",.5));if(length(pseudo)!=1||!is.finite(pseudo)||pseudo<=0)stop("CLR pseudocount须为正数")
clr<-log(x+pseudo)-rowMeans(log(x+pseudo))
fit<-limma::eBayes(limma::lmFit(t(clr),design_info$design),trend=TRUE);differential<-limma::topTable(fit,coef=design_info$coef,number=Inf,sort.by="none");differential<-data.frame(feature=rownames(differential),differential,row.names=NULL)
if(ncol(om$annotation))differential<-cbind(differential,om$annotation[match(differential$feature,rownames(om$x)),,drop=FALSE])
fig<-ggplot2::ggplot(ordination,ggplot2::aes(PCoA1,PCoA2,color=group))+ggplot2::geom_point()+ggplot2::theme_minimal()
.out$alpha<-.wc_table("alpha",alpha);.out$pcoa<-.wc_table("pcoa",ordination);.out$nmds<-.wc_table("nmds",nmds_table);.out$diff<-.wc_table("diff",differential);.out$figure<-.wc_figure("figure",fig);.out$volcano<-.wc_figure("volcano",.om_volcano(differential))
.out<-c(.out,.om_finish(paste("vegan alpha/Bray-Curtis/PCoA/NMDS/PERMANOVA；实际调整",paste(all.vars(design_info$formula),collapse=","),"；配对时限制组内置换；CLR-limma 特征关联，pseudocount",pseudo),paste(nrow(x),"样本；",ncol(x),"菌群；调整后group边际PERMANOVA P",adonis[".group","Pr(>F)"],"。CLR logFC为相对组成关联，无绝对丰度/去污染声明。")))
'''
for _id in ("microbiome_amplicon","omics_microbiome_compositional"):
 _BODIES[_id]=_MICROBIOME;INPUTS[_id]=dict(_MATRIX_INPUTS,pseudocount="pseudocount_v")
INPUTS["microbiome_amplicon"]["abundance_object"]="object_v"
INPUTS["microbiome_amplicon"].pop("data_object")
INPUTS["microbiome_amplicon"]["taxonomy_annotations"]="feature_annotations_v"
INPUTS["microbiome_amplicon"].pop("feature_annotations")
INPUTS["omics_microbiome_compositional"]["taxon_abundance"]="features_v"
INPUTS["omics_microbiome_compositional"].pop("feature_columns")
INPUTS["omics_microbiome_compositional"]["phylogeny"]="phylogeny_v"
ROLE_MAPS["microbiome_amplicon"]=dict(table_1="alpha",table_2="pcoa",table_3="nmds",figure_1="volcano",table_4="alpha",figure_2="figure",table_5="diff",manuscript_results="results")
ROLE_MAPS["omics_microbiome_compositional"]=dict(primary_table="diff",diagnostic_plot="figure",manuscript_results="results")

_BODIES["omics_eqtl"] = r'''

sid<-.wc_get("sample_v");genotype<-.wc_get("genotype_v");expression<-.wc_get("expression_v")
covs<-.wc_cols(c(.wc_get("pcs_v"),.wc_get("batch_v"),.wc_get("covariates_v")))
if(length(.wc_cols(sid))!=1||length(.wc_cols(genotype))!=1||length(.wc_cols(expression))!=1)stop("eQTL需要明确样本ID、剂量列和表达列")
if(length(unique(c(sid,genotype,expression)))!=3)stop("样本ID、剂量与表达必须使用不同数据列")
variant<-.wc_get("variant_id_v");gene<-.wc_get("gene_id_v")
has_variant<-length(.wc_cols(variant))>0;has_gene<-length(.wc_cols(gene))>0
if(has_variant!=has_gene)stop("长表eQTL必须同时指定variant_id与gene_id；单一配对可同时省略")
long_table<-has_variant&&has_gene
if(long_table&&(length(.wc_cols(variant))!=1||length(.wc_cols(gene))!=1))stop("variant_id与gene_id各须为一个确认的数据列名")
identity_cols<-.wc_cols(c(sid,if(long_table)c(variant,gene)else NULL))
model_cols<-.wc_cols(c(sid,genotype,expression,covs,if(long_table)c(variant,gene)else NULL))
position_cols<-list(variant_chromosome=.wc_get("variant_chromosome_v"),variant_position=.wc_get("variant_position_v"),gene_chromosome=.wc_get("gene_chromosome_v"),gene_start=.wc_get("gene_start_v"),gene_end=.wc_get("gene_end_v"))
if(any(vapply(position_cols,function(z)length(.wc_cols(z))>1,logical(1))))stop("每个确认位置字段须为一个数据列名")
annotations<-.wc_cols(c(.wc_get("position_v"),unlist(position_cols,use.names=FALSE)))
if(!all(c(model_cols,annotations)%in%names(dataset)))stop("eQTL确认模型列或位置注释列不存在")
for(nm in identity_cols)if(anyNA(dataset[[nm]])||any(!nzchar(as.character(dataset[[nm]]))))stop("eQTL样本/配对ID不能缺失或为空")
window<-as.numeric(.wc_get("cis_window_v",1000000))
if(length(window)!=1||!is.finite(window)||window<0||window!=floor(window))stop("cis_window须为非负整数碱基数")
# Only actual model variables and mandatory identity columns determine complete cases.
# Optional old/new positions are preserved separately and never remove observations.
df<-.om_frame(model_cols,annotations=annotations)
if(!is.numeric(df[[genotype]])||any(!df[[genotype]]%in%c(0,1,2))||!is.numeric(df[[expression]]))stop("eQTL需要0/1/2等位基因剂量和数值表达")
if(long_table){
  pairs<-unique(data.frame(variant_id=as.character(dataset[[variant]]),gene_id=as.character(dataset[[gene]]),stringsAsFactors=FALSE))
}else pairs<-data.frame(variant_id=as.character(genotype),gene_id=as.character(expression),stringsAsFactors=FALSE)
results<-lapply(seq_len(nrow(pairs)),function(i){
  if(long_table){
    raw_index<-as.character(dataset[[variant]])==pairs$variant_id[i]&as.character(dataset[[gene]])==pairs$gene_id[i]
    model_index<-as.character(df[[variant]])==pairs$variant_id[i]&as.character(df[[gene]])==pairs$gene_id[i]
    raw_pair<-dataset[raw_index,,drop=FALSE];d<-df[model_index,,drop=FALSE]
  }else{raw_pair<-dataset;d<-df}
  if(anyDuplicated(as.character(raw_pair[[sid]])))stop("同一variant/gene配对内样本ID必须唯一；不同配对可重复使用同一受试者")
  if(nrow(d)<3)stop("至少一个eQTL配对完整模型样本少于3，无法完成该检验族")
  batch<-.wc_get("batch_v");if(!is.null(batch))d[[batch]]<-factor(d[[batch]])
  fit<-.wc_check_fit(stats::lm(stats::as.formula(paste(.wc_rhs(expression),"~",.wc_rhs(c(genotype,covs)))),d))
  tab<-.wc_coef_table(fit,.wc_vcov(fit),terms=names(stats::coef(fit))[2])
  if(nrow(tab)!=1||!is.finite(tab$P值)||tab$P值<0||tab$P值>1)stop("至少一个eQTL配对HC1推断不可估计")
  tab$variant_id<-pairs$variant_id[i];tab$gene_id<-pairs$gene_id[i]
  tab$n<-nrow(d);tab$input_n<-nrow(raw_pair);tab$dropped_model_missing<-nrow(raw_pair)-nrow(d)
  tab$effect<-tab$效应;tab$CI_lower<-tab$CI下限;tab$CI_upper<-tab$CI上限;tab$P<-tab$P值
  tab$variant_chromosome<-NA_character_;tab$variant_position<-NA_real_
  tab$gene_chromosome<-NA_character_;tab$gene_start<-NA_real_;tab$gene_end<-NA_real_
  complete_location<-all(vapply(position_cols,function(z)length(.wc_cols(z))==1,logical(1)))
  for(field in names(position_cols)){
    nm<-position_cols[[field]];if(!length(.wc_cols(nm)))next
    values<-raw_pair[[nm]]
    if(field%in%c("variant_chromosome","gene_chromosome")){
      observed<-as.character(values[!is.na(values)]);observed<-observed[nzchar(observed)]
      if(length(unique(observed))>1)stop("同一variant/gene配对的确认染色体注释不一致")
      if(anyNA(values)||any(!nzchar(as.character(values))))complete_location<-FALSE
      if(length(observed))tab[[field]]<-observed[1]
    }else{
      if(all(is.na(values))){complete_location<-FALSE;next}
      if(!is.numeric(values))stop("确认位置列必须为数值；不从自由文本或旧gene_position猜测坐标")
      observed<-values[!is.na(values)]
      if(any(!is.finite(observed))||any(observed<1)||any(observed!=floor(observed)))stop("确认位置须为严格正的1-based整数坐标")
      if(length(unique(observed))>1)stop("同一variant/gene配对的确认位置注释不一致")
      if(anyNA(values))complete_location<-FALSE
      if(length(observed))tab[[field]]<-observed[1]
    }
  }
  if(is.finite(tab$gene_start)&&is.finite(tab$gene_end)&&tab$gene_end<tab$gene_start)stop("确认基因区间终点小于起点")
  tab$location_confirmed<-complete_location
  tab$location_status<-if(complete_location)"confirmed"else"incomplete"
  tab$cis_trans<-"unclassified";tab$cis_window<-window
  if(complete_location){
    same_chr<-tab$variant_chromosome==tab$gene_chromosome
    in_window<-tab$variant_position>=tab$gene_start-window&&tab$variant_position<=tab$gene_end+window
    tab$cis_trans<-if(same_chr&&in_window)"cis"else"trans"
  }
  tab<-.om_preserve_annotations(tab,raw_pair,annotations)
  list(table=tab,scatter=data.frame(dose=d[[genotype]],expression=d[[expression]]))
})
tab<-do.call(rbind,lapply(results,function(z)z$table));rownames(tab)<-NULL
# The family is every unique submitted variant/gene pair, including cis, trans,
# and unclassified pairs. There is no per-gene/per-chromosome hidden BH split.
tab$FDR<-stats::p.adjust(tab$P,"BH");tab$test_family_size<-nrow(tab)
if(nrow(tab)>=2){
  qq<-data.frame(expected=-log10(stats::ppoints(nrow(tab))),observed=-log10(pmax(sort(tab$P),.Machine$double.xmin)))
  fig<-ggplot2::ggplot(qq,ggplot2::aes(expected,observed))+ggplot2::geom_point()+ggplot2::geom_abline(intercept=0,slope=1)+ggplot2::theme_minimal()+ggplot2::labs(x="期望 -log10(P)",y="观察 -log10(P)",title=paste("eQTL实际QQ：",nrow(tab),"个唯一配对检验"))
  qq_note<-paste("QQ基于全部",nrow(tab),"个唯一配对的实际HC1检验P值。")
}else{
  scatter<-results[[1]]$scatter
  fig<-ggplot2::ggplot(scatter,ggplot2::aes(dose,expression))+ggplot2::geom_point(position=ggplot2::position_jitter(width=.08,height=0,seed=104729))+ggplot2::geom_smooth(method="lm",formula=y~x)+ggplot2::theme_minimal()+ggplot2::labs(x="等位基因剂量",y="实际表达",title="QQ不可评估：本轮仅一个eQTL检验",subtitle="图示实际纳入样本的剂量与表达；调整效应及HC1区间见结果表")
  qq_note<-"本轮仅一个配对检验，QQ不可评估；诊断图为实际模型样本的剂量与表达。"
}
.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",fig)
.out<-c(.out,.om_finish("逐variant/gene配对剂量线性模型、确认协变量调整与HC1推断；全检验族BH；明确坐标的cis/trans分类",paste(nrow(tab),"个唯一配对；cis",sum(tab$cis_trans=="cis"),"；trans",sum(tab$cis_trans=="trans"),"；坐标未分类",sum(tab$cis_trans=="unclassified"),"；cis窗口",window,"bp。",qq_note,"仅模型变量缺失参与完整病例筛选；旧gene_position仅保留注释，不解析坐标。")))

'''
INPUTS["omics_eqtl"]=dict(sample_id="sample_v",genotype_variable="genotype_v",expression_variable="expression_v",principal_components="pcs_v",batch="batch_v",covariates="covariates_v",gene_position="position_v")
INPUTS["omics_eqtl"].update({'variant_id': 'variant_id_v', 'gene_id': 'gene_id_v', 'variant_chromosome': 'variant_chromosome_v', 'variant_position': 'variant_position_v', 'gene_chromosome': 'gene_chromosome_v', 'gene_start': 'gene_start_v', 'gene_end': 'gene_end_v', 'cis_window': 'cis_window_v'})

_BODIES["omics_gwas_prs"] = r'''
sid<-.wc_get("sample_v");phenotype<-.wc_get("outcome_v");features<-.wc_cols(.wc_get("features_v"));covs<-.wc_cols(c(.wc_get("pcs_v"),.wc_get("covariates_v")))
df<-.wc_frame(c(sid,phenotype,features,covs,.wc_get("split_v")));if(anyDuplicated(df[[sid]]))stop("GWAS/PRS样本ID必须唯一")
for(f in features)if(!is.numeric(df[[f]])||any(!df[[f]]%in%c(0,1,2)))stop("遗传变量必须为 0/1/2 剂量；锁定PRS另提供 score_weights")
y_info<-.wc_outcome(df[[phenotype]],.wc_get("positive_v"),.wc_get("family_v","auto"));df$.y<-y_info$y
rows<-lapply(features,function(f){fit<-.wc_check_fit(stats::glm(stats::as.formula(paste(".y~",.wc_rhs(c(f,covs)))),df,family=if(y_info$binary)stats::binomial()else stats::gaussian()));tab<-.wc_coef_table(fit,.wc_vcov(fit),terms=f,exponentiate=y_info$binary);tab$类型<-"GWAS association";tab});tab<-do.call(rbind,rows);tab$FDR<-stats::p.adjust(tab$P值,"BH");tab$validation_n<-NA_integer_;tab$AUC<-NA_real_;tab$continuous_score_R2<-NA_real_
score_weights<-as.numeric(.wc_get("score_weights_v"));prs_note<-"未提供锁定外部权重，未生成或评估PRS。"
if(length(score_weights)){
 if(length(score_weights)!=length(features)||any(!is.finite(score_weights)))stop("score_weights须与剂量列逐项对应")
 df$.prs<-as.numeric(as.matrix(df[,features,drop=FALSE])%*%score_weights)
 split<-.wc_get("split_v");validation_level<-.wc_get("validation_v");test<-rep(TRUE,nrow(df))
 if(!is.null(split)){if(is.null(validation_level)||!validation_level%in%df[[split]])stop("必须指定 validation_level");test<-as.character(df[[split]])==as.character(validation_level)}
 validation<-df[test,,drop=FALSE];if(nrow(validation)<10)stop("PRS评估样本不足")
 prs_fit<-.wc_check_fit(stats::glm(stats::as.formula(paste(".y~",.wc_rhs(c(".prs",covs)))),validation,family=if(y_info$binary)stats::binomial()else stats::gaussian()));prs_tab<-.wc_coef_table(prs_fit,.wc_vcov(prs_fit),terms=".prs",exponentiate=y_info$binary);prs_tab$类型<-"locked PRS association";prs_tab$FDR<-NA_real_;prs_tab$validation_n<-nrow(validation);prs_tab$AUC<-if(y_info$binary)as.numeric(pROC::auc(pROC::roc(validation$.y,validation$.prs,levels=c(0,1),direction="<",quiet=TRUE)))else NA_real_;prs_tab$continuous_score_R2<-if(!y_info$binary)stats::cor(validation$.y,validation$.prs)^2 else NA_real_;tab<-rbind(tab,prs_tab)
 prs_note<-paste("锁定外部权重PRS关联评估样本",nrow(validation),"；未拟合权重，无训练测试泄漏；实际性能见表中AUC或连续score相关R2。")
}
qq<-data.frame(expected=-log10(stats::ppoints(length(features))),observed=-log10(sort(pmax(tab$P值[seq_along(features)],.Machine$double.xmin))))
.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",ggplot2::ggplot(qq,ggplot2::aes(expected,observed))+ggplot2::geom_point()+ggplot2::geom_abline()+ggplot2::theme_minimal());.out<-c(.out,.om_finish("逐剂量 GLM 与 BH 校正；可选锁定外部权重PRS",prs_note))
'''
INPUTS["omics_gwas_prs"]=dict(sample_id="sample_v",phenotype="outcome_v",genotype_prs_variable="features_v",principal_components="pcs_v",covariates="covariates_v",validation_split="split_v",validation_level="validation_v",score_weights="score_weights_v",positive_class="positive_v",outcome_family="family_v")

_BODIES["omics_variant_burden_annotation"] = r'''
sid<-.wc_get("sample_v");gene<-.wc_get("gene_v");dose<-.wc_get("dose_v");outcome<-.wc_get("outcome_v");covs<-.wc_cols(.wc_get("covariates_v"));weight<-.wc_get("weight_v")
df<-.om_frame(c(sid,gene,dose,outcome,covs,weight),.wc_get("annotations_v"));if(!is.numeric(df[[dose]])||any(!df[[dose]]%in%c(0,1,2)))stop("变异负担需要原始 0/1/2 剂量")
for(v in c(outcome,covs))if(any(vapply(split(df[[v]],df[[sid]]),function(x)length(unique(x))!=1,logical(1))))stop("样本内结局与协变量必须一致")
df$.weighted<-df[[dose]];if(!is.null(weight)){if(!is.numeric(df[[weight]])||any(df[[weight]]<0))stop("功能权重需要非负数值");df$.weighted<-df$.weighted*df[[weight]]}
burden<-stats::aggregate(df$.weighted,list(sample=as.character(df[[sid]]),gene=as.character(df[[gene]])),sum)
subjects<-df[!duplicated(df[[sid]]),c(sid,outcome,covs),drop=FALSE];y_info<-.wc_outcome(subjects[[outcome]],.wc_get("positive_v"),.wc_get("family_v","auto"));subjects$.y<-y_info$y
rows<-lapply(unique(burden$gene),function(g){b<-burden[burden$gene==g,];d<-subjects;d$.burden<-b$x[match(as.character(d[[sid]]),b$sample)];if(anyNA(d$.burden))stop("每个样本-基因需明确记录零剂量；缺行不视为野生型");fit<-.wc_check_fit(stats::glm(stats::as.formula(paste(".y~",.wc_rhs(c(".burden",covs)))),d,family=if(y_info$binary)stats::binomial()else stats::gaussian()));t<-.wc_coef_table(fit,.wc_vcov(fit),terms=".burden",exponentiate=y_info$binary);t$gene<-g;t})
tab<-do.call(rbind,rows);tab$FDR<-stats::p.adjust(tab$P值,"BH");tab<-.om_preserve_annotations(tab,df,.wc_get("annotations_v"),gene=gene)
fig<-ggplot2::ggplot(tab,ggplot2::aes(gene,效应))+ggplot2::geom_point()+ggplot2::geom_errorbar(ggplot2::aes(ymin=CI下限,ymax=CI上限)) +ggplot2::theme_minimal()
.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",fig);.out<-c(.out,.om_finish("已确认变异注释上的显式加权剂量基因负担 GLM；不在线伪造注释",paste(nrow(tab),"基因；零剂量必须显式存在；BH 校正。")))
'''
INPUTS["omics_variant_burden_annotation"]=dict(sample_id="sample_v",gene_region="gene_v",dose="dose_v",phenotype="outcome_v",functional_annotations="annotations_v",covariates="covariates_v",weight="weight_v",positive_class="positive_v",outcome_family="family_v")

_BODIES["omics_somatic_mutation_cnv"] = r'''
sid<-.wc_get("sample_v");gene<-.wc_get("gene_v");state<-.wc_get("state_v");outcome<-.wc_get("outcome_v");tumor<-.wc_get("tumor_v")
df<-.om_frame(c(sid,gene,state,outcome),c(tumor,.wc_get("annotations_v")));if(anyDuplicated(paste(df[[sid]],df[[gene]],sep="\r")))stop("每个样本-基因/片段只能有一个确认状态；不自动合并冲突变异")
reference<-.wc_get("neutral_v");if(is.null(reference)||!reference%in%as.character(df[[state]]))stop("必须明确 neutral_state，缺失记录不视为阴性")
df$.altered<-as.character(df[[state]])!=as.character(reference)
tab<-do.call(rbind,lapply(split(df,as.character(df[[gene]])),function(d){data.frame(gene=as.character(d[[gene]][1]),samples=nrow(d),altered=sum(d$.altered),frequency=mean(d$.altered))}))
tab<-.om_preserve_annotations(tab,df,c(tumor,.wc_get("annotations_v")),gene=gene)
if(!is.null(outcome)){if(!all(vapply(split(df[[outcome]],df[[sid]]),function(x)length(unique(x))==1,logical(1))))stop("样本结局不一致");association<-lapply(split(df,as.character(df[[gene]])),function(d){y<-.wc_binary(d[[outcome]],.wc_get("positive_v"));ct<-table(factor(d$.altered,levels=c(FALSE,TRUE)),factor(y,levels=0:1));if(any(rowSums(ct)==0)||any(colSums(ct)==0))return(NA_real_);stats::fisher.test(ct)$p.value});tab$P<-unlist(association);tab$FDR<-stats::p.adjust(tab$P,"BH")}
df$.sample<-as.character(df[[sid]]);df$.gene<-as.character(df[[gene]]);df$.state<-as.character(df[[state]])
fig<-ggplot2::ggplot(df,ggplot2::aes(.sample,.gene,fill=.state))+ggplot2::geom_tile()+ggplot2::theme_minimal()+ggplot2::theme(axis.text.x=ggplot2::element_blank())
.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",fig);.out<-c(.out,.om_finish("确认体细胞/CNV状态矩阵频率与可选 Fisher 关联；不从类别标签重建分段/变异调用",paste(nrow(tab),"特征；状态参照",reference,"；分母为显式检测样本。")))
'''
INPUTS["omics_somatic_mutation_cnv"]=dict(sample_id="sample_v",gene_segment="gene_v",variant_cnv_state="state_v",tumor_type="tumor_v",outcome="outcome_v",mutation_annotations="annotations_v",neutral_state="neutral_v",positive_class="positive_v")
for _id in ("omics_eqtl","omics_gwas_prs","omics_variant_burden_annotation","omics_somatic_mutation_cnv"):
 ROLE_MAPS[_id]=dict(primary_table="primary",diagnostic_plot="figure",manuscript_results="results")

_CELL_HELPERS = r'''
.om_seurat <- function(om) {
  counts<-.om_counts(om$x)
  if(ncol(counts)<20||nrow(counts)<20)stop("单细胞至少需要20细胞及20特征")
  seurat<-Seurat::CreateSeuratObject(counts=counts,meta.data=om$meta,min.cells=0,min.features=0)
  seurat<-Seurat::NormalizeData(seurat,verbose=FALSE)
  seurat<-Seurat::FindVariableFeatures(seurat,nfeatures=min(2000,nrow(seurat)),verbose=FALSE)
  seurat<-Seurat::ScaleData(seurat,verbose=FALSE)
  dims<-as.integer(.wc_get("dimensions_v",5));if(dims<2||dims>=min(dim(seurat)))stop("PCA dimensions无效")
  seurat<-Seurat::RunPCA(seurat,npcs=dims,verbose=FALSE)
  list(object=seurat,dims=seq_len(dims))
}
.om_cell_plot <- function(seurat) {
  embedding<-Seurat::Embeddings(seurat,"umap");d<-data.frame(cell=rownames(embedding),UMAP1=embedding[,1],UMAP2=embedding[,2],cluster=as.character(Seurat::Idents(seurat)))
  list(table=d,figure=ggplot2::ggplot(d,ggplot2::aes(UMAP1,UMAP2,color=cluster))+ggplot2::geom_point(size=.5)+ggplot2::theme_minimal())
}
'''

_SINGLE = r'''
om<-.om_read();prep<-.om_seurat(om);seurat<-prep$object
sample<-.wc_get("sample_v");if(is.null(sample)||!sample%in%names(om$meta))stop("必须提供每个细胞的 sample_id")
set.seed(104729);seurat<-Seurat::FindNeighbors(seurat,dims=prep$dims,verbose=FALSE)
seurat<-Seurat::FindClusters(seurat,resolution=as.numeric(.wc_get("resolution_v",.5)),verbose=FALSE,random.seed=104729)
seurat<-Seurat::RunUMAP(seurat,dims=prep$dims,n.neighbors=min(15,ncol(seurat)-1),seed.use=104729,verbose=FALSE)
embedding<-.om_cell_plot(seurat)
condition_fig<-embedding$figure;condition<-.wc_get("group_v");if(!is.null(condition)){
 if(!condition%in%names(om$meta))stop("明确 group 注释不存在")
 condition_data<-embedding$table;condition_data$condition<-as.character(om$meta[[condition]])
 condition_fig<-ggplot2::ggplot(condition_data,ggplot2::aes(UMAP1,UMAP2,color=condition))+ggplot2::geom_point(size=.5)+ggplot2::theme_minimal()
}
markers<-Seurat::FindAllMarkers(seurat,only.pos=TRUE,test.use="wilcox",min.pct=.1,logfc.threshold=0,verbose=FALSE)
if(!nrow(markers))markers<-data.frame(gene=character(),cluster=character(),p_val=numeric(),p_val_adj=numeric())
qc<-data.frame(cell=colnames(seurat),sample=as.character(om$meta[[sample]]),counts=colSums(om$x),features=colSums(om$x>0))
annotation<-data.frame(embedding$table,om$meta,row.names=NULL)
qcfig<-ggplot2::ggplot(qc,ggplot2::aes(counts,features,color=sample))+ggplot2::geom_point(size=.5)+ggplot2::theme_minimal()
.out$markers<-.wc_table("markers",markers);.out$annotation<-.wc_table("annotation",annotation);.out$qc<-.wc_table("qc",qc);.out$qcfig<-.wc_figure("qcfig",qcfig);.out$figure<-.wc_figure("figure",embedding$figure)
.out$condition<-.wc_figure("condition",condition_fig)
.out<-c(.out,.om_finish("Seurat 原始计数 LogNormalize/高变特征/Scale/PCA/邻接聚类/真实UMAP/Wilcoxon markers",paste(ncol(seurat),"细胞；",length(unique(Seurat::Idents(seurat))),"聚类；未自动命名细胞类型，已提供注释原样保留。")))
'''
_BODIES["single_cell"]=_CELL_HELPERS+_SINGLE
INPUTS["single_cell"]=dict(_MATRIX_INPUTS,cell_annotations="cell_annotations_v",pca_dimensions="dimensions_v",cluster_resolution="resolution_v")
ROLE_MAPS["single_cell"]=dict(figure_1="qcfig",figure_2="condition",table_1="markers",table_2="annotation",table_3="qc",figure_3="figure",manuscript_results="results")

_BODIES["scrna_marker_enrichment"] = _CELL_HELPERS+r'''
om<-.om_read();prep<-.om_seurat(om);seurat<-prep$object;design_info<-.om_design(om$meta)
Seurat::Idents(seurat)<-design_info$data$.group
markers<-Seurat::FindMarkers(seurat,ident.1=design_info$comparison,ident.2=design_info$reference,test.use="wilcox",logfc.threshold=0,min.pct=.1,verbose=FALSE)
markers<-data.frame(gene=rownames(markers),markers,row.names=NULL)
mapping<-.wc_get("terms_v");if(is.null(mapping))stop("Marker解释需要确认的 term_gene_mapping")
selected<-markers$gene[markers$p_val_adj<.05 & markers$avg_log2FC>0]
enrichment<-.om_enrich(selected,rownames(om$x),mapping)
.out$markers<-.wc_table("markers",markers);.out$figure<-.wc_figure("figure",.om_enrich_plot(enrichment$table))
.out<-c(.out,.om_finish("Seurat 实际 marker 检验后 clusterProfiler ORA，背景为实际检测特征",paste(length(selected),"正向显著marker；",nrow(enrichment$table),"富集通路。细胞级marker解释不等于跨供体差异表达。")))
'''
INPUTS["scrna_marker_enrichment"]=dict(_MATRIX_INPUTS,group_condition="group_v",cell_annotations="cell_annotations_v",pca_dimensions="dimensions_v")
INPUTS["scrna_marker_enrichment"].pop("group")
ROLE_MAPS["scrna_marker_enrichment"]=dict(table_1="markers",manuscript_results="results",figure_1="figure",method_note="method",manuscript_results_2="results",diagnostic_output="diagnostic")

_BODIES["scrna_sample_integration"] = _CELL_HELPERS+r'''
om<-.om_read();batch<-.wc_get("batch_v");sample<-.wc_get("sample_v");if(!all(c(batch,sample)%in%names(om$meta)))stop("整合需明确细胞样本与批次注释")
if(length(unique(om$meta[[batch]]))<2)stop("整合需要至少两个批次")
base<-Seurat::CreateSeuratObject(.om_counts(om$x),meta.data=om$meta,min.cells=0,min.features=0)
objects<-Seurat::SplitObject(base,split.by=batch)
objects<-lapply(objects,function(s){s<-Seurat::NormalizeData(s,verbose=FALSE);Seurat::FindVariableFeatures(s,nfeatures=min(2000,nrow(s)),verbose=FALSE)})
dims<-seq_len(as.integer(.wc_get("dimensions_v",5)));if(min(vapply(objects,ncol,numeric(1)))<20)stop("每批次至少20细胞")
features<-Seurat::SelectIntegrationFeatures(objects,nfeatures=min(2000,nrow(base)))
set.seed(104729);anchors<-Seurat::FindIntegrationAnchors(objects,anchor.features=features,dims=dims,k.anchor=3,k.filter=min(100,min(vapply(objects,ncol,numeric(1)))-1),k.score=10,verbose=FALSE)
seurat<-Seurat::IntegrateData(anchors,dims=dims,k.weight=min(20,min(vapply(objects,ncol,numeric(1)))-1),verbose=FALSE)
seurat<-Seurat::ScaleData(seurat,verbose=FALSE);seurat<-Seurat::RunPCA(seurat,npcs=max(dims),verbose=FALSE)
seurat<-Seurat::FindNeighbors(seurat,dims=dims,verbose=FALSE);seurat<-Seurat::FindClusters(seurat,resolution=.5,random.seed=104729,verbose=FALSE)
seurat<-Seurat::RunUMAP(seurat,dims=dims,n.neighbors=15,seed.use=104729,verbose=FALSE)
embedding<-.om_cell_plot(seurat);proportions<-as.data.frame(table(sample=as.character(seurat[[]][[sample]]),cluster=as.character(Seurat::Idents(seurat))));proportions$proportion<-proportions$Freq/ave(proportions$Freq,proportions$sample,FUN=sum)
condition<-.wc_get("group_v");note<-"整合表达用于聚类展示；真实组间检验需使用原始计数并按供体重复建模。"
if(!is.null(condition)){tab<-table(om$meta[[batch]],om$meta[[condition]]);note<-paste(note,"批次×条件覆盖：",paste(capture.output(print(tab)),collapse=" "))}
.out$proportions<-.wc_table("proportions",proportions);.out$figure<-.wc_figure("figure",embedding$figure);.out$condition<-.wc_text("condition",note)
.out<-c(.out,.om_finish("Seurat FindIntegrationAnchors/IntegrateData 实际锚点整合后 PCA/UMAP",note))
'''
INPUTS["scrna_sample_integration"]=dict(_MATRIX_INPUTS,group_condition="group_v",pca_dimensions="dimensions_v")
INPUTS["scrna_sample_integration"].pop("group")
ROLE_MAPS["scrna_sample_integration"]=dict(figure_1="figure",table_1="proportions",text_1="condition",method_note="method",manuscript_results="results",diagnostic_output="diagnostic")

_BODIES["omics_scrna_trajectory"] = r'''
om<-.om_read();counts<-.om_counts(om$x);cluster_cols<-.wc_cols(.wc_get("clusters_v"));if(length(cluster_cols)!=1||!cluster_cols%in%names(om$meta))stop("轨迹需要明确一个已确认 cluster 注释列")
start<-.wc_get("start_cluster_v");if(is.null(start)||!start%in%as.character(om$meta[[cluster_cols]]))stop("必须明确 starting_cell_population")
logcounts<-log1p(t(t(counts)/colSums(counts))*10000);pca<-.om_pca(logcounts,om$meta)
sce<-SingleCellExperiment::SingleCellExperiment(assays=list(counts=counts,logcounts=logcounts),colData=S4Vectors::DataFrame(om$meta))
SingleCellExperiment::reducedDim(sce,"PCA")<-pca$model$x[,seq_len(min(5,ncol(pca$model$x))),drop=FALSE]
sce<-slingshot::slingshot(sce,clusterLabels=as.character(om$meta[[cluster_cols]]),reducedDim="PCA",start.clus=as.character(start))
pseudotime<-slingshot::slingPseudotime(sce,na=FALSE);weights<-slingshot::slingCurveWeights(sce)
if(any(!is.finite(pseudotime))||!ncol(pseudotime))stop("原生slingshot未产生可估计轨迹")
nuisance<-.wc_cols(c(.wc_get("sample_v"),.wc_get("group_v")));U<-NULL
if(length(nuisance)){
 if(!all(nuisance%in%names(om$meta))||anyNA(om$meta[,nuisance,drop=FALSE]))stop("轨迹调整样本/组别注释缺失")
 U<-stats::model.matrix(stats::as.formula(paste("~",.wc_rhs(nuisance))),om$meta)
 if(qr(U)$rank<ncol(U))stop("轨迹样本/组别调整设计混淆")
}
set.seed(104729);fit<-tradeSeq::fitGAM(counts=counts,pseudotime=pseudotime,cellWeights=weights,U=U,nknots=as.integer(.wc_get("knots_v",4)),verbose=FALSE,parallel=FALSE)
tests<-tradeSeq::associationTest(fit);tab<-data.frame(feature=rownames(tests),tests,row.names=NULL);tab$FDR<-stats::p.adjust(tab$pvalue,"BH")
trajectory<-data.frame(cell=colnames(counts),PC1=pca$model$x[,1],PC2=pca$model$x[,2],pseudotime=pseudotime[,1],cluster=as.character(om$meta[[cluster_cols]]))
fig<-ggplot2::ggplot(trajectory,ggplot2::aes(PC1,PC2,color=pseudotime))+ggplot2::geom_point()+ggplot2::theme_minimal()
curves<-slingshot::slingCurves(sce);curve<-do.call(rbind,lapply(seq_along(curves),function(i)data.frame(PC1=curves[[i]]$s[curves[[i]]$ord,1],PC2=curves[[i]]$s[curves[[i]]$ord,2],lineage=i)))
fig<-fig+ggplot2::geom_path(data=curve,ggplot2::aes(PC1,PC2,group=lineage),inherit.aes=FALSE,color="black")
.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",fig);.out<-c(.out,.om_finish("原生 slingshot 曲线与伪时序；tradeSeq 负二项 GAM associationTest",paste(ncol(pseudotime),"条真实谱系；起始cluster",start,"；",nrow(tab),"基因沿轨迹检验；明确调整",paste(nuisance,collapse=","),"。伪时序不等同真实时间/因果。")))
'''
INPUTS["omics_scrna_trajectory"]=dict(_MATRIX_INPUTS,cell_annotations="clusters_v",starting_cell_population="start_cluster_v",trajectory_knots="knots_v")

_BODIES["omics_scrna_differential_abundance"] = r'''
om<-.om_read();meta<-om$meta;sid<-.wc_get("sample_v");celltype<-.wc_get("type_v");group<-.wc_get("group_v");covs<-.wc_cols(c(.wc_get("batch_v"),.wc_get("pair_v"),.wc_get("covariates_v")))
if(!all(c(sid,celltype,group,covs)%in%names(meta))||anyNA(meta[,c(sid,celltype,group,covs),drop=FALSE]))stop("差异丰度细胞注释缺失")
for(v in c(group,covs))if(any(vapply(split(meta[[v]],meta[[sid]]),function(x)length(unique(x))!=1,logical(1))))stop("供体内组别/协变量不一致")
cellcounts<-t(table(sample=as.character(meta[[sid]]),type=as.character(meta[[celltype]])))
samplemeta<-meta[!duplicated(meta[[sid]]),,drop=FALSE];samplemeta<-samplemeta[match(colnames(cellcounts),as.character(samplemeta[[sid]])),,drop=FALSE];design_info<-.om_design(samplemeta)
y<-edgeR::DGEList(cellcounts,lib.size=colSums(cellcounts));y<-edgeR::calcNormFactors(y,method="none")
y<-edgeR::estimateDisp(y,design_info$design,robust=TRUE);fit<-edgeR::glmQLFit(y,design_info$design,robust=TRUE);test<-edgeR::glmQLFTest(fit,coef=design_info$coef)
tab<-data.frame(cell_type=rownames(cellcounts),edgeR::topTags(test,n=Inf,sort.by="none")$table,row.names=NULL)
composition<-as.data.frame(as.table(cellcounts));names(composition)<-c("cell_type","sample","cells");composition$proportion<-composition$cells/as.numeric(colSums(cellcounts)[as.character(composition$sample)])
fig<-ggplot2::ggplot(composition,ggplot2::aes(sample,proportion,fill=cell_type))+ggplot2::geom_col()+ggplot2::theme_minimal()
.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",fig);.out<-c(.out,.om_finish("供体×细胞类型真实计数 edgeR QL；总细胞数 offset，未使用每个细胞作独立重复",paste(ncol(cellcounts),"供体；",nrow(cellcounts),"细胞类型；BH FDR。")))
'''
INPUTS["omics_scrna_differential_abundance"]=dict(_MATRIX_INPUTS,cell_type="type_v")

_BODIES["omics_spatial_transcriptomics"] = r'''
om<-.om_read();counts<-.om_counts(om$x);meta<-om$meta;coordinates<-.wc_get("coordinates_v")
if(inherits(.wc_get("object_v"),"SpatialExperiment"))coordinates_matrix<-SpatialExperiment::spatialCoords(.wc_get("object_v")) else {
 columns<-.wc_cols(coordinates);if(length(columns)!=2||!all(columns%in%names(meta)))stop("空间对象需要明确二维 coordinates列向量或 SpatialExperiment spatialCoords")
 coordinates_matrix<-as.matrix(meta[,columns,drop=FALSE])
}
if(!is.numeric(coordinates_matrix)||anyNA(coordinates_matrix)||nrow(coordinates_matrix)!=ncol(counts))stop("空间坐标与spot不一致")
region<-.wc_get("region_v");group<-.wc_get("group_v");if(is.null(group))group<-region;if(is.null(group))stop("区域差异需要明确 region/group 注释")
sample<-.wc_get("sample_v");if(is.null(sample)||!all(c(sample,group)%in%names(meta))||anyNA(meta[,c(sample,group),drop=FALSE]))stop("必须提供真实生物学样本ID与区域/分组")
keys<-interaction(as.character(meta[[sample]]),as.character(meta[[group]]),drop=TRUE)
design_columns<-.wc_cols(c(.wc_get("batch_v"),.wc_get("pair_v"),.wc_get("covariates_v")))
for(v in design_columns){
 if(!v%in%names(meta)||anyNA(meta[[v]])||any(vapply(split(meta[[v]],keys),function(z)length(unique(z))!=1,logical(1))))stop("空间pseudobulk内批次/配对/协变量必须一致，不按首spot猜设计")
}
pseudobulk<-t(rowsum(t(counts),keys,reorder=FALSE));samplemeta<-meta[match(colnames(pseudobulk),as.character(keys)),,drop=FALSE]
rownames(samplemeta)<-colnames(pseudobulk)
coverage<-table(as.character(samplemeta[[sample]]),as.character(samplemeta[[group]]))
if(any(colSums(coverage>0)<3))stop("空间差异分析每组至少需要3个独立生物学样本；同切片spot不能当供体重复")
donor_coverage<-rowSums(coverage>0)
if(any(donor_coverage==2)&&!all(donor_coverage==2))stop("当前空间设计支持完整配对或完全独立样本；部分配对不能当独立重复")
if(all(donor_coverage==2)){
 explicit_pair<-.wc_get("pair_v")
 if(!is.null(explicit_pair)){
   if(any(vapply(split(samplemeta[[explicit_pair]],samplemeta[[sample]]),function(z)length(unique(z))!=1,logical(1)))||any(vapply(split(samplemeta[[sample]],samplemeta[[explicit_pair]]),function(z)length(unique(z))!=1,logical(1))))stop("显式pair_id与真实同供体配对冲突")
 }else{samplemeta$.donor<-factor(as.character(samplemeta[[sample]]));.wc_inputs$pair_v<-".donor"}
}
design_info<-.om_design(samplemeta,group=group);dds<-DESeq2::DESeqDataSetFromMatrix(pseudobulk,design_info$data,design=design_info$formula);dds<-DESeq2::DESeq(dds,quiet=TRUE)
res<-as.data.frame(DESeq2::results(dds,contrast=c(".group",design_info$comparison,design_info$reference)));tab<-data.frame(feature=rownames(res),res,row.names=NULL)
spatial<-data.frame(x=coordinates_matrix[,1],y=coordinates_matrix[,2],region=as.character(meta[[group]]),library_size=colSums(counts))
fig<-ggplot2::ggplot(spatial,ggplot2::aes(x,y,color=region,size=library_size))+ggplot2::geom_point()+ggplot2::coord_equal()+ggplot2::theme_minimal()
.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",fig);.out<-c(.out,.om_finish("真实二维spot空间展示；样本×区域聚合整数计数DESeq2；同供体双区域加入配对固定效应",paste(ncol(counts),"spot；",nrow(coverage),"独立样本；区域",design_info$comparison,"对",design_info$reference,"；同切片spot未当供体重复。")))
'''
INPUTS["omics_spatial_transcriptomics"]=dict(_MATRIX_INPUTS,coordinates="coordinates_v",region="region_v",cell_type="type_v")
for _id in ("omics_scrna_trajectory","omics_scrna_differential_abundance","omics_spatial_transcriptomics"):
 ROLE_MAPS[_id]=dict(primary_table="primary",diagnostic_plot="figure",manuscript_results="results")

_BODIES["flow_cytometry"] = r'''
object<-.wc_get("object_v",dataset);meta<-if(inherits(object,"SummarizedExperiment"))as.data.frame(SummarizedExperiment::colData(object))else if(is.data.frame(object))object else stop("本分支需要已门控事件表或带确认门控标签的 SummarizedExperiment")
sample<-.wc_get("sample_v");type<-.wc_get("type_v");group<-.wc_get("group_v");batch<-.wc_get("batch_v")
if(!all(c(sample,type,group,batch)%in%names(meta))||anyNA(meta[,c(sample,type,group,batch),drop=FALSE]))stop("确认门控标签、样本与组别不能缺失")
for(v in c(group,batch))if(any(vapply(split(meta[[v]],meta[[sample]]),function(x)length(unique(x))!=1,logical(1))))stop("样本内组别/批次不一致")
counts<-table(sample=as.character(meta[[sample]]),type=as.character(meta[[type]]));samplemeta<-meta[!duplicated(meta[[sample]]),,drop=FALSE];samplemeta<-samplemeta[match(rownames(counts),as.character(samplemeta[[sample]])),,drop=FALSE]
design_info<-.om_design(samplemeta);proportions<-as.data.frame(counts);names(proportions)<-c("sample","cell_type","events");proportions$total<-as.numeric(rowSums(counts)[as.character(proportions$sample)]);proportions$proportion<-proportions$events/proportions$total
rows<-lapply(colnames(counts),function(t){d<-design_info$data;d$.success<-counts[,t];d$.failure<-rowSums(counts)-counts[,t];fit<-.wc_check_fit(stats::glm(stats::update(design_info$formula,cbind(.success,.failure)~.),d,family=stats::quasibinomial()));tab<-.wc_coef_table(fit,.wc_vcov(fit),terms=names(stats::coef(fit))[design_info$coef],exponentiate=TRUE);tab$cell_type<-t;tab})
tab<-do.call(rbind,rows);tab$FDR<-stats::p.adjust(tab$P值,"BH");proportions$group<-as.character(samplemeta[[group]][match(as.character(proportions$sample),as.character(samplemeta[[sample]]))])
fig<-ggplot2::ggplot(proportions,ggplot2::aes(group,proportion,color=cell_type))+ggplot2::geom_point(position=ggplot2::position_jitter(width=.1))+ggplot2::facet_wrap(~cell_type)+ggplot2::theme_minimal()
.out$proportions<-.wc_table("proportions",proportions);.out$diff<-.wc_table("diff",tab);.out$figure<-.wc_figure("figure",fig)
.out$gating<-.wc_text("gating",paste("采用已确认门控标签列",type,"；本分支不重新推断门，不从事件散点虚构亚群。"));.out<-c(.out,.om_finish("按样本聚合门控事件；quasibinomial 比例比较与HC1区间",paste(nrow(counts),"样本；",ncol(counts),"确认亚群；分母为每样本提供的总事件。")))
'''
INPUTS["flow_cytometry"]=dict(flow_object="object_v",cell_subpopulation_marker="type_v",sample_id="sample_v",group="group_v",batch="batch_v",ref_group="reference_v",comparison_group="comparison_v")
ROLE_MAPS["flow_cytometry"]=dict(text_1="gating",table_1="proportions",figure_1="figure",table_2="proportions",table_3="diff",figure_2="figure",text_2="diagnostic")

_BODIES["multiomics_integration"] = r'''
object<-.wc_get("object_v",dataset);sample<-.wc_get("sample_v");group<-.wc_get("group_v")
if(inherits(object,"MultiAssayExperiment")){
  assays<-MultiAssayExperiment::experiments(object);smap<-MultiAssayExperiment::sampleMap(object);meta<-as.data.frame(SummarizedExperiment::colData(object))
  matrices<-lapply(seq_along(assays),function(i){a<-assays[[i]];x<-if(inherits(a,"SummarizedExperiment"))as.matrix(SummarizedExperiment::assay(a))else as.matrix(a);map<-as.data.frame(smap[smap$assay==names(assays)[i],]);if(anyDuplicated(map$primary)||anyDuplicated(map$colname))stop("每组学每样本只能有一个确认测量，不自动聚合技术重复");matched<-match(colnames(x),map$colname);if(anyNA(matched))stop("原生组学轴未被sampleMap完整覆盖");colnames(x)<-as.character(map$primary[matched]);x});names(matrices)<-names(assays)
  common<-Reduce(intersect,lapply(matrices,colnames));if(length(common)<6)stop("共同样本少于6")
  matrices<-lapply(matrices,function(x)x[,common,drop=FALSE]);meta<-meta[match(common,rownames(meta)),,drop=FALSE]
}else{
  if(!is.data.frame(object)||is.null(sample)||!sample%in%names(object)||anyDuplicated(object[[sample]]))stop("表格多组学需要唯一 sample_id")
  blocks<-.wc_get("blocks_v");if(is.null(blocks)||!is.character(blocks)||length(blocks)!=1)stop("需要 assay_blocks JSON 明确每组学特征列")
  blocks<-jsonlite::fromJSON(blocks);if(length(blocks)<2||is.null(names(blocks)))stop("至少两个命名组学块")
  matrices<-lapply(blocks,function(cols){if(!all(cols%in%names(object))||!all(vapply(object[,cols,drop=FALSE],is.numeric,logical(1))))stop("组学块需数值特征列");x<-t(as.matrix(object[,cols,drop=FALSE]));colnames(x)<-as.character(object[[sample]]);x});common<-as.character(object[[sample]]);meta<-object
}
if(length(matrices)<2||any(lengths(lapply(matrices,rownames))<2))stop("每个组学至少两个特征")
if(any(vapply(matrices,function(x)anyNA(x)||any(!is.finite(x)),logical(1))))stop("组学矩阵存在缺失/非有限值；未自动插补")
if(is.null(sample)||!sample%in%names(meta)||anyNA(meta[[sample]])||anyDuplicated(meta[[sample]]))stop("明确sample_id必须在原生colData/表格中存在且唯一，与sampleMap对齐后保留")
if(is.null(group)||!group%in%names(meta)||anyNA(meta[[group]]))stop("明确phenotype_group必须存在且完整")
reported_ids<-as.character(meta[[sample]])
batch<-.wc_get("batch_v");if(!is.null(batch)){
 if(!batch%in%names(meta)||anyNA(meta[[batch]]))stop("批次列不存在或缺失")
 design_data<-data.frame(.batch=factor(meta[[batch]]),.group=factor(meta[[group]]))
 complete_design<-stats::model.matrix(~.batch+.group,design_data);if(qr(complete_design)$rank<ncol(complete_design))stop("多组学批次与表型混淆，不能独立估计")
 preserve<-stats::model.matrix(~.group,design_data)
 matrices<-lapply(matrices,function(x)limma::removeBatchEffect(x,batch=design_data$.batch,design=preserve))
}
for(i in seq_along(matrices))rownames(matrices[[i]])<-paste(names(matrices)[i],rownames(matrices[[i]]),sep="::")
combined<-t(do.call(rbind,matrices));mfa<-FactoMineR::MFA(as.data.frame(combined),group=vapply(matrices,nrow,integer(1)),type=rep("s",length(matrices)),name.group=names(matrices),ncp=2,graph=FALSE)
features<-data.frame(feature=rownames(mfa$quanti.var$coord),mfa$quanti.var$coord,row.names=NULL)
sample_table<-data.frame(sample=reported_ids,mfa$ind$coord,row.names=NULL)
alignment<-data.frame(sample=reported_ids,primary_sample=common,assays_present=length(matrices));correlations<-list()
for(i in seq_len(length(matrices)-1))for(j in (i+1):length(matrices))for(a in rownames(matrices[[i]]))for(b in rownames(matrices[[j]])){
 ct<-stats::cor.test(matrices[[i]][a,],matrices[[j]][b,]);correlations[[length(correlations)+1]]<-data.frame(from=a,to=b,correlation=as.numeric(ct$estimate),P=ct$p.value)
}
correlations<-do.call(rbind,correlations);correlations$FDR<-stats::p.adjust(correlations$P,"BH")
sample_table$group<-if(!is.null(group)&&group%in%names(meta))as.character(meta[[group]])else"all"
fig<-ggplot2::ggplot(sample_table,ggplot2::aes(.data[[names(sample_table)[2]]],.data[[names(sample_table)[3]]],color=group))+ggplot2::geom_point()+ggplot2::theme_minimal()
.out$features<-.wc_table("features",features);.out$correlations<-.wc_table("correlations",correlations);.out$alignment<-.wc_table("alignment",alignment);.out$figure<-.wc_figure("figure",fig)
.out<-c(.out,.om_finish("实际 MultiAssayExperiment sampleMap 或明确组学块对齐；FactoMineR MFA 与跨组学 Pearson/BH",paste(length(common),"共同样本；",length(matrices),"组学；联合维度为实际 MFA。不是监督预测/因果网络。")))
'''
INPUTS["multiomics_integration"]=dict(_MATRIX_INPUTS,phenotype_group="group_v",assay_blocks="blocks_v")
ROLE_MAPS["multiomics_integration"]=dict(table_1="features",table_2="correlations",manuscript_results="results",table_3="alignment",figure_1="figure",table_4="features")

_BODIES["omics_alternative_splicing"] = r'''
columns<-.wc_cols(.wc_get("features_v"));gene<-.wc_get("gene_v");feature<-.wc_get("feature_id_v")
if(!all(c(columns,gene,feature)%in%names(dataset))||is.null(feature))stop("剪接需要每行 exon/event 的 feature_id、gene_transcript_id 及明确样本计数列")
if(anyDuplicated(dataset[[feature]])||anyNA(dataset[,c(columns,gene,feature),drop=FALSE]))stop("剪接事件ID不唯一或计数/注释缺失")
counts<-as.matrix(dataset[,columns,drop=FALSE]);rownames(counts)<-as.character(dataset[[feature]]);counts<-.om_counts(counts)
metadata<-.wc_get("metadata_v");if(is.null(metadata))stop("宽格式剪接计数需要 sample_metadata JSON")
meta<-.om_annotation(metadata);sample<-.wc_get("sample_v","sample");if(!sample%in%names(meta)||anyDuplicated(meta[[sample]]))stop("样本注释ID无效")
meta<-meta[match(columns,as.character(meta[[sample]])),,drop=FALSE];if(anyNA(meta))stop("剪接计数与样本注释不能对齐")
design_info<-.om_design(meta)
y<-edgeR::calcNormFactors(edgeR::DGEList(counts,genes=data.frame(feature=rownames(counts),gene=as.character(dataset[[gene]]))))
y<-edgeR::estimateDisp(y,design_info$design,robust=TRUE);fit<-edgeR::glmQLFit(y,design_info$design,robust=TRUE)
splicing<-edgeR::diffSpliceDGE(fit,coef=design_info$coef,geneid="gene",exonid="feature",verbose=FALSE)
tab<-edgeR::topSpliceDGE(splicing,test="Simes",number=Inf)
exons<-edgeR::topSpliceDGE(splicing,test="exon",number=Inf);if(!nrow(tab))stop("没有包含多个事件的可检验基因")
fig<-ggplot2::ggplot(exons,ggplot2::aes(logFC,-log10(pmax(P.Value,.Machine$double.xmin))))+ggplot2::geom_point()+ggplot2::theme_minimal()
if(all(c("gene","P.Value","FDR")%in%names(tab))&&"gene"%in%names(exons)){
 exons$gene_Simes_P<-tab$P.Value[match(exons$gene,tab$gene)];exons$gene_FDR<-tab$FDR[match(exons$gene,tab$gene)]
}
.out$primary<-.wc_table("primary",exons);.out$figure<-.wc_figure("figure",fig);.out<-c(.out,.om_finish("edgeR 真实负二项 GLM + diffSpliceDGE 事件相对基因变化；输出exon事件表并保留Simes基因级检验",paste(nrow(counts),"事件；",nrow(tab),"基因；保留原始整数计数，未用差异表达替代差异剪接。")))
'''
INPUTS["omics_alternative_splicing"]=dict(feature_count="features_v",sample_group="group_v",gene_transcript_id="gene_v",batch="batch_v",pair_id="pair_v",event_type="event_v",feature_id="feature_id_v",sample_metadata="metadata_v",sample_id="sample_v",ref_group="reference_v",comparison_group="comparison_v")
ROLE_MAPS["omics_alternative_splicing"]=dict(primary_table="primary",diagnostic_plot="figure",manuscript_results="results")

_BASE_KEYS={"data_object","feature_columns","matrix_orientation","sample_metadata","assay_name","sample_id","sample_annotations","feature_annotations","feature_annotation_table"}
_DESIGN_KEYS={"group","group_condition","sample_group","batch","pair_id","covariates","ref_group","comparison_group"}
_EXPRESSION_IDS={"bulk_rnaseq","microarray_expression","proteomics_ms","methylation_expression_proxy","metabolomics_xcms","omics_metabolomics_pathway","omics_atac_chip_binding"}
for _id in _EXPRESSION_IDS:
    _keep=_BASE_KEYS|_DESIGN_KEYS|{"input_scale","peak_annotations","metabolite_annotations"}
    if _id in {"bulk_rnaseq","omics_atac_chip_binding"}:_keep.discard("input_scale")
    if _id=="omics_metabolomics_pathway":_keep|={"term_gene_mapping","internal_standard"}
    if _id in {"proteomics_ms","metabolomics_xcms"}:_keep|={"term_gene_mapping"}
    INPUTS[_id]={k:v for k,v in INPUTS[_id].items() if k in _keep}
for _id,_extra in {
 "rnaseq_batch_qc":{"group","batch"},
 "wgcna_network":{"sample_trait","group","batch","input_scale","soft_power","min_module_size"},
 "microbiome_amplicon":_DESIGN_KEYS|{"abundance_object","taxonomy_annotations","pseudocount"},
 "omics_microbiome_compositional":_DESIGN_KEYS|{"taxon_abundance","pseudocount","phylogeny"},
 "single_cell":{"group","cell_annotations","pca_dimensions","cluster_resolution"},
 "scrna_marker_enrichment":{"group_condition","cell_annotations","pca_dimensions","ref_group","comparison_group","term_gene_mapping"},
 "scrna_sample_integration":{"batch","group_condition","pca_dimensions"},
 "omics_scrna_trajectory":{"cell_annotations","starting_cell_population","trajectory_knots","group"},
 "omics_scrna_differential_abundance":_DESIGN_KEYS|{"cell_type"},
 "omics_spatial_transcriptomics":_DESIGN_KEYS|{"coordinates","region","cell_type"},
 "multiomics_integration":{"phenotype_group","batch","assay_blocks"},
}.items():
    INPUTS[_id]={k:v for k,v in INPUTS[_id].items() if k in _BASE_KEYS|_extra}

for _id in ("single_cell","scrna_marker_enrichment","scrna_sample_integration","omics_scrna_trajectory","omics_scrna_differential_abundance","omics_spatial_transcriptomics"):
    INPUTS[_id]["observation_id"]="observation_v"
INPUTS["multiomics_integration"]={k:v for k,v in INPUTS["multiomics_integration"].items() if k in {"data_object","sample_id","phenotype_group","batch","assay_blocks"}}

def _method_body(template_id):
    body=_BODIES[template_id]
    if template_id in _EXPRESSION_IDS:
        if template_id!="omics_metabolomics_pathway":
            body=body.split('\nif(.wc_get("template_v")=="omics_metabolomics_pathway")')[0]
        count_engine=template_id in {"bulk_rnaseq","omics_atac_chip_binding"}
        body=re.sub(r'if\(.wc_get\("engine_v"\)=="DESeq2"\) \{\n(.*?)\n\} else \{\n(.*?)\n\}\ntab',lambda m:(m.group(1) if count_engine else m.group(2))+"\ntab",body,flags=re.S)
    if template_id in {"proteomics_ms","metabolomics_xcms"}:body+='\n'+_PROTEOMICS_ENRICHMENT
    return body


def _method_helpers(template_id):
    """Emit reachable helpers so package scanning follows this method's chain."""
    raw=_COMMON+_HELPERS
    starts=list(re.finditer(r"(?m)^(\.\w+)\s*<-\s*function\(",raw))
    functions={m.group(1):raw[m.start():starts[i+1].start() if i+1<len(starts) else len(raw)] for i,m in enumerate(starts)}
    adapter=functions[".om_matrix"]
    single_cell_ids={"single_cell","scrna_marker_enrichment","scrna_sample_integration","omics_scrna_trajectory","omics_scrna_differential_abundance","omics_spatial_transcriptomics"}
    if template_id not in single_cell_ids:
        adapter=re.sub(r'  \} else if \(inherits\(object,"Seurat"\)\) \{.*?(?=  \} else if \(is.data.frame)',"",adapter,flags=re.S)
    if template_id not in {"microbiome_amplicon","omics_microbiome_compositional"}:
        adapter=re.sub(r'  \}else if\(inherits\(object,"phyloseq"\)\)\{.*?(?=  \}else if \(inherits\(object,"SummarizedExperiment")',"",adapter,flags=re.S)
    if template_id not in {"microarray_expression","proteomics_ms","omics_metabolomics_pathway"}:
        adapter=re.sub(r'  if\(inherits\(object,"ExpressionSet"\).*?\{.*?\n  \}else ',"  ",adapter,flags=re.S)
    functions[".om_matrix"]=adapter
    needed=set(re.findall(r"\.(?:wc|om)_\w+",_method_body(template_id)))
    previous=set()
    while needed!=previous:
        previous=set(needed)
        for name in tuple(needed):
            if name in functions:needed.update(re.findall(r"\.(?:wc|om)_\w+",functions[name]))
    return "\n".join(body for name,body in functions.items() if name in needed)


# Per-method semantic figures use actual fitted objects; unrelated methods retain their dependencies.

_BODIES['wgcna_network'] = _BODIES['wgcna_network'].replace(r'''fig<-ggplot2::ggplot(module_cor,ggplot2::aes(module,correlation,fill=correlation))+ggplot2::geom_col()+ggplot2::theme_minimal()''', r'''fig<-ggplot2::ggplot(module_cor,ggplot2::aes(module,correlation,fill=correlation))+ggplot2::geom_col()+ggplot2::theme_minimal()+ggplot2::labs(title="实际模块与性状相关",y="Pearson相关")
module_trait_heat<-data.frame(module=module_cor$module,trait=rep(as.character(trait),nrow(module_cor)),correlation=module_cor$correlation,P=module_cor$P,FDR=module_cor$FDR)
module_trait_heat$label<-sprintf("r=%.2f\nP=%.2g",module_trait_heat$correlation,module_trait_heat$P)
heatmap_fig<-ggplot2::ggplot(module_trait_heat,ggplot2::aes(trait,module,fill=correlation))+ggplot2::geom_tile()+ggplot2::geom_text(ggplot2::aes(label=label))+ggplot2::scale_fill_gradient2(low="#2166AC",mid="white",high="#B2182B",midpoint=0,limits=c(-1,1))+ggplot2::theme_minimal()+ggplot2::labs(title="实际模块×性状关联热图",x="已确认数值性状",y="WGCNA模块",fill="Pearson r")
.out$heatmap<-.wc_figure("heatmap",heatmap_fig)''')

_BODIES['genomic_ranges'] = _BODIES['genomic_ranges'].replace(r'''fig<-ggplot2::ggplot(annotation,ggplot2::aes(chromosome,width))+ggplot2::geom_boxplot()+ggplot2::theme_minimal()''', r'''interval_track<-annotation
interval_track$track<-ave(interval_track$region,interval_track$chromosome,FUN=seq_along)
fig<-ggplot2::ggplot(interval_track,ggplot2::aes(x=start,xend=end,y=track,yend=track,color=chromosome))+ggplot2::geom_segment(linewidth=1.8)+ggplot2::facet_wrap(~chromosome,scales="free_x")+ggplot2::theme_minimal()+ggplot2::labs(title="实际基因组区间轨迹",x="1-based闭区间基因组坐标",y="确认输入区间",color="染色体")''')

_BODIES['omics_atac_chip_binding'] = _BODIES['omics_atac_chip_binding'].replace(r'''.out$volcano<-.wc_figure("volcano",.om_volcano(tab));.out$heatmap<-.wc_figure("heatmap",.om_heat(normalized));.out$pca_fig<-.wc_figure("pca_fig",pca$figure)''', r'''.out$volcano<-.wc_figure("volcano",patchwork::wrap_plots(.om_heat(normalized)+ggplot2::labs(title="DESeq2标准化信号热图"),pca$figure+ggplot2::labs(title="实际标准化信号PCA"),.om_volcano(tab)+ggplot2::labs(title="实际差异结合火山图"),ncol=3));.out$heatmap<-.wc_figure("heatmap",.om_heat(normalized));.out$pca_fig<-.wc_figure("pca_fig",pca$figure)''')

_BODIES['omics_metabolomics_pathway'] = _BODIES['omics_metabolomics_pathway'].replace(r'''  .out$diff<-.wc_table("diff",pathway_table);.out$volcano<-.wc_figure("volcano",.om_enrich_plot(enrichment$table))''', r'''  metabolite_diagnostic<-patchwork::wrap_plots(pca$figure+ggplot2::labs(title="实际代谢特征PCA"),.om_volcano(tab)+ggplot2::labs(title="实际差异代谢物火山图"),.om_enrich_plot(enrichment$table)+ggplot2::labs(title="确认映射的实际通路富集"),ncol=3)
  .out$diff<-.wc_table("diff",pathway_table);.out$volcano<-.wc_figure("volcano",metabolite_diagnostic)''')

_BODIES['omics_scrna_trajectory'] = _BODIES['omics_scrna_trajectory'].replace(r'''fig<-fig+ggplot2::geom_path(data=curve,ggplot2::aes(PC1,PC2,group=lineage),inherit.aes=FALSE,color="black")''', r'''fig<-fig+ggplot2::geom_path(data=curve,ggplot2::aes(PC1,PC2,group=lineage),inherit.aes=FALSE,color="black")+ggplot2::labs(title="slingshot实际谱系与伪时序")
smooth_candidates<-tab[is.finite(tab$pvalue)&tab$feature%in%rownames(fit),,drop=FALSE]
if(!nrow(smooth_candidates))stop("tradeSeq无具有有限检验P值的可展示基因，不能虚构表达趋势")
smooth_gene<-as.character(smooth_candidates$feature[order(smooth_candidates$pvalue)][1])
smooth_prediction<-tradeSeq::predictSmooth(fit,gene=smooth_gene,nPoints=50,tidy=TRUE)
if(!is.data.frame(smooth_prediction)||!all(c("lineage","time","gene","yhat")%in%names(smooth_prediction))||!nrow(smooth_prediction)||any(!is.finite(smooth_prediction$time))||any(!is.finite(smooth_prediction$yhat)))stop("tradeSeq原生平滑预测没有产生完整可绘制曲线")
smooth_fig<-ggplot2::ggplot(smooth_prediction,ggplot2::aes(time,yhat,color=factor(lineage),group=lineage))+ggplot2::geom_line()+ggplot2::theme_minimal()+ggplot2::labs(title=paste("tradeSeq实际表达趋势：",smooth_gene),subtitle="模型平滑预测；调整项及文库offset采用模型参考值",x="slingshot伪时序",y="tradeSeq预测表达",color="真实谱系")
tab$lineage_count<-ncol(pseudotime);tab$starting_cluster<-as.character(start);tab$smoother_displayed<-tab$feature==smooth_gene
fig<-patchwork::wrap_plots(fig,smooth_fig,ncol=2)''')

_BODIES['omics_spatial_transcriptomics'] = _BODIES['omics_spatial_transcriptomics'].replace(r'''fig<-ggplot2::ggplot(spatial,ggplot2::aes(x,y,color=region,size=library_size))+ggplot2::geom_point()+ggplot2::coord_equal()+ggplot2::theme_minimal()''', r'''spatial$sample<-as.character(meta[[sample]])
region_fig<-ggplot2::ggplot(spatial,ggplot2::aes(x,y,color=region,size=library_size))+ggplot2::geom_point()+ggplot2::coord_equal()+ggplot2::facet_wrap(~sample)+ggplot2::theme_minimal()+ggplot2::labs(title="实际样本与区域空间地图")
spatial_candidates<-tab[is.finite(tab$padj)&tab$feature%in%rownames(counts),,drop=FALSE]
if(!nrow(spatial_candidates))stop("空间差异没有具有有限调整P值的可展示基因，不能虚构差异表达地图")
spatial_gene<-as.character(spatial_candidates$feature[order(spatial_candidates$padj,spatial_candidates$pvalue)][1])
spatial$normalized_expression<-log1p(as.numeric(counts[spatial_gene,])/colSums(counts)*10000)
expression_fig<-ggplot2::ggplot(spatial,ggplot2::aes(x,y,color=normalized_expression))+ggplot2::geom_point()+ggplot2::coord_equal()+ggplot2::facet_wrap(~sample)+ggplot2::scale_color_gradient(low="#F7FBFF",high="#08306B")+ggplot2::theme_minimal()+ggplot2::labs(title=paste("实际空间表达：",spatial_gene),subtitle="每spot原始计数按文库标准化；差异推断仍来自样本×区域pseudobulk",color="log1p(每万计数)")
spatial_plots<-list(region_fig,expression_fig)
celltype<-.wc_get("type_v")
if(!is.null(celltype)){
 if(length(celltype)!=1||!celltype%in%names(meta))stop("确认cell_type注释字段不存在")
 spatial$cell_type<-as.character(meta[[celltype]])
 tab$selected_cell_types<-paste(unique(spatial$cell_type[!is.na(spatial$cell_type)]),collapse=";");tab$missing_cell_type_annotations<-sum(is.na(spatial$cell_type))
 celltype_fig<-ggplot2::ggplot(spatial,ggplot2::aes(x,y,color=cell_type))+ggplot2::geom_point()+ggplot2::coord_equal()+ggplot2::facet_wrap(~sample)+ggplot2::theme_minimal()+ggplot2::labs(title="已确认细胞类型空间注释",color="确认细胞类型")
 spatial_plots[[3]]<-celltype_fig
}
tab$spatial_display_gene<-spatial_gene
fig<-patchwork::wrap_plots(spatial_plots,ncol=2)''')
ROLE_MAPS['wgcna_network'].update({'figure_1': 'heatmap', 'figure_2': 'figure', 'table_2': 'hubs'})

_BODIES['omics_microbiome_compositional'] = _BODIES['omics_microbiome_compositional'].replace(
    r'''.out$alpha<-.wc_table("alpha",alpha);.out$pcoa<-.wc_table("pcoa",ordination);.out$nmds<-.wc_table("nmds",nmds_table);.out$diff<-.wc_table("diff",differential);.out$figure<-.wc_figure("figure",fig);.out$volcano<-.wc_figure("volcano",.om_volcano(differential))''',
    r'''sample_diversity<-alpha;sample_diversity$row_type<-"sample_diversity";sample_diversity$group<-as.character(design_info$data$.group)
taxon_differential<-differential;taxon_differential$row_type<-"taxon_CLR_differential"
union_columns<-unique(c("row_type","sample","feature","group",names(sample_diversity),names(taxon_differential)))
for(nm in setdiff(union_columns,names(sample_diversity)))sample_diversity[[nm]]<-NA
for(nm in setdiff(union_columns,names(taxon_differential)))taxon_differential[[nm]]<-NA
compositional_table<-rbind(sample_diversity[,union_columns,drop=FALSE],taxon_differential[,union_columns,drop=FALSE])
top_taxa<-colnames(relative)[order(colMeans(relative),decreasing=TRUE)[seq_len(min(12,ncol(relative)))]]
display_relative<-relative[,top_taxa,drop=FALSE]
other_taxa<-setdiff(colnames(relative),top_taxa)
if(length(other_taxa)){
 other_label<-"Other (remaining taxa)";while(other_label%in%top_taxa)other_label<-paste0(other_label,"_")
 display_relative<-cbind(display_relative,rowSums(relative[,other_taxa,drop=FALSE]));colnames(display_relative)[ncol(display_relative)]<-other_label
}
composition_table<-as.data.frame(as.table(display_relative),stringsAsFactors=FALSE);names(composition_table)<-c("sample","feature","relative_abundance")
composition_table$group<-as.character(design_info$data$.group[match(composition_table$sample,rownames(relative))])
composition_fig<-ggplot2::ggplot(composition_table,ggplot2::aes(sample,relative_abundance,fill=feature))+ggplot2::geom_col()+ggplot2::facet_grid(~group,scales="free_x",space="free_x")+ggplot2::theme_minimal()+ggplot2::theme(axis.text.x=ggplot2::element_text(angle=90,hjust=1))+ggplot2::labs(title="实际菌群相对组成",subtitle="平均相对丰度前12类；其余真实丰度合并为Other",x="真实样本",y="相对丰度",fill="已确认特征")
ordination_fig<-fig+ggplot2::labs(title="实际Bray-Curtis PCoA排序")
effect_fig<-.om_volcano(differential)+ggplot2::labs(title="实际CLR-limma相对组成效应",x="调整后CLR logFC")
compositional_fig<-patchwork::wrap_plots(composition_fig,ordination_fig,effect_fig,ncol=3)
.out$alpha<-.wc_table("alpha",alpha);.out$pcoa<-.wc_table("pcoa",ordination);.out$nmds<-.wc_table("nmds",nmds_table);.out$diff<-.wc_table("diff",compositional_table);.out$figure<-.wc_figure("figure",compositional_fig);.out$volcano<-.wc_figure("volcano",effect_fig)''',
)
REFERENCE_CASES['omics_microbiome_compositional']='真实vegan多样性与CLR-limma；primary带row_type交付样本多样性/特征差异；真实相对组成、Bray PCoA和CLR效应组合图，全角色验收。'

_BODIES['omics_eqtl'] = _BODIES['omics_eqtl'].replace(
    r'''.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",fig)''',
    r'''effect_display<-data.frame(pair=paste(tab$variant_id,tab$gene_id,sep=" / "),effect=tab$effect,CI_lower=tab$CI_lower,CI_upper=tab$CI_upper,cis_trans=tab$cis_trans)
effect_fig<-ggplot2::ggplot(effect_display,ggplot2::aes(pair,effect,ymin=CI_lower,ymax=CI_upper,color=cis_trans))+ggplot2::geom_pointrange()+ggplot2::geom_hline(yintercept=0,linetype="dashed")+ggplot2::coord_flip()+ggplot2::theme_minimal()+ggplot2::labs(title="实际eQTL调整效应与HC1区间",x="确认variant / gene配对",y="每等位基因剂量的调整表达效应",color="确认位置分类")
fig<-patchwork::wrap_plots(effect_fig,fig,ncol=2)
.out$primary<-.wc_table("primary",tab);.out$figure<-.wc_figure("figure",fig)''',
)
REFERENCE_CASES['omics_eqtl']='单对兼容与真实CSV多对lm/HC1；明确cis/trans、全族BH；真实HC1效应区间和QQ组合，单对说明QQ不可评估；注释缺失保留样本，冲突拒绝。'

CORES = {template_id:_frozen_body(template_id) for template_id in _BODIES}
