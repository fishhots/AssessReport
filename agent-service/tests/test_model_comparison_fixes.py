import json
from uuid import uuid4

import pytest
from docx import Document

from assess_agent.evidence import EvidenceStore
from assess_agent.errors import AgentError
from assess_agent.exporter import chart_dimensions, export_document
from assess_agent.report_style import qualitative_lead
from assess_agent.schemas import ContentBlock, Corpus, CustomTopic, Evidence, JobRequest, ParsedTable, SectionDraft
from assess_agent.settings import CHAPTERS
from assess_agent.survey import SurveyAnalyzer
from assess_agent.topic_evidence import compact_result
from assess_agent.validation import document_paths, subsection_paths, validate_section, extension_path, valid_subsection
from assess_agent.visuals import VisualRegistry
from tests.test_report_improvements import survey_store


def test_thematic_custom_subsections_survive_validation_and_export(tmp_path):
    store, table = survey_store(['届次', '人数'], [['2025', '80']])
    key = table.source_ids[0]
    store.by_id[key].text = '就业人数80人。'
    draft = SectionDraft(title='第十章 AI人才培养方向', blocks=[
        {'kind':'finding','text':'就业人数80人。','source_ids':[key],'subsection':'一、现状基础'},
        {'kind':'recommendation','text':'建议开展综合项目，以作品和答辩评价学习成果。','subsection':'二、阶段路径 / 项目评价'},
        {'kind':'gap','text':'课程映射需核对课程大纲。','subsection':'数据缺口说明'}])
    assert validate_section(draft, draft.title, store, 2025) == []
    request = JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='glm',chapters=[CHAPTERS[1]],
        materials=[{'id':table.material_id,'name':table.name,'category':'DATA'}])
    output = tmp_path/'topic.docx'
    export_document(request,[draft],store,output,'毕业生培养质量评价报告',[draft.title])
    doc = Document(output)
    assert [p.text for p in doc.paragraphs if p.style.name in ('Heading 2','Heading 3')] == ['现状基础','阶段路径','项目评价']
    assert any('作品和答辩' in p.text for p in doc.paragraphs)
    assert '数据缺口说明' not in [p.text for p in doc.paragraphs if p.style.name.startswith('Heading ')]
    formal = draft.model_copy(update={'title':CHAPTERS[1]})
    assert any('原小节' in issue for issue in validate_section(formal, formal.title, store, 2025))


def test_personalized_nested_headings_fit_after_merge_without_duplicate_dimension():
    from assess_agent.settings import DIAGNOSIS_TITLES
    title=DIAGNOSIS_TITLES[0]
    for path in ('现状 / 核心依据',title+' / 现状 / 核心依据'):
        assert valid_subsection(title,path)
        assert extension_path(title,path)==title+' / 现状 / 核心依据'
        assert valid_subsection('第九章 个性化报告',extension_path(title,path))
    assert not valid_subsection(title,'现状 / 依据 / 补充说明')


def test_missing_content_preserves_every_original_heading(tmp_path):
    store, table = survey_store(['届次','人数'],[['2025','80']])
    request = JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='qwen',chapters=[CHAPTERS[1]],
        materials=[{'id':table.material_id,'name':table.name,'category':'DATA'}])
    draft=SectionDraft(title=CHAPTERS[1],blocks=[{'kind':'gap','text':'个别指标的计算规则待明确。'}])
    output=tmp_path/'fixed.docx'
    export_document(request,[draft],store,output,'毕业生培养质量评价报告',[draft.title])
    expected=[p.split(' / ')[-1] for p in subsection_paths(draft.title)]
    actual=[p.text for p in Document(output).paragraphs if p.style.name in ('Heading 2','Heading 3','Heading 4')]
    assert actual == expected


def test_summary_is_a_short_judgement_and_never_bolds_a_metric_list():
    block=ContentBlock(kind='finding',text='教学满意度92.57%，岗位适应性92.21%。',source_ids=['E-source'],
        summary='教学与岗位适应表现较好，实践支撑仍需加强。')
    lead, remainder=qualitative_lead(block,block.text)
    assert lead == block.summary and remainder == block.text
    block.summary=''
    assert qualitative_lead(block,block.text) == ('',block.text)
    block.text='人才培养定位落实较好。教学满意度为92.57%。'
    assert qualitative_lead(block,block.text) == ('人才培养定位落实较好。','教学满意度为92.57%。')
    block.summary='教学满意度为92.57%。'
    store,_=survey_store(['届次'],[['2025']])
    assert any('summary' in issue for issue in validate_section(SectionDraft(title=CHAPTERS[1],blocks=[block]),CHAPTERS[1],store,2025))


