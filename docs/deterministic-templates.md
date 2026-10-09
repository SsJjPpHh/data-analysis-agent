# 固定脚本模板清单（213 个）

8 个分类的 213 个用户模板接入按模板 ID 注册的固定 R 实现，并有对应参考用例。自由分析由模型按冻结方案规划和生成代码。

实现登记、参考用例、实际产物合同和具体研究判断分别记录。`complete=true` 表示冻结方案的必需产物合同完成；单项诊断保留 passed / warning / failed / not_assessed，汇总 `diagnostic_quality` 还可能为 incomplete，不将未计算的检查标成通过。`scientific_review_status` 记录具体研究结论的复核状态。

下表说明各模板的实现和参考测试范围，不是每位用户安装后自动获得的验收结论。方法测试源码位于 `tests/science/`，本机 R 执行测试位于 `tests/runtime/`，完整 R 参考检查的运行方式见 [开发与验证](development.md)。标题中包含的其他算法、任意数据规模和全部研究设计不能由单个参考用例推定覆盖。

## 分类覆盖

| 分类 | 固定实现 / 目录 | 参考用例登记 |
| --- | ---: | ---: |
| 基础统计 | 24 / 24 | 24 |
| 临床研究 | 27 / 27 | 27 |
| 公共卫生 | 25 / 25 | 25 |
| 临床试验与证据合成 | 29 / 29 | 29 |
| 行为与社会科学 | 27 / 27 | 27 |
| 机器学习 | 24 / 24 | 24 |
| 时间空间与文本 | 28 / 28 | 28 |
| 组学与生物信息 | 29 / 29 | 29 |

## 逐项实现范围

### 基础统计

| 模板 | 已验证范围与参考用例 |
| --- | --- |
| `data_quality`（数据质量与变量字典） | 真实 R 核查缺失比例、样本ID重复、逐行缺失模式；保留确认的数值类型。 |
| `publication_table`（论文 Table 1 / 三线表） | 已选连续变量不被低基数自动重分类；缺失与总体/分组描述参考用例。 |
| `distribution_visual`（分布探索与科研可视化） | 真实 R：连续变量分组密度、直方图、箱线图和有效/缺失数量。 |
| `group_tests`（组间比较与非参数检验） | Student/Welch 参数与报告方法一致；Pearson 无连续性校正、Cramér V 和预期频数稀疏检查。 |
| `correlation_agreement`（相关性与一致性分析） | 真实 R：Pearson/Spearman/Kendall；ICC(A,1) 与 Bland–Altman 界限；分类 Kappa；保留数值低基数角色。 |
| `regression_glm`（线性与广义线性模型） | 连续终点系数的独立闭式参考与小样本 t 区间；0/1 二分类 OR 的解析参考；秩不足拒绝。 |
| `categorical_ordinal_models`（多分类 / 有序 / 计数结局） | 真实 R：multinom/polr/Poisson/NB 分支与 log 暴露时间偏移；连续暴露不分箱；未确认有序等级拒绝。 |
| `mixed_repeated`（混合模型与重复测量） | 重复测量随机截距混合模型与交换型工作相关 GEE 的参考拟合。 |
| `bayesian_robust_stats`（贝叶斯与稳健统计） | 真实 R：Huber 系数参考值、MCMC 正规先验后验抽样及 Chib Bayes 因子。 |
| `data_import_workflow`（多格式数据导入与字段核对） | 真实 R 检查选定字段映射与导入后类型，不自动重编码。 |
| `data_cleaning_recoding`（数据清洗与变量重编码） | 显式JSON清洗规则的修改回执、原始输入保留与行数检查；拒绝丢失有效取值的数值转换。 |
| `missing_visual_diagnostics`（缺失可视化与机制判断） | 只对选定字段计算缺失率、共缺失计数矩阵与行级模式。 |
| `robust_outlier_analysis`（离群值与稳健统计） | 真实 R：同样本 OLS 与 Huber rlm 系数对比；含高影响点合成数据的参考拟合。 |
| `assumption_diagnostics`（统计假设与模型诊断） | 真实 R：同一 OLS 模型的数值系数及残差假设诊断。 |
| `effect_size_precision`（效应量、精度与可解释差异） | 真实 R：Welch 原尺度差值、完整配对差值、低基数数值保持连续；零单元格 Fisher 条件精确 OR/CI。 |
| `multiple_testing_endpoints`（多终点与多重检验控制） | 真实 R：同族连续终点 Welch 检验及 Holm/BH 校正。 |
| `automated_eda_report`（自动化 EDA 概览） | 真实 R 生成字段风险表与实际缺失图，图表索引对应真实产物。 |
| `subgroup_sensitivity_basic`（亚组与敏感性分析框架） | 真实 R：连续结局联合交互检验、调整/未调整同一样本对比；效应与残差自由度 t 区间。 |
| `paired_matched_tests`（配对与匹配样本比较） | 真实 R：对象对齐的配对检验和实际均值差。 |
| `ancova_adjusted_groups`（ANCOVA 与调整后组间比较） | 真实 R：三组 emmeans 调整均值、全部成对 Tukey 对比、只含组别×协变量的斜率联合检验。 |
| `stratified_categorical_trend`（分层分类资料与趋势检验） | 真实 R：实际频数加权分层 CMH 共同优势比。 |
| `bootstrap_permutation_inference`（Bootstrap 与置换推断） | 真实 R：单总体、独立两组、完整配对、聚类采样单位；无分组不造组别；置换 P 加一与不完整配对拒绝。 |
| `nonlinear_spline_gam`（非线性、样条与 GAM 回归） | 真实 R：mgcv Gaussian REML 平滑模型、edf 和条件曲线。 |
| `multiple_imputation_pooling`（通用多重插补与 Rubin 合并） | MICE 随机插补与线性模型 Rubin 方差合并；MAR 与收敛需人工复核。 |

