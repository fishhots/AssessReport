from uuid import uuid4

import pytest
from docx import Document
from docx.oxml.ns import qn

from assess_agent.exporter import export_document
from assess_agent.report_style import core_heading_conclusions, chapter_introduction_paragraphs
from assess_agent.schemas import JobRequest, SectionDraft
from assess_agent.settings import CHAPTERS, REPORT_CONFIG
from assess_agent.validation import document_paths, validate_section
from assess_agent.partial_content import salvage_section
from assess_agent.coverage_recovery import recover_coverage
from tests.test_report_improvements import survey_store


def request_for(table, chapters):
    return JobRequest(task_id=uuid4(), school='测试大学', year=2025, model_id='deepseek', chapters=chapters,
        materials=[{'id':table.material_id, 'name':table.name, 'category':'DATA'}])


def test_background_is_between_each_selected_chapter_and_its_summary(tmp_path):
    store, table = survey_store(['届次'], [['2025']])
    chapters = CHAPTERS[1:8]
    sections = [SectionDraft(title=t, blocks=[{'kind':'gap','subsection':'本章小结','text':'指标数据待补充。'}])
        for t in chapters]
    output = tmp_path/'chapter-backgrounds.docx'
    export_document(request_for(table, chapters), sections, store, output, '毕业生培养质量评价报告', chapters)
    paragraphs = Document(output).paragraphs
    for title in chapters:
        index = next(i for i,p in enumerate(paragraphs) if p.style.name=='Heading 1' and p.text==title)
        introduction = next(c['introduction'] for c in REPORT_CONFIG['chapters'] if c['title']==title)
        opening = chapter_introduction_paragraphs(introduction)
        assert [p.text for p in paragraphs[index+1:index+1+len(opening)]]==opening
        assert all(p.style.name=='Normal' for p in paragraphs[index+1:index+1+len(opening)])
        assert not any(r.bold for p in paragraphs[index+1:index+1+len(opening)] for r in p.runs)
        assert paragraphs[index+1+len(opening)].text=='本章小结'
        assert paragraphs[index+1+len(opening)].style.name=='Heading 2'
    assert not any(p.style.name.startswith('Heading ') and p.text in ('背景介绍','背景概括') for p in paragraphs)


def test_core_blue_conclusions_follow_fixed_topics_and_normal_summaries_remain_in_body(tmp_path):
    store, table = survey_store(['届次','指标'], [['2025','81.11']])
    summaries = {
        '教学成效 / 适应度':'培养定位与就业去向衔接较好，重点群体支持仍需加强。',
        '教学成效 / 达成度':'能力培养获得积极反馈，高阶能力训练仍有提升空间。',
        '教学成效 / 保障度':'学习资源与校园设施提供支撑，部分服务仍可完善。',
        '教学成效 / 满意度':'在校体验获得毕业生认可，服务精准度仍需提升。',
        '培养过程 / 产教融合':'合作项目形成培养支撑，实践参与覆盖仍可扩大。',
        '培养过程 / “AI+”教育':'人工智能教学形成基础，专业融合与项目训练仍需加强。',
        '培养过程 / 课堂教学':'课程教学提供专业支撑，教学内容更新仍可加强。',
        '培养过程 / 实践与双创':'实践训练获得积极评价，创新项目支持仍可完善。',
        '教师队伍 / 教学投入':'教师指导提供成长支持，跨环节育人仍需加强。',
    }
    core = SectionDraft(title=CHAPTERS[0], blocks=[{'kind':'finding','subsection':path,'summary':summary,
        'text':path.split(' / ')[-1]+'对应指标为81.11%。相关反馈可用于识别培养优势与改进重点。',
        'source_ids':[table.source_ids[0]]} for path,summary in summaries.items()])
    normal_summary = '毕业生初期就业表现较好，职业发展支持仍需完善。'
    normal = SectionDraft(title=CHAPTERS[2], blocks=[{'kind':'finding','subsection':'本章小结',
        'summary':normal_summary,'text':'就业质量对应指标为81.11%。','source_ids':[table.source_ids[0]]}])
    before = core.model_dump()
    output = tmp_path/'core-headings.docx'
    export_document(request_for(table, [core.title,normal.title]), [core,normal], store, output,
        '毕业生培养质量评价报告', [core.title,normal.title])
    paragraphs = Document(output).paragraphs
    headings = [p for p in paragraphs if p.style.name=='Heading 3 Core' and '：' in p.text]
    assert [p.text for p in headings]==[path.split(' / ')[-1]+'：'+summary.rstrip('。')
        for path,summary in summaries.items()]
    assert len(headings)==9
    for heading in headings:
        assert all(str(r.font.color.rgb)=='006699' for r in heading.runs)
        num = heading._p.pPr.find(qn('w:numPr'))
        assert num is not None and num.find(qn('w:ilvl')).get(qn('w:val'))=='2'
    for path,summary in summaries.items():
        assert not any(summary in p.text for p in paragraphs if p.style.name=='Normal')
        assert any(path.split(' / ')[-1]+'对应指标为81.11%。' in p.text for p in paragraphs)
    normal_paragraph = next(p for p in paragraphs if p.text.startswith(normal_summary))
    assert normal_paragraph.runs[0].bold
    assert '81.11%' in normal_paragraph.text
    assert core.model_dump()==before
    assert document_paths(core)==[p for c in REPORT_CONFIG['chapters'] if c['title']==core.title
        for group in c['outline'] for p in [group['title'],*[group['title']+' / '+s for s in group['children']]]]


