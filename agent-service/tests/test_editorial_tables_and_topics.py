from copy import deepcopy
from uuid import uuid4

from docx import Document

from assess_agent.extension_style import normalize_extension_structure, TOPIC_HEADINGS
from assess_agent.report_style import formal_text
from assess_agent.schemas import ContentTable, SectionDraft, JobRequest
from assess_agent.settings import CHAPTERS
from assess_agent.table_layout import ai_frequency_summary, comparison_table_view, duplicate_statistic_table
from assess_agent.exporter import export_document, add_data_table
from assess_agent.visuals import VisualRegistry
from assess_agent.survey import SurveyAnalyzer
from tests.test_report_improvements import survey_store


def test_ai_summary_uses_actual_counts_and_separate_task_denominators():
    result=ai_frequency_summary([
        {'group':'信息类','task':'开发','validCount':5,'counts':{'总是':1,'经常':3,'偶尔':1}},
        {'group':'信息类','task':'协作','validCount':100,'counts':{'总是':10,'经常':20,'偶尔':70}},
        {'group':'信息类','task':'记录','validCount':10,'counts':{'偶尔':10}},
    ])
    assert result['rows']==[['信息类','开发（80.00%）','记录（0.00%）']]
    assert ai_frequency_summary([{'group':'未知','task':'开发','validCount':10,'counts':{'1':5,'2':5}}]) is None
    assert ai_frequency_summary([{'group':'缺失','task':'开发','validCount':10,'counts':{'总是':5}}]) is None


def test_ai_summary_handles_exact_ties_without_claiming_other_tasks_are_lower():
    result=ai_frequency_summary([{'group':'信息类','task':t,'validCount':5,'counts':{'总是':5}} for t in ('开发','记录','协作')])
    assert result['rows'][0][2]=='各任务高频使用比例一致'


def test_distribution_view_reduces_body_rows_without_renormalizing_or_mutating_data():
    data={'templateId':'table-1-6','columns':['类别','人数','比例（%）'],
          'rows':[[str(i),str(n),str(n)] for i,n in enumerate([40,20,10,8,7,6,5,4])]}
    before=deepcopy(data); view=comparison_table_view(data)
    assert len(view['rows'])==5 and [r[-1] for r in view['rows']]==['40.00','20.00','10.00','8.00','7.00']
    assert all(r[1:4]==['—']*3 for r in view['rows'])
    assert data==before


def test_saved_frequency_export_pivots_all_options_and_keeps_zero_choices():
    data={'templateId':'table-5-5','columns':['职业类','工作任务','使用频率','比例（%）','有效回答人数'],
          'rows':[['信息类','开发','总是','20','5'],['信息类','开发','经常','80','5']]}
    assert comparison_table_view(data)['rows']==[['信息类','开发','20.00','80.00','0.00','0.00']]
    data['rows'][1][4]='6'
    assert comparison_table_view(data)['rows']==[['信息类','开发',*['—']*4]]


def test_activity_metrics_rank_independently_in_reference_discipline_columns():
    data={'templateId':'table-5-2','columns':['学科门类','评价项目或主要需求','比例（%）','有效回答人数'],
        'rows':[['工学','活动'+str(i),str(i),'10'] for i in range(10)]}
    view=comparison_table_view(data)
    assert view['columns']==['选项','工学','选项','管理学','选项','理学','选项','文学']
    assert [r[0] for r in view['rows']]==['活动'+str(i) for i in range(9,3,-1)]
    assert [r[1] for r in view['rows']]==[f'{i:.2f}' for i in range(9,3,-1)]
    assert all(r[2:]==['—']*6 for r in view['rows'])
    assert len(data['rows'])==10


def test_full_ai_demand_matrix_includes_every_option_and_school_average(tmp_path):
    question='您认为AI时代需强化的能力和素养'
    choices=['人机交互能力','复杂问题拆解能力','数据思维与分析能力','批判性思维','跨领域知识融合能力','伦理意识与责任感']
    columns=['届次','学院名称',*[question+'（'+c+'）' for c in choices]]
    rows=[['2025','甲学院',*(['1']*5+['0'])] for _ in range(6)]
    rows += [['2025','乙学院',*(['0']*5+['1'])] for _ in range(6)]
    store,_=survey_store(columns,rows);store.survey=SurveyAnalyzer(store,2025,'测试大学').build()
    registry=VisualRegistry(store,tmp_path,2025,CHAPTERS);store.visuals=registry;registry.plan()
    data=registry.datasets[registry.coverage['table-5-6']['datasetId']]
    view=comparison_table_view(data)
    assert len(data['rows'])==18
    assert len(view['rows'])==3 and len(view['columns'])==7
    average=next(r for r in view['rows'] if r[0]=='本校平均')
    assert all(float(x)==50 for x in average[1:])
    assert set(view['columns'][1:])==set(choices)
    assert any(float(x)==0 for r in view['rows'] for x in r[1:])


