"""Real R computations under the product frozen-input and AST guards."""
from __future__ import annotations

import json
import csv
import random
from pathlib import Path

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.contract_conditions import conditions_match
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.omics_workflows import CORES, INPUTS, ROLE_MAPS
from agent.analysis.skeleton_cores import _make_engine
from agent.tools.research.r_binding import BindingHelpersMixin
from r_runtime.protocol import DatasetRef, RRunRequest
from tests.science.test_clinical_public_workflows import backend, validate_result


def run_omics(backend, tmp_path, tid, data, inputs, probe="list(ok=TRUE)",prepared_plan=None,dataset_ref=None,expected_error=None):
    t=AnalysisCatalog().get(tid);specs={x.id:x for x in t.input_specs}
    resolved={k:ResolvedInput(k,k,specs[k].kind if k in specs else "specification",v,"user") for k,v in inputs.items()}
    plan=AnalysisPlan(plan_id="omics-native",source="template",dataset_id="fixture",intent=t.title,category=t.category,method_family=t.family,scenario=t.scenario,variable_bindings={},packages=(),prerequisites=(),assumptions=(),diagnostics=t.diagnostics,required_outputs=tuple(o for o in t.required_outputs if conditions_match(o.conditions,resolved)),resource_class="standard",template_id=tid,resolved_inputs=resolved)
    if prepared_plan is not None:
        from agent.analysis.script_library import render_script
        from agent.analysis.workflows.registry import default_workflow_registry
        from agent.analysis.workflows.validation import validate_workflow_run_spec
        from r_runtime.inspection import inspection_summary

        plan=prepared_plan
        inspected=backend.inspect_dataset(dataset_ref,timeout_seconds=120)
        assert inspected.ok,inspected.stderr
        profile=dict(inspection_summary(inspected),dataset_id=plan.dataset_id,format=dataset_ref.format)
        spec=default_workflow_registry().compile(plan,dataset_profile=profile)
        preflight=validate_workflow_run_spec(spec,profile)
        (tmp_path/'workflow-preflight.json').write_text(json.dumps(dict(profile=profile,resolved_inputs=spec.resolved_inputs,issues=[x.__dict__ for x in preflight.issues]),ensure_ascii=False,indent=2))
        assert preflight.ready,preflight.issues
        script=render_script(plan)
        assert script is not None
    else:
        script=_make_engine(CORES[tid],INPUTS[tid],())(plan,ROLE_MAPS[tid])
    guarded=script+'\nbaa_text("probe",jsonlite::toJSON('+probe+',auto_unbox=TRUE,digits=15))'
    code=data+'\n'+BindingHelpersMixin._frozen_input_prelude(plan)+'\n'+BindingHelpersMixin._frozen_user_code_block(guarded,plan)+'\n'+BindingHelpersMixin._frozen_input_epilogue(plan)
    path=tmp_path/'input.csv';path.write_text('x\n1\n')
    result=backend.run_code(RRunRequest(job_id="omics-native",dataset=dataset_ref or DatasetRef("fixture",str(path),"csv","input.csv"),code=code,artifacts_dir=str(tmp_path/'artifacts'),timeout_seconds=300,resource_class=plan.resource_class))
    if expected_error is not None:
        assert not result.ok
        assert expected_error in result.error+'\n'+result.stderr
        return result,None
    assert result.ok,result.error+'\n'+result.stderr
    validate_result(plan,result,tmp_path,'omics-native')
    p=next(a for a in result.texts if a.name=='probe')
    return result,json.loads(Path(p.path).read_text())


FEATURES=[f'g{i}' for i in range(1,61)]
CONTINUOUS='''set.seed(902);n<-24;p<-60;group<-rep(0:1,each=12);x<-matrix(rnorm(n*p),n,p);x[group==1,1:10]<-x[group==1,1:10]+2
dataset<-data.frame(id=paste0("s",1:n),group=group,batch=rep(c("A","B"),12),trait=rnorm(n),x);names(dataset)[5:ncol(dataset)]<-paste0("g",1:p)'''
COUNTS='''set.seed(992);n<-24;p<-60;group<-rep(0:1,each=12);x<-matrix(rnbinom(n*p,mu=50,size=8),n,p);x[group==1,1:10]<-x[group==1,1:10]+80
dataset<-data.frame(id=paste0("s",1:n),group=group,batch=rep(c("A","B"),12),x);names(dataset)[4:ncol(dataset)]<-paste0("g",1:p)'''
COUNT_SE=COUNTS+'''
dataset<-SummarizedExperiment::SummarizedExperiment(assays=list(counts=t(as.matrix(dataset[,paste0("g",1:60)]))),colData=S4Vectors::DataFrame(id=dataset$id,group=dataset$group,batch=dataset$batch));colnames(dataset)<-paste0("s",1:24)'''
CELLS='''set.seed(448);p<-80;n<-100;group<-rep(0:1,each=50);x<-matrix(rpois(p*n,lambda=3),p,n);x[1:10,group==1]<-x[1:10,group==1]+8;rownames(x)<-paste0("g",1:p);colnames(x)<-paste0("cell",1:n)
dataset<-SingleCellExperiment::SingleCellExperiment(assays=list(counts=x),colData=S4Vectors::DataFrame(sample=rep(paste0("s",1:10),each=10),group=group,batch=rep(c("a","b"),50),cluster=rep(c("root","middle","late","late"),each=25),type=rep(c("T","B","T","M"),25)))'''
CELL_INPUTS=dict(data_object="fixture",sample_id="sample",group="group")
MAP=json.dumps({"term":["P1"]*10+["P2"]*10,"gene":[f'g{i}' for i in range(1,21)]})

