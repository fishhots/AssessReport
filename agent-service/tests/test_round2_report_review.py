import json
from uuid import uuid4

import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor

from assess_agent.exporter import add_data_table
from assess_agent.quantitative_review import comparison_cards, quantitative_issues
from assess_agent.report_style import formal_text
from assess_agent.schemas import ContentTable, Evidence
from assess_agent.schemas import ParsedTable
from assess_agent.table_layout import PRACTICE, comparison_table_view, duplicate_statistic_table
from assess_agent.topic_evidence import compact_result
from assess_agent.visuals import VisualRegistry
from assess_agent.writing_limits import writing_limits
from tests.test_report_improvements import survey_store
from assess_agent.settings import CHAPTERS


def source(data):
    return Evidence(id='C-result',material_id=str(uuid4()),name='统计',category='DATA',location='统计',
        computed=True,text=json.dumps(data,ensure_ascii=False))


def test_full_comparison_finds_an_extreme_omitted_by_prompt_sampling():
    data={'labels':[f'专业{i}' for i in range(30)],'series':[{'name':'比例','values':[50]*30}],'unit':'%'}
    data['series'][0]['values'][15]=99
    result=compact_result(source(data))
    assert not result['labelsComplete'] and '专业15' not in result['labels']
    card=result['comparisonCards'][0]
    assert card['highestLabels']==['专业15'] and card['highestValue']==99
    assert card['lowestTieCount']==29


def test_missing_means_do_not_become_zero_or_fail_the_workflow():
    data={'items':[{'label':'能力甲','mean':None,'unit':'级'},{'label':'能力乙','mean':3.5,'unit':'级'}]}
    assert comparison_cards(data)[0]['highestLabels']==['能力乙']


def test_mastery_and_demand_extrema_are_checked_separately():
    data={'type':'numeric','items':[
        {'label':'使用工程工具及信息技术工具的能力 / 大学毕业时，您掌握了什么水平的该项能力','mean':3.43,'unit':'级'},
        {'label':'终身学习 / 大学毕业时，您掌握了什么水平的该项能力','mean':3.45,'unit':'级'},
        {'label':'使用工程工具及信息技术工具的能力 / 您现在的工作需要什么水平的该项能力','mean':3.69,'unit':'级'},
        {'label':'终身学习 / 您现在的工作需要什么水平的该项能力','mean':3.87,'unit':'级'}]}
    src=source(data)
    assert len(comparison_cards(data))==2
    errors=quantitative_issues('使用工程工具及信息技术工具的能力均值最高，为3.43级；终身学习为3.45级。',[src])
    assert any('最高判断' in e for e in errors)
    assert not quantitative_issues('终身学习掌握均值最高，为3.45级，需求水平为3.87级。',[src])
    assert not quantitative_issues('使用工程工具及信息技术工具的能力掌握均值为3.43级。',[src])


def test_true_numbers_cannot_be_swapped_between_survey_options():
    src=source({'question':'校外实习来源','labels':['学校安排','求职网站'],'series':[{'name':'比例','values':[26.03,28.97]}],'unit':'%'})
    assert any('标签不对应' in e for e in quantitative_issues('学校安排的比例为28.97%。',[src]))
    assert quantitative_issues('学校安排为26.03%，求职网站为28.97%。',[src])==[]


@pytest.mark.parametrize('text',['甲学院显著低于本校均值。','学院间差异显著。','离职率处于中等水平，整体可控。'])
def test_unsupported_significance_and_absolute_grades_are_rejected(text):
    assert quantitative_issues(text,[source({'labels':['甲学院','乙学院'],'series':[{'name':'比例','values':[50,90]}],'unit':'%'})])


def test_legitimate_qualitative_feedback_is_not_indiscriminately_filtered():
    assert quantitative_issues('项目参与率相对较低，建议加强实践支持。',[])==[]
    assert quantitative_issues('学院间差异显著。',[source({'type':'statistical_test','pValue':.01,'alpha':.05})])==[]
    assert quantitative_issues('学院间差异显著。',[source({'type':'statistical_test','pValue':.3,'alpha':.05})])