def test_noneditorial_summary_moves_to_body_and_still_requires_verified_numbers():
    block=ContentBlock(kind='finding',summary='教学满意度92.57%，岗位适应性92.21%。',text='两项反馈存在差异。',source_ids=['E-source'])
    assert block.summary=='' and block.text.startswith('教学满意度92.57%')
    store,_=survey_store(['届次'],[['2025']])
    assert any('数字未能' in issue for issue in validate_section(SectionDraft(title=CHAPTERS[1],blocks=[block]),CHAPTERS[1],store,2025))


def test_small_statistic_tables_are_complete_and_large_samples_are_marked():
    source=Evidence(id='C-source',material_id=str(uuid4()),name='统计',category='DATA',location='统计',computed=True,
        text=json.dumps({'rows':[[str(i)] for i in range(14)],'columns':['专业']}))
    result=compact_result(source)
    assert result['rowsCount']==14 and result['rowsComplete'] and len(result['rows'])==14
    source.text=json.dumps({'rows':[[str(i)] for i in range(30)],'columns':['专业']})
    result=compact_result(source)
    assert result['rowsCount']==30 and not result['rowsComplete'] and len(result['rows'])==8


@pytest.mark.parametrize('kind,ratio,max_height', [('pie',1,8),('donut',1.5,8),('line',.8,8.5),('horizontal_bar',.6,11)])
def test_chart_physical_height_is_bounded(kind,ratio,max_height):
    width,height=chart_dimensions(kind,1000*ratio,1000)
    assert height <= max_height and width <= 15.3
    if kind in ('pie','donut'): assert width<=8


def test_chart_display_label_omits_survey_navigation_only():
    from assess_agent.chart_render import display_label
    assert display_label('无工作，继续寻找工作（求职中；如果在签约中请选择其他）')=='继续寻找工作'
    assert display_label('信息传输、软件和信息技术服务业')=='信息传输、软件和信息技术服务业'


def test_caption_does_not_describe_current_cohort_as_two_pooled_cohorts():
    from assess_agent.exporter import visual_caption
    original='表7-13 主要行业（2024 届、2025 届合并）'
    assert visual_caption(original,{'cohort':2025,'pooledCohorts':False})=='表7-13 主要行业（2025届）'
    assert visual_caption(original,{'cohort':None,'pooledCohorts':True})==original


def test_course_summary_is_based_on_course_records_and_declared_membership(tmp_path):
    rows=[['2025','软件工程','系统设计','1']]*6+[ ['2025','软件工程','数据库','0']]*5
    rows += [['2025','软件工程','少量课程','0']]*2
    store,_=survey_store(['届次','一流专业显示名称','课程名称','课程是否满足工作或学习要求（1表示满足，0表示不满足）'],rows)
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[7]]);store.visuals.plan()
    data=store.visuals.datasets[store.visuals.coverage['table-7-9']['datasetId']]
    assert len(data['rows'])==1 and '系统设计（100%' in data['rows'][0][1]
    assert '少量课程' not in str(data['rows']) and '记录' in data['basis']


def test_filtered_service_expectations_exclude_other_branches(tmp_path):
    store,_=survey_store(['届次','您现在是否有工作','希望学校提供哪些考研服务（自习室）','希望学校提供哪些考研服务（资料）',
        '您希望学校提供何种求职服务（面试）','您希望学校提供何种求职服务（招聘）'],
        [['2025','准备中国境内考研','1','0','','']]*6+
        [['2025','正在中国境内读研','0','1','','']]*9+
        [['2025','无工作，继续寻找工作','0','0','1','0']]*6)
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[1]])
    store.visuals.plan()
    for key in ('figure-1-7','figure-1-9'):
        data=store.visuals.datasets[store.visuals.coverage[key]['datasetId']]
        assert data['validSamples']==6 and data['series'][0]['values']==[100,0]