def test_duplicate_tables_with_distinct_computation_ids_are_removed_only_when_exact():
    data={'type':'table','templateId':'table-5-9','columns':['学院','核心课程重要度（%）'],'rows':[['甲','80.00']]}
    table=ContentTable(title='课程评价',columns=data['columns'],rows=data['rows'],source_ids=['C-other'])
    assert duplicate_statistic_table(table,[data])
    table.columns=['学院','核心课程满足度（%）']
    assert not duplicate_statistic_table(table,[data])


def test_practice_activity_table_binds_to_activity_satisfaction_not_practice_conditions(tmp_path):
    columns=['届次','专业大类名称','您对以下实习和实践环节是否满意（毕业论文/设计）',
        '您对以下实习和实践环节是否满意（课程实验）',
        '请您对校内实习实践进行评价（教师实践经验丰富，指导效果好）']
    store,_=survey_store(columns,[['2025','工学','满意','不满意','满意'] for _ in range(6)])
    store.survey=SurveyAnalyzer(store,2025,'测试大学').build()
    registry=VisualRegistry(store,tmp_path,2025,CHAPTERS);store.visuals=registry;registry.plan()
    data=registry.datasets[registry.coverage['table-5-2']['datasetId']]
    view=comparison_table_view(data)
    assert [(r[0],r[1]) for r in view['rows']]==[('毕业论文/设计','100.00'),('课程实验','0.00')]
    assert all(r[2:]==['—']*6 for r in view['rows'])


def test_textbook_table_shows_full_response_distribution_in_reference_order(tmp_path):
    columns=['届次','请您对学习过程中使用的教材情况进行评价（教材实用性）',
        '请您对学习过程中使用的教材情况进行评价（教材先进性）']
    rows=[['2025','符合','非常符合'],['2025','非常符合','符合'],['2025','不符合','非常不符合']]
    store,_=survey_store(columns,rows);store.survey=SurveyAnalyzer(store,2025,'测试大学').build()
    registry=VisualRegistry(store,tmp_path,2025,CHAPTERS);store.visuals=registry;registry.plan()
    data=registry.datasets[registry.coverage['table-5-10']['datasetId']]
    assert data['columns']==['评价项目','非常符合（%）','符合（%）','不符合（%）','非常不符合（%）']
    assert all(abs(sum(map(float,r[1:]))-100)<.02 for r in data['rows'])


def fragmented_topic():
    paths=['现状基点','需求侧信号','能力结构','培养过程约束','情景假设','培养定位与专业结构',
           '可观测学习成果','阶段路径','实施条件','评价方式']
    return SectionDraft(title='第十章 AI人才培养方向',blocks=[
        {'kind':'recommendation','text':'【建议】'+p+'的具体措施。','subsection':p,'source_ids':['E-'+str(i)]}
        for i,p in enumerate(paths)])


def test_topic_consolidation_retains_all_text_and_references_and_respects_explicit_outline():
    draft=fragmented_topic();before=[(b.text,list(b.source_ids)) for b in draft.blocks]
    normalize_extension_structure(draft)
    assert list(dict.fromkeys(b.subsection for b in draft.blocks))==list(TOPIC_HEADINGS)
    assert [(b.text,b.source_ids) for b in draft.blocks]==before
    assert len(draft.blocks)==10
    explicit=fragmented_topic();normalize_extension_structure(explicit,'请保留十个小节。')
    assert len({b.subsection for b in explicit.blocks})==10
    formal=fragmented_topic();formal.title=CHAPTERS[5];normalize_extension_structure(formal)
    assert len({b.subsection for b in formal.blocks})==10