CASES={}
for tid in ('microarray_expression','proteomics_ms','methylation_expression_proxy','metabolomics_xcms','omics_metabolomics_pathway'):
    ins=dict(data_object="fixture",feature_columns=FEATURES,group="group",sample_id="id",input_scale="log2")
    if tid in ('methylation_expression_proxy','metabolomics_xcms'):
        ins.pop('feature_columns');ins['data_object']=FEATURES;ins.pop('group');ins['group_condition']='group'
    if tid=='omics_metabolomics_pathway':ins['term_gene_mapping']=MAP
    CASES[tid]=(CONTINUOUS,ins,'list(n=nrow(tab),effect=tab$logFC[match("g1",tab$feature)])','expression')
for tid in ('bulk_rnaseq','omics_atac_chip_binding'):
    ins=dict(data_object="fixture",group="group",batch="batch")
    if tid=='omics_atac_chip_binding':ins['sample_group']=ins.pop('group')
    CASES[tid]=(COUNT_SE,ins,'list(n=nrow(tab),normalized=all(is.finite(normalized)),integer=all(x==floor(x)))','counts')
CASES['rnaseq_batch_qc']=(COUNT_SE,dict(data_object="fixture",sample_id="id",batch="batch",group="group"),'list(n=nrow(qc),norm=prod(qc$TMM因子))','qc')
CASES['wgcna_network']=(CONTINUOUS,dict(data_object="fixture",feature_columns=FEATURES,sample_trait="trait",input_scale="log2",min_module_size=10),'list(n=nrow(modules),hubs=nrow(hubs),native=length(network$colors))','wgcna')
CASES['pathway_enrichment']=('''dataset<-data.frame(gene=paste0("g",1:10))''',dict(gene_feature_list="gene",species_id_type="confirmed synthetic/geneID",background_gene_set=[f'g{i}' for i in range(1,81)],term_gene_mapping=MAP),'list(P=tab$pvalue[tab$ID=="P1"],ref=phyper(9,10,70,10,lower.tail=FALSE),terms=nrow(tab))','enrichment')
RANGES='''dataset<-data.frame(chr=c("chr1","chr1","chr1","chr2"),start=c(1,10,30,1),end=c(15,20,40,5),signal=1:4)'''
REFERENCE=json.dumps(dict(chromosome=['chr1','chr2'],start=[1,1],end=[40,10],id=['geneA','geneB']))
CASES['genomic_ranges']=(RANGES,dict(chromosome='chr',start_position='start',end_position='end',annotation_reference='confirmed synthetic-v1',annotation_intervals=REFERENCE,coordinate_system='one_based_closed'),'list(hits=nrow(overlap),widths=annotation$width)','ranges')
CASES['peak_region_analysis']=(RANGES,dict(chromosome='chr',start_position='start',end_position='end',peak_signal='signal',coordinate_system='one_based_closed'),'list(hits=nrow(overlap),widths=annotation$width)','peak')
CASES['multiassay_clinical_bridge']=(CONTINUOUS+'\ndataset$y<-1+3*dataset$g1+rnorm(24,.0,.1)',dict(sample_id='id',data_object=['g1','g2'],outcome='y',outcome_family='continuous'),'list(effect=model$效应[model$feature=="g1"],n=nrow(alignment))','bridge')
CASES['immune_repertoire_summary']=('''dataset<-data.frame(sample=rep(c("s1","s2"),each=3),clone=rep(c("a","b","c"),2),count=c(1,1,2,2,3,5))''',dict(sample_id='sample',clone_sequence_id='clone',measure='count'),'list(shannon=diversity$shannon[1],ref=-sum(c(.25,.25,.5)*log(c(.25,.25,.5))),shared=sum(colSums(mat>0)>1))','repertoire')
for tid in ('microbiome_amplicon','omics_microbiome_compositional'):
    ins=dict(data_object='fixture',feature_columns=FEATURES,group='group',sample_id='id')
    if tid=='microbiome_amplicon':ins['abundance_object']=ins.pop('data_object')
    else:ins.pop('data_object');ins.pop('feature_columns');ins['taxon_abundance']=FEATURES
    CASES[tid]=(COUNTS,ins,'list(n=nrow(differential),center=max(abs(rowMeans(clr))),alpha=nrow(alpha))','microbiome')