@pytest.mark.parametrize('topic_prefix', ['', '适应度：', '适应度: '])
def test_legacy_qualitative_lead_moves_to_heading_without_losing_evidence(tmp_path, topic_prefix):
    store, table = survey_store(['届次','指标'], [['2025','81.11']])
    draft = SectionDraft(title=CHAPTERS[0], blocks=[{'kind':'finding','subsection':'教学成效 / 适应度',
        'text':topic_prefix+'培养定位落实较好。毕业去向落实率为81.11%。','source_ids':[table.source_ids[0]]},
        {'kind':'gap','subsection':'教学成效 / 达成度','text':'能力评价资料待补充。'},
        {'kind':'recommendation','subsection':'教学成效 / 保障度','text':'学习资源保障较好。'}])
    output = tmp_path/'legacy-core.docx'
    export_document(request_for(table,[draft.title]), [draft], store, output, '毕业生培养质量评价报告', [draft.title])
    paragraphs = Document(output).paragraphs
    assert any(p.text=='适应度：培养定位落实较好' and p.style.name=='Heading 3 Core' for p in paragraphs)
    assert any(p.text=='毕业去向落实率为81.11%。' and p.style.name=='Normal' for p in paragraphs)
    assert any(p.text=='达成度' and p.style.name=='Heading 3 Core' for p in paragraphs)
    assert not any(p.text.startswith('保障度：') for p in paragraphs)
    assert not any('培养定位落实较好' in p.text for p in paragraphs if p.style.name=='Normal')


def test_numerical_or_recommendation_summary_is_never_a_core_heading_conclusion():
    draft = SectionDraft(title=CHAPTERS[0], blocks=[
        {'kind':'finding','subsection':'教学成效 / 适应度','summary':'就业落实率为81.11%。','text':'就业落实情况已统计。'},
        {'kind':'recommendation','subsection':'培养过程 / 产教融合','summary':'合作培养成效突出。','text':'开展合作项目。'},
        {'kind':'gap','subsection':'教学成效 / 达成度','summary':'能力培养成效突出。','text':'能力资料待补充。'},
    ])
    assert core_heading_conclusions(draft)=={}


