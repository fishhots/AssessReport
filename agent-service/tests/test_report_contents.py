from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml.ns import qn
import pytest

from assess_agent.exporter import export_document
from assess_agent.schemas import SectionDraft
from assess_agent.settings import CHAPTERS
from assess_agent.validation import subsection_paths
from tests.test_chapter_opening_and_core_headings import request_for
from tests.test_report_improvements import survey_store


@pytest.mark.parametrize('chapters', [[CHAPTERS[0]], [CHAPTERS[1]], [CHAPTERS[0], CHAPTERS[1]]])
def test_contents_excludes_core_children_and_keeps_other_chapter_headings(tmp_path, chapters):
    store, table = survey_store(['届次'], [['2025']])
    drafts = [SectionDraft(title=title, blocks=[{'kind':'gap', 'text':'对应指标资料待补充。'}])
              for title in chapters]
    path = tmp_path/'report.docx'
    export_document(request_for(table, chapters), drafts, store, path, '毕业生培养质量评价报告', chapters)
    document = Document(path)
    chapter = None
    core_headings, other_headings = [], []
    for p in document.paragraphs:
        if p.style.name == 'Heading 1':
            chapter = p.text if p.text in chapters else None
        elif chapter and p.style.name.startswith('Heading '):
            if chapter == '核心发现':
                core_headings.append(p)
                assert p.style.name in ('Heading 2 Core', 'Heading 3 Core')
                assert not p.style.builtin
                assert p.style.base_style.name == p.style.name.removesuffix(' Core')
                assert p._p.pPr.find(qn('w:outlineLvl')).get(qn('w:val')) == '9'
                assert p._p.pPr.find(qn('w:numPr')) is not None
            else:
                other_headings.append(p)
                assert p.style.name in ('Heading 2', 'Heading 3', 'Heading 4')
    assert [p.text for p in core_headings] == ([p.split(' / ')[-1] for p in subsection_paths(CHAPTERS[0])]
                                            if CHAPTERS[0] in chapters else [])
    assert [p.text for p in other_headings] == ([p.split(' / ')[-1] for p in subsection_paths(CHAPTERS[1])]
                                             if CHAPTERS[1] in chapters else [])
    assert ' TOC ' in document.element.xml


def test_chapter_page_number_tab_uses_dotted_right_stop(tmp_path):
    store, table = survey_store(['届次'], [['2025']])
    draft = SectionDraft(title=CHAPTERS[0], blocks=[{'kind':'gap', 'text':'对应指标资料待补充。'}])
    path = tmp_path/'report.docx'
    export_document(request_for(table, [draft.title]), [draft], store, path,
                    '毕业生培养质量评价报告', [draft.title])
    document = Document(path)
    style = next(s for s in document.styles if s.name.lower() == 'toc 1')
    stops = list(style.paragraph_format.tab_stops)
    assert len(stops) == 1
    assert stops[0].alignment == WD_TAB_ALIGNMENT.RIGHT
    assert stops[0].leader == WD_TAB_LEADER.DOTS
    section = document.sections[1]
    width = section.page_width-section.left_margin-section.right_margin
    assert width*.9 <= stops[0].position <= width
    assert style.paragraph_format.left_indent == 0
    assert style.paragraph_format.first_line_indent == 0