### 临床研究

| 模板 | 已验证范围与参考用例 |
| --- | --- |
| `survival_km_cox`（生存分析与 Cox 回归） | 0/1 右删失数据的 KM 风险集与生存概率；可选协变量缺省；错误事件编码拒绝。 |
| `extended_survival`（扩展生存模型） | 真实 R：时间交互 Cox 的 survival 系数参考；计数过程缺少区间起始字段时拒绝。 |
| `competing_risk`（竞争风险分析） | 真实 R：cmprsk crr 的 Fine–Gray 系数参考；CSV 生产链验证分组 CIF/Gray、独立原因别 Cox 及未调整 CIF，未提供分组/协变量时不生成占位模型。 |
| `prognostic_model`（临床预后模型） | 固定训练/验证标记的 Logistic 与 Cox 系数/风险参考；翻转验证结局不影响模型；真实 DCA 净获益手算、指定时间 IPCW AUC/Brier 与删失权重。 |
| `time_dependent_roc`（时间依赖 ROC） | 真实 R：timeROC 边际 IPCW AUC 与早期删失；无删失的概率 Brier 与均方误差参考。 |
| `diagnostic_roc`（诊断性能 / ROC） | 重复评分阈值与类别不平衡 PR 精确率；阳性类别和方向需确认。 |
| `calibration_dca`（校准曲线与决策曲线） | 真实冻结输入下核对 Brier、logit 校准斜率及决策阈值。 |
| `missing_imputation`（缺失值与多重插补） | MICE 随机插补与线性模型 Rubin 方差合并；MAR 与收敛需人工复核。 |
| `propensity_clinical`（临床队列倾向评分） | ATE/ATT/overlap 权重、外部基础数值权重、连续和类别水平平衡、处理方向与显式截断；重新拟合倾向模型 bootstrap。 |
| `matched_case_control`（匹配病例对照研究） | 60 个完整匹配组条件 logistic 系数与原生 clogit 相等。 |
| `longitudinal_clinical_outcome`（临床纵向结局模型） | 实际 lmer 随机截距及时间与组别交互，输出真实边际均值。 |
| `biomarker_trajectory`（生物标志物轨迹分析） | 实际 mgcv 平滑时间和受试者随机效应。 |
| `clinical_cutpoint_maxstat`（临床阈值与截点探索） | 二分类候选 Youden 与阳性方向手算、最大统计量标签置换 P；生存 maxstat LogRank + Lau94 搜索调整 P 参考一致。 |
| `external_validation_model`（预测模型外部验证） | 锁定概率的整体、来源与亚组外部验证，真实输出各组 AUC。 |
| `nri_idi_reclassification`（重分类改进 NRI / IDI） | 成对概率固定案例核对连续 NRI=2 和 IDI=0.6。 |
| `safety_adverse_events`（不良事件与安全性分析） | 重复记录按患者去重风险分母，核对 RR=2 与总人时。 |
| `clinical_subgroup_interaction`（临床亚组与交互作用） | 连续与数值 0/1 二分类的实际交互项系数及联合 F/LR 检验；输出交互表而非仅亚组内效应。 |
| `nested_case_control`（巢式病例对照 / 病例队列） | 巢式病例对照完整风险集的条件 logistic 与原生 clogit 相等。 |
| `composite_endpoint`（复合终点与层级终点） | 二分类复合结局核对 OR=3，保留组成事件统计。 |
| `clinical_rmst_non_ph`（RMST 与非比例风险分析） | 真实 R：显式 tau、参照方向、协变量调整的 survRM2 参考。 |
| `clinical_recurrent_events`（复发事件与多次事件分析） | 真实 R：实际亚单位时间区间、个体聚类稳健方差及重复时间拒绝。 |
| `clinical_multistate_illness_death`（多状态与 Illness-Death 模型） | 原生多状态 Aalen-Johansen 概率和为 1，正确风险区间事件数。 |
| `clinical_restricted_cubic_spline`（临床限制性立方样条与剂量反应） | rms Gaussian/Logistic 的真实四分位结点、非线性检验及参考值零对比；实际 rms::contrast 全协方差区间。 |
| `clinical_landmark_dynamic_prediction`（Landmark 与动态风险预测） | landmark 风险集与固定 horizon 的 timeROC IPCW AUC、KM 校准实际运行。 |
| `clinical_target_trial_emulation`（目标试验模拟） | 已确认基线策略、固定时窗二分类终点的 IPTW 风险差与重拟合 bootstrap。 |
| `clinical_doubly_robust_effect`（双重稳健治疗效应） | 含协变量的两臂结局回归 AIPW 与独立公式对照；IPTW/AIPW 分别计算、二元结局、基础权重、亚组及重拟合 bootstrap。 |
| `clinical_joint_longitudinal_survival`（纵向—生存联合分析） | JM 实际共享参数纵向-生存联合似然与关联参数估计。 |

