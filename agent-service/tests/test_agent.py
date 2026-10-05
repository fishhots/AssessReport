import json
import time
from uuid import uuid4
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
import pytest

from assess_agent.app import create_app
from assess_agent.errors import AgentError
from assess_agent.evidence import EvidenceStore
from assess_agent.models import SectionWriter, provider_status
from assess_agent.parsers import parse_job
from assess_agent.schemas import JobRequest, SectionDraft
from assess_agent.settings import CHAPTERS, DIAGNOSIS_TITLES, Settings
from assess_agent.store import JobStore
from assess_agent.validation import validate_section
from assess_agent.workflow import run_workflow
from fake_model import ToolCallingTestModel


def setup_task(tmp_path, chapters=None):
    upload = tmp_path / "uploads"
    upload.mkdir()
    material_id, task_id = str(uuid4()), str(uuid4())
    (upload / material_id).write_text("指标,人数\n就业,80\n有效样本,100\n", encoding="utf-8")
    request = JobRequest.model_validate({"taskId": task_id, "school": "离线测试学校", "year": 2025,
        "modelId": "qwen", "chapters": chapters or [CHAPTERS[1]],
        "materials": [{"id": material_id, "name": "离线测试数据.csv", "category": "DATA"}]})
    settings = Settings(upload_root=upload, data_root=tmp_path / "agent", max_model_calls=80, update_word_fields=False)
    return settings, request


def test_evidence_tools_and_numeric_validation(tmp_path):
    settings, request = setup_task(tmp_path)
    store = EvidenceStore(parse_job(request, settings))
    table = store.corpus.tables[0]
    source = store.search("就业")[0]["id"]
    counts = store.summarize(table.material_id, table.sheet, "指标")
    assert counts["groups"][0]["validCount"] == 2
    stats = store.summarize(table.material_id, table.sheet, "人数", "numeric")
    assert stats["groups"][0]["total"] == "180"
    assert stats["groups"][0]["mean"] == "90.0000" or stats["groups"][0]["mean"] == "90"
    ratio = store.ratio(80, 100, [source])
    assert ratio["percentage"] == "80.00%"
    with pytest.raises(AgentError):
        store.ratio(80, 0, [source])
    with pytest.raises(AgentError):
        store.ratio(50, 100, [source])
    with pytest.raises(AgentError):
        store.read("foreign-task-evidence")
    draft = SectionDraft(title=CHAPTERS[1], blocks=[{"kind": "finding", "text": "就业人数为 999 人", "source_ids": [source]}])
    assert validate_section(draft, CHAPTERS[1], store, 2025)
    draft.blocks[0].text = "就业人数为 80 人"
    assert not validate_section(draft, CHAPTERS[1], store, 2025)


def test_langchain_really_calls_registered_tools(tmp_path):
    settings, request = setup_task(tmp_path)
    store = EvidenceStore(parse_job(request, settings))
    writer = SectionWriter(settings, request, store, model=ToolCallingTestModel())
    draft = writer.write(CHAPTERS[1], "就业")
    assert draft.title == CHAPTERS[1]
    assert draft.blocks[0].source_ids[0] in store.by_id
    assert writer.budget.calls == 2
    assert writer.budget.input_tokens == 2


