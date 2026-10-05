from docx import Document
import pytest
from uuid import uuid4

from assess_agent.evidence import EvidenceStore
from assess_agent.errors import AgentError
from assess_agent.exporter import export_document
from assess_agent.models import SectionWriter
from assess_agent.parsers import parse_job
from assess_agent.partial_content import salvage_section
from assess_agent.schemas import SectionDraft
from assess_agent.settings import CHAPTERS, DIAGNOSIS_TITLES
from assess_agent.store import JobStore
from assess_agent.validation import validate_section
from assess_agent.workflow import run_workflow
from fake_model import ToolCallingTestModel
from tests.test_agent import setup_task


def test_partial_validation_preserves_verified_content_and_drops_bad_claims(tmp_path):
    settings, request = setup_task(tmp_path)
    store = EvidenceStore(parse_job(request, settings))
    source = store.search("就业")[0]["id"]
    draft = SectionDraft(title=CHAPTERS[4], blocks=[
        {"kind": "finding", "text": "就业人数80人。", "source_ids": [source]},
        {"kind": "finding", "text": "达成率999%。", "source_ids": [source]}], tables=[
        {"title": "可用数据", "columns": ["人数"], "rows": [["80"]], "source_ids": [source]},
        {"title": "无法核实", "columns": ["人数"], "rows": [["999"]], "source_ids": [source]}],
        visuals=[{"asset_id": "foreign", "template_id": "figure-4-1"}])
    result, skipped = salvage_section(draft, CHAPTERS[4], store, request.year, "五育调查题项与有效样本。")
    assert result.blocks[0] == draft.blocks[0]
    assert result.tables == [draft.tables[0]] and not result.visuals
    assert skipped == {"blocks": 1, "tables": 1, "visuals": 1}
    assert "后续可补充" in result.blocks[-1].text
    assert not validate_section(result, CHAPTERS[4], store, request.year)


def test_one_small_uploaded_file_can_export_all_selected_chapters_and_diagnosis(tmp_path):
    settings, request = setup_task(tmp_path, CHAPTERS)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    result = run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel(invalid_numbers=True))
    directory = settings.jobs_root() / str(request.task_id) / "attempt-1"
    for kind, titles in (("report", [*CHAPTERS, '第九章 个性化报告']),):
        doc = Document(directory / f"{kind}.docx")
        assert [p.text for p in doc.paragraphs if p.style.name == "Heading 1" and p.text in titles] == titles
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "999" not in text and "未开展分析的项目" in text
    assert len(result["contentGaps"]) == len(CHAPTERS) + len(DIAGNOSIS_TITLES)


def test_full_section_still_exports_gap_note_when_only_a_table_is_rejected(tmp_path):
    settings, request = setup_task(tmp_path, [CHAPTERS[4]])
    store = EvidenceStore(parse_job(request, settings))
    source = store.search("就业")[0]["id"]
    draft = SectionDraft(title=CHAPTERS[4], blocks=[
        {"kind": "finding", "text": "就业人数80人。", "source_ids": [source]} for _ in range(96)],
        tables=[{"title": "无法核实", "columns": ["人数"], "rows": [["999"]], "source_ids": [source]}])
    result, skipped = salvage_section(draft, CHAPTERS[4], store, request.year, "有效样本与指标口径。")
    assert len(result.blocks) == 96 and skipped["tables"] == 1
    assert not validate_section(result, CHAPTERS[4], store, request.year)
    path = tmp_path / "report.docx"
    export_document(request, [result], store, path, "毕业生培养质量评价报告", [CHAPTERS[4]])
    text = "\n".join(p.text for p in Document(path).paragraphs)
    assert "未开展分析的项目" in text
    cells=' '.join(c.text for table in Document(path).tables for row in table.rows for c in row.cells)
    assert "有效样本与指标口径" in cells and '999' not in cells


def test_unusable_file_is_skipped_when_another_file_is_readable(tmp_path):
    settings, request = setup_task(tmp_path)
    empty_id = uuid4()
    (settings.upload_root / str(empty_id)).write_text("", encoding="utf-8")
    other = request.materials[0].model_copy(update={"id": empty_id, "name": "不可读取资料.txt"})
    request = request.model_copy(update={"materials": [other, *request.materials]})
    corpus = parse_job(request, settings)
    assert corpus.evidence and any("已跳过" in warning for warning in corpus.warnings)
    assert all(e.material_id != str(empty_id) for e in corpus.evidence)


def test_single_unfinished_chapter_keeps_title_and_all_other_chapters_continue(tmp_path, monkeypatch):
    settings, request = setup_task(tmp_path, [CHAPTERS[4], CHAPTERS[5]])
    original = SectionWriter.write
    def incomplete(self, title, *args, **kwargs):
        if title == CHAPTERS[4]:
            raise AgentError("MODEL_STRUCTURE", "格式不合规")
        return original(self, title, *args, **kwargs)
    monkeypatch.setattr(SectionWriter, "write", incomplete)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    result = run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel())
    assert any(item["title"] == CHAPTERS[4] and item["generationError"] == "MODEL_STRUCTURE" for item in result["contentGaps"])
    doc = Document(settings.jobs_root() / str(request.task_id) / "attempt-1/report.docx")
    assert CHAPTERS[5] in [p.text for p in doc.paragraphs]


def test_real_provider_errors_are_not_mislabeled_as_missing_materials(tmp_path, monkeypatch):
    settings, request = setup_task(tmp_path)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    def denied(*args, **kwargs):
        raise AgentError("MODEL_AUTH", "认证失败")
    monkeypatch.setattr(SectionWriter, "write", denied)
    with pytest.raises(AgentError) as error:
        run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel())
    assert error.value.code == "MODEL_AUTH"
    assert not list(settings.jobs_root().rglob("*.docx"))