### 公共卫生

| 模板 | 已验证范围与参考用例 |
| --- | --- |
| `epi_measures`（流行病学指标计算） | 明确事件类别和参照组的 RR/OR/风险差手算参考；零单元格拒绝使用 Wald 区间。 |
| `infectious_surveillance`（感染病监测与暴发趋势） | 历史基线先于监测区间，实际负二项预测界限与超限监测。 |
| `survey_weighted`（复杂抽样 / 加权分析） | 真实 survey::svydesign 的权重、重复 PSU ID 分层、设计自由度 t 区间及 svyglm 系数对照；孤立 PSU 默认拒绝、明确 adjust 后执行。 |
| `multilevel_public_health`（多层模型与区域差异） | 实际区域随机截距模型、ICC 与区域效应输出。 |
| `dag_causal`（DAG 与因果路径） | 已指定 DAG 的原生 dagitty 调整集验证与调整模型。 |
| `did_policy`（政策评估 / DID） | 共同政策时点 DID 的 lm 与 vcovCL 对照；真正联合前趋势 Wald、仅政策前安慰剂、日期时点和个体固定效应；信息不足保留未评估。 |
| `iv_panel_public_health`（工具变量与面板模型） | 连续暴露 2SLS 和个体/时间固定效应的 AER/lm 系数对照；固定效应分支无需工具变量；面板 IV、第一阶段稳健 F 与个体聚类实际执行。 |
| `environment_mixture`（环境暴露与混合效应） | 实际多暴露非线性 mgcv 模型、两两 ti 张量交互及联合分位数预测。 |
| `burden_economic`（疾病负担与卫生经济） | 成本/QALY 增量和已指定 WTP 的重抽样 CEAC。 |
| `spatial_epi`（空间流行病学） | 实际 WKT 邻接、spdep 全局及局部 Moran。 |
| `age_standardization`（年龄标准化率） | 共同标准人口直接标化和 epitools gamma 区间对照；粗率与标化率区分；缺失/不一致标准人口拒绝。 |
| `age_period_cohort`（年龄-时期-队列分析） | 实际 Epi APC 自然样条拟合和年龄/时期/队列输出。 |
| `small_area_survey`（小地区估计与加权总体推断） | 实际 survey 分层聚类权重域估计及 t 区间。 |
| `environment_time_series`（环境暴露时间序列） | 实际线性及非线性分布滞后；lpmatrix累计响应的完整协方差与区间。 |
| `health_inequality`（健康不平等与集中指数） | 加权并列 ridit 的手算参考；连续 SII、二元边际风险差/风险比、集中指数；显式类别排序、反向排序和分组实际使用。 |
| `attributable_fraction`（归因分值与可预防负担） | 个体与聚合分母的 RR/AFe/Levin PAF 手算；外部 RR、协变量 g-computation 与暴露减少情景；拒绝异质效应量列。 |
| `demographic_projection`（人口趋势与需求预测） | 实际 auto.arima 年龄分层预测及置信区间。 |
| `staggered_did_panel`（分批实施政策 DID） | 平衡、吸收处理面板的 group-time ATT；合成常数效应参考用例。 |
| `negative_control_sensitivity`（负对照与因果敏感性） | 负对照结局/暴露两分支的真实连续回归系数对照；保留各自尺度并采用 HC1 区间，不将不显著负对照当作无混杂证明。 |
| `public_rt_nowcasting`（Rt 估计与 Nowcasting） | 真实 EpiEstim 仅完整发病队列推断 Rt，迟报校正单列展示。 |
| `public_joinpoint_segmented_trend`（Joinpoint 与分段趋势） | 实际 segmented 固定断点拟合与 APC 区间。 |
| `public_vaccine_test_negative`（疫苗效果与 Test-Negative 设计） | 真实接种状态与年龄/就诊时间/地区/协变量接线；2×2 OR/VE 手算、校正 glm 对照及 VE 区间反向变换。 |
| `public_screening_evaluation`（筛查项目综合评价） | 正确筛查评分与金标准映射，0/1 灵敏度/特异度/净获益手算，Wilson 区间；方向、预设阈值、亚组及随访输出核验。 |
| `public_burden_decomposition`（疾病负担与归因分解） | 六种替换顺序 Shapley 精确归因和直接标化。 |
| `public_cost_effectiveness_psa`（成本效果与概率敏感性分析） | 已提供 draw_id 联合抽样的 ICER/NMB 手算；个体联合 bootstrap 保持成本效果相关；多策略 CEAC 并列概率核验。 |

### 临床试验与证据合成