ASSOC='''set.seed(700);dataset<-data.frame(id=1:160,dose=rbinom(160,2,.35),z=rnorm(160));dataset$expression<-2*dataset$dose+dataset$z+rnorm(160,sd=.4);dataset$y<-dataset$expression;dataset$dose2<-rbinom(160,2,.25)'''
CASES['omics_eqtl']=(ASSOC,dict(sample_id='id',genotype_variable='dose',expression_variable='expression',covariates=['z']),'list(effect=tab$效应[1],ref=coef(lm(expression~dose+z,dataset))[2])','eqtl')
CASES['omics_gwas_prs']=(ASSOC,dict(sample_id='id',phenotype='y',genotype_prs_variable=['dose','dose2'],covariates=['z'],score_weights=[2,.1]),'list(n=nrow(tab),score=max(abs(df$.prs-(2*df$dose+.1*df$dose2))))','gwas')
CASES['omics_variant_burden_annotation']=(ASSOC+'\ndataset$gene<-"geneA"',dict(sample_id='id',gene_region='gene',dose='dose',phenotype='y',covariates=['z']),'list(effect=tab$效应[1],ref=coef(lm(y~dose+z,dataset))[2])','eqtl')
CASES['omics_somatic_mutation_cnv']=('''dataset<-expand.grid(id=1:20,gene=c("A","B"));dataset$state<-ifelse(dataset$id%%3==0,"gain","neutral")''',dict(sample_id='id',gene_segment='gene',variant_cnv_state='state',neutral_state='neutral'),'list(freq=tab$frequency,n=nrow(tab))','cnv')
CASES['single_cell']=(CELLS,CELL_INPUTS,'list(cells=nrow(qc),native=length(Seurat::Idents(seurat)),umap=nrow(Seurat::Embeddings(seurat,"umap")))','cells')
CASES['scrna_marker_enrichment']=(CELLS,dict(data_object='fixture',group_condition='group',sample_id='sample',term_gene_mapping=MAP),'list(n=nrow(markers),enriched=nrow(enrichment$table))','markers')
CASES['scrna_sample_integration']=(CELLS,dict(data_object='fixture',sample_id='sample',batch='batch',group_condition='group'),'list(cells=ncol(seurat),assay=Seurat::DefaultAssay(seurat),sum=max(abs(tapply(proportions$proportion,proportions$sample,sum)-1)))','integration')
TRAJECTORY='''set.seed(482);p<-24;n<-90;t<-seq(0,1,length.out=n);x<-matrix(rpois(p*n,5),p,n);x[1:8,]<-matrix(rpois(8*n,rep(exp(1+3*t),each=8)),8,n);x[9:16,]<-matrix(rpois(8*n,rep(exp(4-3*t),each=8)),8,n);rownames(x)<-paste0("g",1:p);colnames(x)<-paste0("c",1:n)
dataset<-SingleCellExperiment::SingleCellExperiment(assays=list(counts=x),colData=S4Vectors::DataFrame(cluster=rep(c("root","middle","late"),each=30),sample=rep(c("a","b","c"),30)))'''
CASES['omics_scrna_trajectory']=(TRAJECTORY,dict(data_object='fixture',cell_annotations=['cluster'],starting_cell_population='root'),'list(lineages=ncol(pseudotime),genes=nrow(tab),fit=class(fit),finite=sum(is.finite(tab$pvalue)))','trajectory')
DA='''set.seed(772);sample<-rep(paste0("s",1:12),each=100);group<-rep(rep(0:1,each=6),each=100);type<-unlist(lapply(1:12,function(i){nT<-if(i<=6)sample(20:35,1)else sample(55:70,1);c(rep("T",nT),rep("B",100-nT))}));x<-matrix(rpois(20*1200,3),20,1200);rownames(x)<-paste0("g",1:20);colnames(x)<-paste0("c",1:1200);dataset<-SingleCellExperiment::SingleCellExperiment(assays=list(counts=x),colData=S4Vectors::DataFrame(sample=sample,group=group,type=type))'''
CASES['omics_scrna_differential_abundance']=(DA,dict(data_object='fixture',sample_id='sample',cell_type='type',group='group'),'list(samples=ncol(cellcounts),types=nrow(tab),t_effect=tab$logFC[tab$cell_type=="T"])','da')
CASES['omics_spatial_transcriptomics']=(COUNT_SE+'\nSummarizedExperiment::colData(dataset)$x<-rep(1:6,4);SummarizedExperiment::colData(dataset)$y<-rep(1:4,each=6)',dict(data_object='fixture',coordinates=['x','y'],sample_id='id',region='group'),'list(n=nrow(tab),spots=nrow(spatial))','spatial')
FLOW='''set.seed(782);dataset<-data.frame(sample=rep(paste0("s",1:12),each=100),group=rep(rep(0:1,each=6),each=100));dataset$type<-unlist(lapply(1:12,function(i){nT<-if(i<=6)sample(20:35,1)else sample(55:70,1);c(rep("T",nT),rep("B",100-nT))}))'''
CASES['flow_cytometry']=(FLOW,dict(flow_object='fixture',sample_id='sample',group='group',cell_subpopulation_marker='type'),'list(samples=nrow(counts),effect=tab$效应[tab$cell_type=="T"])','flow')
CASES['multiomics_integration']=(CONTINUOUS,dict(data_object='fixture',sample_id='id',phenotype_group='group',assay_blocks=json.dumps({'RNA':['g1','g2','g3'],'protein':['g4','g5','g6']})),'list(samples=nrow(alignment),features=nrow(features),pairs=nrow(correlations),native=class(mfa))','multiomics')
SPLICING='''set.seed(499);n<-16;p<-36;counts<-matrix(rnbinom(n*p,mu=60,size=10),p,n);counts[1:3,9:16]<-counts[1:3,9:16]+90;dataset<-data.frame(feature=paste0("ex",1:p),gene=rep(paste0("gene",1:12),each=3),counts);names(dataset)[3:ncol(dataset)]<-paste0("s",1:n)'''
CASES['omics_alternative_splicing']=(SPLICING,dict(feature_count=[f's{i}' for i in range(1,17)],sample_group='group',gene_transcript_id='gene',feature_id='feature',sample_metadata=json.dumps({'sample':[f's{i}' for i in range(1,17)],'group':[0]*8+[1]*8})),'list(genes=nrow(tab),events=nrow(exons),native=class(splicing))','splicing')


