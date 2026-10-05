from uuid import uuid4
from pathlib import Path
import json

from docx import Document
import pytest

from assess_agent.evidence import EvidenceStore
from assess_agent.exporter import export_document
from assess_agent.schemas import Corpus,Evidence,ParsedTable,JobRequest,SectionDraft
from assess_agent.settings import CHAPTERS
from assess_agent.survey import SurveyAnalyzer,split_question
from assess_agent.validation import validate_section
from assess_agent.visuals import VisualRegistry


def survey_store(columns,rows,category='DATA'):
    material=str(uuid4())
    evidence=Evidence(id='E-source',material_id=material,name='2025届问卷.xlsx',category=category,location='问卷',text='当届原始回答')
    table=ParsedTable(material_id=material,name=evidence.name,sheet='问卷',columns=columns,rows=rows,start_row=2,source_ids=[evidence.id])
    store=EvidenceStore(Corpus(evidence=[evidence],tables=[table]))
    store.survey=SurveyAnalyzer(store,2025,'测试大学').build()
    return store,table


def test_multi_hot_valid_denominator_excludes_skip_and_partial_rows():
    store,table=survey_store(['届次','改进需求（实践）','改进需求（课程）'],
        [['2025','1','1'],['2025','0','1'],['2025','0','0'],['2025','',''],['2025','1',''],['2024','1','0']])
    q=store.survey.questions[0]
    assert q['validCount']==3 and q['unansweredCount']==2
    assert q['items'][0]['count']==1 and q['items'][0]['percentage']==33.33
    assert q['items'][1]['percentage']==66.67
    source=store.read(q['evidenceId'])
    assert source.computed and source.parent_ids==table.source_ids
    assert json.loads(source.text)['cohort']==2025


def test_question_option_parser_preserves_nested_parentheses():
    assert split_question('参与项目（课程（含AI案例））')==('参与项目','课程（含AI案例）')
    assert split_question('能力（分5级展示）（工程知识）_毕业时掌握水平')==('能力（分5级展示）','工程知识 / 毕业时掌握水平')


def test_actual_study_excludes_intention_and_satisfaction_uses_filtered_answers():
    store,_=survey_store(['届次','您现在是否有工作','您的研究生类型','您对自己的现状是否满意'],
        [['2025','正在中国境内读研','专硕','满意'],['2025','正在中国境内读研','学硕','不满意'],
         ['2025','准备中国境内考研','','很满意'],['2025','受雇全职工作，与专业有关','','满意']])
    status=next(q for q in store.survey.questions if q['type']=='study_status')
    assert status['validCount']==4 and status['items'][0]['count']==2 and status['items'][0]['percentage']==50
    q=next(q for q in store.survey.questions if q['question'].startswith('您对自己的现状') and q['scope']=='正在中国境内读研')
    assert q['validCount']==2 and q['positivePercentage']==50
    assert any(q['type']=='study_status' for q in store.survey.context(CHAPTERS[3]))


def test_reference_and_unknown_numeric_codes_not_precomputed():
    store,_=survey_store(['届次','满意度编码'],[['2025','1'],['2025','2']])
    assert not store.survey.questions
    store,_=survey_store(['届次','满意度'],[['2025','满意']],category='REFERENCE')
    assert not store.survey.questions


def test_large_frequency_keeps_other_count_and_real_percentage():
    store,table=survey_store(['届次','就业城市'],[['2025',f'城市{i}'] for i in range(100)])
    result=store.summarize(table.material_id,table.sheet,'就业城市')['groups'][0]
    assert result['validCount']==100 and result['distinctCount']==100 and result['otherCount']==70
    assert sum(result['counts'].values())==100
    assert result['percentages']['其余类别合计']==70


def test_field_inspection_paginates_beyond_first_eighty_columns():
    store,table=survey_store(['届次',*[f'字段{i}' for i in range(130)]],[['2025',*['' for _ in range(130)]]])
    tool=next(t for t in store.tools() if t.name=='inspect_table')
    first=tool.invoke({'material_id':table.material_id,'sheet':table.sheet})
    second=tool.invoke({'material_id':table.material_id,'sheet':table.sheet,'offset':first['nextOffset']})
    assert '字段129' in second['columns'] and second['nextOffset'] is None


def test_single_year_chart_is_bar_and_multiselect_uses_original_caption(tmp_path):
    store,table=survey_store(['届次','您现在是否有工作','您认为学校在AI培养方面需要加强的是（工具教学）','您认为学校在AI培养方面需要加强的是（项目实践）'],
        [['2025','正在中国境内读研','1','1'],['2025','受雇全职工作，与专业有关','0','1']])
    charts=VisualRegistry(store,tmp_path/'visuals',2025,[CHAPTERS[3],CHAPTERS[5]])
    store.visuals=charts;charts.plan()
    chart=charts.datasets[charts.coverage['figure-3-1']['datasetId']]
    assert chart['type']=='bar' and chart['series'][0]['values']==[50]
    assert '未作跨届趋势比较' in chart['basis']
    ai=charts.datasets[charts.coverage['figure-5-17']['datasetId']]
    assert ai['multipleResponse'] and ai['series'][0]['values']==[50,100]
    draft=charts.attach(SectionDraft(title=CHAPTERS[5],blocks=[{'kind':'gap','text':'测试排版。','subsection':'“人工智能”+教育改革评价'}]))
    request=JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='qwen',chapters=[draft.title],materials=[{'id':table.material_id,'name':table.name,'category':'DATA'}])
    file=tmp_path/'report.docx';export_document(request,[draft],store,file,'毕业生培养质量评价报告',[draft.title])
    doc=Document(file);paragraphs=[p.text for p in doc.paragraphs]
    assert paragraphs.index('“人工智能”+教育改革评价') < paragraphs.index('图5-17 AI 教学培养需求（多选）') < paragraphs.index('未开展分析的项目')
    assert ' TOC ' in doc.element.xml and 'chapter_0' in doc.element.xml
    assert all('待补充的图表资料：' not in p for p in paragraphs)
    assert len(doc.sections)==3


def test_trend_and_reused_original_table_number_are_rejected():
    store,_=survey_store(['届次','满意度'],[['2025','满意']])
    key=store.survey.questions[0]['evidenceId']
    draft=SectionDraft(title=CHAPTERS[2],blocks=[{'kind':'inference','text':'离职率同期上行。','source_ids':[key],'subsection':'工作稳定性'}],
        tables=[{'title':'表2-99 其他表','columns':['人数'],'rows':[['1']],'source_ids':[key]}])
    issues=validate_section(draft,draft.title,store,2025)
    assert any('趋势' in i for i in issues) and any('不得自拟' in i for i in issues)


def test_model_view_does_not_repeat_entire_provenance_chain():
    store,table=survey_store(['届次','满意度'],[['2025','满意']])
    table.source_ids=['E-source']*3000
    result=store.computed(table,{'count':100,'sourceIds':table.source_ids,'basis':'实际题项'})
    full=store.read(result['evidenceId'])
    view=store.for_model(full.id)
    assert len(full.parent_ids)==3000 and len(view['parentIds'])==3 and view['parentCount']==3000
    data=json.loads(view['text'])
    assert data['count']==100 and data['sourceIdsCount']==3000 and len(data['sourceIds'])==3
    assert len(json.dumps(view,ensure_ascii=False)) < len(full.model_dump_json())/10