| 模板 | 已验证范围与参考用例 |
| --- | --- |
| `sample_size_power`（样本量与把握度） | 独立两组连续终点正态近似；对照/试验分配比。 |
| `noninferiority_equivalence`（非劣效 / 等效结果分析） | 独立两组连续结局的 Welch 区间、双方向非劣效、等效及分析集筛选。 |
| `group_sequential`（群序贯与适应性设计） | 单侧、等分配连续终点；信息时间、Lan–DeMets 花费和总样本量。 |
| `dose_response_trial`（剂量反应与剂量探索） | 线性/二次/mgcv GAM 同样本 AIC，实际曲线与已观察范围推荐；仅已选毒性终点输出安全表。 |
| `rct_primary_analysis`（RCT 主终点分析） | 实际 ITT 筛选、协变量/分层 Gaussian 模型和主效应独立参考；同样本调整敏感性。 |
| `pairwise_meta`（成对 Meta 分析） | 原始效应尺度的闭式逆方差合并及 Q；显式 log 比值、DL 与指定亚组；逐研究剔除真实 R 核验。 |
| `network_meta`（网络 Meta 分析） | netmeta 实现接受两臂/多臂二分类 OR；当前真实参考验证九项两臂研究组成的三治疗网络及联赛表、P-score、比较图。 |
| `diagnostic_meta`（诊断试验 Meta 分析） | mada Reitsma 双变量真实四格；灵敏度/特异度、协方差、SROC 及精确二项区间核验。 |
| `meta_bias_sensitivity`（Meta 偏倚与敏感性） | metafor REML 独立参考合并；真实 Egger、风险 Meta 回归、LOO、Cook 距离。 |
| `bayesian_meta`（贝叶斯 Meta / 证据整合） | 实现正态均值及半正态/半Cauchy异质性先验；当前真实参考使用 half_normal，验证 bayesmeta 后验/新研究预测区间及未知先验字段拒绝。 |
| `sap_protocol_stats`（统计分析计划 SAP 框架） | 明确冻结 SAP JSON 方案，真实人数表壳；缺必需策略拒绝，不把表壳伪作研究终点结果。 |
| `randomization_allocation`（随机化与分配方案） | 真实分层置换区组、可变4/8区组、冻结种子；完整48人组间24/24，未反推既有分配。 |
| `crossover_trial_analysis`（交叉试验分析） | 完整2×2 nlme 随机受试者、治疗/周期/序列；emmeans 模型自由度区间和独立治疗效应参考。 |
| `cluster_rct_analysis`（整群随机试验） | 连续随机截距 lmer + CR2/Satterthwaite、加权二分类 GEE 的参考系数与 SE；ICC/模型表角色修复。 |
| `bioequivalence_pk`（生物等效性 / PK 分析） | 完整2×2 正值PK log模型，90% T/R 几何均值区间、明确0.8/1.25界限，独立 nlme 参考。 |
| `survival_trial_design`（生存终点试验设计） | gsDesign 双侧 Schoenfeld HR 事件设计参考；无概率仅事件设计，有已确认事件概率才转样本量。 |
| `safety_meta_analysis`（安全性 Meta 分析） | meta MH RR 与 DL/逆方差 RD；零单元格和双零研究真实处理，参考 RR 一致。 |
| `ipd_meta_mixed`（个体数据 IPD Meta） | lmer 一阶段研究随机截距与调整治疗均值差参考；未选亚组不生成亚组占位表。 |
| `living_review_update`（动态更新证据合成） | 按明确数字版本更新，同研究旧版本替换；最终十二独立研究不重复计权，REML 参考一致。 |
| `grade_evidence_summary`（证据等级与 Summary of Findings） | 真实效应合并与 log-RR 绝对风险换算；逐项保留用户确认等级，不自动发明降级判断。 |
| `trial_estimand_ich_e9`（ICH E9(R1) 估计目标分析） | 已确认 treatment-policy 分析集调整参考；composite 中间事件按明确值真实重编码，不虚构 hypothetical estimand。 |
| `trial_missing_tipping_point`（缺失数据 Tipping-Point 分析） | 真实 mice PMM delta-MI，Rubin 方差/自由度；仅缺失行平移、四格delta效应方向与区间核验。 |
| `trial_phase1_dose_escalation`（一期剂量递增设计与分析） | 标准3+3完成三/六人队列，1/6升级至明确下一级，精确二项区间；观察窗未完成拒绝决策。 |
| `trial_simon_two_stage`（Simon 二阶段设计） | p0=.2、p1=.4、alpha=.05、power=.8 的 optimal/minimax 及精确操作特征。 |
| `trial_sample_size_reestimation`（样本量再估计） | 连续终点盲态 pooled 调整残差SD、rpact固定设计重估N独立参考；生存分支明确拒绝。 |
| `trial_adaptive_randomization`（适应性随机与分配评估） | 实际顺序/分层/阶段分配轨迹；使用提供的真实assigned-treatment概率计算Hájek均值，参考手算一致。 |
| `trial_multiplicity_gatekeeping`（多重性与门控检验） | 指定 P 值列包含 0/1；家族内加权 Bonferroni、完整拒绝后的串行门控及亚组 alpha 分配手算核验。 |
| `evidence_meta_regression`（Meta 回归与亚组解释） | 连续/分类研究层协变量的 metafor REML + Knapp–Hartung 参考拟合；相同模型逐项剔除与拟合值气泡图。 |
| `evidence_proportion_rate_meta`（比例、率与患病率 Meta 分析） | 明确样本量或人时；零/全事件 PLO 变换和 IRLN 的独立数值参考；保留边界研究与事件数超过人时的有效率数据。 |

### 行为与社会科学

