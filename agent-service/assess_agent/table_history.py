"""Collect available single-cohort tables without splitting pooled indicators."""
import re

from .evidence import EvidenceStore
from .survey import SurveyAnalyzer
from .template_tables import ANNUAL_METRICS, DISTRIBUTIONS

HISTORICAL_TABLES=ANNUAL_METRICS | DISTRIBUTIONS | {'table-7-12'}


def available_table_cohorts(registry):
    years=set()
    for table in registry.evidence.corpus.tables:
        if not table.source_ids or any(registry.evidence.read(k).category!='DATA' for k in table.source_ids):continue
        if '届次' in table.columns:
            i=table.columns.index('届次')
            years.update(int(r[i]) for r in table.rows if re.fullmatch(r'20\d{2}',r[i]))
        elif '合并' not in table.name+table.sheet:
            declared=set(re.findall(r'20\d{2}',table.name+table.sheet))
            if len(declared)==1:years.add(int(next(iter(declared))))
    return sorted(y for y in years if y<=registry.year)


def attach_table_history(registry):
    from .visuals import VisualRegistry
    current={d['templateId']:d for d in registry.datasets.values() if d['type']=='table'}
    histories={}
    uploaded_cohorts=available_table_cohorts(registry)
    for year in uploaded_cohorts:
        if year==registry.year:continue
        if registry.evidence.cancel_check:registry.evidence.cancel_check()
        corpus=registry.evidence.corpus.model_copy(update={'evidence':list(registry.evidence.corpus.evidence),
                                                         'tables':list(registry.evidence.corpus.tables)})
        evidence=EvidenceStore(corpus)
        evidence.cancel_check=registry.evidence.cancel_check
        evidence.survey=SurveyAnalyzer(evidence,year,getattr(registry.evidence.survey,'school','')).build()
        historical=VisualRegistry(evidence,registry.directory/'table-history'/str(year)/'visuals',year,registry.chapters)
        historical.plan(include_table_history=False)
        for item in evidence.corpus.evidence:
            if item.id not in registry.evidence.by_id:
                registry.evidence.corpus.evidence.append(item);registry.evidence.by_id[item.id]=item
        for data in historical.datasets.values():
            if data['type']=='table' and data['templateId'] in HISTORICAL_TABLES:
                histories.setdefault(data['templateId'],{})[str(year)]=data
    keys=set(histories) | (set(current) & HISTORICAL_TABLES)
    for key in keys:
        older=histories.get(key,{})
        original=current.get(key)
        base=original or older[max(older)]
        versions={**older,**({str(registry.year):original} if original else {})}
        data={k:v for k,v in base.items() if k!='datasetId'}
        data['availableCohorts']=uploaded_cohorts
        if not original:
            data.update(cohort=registry.year,labels=[],rows=[['—']*len(data['columns'])])
        data['byCohort']={y:{k:v for k,v in d.items() if k in ('columns','rows','cohort','basis','sourceIds')}
                          for y,d in versions.items()}
        parents=[d['datasetId'] for d in versions.values()]
        source=registry.evidence.read(base['binding']['questionEvidenceId'])
        sheet=re.match(r'工作表 (.*?)，原始行 ',source.location)
        if sheet is None:continue
        table=registry.evidence.table(source.material_id,sheet[1])
        result=registry.evidence.computed(table,data)
        registry.evidence.read(result['evidenceId']).parent_ids=parents
        data['datasetId']=result['evidenceId'];registry.datasets[result['evidenceId']]=data
        registry.coverage[key].update(status='prepared',reason='',datasetId=result['evidenceId'])
