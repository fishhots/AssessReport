"""Table contracts from the human report, with synthetic source-backed values."""
from copy import deepcopy
import json
from uuid import uuid4

from docx import Document
from docx.oxml.ns import qn
import pytest

from assess_agent.evidence import EvidenceStore
from assess_agent.exporter import add_data_table, export_document
from assess_agent.parsers import parse_rows
from assess_agent.schemas import Corpus, Evidence, MaterialInput, ParsedTable, JobRequest, SectionDraft
from assess_agent.settings import Settings, CHAPTERS
from assess_agent.survey import SurveyAnalyzer
from assess_agent.template_tables import table_template, reference_table_view
from assess_agent.visuals import VisualRegistry
from assess_agent.table_layout import duplicate_statistic_table
from assess_agent.schemas import ContentTable
from tests.test_report_improvements import survey_store


ANNUAL_KEYS=[f'table-1-{i}' for i in (1,2,3,4,5,6,8,9)]+[f'table-7-{i}' for i in range(1,9)]


@pytest.mark.parametrize('key',ANNUAL_KEYS)
def test_single_uploaded_cohort_uses_one_dynamic_column_and_keeps_observed_zero(key):
    source={'templateId':key,'columns':['专业名称','比例（%）'],'rows':[['甲专业','0']]}
    before=deepcopy(source)
    view=reference_table_view(source)
    assert view['columns'][1:]==['2025届']
    assert view['rows']==[['甲专业','0.00']]
    assert source==before


def test_all_target_tables_keep_reference_shapes_when_no_values_available():
    expected_counts={1:[2,2,2,2,2,2,3,2,2,3],5:[8,8,9,7,6,7,6,6,5,5,3],6:[5],
                     7:[2,2,2,2,2,2,2,2,5,6,9,3,2,2]}
    for chapter,counts in expected_counts.items():
        for i,count in enumerate(counts,1):
            view=reference_table_view({'templateId':f'table-{chapter}-{i}','columns':[],'rows':[]})
            assert len(view['columns'])==count
            assert all(len(r)==count and all(v=='—' for v in r[1:]) for r in view['rows'])


def test_historical_distribution_remains_visible_when_current_cohort_is_missing():
    data={'templateId':'table-1-1','columns':['省份','人数','比例（%）'],'rows':[],
          'byCohort':{'2024':{'columns':['省份','人数','比例（%）'],
                             'rows':[['北京','7','70'],['广东','3','30']]}}}
    view=reference_table_view(data)
    assert view['columns'][1:]==['2024届']
    assert view['rows']==[['北京','70.00'],['广东','30.00']]


def test_non_2025_report_uses_uploaded_nonconsecutive_cohorts_only():
    data={'templateId':'table-7-1','reportYear':2026,'cohort':2026,
          'availableCohorts':[2022,2024,2026],
          'columns':['专业名称','毕业去向落实率（%）'],'rows':[['甲专业','90']],
          'byCohort':{'2022':{'columns':['专业名称','毕业去向落实率（%）'],
                              'rows':[['甲专业','70']]}}}
    view=reference_table_view(data)
    assert view['columns']==['专业名称','2022届','2024届','2026届']
    assert view['rows']==[['甲专业','70.00','—','90.00']]


def test_activity_satisfaction_excludes_nonparticipants_and_keeps_distinct_experiments(tmp_path):
    stem='您对以下实习和实践环节是否满意'
    cols=['届次','专业大类名称',stem+'（课程实验）',stem+'（课程实验（项目））',stem+'（毕业论文（设计））']
    store,_=survey_store(cols,[['2025','理学','满意','不满意','满意']]*5+
                            [['2025','理学','未参加','未参加','未参加']]*2)
    registry=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[5]]);registry.plan()
    view=reference_table_view(registry.datasets[registry.coverage['table-5-2']['datasetId']])
    assert {(r[4],r[5]) for r in view['rows']}=={('课程实验','100.00'),('课程实验（项目）','0.00'),('毕业论文/设计','100.00')}


def test_merged_group_cell_has_one_label_and_empty_continuations():
    doc=Document()
    view=reference_table_view({'templateId':'table-5-5','columns':['职业','任务','总是','经常','偶尔','从不'],
        'rows':[['工程技术类','任务甲','10','20','30','40'],['工程技术类','任务乙','0','30','30','40'],
                ['行政管理类','任务丙','0','0','0','100']]})
    table=add_data_table(doc,view['columns'],view['rows'],view['headerGroups'],'table-5-5')
    cells=table._tbl.findall(qn('w:tr'))
    merged=cells[1].findall(qn('w:tc'))[0]
    continued=cells[2].findall(qn('w:tc'))[0]
    assert ''.join(node.text or '' for node in merged.iter(qn('w:t'))).count('工程技术类')==1
    assert not list(continued.iter(qn('w:t')))
    assert merged.find(qn('w:tcPr')).find(qn('w:vMerge')).get(qn('w:val'))=='restart'
    assert continued.find(qn('w:tcPr')).find(qn('w:vMerge')) is not None
    assert cells[3].findall(qn('w:tc'))[0].find(qn('w:tcPr')).find(qn('w:vMerge')) is None


