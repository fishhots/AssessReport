from pathlib import Path
from uuid import uuid4
import json

from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter
import pytest

from assess_agent.errors import AgentError
from assess_agent.models import build_model
from assess_agent.parsers import parse_job
from assess_agent.schemas import JobRequest
from assess_agent.settings import CHAPTERS, Settings


def parse_file(tmp_path, name, builder, extracted_text=None):
    material_id = str(uuid4())
    upload = tmp_path / "uploads"
    upload.mkdir(parents=True)
    builder(upload / material_id)
    request = JobRequest.model_validate({"taskId": str(uuid4()), "school": "解析测试学校", "year": 2025,
        "modelId": "qwen", "chapters": [CHAPTERS[0]], "materials": [{"id": material_id,
        "name": name, "category": "DATA", "extractedText": extracted_text}]})
    return parse_job(request, Settings(upload_root=upload, data_root=tmp_path / "jobs"))


def test_uuid_stored_excel_and_merged_title(tmp_path):
    def build(path):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "就业数据"
        sheet.append(["就业调研"])
        sheet.append(["专业", "就业人数", "有效样本"])
        sheet.append(["计算机", 80, 100])
        workbook.save(path)
    corpus = parse_file(tmp_path, "调研.xlsx", build)
    assert corpus.tables[0].columns == ["专业", "就业人数", "有效样本"]
    assert corpus.tables[0].rows == [["计算机", "80", "100"]]
    assert corpus.tables[0].start_row == 3


def test_all_sheets_including_hidden_and_uncached_formulas_are_accounted_for(tmp_path):
    def build(path):
        workbook=Workbook();sheet=workbook.active;sheet.title='问卷'
        sheet.append(['用户ID','届次','学院']);sheet.append(['student-private',2025,'甲'])
        hidden=workbook.create_sheet('课程');hidden.sheet_state='hidden'
        hidden.append(['用户ID','届次','评价']);hidden.append(['student-private',2025,1])
        formulas=workbook.create_sheet('指标');formulas.append(['学院','人数'])
        formulas.append(['甲','=1+1']);workbook.save(path)
    corpus=parse_file(tmp_path,'2025多Sheet.xlsx',build)
    assert [t.sheet for t in corpus.tables]==['问卷','课程','指标']
    assert corpus.tables[0].record_keys[0]==corpus.tables[1].record_keys[0]
    assert all('student-private' not in e.text for e in corpus.evidence)
    assert corpus.tables[2].rows[0][1]==''
    assert any('公式单元格缺少已计算缓存' in w and 'B2' in w for w in corpus.warnings)


def test_docx_paragraph_and_table_locations(tmp_path):
    def build(path):
        document = Document()
        document.add_paragraph("就业人数为 80")
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "指标"
        table.cell(0, 1).text = "人数"
        table.cell(1, 0).text = "就业"
        table.cell(1, 1).text = "80"
        document.save(path)
    corpus = parse_file(tmp_path, "调查.docx", build)
    assert any("正文表格" in source.location and "80" in source.text for source in corpus.evidence)
    assert any("正文块" in source.location for source in corpus.evidence)


def test_legacy_doc_bridge_and_gb_csv(tmp_path):
    doc = parse_file(tmp_path / "doc", "历史.doc", lambda path: path.write_bytes(b"legacy-bridge-fixture"), "就业人数为 80")
    assert "旧版 Word" in doc.evidence[0].location
    csv = parse_file(tmp_path / "csv", "调研.csv", lambda path: path.write_bytes("指标,人数\n就业,80\n".encode("gb18030")))
    assert csv.tables[0].columns == ["指标", "人数"]


def test_scan_pdf_and_corrupt_docx_rejected(tmp_path):
    def pdf(path):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(path)
    with pytest.raises(AgentError) as caught:
        parse_file(tmp_path / "pdf", "扫描.pdf", pdf)
    assert caught.value.code == "MATERIAL_EMPTY"
    with pytest.raises(AgentError) as caught:
        parse_file(tmp_path / "docx", "损坏.docx", lambda path: path.write_bytes(b"invalid zip"))
    assert caught.value.code == "MATERIAL_PARSE"


@pytest.mark.parametrize("provider", ["qwen", "deepseek", "glm", "gpt", "claude", "gemini"])
def test_provider_factory_builds_correct_api_without_network(tmp_path, monkeypatch, provider):
    monkeypatch.setattr(Settings, "provider_config", lambda self, selected: {
        "id": selected, "key": "unit-test-placeholder", "model": "unit-test-model",
        "base_url": "https://example.invalid/v1", "extra_body": "{}"})
    model, metadata = build_model(Settings(data_root=tmp_path), provider)
    assert metadata["provider"] == provider
    if provider == "gpt":
        assert model.use_responses_api is True and model.store is False
    elif provider != "claude":
        assert model.use_responses_api is False
        assert "max_tokens" in model._default_params
        assert "max_completion_tokens" not in model._default_params