| 模板 | 已验证范围与参考用例 |
| --- | --- |
| `scale_reliability`（问卷信度与项目分析） | 真实 R：确认反向项 alpha、维度映射、单因子 omega_total、条目相关；两条目删题后信度留空。 |
| `efa_factor`（探索性因子分析） | 真实 R：主轴因子 pa 的载荷及 KMO 参考；完整 150 个样本 ID 的得分保留；固定种子并行分析。 |
| `cfa_sem`（CFA / 结构方程模型） | 真实 R：cfa/sem 分支语法、多组模型及 WLSMV 有序条目；实际载荷与参数估计对照。 |
| `bayesian_sem`（贝叶斯 SEM） | 真实 R：blavaan Stan 实际抽样、先验绑定、后验路径及 Rhat/ESS。 |
| `irt_models`（IRT / Rasch / 测验模型） | 真实 R：一维 Rasch 条目模型、实际信息函数和多组 DIF。 |
| `latent_class`（潜类别 / 聚类画像） | 真实 R：poLCA 多起点候选 BIC 与归一化类别后验概率。 |
| `psych_network`（心理网络分析） | 真实 R：EBICglasso 实际非零边、中心性和对象 Bootstrap。 |
| `social_network`（社会网络 / 关系数据） | 真实 R：无向简单关系网络及实际边数、ERGM 边项模型。 |
| `text_social_science`（文本内容量化） | 真实 R：quanteda TF-IDF 余弦相似度和无监督词汇聚类。 |
| `multivariate_ordination`（多变量降维与排序） | 真实 R：标准化数值 PCA 特征值、完整坐标和贡献。 |
| `measurement_invariance`（测量等值性检验） | 真实 R：lavaan 构型、度量、标量与严格不变性模型。 |
| `latent_growth_sem`（潜变量增长模型） | 真实 R：对象对齐四波次实际时间载荷的潜增长模型。 |
| `multilevel_behavior_model`（多层行为与教育模型） | 真实 R：lme4 层内×层间交互系数参考值及聚类 ICC。 |
| `mediation_moderation`（中介、调节与条件过程） | 真实 R：连续线性中介、调节、第一阶段调节中介及 Bootstrap；含空格列名参数映射。 |
| `weighted_survey_scale`（加权问卷与社会调查分析） | 真实 R：survey 设计均值和分组均值与加权均值参考值。 |
| `conjoint_choice`（联合分析 / 偏好实验） | 真实 R：respondent×task 条件选择模型及设计内相对重要性。 |
| `discrete_choice_model`（离散选择模型） | 真实 R：含备选项常数的条件模型，各选择集概率归一化。 |
| `interrater_agreement`（编码一致性与评分者信度） | 真实 R：全部评分者 ICC(A,1)、全部评分者对 Cohen Kappa 与线性加权 Kappa；对象 Bootstrap 及重复记录拒绝。 |
| `item_equating`（测验等值与题库链接） | 真实 R：两形式共同锚题 Rasch 校准及共同尺度真分映射。 |
| `mixed_methods_open_text`（开放题文本与人工编码整合） | 真实 R：quanteda 实际词频及两评定者人工编码一致性。 |
| `behavior_scale_development`（量表开发全流程） | 真实 R：量表条目 EFA 和 alpha 与 psych 参考值。 |
| `behavior_riclpm_cross_lagged`（交叉滞后与 RI-CLPM） | 真实 R：四波次双特征 RI-CLPM 随机截距及十二条滞后路径。 |
| `behavior_apim_dyadic`（行动者—伙伴互依模型 APIM） | 真实 R：可区分成员 APIM 四条实际 actor/partner 路径。 |
| `behavior_multilevel_sem`（多层结构方程模型） | 真实 R：lavaan 两层潜变量测量及层内/层间结构路径。 |
| `behavior_common_method_bias`（共同方法偏差评估） | 真实 R：真实单因子 CFA 拟合诊断，不把该诊断当作偏差成立证明。 |
| `behavior_latent_transition`（潜在转变分析） | 真实 R：LMest 联合潜 Markov 模型及状态转变概率归一化。 |
| `behavior_response_style_nonresponse`（响应风格与无应答偏差） | 真实 R：对象缺失/直线作答标记及真实响应状态逻辑模型。 |

### 机器学习