def test_region_rows_keep_province_and_cohorts_with_major_name_merged_once():
    data={'templateId':'table-7-12','columns':['专业','省份','比例（%）','有效人数'],
          'rows':[['甲专业','北京','75','8'],['甲专业','广东','25','8']],
          'byCohort':{'2024':{'columns':['专业','省份','比例（%）','有效人数'],
                             'rows':[['甲专业','北京','60','10']]}}}
    view=reference_table_view(data)
    assert view['columns']==['专业名称','主要就业省份','2024届','2025届']
    assert view['rows']==[['甲专业','北京','60.00','75.00'],['甲专业','广东','—','25.00']]
    table=add_data_table(Document(),view['columns'],view['rows'],template_id='table-7-12')
    assert table.cell(1,0).text=='甲专业' and table.cell(2,1).text=='广东'


def test_textbook_labels_order_and_construction_metrics_are_exact():
    view=reference_table_view({'templateId':'table-5-10','columns':['评价','非常符合（%）','符合（%）','不符合（%）','非常不符合（%）'],
        'rows':[['教材先进性','10','20','30','40'],['教材实用性','20','30','40','10']]})
    assert view['rows']==[['课程教材实用性','20.00','30.00','40.00','10.00'],['课程教材先进性','10.00','20.00','30.00','40.00']]
    assert view['columns']==['选项','非常符合','符合','不符合','非常不符合']
    assert table_template('table-5-11')['columns']==['学院名称','课程教材实用性','课程教材先进性']
    assert table_template('table-6-1')['columns']==['学院名称','一网通办，提高学生管理与服务效率','能获得丰富的数字资源','提升学生信息技术应用能力','能满足个性化学习，提升学习体验']


def test_uploaded_wide_teacher_metrics_use_named_columns_without_recomputing():
    source={'templateId':'table-7-10','columns':['专业名称','教师指导总体满足度（%）','师生高频交流的比例（%）','学习指导（%）','职业规划（%）','就业指导（%）'],
            'rows':[['甲专业','85','50','90','80','70']]}
    assert reference_table_view(source)['rows']==[['甲专业','85.00','50.00','90.00','80.00','70.00']]


def test_explicit_overall_teacher_indicator_is_joined_with_source_provenance(tmp_path):
    stem='本专业的教师提供的以下课外指导'
    cols=['届次','学院名称',stem+'（学习指导）',stem+'（职业规划）',stem+'（就业指导）']
    store,_=survey_store(cols,[['2025','甲学院','满足','满足','满足']]*6)
    material=str(uuid4());eid='E-overall'
    evidence=Evidence(id=eid,material_id=material,name='2025届指导指标.xlsx',category='DATA',location='学院',text='教师指导总体满足度85%')
    aggregate=ParsedTable(material_id=material,name=evidence.name,sheet='学院',columns=['学院名称','教师指导总体满足度（%）'],
        rows=[['甲学院','85'],['本校平均','85']],start_row=2,source_ids=[eid])
    store.corpus.evidence.append(evidence);store.by_id[eid]=evidence;store.corpus.tables.append(aggregate)
    registry=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[5]]);registry.plan()
    data=registry.datasets[registry.coverage['table-5-8']['datasetId']]
    assert next(r for r in reference_table_view(data)['rows'] if r[0]=='甲学院')[1]=='85.00'
    assert eid in store.read(data['sourceIds'][0]).parent_ids


