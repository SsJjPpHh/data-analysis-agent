# -*- coding: utf-8 -*-
"""Research R package capability catalog for prompt construction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from textwrap import dedent

@dataclass(frozen=True)
class RCapability:
    name: str
    triggers: str
    packages: str
    outputs: str


CAPABILITIES: tuple[RCapability, ...] = (
    RCapability(
        name="Data import, cleaning, profiling",
        triggers="data upload, variable dictionary, missingness, outliers, data quality, type detection, descriptive overview",
        packages="readr, readxl, haven, arrow, vroom, data.table, openxlsx, rio, janitor, skimr, summarytools, naniar, visdat, DataExplorer, pointblank, validate, assertr",
        outputs="dataset profile, variable table, missingness table/plot, quality warnings, cleaned analysis-ready data frame",
    ),
    RCapability(
        name="Publication tables and reporting",
        triggers="Table 1, baseline characteristics, three-line table, manuscript table, subgroup table, regression table, formatted report",
        packages="gtsummary, tableone, compareGroups, arsenal, finalfit, gt, flextable, huxtable, kableExtra, modelsummary, broom, broom.mixed, parameters, marginaleffects, emmeans",
        outputs="baa_table tables, formatted summaries, effect estimates, CI, p values, report-ready interpretation",
    ),
    RCapability(
        name="Classical statistics and mixed models",
        triggers="t test, Wilcoxon, ANOVA, chi-square, Fisher, correlation, nonparametric, linear/logistic/ordinal/Poisson/negative binomial, mixed model, repeated measures",
        packages="stats, MASS, nlme, lme4, glmmTMB, mgcv, nnet, VGAM, pscl, ordinal, brant, quantreg, robustbase, boot, coin, exact2x2, DescTools, rstatix, PMCMRplus, car, multcomp",
        outputs="method selection rationale, assumption checks, model summaries, post-hoc comparisons, effect sizes",
    ),
    RCapability(
        name="Bayesian and probabilistic modeling",
        triggers="Bayesian, posterior, prior, MCMC, Stan, INLA, hierarchical Bayes, Bayesian regression, Bayesian meta-analysis",
        packages="brms, rstan, rstanarm, cmdstanr, INLA, bayesplot, posterior, loo, tidybayes, bayesmeta",
        outputs="posterior summaries, credible intervals, convergence diagnostics, trace plots, posterior predictive checks",
    ),
    RCapability(
        name="Survival, competing risks, prognosis",
        triggers="survival, Kaplan-Meier, Cox, hazard ratio, competing risk, Fine-Gray, time-dependent ROC, calibration, nomogram, decision curve",
        packages="survival, survminer, coxme, mstate, survRM2, cmprsk, riskRegression, prodlim, pec, timeROC, survivalROC, survAUC, rms, DynNom, regplot, rmda",
        outputs="KM plot, HR table, competing-risk table, calibration/ROC/DCA plots, prognostic interpretation",
    ),
    RCapability(
        name="Clinical prediction and diagnostic performance",
        triggers="ROC, AUC, sensitivity, specificity, calibration, confusion matrix, cut-off, diagnostic model, prediction model, clinical utility",
        packages="pROC, PRROC, precrec, caret, yardstick, ResourceSelection, rms, riskRegression, rmda, performance, effectsize",
        outputs="AUC/ROC plots, threshold table, calibration summary, diagnostic metrics, clinical interpretation",
    ),
    RCapability(
        name="Missing data and imputation",
        triggers="missing values, missing pattern, multiple imputation, sensitivity analysis, complete-case comparison",
        packages="mice, miceadds, missForest, missMDA, Amelia, VIM, naniar, visdat",
        outputs="missingness diagnostics, imputation plan, imputed-model pooling, sensitivity notes",
    ),
    RCapability(
        name="Epidemiology and public health",
        triggers="incidence, prevalence, odds ratio, risk ratio, rate ratio, outbreak, epidemic curve, R0, surveillance, distributed lag, environmental exposure",
        packages="Epi, epiR, epitools, epitrix, incidence, incidence2, outbreaks, epicontacts, EpiEstim, surveillance, dlnm, tsModel, segmented, popEpi, apyramid, R0",
        outputs="epi measures, epidemic curves, surveillance plots, distributed lag curves, public-health interpretation",
    ),
    RCapability(
        name="Causal inference and real-world evidence",
        triggers="causal effect, treatment effect, propensity score, matching, weighting, IPW, balance, mediation, DAG, target trial, observational study, RWE",
        packages="MatchIt, WeightIt, cobalt, twang, ipw, mediation, medflex, dagitty, fixest, estimatr, AER, sandwich, lmtest, clubSandwich, margins, geepack, Matching, optmatch, cem, CBPS, AIPW, SuperLearner, tmle, grf, DoubleML",
        outputs="DAG assumptions, balance table/love plot, matched/weighted effect estimates, robust SE, sensitivity caveats",
    ),
    RCapability(
        name="Survey-weighted analysis",
        triggers="survey, sampling weight, complex sampling, stratification, cluster, NHANES-like data, population-weighted estimate",
        packages="survey, srvyr, weights, anesrake, Hmisc",
        outputs="weighted descriptive tables, survey GLM, design-aware CI, population-level interpretation",
    ),
    RCapability(
        name="Clinical trials, power, meta-analysis",
        triggers="clinical trial, sample size, power, non-inferiority, equivalence, interim analysis, meta-analysis, forest plot, network meta-analysis",
        packages="rpact, gsDesign, TrialSize, PowerTOST, clinfun, meta, metafor, netmeta, mada, metasens, bayesmeta, forestplot",
        outputs="sample-size/power table, trial design summary, pooled effect forest plot, heterogeneity and bias notes",
    ),
    RCapability(
        name="Psychology, questionnaires, social science",
        triggers="questionnaire, scale, reliability, Cronbach, factor analysis, CFA, SEM, IRT, latent class, Likert, mediation/moderation, social science",
        packages="psych, lavaan, semTools, mirt, ltm, TAM, GPArotation, polycor, irr, irrCAC, ICC, FactoMineR, factoextra, poLCA, likert, sjPlot, sjmisc, sjstats, jtools, interactions",
        outputs="reliability table, factor loadings, SEM/IRT model fit, Likert plot, moderation/mediation interpretation",
    ),
    RCapability(
        name="Machine learning and explainable prediction",
        triggers="prediction, classification, regression ML, feature importance, cross-validation, tuning, SHAP, partial dependence, model comparison",
        packages="tidymodels, parsnip, recipes, workflows, yardstick, tune, rsample, workflowsets, finetune, probably, stacks, embed, themis, bonsai, caret, mlr3, mlr3learners, mlr3tuning, xgboost, ranger, randomForest, gbm, C50, Cubist, e1071, glmnet, kernlab, LiblineaR, naivebayes, klaR, partykit, earth, pls, vip, pdp, iml, DALEX, shapviz, fastshap, h2o, tensorflow, keras3, keras, torch, sparklyr",
        outputs="resampling metrics, ROC/PR curves, calibration, variable importance, explainability plots, overfitting caveats",
    ),
    RCapability(
        name="Time series, longitudinal trends, signal processing",
        triggers="time series, forecast, seasonality, trend, change point, interrupted time series, signal, waveform, frequency, audio, sensor, ECG-like tabular signal",
        packages="forecast, fable, fabletools, tsibble, feasts, tseries, urca, vars, changepoint, strucchange, zoo, xts, slider, signal, pracma, wavelets, WaveletComp, seewave, tuneR, dtw, TTR",
        outputs="trend decomposition, forecasts, change-point table, signal features, frequency/wavelet plots",
    ),
    RCapability(
        name="Spatial, environmental, geographic analysis",
        triggers="map, coordinates, spatial, GIS, raster, shapefile, environmental exposure, Moran, cluster map, spatial autocorrelation",
        packages="sf, sp, terra, raster, stars, spdep, tmap, leaflet, geosphere, exactextractr, spatstat.geom, spatstat.explore, spatstat.model, mapview, osmdata",
        outputs="maps, spatial joins, exposure extraction, autocorrelation statistics, spatial model notes",
    ),
    RCapability(
        name="Text mining and network analysis",
        triggers="text, corpus, keyword, sentiment, topic model, word frequency, document clustering, social network, graph, centrality, community",
        packages="quanteda, quanteda.textplots, quanteda.textstats, tidytext, stm, topicmodels, text2vec, SnowballC, tm, sentimentr, textclean, udpipe, widyr, igraph, tidygraph, ggraph, sna, network, statnet.common, ergm, RSiena",
        outputs="term tables, topic summaries, sentiment plots, network metrics, graph visualization",
    ),
    RCapability(
        name="Bulk omics and differential expression",
        triggers="RNA-seq, transcriptomics, counts matrix, differential expression, volcano plot, heatmap, batch correction, gene expression",
        packages="DESeq2, edgeR, limma, tximport, tximeta, apeglm, ashr, sva, RUVSeq, EDASeq, cqn, NOISeq, Glimma, iSEE, variancePartition, BiocSingular, recount3, GEOquery, biomaRt, pheatmap, ComplexHeatmap, EnhancedVolcano, PCAtools",
        outputs="DE tables, normalized expression, PCA, volcano plot, heatmap, batch-effect notes, gene-level interpretation",
    ),
    RCapability(
        name="Pathway, enrichment, annotation",
        triggers="GO, KEGG, Reactome, GSEA, pathway, enrichment, gene set, annotation, ID conversion",
        packages="clusterProfiler, ReactomePA, enrichplot, DOSE, GOSemSim, GSEABase, GSVA, fgsea, pathview, graphite, gage, qusage, BiocSet, AnnotationDbi, org.Hs.eg.db, org.Mm.eg.db, GO.db, KEGGREST, biomaRt, msigdbr",
        outputs="enrichment tables, dotplots, GSEA plots, pathway diagrams, annotated gene lists",
    ),
    RCapability(
        name="Single-cell and cell annotation",
        triggers="single-cell, scRNA-seq, cell type, clustering, UMAP, marker genes, pseudotime, trajectory, batch correction",
        packages="Seurat, SeuratObject, sctransform, SingleCellExperiment, scater, scran, scuttle, DropletUtils, MAST, batchelor, glmGamPoi, SingleR, celldex, AUCell, slingshot, tradeSeq, Nebulosa, dittoSeq, bluster, scrapper, uwot, Rtsne, ArchR, SCENIC, monocle3, CellChat, Signac, BayesSpace, MOFA2",
        outputs="QC metrics, clustering/UMAP, marker tables, cell labels, pseudotime summaries for moderate workloads",
    ),
    RCapability(
        name="Genomics, variants, ChIP-seq, epigenomics",
        triggers="genomic ranges, BED/GTF/BAM/VCF, variants, ChIP-seq, peaks, motif, methylation, epigenomics",
        packages="GenomicRanges, IRanges, GenomeInfoDb, Rsamtools, GenomicAlignments, rtracklayer, GenomicFeatures, VariantAnnotation, VariantTools, BSgenome, ChIPseeker, DiffBind, csaw, motifmatchr, TFBSTools, minfi, missMethyl, DMRcate, methylKit, sesame",
        outputs="range/variant tables, annotation summaries, peak reports, methylation differential tables, genomic plots",
    ),
    RCapability(
        name="Microbiome, proteomics, multi-omics, medical imaging",
        triggers="microbiome, 16S, alpha diversity, beta diversity, proteomics, mass spectrometry, multi-omics, NIfTI, medical image file",
        packages="phyloseq, mia, microbiome, MSnbase, QFeatures, DEP, mixOmics, SpatialExperiment, EBImage, oro.nifti, RNifti, neurobase, imager, magick",
        outputs="diversity tables/plots, ordination, differential abundance/protein tables, multi-omics summaries, lightweight image summaries",
    ),
)


def _split_packages(packages: str) -> tuple[str, ...]:
    return tuple(pkg.strip() for pkg in packages.split(",") if pkg.strip())


def capability_to_dict(item: RCapability) -> dict[str, object]:
    packages = list(_split_packages(item.packages))
    return {
        "name": item.name,
        "triggers": item.triggers,
        "packages": packages,
        "outputs": item.outputs,
    }


def search_capabilities(query: str = "", limit: int = 20) -> list[dict[str, object]]:
    needle = (query or "").strip().lower()
    terms = list(dict.fromkeys(term for term in re.split(r"[\s,;/|]+", needle) if len(term.strip()) >= 2))
    ranked: list[tuple[int, int, RCapability, list[str]]] = []
    for index, item in enumerate(CAPABILITIES):
        haystack = " ".join(
            (
                item.name,
                item.triggers,
                item.packages,
                item.outputs,
            )
        ).lower()
        tokens = set(re.findall(r"[a-z0-9_.]+", haystack))
        score = 0
        hits: list[str] = []
        for term in terms:
            if len(term) <= 3:
                matched = any(token == term or token.startswith(term) for token in tokens)
            else:
                matched = term in haystack
            if not matched:
                continue
            hits.append(term)
            if term in tokens:
                score += 4
            elif any(token.startswith(term) for token in tokens):
                score += 3
            else:
                score += 1
        if not terms:
            score = 1
        if score > 0:
            ranked.append((score, index, item, hits))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    matches: list[dict[str, object]] = []
    for score, _, item, hits in ranked[: max(1, int(limit))]:
        entry = capability_to_dict(item)
        entry["match_score"] = score
        entry["matched_terms"] = hits
        matches.append(entry)
    return matches


def build_r_capability_prompt() -> str:
    rows = "\n".join(
        (f"- {item.name}: triggers [{item.triggers}]; outputs [{item.outputs}].") for item in CAPABILITIES
    )
    return dedent(
        f"""
        R package capability routing:
        The user's local R installation exposes a broad research R ecosystem. Do not wait for
        the user to name a package. Infer the statistical/disciplinary intent
        from their words, the dataset structure, and variable roles, then select
        the most suitable package family below. These entries are routing
        examples, not limits: if a relevant installed package is not named here,
        you may still use it when appropriate.

        {rows}

        Package-use protocol:
        - For every specialized capability, call get_r_package_inventory with
          the analysis intent to retrieve the live installed package candidates
          before writing code. The capability rows describe when to route, not
          a guaranteed package list.
        - Before using a non-base package in R, check availability with
          requireNamespace("pkg", quietly = TRUE). If the first-choice package
          is unavailable, use a reasonable alternative from the same capability
          family and mention the fallback in baa_text().
        - Select the method and compute stack required by the scientific question.
          The application does not disable long-running, Bayesian, deep-learning,
          distributed, or large single-cell packages; execution capacity follows
          the user's machine and installed external components.
        - Combine packages when needed: for example, use mice + gtsummary +
          broom for imputed clinical models, MatchIt + cobalt + survey for
          weighted causal analysis, or DESeq2 + ggplot2/ggrepel +
          clusterProfiler for RNA-seq interpretation.
        - Generate analysis artifacts, not only prose: use baa_table() for
          tables, baa_plot() for ggplot/plot objects, baa_text() for narrative
          notes, and baa_model_summary() for model objects.
        - If a selected package needs an external compiler, Java, CUDA, CmdStan,
          a system library, or a database/runtime service, state the concrete
          prerequisite. Once installed on the user's machine, use it normally.
        """
    ).strip()