@pytest.mark.parametrize('tid',list(CASES))
def test_native_omics(backend,tmp_path,tid):
    data,inputs,probe,check=CASES[tid];result,p=run_omics(backend,tmp_path,tid,data,inputs,probe)
    actual={a.metadata.get('role') for a in [*result.tables,*result.figures,*result.texts]}
    applicable=set(ROLE_MAPS[tid])
    if tid=='proteomics_ms' and 'term_gene_mapping' not in inputs:applicable-={'manuscript_results','pathway_table','pathway_plot'}
    if tid=='metabolomics_xcms' and 'term_gene_mapping' not in inputs:applicable-={'analysis_recommendation','pathway_table','pathway_plot'}
    if tid=='pathway_enrichment' and str(inputs.get('analysis_method','ora')).lower()!='gsea':applicable-={'figure_3'}
    assert applicable<=actual,(tid,applicable-actual)
    if check=='expression':assert p['n']==60 and p['effect']>1
    elif check=='counts':assert p==dict(n=60,normalized=True,integer=True)
    elif check=='qc':assert p['n']==24 and p['norm']==pytest.approx(1)
    elif check=='wgcna':
        assert p==dict(n=60,hubs=60,native=60)
        artifact=next(a for a in result.tables if a.metadata.get('role')=='table_2')
        with Path(artifact.path).open() as fh:assert {'feature','module','kME'}<=set(csv.DictReader(fh).fieldnames)
    elif check=='enrichment':assert p['P']==pytest.approx(p['ref']) and p['terms']>=1
    elif check=='ranges':assert p['hits']==4 and p['widths']==[15,11,11,5]
    elif check=='peak':assert p['hits']==1
    elif check=='bridge':assert p['effect']==pytest.approx(3,abs=.15) and p['n']==24
    elif check=='repertoire':assert p['shannon']==pytest.approx(p['ref']) and p['shared']==3
    elif check=='microbiome':
        assert p['n']==60 and p['center']<1e-12 and p['alpha']==24
        if tid=='omics_microbiome_compositional':
            artifact=next(a for a in result.tables if a.metadata.get('role')=='primary_table')
            with Path(artifact.path).open() as fh:rows=list(csv.DictReader(fh))
            diversity=[r for r in rows if r['row_type']=='sample_diversity']
            difference=[r for r in rows if r['row_type']=='taxon_CLR_differential']
            assert len(diversity)==24 and len(difference)==60
            assert {r['sample'] for r in diversity}=={f's{i}' for i in range(1,25)}
            assert all(float(r['shannon'])>0 and float(r['richness'])==60 for r in diversity)
            assert all(r['sample'] in ('','NA') and r['feature'] for r in difference)
    elif check=='eqtl':assert p['effect']==pytest.approx(p['ref'])
    elif check=='gwas':assert p['n']==3 and p['score']==pytest.approx(0)
    elif check=='cnv':assert p['n']==2 and p['freq']==pytest.approx([.3,.3])
    elif check=='cells':assert p==dict(cells=100,native=100,umap=100)
    elif check=='markers':assert p['n']>0 and p['enriched']>=1
    elif check=='integration':assert p['cells']==100 and p['assay']=='integrated' and p['sum']<1e-12
    elif check=='trajectory':assert p['lineages']>=1 and p['genes']==24 and p['finite']>0
    elif check=='da':assert p['samples']==12 and p['types']==2 and p['t_effect']>0
    elif check=='spatial':assert p==dict(n=60,spots=24)
    elif check=='flow':assert p['samples']==12 and p['effect']>2
    elif check=='multiomics':assert p['samples']==24 and p['features']==6 and p['pairs']==9 and 'MFA' in p['native']
    elif check=='splicing':
        assert p['genes']==12 and p['events']==36
        artifact=next(a for a in result.tables if a.metadata.get('role')=='primary_table')
        with Path(artifact.path).open() as fh:assert len(list(csv.DictReader(fh)))==36


def test_native_matrix_annotation_and_reversed_contrast(backend,tmp_path):
    data='''set.seed(820);x<-matrix(rnorm(10*16),10,16);x[1:3,9:16]<-x[1:3,9:16]+2;rownames(x)<-paste0("gene",1:10);colnames(x)<-paste0("s",1:16);dataset<-x'''
    inputs=dict(data_object='fixture',matrix_orientation='features_by_samples',sample_metadata=json.dumps({'id':[f's{i}' for i in range(1,17)],'group':[0]*8+[1]*8}),sample_id='id',group='group',input_scale='log2',ref_group='1',comparison_group='0')
    _,p=run_omics(backend,tmp_path,'microarray_expression',data,inputs,'list(actual=tab$logFC[match("gene1",tab$feature)],ref=mean(dataset[1,1:8])-mean(dataset[1,9:16]),annotation=nrow(annotation))')
    assert p['actual']==pytest.approx(p['ref']) and p['annotation']==10


def test_native_multiassay_sample_alignment(backend,tmp_path):
    data='''set.seed(181);ids<-paste0("s",1:12);rna<-matrix(rnorm(4*12),4,12,dimnames=list(paste0("g",1:4),ids));protein<-matrix(rnorm(3*12),3,12,dimnames=list(paste0("p",1:3),rev(ids)));dataset<-MultiAssayExperiment::MultiAssayExperiment(experiments=list(RNA=SummarizedExperiment::SummarizedExperiment(assays=list(log= rna)),protein=SummarizedExperiment::SummarizedExperiment(assays=list(log=protein))),colData=S4Vectors::DataFrame(id=ids,group=rep(0:1,each=6),row.names=ids))'''
    _,p=run_omics(backend,tmp_path,'multiomics_integration',data,dict(data_object='fixture',sample_id='id',phenotype_group='group'),'list(samples=nrow(alignment),error=max(abs(matrices$protein[1,]-protein[1,match(common,colnames(protein))])))')
    assert p['samples']==12 and p['error']==pytest.approx(0)


def test_actual_ranked_gsea(backend,tmp_path):
    data='''dataset<-data.frame(gene=paste0("g",1:100),rank=100:1)'''
    mapping=json.dumps({'term':['P1']*15+['P2']*15,'gene':[f'g{i}' for i in range(1,16)]+[f'g{i}' for i in range(60,75)]})
    _,p=run_omics(backend,tmp_path,'pathway_enrichment',data,dict(gene_feature_list='gene',ranking_statistic='rank',species_id_type='confirmed synthetic/geneID',analysis_method='gsea',term_gene_mapping=mapping),'list(native=class(enrichment),NES=tab$NES[tab$ID=="P1"],n=nrow(tab))')
    assert 'gseaResult' in p['native'] and p['NES']>0 and p['n']==2


