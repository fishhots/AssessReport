from pathlib import Path
from hashlib import sha256
import zipfile
import re
import json
from copy import deepcopy
from math import ceil

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_SECTION_START
from docx.oxml import OxmlElement
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .errors import AgentError
from .schemas import JobRequest, SectionDraft
from .settings import CHAPTERS, DIAGNOSIS_TITLES, REPORT_CONFIG, SERVICE_ROOT
from .validation import subsection_paths, document_paths, template_coverage, subsection_aliases
from .report_style import (formal_text, visual_note, qualitative_lead, recommendation_paragraph,
                           core_heading_conclusions, without_heading_conclusion)
from .table_layout import comparison_table_view, duplicate_statistic_table


def set_font(style, name: str, size: int):
    style.font.name = name
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor(0, 0, 0)
    style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), name)


def new_document(request: JobRequest, title: str) -> Document:
    template = SERVICE_ROOT / 'config/report-template.docx'
    profile = json.loads((SERVICE_ROOT / 'config/template-profile.json').read_text(encoding='utf-8'))
    document = Document(template)
    for part in [document.part, *[r.target_part for r in document.part.rels.values()
                                 if not r.is_external and r.reltype.endswith(('/header','/footer'))]]:
        for node in part.element.xpath('.//w:t'):
            node.text = (node.text or '').replace('{{SCHOOL}}', request.school).replace('{{YEAR}}', str(request.year))
            if part == document.part and node.text == '2025':
                node.text = str(request.year)
    for name, role in profile['roles'].items():
        style = document.styles[name]
        for property_name in ('pPr', 'rPr'):
            existing = style.element.find(qn('w:' + property_name))
            if existing is not None:
                style.element.remove(existing)
            if role[property_name]:
                properties = parse_xml(role[property_name])
                for child in list(properties):
                    if child.tag in {qn('w:pStyle'), qn('w:numPr'), qn('w:sectPr'), qn('w:rPr')}:
                        properties.remove(child)
                style.element.append(properties)
        if name.startswith('Heading '):
            for old in list(style.element.get_or_add_pPr().findall(qn('w:outlineLvl'))):
                style.element.get_or_add_pPr().remove(old)
            level = OxmlElement('w:outlineLvl');level.set(qn('w:val'), str(int(name[-1])-1))
            style.element.get_or_add_pPr().append(level)
        if name == 'Normal':
            style.font.bold = False
            complex_bold = style.element.get_or_add_rPr().find(qn('w:bCs'))
            if complex_bold is not None:complex_bold.set(qn('w:val'),'0')
    update = document.settings.element.find(qn('w:updateFields'))
    if update is None:
        update = OxmlElement('w:updateFields');document.settings.element.append(update)
    update.set(qn('w:val'), 'true')
    return document


def legacy_document(request: JobRequest, title: str) -> Document:
    """Kept for reading legacy layout specifications; new reports use the native template."""
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.2)
    section.left_margin = section.right_margin = Cm(2.4)
    for name, font, size in [("Normal", "宋体", 11), ("Title", "黑体", 22),
                             ("Heading 1", "黑体", 16), ("Heading 2", "黑体", 13),
                             ("Heading 3", "黑体", 11), ("Caption", "宋体", 10)]:
        set_font(document.styles[name], font, size)
    normal = document.styles["Normal"].paragraph_format
    normal.line_spacing = 1.5
    normal.space_after = Pt(7)
    normal.first_line_indent = Cm(.74)
    for name in ("Heading 1", "Heading 2", "Heading 3", "Caption"):
        document.styles[name].paragraph_format.keep_with_next = True
        document.styles[name].paragraph_format.first_line_indent = Cm(0)
    cover = document.add_paragraph(f"{request.school}{request.year}届", style="Title")
    cover.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cover.paragraph_format.space_before = Pt(100)
    document.add_paragraph(title, style="Title").alignment = WD_ALIGN_PARAGRAPH.CENTER
    update = OxmlElement('w:updateFields');update.set(qn('w:val'),'true')
    document.settings.element.append(update)
    return document


def heading_text(chapter, path):
    text = path.split(' / ')[-1]
    if chapter not in CHAPTERS and path not in subsection_paths(chapter):
        text = re.sub(r'^(?:[一二三四五六七八九十百零\d]+[、.．]\s*|[（(][一二三四五六七八九十百零\d]+[）)]\s*)', '', text)
    return text