| 模板 | 已验证范围与参考用例 |
| --- | --- |
| `tabular_ml_workflow`（表格机器学习统一工作流） | 训练集交叉验证选模；改变测试集结局不改变选择分数与所选模型。 |
| `tree_boosting`（树模型与 Boosting） | 真实R：ranger/randomForest/gbm/xgboost四种实际学习器，训练内调参及独立分类/回归测试，命名事件类。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `regularized_models`（正则化与高维建模） | 真实R：训练内glmnet选择、真实系数路径；显式分类和保留数值0/1回归角色分别验证。 |
| `imbalanced_classification`（不平衡分类与稀有事件） | 真实R：仅训练平衡类别权重、预先阈值、独立测试PR/AP、ROC与混淆矩阵。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `survival_ml`（生存机器学习） | 真实R：生存森林/Cox独立测试C-index/SE、动态AUC与IPCW校准；仅训练Cox对照HR及95%Wald区间，模型含义明确。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `ml_explainability`（可解释机器学习） | 真实R：原生已训练lm/ranger模型bundle、训练背景fastshap、PDP与命名事件类；不重新拟合。 |
| `feature_selection`（特征筛选与稳定性） | 真实R：仅训练子采样Lasso选择频率与完整保留测试集。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `unsupervised_ml`（无监督学习与聚类） | 真实R：kmeans/mclust/dbscan各自实际模型，轮廓/BIC/噪声保持方法含义。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `automl_tabular`（表格数据 AutoML 候选集） | 真实R：预先Lasso/Ridge/ranger候选集，训练内CV选择与独立测试。 |
| `probability_calibration_ml`（机器学习概率校准） | 真实R：仅训练Platt系数及独立测试校准/Brier参考值。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `model_drift_shift`（数据漂移与模型迁移风险） | 真实R：确认参考来源的KS、参考分箱PSI及同族Holm校正。 |
| `sparse_text_ml`（文本特征机器学习） | 真实R：每个训练折独立词表、稀疏glmnet选择与独立测试AP。 |
| `anomaly_detection_ml`（异常检测与质量预警） | 真实R：dbscan LOF、预先异常阈值及逐行实际分数。 |
| `association_recommender`（关联规则与推荐分析） | 真实R：训练交易Apriori，测试交易遮盖物品实际推荐与命中。 |
| `dimensionality_reduction_ml`（高维特征降维） | 真实R：PCA/UMAP/t-SNE实际坐标与源数据行对应；仅PCA导出rotation/sdev/解释方差结构，含缺失病例行映射校验。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `stacking_ensemble_ml`（集成学习与 Stacking） | 真实R：四折OOF基模型预测、非负Ridge元模型、全训练重拟合和独立测试。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `cost_sensitive_threshold`（成本敏感阈值决策） | 真实R：仅训练FP/FN成本选择阈值及冻结阈值独立测试成本。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `ml_nested_cv_leakage`（嵌套交叉验证与泄漏审计） | 真实R：内调参/外CV/独立测试，整组、向前时间及联合清除cluster；拒绝特征与结构角色泄漏。 实际CSV→inspect→Preparer→注册render→冻结R→ResultValidator生产链通过。 |
| `ml_external_transportability`（外部验证与可迁移性） | 真实R：冻结预测按来源/亚组独立评估与实际校准，不用外部结局调参。 |
| `ml_fairness_subgroup`（公平性与亚组性能） | 真实R：行权重、确认阈值及亚组敏感度与加权参考值。 |
| `ml_conformal_prediction`（Conformal Prediction） | 真实R：有限样本校准分位数、独立测试区间与二分类Mondrian/密度比加权集合。 |
| `ml_multiclass_multilabel_ordinal`（多分类、多标签与有序预测） | 真实R：多项softmax、逐标签二项和有序polr预测，保持标签结构与独立测试。 |
| `ml_causal_uplift`（因果机器学习与 Uplift） | 真实R：诚实grf causal_forest、训练OOB nuisance、独立测试CATE和DR策略值。 |
| `ml_clinical_score_simplification`（临床评分简化） | 真实R：仅训练稀疏选择、整数系数×训练标准化特征评分、训练概率映射和独立测试。 |

### 时间空间与文本