def scoped_courses(tmp_path):
    corpus=Corpus();material=MaterialInput(id=uuid4(),name='2025届课程问卷.xlsx',category='DATA')
    settings=Settings(_env_file=None)
    questions=[['用户ID','届次','学院名称','专业名称','一流专业显示名称','您现在是否有工作','您硕士研究生专业与本科专业是否一致']]
    courses=[['用户ID','届次','学院名称','专业名称','一流专业显示名称','课程名称','课程的重要性','课程的重要度','课程是否满足工作或学习要求（1表示满足，0表示不满足）']]
    for i in range(25):
        employment=i<10;study=10<=i<20
        questions.append([f'p{i}',2025,'甲学院','甲专业','甲专业',
            '受雇全职工作，与专业有关' if employment else '正在中国境内读研' if study else '准备中国境内考研','是'])
        important=i<8 if employment else i<15 if study else True
        satisfied=i<6 if employment else True
        courses.append([f'p{i}',2025,'甲学院','甲专业','甲专业','核心课',
            '重要' if important else '不重要',1 if important else 0,1 if satisfied else 0])
    parse_rows(corpus,material,'问卷',questions,settings)
    parse_rows(corpus,material,'课程',courses,settings)
    store=EvidenceStore(corpus);store.survey=SurveyAnalyzer(store,2025,'测试大学').build()
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[5],CHAPTERS[7]])
    store.visuals.plan()
    return store


def test_course_tables_use_two_independent_populations_and_conditional_denominators(tmp_path):
    store=scoped_courses(tmp_path)
    for key,label in [('table-5-9','甲学院'),('table-7-9','甲专业')]:
        registry=store.visuals;data=registry.datasets[registry.coverage[key]['datasetId']]
        view=reference_table_view(data)
        assert view['columns'][1:]==['重要度','满足度','重要度','满足度']
        assert view['headerGroups'][1:]==[(1,2,'对口就业/对口读研人群'),(3,2,'对口就业人群')]
        assert view['rows']==[['本校平均','65.00','84.62','80.00','75.00'],[label,'65.00','84.62','80.00','75.00']]
        facts=json.loads(store.read(data['sourceIds'][0]).text)
        assert facts['scopedRecordCounts'][label]==[[20,13],[10,8]]
        asset=registry.render(data['datasetId']);assert registry.validate(asset['assetId'])['valid']


def test_explicit_occupation_taxonomy_drives_frequency_and_application_tables(tmp_path):
    task='结合您所从事的职业，请评价在以下工作任务中使用AI技术/工具的频率（设计方案）'
    app='您在工作中使用AI工具/技术的主要应用场景（创新性使用AI工具解决工作问题（如内容生成、创新研发等））'
    task2=task.replace('设计方案','测试方案');app2='您在工作中使用AI工具/技术的主要应用场景（其他）'
    store,_=survey_store(['届次','您现在从事的职业一级分类','您现在从事的职业类',task,task2,app,app2],
        [['2025','工程技术类','职业'+str(i),'经常','偶尔','1','0'] for i in range(6)])
    registry=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[5]]);registry.plan()
    for key in ('table-5-4','table-5-5'):
        data=registry.datasets[registry.coverage[key]['datasetId']]
        view=reference_table_view(data)
        assert any(r[0]=='工程技术类' for r in view['rows'])
        assert '职业0' not in str(view['rows'])
        if key=='table-5-4':
            assert next(r for r in view['rows'] if r[0]=='工程技术类')[1]=='100.00'
            assert '其他' not in view['columns']
    assert ['工程技术类','设计方案','0.00','100.00','0.00','0.00'] in reference_table_view(registry.datasets[registry.coverage['table-5-5']['datasetId']])['rows']


def test_real_historical_major_values_join_without_splitting_pooled_values(tmp_path):
    store,_=survey_store(['届次','学院名称','专业名称','一流专业显示名称'],
                        [['2025','甲学院','甲专业','甲专业']]*6+[['2024','甲学院','甲专业','甲专业']]*6)
    for year,rate,name in [(2025,'90','2025届指标.xlsx'),(2024,'80','2024届指标.xlsx'),(None,'99','2024届2025届合并.xlsx')]:
        material=str(uuid4());eid='E-'+material
        evidence=Evidence(id=eid,material_id=material,name=name,category='DATA',location='指标',text='上传聚合指标')
        table=ParsedTable(material_id=material,name=name,sheet='专业指标',
            columns=['学院名称','专业名称','毕业去向落实率（%）'],rows=[['甲学院','甲专业',rate],['本校平均','本校平均',rate]],
            start_row=2,source_ids=[eid])
        store.corpus.evidence.append(evidence);store.by_id[eid]=evidence;store.corpus.tables.append(table)
    registry=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[7]]);registry.plan()
    data=registry.datasets[registry.coverage['table-7-1']['datasetId']]
    view=reference_table_view(data)
    assert view['columns']==['专业名称','2024届','2025届']
    assert view['rows']==[['本校平均','80.00','90.00'],['甲专业','80.00','90.00']]
    assert data['availableCohorts']==[2024,2025]
    assert set(data['byCohort'])=={'2024','2025'}
    assert all(r[1]!='99' for version in data['byCohort'].values() for r in version['rows'])
    asset=registry.render(data['datasetId']);assert registry.validate(asset['assetId'])['valid']
    root=json.loads((tmp_path/'visual-assets.json').read_text(encoding='utf-8'))
    assert root['datasets'][data['datasetId']]['cohort']==2025