def test_group_scope_qualifiers_and_response_substrings_are_not_false_errors():
    faculty=source({'labels':['外国语学院','自动化学院'],'series':[{'name':'比例','values':[91.43,86.99]}],'unit':'%'})
    major=source({'labels':['电气工程及其自动化（自动化学院）'],'series':[{'name':'比例','values':[100]}],'unit':'%'})
    text='外国语学院（91.43%）最高；电气工程及其自动化（自动化学院）为100%。'
    assert not quantitative_issues(text,[faculty,major])
    responses=source({'question':'满意度','items':[{'label':'不满意','percentage':2.96},{'label':'很不满意','percentage':1.31}]})
    assert not quantitative_issues('不满意2.96%，很不满意1.31%。',[responses])
    assert not quantitative_issues('学院差异描述不涉及统计显著性检验。',[])


def test_matrix_positive_rate_does_not_get_mistaken_for_a_single_response():
    matrix=source({'question':'合作课程','items':[{'label':'课程内容与产业需求匹配','positivePercentage':92.37,'nonPositivePercentage':7.63}]})
    chart=source({'labels':['课程内容与产业需求匹配'],'series':[{'name':'符合','values':[53.44]},{'name':'非常符合','values':[38.93]}],'unit':'%'})
    assert not quantitative_issues('课程内容与产业需求匹配的正向评价为92.37%。',[matrix,chart])


def test_longer_exact_item_takes_precedence_over_an_embedded_different_item():
    src=source({'items':[{'label':'创业课程提升创业能力（如市场洞察力）','percentage':22.6},
                         {'label':'提升创业能力（如市场洞察力）','percentage':14.8}]})
    assert not quantitative_issues('创业课程提升创业能力（如市场洞察力）为22.60%。',[src])


def test_salary_generalization_cannot_include_a_below_threshold_major():
    src=source({'columns':['学院','专业','月收入（元）'],'rows':[
        ['计算机学院','计算机科学与技术','10161.54'],['计算机学院','信息安全','9944.44']]})
    assert quantitative_issues('计算机学院计算机科学与技术和信息安全专业毕业生月收入超过万元。',[src])
    assert not quantitative_issues('计算机学院计算机科学与技术专业毕业生月收入超过万元。',[src])


def test_moral_increment_uses_complete_exclusive_responses(tmp_path):
    cols=['届次','大学帮助您在哪些素养方面有明显的提升（人文底蕴）','大学帮助您在哪些素养方面有明显的提升（社会责任）','大学帮助您在哪些素养方面有明显的提升（没有任何提升）']
    rows=[['2025','1','0','0']]*6+[['2025','0','1','0']]*3+[['2025','0','0','1']]+[['2025','','','']]+[['2024','0','0','1']]*9
    store,_=survey_store(cols,rows)
    registry=VisualRegistry(store,tmp_path,2025,[CHAPTERS[4]]);registry.plan()
    data=registry.datasets[registry.coverage['figure-4-1']['datasetId']]
    assert data['validSamples']==10 and data['series'][0]['values']==[90]
    assert '互斥' in data['basis']


