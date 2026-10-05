import json
from pathlib import Path
from uuid import uuid4

from assess_agent.data_inventory import DataInventory,statistics_page
from assess_agent.evidence import EvidenceStore
from assess_agent.parsers import parse_rows
from assess_agent.schemas import Corpus,MaterialInput,Evidence
from assess_agent.settings import CHAPTERS,Settings
from assess_agent.survey import SurveyAnalyzer,frequency
from assess_agent.visuals import VisualRegistry


def linked_store(tmp_path,duplicate=False,leading=False):
    corpus=Corpus();m=MaterialInput(id=uuid4(),name='2025届问卷.xlsx',category='DATA')
    settings=Settings(_env_file=None)
    records=[['用户ID','届次','学院名称','专业名称','您现在是否有工作','您硕士研究生专业与本科专业是否一致']]
    # Employment 5; actual relevant study 5; intentions must be excluded.
    for i in range(15):
        records.append([f'private-{i}',2025,'甲学院','甲专业',
            '受雇全职工作，与专业有关' if i<5 else '正在中国境内读研' if i<10 else '准备中国境内考研','是'])
    if duplicate:records.append(records[1])
    parse_rows(corpus,m,'问卷',records,settings)
    course=[['用户ID','届次','学院名称','课程名称','课程的重要性','课程的重要度','课程是否满足工作或学习要求（1表示满足，0表示不满足）']]
    for i in range(15):
        course.append([f'private-{i}',2025,'甲学院','核心课程','重要' if i%5<3 else '不重要',1 if i%5<3 else 0,
                       1 if i%5<2 else 0])
    course.append(['unmatched',2025,'甲学院','核心课程','重要',1,1])
    if leading:
        course[0]+=['专业名称','一流专业显示名称']
        for row in course[1:]:row+=['甲专业','甲专业']
    parse_rows(corpus,m,'课程',course,settings)
    store=EvidenceStore(corpus);store.survey=SurveyAnalyzer(store,2025,'学校').build()
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,CHAPTERS);store.visuals.plan()
    store.inventory=DataInventory(store,2025).build()
    return store


def test_workbook_join_uses_actual_scope_and_distinct_denominators(tmp_path):
    store=linked_store(tmp_path)
    charts=store.visuals
    data={k:charts.datasets[charts.coverage[k]['datasetId']] for k in ('figure-5-21','figure-5-22','figure-5-23','figure-5-25')}
    assert data['figure-5-21']['series'][0]['values']==[60]
    assert data['figure-5-21']['validSamples']==10
    assert data['figure-5-22']['series'][0]['values']==[66.67]
    assert data['figure-5-22']['validSamples']==6
    assert data['figure-5-23']['validSamples']==5
    assert data['figure-5-25']['validSamples']==5
    result=json.loads(store.read(data['figure-5-21']['sourceIds'][0]).text)
    assert result['unmatchedCourseRecords']==1
    assert len(store.read(data['figure-5-21']['sourceIds'][0]).parent_ids)>1


def test_duplicate_respondents_are_not_implicitly_multiplied(tmp_path):
    store=linked_store(tmp_path,duplicate=True)
    c=store.visuals.coverage['figure-5-21'];data=store.visuals.datasets[c['datasetId']]
    assert data['validSamples']==9
    origin=json.loads(store.read(data['sourceIds'][0]).text)
    assert origin['duplicateRespondentKeys']==1 and origin['unmatchedCourseRecords']==2


def test_leading_major_courses_share_the_verified_relevant_population(tmp_path):
    store=linked_store(tmp_path,leading=True)
    data=store.visuals.datasets[store.visuals.coverage['table-7-9']['datasetId']]
    assert data['rows'][0][1]=='核心课程（66.67%）'
    result=json.loads(store.read(data['sourceIds'][0]).text)
    assert result['detailRows']==[['甲专业','核心课程','66.67','6']]


def test_opaque_joins_are_scoped_and_never_exposed_to_model_tools(tmp_path):
    store=linked_store(tmp_path)
    assert store.corpus.tables[0].record_keys[0]==store.corpus.tables[1].record_keys[0]
    assert all('private-' not in e.text for e in store.corpus.evidence)
    tools={t.name:t for t in store.tools()}
    for name,args in [('list_sources',{}),('inspect_data_coverage',{}),('inspect_table',
        {'material_id':store.corpus.tables[0].material_id,'sheet':'问卷'})]:
        output=json.dumps(tools[name].invoke(args),ensure_ascii=False)
        assert 'private-' not in output and store.corpus.tables[0].record_keys[0] not in output
    m=MaterialInput(id=uuid4(),name='另一本.xlsx',category='DATA');c=Corpus()
    parse_rows(c,m,'问卷',[['用户ID','届次'],['private-0',2025]],Settings(_env_file=None))
    assert c.tables[0].record_keys[0]!=store.corpus.tables[0].record_keys[0]