def test_destinations_use_concrete_names_and_pool_counts_only_when_both_cohorts_exist(tmp_path):
    columns=['届次','学院名称','专业名称','一流专业显示名称','您现在是否有工作','您现在就业的行业类','您现在就业的行业名称','您现在从事的职业名称']
    rows=[['2025','甲学院','甲专业','甲专业','受雇全职工作，与专业有关','信息业','软件开发业','软件开发人员']]*6
    rows += [['2024','甲学院','甲专业','甲专业','受雇全职工作，与专业有关','信息业','互联网平台服务业','互联网开发人员']]*9
    store,_=survey_store(columns,rows)
    registry=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[1],CHAPTERS[7]]);registry.plan()
    for key in ('table-1-7','table-7-13'):
        data=registry.datasets[registry.coverage[key]['datasetId']]
        assert data['pooledCohorts'] and data['cohorts']==[2024,2025]
        assert reference_table_view(data)['rows'][0][-1]=='互联网平台服务业'
        asset=registry.render(data['datasetId']);assert registry.validate(asset['assetId'])['valid']


def test_historical_destination_sheet_can_have_different_unrelated_columns(tmp_path):
    from assess_agent.report_statistics import destination_population
    columns=['届次','学院名称','专业名称','您现在是否有工作','您现在就业的行业名称','2025新增题']
    store,current=survey_store(columns,[['2025','甲','甲专业','受雇全职工作，与专业有关','软件开发业','回答']]*6)
    material=str(uuid4());eid='E-history'
    evidence=Evidence(id=eid,material_id=material,name='2024届原始数据.xlsx',category='DATA',location='原始数据',text='历史真实记录')
    history=ParsedTable(material_id=material,name=evidence.name,sheet='原始数据',columns=columns[:-1],
        rows=[['2024','甲','甲专业','受雇全职工作，与专业有关','互联网平台服务业']]*8,start_row=2,source_ids=[eid])
    store.corpus.evidence.append(evidence);store.by_id[eid]=evidence;store.corpus.tables.append(history)
    registry=VisualRegistry(store,tmp_path,2025,[CHAPTERS[1]])
    joint,rows,years=destination_population(registry,current,'您现在就业的行业名称')
    assert years==[2024,2025] and len(rows)==14
    assert rows[0][-1]=='' and rows[-1][-1]=='回答'
    assert eid in joint.source_ids


def test_all_singleton_destinations_are_kept_in_details_not_a_long_primary_cell(tmp_path):
    columns=['届次','学院名称','专业名称','您现在是否有工作','您现在从事的职业名称']
    rows=[['2025','甲学院','集中专业','受雇全职工作，与专业有关','开发人员']]*3
    rows += [['2025','甲学院','集中专业','受雇全职工作，与专业有关','审计人员']]*3
    rows += [['2025','甲学院','分散专业','受雇全职工作，与专业有关','岗位'+str(i)] for i in range(8)]
    store,_=survey_store(columns,rows);registry=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[1]]);registry.plan()
    data=registry.datasets[registry.coverage['table-1-10']['datasetId']]
    view=reference_table_view(data)
    assert view['rows']==[['甲学院','集中专业','审计人员'],['甲学院','集中专业','开发人员']]
    source=json.loads(store.read(data['sourceIds'][0]).text)
    assert len([r for r in source['detailRows'] if r[1]=='分散专业'])==8
    table=add_data_table(Document(),view['columns'],view['rows'],template_id='table-1-10')
    assert table.cell(1,0).text=='甲学院' and table.cell(1,1).text=='集中专业'
    assert all(not ''.join(n.text or '' for n in cell.iter(qn('w:t'))) for cell in table._tbl.findall(qn('w:tr'))[2].findall(qn('w:tc'))[:2])


