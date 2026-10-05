import json
from uuid import uuid4

from docx import Document
from langchain_core.messages import AIMessage

from assess_agent.coverage_recovery import recover_coverage
from assess_agent.exporter import export_document
from assess_agent.models import SectionWriter
from assess_agent.report_style import formal_text,visual_note
from assess_agent.schemas import JobRequest,SectionDraft
from assess_agent.settings import CHAPTERS,REPORT_CONFIG,Settings
from assess_agent.topic_evidence import topic_packets
from assess_agent.validation import validate_section
from assess_agent.visuals import VisualRegistry
from tests.test_report_improvements import survey_store


def prepared(tmp_path,columns,rows,chapters):
    store,table=survey_store(columns,rows)
    store.visuals=VisualRegistry(store,tmp_path/'visuals',2025,chapters)
    store.visuals.plan()
    return store,table


def test_grouped_major_statistics_use_current_employed_samples_and_preserve_caption_scope(tmp_path):
    rows=[['2025','受雇全职工作，与专业有关','学院','软件工程','信息业']]*6
    rows += [['2024','受雇全职工作，与专业有关','学院','软件工程','其他']]*7
    rows += [['2025','准备中国境内考研','学院','软件工程','其他']]*5
    store,table=prepared(tmp_path,['届次','您现在是否有工作','学院名称','专业名称','您现在就业的行业类'],rows,[CHAPTERS[1]])
    row=store.visuals.coverage['table-1-7'];data=store.visuals.datasets[row['datasetId']]
    assert data['rows']==[['学院','软件工程','信息业（100%）']]
    parent=store.read(json.loads(store.read(row['datasetId']).text)['binding']['questionEvidenceId'])
    assert json.loads(parent.text)['detailRows']==[['学院','软件工程','信息业','100.0','6']]
    asset=store.visuals.render(row['datasetId'])
    assert store.visuals.assets[asset['assetId']]['caption'].endswith('（2025届）')
    assert '2024' not in data['basis']


def test_income_unit_and_course_record_denominators_are_not_percentage_or_people(tmp_path):
    store,_=prepared(tmp_path,['届次','您现在是否有工作','您现在从事的职业类','月收入（元）'],
        [['2025','受雇全职工作，与专业有关','软件开发','8000']]*6,[CHAPTERS[2]])
    data=store.visuals.datasets[store.visuals.coverage['figure-2-10']['datasetId']]
    assert data['unit']=='元' and data['series'][0]['values']==[8000]
    store,_=prepared(tmp_path,['届次','学院名称','课程名称','课程的重要性','课程的重要度','课程是否满足工作或学习要求（1表示满足，0表示不满足）'],
        [['2025','学院','程序设计','重要','1','1']]*5+[['2025','学院','程序设计','不重要','0','0']], [CHAPTERS[5]])
    data=store.visuals.datasets[store.visuals.coverage['table-5-9']['datasetId']]
    assert data['rows'][0]==['学院','83.33','6','83.33','6']
    assert '记录数不是人数' in data['basis']
    packets=topic_packets(store,CHAPTERS[5])
    assert next(p for p in packets if p['subsection'].endswith('/ 专业课程培养效果'))['source_ids']


def test_ambiguous_importance_codes_and_small_groups_do_not_become_rates(tmp_path):
    store,_=prepared(tmp_path,['届次','学院名称','课程名称','课程的重要性','课程的重要度','课程是否满足工作或学习要求（1表示满足，0表示不满足）'],
        [['2025','学院','程序设计','不重要','1','1']]*5, [CHAPTERS[5]])
    data=store.visuals.datasets[store.visuals.coverage['table-5-9']['datasetId']]
    assert data['rows'][0][3]=='—'
    store,_=prepared(tmp_path,['届次','您现在是否有工作','学院名称','专业名称','您现在就业的行业类'],
        [['2025','受雇全职工作，与专业有关','学院','软件工程','信息业']]*4,[CHAPTERS[1]])
    assert store.visuals.coverage['table-1-7']['status']=='missing'


def test_positive_and_nonpositive_rates_are_registered_for_network_topic(tmp_path):
    store,_=prepared(tmp_path,['届次','请评价学校网络学习空间对您自主学习和互动的支持度（访问速度）','请评价学校网络学习空间对您自主学习和互动的支持度（互动功能）'],
        [['2025','满意','满意']]*9+[['2025','不满意','不满意']], [CHAPTERS[6]])
    packet=next(p for p in topic_packets(store,CHAPTERS[6]) if p['subsection'].endswith('/ 网络学习空间支持度'))
    assert packet['support']=='verified_statistics'
    q=next(r for r in packet['results'] if r.get('type')=='matrix')
    assert q['items'][0]['positivePercentage']==90 and q['items'][0]['nonPositivePercentage']==10