| 模板 | 已验证范围与参考用例 |
| --- | --- |
| `forecasting`（时间序列预测） | ETS 指定预测步数及按时间留出 RMSE 与 forecast 参考一致；分地区月度序列分解、预测和不规则时间拒绝。 |
| `interrupted_time_series`（中断时间序列 / 变化点） | 精确预设干预点、即时水平及斜率变化，含协变量 OLS 与 AR(1) GLS 参考拟合；不自动改为序列中点。 |
| `bayesian_state_space`（贝叶斯状态空间 / 动态模型） | 真实 dlm Gaussian 趋势 Gibbs、IG 方差先验；72点/200次Gibbs与100个保留样本状态后验和含观测噪声的未来预测区间，明确固定外生系数。 |
| `gam_splines_time`（GAM 与非线性趋势） | mgcv REML 平滑及调整协变量系数独立参考，真实 edf/p、曲线点区间；二分类采用 logit。 |
| `gis_mapping`（GIS 地图与空间数据处理） | 真实 sf EPSG3857 点几何、WKT/坐标连接、有效性与指标汇总；点地图，不冒充行政区多边形。 |
| `spatial_autocorrelation`（空间自相关与热点） | 明确对称邻接/行标准化 spdep 全局 Moran 独立参考、真实局部 Moran BH 与点位 LISA。 |
| `geostatistics`（地统计与空间插值） | 真实投影 CRS、gstat Sph/Exp/Gau 经验变差函数拟合与普通克里金；预测均值独立参考及非负方差核验。 |
| `network_graphs`（网络图与图结构分析） | 真实 igraph 加权无向网络、平行边合并、Louvain、中心性与独立 degree 参考；边强度倒数用于路径。 |
| `text_mining_nlp`（文本挖掘与语义特征） | quanteda 真实词频/双词/TF-IDF 文档特征与1770文档对余弦；真实总词频一致，明确英文或已分词空格输入。 |
| `large_data_engine`（大数据表格与列式计算） | data.table按选定组别和指标计算实际均值、行数与性能回执。 |
| `panel_forecast`（面板时间序列预测） | 两面板真实 ARIMA、末段留出 MAE/RMSE 和 refit 预测独立参考；外生分支必须有真实未来矩阵。 |
| `functional_data_analysis`（函数型数据分析） | fda B样条平滑与 FPCA 解释率独立参考，12受试者曲线/分数/聚类；完整共同观测网格。 |
| `spatiotemporal_panel`（时空面板数据） | 真实区域固定截距/时间趋势/协变量 Gaussian 同样本系数参考；有坐标及 CRS 才输出 sf 地图，无坐标不造地图。 |
| `raster_exposure_linkage`（栅格暴露提取与空间连接） | 真实 terra 2×2 Raster 与 CRS 变换后逐点提取四值、暴露差值/覆盖率；明确冻结栅格JSON。 |
| `distance_accessibility`（距离、可达性与服务覆盖） | 真实 WGS84 椭球距离 geosphere 独立参考、最近设施与阈值覆盖；距离单位米，不伪作路网时间。 |
| `spatial_regression_models`（空间回归模型） | 实际 OLS/SAR lag/SEM error、同样本 AIC、SAR rho 独立参考及残差 Moran/空间点图。 |
| `topic_embedding_text`（文本主题与向量表征） | text2vec TF-IDF/LSA-SVD 真实二维嵌入、聚类和代表词；不冒充 Transformer 语义向量。 |
| `knowledge_graph`（知识图谱与关系网络） | 真实已提供的有向关系多重图和实体/边属性，方向/四边/三类关系核验；不发明关系语义。 |
| `event_sequence_patterns`（事件序列与路径模式） | 真实八受试者按时间序列路径、条件转移计数/概率和实际间隔；重复时点不明确顺序时拒绝。 |
| `time_var_vecm_cointegration`（VAR、VECM 与协整分析） | vars VAR 系数独立参考、真实 IRF/FEVD；额外 urca Johansen 秩1 VECM 分支核验，秩不合法明确拒绝。 |
| `time_hierarchical_grouped_forecast`（分层与分组预测） | 真实 hts 层级与底层 ARIMA、bottom-up 完全一致求和；真实留出65/7；选入外生变量明确要求未来输入，未伪造聚合区间。 |
| `time_seasonal_anomaly_changepoint`（季节异常与变化点检测） | 真实 robust STL 残差独立参考、MAD 检测第45点异常及 PELT/MBIC 趋势断点；干预标记仅观测关联。 |
| `spatial_point_pattern`（空间点格局分析） | 真实平面窗口 spatstat Poisson ppm 强度50/10000与 isotropic K；总体过程明确 unmark，不拿标记频率冒充强度。 |
| `spatial_interpolation_validation`（空间插值验证与暴露不确定性） | 真实 gstat 五块空间交叉验证 RMSE 独立参考及预测残差；留出法可选，变差模型复用训练全样本，非嵌套调参验收。 |
| `text_sentiment_analysis`（情感与立场分析） | 真实 syuzhet English Bing 分值独立参考和词典覆盖；无覆盖保留NA、全无覆盖拒绝，不捏造中文情感。 |
| `text_supervised_classification`（监督式文本分类） | 真实二分类 glmnet ElasticNet 42/18留出、训练词表/IDF隔离、真实 AUC/Brier/混淆矩阵；重复文本跨集拒绝。 |
| `text_entity_relation_extraction`（实体与关系抽取） | 明确词典字面实体及 source/target 命名捕获关系规则；四实体/两 treats 关系及位置核验，无关系不以共现伪作关系。 |
| `text_topic_stability`（主题模型稳定性验证） | 真实 text2vec LDA 两次独立拟合和 clue Hungarian 主题词分布余弦匹配；真实元数据描述汇总，不冒充 STM 因果/协变量效应。 |

### 组学与生物信息