def template_heading(document, text, chapter, path='', level=1, numbering_state=None):
    profile = json.loads((SERVICE_ROOT / 'config/template-profile.json').read_text(encoding='utf-8'))
    pattern = profile.get('headingPatterns', {}).get(chapter + '|' + path, {})
    level = pattern.get('level', level)
    paragraph = document.add_heading(text, level=level)
    original = pattern.get('pPr') or profile['roles'][f'Heading {level}']['pPr']
    if original:
        properties = parse_xml(original)
        for child in list(properties):
            if child.tag in {qn('w:pStyle'), qn('w:sectPr')} or (level == 1 and child.tag == qn('w:numPr')):
                properties.remove(child)
        style = OxmlElement('w:pStyle');style.set(qn('w:val'), document.styles[f'Heading {level}'].style_id)
        properties.insert(0, style)
        num_properties = properties.find(qn('w:numPr'))
        if num_properties is not None and numbering_state is not None:
            original_id = int(num_properties.find(qn('w:numId')).get(qn('w:val')))
            key = (chapter, original_id)
            if key not in numbering_state:
                numbering = document.part.numbering_part.element
                source_num = next(n for n in numbering.findall(qn('w:num')) if int(n.get(qn('w:numId'))) == original_id)
                cloned = deepcopy(source_num)
                new_id = max(int(n.get(qn('w:numId'))) for n in numbering.findall(qn('w:num'))) + 1
                cloned.attrib.clear();cloned.set(qn('w:numId'), str(new_id))
                for ilvl in (1,2,3):
                    override = next((n for n in cloned.findall(qn('w:lvlOverride')) if n.get(qn('w:ilvl')) == str(ilvl)),None)
                    if override is None:
                        override=OxmlElement('w:lvlOverride');override.set(qn('w:ilvl'),str(ilvl));cloned.append(override)
                    start=override.find(qn('w:startOverride'))
                    if start is None:start=OxmlElement('w:startOverride');override.insert(0,start)
                    start.set(qn('w:val'),'1')
                numbering.append(cloned)
                numbering_state[key] = new_id
            num_properties.find(qn('w:numId')).set(qn('w:val'), str(numbering_state[key]))
        paragraph._p.replace(paragraph._p.pPr, properties)
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.keep_together = True
    return paragraph


def body_section(document, title, request=None):
    profile = json.loads((SERVICE_ROOT / 'config/template-profile.json').read_text(encoding='utf-8'))
    section=document.add_section(WD_SECTION_START.NEW_PAGE)
    for kind, paragraphs in profile['pageFurniture'].items():
        furniture = getattr(section, kind)
        furniture.is_linked_to_previous = False
        element = furniture._element
        relationships = {}
        for old_id, asset in profile.get('furnitureAssets',{}).get(kind,{}).items():
            relationships[old_id], _ = furniture.part.get_or_add_image(str(SERVICE_ROOT/'config'/asset))
        for child in list(element):element.remove(child)
        for xml in paragraphs:
            paragraph = parse_xml(xml)
            for node in paragraph.iter():
                for attribute in (qn('r:id'),qn('r:embed'),qn('r:link')):
                    if node.get(attribute) in relationships:node.set(attribute,relationships[node.get(attribute)])
            for node in paragraph.iter(qn('w:t')):
                if request:
                    node.text = (node.text or '').replace('{{SCHOOL}}',request.school).replace('{{YEAR}}',str(request.year))
                if kind.endswith('footer') and (node.text or '').isdigit():node.text = '1'
            element.append(paragraph)
    for old in list(section._sectPr.findall(qn('w:pgNumType'))):section._sectPr.remove(old)
    page=OxmlElement('w:pgNumType');page.set(qn('w:start'),'1');section._sectPr.append(page)


def add_bookmark(paragraph, name, number):
    start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(number));start.set(qn('w:name'),name)
    end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),str(number))
    paragraph._p.insert(0,start);paragraph._p.append(end)