def test_empty_public_analysis_still_composes_from_evidence(tmp_path):
    store,table=prepared(tmp_path,['届次','您现在是否有工作'],[['2025','正在中国境内读研']]*6,[CHAPTERS[3]])
    request=JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='deepseek',chapters=[CHAPTERS[3]],
        materials=[{'id':table.material_id,'name':table.name,'category':'DATA'}])
    class Model:
        def __init__(self,content):self.content=content
        def invoke(self,messages):return AIMessage(content=self.content)
    key=next(q['evidenceId'] for q in store.survey.questions if q['type']=='study_status')
    reply=json.dumps({'title':CHAPTERS[3],'blocks':[{'kind':'finding','text':'境内读研比例为100%。',
        'subsection':'读研比例 / 境内读研比例','source_ids':[key]}]})
    writer=SectionWriter(Settings(data_root=tmp_path),request,store,model=Model(reply));writer.analysis_model=Model('')
    draft=writer.write(CHAPTERS[3],'升学',focus_paths=['读研比例 / 境内读研比例'])
    assert writer.call_trace[0]['analysisAnswerMissing']
    assert not validate_section(draft,draft.title,store,2025)


def test_supported_omissions_are_recovered_and_unsolved_items_are_not_called_missing_uploads(tmp_path):
    store,_=prepared(tmp_path,['届次','您现在是否有工作'],[['2025','正在中国境内读研']]*6,[CHAPTERS[3]])
    class Writer:
        evidence=store
        def write(self,title,*args,**kwargs):return SectionDraft(title=title,blocks=[{'kind':'gap','text':'模型未完成。'}])
    draft=SectionDraft(title=CHAPTERS[3],blocks=[{'kind':'gap','text':'先前结构化失败。'}])
    config=next(c for c in REPORT_CONFIG['chapters'] if c['title']==draft.title)
    draft,coverage=recover_coverage(Writer(),draft,config,2025)
    assert any(b.subsection=='读研比例 / 境内读研比例' and b.kind=='finding' for b in draft.blocks)
    assert any(b.subsection=='本章小结' and b.kind=='finding' for b in draft.blocks)
    assert not validate_section(draft,draft.title,store,2025)
    assert any(r['status']=='no_verified_statistics' for r in coverage['recovery'])


def test_word_has_formal_prose_no_visible_sources_and_full_internal_audit(tmp_path):
    store,table=survey_store(['届次','就业人数'],[['2025','80']])
    key=table.source_ids[0]
    request=JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='qwen',chapters=[CHAPTERS[1]],
        materials=[{'id':table.material_id,'name':table.name,'category':'DATA'}])
    draft=SectionDraft(title=CHAPTERS[1],blocks=[{'kind':'finding','text':'就业人数为80人，取自本校主要指标表本校平均行；原表未列有效样本数，故仅呈现当届水平，不作趋势判断。',
        'subsection':'本章小结','source_ids':[key]}])
    path=tmp_path/'report.docx';export_document(request,[draft],store,path,'毕业生培养质量评价报告',[draft.title])
    texts=[p.text for p in Document(path).paragraphs]
    assert '就业人数为80人。' in texts
    assert not any('资料来源' in p or '取自' in p or '本次未形成该项' in p or '[1]' in p for p in texts)
    assert '未就业人群分布' in texts
    assert '未开展分析的项目' in texts
    audit=json.loads(path.with_suffix('.audit.json').read_text(encoding='utf-8'))
    assert audit['sourceIds']==[key] and audit['sections'][0]['blocks'][0]['source_ids']==[key]


def test_formal_style_keeps_actual_scope_and_required_chart_notes():
    text='在正在中国境内读研的受访毕业生中，满意比例为90%，不包含准备考研者。'
    assert formal_text(text)==text
    example='2025届毕业去向落实率为81.11%，取自本校2025届主要指标表“本校平均”行，该数值与各学院、各专业指标表口径一致；原表未列出该指标有效样本数，本次资料亦未提供可比届次的同口径单届数值，故仅呈现当届水平，不作趋势判断。'
    assert formal_text(example)=='2025届毕业去向落实率为81.11%。'
    assert formal_text('原表未提供受访者毕业状态，因此只解释原题总体样本。')=='原表未提供受访者毕业状态，因此只解释原题总体样本。'
    assert '多选' in visual_note({'multipleResponse':True,'basis':'本次资料未列样本数','type':'bar'})
    assert visual_note({'multipleResponse':False,'basis':'原表未列样本数；仅当届，未作趋势比较','type':'bar'})==''


def test_core_summaries_include_later_themes_instead_of_only_first_paragraphs():
    from assess_agent.workflow import verified_summaries
    section={'title':CHAPTERS[5],'blocks':[{'kind':'finding','text':f'教学结果{i}。','source_ids':['E-source'],'subsection':'教学满意度 / 教学满意度'} for i in range(20)]}
    section['blocks'] += [{'kind':'finding','text':'AI教学培养需求已有结果。','source_ids':['E-ai'],'subsection':'“人工智能”+教育改革评价 / AI教学培养需求'},
                          {'kind':'finding','text':'课程已有评价。','source_ids':['E-course'],'subsection':'专业核心课程评价 / 专业课程培养效果'}]
    result=verified_summaries([section],limit=3)[0]['blocks']
    assert [b['source_ids'][0] for b in result]==['E-source','E-ai','E-course']