def test_selected_chapters_full_diagnosis_and_durable_state(tmp_path):
    settings, request = setup_task(tmp_path, [CHAPTERS[3], CHAPTERS[1]])
    assert request.chapters == [CHAPTERS[1], CHAPTERS[3]]
    jobs = JobStore(settings.data_root)
    row, created = jobs.create(request)
    assert created
    assert jobs.create(request)[1] is False
    result = run_workflow(request, settings, jobs, row["attempt"], model=ToolCallingTestModel())
    jobs.finish(row["id"], row["attempt"], result)
    fresh = JobStore(settings.data_root)
    status = fresh.view(fresh.get(str(request.task_id)))
    assert status["status"] == "SUCCEEDED"
    assert all(step["status"] == "COMPLETED" for step in status["steps"])
    root = settings.jobs_root() / str(request.task_id) / "attempt-1"
    report = Document(root / "report.docx")
    titles = lambda doc: [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert [t for t in titles(report) if t in CHAPTERS] == request.chapters
    assert "第九章 个性化报告" in titles(report)
    assert set(result['artifacts']) == {'report'}
    assert not (root / 'diagnosis.docx').exists()
    assert [p.text for p in report.paragraphs if p.style.name == 'Heading 2' and p.text in DIAGNOSIS_TITLES] == DIAGNOSIS_TITLES
    assert status["result"]["model"]["provider"] == "test"
    assert result["usage"]["calls"] == 10


def test_http_async_download_and_failure_guard(tmp_path):
    settings, request = setup_task(tmp_path)
    with TestClient(create_app(settings, model=ToolCallingTestModel())) as client:
        response = client.post("/jobs", json=request.model_dump(mode="json", by_alias=True))
        assert response.status_code == 202
        # This checks asynchronous completion and artifact guards, not a two-
        # second performance target. Durable checkpoints add filesystem work.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = client.get(f"/jobs/{request.task_id}").json()
            if status["status"] in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(0.02)
        assert status["status"] == "SUCCEEDED", status
        for kind in ("report",):
            download = client.get(f"/jobs/{request.task_id}/artifacts/{kind}")
            assert download.status_code == 200
            assert download.content.startswith(b"PK")
        assert client.post(f"/jobs/{request.task_id}/retry").status_code == 409
        assert client.get(f"/jobs/{request.task_id}/artifacts/unknown").status_code == 404
        root = settings.jobs_root() / str(request.task_id) / "attempt-1"
        (root / "report.docx").write_bytes(b"changed")
        assert client.get(f"/jobs/{request.task_id}/artifacts/report").status_code == 409


def test_unconfigured_provider_and_service_token(tmp_path, monkeypatch):
    settings, request = setup_task(tmp_path)
    monkeypatch.setattr(Settings, "provider_config", lambda self, provider: {
        "id": provider, "key": "", "model": "", "base_url": "", "extra_body": "{}"})
    assert not any(entry["configured"] for entry in provider_status(settings))
    settings.service_token = "test-internal-token"
    with TestClient(create_app(settings)) as client:
        assert client.get("/jobs").status_code == 401
        response = client.post("/jobs", json=request.model_dump(mode="json", by_alias=True), headers={"X-Agent-Token": "test-internal-token"})
        assert response.status_code == 422
        assert response.json()["code"] == "MODEL_NOT_CONFIGURED"
        assert client.get("/jobs", headers={"X-Agent-Token": "test-internal-token"}).json() == []


def test_unverified_numbers_are_skipped_and_other_sections_still_export(tmp_path):
    settings, request = setup_task(tmp_path)
    jobs = JobStore(settings.data_root)
    row, _ = jobs.create(request)
    result = run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel(invalid_numbers=True))
    jobs.finish(row["id"], 1, result)
    assert jobs.get(row["id"])["status"] == "SUCCEEDED"
    assert result["contentGaps"] and len(result["artifacts"]) == 1
    for kind in ("report",):
        text = "\n".join(p.text for p in Document(settings.jobs_root() / row["id"] / "attempt-1" / f"{kind}.docx").paragraphs)
        assert "999" not in text
        assert "未开展分析的项目" in text
        assert (root := settings.jobs_root() / row['id'] / 'attempt-1' / 'report.audit.json').is_file()


def test_bad_ids_empty_input_and_personal_columns(tmp_path):
    settings, request = setup_task(tmp_path)
    path = settings.upload_root / str(request.materials[0].id)
    path.write_text("专业,姓名,人数\n计算机,测试名字,80\n", encoding="utf-8")
    corpus = parse_job(request, settings)
    assert corpus.tables[0].columns == ["专业", "人数"]
    assert "测试名字" not in " ".join(e.text for e in corpus.evidence)
    path.write_text("", encoding="utf-8")
    with pytest.raises(AgentError) as error:
        parse_job(request, settings)
    assert error.value.code == "MATERIAL_EMPTY"
    payload = request.model_dump(mode="json", by_alias=True)
    payload["materials"][0]["id"] = "../../anything"
    with TestClient(create_app(settings, model=ToolCallingTestModel())) as client:
        assert client.post("/jobs", json=payload).status_code == 422