def test_bed_coordinate_conversion(backend,tmp_path):
    data='''dataset<-data.frame(chr=c("chr1","chr1"),start=c(0,10),end=c(10,20))'''
    _,p=run_omics(backend,tmp_path,'peak_region_analysis',data,dict(chromosome='chr',start_position='start',end_position='end',coordinate_system='zero_based_half_open'),'list(hits=nrow(overlap),start=annotation$start,width=annotation$width)')
    assert p==dict(hits=0,start=[1,11],width=[10,10])


def test_native_phyloseq_through_preparer(backend,tmp_path):
    from agent.analysis.preparation import AnalysisPreparer
    from r_runtime.inspection import inspection_summary
    data=COUNTS+'''\ncounts<-t(as.matrix(dataset[,paste0("g",1:60)]));colnames(counts)<-dataset$id;metadata<-dataset[,c("id","group","batch")];rownames(metadata)<-metadata$id;taxonomy<-matrix(paste0("genus",1:60),60,1,dimnames=list(rownames(counts),"Genus"));dataset<-phyloseq::phyloseq(phyloseq::otu_table(counts,taxa_are_rows=TRUE),phyloseq::sample_data(metadata),phyloseq::tax_table(taxonomy))'''
    target=tmp_path/'generation'/'object.rds';dummy=tmp_path/'generator.csv';dummy.write_text('x\n1\n')
    generated=backend.run_code(RRunRequest(job_id='omics-fixture',dataset=DatasetRef('seed',str(dummy),'csv'),code=data+'\nsaveRDS(dataset,'+json.dumps(str(target))+')',artifacts_dir=str(tmp_path/'generation'),timeout_seconds=120))
    assert generated.ok,generated.stderr
    ref=DatasetRef('fixture',str(target),'rds','object.rds');inspected=backend.inspect_dataset(ref,timeout_seconds=120)
    assert inspected.ok,inspected.stderr
    profile=dict(inspection_summary(inspected),dataset_id='fixture',format='rds')
    provided=dict(abundance_object='fixture',group='group',sample_id='id',taxonomy_annotations=['Genus'])
    prepared=AnalysisPreparer(AnalysisCatalog()).prepare('microbiome_amplicon',profile,provided)
    assert prepared.status=='ready',repr(prepared)
    _,p=run_omics(backend,tmp_path,'microbiome_amplicon','',{},'list(samples=nrow(alpha),taxa=nrow(differential),annotated=all(differential$Genus==om$annotation$Genus))',prepared_plan=prepared.plan,dataset_ref=ref)
    assert p==dict(samples=24,taxa=60,annotated=True)


@pytest.mark.parametrize('tid',['bulk_rnaseq','wgcna_network'])
def test_real_csv_inspection_preparation_execution(backend,tmp_path,tid):
    from agent.analysis.preparation import AnalysisPreparer
    from r_runtime.inspection import inspection_summary
    path=tmp_path/'matrix.csv';rng=random.Random(1819)
    with path.open('w',newline='') as fh:
        w=csv.writer(fh);w.writerow(['id','group','batch','trait',*FEATURES])
        for i in range(24):
            group=int(i>=12);trait=rng.gauss(0,1)
            values=[rng.randrange(20,120)+(80 if group and j<10 else 0) for j in range(60)] if tid=='bulk_rnaseq' else [rng.gauss(0,1)+trait*(j<30) for j in range(60)]
            w.writerow([f's{i+1}',group,'A' if i%2 else 'B',trait,*values])
    ref=DatasetRef('fixture',str(path),'csv','matrix.csv');inspected=backend.inspect_dataset(ref,timeout_seconds=120)
    assert inspected.ok,inspected.stderr
    profile=dict(inspection_summary(inspected),dataset_id='fixture',format='csv')
    provided=dict(__matrix_orientation__='samples_by_features',data_object='fixture',feature_columns=FEATURES,sample_id='id',group='group')
    if tid=='bulk_rnaseq':provided['batch']='batch'
    else:provided.update(sample_trait='trait',input_scale='log2',min_module_size=10)
    prepared=AnalysisPreparer(AnalysisCatalog()).prepare(tid,profile,provided)
    assert prepared.status=='ready',repr(prepared)
    _,p=run_omics(backend,tmp_path,tid,'',{},'list(features=nrow(om$x),samples=ncol(om$x),orientation=.wc_inputs$orientation_v)',prepared_plan=prepared.plan,dataset_ref=ref)
    assert p==dict(features=60,samples=24,orientation='samples_by_features')