| 模板 | 已验证范围与参考用例 |
| --- | --- |
| `bulk_rnaseq`（Bulk RNA-seq 差异表达） | 原始整数SE与实际CSV经inspect/preparer/冻结链DESeq2；规范化、方向及全角色验收。 |
| `microarray_expression`（芯片表达谱分析） | 真实limma特征检验；独立矩阵JSON注释严格ID对齐和反向比较等于均值差。 |
| `pathway_enrichment`（通路富集 / GO / KEGG / GSEA） | ORA P与完整背景超几何一致；原生共享基因emapplot、真实GSEA/NES/运行曲线，分支输出条件化。 |
| `wgcna_network`（WGCNA 共表达网络） | WGCNA signed真网络、实际模块性状热图/相关/kME hubs；hub角色正确，真实CSV准备链通过。 |
| `single_cell`（单细胞 RNA-seq） | 真实SCE RDS导入经准备/冻结链Seurat Normalize/PCA/聚类/UMAP与markers，100细胞。 |
| `genomic_ranges`（基因组区间与注释） | 确认版本/区间坐标findOverlaps核对4命中与宽度；实际基因组start/end轨迹。 |
| `microbiome_amplicon`（微生物组 / 扩增子） | 实际vegan/CLR-limma、原生phyloseq导入注释；已确认协变量与pair限制置换进入PERMANOVA。 |
| `proteomics_ms`（蛋白组 / 质谱表达矩阵） | 真实limma差异/PCA；实际CSV+JSON records确认映射经preparer执行clusterProfiler ORA，无映射不造通路。 |
| `flow_cytometry`（流式细胞术数据） | 确认门控事件按12独立样本计数，quasibinomial比例差异及HC1推断。 |
| `multiomics_integration`（多组学整合与联合解释） | 真实MFA跨组学关联；MAE sampleMap逆序对齐误差0，确认colData样本ID与primary映射，批次保留表型。 |
| `rnaseq_batch_qc`（RNA-seq 批次与质控） | 真实edgeR TMM因子积为1，计数深度/PCA/设计矩阵秩诊断通过验收。 |
| `scrna_marker_enrichment`（单细胞 Marker 富集解释） | Seurat真实组别marker检验和检测背景clusterProfiler富集。 |
| `scrna_sample_integration`（多样本单细胞整合） | 真实FindIntegrationAnchors/IntegrateData integrated assay；样本组成和为1。 |
| `methylation_expression_proxy`（甲基化/表观组矩阵探索） | 明确log2矩阵真实limma；PCA与标准化热图，未自动猜测beta/M。 |
| `peak_region_analysis`（峰/开放染色质区间分析） | 实际重叠与注释；BED半开区间明确转换为闭区间，邻接区间无假重叠。 |
| `metabolomics_xcms`（代谢组峰表与差异代谢物） | 明确尺度峰表limma；确认映射时实际clusterProfiler ORA；模型文字独立，不冒称原始峰识别。 |
| `multiassay_clinical_bridge`（组学-临床变量桥接） | 唯一样本ID桥接，已知特征临床效应约3，HC1关联与BH。 |
| `immune_repertoire_summary`（免疫组库汇总分析） | 聚合克隆Shannon与手算相等、真实共享克隆3个。 |
| `omics_gwas_prs`（GWAS 与多基因风险评分） | 逐剂量GLM与锁定外部PRS精确加权；真实二分类AUC在性能表，未在评估集训练权重。 |
| `omics_variant_burden_annotation`（变异注释与基因负担检验） | 显式零剂量基因负担，调整系数与原生lm精确相等。 |
| `omics_scrna_trajectory`（单细胞轨迹与伪时序） | slingshot实际谱系/伪时序、tradeSeq NB GAM/associationTest/predictSmooth；实际表达趋势与轨迹组合。 |
| `omics_scrna_differential_abundance`（单细胞差异丰度） | 供体级cellcounts edgeR QL；table/独立matrix的唯一观测轴与重复供体分开；实际cells CSV准备链通过。 |
| `omics_spatial_transcriptomics`（空间转录组分析） | 真实坐标和独立样本区域pseudobulk DESeq2；完整配对入模，部分配对/设计冲突拒绝，真实基因表达地图。 |
| `omics_atac_chip_binding`（ATAC/ChIP 差异结合分析） | 真实整数peak count的DESeq2差异结合和火山；批次设计实际入模。 |
| `omics_alternative_splicing`（可变剪接分析） | edgeR diffSpliceDGE真剪接；12基因/36事件，primary输出真实exon事件，保留Simes与事件检验。 |
| `omics_eqtl`（eQTL 分析） | 单对兼容与真实CSV多对lm/HC1；明确cis/trans、全族BH；真实HC1效应区间和QQ组合，单对说明QQ不可评估；注释缺失保留样本，冲突拒绝。 |
| `omics_somatic_mutation_cnv`（体细胞突变与 CNV 综合分析） | 确认中性类别后的显式检测分母，变异频率0.3，真实状态矩阵。 |
| `omics_microbiome_compositional`（微生物组组成型差异） | 真实vegan多样性与CLR-limma；primary带row_type交付样本多样性/特征差异；真实相对组成、Bray PCoA和CLR效应组合图，全角色验收。 |
| `omics_metabolomics_pathway`（代谢组注释与通路分析） | 实际limma差异代谢物与确认映射clusterProfiler ORA；背景为检测特征。 |

## 执行完成与文件隔离

对话最后一个 `done` 事件在规范结果状态与任务回执落盘后发送。对话结束不自动使分析成功：R 失败、冻结方案未执行或必需产物缺失时，任务保留 `completed=false`、`turn_completed=true`；成功修复后的当前规范 portfolio 可以恢复成功。`complete=true` 与诊断质量、人工科学审核仍是独立字段。

分析 R 和其子进程仅可读取声明的输入、运行时/包库、helper 和已有 checkpoint，仅可写本任务输出与独立私有临时目录，禁止网络访问。临时目录使用无空格路径，并在操作结束后清理。checkpoint 由宿主校验后晋升。宿主以原子替换写入脚本，避免重复运行时通过旧脚本链接改写目录外文件。包安装、包就绪检查属于独立的宿主操作。

macOS 使用系统 `sandbox-exec`，Linux 使用 `bubblewrap`，Windows 10 / 11 使用 AppContainer 和 Job Object。相关参考测试检查读取、写入、输入覆盖、连接和链接绕过；执行结果取决于实际测试平台。Windows 原生检查需要真实 Windows 与 R，不能由模拟平台测试代替。缺失隔离后端时不启动未隔离的分析进程；API 就绪状态也不会仅因发现 R 而声明分析可用。`isolation.verification=launcher_only` 表示启动器或系统 API 存在检查，平台执行验收仍是独立步骤。