def add_contents(document, sections):
    if len(document.sections) == 1:
        document.add_section(WD_SECTION_START.NEW_PAGE)
    document.add_paragraph('目录',style='Title')
    # A live TOC with a readable, clickable fallback, rather than invented page
    # numbers. Word updates the field from the actual Heading 1–3 layout.
    first=document.add_paragraph()
    begin=OxmlElement('w:fldChar');begin.set(qn('w:fldCharType'),'begin');first.add_run()._r.append(begin)
    instruction=OxmlElement('w:instrText');instruction.set(qn('xml:space'),'preserve')
    instruction.text=' TOC \\o "1-3" \\h \\z \\u '
    first.add_run()._r.append(instruction)
    separate=OxmlElement('w:fldChar');separate.set(qn('w:fldCharType'),'separate');first.add_run()._r.append(separate)
    for i,section in enumerate(sections):
        p=document.add_paragraph();p.paragraph_format.first_line_indent=Cm(0)
        link=OxmlElement('w:hyperlink');link.set(qn('w:anchor'),f'chapter_{i}')
        run=OxmlElement('w:r');text=OxmlElement('w:t');text.text=section.title;run.append(text);link.append(run);p._p.append(link)
    last=document.add_paragraph()
    end=OxmlElement('w:fldChar');end.set(qn('w:fldCharType'),'end');last.add_run()._r.append(end)


