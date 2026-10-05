import json
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from assess_agent.app import create_app
from assess_agent.errors import AgentError
from assess_agent.store import JobStore
from tests.test_agent import setup_task
from fake_model import ToolCallingTestModel


def wait_status(client, task_id, expected):
    for _ in range(200):
        row = client.get(f"/jobs/{task_id}").json()
        if row["status"] == expected:
            return row
        time.sleep(.02)
    pytest.fail(f"Expected {expected}, got {row['status']}")


def test_running_and_queued_cancellation_never_launch_more_model_calls(tmp_path, monkeypatch):
    settings, request = setup_task(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = ToolCallingTestModel._generate
    def blocked(self, *args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(8)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ToolCallingTestModel, "_generate", blocked)
    with TestClient(create_app(settings, model=ToolCallingTestModel())) as client:
        try:
            assert client.post("/jobs", json=request.model_dump(mode="json", by_alias=True)).status_code == 202
            assert entered.wait(5)
            other = request.model_copy(update={"task_id": uuid4()})
            client.post("/jobs", json=other.model_dump(mode="json", by_alias=True))
            assert client.post(f"/jobs/{other.task_id}/cancel").json()["status"] == "CANCELLED"
            assert client.post(f"/jobs/{request.task_id}/cancel").json()["status"] == "CANCELLING"
            assert client.delete(f"/jobs/{request.task_id}").status_code == 409
            assert client.post(f"/jobs/{request.task_id}/retry").status_code == 409
        finally:
            release.set()
        wait_status(client, request.task_id, "CANCELLED")
        assert len(calls) == 1
        assert client.get(f"/jobs/{request.task_id}/artifacts/report").status_code == 409
        assert not list(settings.jobs_root().rglob("*.docx"))


def test_cancel_state_cannot_be_overwritten_by_old_work_or_restart(tmp_path):
    settings, request = setup_task(tmp_path)
    jobs = JobStore(settings.data_root)
    row, _ = jobs.create(request)
    jobs.phase(row["id"], 1, 1, "RUNNING")
    jobs.cancel(row["id"])
    with pytest.raises(AgentError):
        jobs.phase(row["id"], 1, 2, "RUNNING")
    jobs.recover_interrupted()
    assert jobs.get(row["id"])["status"] == "CANCELLED"
    assert jobs.retry(row["id"])["attempt"] == 2
    jobs.finish(row["id"], 1, {})
    jobs.fail(row["id"], 1, AgentError("MODEL_ERROR", "旧请求"))
    assert jobs.get(row["id"])["status"] == "QUEUED"
    jobs.cancel(row["id"])
    assert jobs.get(row["id"])["status"] == "CANCELLED"


def test_delete_finished_job_removes_record_and_outputs_but_keeps_uploads(tmp_path):
    settings, request = setup_task(tmp_path)
    with TestClient(create_app(settings, model=ToolCallingTestModel())) as client:
        client.post("/jobs", json=request.model_dump(mode="json", by_alias=True))
        wait_status(client, request.task_id, "SUCCEEDED")
        directory = settings.jobs_root() / str(request.task_id)
        assert directory.exists()
        assert client.delete(f"/jobs/{request.task_id}").status_code == 204
        assert client.delete(f"/jobs/{request.task_id}").status_code == 204
        assert client.get(f"/jobs/{request.task_id}").status_code == 404
        assert client.get(f"/jobs/{request.task_id}/artifacts/report").status_code == 404
        assert not directory.exists()
        assert client.get("/jobs").json() == []
        assert (settings.upload_root / str(request.materials[0].id)).exists()


def test_delete_rejects_task_directory_symlink_to_another_path(tmp_path):
    settings, request = setup_task(tmp_path)
    settings.jobs_root().mkdir(parents=True)
    target = tmp_path / "keep"
    target.mkdir()
    link = settings.jobs_root() / str(request.task_id)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not allow symlinks")
    with TestClient(create_app(settings, model=ToolCallingTestModel())) as client:
        assert client.delete(f"/jobs/{request.task_id}").status_code == 409
    assert target.exists()