def test_moral_discipline_join_uses_explicit_membership_and_retains_both_sources(tmp_path):
    cols=['届次','学院名称','专业名称','大学帮助您在哪些素养方面有明显的提升（人文底蕴）','大学帮助您在哪些素养方面有明显的提升（没有任何提升）']
    store,raw=survey_store(cols,[['2025','甲学院','同名专业','1','0']]*6+[['2025','乙学院','同名专业','0','1']]*5)
    key='E-classification';material=str(uuid4())
    evidence=Evidence(id=key,material_id=material,name='2025届课程数据',category='DATA',location='分类',text='甲学院工学，乙学院管理学。')
    table=ParsedTable(material_id=material,name=evidence.name,sheet='课程',start_row=2,source_ids=[key],
        columns=['届次','学历层次','学院名称','专业名称','专业大类名称'],
        rows=[['2025','本科','甲学院','同名专业','工学'],['2025','本科','乙学院','同名专业','管理学']])
    store.corpus.tables.append(table);store.corpus.evidence.append(evidence);store.by_id[key]=evidence
    registry=VisualRegistry(store,tmp_path,2025,[CHAPTERS[4]]);registry.plan()
    data=registry.datasets[registry.coverage['table-4-1']['datasetId']]
    assert data['rows']==[['工学','100.0'],['管理学','0.0']]
    source=store.read(data['binding']['questionEvidenceId'])
    assert any(key in store.read(k).parent_ids for k in source.parent_ids if store.read(k).computed)
    assert raw.source_ids[0] in source.parent_ids
    detail=registry.datasets[registry.coverage['table-4-2']['datasetId']]
    assert detail['rows']==[['人文底蕴','100.00','人文底蕴','0.00']]


@pytest.mark.parametrize('bad',['1','0'])
def test_ambiguous_moral_rows_do_not_create_an_assumed_percentage(tmp_path,bad):
    cols=['届次','大学帮助您在哪些素养方面有明显的提升（人文底蕴）','大学帮助您在哪些素养方面有明显的提升（没有任何提升）']
    rows=[['2025','1','0']]*5+[['2025',bad,bad]]
    store,_=survey_store(cols,rows)
    registry=VisualRegistry(store,tmp_path,2025,[CHAPTERS[4]]);registry.plan()
    assert registry.coverage['figure-4-1']['status']=='missing'
    assert '全零' in registry.coverage['figure-4-1']['reason']


def test_career_records_get_overall_and_faculty_visuals_without_becoming_headcounts(tmp_path):
    store,_=survey_store(['届次','学院名称','职业能力名称','职业能力是否满足目前的工作需要'],
        [['2025','甲学院','工程分析','满足']]*6+[['2025','乙学院','沟通','不满足']]*5+[['2024','乙学院','沟通','不满足']]*20)
    registry=VisualRegistry(store,tmp_path,2025,[CHAPTERS[4]]);registry.plan()
    data=registry.datasets[registry.coverage['figure-4-9']['datasetId']]
    assert data['series'][0]['values']==[54.55] and data['validSamples']==11
    group=registry.datasets[registry.coverage['figure-4-10']['datasetId']]
    assert group['rows']==[['乙学院','0.0','5'],['甲学院','100.0','6']]
    assert '记录数不是毕业生人数' in group['basis']


def test_club_satisfaction_preserves_each_question_denominator(tmp_path):
    store,_=survey_store(['届次','您对公益类社团是否满意','您对体育户外类社团是否满意'],
        [['2025','满意','满意']]*5+[['2025','不满意','']]*5+[['2025','','未知编码']])
    registry=VisualRegistry(store,tmp_path,2025,[CHAPTERS[4]]);registry.plan()
    data=registry.datasets[registry.coverage['figure-4-14']['datasetId']]
    assert data['rows']==[['公益类社团','50.0','10']]


def test_explicit_is_satisfied_binary_answer_is_supported(tmp_path):
    store,_=survey_store(['届次','您对体育户外类社团是否满意'],[['2025','是']]*7+[['2025','否']]*3)
    registry=VisualRegistry(store,tmp_path,2025,[CHAPTERS[4]]);registry.plan()
    assert registry.datasets[registry.coverage['figure-4-14']['datasetId']]['series'][0]['values']==[70]


