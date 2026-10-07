"""Verify the executor keeps an authenticated user's settings and artifacts."""
from hashlib import sha256
from importlib import import_module
from threading import Event
from uuid import uuid4
import time

from fastapi.testclient import TestClient

from assess_agent.app import create_app
from assess_agent.settings import Settings


def test_worker_and_download_keep_user_scope(tmp_path, monkeypatch):
    settings = Settings(data_root=tmp_path / 'agent', upload_root=tmp_path / 'backend' / 'uploads', service_token='internal-test')
    alice, bob, task_id, material_id = (str(uuid4()) for _ in range(4))
    observed = []

    def offline_workflow(request, scoped, store, attempt, model=None):
        content = (scoped.upload_root / str(request.materials[0].id)).read_bytes()
        observed.append((scoped.data_root, scoped.provider_config('qwen')['key'], content))
        output = scoped.jobs_root() / str(request.task_id) / f'attempt-{attempt}' / 'report.docx'
        output.parent.mkdir(parents=True)
        output.write_bytes(content)
        (output.parent / 'corpus.json').write_bytes(b'redundant parsed data')
        return {'artifacts': {'report': {'sha256': sha256(content).hexdigest()}}}

    monkeypatch.setattr(import_module('assess_agent.app'), 'run_workflow', offline_workflow)
    # Model injection bypasses provider capability checks; the worker is entirely offline.
    app = create_app(settings, model=object())
    with TestClient(app) as client:
        headers = lambda user: {'X-Agent-Token': 'internal-test', 'X-Workspace-User': user}
        for user, content in [(alice, b'Alice private data'), (bob, b'Bob private data')]:
            client.put('/models/qwen/config', headers=headers(user), json={
                'model': 'offline-model', 'baseUrl': 'https://example.invalid/v1', 'apiKey': 'test-' + user, 'revision': 0})
            folder = tmp_path / 'backend' / 'users' / user / 'uploads'
            folder.mkdir(parents=True)
            (folder / material_id).write_bytes(content)
            response = client.post('/jobs', headers=headers(user), json={
                'taskId': task_id, 'school': 'School', 'year': 2026, 'modelId': 'qwen',
                'chapters': ['第一章 社会需求适应'], 'materials': [{'id': material_id, 'name': 'data.txt', 'category': 'DATA'}]})
            assert response.status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            statuses = [client.get(f'/jobs/{task_id}', headers=headers(user)).json()['status'] for user in (alice, bob)]
            if all(status == 'SUCCEEDED' for status in statuses):
                break
            time.sleep(.02)
        assert statuses == ['SUCCEEDED', 'SUCCEEDED']
        assert observed == [(settings.data_root / 'users' / user, 'test-' + user, content)
                            for user, content in [(alice, b'Alice private data'), (bob, b'Bob private data')]]
        assert client.get(f'/jobs/{task_id}/artifacts/report', headers=headers(alice)).content == b'Alice private data'
        assert client.get(f'/jobs/{task_id}/artifacts/report', headers=headers(bob)).content == b'Bob private data'
        assert client.delete(f'/jobs/{task_id}', headers=headers(alice)).status_code == 204
        assert client.get(f'/jobs/{task_id}/artifacts/report', headers=headers(bob)).content == b'Bob private data'
    # Executor shutdown waits for the cleanup that follows the committed status.
    remaining = settings.data_root / 'users' / bob / 'jobs' / task_id
    assert [p.name for p in remaining.rglob('*') if p.is_file()] == ['report.docx']