def add_data_table(document, columns, rows, header_groups=None):
    table = document.add_table(rows=1, cols=len(columns))
    table.style = "Table Grid"
    table.autofit = False
    numeric = [all(re.fullmatch(r'[\d,.%％+\-—]+',str(row[i])) for row in rows) for i in range(len(columns))]
    weights=[2.6 if i==0 and not numeric[i] else 1.6 if not numeric[i] else 1 for i in range(len(columns))]
    if (len(columns) == 3 and '学院' in columns[0] and '专业' in columns[1]
            and any(label in columns[2] for label in ('主要行业', '主要职业'))):
        # Grouped destinations need room for three named industries or occupations.
        weights = [1.5, 1.6, 3.0]
    if len(columns)==2 and '专业' in columns[0] and any(t in columns[1] for t in ('主要行业','主要职业')):
        weights=[1.7,4.4]
    if len(columns)==3 and columns[0]=='职业类' and all('高频使用比例' in c for c in columns[1:]):
        weights=[1.5,3.0,3.0]
    width_cm = document.sections[-1].page_width.cm-document.sections[-1].left_margin.cm-document.sections[-1].right_margin.cm
    widths=[width_cm*w/sum(weights) for w in weights]
    profile = json.loads((SERVICE_ROOT / 'config/template-profile.json').read_text(encoding='utf-8'))
    original = profile['tableProperties'].get(str(len(columns)))
    if original:
        table._tbl.replace(table._tbl.tblPr, parse_xml(original))
    cell_formats = profile['tableFormats'].get(str(len(columns)), next(iter(profile['tableFormats'].values())))
    for col,width in zip(table.columns,widths):col.width=Cm(width)
    header_count = 2 if header_groups else 1
    all_rows = [*([columns]*header_count), *rows]
    # Avoid an isolated last row in short comparison matrices. Estimate wrapped
    # lines so long cells are not forced into an oversized two-row block.
    wrapped_lines=sum(max(ceil(sum(1 if ord(ch)>127 else .5 for ch in str(value)) /
                              max(1,width*28.35/11)) for value,width in zip(values,widths))
                      for values in all_rows)
    keep_short_table=len(rows)<=8 and wrapped_lines<=22
    for index, values in enumerate(all_rows):
        row = table.rows[0] if not index else table.add_row()
        is_header = index < header_count
        if is_header:
            header = OxmlElement('w:tblHeader');row._tr.get_or_add_trPr().append(header)
        prevent_split = OxmlElement("w:cantSplit")
        row._tr.get_or_add_trPr().append(prevent_split)
        for column_index, (cell, value, width) in enumerate(zip(row.cells, values, widths)):
            role = cell_formats[('header' if is_header else 'body')+('Label' if not column_index else 'Value')]
            properties = parse_xml(role['tcPr'])
            for child in list(properties):
                if child.tag in {qn('w:tcW'),qn('w:gridSpan'),qn('w:vMerge'),qn('w:hMerge')}:
                    properties.remove(child)
            cell._tc.replace(cell._tc.tcPr, properties)
            cell.width = Cm(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            display=str(value)
            if not is_header and numeric[column_index] and display not in ('—','-') and (header_groups or '%' in columns[column_index] or '％' in columns[column_index]):
                try:display=f'{float(display.rstrip("%％").replace(",","")):.2f}'
                except ValueError:pass
            cell.text = display
            paragraph = cell.paragraphs[0]
            if role['pPr']:
                properties = parse_xml(role['pPr'])
                for child in list(properties):
                    if child.tag in {qn('w:pStyle'),qn('w:numPr'),qn('w:sectPr')}:
                        properties.remove(child)
                paragraph._p.insert(0,properties)
            paragraph.paragraph_format.keep_with_next = is_header or (keep_short_table and index==len(all_rows)-2)
            paragraph.paragraph_format.keep_together = True
            if len(columns)>=8:
                paragraph.paragraph_format.first_line_indent=Cm(0)
                paragraph.paragraph_format.line_spacing=1.05
                paragraph.paragraph_format.space_before=Pt(0)
                paragraph.paragraph_format.space_after=Pt(0)
                margins=cell._tc.tcPr.find(qn('w:tcMar'))
                if margins is None:margins=OxmlElement('w:tcMar');cell._tc.tcPr.append(margins)
                for side in ('left','right'):
                    node=margins.find(qn('w:'+side))
                    if node is None:node=OxmlElement('w:'+side);margins.append(node)
                    node.set(qn('w:w'),'45');node.set(qn('w:type'),'dxa')
            if not is_header and numeric[column_index]:
                paragraph.alignment=WD_ALIGN_PARAGRAPH.CENTER
                paragraph.paragraph_format.first_line_indent=Cm(0)
                paragraph.paragraph_format.left_indent=Cm(0)
                paragraph.paragraph_format.right_indent=Cm(0)
                no_wrap=OxmlElement('w:noWrap');cell._tc.tcPr.append(no_wrap)
                margins=cell._tc.tcPr.find(qn('w:tcMar'))
                if margins is None:margins=OxmlElement('w:tcMar');cell._tc.tcPr.append(margins)
                for side in ('left','right'):
                    node=margins.find(qn('w:'+side))
                    if node is None:node=OxmlElement('w:'+side);margins.append(node)
                    node.set(qn('w:w'),'45');node.set(qn('w:type'),'dxa')
            for run in paragraph.runs:
                if role['rPr']:
                    run._r.insert(0,parse_xml(role['rPr']))
                # The extracted body sample can be the reference's bold school
                # average row. Do not apply that emphasis to every data row.
                run.bold = is_header or str(values[0]) in ('本校平均','全校平均')
                run.font.color.rgb=RGBColor(255,255,255) if is_header else RGBColor(0,0,0)
                if len(columns)>=8:run.font.size=Pt(9)
                elif not is_header and numeric[column_index]:run.font.size=Pt(10)
                cs = run._r.get_or_add_rPr().find(qn('w:bCs'))
                if cs is not None:cs.set(qn('w:val'),'1' if run.bold else '0')
            if not is_header:
                shading=cell._tc.tcPr.find(qn('w:shd'))
                if shading is None:shading=OxmlElement('w:shd');cell._tc.tcPr.append(shading)
                shading.set(qn('w:fill'),'F2F2F2' if (index-header_count)%2 else 'FFFFFF')
    if header_groups:
        for start,size,label in header_groups:
            base_p=deepcopy(table.cell(0,start).paragraphs[0]._p.pPr)
            base_r=deepcopy(table.cell(0,start).paragraphs[0].runs[0]._r.rPr)
            if size == 1:
                cell=table.cell(0,start).merge(table.cell(1,start))
            else:
                cell=table.cell(0,start).merge(table.cell(0,start+size-1))
            cell.text=label
            for paragraph in cell.paragraphs:
                if base_p is not None:paragraph._p.insert(0,deepcopy(base_p))
                paragraph.alignment=WD_ALIGN_PARAGRAPH.CENTER
                paragraph.paragraph_format.first_line_indent=Cm(0)
                paragraph.paragraph_format.keep_with_next=True
                for run in paragraph.runs:
                    if base_r is not None:run._r.insert(0,deepcopy(base_r))
                    run.bold=True
                    run.font.color.rgb=RGBColor(255,255,255)
    return table


def chart_dimensions(kind, width, height, available=15.3):
    # Physical size includes the labels. Dense bars keep more height than pies.
    max_width, max_height = ((8.0, 8.0) if kind in ('pie', 'donut') else
                            (available, 11.0) if kind in ('horizontal_bar', 'stacked_bar', 'mirror_bar') else
                            (available, 8.5))
    result = min(available, max_width, max_height * width / height)
    return result, result * height / width


def visual_caption(caption, data):
    if data.get('cohort') and data.get('pooledCohorts') is False:
        caption = re.sub(r'[（(]\s*20\d{2}\s*届[、，,]\s*20\d{2}\s*届合并\s*[）)]',
                         f"（{data['cohort']}届）", caption)
    return caption


def add_visual(document, registry, visual, references):
    registry.validate(visual.asset_id, visual.template_id)
    asset = registry.assets[visual.asset_id]
    data = registry.datasets[asset["datasetId"]]
    caption_text = visual_caption(asset['caption'], data)
    if data["type"] == "table":
        caption = document.add_paragraph(caption_text,style='Caption')
        caption.paragraph_format.keep_with_next = True
        caption.runs[0].bold = True
        view=comparison_table_view(data)
        for index,panel in enumerate(view.get('panels') or [view]):
            if index:
                caption=document.add_paragraph('续'+caption_text,style='Caption')
                caption.paragraph_format.keep_with_next=True
                caption.runs[0].bold=True
            if panel['headerGroups'] or data.get('templateId')=='table-4-2':
                unit=document.add_paragraph('单位：%')
                unit.alignment=WD_ALIGN_PARAGRAPH.RIGHT
                unit.paragraph_format.first_line_indent=Cm(0)
                unit.paragraph_format.line_spacing=1
                unit.paragraph_format.space_after=Pt(2)
                unit.paragraph_format.keep_with_next=True
                unit.runs[0].font.size=Pt(9)
            add_data_table(document,panel['columns'],panel['rows'],panel['headerGroups'])
    else:
        from PIL import Image
        for index, file in enumerate(asset["files"]):
            path = registry.directory / file["filename"]
            with Image.open(path) as image:
                available = document.sections[-1].page_width.cm-document.sections[-1].left_margin.cm-document.sections[-1].right_margin.cm
                width, _ = chart_dimensions(data['type'], image.width, image.height, available)
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.first_line_indent=Cm(0)
            paragraph.paragraph_format.line_spacing = 1
            paragraph.paragraph_format.space_before = Pt(3)
            paragraph.paragraph_format.space_after = Pt(3)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.keep_with_next = True
            paragraph.add_run().add_picture(str(path), width=Cm(width))
            caption = document.add_paragraph(('续' if index else '')+caption_text,style='Caption')
            caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
            caption.paragraph_format.keep_with_next = index == len(asset["files"]) - 1 and bool(visual_note(data))
            caption.runs[0].bold = True
    note_text=visual_note(data)
    if data['type']=='table' and view.get('note'):
        note_text=note_text.rstrip('。')+'；'+view['note']+'。' if note_text else '注：'+view['note']+'。'
    if data['type']=='table' and view['headerGroups'] and any(c=='—' for r in view['rows'] for c in r):
        note_text=(note_text or '注：').rstrip('。')+'；—表示无可用同口径指标值。'
    if not note_text:return
    note = document.add_paragraph(note_text)
    note.paragraph_format.line_spacing = 1.15
    note.paragraph_format.first_line_indent=Cm(0)
    for run in note.runs:
        run.font.size = Pt(9)


def pending_analysis_rows(sections, store):
    """Describe real uncompleted items, not rejected sentences in written items."""
    pending = []
    for section in sections:
        topics = template_coverage(section)['unwrittenTopics']
        resources = store.visuals.context(section.title) if store.visuals else []
        grouped = {}
        for path in topics:
            matched = [r for r in resources if r['subsection']==path]
            missing = [r for r in matched if r['status']=='missing']
            reasons = list(dict.fromkeys(r.get('reason','') for r in missing if r.get('reason')))
            if reasons:
                reason = '；'.join(reasons)
            elif any(r['status'] in ('prepared','rendered') for r in matched):
                reason = '已取得该项统计结果，正文尚未完成。'
            else:
                reason = '该项分析正文尚未完成。'
                note = store.content_gap_notes.get(section.title, '')
                if '后续可补充：' in note:
                    reason += '后续可补充：'+note.split('后续可补充：')[-1][:150]
            grouped.setdefault(reason, []).append(path.split(' / ')[-1])
        # A written item can still lack a specific formula/chart. Name that
        # resource precisely instead of calling the whole item uncompleted.
        for resource in resources:
            if resource['status']!='missing' or resource['subsection'] in topics:
                continue
            reason = resource.get('reason') or '该图表尚未完成。'
            grouped.setdefault(reason, []).append(resource.get('caption') or resource['templateId'])
        if not subsection_paths(section.title) and not any(b.kind!='gap' and formal_text(b.text) for b in section.blocks) and not section.tables:
            grouped.setdefault('本章正文尚未完成。', []).append('正文')
        for reason, names in grouped.items():
            pending.append([section.title, '；'.join(dict.fromkeys(names)), reason])
    return pending


def export_document(request: JobRequest, sections: list[SectionDraft], store, path: Path,
                    title: str, expected_titles: list[str], refresh_fields=False) -> dict:
    if [section.title for section in sections] != expected_titles:
        raise AgentError("EXPORT_SCOPE", "导出章节或诊断范围不符合任务")
    from .extension_style import normalize_extension_structure
    requirements=request.instructions+' '+(request.custom_topic.requirements if request.custom_topic else '')
    sections=[normalize_extension_structure(s.model_copy(deep=True),requirements) for s in sections]
    document = new_document(request, title)
    add_contents(document,sections)
    body_section(document,f'{request.school}{request.year}届{title}',request)
    used = []
    for section in sections:
        for block in section.blocks:
            used.extend(block.source_ids)
        for table in section.tables:
            used.extend(table.source_ids)
        for visual in section.visuals:
            store.visuals.validate(visual.asset_id, visual.template_id, section.title)
            used.extend(store.visuals.assets[visual.asset_id]["sourceIds"])
    used = list(dict.fromkeys(used))
    def references(keys):
        return ""

    numbering_state = {}
    expected_outline = {}
    for index, section in enumerate(sections):
        heading=template_heading(document,section.title,section.title,numbering_state=numbering_state)
        if index:
            heading.paragraph_format.page_break_before=True
        add_bookmark(heading,f'chapter_{index}',index)
        paths=document_paths(section)
        aliases=subsection_aliases(section)
        fixed_paths=subsection_paths(section.title)
        chapter_config=next((c for c in REPORT_CONFIG['chapters'] if c['title']==section.title), {})
        introduction=chapter_config.get('introduction', '')
        if introduction:
            # This is template background and scope, not a generated finding or
            # an extra subsection. It belongs before the original chapter summary.
            document.add_paragraph(introduction)
        core_conclusions=core_heading_conclusions(section)
        expected_outline[section.title]=[]
        recommendation_paths=set(chapter_config.get('recommendationPaths', []))
        labelled_recommendations=set()
        fallback=fixed_paths[0] if fixed_paths else ''
        from .visuals import ITEMS
        mixed=any(b.kind!='gap' for b in section.blocks) or bool(section.visuals or section.tables)
        occupied={b.subsection or fallback for b in section.blocks if b.kind!='gap' and formal_text(b.text)}
        occupied.update(t.subsection or fallback for t in section.tables)
        occupied.update(ITEMS.get(v.template_id,{}).get('subsection','') or fallback for v in section.visuals)
        def add_group(path):
            introduced = False
            for block in section.blocks:
                assigned=aliases.get(block.subsection,block.subsection) or fallback
                if assigned != path:continue
                if block.kind=='gap':continue
                text=re.sub(r'\{\$学校名称\}',request.school,block.text)
                text=re.sub(r'\{\$届次\}',str(request.year),text)
                text=formal_text(text)
                if not text:continue
                # The original report labels selected summary/action paragraphs.
                # Extensions may request a label once per main theme.
                label_scope=path if fixed_paths else path.split(' / ')[0]
                show_label=(block.kind=='recommendation' and label_scope not in labelled_recommendations and
                            (path in recommendation_paths or (not fixed_paths and text.startswith('【建议】'))))
                text=recommendation_paragraph(block,text,show_label)
                if path in core_conclusions and block.kind in ('finding', 'inference'):
                    text=without_heading_conclusion(text,core_conclusions[path],path.split(' / ')[-1])
                if not text:continue
                if show_label:labelled_recommendations.add(label_scope)
                paragraph = document.add_paragraph()
                lead, remainder = qualitative_lead(block, text) if not introduced and path not in core_conclusions else ('', text)
                if lead:
                    paragraph.add_run(lead).bold = True
                    paragraph.add_run(remainder+references(block.source_ids))
                else:
                    paragraph.add_run(text+references(block.source_ids))
                introduced = True
            for visual in section.visuals:
                assigned=ITEMS.get(visual.template_id,{}).get('subsection','') or fallback
                if assigned==path:add_visual(document,store.visuals,visual,references)
            for data in section.tables:
                if (aliases.get(data.subsection,data.subsection) or fallback)!=path:continue
                attached=[store.visuals.datasets[store.visuals.assets[v.asset_id]['datasetId']]
                          for v in section.visuals] if store.visuals else []
                if duplicate_statistic_table(data,attached):continue
                caption=document.add_paragraph(data.title,style='Caption')
                caption.runs[0].bold=True
                add_data_table(document,data.columns,data.rows)
        if paths:
            if not fixed_paths:
                add_group('')
            for group_path in paths:
                group_text=heading_text(section.title,group_path)
                if group_path in core_conclusions:group_text+='：'+core_conclusions[group_path]
                expected_outline[section.title].append(group_text)
                group_heading=template_heading(document,group_text,section.title,group_path,
                                  level=min(4,len(group_path.split(' / '))+1),numbering_state=numbering_state)
                if group_path in core_conclusions:
                    # Match the manual report's blue heading; numbering remains
                    # the original Word automatic numbering for this fixed path.
                    color=group_heading.style.font.color.rgb or RGBColor.from_string('006699')
                    for run in group_heading.runs:run.font.color.rgb=color
                add_group(group_path)
                if (fixed_paths and group_path not in occupied and
                        not any(p.startswith(group_path+' / ') for p in paths)):
                    # Preserve the title without repeating placeholder prose.
                    # Explicitly end the keep-with-next chain for an empty leaf.
                    group_heading.paragraph_format.keep_with_next = False
        else:add_group('')
        if not occupied:
            document.add_paragraph('本章分析未完成，相关项目见文末说明。')
    gaps=[r for s in sections for r in (store.visuals.context(s.title) if store.visuals else []) if r['status']=='missing']
    pending=pending_analysis_rows(sections,store)
    if pending:
        heading=document.add_heading('未开展分析的项目',level=1)
        heading.paragraph_format.page_break_before=True
        add_data_table(document,['章节','项目','说明'],pending)
    # Store full traceability next to the export, never as a report appendix.
    audit_path=path.with_suffix('.audit.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    audit_path.write_text(json.dumps({'sections':[s.model_dump() for s in sections],
        'sourceIds':used,'sources':[store.read(k).model_dump(by_alias=True) for k in used],
        'coverage':store.topic_coverage,'missingVisuals':gaps},ensure_ascii=False,indent=2),encoding='utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.docx")
    document.save(temporary)
    layout = {'status': 'notRequested'}
    if refresh_fields:
        from .office import refresh_word_fields
        layout = refresh_word_fields(temporary)
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() or "word/document.xml" not in archive.namelist():
            raise AgentError("EXPORT_FILE", "Word 文件结构校验失败")
    loaded = Document(temporary)
    headings = [p.text for p in loaded.paragraphs if p.style.name == "Heading 1" and p.text in expected_titles]
    if headings != expected_titles:
        raise AgentError("EXPORT_HEADINGS", "Word 文档章节校验失败")
    chapter = None
    actual_paths = {}
    for paragraph in loaded.paragraphs:
        if paragraph.style.name == 'Heading 1':
            chapter = paragraph.text if paragraph.text in expected_titles else None
            if chapter:
                actual_paths[chapter] = []
        elif chapter and paragraph.style.name.startswith('Heading '):
            actual_paths[chapter].append(paragraph.text)
    for section in sections:
        if actual_paths[section.title] != expected_outline[section.title]:
            raise AgentError('EXPORT_OUTLINE', 'Word 文档小节名称或顺序与模板不一致')
    temporary.replace(path)
    return {"filename": path.name, "size": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest(), 'layout': layout}


def export_pair(request: JobRequest, report: list[SectionDraft], diagnosis: list[SectionDraft], store, directory: Path) -> dict:
    return {
        "report": export_document(request, report, store, directory / "report.docx", "毕业生培养质量评价报告", request.chapters),
        "diagnosis": export_document(request, diagnosis, store, directory / "diagnosis.docx", "深度诊断框架", DIAGNOSIS_TITLES),
    }


def export_report(request: JobRequest, sections: list[SectionDraft], store, directory: Path, refresh_fields=True) -> dict:
    return {"report": export_document(request, sections, store, directory / "report.docx",
        "毕业生培养质量评价报告", request.chapters + request.extension_titles(), refresh_fields)}