def test_summary_analysis_precedes_advice_and_parent_heading_has_no_body(tmp_path):
    store, table = survey_store(['届次','指标'], [['2025','81.11']])
    source_id = table.source_ids[0]
    draft = SectionDraft(title=CHAPTERS[1], blocks=[
        {'kind':'recommendation','subsection':'本章小结','text':'完善分类就业支持。'},
        {'kind':'finding','subsection':'本章小结','summary':'就业结果总体稳定，就业支持仍需进一步细化。',
         'text':'毕业生就业结果已形成统计，可据此识别重点支持群体。','source_ids':[source_id]},
        {'kind':'finding','subsection':'毕业去向','text':'这段父标题概述不应进入正式报告。','source_ids':[source_id]},
        {'kind':'finding','subsection':'毕业去向 / 毕业去向落实率',
         'text':'毕业去向落实率为81.11%。','source_ids':[source_id]},
    ])
    output = tmp_path/'manual-structure.docx'
    export_document(request_for(table,[draft.title]), [draft], store, output,
        '毕业生培养质量评价报告', [draft.title])
    paragraphs = Document(output).paragraphs
    summary = next(i for i,p in enumerate(paragraphs) if p.text=='本章小结')
    analysis = next(i for i,p in enumerate(paragraphs) if '毕业生就业结果已形成统计' in p.text)
    advice = next(i for i,p in enumerate(paragraphs) if p.text.startswith('【建议】'))
    assert summary < analysis < advice
    parent = next(i for i,p in enumerate(paragraphs) if p.text=='毕业去向' and p.style.name.startswith('Heading '))
    next_nonempty = next(p for p in paragraphs[parent+1:] if p.text.strip())
    assert next_nonempty.text=='毕业去向落实率'
    assert next_nonempty.style.name.startswith('Heading ')
    assert not any('父标题概述不应进入正式报告' in p.text for p in paragraphs)


def test_validation_rejects_direct_advice_summary_and_parent_body():
    store, table = survey_store(['届次','指标'], [['2025','81.11']])
    draft = SectionDraft(title=CHAPTERS[1], blocks=[
        {'kind':'recommendation','subsection':'本章小结','text':'完善分类就业支持。'},
        {'kind':'finding','subsection':'毕业去向','text':'父标题概述。','source_ids':[table.source_ids[0]]},
    ])
    issues = validate_section(draft,draft.title,store,2025,review_semantics=False)
    assert any('本章小结必须先有' in issue for issue in issues)
    assert any('二级父标题“毕业去向”' in issue for issue in issues)


@pytest.mark.parametrize('title,path',[(CHAPTERS[4],'德育成效'),
                                      (CHAPTERS[6],'毕业生满意度 / 校友满意度')])
def test_reference_parent_body_exceptions_remain_visible(tmp_path,title,path):
    store,table=survey_store(['届次','指标'],[['2025','81.11']])
    text='人工版在此处保留的总体分析。'
    draft=SectionDraft(title=title,blocks=[{'kind':'finding','subsection':path,
        'text':text,'source_ids':[table.source_ids[0]]}])
    assert not validate_section(draft,title,store,2025,review_semantics=False)
    output=tmp_path/'allowed-parent.docx'
    export_document(request_for(table,[title]),[draft],store,output,'毕业生培养质量评价报告',[title])
    paragraphs=Document(output).paragraphs
    heading=next(i for i,p in enumerate(paragraphs) if p.text==path.split(' / ')[-1]
        and p.style.name.startswith('Heading '))
    assert paragraphs[heading+1].text==text


def test_salvage_preserves_advice_and_recovery_builds_missing_summary_analysis(monkeypatch):
    store,table=survey_store(['届次','指标'],[['2025','81.11']])
    draft=SectionDraft(title=CHAPTERS[1],blocks=[
        {'kind':'recommendation','subsection':'本章小结','text':'加强重点群体就业指导。'},
        {'kind':'finding','subsection':'毕业去向 / 毕业去向落实率',
         'text':'毕业生就业结果已形成统计。','source_ids':[table.source_ids[0]]}])
    salvaged,_=salvage_section(draft,draft.title,store,2025,'就业数据。')
    assert salvaged.blocks[0]==draft.blocks[0]
    monkeypatch.setattr('assess_agent.coverage_recovery.topic_packets',lambda *args:[])
    class Writer:
        evidence=store
    recovered,_=recover_coverage(Writer(),salvaged,{'query':'就业'},2025)
    summary=[b for b in recovered.blocks if b.subsection=='本章小结']
    assert [b.kind for b in summary]==['finding','recommendation']
    assert summary[0].source_ids==[table.source_ids[0]]
    assert not any('本章小结必须先有' in issue for issue in validate_section(recovered,draft.title,store,2025))