def test_practice_table_matches_reference_comparison_columns_without_losing_values():
    rows=[['甲专业',p,str(80+i),str(5+i)] for i,(p,_,_) in enumerate(PRACTICE)]
    data={'templateId':'table-7-11','columns':['专业','评价项目或主要需求','比例（%）','有效回答人数'],'rows':rows}
    view=comparison_table_view(data)
    assert len(view['columns'])==9 and len(view['rows'])==1
    assert view['rows'][0][1:]==[f'{80+i:.2f}' for i in range(8)]
    assert view['headerGroups']==[(0,1,'专业名称'),(1,4,'校内实习实践评价'),(5,4,'校外实习实践评价')]
    assert data['rows']==rows  # original denominators remain in audit


def test_major_destinations_keep_reference_two_columns_and_all_groups():
    data={'templateId':'table-7-13','columns':['专业','主要行业','比例（%）','有效回答人数'],
        'rows':[['甲学院 / 软件工程','信息业','50','10'],['甲学院 / 软件工程','制造业','30','10'],
                ['乙学院 / 软件工程','金融业','40','5']]}
    view=comparison_table_view(data)
    assert view['columns']==['专业名称','就业的主要行业'] and len(view['rows'])==2
    assert view['rows'][0]==['甲学院 / 软件工程','信息业；制造业']
    assert data['rows'][0][3]=='10'
    data['rows'][0][2]='—'
    assert comparison_table_view(data)['rows'][0]==view['rows'][0]


def test_single_teaching_satisfaction_table_does_not_repeat_question_in_every_row():
    data={'templateId':'table-7-8','columns':['专业','评价项目或主要需求','比例（%）','有效回答人数'],
        'rows':[['甲专业','您对教学工作的满意程度','100','8'],['乙专业','您对教学工作的满意程度','80','10']]}
    view=comparison_table_view(data)
    assert view['columns']==['专业名称','2022届','2023届','2024届','2025届']
    assert view['rows']==[['甲专业','—','—','—','100.00'],['乙专业','—','—','—','80.00']]
    assert data['rows'][0][3]=='8'
    data['rows'][1][1]='另一项评价'
    assert comparison_table_view(data)['rows']==[['甲专业','—','—','—','100.00']]


def test_unknown_metric_is_preserved_and_conflicting_pairs_do_not_get_pivoted():
    data={'templateId':'table-7-10','columns':['专业','评价项目或主要需求','比例（%）','有效回答人数'],
          'rows':[['甲专业','学习指导','90','8'],['甲专业','学习指导','70','5']]}
    assert comparison_table_view(data)['rows']==[['甲专业',*['—']*5]]
    assert data['rows'][1][2]=='70'
    data['rows']=[['甲专业','新增项目','90','8']]
    assert '新增项目' not in comparison_table_view(data)['columns']
    assert data['rows'][0][1]=='新增项目'


def test_table_header_repeat_and_body_bold_are_correct(tmp_path):
    doc=Document()
    table=add_data_table(doc,['专业','学习指导','就业指导'],[['本校平均','90','80'],['甲专业','95','75']],[(0,1,'专业'),(1,2,'指导满足度')])
    assert len(table.rows)==4
    assert all(r._tr.trPr.find(qn('w:tblHeader')) is not None for r in table.rows[:2])
    assert all(run.bold for cell in table.rows[2].cells for p in cell.paragraphs for run in p.runs)
    assert all(run.bold is False for cell in table.rows[3].cells for p in cell.paragraphs for run in p.runs)
    path=tmp_path/'table.docx';doc.save(path)
    assert len(Document(path).tables[0].rows)==4


def test_dense_reference_table_body_is_black_and_numbers_do_not_wrap():
    doc=Document()
    columns=['专业',*[f'指标{i}' for i in range(8)]]
    table=add_data_table(doc,columns,[['测试专业',*['100.00']*8]],[(0,1,'专业'),(1,4,'校内评价'),(5,4,'校外评价')])
    for cell in table.rows[2].cells:
        for p in cell.paragraphs:
            for run in p.runs:
                assert run.font.color.rgb==RGBColor(0,0,0) and run.font.size.pt==9
    assert table.rows[2].cells[1]._tc.tcPr.find(qn('w:noWrap')) is not None


