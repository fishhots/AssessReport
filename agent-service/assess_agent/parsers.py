from pathlib import Path
from hashlib import sha256
from itertools import islice
import csv
import io
import json
import re
import zipfile
import posixpath
import xml.etree.ElementTree as ET

from .errors import AgentError
from .schemas import Corpus, Evidence, JobRequest, MaterialInput, ParsedTable
from .settings import Settings

PERSONAL_HEADERS = re.compile(r"姓名|身份证|手机|电话号码|电子邮箱|邮箱地址|学号|通讯地址|用户ID|用户编号", re.IGNORECASE)


def evidence_id(material_id: str, location: str, text: str) -> str:
    return "E-" + sha256(f"{material_id}|{location}|{text}".encode()).hexdigest()[:16]


def source_path(settings: Settings, material: MaterialInput) -> Path:
    root = settings.upload_root.resolve()
    path = (root / str(material.id)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AgentError("MATERIAL_NOT_FOUND", f"资料不存在：{material.name}")
    return path


def value_text(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def add_text(corpus: Corpus, material: MaterialInput, text: str, location: str):
    text = text.replace("\x00", "").strip()
    if not text:
        return
    for offset in range(0, len(text), 2200):
        part = text[offset:offset + 2200]
        loc = f"{location}；片段 {offset // 2200 + 1}"
        corpus.evidence.append(Evidence(
            id=evidence_id(str(material.id), loc, part), material_id=str(material.id),
            name=material.name, category=material.category, location=loc, text=part,
        ))


def parse_rows(corpus: Corpus, material: MaterialInput, sheet: str, input_rows, settings: Settings, merged_ranges=()):
    rows = []
    chars = 0
    for row in input_rows:
        if len(rows) >= settings.max_table_rows:
            raise AgentError("MATERIAL_LIMIT", f"表格行数超过限制：{material.name} / {sheet}")
        values = [value_text(value) for value in row]
        chars += sum(len(value) for value in values)
        if chars > settings.max_source_chars:
            raise AgentError("MATERIAL_LIMIT", f"表格内容超过解析限制：{material.name}")
        rows.append(values)
    # Expand only actual vertical merged text labels, never ordinary blank cells or numeric indicators.
    for left, top, right, bottom in merged_ranges:
        if left != right or top == bottom or top < 1 or bottom > len(rows) or left > len(rows[top - 1]):
            continue
        anchor = rows[top - 1][left - 1]
        if not anchor or re.fullmatch(r"[\d.,%％+-]+", anchor):
            continue
        for row in rows[top:bottom]:
            if left <= len(row) and not row[left - 1]:
                row[left - 1] = anchor
    if not any(any(row) for row in rows):
        corpus.warnings.append(f"{material.name} / {sheet} 没有可用表格数据")
        return
    # 多行标题的指标表取前 20 行中非空文本字段最多的一行为表头，不臆造字段含义。
    candidates = [(sum(bool(v) and not re.fullmatch(r"[\d.%+-]+", v) for v in row), i)
                  for i, row in enumerate(rows[:20])]
    _, header_index = max(candidates, key=lambda pair: (pair[0], -pair[1]))
    # A blank header must not silently discard populated data columns.
    width = max((i+1 for row in rows[header_index:] for i,v in enumerate(row) if v),default=0)
    columns = []
    seen = {}
    for index in range(width):
        label = (rows[header_index][index] if index<len(rows[header_index]) else '') or f"列{index + 1}"
        seen[label] = seen.get(label, 0) + 1
        columns.append(label if seen[label] == 1 else f"{label}（{seen[label]}）")
    keep = [i for i, name in enumerate(columns) if not PERSONAL_HEADERS.search(name)]
    identifiers = [i for i,c in enumerate(columns) if re.fullmatch(r'用户ID|用户编号|学号',c,re.IGNORECASE)]
    record_keys = []
    if len(identifiers)==1:
        ii=identifiers[0]
        record_keys=[sha256(f'{material.id}|record|{r[ii]}'.encode()).hexdigest()
                     if ii<len(r) and r[ii] else '' for r in rows[header_index+1:]]
    if len(keep) != len(columns):
        corpus.warnings.append(f"{material.name} / {sheet} 已排除直接身份字段")
    columns = [columns[i] for i in keep]
    if not columns:
        return
    data = [[row[i] if i < len(row) else "" for i in keep] for row in rows[header_index + 1:]]
    for index, row in enumerate(rows[:header_index + 1], 1):
        add_text(corpus, material, " | ".join(row), f"工作表 {sheet}，表头说明第 {index} 行")
    ids = []
    for offset in range(0, len(data), 12):
        batch = data[offset:offset + 12]
        start = header_index + offset + 2
        end = start + len(batch) - 1
        text = "字段：" + " | ".join(columns) + "\n" + "\n".join(" | ".join(row) for row in batch)
        before = len(corpus.evidence)
        add_text(corpus, material, text, f"工作表 {sheet}，原始行 {start}–{end}")
        ids.extend(item.id for item in corpus.evidence[before:])
    corpus.tables.append(ParsedTable(material_id=str(material.id), name=material.name, sheet=sheet,
                                    columns=columns, rows=data, start_row=header_index + 2, source_ids=ids,
                                    record_keys=record_keys))


def check_zip(path: Path):
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > 200 * 1024 * 1024:
            raise AgentError("MATERIAL_LIMIT", "Office 文档解压后体积超过限制")


def xlsx_merged_ranges(path: Path, formula_gaps=None) -> dict:
    from openpyxl.utils.cell import range_boundaries
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relation = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    output = {}
    with zipfile.ZipFile(path) as archive:
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.attrib["Id"]: r.attrib["Target"] for r in relations}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.findall(f"{{{main}}}sheets/{{{main}}}sheet"):
            target = targets[sheet.attrib[f"{{{relation}}}id"]]
            member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            ranges = []
            uncached=[]
            with archive.open(member) as stream:
                for _, element in ET.iterparse(stream, events=("end",)):
                    if element.tag == f"{{{main}}}mergeCell":
                        ranges.append(range_boundaries(element.attrib["ref"]))
                    if element.tag==f'{{{main}}}c' and element.find(f'{{{main}}}f') is not None:
                        cache=element.find(f'{{{main}}}v')
                        if cache is None or not cache.text:uncached.append(element.attrib.get('r',''))
                    if element.tag in (f'{{{main}}}c',f'{{{main}}}row',f'{{{main}}}mergeCell'):
                        element.clear()
            output[sheet.attrib["name"]] = ranges
            if formula_gaps is not None and uncached:
                formula_gaps.append({'sheet':sheet.attrib['name'],'count':len(uncached),'cells':uncached[:5]})
    return output


def parse_material(corpus: Corpus, material: MaterialInput, settings: Settings):
    path = source_path(settings, material)
    suffix = Path(material.name).suffix.lower()
    try:
        if suffix in (".xlsx", ".xls"):
            if suffix == ".xlsx":
                from openpyxl import load_workbook
                check_zip(path)
                formula_gaps=[]
                merges = xlsx_merged_ranges(path,formula_gaps)
                for gap in formula_gaps:
                    corpus.warnings.append(f"{material.name} / {gap['sheet']} 有{gap['count']}个公式单元格缺少已计算缓存（示例：{','.join(gap['cells'])}），需要在Excel中重新计算并保存；这些值未作为0参与统计。")
                # Uploaded files are stored by UUID without an extension.
                stream = path.open("rb")
                workbook = load_workbook(stream, read_only=True, data_only=True)
                try:
                    for sheet in workbook.worksheets:
                        if sheet.max_row and sheet.max_row > settings.max_table_rows:
                            raise AgentError("MATERIAL_LIMIT", f"表格行数超过限制：{material.name}")
                        parse_rows(corpus, material, sheet.title, sheet.iter_rows(values_only=True), settings, merges.get(sheet.title, ()))
                finally:
                    workbook.close()
                    stream.close()
            else:
                import xlrd
                workbook = xlrd.open_workbook(str(path), on_demand=True, formatting_info=True)
                try:
                    for sheet in workbook.sheets():
                        parse_rows(corpus, material, sheet.name,
                                   (sheet.row_values(i) for i in range(sheet.nrows)), settings,
                                   [(c1 + 1, r1 + 1, c2, r2) for r1, r2, c1, c2 in sheet.merged_cells])
                finally:
                    workbook.release_resources()
        elif suffix == ".csv":
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("gb18030")
            parse_rows(corpus, material, "CSV", csv.reader(io.StringIO(text)), settings)
        elif suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise AgentError("PDF_ENCRYPTED", f"PDF 已加密：{material.name}")
            for index, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if not text.strip():
                    corpus.warnings.append(f"{material.name} 第 {index} 页无可提取文本，可能需要 OCR")
                add_text(corpus, material, text, f"第 {index} 页")
        elif suffix == ".docx":
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            check_zip(path)
            document = Document(path)
            for index, element in enumerate(document.element.body, 1):
                if element.tag.endswith("}p"):
                    add_text(corpus, material, Paragraph(element, document).text, f"正文块 {index}")
                elif element.tag.endswith("}tbl"):
                    table = Table(element, document)
                    add_text(corpus, material, "\n".join(" | ".join(cell.text for cell in row.cells)
                                                      for row in table.rows), f"正文表格 {index}")
        elif suffix == ".doc":
            if not material.extracted_text:
                raise AgentError("DOC_REQUIRES_BRIDGE", "旧版 Word 资料需通过 Spring Boot 的 Word 解析桥接提交")
            add_text(corpus, material, material.extracted_text, "旧版 Word 正文")
        elif suffix == ".txt":
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("gb18030")
            add_text(corpus, material, text, "正文")
        else:
            raise AgentError("MATERIAL_FORMAT", f"暂不支持解析的格式：{material.name}")
    except AgentError:
        raise
    except Exception as exc:
        raise AgentError("MATERIAL_PARSE", f"资料无法解析，请检查文件是否完整：{material.name}") from exc


def parse_job(request: JobRequest, settings: Settings) -> Corpus:
    corpus = Corpus()
    total_chars = 0
    first_parse_error = None
    for material in request.materials:
        before = len(corpus.evidence)
        try:
            parse_material(corpus, material, settings)
        except AgentError as error:
            if error.code not in ("MATERIAL_PARSE", "MATERIAL_EMPTY", "PDF_ENCRYPTED", "DOC_REQUIRES_BRIDGE", "MATERIAL_FORMAT"):
                raise
            first_parse_error = first_parse_error or error
            corpus.evidence = [e for e in corpus.evidence if e.material_id != str(material.id)]
            corpus.tables = [t for t in corpus.tables if t.material_id != str(material.id)]
            corpus.warnings.append(f"{material.name} 未能提取可用内容，已跳过")
            continue
        if len(corpus.evidence) == before:
            corpus.warnings.append(f"{material.name} 没有可用文本或数据，已跳过")
            continue
        total_chars += sum(len(item.text) for item in corpus.evidence[before:])
        if total_chars > settings.max_source_chars:
            raise AgentError("MATERIAL_LIMIT", "本次资料内容超过解析限制，请减少单次资料量")
    if not corpus.evidence:
        if first_parse_error:
            raise first_parse_error
        raise AgentError("MATERIAL_EMPTY", "已上传资料中没有可读取的文本或数据，请提供至少一份可用资料")
    return corpus