@pytest.mark.parametrize('tid,data,provided,probe',[('bulk_rnaseq',COUNT_SE,dict(data_object='fixture',group='group',batch='batch'),'list(features=nrow(tab),samples=ncol(x))'),('single_cell',CELLS,CELL_INPUTS,'list(cells=nrow(qc),native=ncol(seurat))')])
def test_real_native_object_inspection_preparation_execution(backend,tmp_path,tid,data,provided,probe):
    from agent.analysis.preparation import AnalysisPreparer
    from r_runtime.inspection import inspection_summary
    target=tmp_path/'generation'/'object.rds';dummy=tmp_path/'generator.csv';dummy.write_text('x\n1\n')
    generated=backend.run_code(RRunRequest(job_id='omics-fixture',dataset=DatasetRef('seed',str(dummy),'csv'),code=data+'\nsaveRDS(dataset,'+json.dumps(str(target))+')',artifacts_dir=str(tmp_path/'generation'),timeout_seconds=120))
    assert generated.ok,generated.stderr
    ref=DatasetRef('fixture',str(target),'rds','object.rds');inspected=backend.inspect_dataset(ref,timeout_seconds=120)
    assert inspected.ok,inspected.stderr
    profile=dict(inspection_summary(inspected),dataset_id='fixture',format='rds')
    prepared=AnalysisPreparer(AnalysisCatalog()).prepare(tid,profile,provided)
    assert prepared.status=='ready',repr(prepared)
    _,p=run_omics(backend,tmp_path,tid,'',{},probe,prepared_plan=prepared.plan,dataset_ref=ref)
    assert p==dict(features=60,samples=24) if tid=='bulk_rnaseq' else p==dict(cells=100,native=100)


@pytest.mark.parametrize('representation',['table','matrix'])
def test_cell_observation_axis_distinct_from_donor(backend,tmp_path,representation):
    donors=[f's{i+1}' for i in range(10) for _ in range(10)]
    groups=[0]*50+[1]*50
    types=[]
    for i in range(10):
        nt=(3+i%2) if i<5 else (6+i%2)
        types.extend(['T']*nt+['B']*(10-nt))
    observations=[f'c{i+1}' for i in range(100)]
    metadata=dict(cell=observations,sample=donors,group=groups,type=types)
    data='''set.seed(128);x<-matrix(rpois(20*100,4),20,100,dimnames=list(paste0("g",1:20),paste0("c",1:100)))'''
    inputs=dict(data_object='fixture',sample_id='sample',group='group',cell_type='type',observation_id='cell')
    if representation=='table':
        data+='\nmeta<-as.data.frame(jsonlite::fromJSON('+json.dumps(json.dumps(metadata))+'));dataset<-cbind(meta,as.data.frame(t(x)))'
        inputs['feature_columns']=[f'g{i}' for i in range(1,21)]
    else:
        inputs.update(matrix_orientation='features_by_samples',sample_metadata=json.dumps({k:list(reversed(v)) for k,v in metadata.items()}))
        data+='\ndataset<-x'
    _,p=run_omics(backend,tmp_path,'omics_scrna_differential_abundance',data,inputs,'list(donors=ncol(cellcounts),cells=ncol(om$x),aligned=identical(colnames(om$x),as.character(meta$cell)),T_effect=tab$logFC[tab$cell_type=="T"])')
    assert p['donors']==10 and p['cells']==100 and p['aligned'] and p['T_effect']>0


def test_microbiome_adjusted_and_paired_permanova(backend,tmp_path):
    data=COUNTS+'\ndataset$pair<-rep(paste0("p",1:12),2);set.seed(221);dataset$z<-rnorm(24)'
    inputs=dict(abundance_object='fixture',feature_columns=FEATURES,sample_id='id',group='group',pair_id='pair',covariates=['z'])
    probe='''{set.seed(99);pm<-permute::shuffleSet(n=nrow(meta),nset=10,control=permutation_control);list(pair="pair"%in%rownames(adonis),covariate="z"%in%rownames(adonis),P=adonis[".group","Pr(>F)"],restricted=all(vapply(seq_len(nrow(pm)),function(i)all(meta$pair[pm[i,]]==meta$pair),logical(1))))}'''
    _,p=run_omics(backend,tmp_path,'microbiome_amplicon',data,inputs,probe)
    assert p['pair'] and p['covariate'] and p['restricted'] and 0<=p['P']<=1


def test_spatial_complete_donor_pairing(backend,tmp_path):
    data=COUNTS+'\ndataset$donor<-rep(paste0("d",1:6),each=2,times=2);dataset$x<-rep(1:6,4);dataset$y<-rep(1:4,each=6)'
    inputs=dict(data_object='fixture',feature_columns=FEATURES,sample_id='donor',observation_id='id',coordinates=['x','y'],region='group')
    _,p=run_omics(backend,tmp_path,'omics_spatial_transcriptomics',data,inputs,'list(donors=nrow(coverage),bulk=ncol(pseudobulk),paired=".donor"%in%all.vars(design_info$formula),cells=nrow(spatial))')
    assert p==dict(donors=6,bulk=12,paired=True,cells=24)


@pytest.mark.parametrize('problem',['partial_pairs','inconsistent_covariate'])
def test_spatial_rejects_ambiguous_design(backend,tmp_path,problem):
    data=COUNTS+'\ndataset$x<-rep(1:6,4);dataset$y<-rep(1:4,each=6)'
    inputs=dict(data_object='fixture',feature_columns=FEATURES,sample_id='donor',observation_id='id',coordinates=['x','y'],region='group')
    if problem=='partial_pairs':
        data+='\ndataset$donor<-rep(paste0("d",1:12),2);dataset$donor[13]<-"d13"'
        error='部分配对不能当独立重复'
    else:
        data+='\ndataset$donor<-rep(paste0("d",1:6),each=2,times=2)'
        inputs['batch']='batch'
        error='pseudobulk内批次/配对/协变量必须一致'
    run_omics(backend,tmp_path,'omics_spatial_transcriptomics',data,inputs,expected_error=error)