def test_redundant_distribution_and_combined_major_tables_do_not_reappear():
    datasets=[{'type':'table','templateId':'table-1-1','columns':['省份','人数','比例（%）'],
               'rows':[['北京','7','70'],['广东','3','30']]},
              {'type':'table','templateId':'table-7-1','columns':['专业名称','毕业去向落实率（%）'],'rows':[['甲专业','90']]},
              {'type':'table','templateId':'table-7-3','columns':['专业名称','月收入（元）'],'rows':[['甲专业','8000']]}]
    table=ContentTable(title='就业地域',columns=['就业地区','比例（%）'],rows=[['北京','70.00']],source_ids=['C-old'])
    assert duplicate_statistic_table(table,datasets)
    table=ContentTable(title='一流专业综合比较',columns=['学院','专业','毕业去向落实率（%）','月收入（元）'],
                       rows=[['甲学院','甲专业','90','8000']],source_ids=['C-old'])
    assert duplicate_statistic_table(table,datasets)
    table=ContentTable(title='一流专业综合比较',columns=['学院','专业','工作与专业相关度（%）'],rows=[['甲学院','甲专业','80']],source_ids=['C-old'])
    assert duplicate_statistic_table(table,[*datasets,{'type':'table','templateId':'table-7-2','columns':['专业名称','专业相关度（%）'],'rows':[['甲专业','80']]}])
    figure={'templateId':'figure-1-3','type':'bar','labels':['甲学院'],'series':[{'name':'毕业去向落实率','values':[90]}],'benchmarks':[80]}
    table=ContentTable(title='各学院落实率',columns=['学院','毕业去向落实率（%）'],rows=[['甲学院','90.00'],['本校平均','80']],source_ids=['C-old'])
    assert duplicate_statistic_table(table,[figure])
    table.columns[1]='就业满意度（%）'
    assert not duplicate_statistic_table(table,[figure])
    table.columns[1]='毕业去向落实率（元）'
    assert not duplicate_statistic_table(table,[figure])
    table=ContentTable(title='课程改进行动',columns=['专业','措施','目标'],rows=[['甲专业','增加实践环节','提升应用能力']],source_ids=['C-old'])
    assert not duplicate_statistic_table(table,datasets)
    table.title='根据表7-1提出课程改进行动'
    assert not duplicate_statistic_table(table,datasets)

    stacked={'templateId':'figure-5-6','type':'stacked_bar','unit':'%',
             'labels':['课程内容','实践教学'],
             'series':[{'name':'符合','values':[60,70]},{'name':'非常符合','values':[30,20]},
                       {'name':'不符合','values':[10,10]}]}
    table=ContentTable(title='合作课程评价原始数据',
        columns=['评价项目','符合（%）','非常符合（%）','不符合（%）'],
        rows=[['课程内容','60','30','10'],['实践教学','70','20','10']],source_ids=['C-old'])
    assert duplicate_statistic_table(table,[stacked])


def test_missing_ability_tables_are_exported_without_registering_fake_statistics(tmp_path):
    store,_=survey_store(['届次','一流专业显示名称'],[['2025','甲专业']]*6)
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[7]]);store.visuals.plan()
    section=SectionDraft(title=CHAPTERS[7],blocks=[{'kind':'gap','text':'能力算法尚待确认。','subsection':'毕业要求 / 通用能力达成度'}])
    request=JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='deepseek',chapters=[CHAPTERS[7]],
        materials=[MaterialInput(id=uuid4(),name='2025数据.xlsx',category='DATA')])
    path=tmp_path/'report.docx';export_document(request,[section],store,path,'毕业生培养质量评价报告',[CHAPTERS[7]])
    doc=Document(path)
    assert all(store.visuals.coverage[f'table-7-{i}']['status']=='missing' for i in (5,6,7))
    texts=[p.text for p in doc.paragraphs]
    assert all(any(p.startswith(f'表7-{i} ') for p in texts) for i in (5,6,7))
    matrices=[t for t in doc.tables if [c.text for c in t.rows[0].cells]==['专业名称','2025届']]
    assert len(matrices)>=3 and all(t.cell(1,1).text=='—' for t in matrices)


def test_professional_table_labels_hide_internal_faculty_qualifiers():
    from assess_agent.template_tables import annual_view, course_view, region_view, table_template

    annual={'columns':['专业名称','毕业去向落实率（%）'],
            'rows':[['甲专业（甲学院）','90'],['甲专业（乙学院）','80']]}
    rows=annual_view(annual,table_template('table-7-1'), 'table-7-1',2025)
    assert [row[0] for row in rows]==['甲专业','甲专业']

    course={'columns':['一流专业显示名称','对口就业/对口读研重要度','对口就业/对口读研满足度','对口就业重要度','对口就业满足度'],
            'rows':[['甲学院 / 甲专业','80','90','75','88']]}
    assert course_view(course,table_template('table-7-9'))[0][0]=='甲专业'

    region={'columns':['专业名称','主要就业省份','比例（%）','有效回答人数'],
            'rows':[['甲学院 / 甲专业','北京','100','5']]}
    assert region_view(region,table_template('table-7-12'),2025)[0][0]=='甲专业'