def test_duplicate_statistics_removed_but_action_matrix_retained():
    data={'datasetId':'C-result','type':'table','columns':['学院','评价项目','比例'], 'rows':[['甲学院','学习指导','90']]}
    table=ContentTable(title='指导结果摘录',columns=data['columns'],rows=data['rows'],source_ids=['C-result'])
    assert duplicate_statistic_table(table,[data])
    table.rows=[['甲学院','改进导师指导','开展生涯访谈']]
    assert not duplicate_statistic_table(table,[data])


def test_process_disclaimer_removed_but_real_sample_scope_retained():
    text='在正在境内读研的毕业生中，满意度为90%。上述判断均基于本届调查，学院差异描述不涉及统计显著性检验。'
    assert formal_text(text)=='在正在境内读研的毕业生中，满意度为90%。'


def test_writing_limits_respect_explicit_user_length():
    assert writing_limits('专业结构与培养定位',True)['maxBlocks']==8
    assert writing_limits('第十章 AI方向',True)['maxBlocks']==10
    longer=writing_limits('第十章 AI方向',True,'请写约6000字。')
    assert longer['maxBlocks']>=20 and '6000' in longer['targetCharacters']


def test_extension_equivalent_headings_merge_and_export_both_bodies(tmp_path):
    from assess_agent.exporter import export_document
    from assess_agent.schemas import JobRequest, SectionDraft
    from assess_agent.validation import document_paths
    store,table=survey_store(['届次','就业人数'],[['2025','80']])
    title='第十章 AI培养方向'
    draft=SectionDraft(title=title,blocks=[
        {'kind':'recommendation','subsection':'未来五年情景假设与培养方向','text':'建立AI学习成果评价。'},
        {'kind':'recommendation','subsection':'未来 5 年情景假设与培养方向','text':'以项目作品检验能力。'}])
    assert document_paths(draft)==['未来五年情景假设与培养方向']
    request=JobRequest(task_id=uuid4(),school='测试大学',year=2025,model_id='glm',chapters=[CHAPTERS[1]],
        materials=[{'id':table.material_id,'name':table.name,'category':'DATA'}])
    path=tmp_path/'topic.docx'
    export_document(request,[draft],store,path,'毕业生培养质量评价报告',[title])
    document=Document(path)
    assert [p.text for p in document.paragraphs if p.style.name=='Heading 2']==document_paths(draft)
    prose=''.join(p.text for p in document.paragraphs)
    assert '建立AI学习成果评价' in prose and '以项目作品检验能力' in prose
    # Presentation does not mutate frozen draft subsection IDs.
    assert draft.blocks[1].subsection=='未来 5 年情景假设与培养方向'


def test_pending_list_keeps_actual_resource_gap_but_not_rejected_extra_sentence():
    from types import SimpleNamespace
    from assess_agent.exporter import pending_analysis_rows
    from assess_agent.schemas import SectionDraft
    from assess_agent.validation import template_coverage
    title='第九章 个性化报告'
    draft=SectionDraft(title=title,blocks=[
        {'kind':'recommendation','subsection':name+' / 改进建议','text':'依据实际问题设计评价。'}
        for name in ('专业结构与培养定位','毕业学习成果','课程与实践教学体系')]+[
        {'kind':'gap','subsection':'专业结构与培养定位 / 改进建议','text':'此前某一句判断未通过校验。'}])
    store=SimpleNamespace(visuals=None,content_gap_notes={})
    assert template_coverage(draft)['writtenTopics']==3
    assert pending_analysis_rows([draft],store)==[]
    store.visuals=SimpleNamespace(context=lambda _: [{'subsection':'毕业学习成果','status':'missing',
        'templateId':'figure-example','caption':'某项能力达成率','reason':'缺少该指标计算公式。'}])
    assert pending_analysis_rows([draft],store)==[[title,'某项能力达成率','缺少该指标计算公式。']]
