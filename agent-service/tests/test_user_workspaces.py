"""Account isolation tests; no model API or report generation is invoked."""
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from assess_agent.app import create_app
from assess_agent.errors import AgentError
from assess_agent.model_config import ModelConfigStore
from assess_agent.parsers import source_path
from assess_agent.schemas import JobRequest
from assess_agent.settings import Settings


def config(key='alice-test-secret'):
    return {'model': 'user-chosen-model', 'baseUrl': 'https://example.invalid/v1',
            'apiKey': key, 'extraBody': {}, 'revision': 0}


def test_scoped_configs_jobs_sources_and_restart(tmp_path):
    settings = Settings(data_root=tmp_path / 'agent', upload_root=tmp_path / 'backend' / 'uploads',
                        service_token='internal-test-token')
    alice, bob, task_id, material_id = (str(uuid4()) for _ in range(4))
    internal = {'X-Agent-Token': 'internal-test-token'}
    a, b = ({**internal, 'X-Workspace-User': user} for user in (alice, bob))
    # Existing single-user credentials must never be inherited by a new account.
    from assess_agent.model_config import ModelConfigInput
    ModelConfigStore(settings.data_root).save('deepseek', ModelConfigInput.model_validate(config('legacy-secret')))
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get('/models', headers={'X-Workspace-User': alice}).status_code == 401
        assert client.get('/models', headers={**internal, 'X-Workspace-User': '../outside'}).status_code == 401
        assert not any(model['configured'] for model in client.get('/models', headers=a).json())
        saved = client.put('/models/qwen/config', json=config(), headers=a)
        assert saved.status_code == 200 and saved.json()['configured']
        assert 'alice-test-secret' not in saved.text
        assert client.get('/models/qwen/config', headers=b).json()['hasApiKey'] is False
        assert client.get('/models/qwen/config', headers=b).json()['revision'] == 0
        assert client.put('/models/qwen/config', json=config('bob-test-secret'), headers=b).status_code == 200
        ar, br = app.state.runtimes[alice], app.state.runtimes[bob]
        assert ar.model_configs.runtime('qwen')['key'] == 'alice-test-secret'
        assert br.model_configs.runtime('qwen')['key'] == 'bob-test-secret'
        assert ar.settings.data_root == settings.data_root / 'users' / alice
        assert ar.settings.upload_root == tmp_path / 'backend' / 'users' / alice / 'uploads'
        request = JobRequest.model_validate({'taskId': task_id, 'school': '甲校', 'year': 2026,
            'modelId': 'qwen', 'chapters': ['第一章 社会需求适应'],
            'materials': [{'id': material_id, 'name': 'sample.csv', 'category': 'DATA'}]})
        ar.settings.upload_root.mkdir(parents=True)
        (ar.settings.upload_root / material_id).write_text('专业,人数\n信息,80', encoding='utf-8')
        assert source_path(ar.settings, request.materials[0]).is_file()
        with pytest.raises(AgentError) as missing:
            source_path(br.settings, request.materials[0])
        assert missing.value.code == 'MATERIAL_NOT_FOUND'
        ar.jobs.create(request)
        assert client.get('/jobs', headers=a).json()[0]['taskId'] == task_id
        assert client.get('/jobs', headers=b).json() == []
        for method, path in [('get', f'/jobs/{task_id}'), ('post', f'/jobs/{task_id}/retry'),
                             ('post', f'/jobs/{task_id}/cancel'), ('delete', f'/jobs/{task_id}'),
                             ('get', f'/jobs/{task_id}/artifacts/report')]:
            # Deletion is idempotent in the internal API, but never touches Alice's namespace.
            assert getattr(client, method)(path, headers=b).status_code == (204 if method == 'delete' else 404)
        assert client.get(f'/jobs/{task_id}', headers=a).json()['status'] == 'QUEUED'
        assert client.post(f'/jobs/{task_id}/cancel', headers=a).json()['status'] == 'CANCELLED'
        # Even equal task IDs live in separate databases.
        br.jobs.create(request.model_copy(update={'school': '乙校'}))
        assert client.get(f'/jobs/{task_id}', headers=b).json()['status'] == 'QUEUED'
        assert client.get(f'/jobs/{task_id}', headers=a).json()['status'] == 'CANCELLED'
        assert client.delete(f'/jobs/{task_id}', headers=a).status_code == 204
        assert client.get(f'/jobs/{task_id}', headers=b).status_code == 200
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/models/qwen/config', headers=a).json()['configured']
        assert restarted.get('/models/qwen/config', headers=b).json()['configured']
        assert restarted.get('/models/deepseek/config', headers=a).json()['configured'] is False
        assert restarted.get('/jobs', headers=a).json() == []
        assert restarted.get(f'/jobs/{task_id}', headers=b).json()['status'] == 'INTERRUPTED'
    assert b'alice-test-secret' not in (settings.data_root / 'users' / alice / 'model-config.sqlite3').read_bytes()