@pytest.mark.parametrize('tid',['omics_eqtl','omics_variant_burden_annotation','omics_somatic_mutation_cnv'])
def test_optional_annotations_do_not_drop_model_records(backend,tmp_path,tid):
    if tid=='omics_eqtl':
        data=ASSOC+'\ndataset$position<-rep(c("chr1:100",NA_character_),80)'
        inputs=dict(sample_id='id',genotype_variable='dose',expression_variable='expression',covariates=['z'],gene_position='position')
        probe='list(n=nrow(df),missing=tab$missing_position,effect=tab$效应[1],reference=coef(lm(expression~dose+z,dataset))[2])'
        expected_n=160;expected_missing=80
    elif tid=='omics_variant_burden_annotation':
        data=ASSOC+'\ndataset$gene<-"geneA";dataset$annotation<-rep(c("confirmed",NA_character_),80)'
        inputs=dict(sample_id='id',gene_region='gene',dose='dose',phenotype='y',covariates=['z'],functional_annotations=['annotation'])
        probe='list(n=nrow(df),missing=tab$missing_annotation,effect=tab$效应[1],reference=coef(lm(y~dose+z,dataset))[2])'
        expected_n=160;expected_missing=80
    else:
        data=CASES[tid][0]+'\ndataset$tumor<-rep(c("confirmed",NA_character_),20);dataset$annotation<-dataset$tumor'
        inputs=dict(CASES[tid][1],tumor_type='tumor',mutation_annotations=['annotation'])
        probe='list(n=nrow(df),missing=tab$missing_annotation,denominator=tab$samples,frequency=tab$frequency)'
        expected_n=40;expected_missing=[10,10]
    _,p=run_omics(backend,tmp_path,tid,data,inputs,probe)
    assert p['n']==expected_n and p['missing']==expected_missing
    if tid=='omics_somatic_mutation_cnv':assert p['denominator']==[20,20] and p['frequency']==[.3,.3]
    else:assert p['effect']==pytest.approx(p['reference'])


@pytest.mark.parametrize('tid',['proteomics_ms','metabolomics_xcms'])
def test_proteomics_actual_pathway_with_confirmed_mapping(backend,tmp_path,tid):
    inputs=dict(data_object='fixture',feature_columns=FEATURES,group='group',sample_id='id',input_scale='log2',term_gene_mapping=MAP)
    if tid=='metabolomics_xcms':inputs.pop('feature_columns');inputs['data_object']=FEATURES;inputs['group_condition']=inputs.pop('group')
    probe='list(native=class(enrichment$fit),terms=nrow(enrichment$table),proteins=nrow(tab),universe=nrow(normalized))'
    if tid=='proteomics_ms':
        from agent.analysis.preparation import AnalysisPreparer
        from r_runtime.inspection import inspection_summary
        target=tmp_path/'generation'/'proteins.csv';dummy=tmp_path/'generator.csv';dummy.write_text('x\n1\n')
        generated=backend.run_code(RRunRequest(job_id='omics-fixture',dataset=DatasetRef('seed',str(dummy),'csv'),code=CONTINUOUS+'\nwrite.csv(dataset,'+json.dumps(str(target))+',row.names=FALSE)',artifacts_dir=str(tmp_path/'generation'),timeout_seconds=120))
        assert generated.ok,generated.stderr
        ref=DatasetRef('fixture',str(target),'csv','proteins.csv');inspected=backend.inspect_dataset(ref,timeout_seconds=120)
        assert inspected.ok,inspected.stderr
        mapping=json.loads(MAP);inputs['term_gene_mapping']=json.dumps([{'term':t,'gene':g} for t,g in zip(mapping['term'],mapping['gene'])]);inputs['__matrix_orientation__']='samples_by_features'
        profile=dict(inspection_summary(inspected),dataset_id='fixture',format='csv')
        prepared=AnalysisPreparer(AnalysisCatalog()).prepare(tid,profile,inputs)
        assert prepared.status=='ready',repr(prepared)
        result,p=run_omics(backend,tmp_path,tid,'',{},probe,prepared_plan=prepared.plan,dataset_ref=ref)
    else:result,p=run_omics(backend,tmp_path,tid,CONTINUOUS,inputs,probe)
    assert 'enrichResult' in p['native'] and p['terms']>=1 and p['proteins']==60 and p['universe']==60
    actual={a.metadata.get('role') for a in [*result.tables,*result.figures,*result.texts]}
    assert ({'model_results','manuscript_results','pathway_table','pathway_plot'} if tid=='proteomics_ms' else {'analysis_recommendation','manuscript_results','pathway_table','pathway_plot'})<=actual


def test_real_csv_cells_with_repeated_donors_through_preparer(backend,tmp_path):
    from agent.analysis.preparation import AnalysisPreparer
    from r_runtime.inspection import inspection_summary
    path=tmp_path/'cells.csv';rng=random.Random(9903)
    with path.open('w',newline='') as fh:
        w=csv.writer(fh);w.writerow(['cell','sample','group','type',*[f'g{i}' for i in range(1,21)]])
        for i in range(100):
            donor=i//10;group=int(donor>=5);nt=(3+donor%2) if not group else (6+donor%2)
            w.writerow([f'c{i+1}',f's{donor+1}',group,'T' if i%10<nt else 'B',*[rng.randrange(1,15) for _ in range(20)]])
    ref=DatasetRef('fixture',str(path),'csv','cells.csv');inspected=backend.inspect_dataset(ref,timeout_seconds=120)
    assert inspected.ok,inspected.stderr
    profile=dict(inspection_summary(inspected),dataset_id='fixture',format='csv')
    provided=dict(__matrix_orientation__='samples_by_features',data_object='fixture',feature_columns=[f'g{i}' for i in range(1,21)],sample_id='sample',observation_id='cell',group='group',cell_type='type')
    prepared=AnalysisPreparer(AnalysisCatalog()).prepare('omics_scrna_differential_abundance',profile,provided)
    assert prepared.status=='ready',repr(prepared)
    _,p=run_omics(backend,tmp_path,'omics_scrna_differential_abundance','',{},'list(donors=ncol(cellcounts),cells=ncol(om$x),T_effect=tab$logFC[tab$cell_type=="T"])',prepared_plan=prepared.plan,dataset_ref=ref)
    assert p['donors']==10 and p['cells']==100 and p['T_effect']>0