def test_region_uses_work_not_origin_and_unknown_city_stays_in_denominator(tmp_path):
    store,_=survey_store(['届次','您现在是否有工作','工作单位境内所在地','原毕业高中所在地','您现在就业的行业类','您现在从事的职业类'],
        [['2025','受雇全职工作，与专业有关','北京海淀区','广东广州','信息业','软件']]*6+
        [['2025','受雇全职工作，与专业有关','河北保定市','北京','制造业','机械']]*2+
        [['2025','受雇全职工作，与专业有关','广东深圳南山区','北京','信息业','软件']]*2+
        [['2025','正在中国境内读研','北京海淀区','北京','信息业','软件']]*10)
    store.survey.school='北京测试大学'
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[1]])
    store.visuals.plan()
    city=store.visuals.datasets[store.visuals.coverage['table-1-2']['datasetId']]
    assert ['北京','6','60.0'] in city['rows'] and ['原始地点尚不能分类','2','20.0'] in city['rows']
    local=store.visuals.datasets[store.visuals.coverage['table-1-3']['datasetId']]
    assert local['rows']==[['信息业','6','100.0']]


def test_major_metric_join_uses_faculty_and_excludes_reference_and_pooled(tmp_path):
    store, table=survey_store(['届次','学院名称','专业名称','一流专业显示名称'],[['2025','甲学院','软件工程','软件工程']]*6)
    key='E-metrics';material=str(uuid4())
    evidence=Evidence(id=key,material_id=material,name='2025届主要指标.xlsx',category='DATA',location='指标',text='甲学院软件工程90；乙学院软件工程70')
    metrics=ParsedTable(material_id=material,name=evidence.name,sheet='专业指标',columns=['学院名称','专业名称','毕业去向落实率（%）'],
        rows=[['甲学院','软件工程','90'],['乙学院','软件工程','70']],start_row=2,source_ids=[key])
    store.corpus.evidence.append(evidence);store.by_id[key]=evidence;store.corpus.tables.append(metrics)
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[7]])
    store.visuals.plan()
    data=store.visuals.datasets[store.visuals.coverage['table-7-1']['datasetId']]
    assert data['rows']==[['甲学院','软件工程','90']]
    assert data['validSamples'] is None
    statistic=store.read(data['binding']['questionEvidenceId'])
    assert any(store.read(k).computed for k in statistic.parent_ids)
    for invalid in ('REFERENCE','pooled'):
        evidence.category='REFERENCE' if invalid=='REFERENCE' else 'DATA'
        metrics.name='两届合并.xlsx' if invalid=='pooled' else '2025届主要指标.xlsx'
        registry=VisualRegistry(store,tmp_path/invalid,2025,[CHAPTERS[7]]);registry.plan()
        assert registry.coverage['table-7-1']['status']=='missing'


def test_leading_major_questionnaire_does_not_merge_same_named_faculties(tmp_path):
    store,_=survey_store(['届次','学院名称','专业名称','一流专业显示名称','请您对学校的教学满意程度'],
        [['2025','甲学院','软件工程','软件工程','满意']]*6+
        [['2025','乙学院','软件工程','软件工程','不满意']]*5)
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[7]]);store.visuals.plan()
    data=store.visuals.datasets[store.visuals.coverage['table-7-8']['datasetId']]
    # Negative-only groups are still valid; zero is a real observed proportion.
    assert ['甲学院 / 软件工程','请您对学校的教学满意程度','100.0','6'] in data['rows']
    assert ['乙学院 / 软件工程','请您对学校的教学满意程度','0.0','5'] in data['rows']
    assert not any(row[0]=='软件工程' for row in data['rows'])


def test_all_gap_custom_topic_is_not_reported_as_success(tmp_path,monkeypatch):
    from assess_agent.models import SectionWriter
    from assess_agent.store import JobStore
    from assess_agent.workflow import run_workflow
    from tests.test_agent import setup_task
    from tests.fake_model import ToolCallingTestModel
    settings,request=setup_task(tmp_path)
    request.custom_topic=CustomTopic(title='AI人才培养方向',requirements='给出培养方向和评价建议')
    original=SectionWriter.write
    def write(self,title,*args,**kwargs):
        if title.startswith('第十章 '):
            return SectionDraft(title=title,blocks=[{'kind':'gap','text':'缺少未来方案。'}])
        return original(self,title,*args,**kwargs)
    monkeypatch.setattr(SectionWriter,'write',write)
    jobs=JobStore(settings.data_root);jobs.create(request)
    with pytest.raises(AgentError) as error:
        run_workflow(request,settings,jobs,1,model=ToolCallingTestModel())
    assert error.value.code=='TOPIC_INCOMPLETE'
    assert (settings.jobs_root()/str(request.task_id)/'attempt-1/report-sections.json').exists()