def test_export_consolidates_saved_topic_and_limits_recommendation_labels(tmp_path):
    store,table=survey_store(['届次'],[['2025']])
    request=JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='deepseek',chapters=[CHAPTERS[1]],
        materials=[{'id':table.material_id,'name':table.name,'category':'DATA'}])
    draft=fragmented_topic()
    for block in draft.blocks:block.source_ids=[]
    output=tmp_path/'topic.docx'
    export_document(request,[draft],store,output,'毕业生培养质量评价报告',[draft.title])
    document=Document(output)
    assert [p.text for p in document.paragraphs if p.style.name=='Heading 2']==list(TOPIC_HEADINGS)
    labelled=[p.text for p in document.paragraphs if p.text.startswith('【建议】')]
    assert len(labelled)==len(TOPIC_HEADINGS)
    assert all(p.text.count('【建议】')<=1 for p in document.paragraphs)
    for block in draft.blocks:
        assert any(block.text.removeprefix('【建议】') in p.text for p in document.paragraphs)
    assert len({b.subsection for b in draft.blocks})==10
    assert formal_text('【建议】建议完善课程。')=='【建议】建议完善课程。'


def test_export_retains_manual_recommendation_positions_without_labelling_all_blocks(tmp_path):
    store,table=survey_store(['届次'],[['2025']])
    request=JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='deepseek',chapters=CHAPTERS,
        materials=[{'id':table.material_id,'name':table.name,'category':'DATA'}])
    positions={CHAPTERS[0]:['教学成效 / 适应度','培养过程 / “AI+”教育','教师队伍 / 教学投入'],
        **{title:['本章小结'] for title in CHAPTERS[1:8]}}
    sections=[]
    expected=[]
    for title in CHAPTERS:
        paths=positions.get(title,['项目背景介绍 / 背景介绍'])
        blocks=[]
        for path in paths:
            prefix=title+' '+path
            blocks.extend([
                {'kind':'finding','subsection':path,'text':'【建议】'+prefix+'的现状分析。'},
                {'kind':'recommendation','subsection':path,'text':prefix+'的重点改进措施。'},
                {'kind':'recommendation','subsection':path,'text':'【建议】'+prefix+'的后续落实措施。'},
            ])
            if title in positions:expected.append('【建议】'+prefix+'的重点改进措施。')
        if title==CHAPTERS[0]:
            blocks.append({'kind':'recommendation','subsection':'教学成效 / 达成度','text':'【建议】普通指标的简短建议。'})
        sections.append(SectionDraft(title=title,blocks=blocks))
    before=[section.model_dump() for section in sections]
    output=tmp_path/'manual-positions.docx'
    export_document(request,sections,store,output,'毕业生培养质量评价报告',CHAPTERS)
    paragraphs=[p.text for p in Document(output).paragraphs]
    assert [p for p in paragraphs if p.startswith('【建议】')]==expected
    assert len(expected)==10
    assert '普通指标的简短建议。' in paragraphs
    assert not any(p.startswith('【建议】') and ('现状分析' in p or '后续落实' in p) for p in paragraphs)
    for section in sections:
        for block in section.blocks:
            assert any(block.text.removeprefix('【建议】') in p for p in paragraphs)
    assert [section.model_dump() for section in sections]==before


def test_recommendation_marker_does_not_hide_process_clauses_or_leave_empty_paragraphs():
    assert formal_text('**[建议]**【建议】完善课程。取自本校主要指标表。')=='【建议】完善课程。'
    assert formal_text('【建议】取自本校主要指标表。')==''


def test_short_comparison_tables_avoid_isolated_tail_rows_but_long_content_can_paginate():
    document=Document()
    small=add_data_table(document,['学科门类','实践满意度（%）'],
        [['门类'+str(i),'90.00'] for i in range(6)])
    assert all(c.paragraphs[0].paragraph_format.keep_with_next for c in small.rows[-2].cells)
    assert all(not c.paragraphs[0].paragraph_format.keep_with_next for r in small.rows[1:-2] for c in r.cells)
    assert all(not c.paragraphs[0].paragraph_format.keep_with_next for c in small.rows[-1].cells)
    assert small._tbl.xpath('.//w:tblHeader')
    large=add_data_table(document,['职业类','任务'],[['信息类','具体工作任务。'*100] for _ in range(4)])
    assert not any(c.paragraphs[0].paragraph_format.keep_with_next for r in large.rows[1:] for c in r.cells)
    assert large._tbl.xpath('.//w:tblHeader')