EQTL_LONG=ASSOC+'''
base<-dataset;set.seed(4401)
dataset<-do.call(rbind,lapply(1:4,function(i){d<-base;d$variant<-paste0("v",i);d$gene<-paste0("gene",i);d$expression<-i*.4*d$dose+d$z+rnorm(nrow(d),sd=.5);d$vchr<-c("chr1","chr1","chr2","chr1")[i];d$vpos<-c(100,50000,100,NA_real_)[i];d$gchr<-"chr1";d$gstart<-90;d$gend<-110;d}))
dataset$expression[560]<-NA_real_
'''
EQTL_LONG_INPUTS=dict(sample_id='id',genotype_variable='dose',expression_variable='expression',covariates=['z'],variant_id='variant',gene_id='gene',variant_chromosome='vchr',variant_position='vpos',gene_chromosome='gchr',gene_start='gstart',gene_end='gend',cis_window=10)


def test_eqtl_multi_pair_cis_trans_family_and_actual_qq(backend,tmp_path):
    from agent.analysis.preparation import AnalysisPreparer
    from r_runtime.inspection import inspection_summary
    target=tmp_path/'generation'/'eqtl.csv';dummy=tmp_path/'generator.csv';dummy.write_text('x\n1\n')
    generated=backend.run_code(RRunRequest(job_id='omics-fixture',dataset=DatasetRef('seed',str(dummy),'csv'),code=EQTL_LONG+'\nwrite.csv(dataset,'+json.dumps(str(target))+',row.names=FALSE)',artifacts_dir=str(tmp_path/'generation'),timeout_seconds=120))
    assert generated.ok,generated.stderr
    ref=DatasetRef('fixture',str(target),'csv','eqtl.csv');inspected=backend.inspect_dataset(ref,timeout_seconds=120)
    assert inspected.ok,inspected.stderr
    profile=dict(inspection_summary(inspected),dataset_id='fixture',format='csv')
    prepared=AnalysisPreparer(AnalysisCatalog()).prepare('omics_eqtl',profile,EQTL_LONG_INPUTS)
    assert prepared.status=='ready',repr(prepared)
    probe='''list(pairs=nrow(tab),classification=tab$cis_trans,n=tab$n,family=unique(tab$test_family_size),BH=max(abs(tab$FDR-p.adjust(tab$P,"BH"))),QQ=nrow(qq),effect_error=max(abs(tab$effect-vapply(split(dataset,dataset$variant),function(d)coef(lm(expression~dose+z,d))[2],numeric(1)))),effect_plot=nrow(effect_display),combined_plot=inherits(fig,"patchwork"))'''
    _,p=run_omics(backend,tmp_path,'omics_eqtl','',{},probe,prepared_plan=prepared.plan,dataset_ref=ref)
    assert p['pairs']==4 and p['classification']==['cis','trans','trans','unclassified'] and p['n']==[160,160,160,159]
    assert p['family']==4 and p['BH']==pytest.approx(0) and p['QQ']==4 and p['effect_error']<1e-12
    assert p['effect_plot']==4 and p['combined_plot'] is True


@pytest.mark.parametrize('problem',['inconsistent_position','duplicate_sample'])
def test_eqtl_rejects_ambiguous_pair_data(backend,tmp_path,problem):
    if problem=='inconsistent_position':
        data=EQTL_LONG+'\ndataset$vpos[1]<-200'
        error='确认位置注释不一致'
    else:
        data=EQTL_LONG+'\ndataset<-rbind(dataset,dataset[1,])'
        error='配对内样本ID必须唯一'
    run_omics(backend,tmp_path,'omics_eqtl',data,EQTL_LONG_INPUTS,expected_error=error)


def test_real_enrichment_map_from_shared_genes(backend,tmp_path):
    data='dataset<-data.frame(gene=paste0("g",1:10))'
    mapping=json.dumps({'term':['P1']*10+['P2']*10,'gene':[f'g{i}' for i in range(1,11)]+[f'g{i}' for i in range(6,16)]})
    _,p=run_omics(backend,tmp_path,'pathway_enrichment',data,dict(gene_feature_list='gene',species_id_type='confirmed synthetic/geneID',background_gene_set=[f'g{i}' for i in range(1,81)],term_gene_mapping=mapping),'list(native=class(term_similarity),terms=nrow(tab),shared=overlap_table$shared,JC=overlap_table$jaccard)')
    assert 'enrichResult' in p['native'] and p['terms']==2 and p['shared']==5 and p['JC']==pytest.approx(1/3)


def test_locked_prs_binary_auc_exported_in_performance_table(backend,tmp_path):
    data=ASSOC+'\nset.seed(3134);dataset$y<-rbinom(nrow(dataset),1,plogis(-2+.5*dataset$dose+.5*dataset$dose2))'
    inputs=dict(sample_id='id',phenotype='y',genotype_prs_variable=['dose','dose2'],covariates=['z'],score_weights=[.5,.5])
    _,p=run_omics(backend,tmp_path,'omics_gwas_prs',data,inputs,'list(AUC=tab$AUC[tab$类型=="locked PRS association"],ref=as.numeric(pROC::auc(pROC::roc(df$.y,df$.prs,levels=c(0,1),direction="<",quiet=TRUE))),n=tab$validation_n[tab$类型=="locked PRS association"])')
    assert p['AUC']==pytest.approx(p['ref']) and p['n']==160