def test_complete_categories_and_statistics_pages_reconcile(tmp_path):
    values=[str(i) for i in range(75)]+['0']
    result=frequency(values)
    assert len(result['items'])==75 and result['otherCount']==0
    assert sum(v['count'] for v in result['items'])==76
    store=linked_store(tmp_path);table=store.corpus.tables[0]
    computed=store.computed(table,result)
    pages=[statistics_page(store,computed['evidenceId'],offset=i,limit=30) for i in (0,30,60)]
    assert sum(len(p['entries']) for p in pages)==75
    assert pages[-1]['nextOffset'] is None


def test_populated_columns_without_headers_are_retained_and_audited(tmp_path):
    corpus=Corpus();m=MaterialInput(id=uuid4(),name='2025数据.xlsx',category='DATA')
    parse_rows(corpus,m,'后续列',[['学院'],['甲',1],['乙',2]],Settings(_env_file=None))
    assert corpus.tables[0].columns==['学院','列2']
    store=EvidenceStore(corpus);inventory=DataInventory(store,2025).build()
    assert inventory.inspect(column_query='列2')['fields'][0]['validCount']==2


def test_inventory_accounts_for_every_sheet_and_preserves_pooled_scope(tmp_path):
    store=linked_store(tmp_path)
    m=MaterialInput(id=uuid4(),name='本校两届合并.xlsx',category='DATA')
    before=len(store.corpus.evidence)
    parse_rows(store.corpus,m,'专业指标',[['专业','比例'],['甲',80]],Settings(_env_file=None))
    store.by_id.update({e.id:e for e in store.corpus.evidence[before:]})
    inventory=DataInventory(store,2025).build()
    assert len(inventory.summary())==3
    assert '合并' in inventory.summary()[-1]['scope']
    for t in inventory.tables:
        assert sum(t['fields'][i]['validCount']+t['fields'][i]['unansweredCount'] for i in range(t['fieldCount']))==t['scopeRowCount']*t['fieldCount']


def test_mixed_scale_matrix_has_means_and_paired_results(tmp_path):
    corpus=Corpus();m=MaterialInput(id=uuid4(),name='2025能力.xlsx',category='DATA')
    stem='评价能力（分7级展示，从最低水平1到7最高水平）'
    columns=['届次',stem+'（沟通）_重要性',stem+'（沟通）_工作需要水平',stem+'（沟通）_大学毕业掌握水平']
    parse_rows(corpus,m,'能力',[columns,[2025,'重要',5,4],[2025,'不重要',3,4],[2025,'重要',4,''],[2024,'重要',7,1]],Settings(_env_file=None))
    store=EvidenceStore(corpus);analyzer=SurveyAnalyzer(store,2025,'学校').build()
    matrix=next(q for q in analyzer.questions if q['type']=='matrix')
    assert matrix['items'][1]['mean']==4 and matrix['items'][2]['mean']==4
    pair=analyzer.scale_comparisons[0]['items'][0]
    assert pair['validCount']==2 and pair['demandMean']==4 and pair['masteryMean']==4
    assert pair['masteryAtLeastDemandPercentage']==50
    from assess_agent.topic_evidence import topic_packets
    packets=topic_packets(store,'第四章 五育成效')
    assert any(r.get('type')=='paired_scale' for p in packets if '通用能力' in p['subsection'] for r in p['results'])


def test_balanced_school_documents_and_generic_counts_do_not_displace_actual_facts(tmp_path):
    store=linked_store(tmp_path)
    for i,text in enumerate(['教学实践课程'*10,'教学实践改革','教学管理方案','无关内容']):
        source=Evidence(id=f'E-speech-{i}',material_id=f'file-{i}',name=f'讲话{i}',category='SPEECH',location='正文',text=text)
        store.by_id[source.id]=source;store.corpus.evidence.append(source)
    selected=store.balanced_speech('教学实践')
    assert len(selected)==3 and len({s['materialId'] for s in selected})==3
    primary=store.corpus.tables[0]
    actual=Evidence(id='E-actual',material_id=primary.material_id,name='资料',category='DATA',location='正文',text='就业人数80人')
    facts=EvidenceStore(Corpus(evidence=[actual],tables=[primary.model_copy(update={'source_ids':[actual.id]})]))
    facts.computed(facts.corpus.tables[0],{'type':'field_frequency','column':'学校','items':[{'label':'就业','count':1}]})
    assert facts.search('就业')[0]['id']==actual.id
