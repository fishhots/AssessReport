"""An export failure must never cause completed chapters to be billed again."""
import json
import subprocess
from pathlib import Path

from docx import Document
import pytest
from fastapi.testclient import TestClient

from assess_agent.app import create_app
from assess_agent.cache_io import cache_path, read_cache_json
from assess_agent.cache_management import cleanup_terminal_task
from assess_agent.errors import AgentError
from assess_agent.export_resume import load_export_snapshot
from assess_agent.models import SectionWriter
from assess_agent.store import JobStore
from assess_agent.workflow import run_workflow
from fake_model import ToolCallingTestModel
from test_agent import setup_task


def fail_export_after_writing(tmp_path, monkeypatch):
    import assess_agent.workflow as workflow
    settings, request = setup_task(tmp_path)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    real_export = workflow.export_report
    monkeypatch.setattr(workflow, 'export_report', lambda *a, **k: (_ for _ in ()).throw(
        AgentError('EXPORT_FILE', '离线模拟导出失败')))
    with pytest.raises(AgentError) as failure:
        run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel())
    jobs.fail(str(request.task_id), 1, failure.value)
    cleanup_terminal_task(settings, jobs, str(request.task_id), 1)
    monkeypatch.setattr(workflow, 'export_report', real_export)
    return settings, request, jobs


def prohibit_generation(monkeypatch):
    import assess_agent.workflow as workflow
    def forbidden(*args, **kwargs):
        pytest.fail('export recovery must not parse uploads or construct/call a model')
    monkeypatch.setattr(workflow, 'parse_job', forbidden)
    monkeypatch.setattr(workflow, 'validate_provider', forbidden)
    monkeypatch.setattr(workflow, 'SectionWriter', forbidden)
    monkeypatch.setattr(SectionWriter, 'write', forbidden)


def test_export_retry_reuses_all_content_after_rules_change_and_missing_upload(tmp_path, monkeypatch):
    settings, request, jobs = fail_export_after_writing(tmp_path, monkeypatch)
    before = settings.jobs_root() / str(request.task_id) / 'attempt-1'
    old = json.loads((before / 'export-checkpoint.json').read_text(encoding='utf-8'))
    for file in settings.upload_root.iterdir():
        file.unlink()
    prohibit_generation(monkeypatch)
    jobs.retry(str(request.task_id), export_only=True)
    result = run_workflow(request, settings, jobs, 2)
    jobs.finish(str(request.task_id), 2, result)
    after = before.parent / 'attempt-2'
    exported = json.loads((after / 'report.audit.json').read_text(encoding='utf-8'))['sections']
    assert exported == old['sections']
    assert result['usage'] == {'calls': 0, 'inputTokens': 0, 'outputTokens': 0}
    assert result['previousUsage']['calls'] > 0
    assert result['resume']['mode'] == 'export'
    assert all(s['status'] == 'COMPLETED' for s in jobs.view(jobs.get(str(request.task_id)))['steps'])
    assert Document(after / 'report.docx')
    assert (after/'data-utilization.json').read_bytes()==(before/'data-utilization.json').read_bytes()


def test_legacy_completed_glm_snapshot_resumes_without_writer(tmp_path, monkeypatch):
    settings, request, jobs = fail_export_after_writing(tmp_path, monkeypatch)
    root = settings.jobs_root() / str(request.task_id) / 'attempt-1'
    saved = json.loads((root / 'export-checkpoint.json').read_text(encoding='utf-8'))
    (root / 'export-checkpoint.json').unlink()
    # Explicitly build the old full-state format, rather than a modern journal.
    (root / 'stage-5.json').write_text(json.dumps({
        'report': saved['sections'][:len(request.chapters)],
        'extensions': saved['sections'][len(request.chapters):]}), encoding='utf-8')
    corpus = read_cache_json(root / 'corpus.json')
    cache_path(root / 'corpus.json').unlink()
    (root / 'corpus.json').write_text(json.dumps(corpus), encoding='utf-8')
    (root / 'report.audit.json').write_text(json.dumps({'sections': saved['sections'],
        'coverage': saved['metadata']['templateCoverage']}), encoding='utf-8')
    prohibit_generation(monkeypatch)
    jobs.retry(str(request.task_id))
    result = run_workflow(request, settings, jobs, 2)
    assert result['usage']['calls'] == 0
    assert result['resume']['sourceAttempt'] == 1


def test_new_editorial_rules_do_not_rewrite_a_previously_validated_export(tmp_path,monkeypatch):
    settings,request,jobs=fail_export_after_writing(tmp_path,monkeypatch)
    import assess_agent.export_resume as resume
    real=resume.validate_section
    calls=[]
    def reviewed(draft,title,store,year,**kwargs):
        calls.append(kwargs.get('review_semantics'))
        return real(draft,title,store,year,**kwargs)
    monkeypatch.setattr(resume,'validate_section',reviewed)
    prohibit_generation(monkeypatch)
    jobs.retry(str(request.task_id),export_only=True)
    result=run_workflow(request,settings,jobs,2)
    assert result['usage']['calls']==0 and calls and all(flag is False for flag in calls)


def test_changed_or_corrupted_export_snapshot_fails_without_paid_fallback(tmp_path, monkeypatch):
    settings, request, jobs = fail_export_after_writing(tmp_path, monkeypatch)
    root = settings.jobs_root() / str(request.task_id) / 'attempt-1'
    changed = request.model_copy(update={'instructions': '不同要求'})
    with pytest.raises(AgentError, match='本次未重新调用模型'):
        load_export_snapshot(changed, root)
    cache_path(root / 'corpus.json').write_bytes(b'corrupted recovery file')
    prohibit_generation(monkeypatch)
    jobs.retry(str(request.task_id))
    with pytest.raises(AgentError) as failure:
        run_workflow(request, settings, jobs, 2)
    assert failure.value.code == 'EXPORT_CHECKPOINT'


def test_retry_api_export_does_not_require_model_key_or_original_upload(tmp_path, monkeypatch):
    settings, request, jobs = fail_export_after_writing(tmp_path, monkeypatch)
    for file in settings.upload_root.iterdir():
        file.unlink()
    prohibit_generation(monkeypatch)
    import assess_agent.app as module
    monkeypatch.setattr(module, 'validate_provider', lambda *a: pytest.fail('no model preflight for export'))
    with TestClient(create_app(settings)) as client:
        response = client.post(f'/jobs/{request.task_id}/retry')
        assert response.status_code == 202
        assert all(s['status'] == 'COMPLETED' for s in response.json()['steps'][:-1])
    assert jobs.get(str(request.task_id))['status'] == 'SUCCEEDED'
    root = settings.jobs_root() / str(request.task_id)
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()) == ['attempt-2/report.docx']


@pytest.mark.parametrize('mode', ['timeout', 'bad_metadata', 'failed', 'success'])
def test_word_refresh_uses_disposable_copy_and_preserves_report_on_failure(tmp_path, monkeypatch, mode):
    import assess_agent.office as office
    path = tmp_path / 'report.tmp.docx'
    path.write_bytes(b'original document')
    monkeypatch.setattr(office.sys, 'platform', 'win32')
    monkeypatch.setattr(office.shutil, 'which', lambda _: 'powershell.exe')
    cleaned = []
    monkeypatch.setattr(office, 'stop_owned_word', lambda owner: cleaned.append(owner))
    def run(args, **kwargs):
        source = Path(args[args.index('-Source') + 1])
        output = Path(args[args.index('-Output') + 1])
        assert source != path and source.read_bytes() == path.read_bytes()
        assert kwargs['timeout'] == 90
        assert '-OwnerRecord' in args
        if mode == 'timeout':
            raise subprocess.TimeoutExpired(args, 90)
        output.write_bytes(b'updated document')
        return subprocess.CompletedProcess(args, 1 if mode == 'failed' else 0,
            stdout='{}' if mode == 'bad_metadata' else '{"pages":42}')
    monkeypatch.setattr(office.subprocess, 'run', run)
    result = office.refresh_word_fields(path)
    assert len(cleaned) == 1
    if mode == 'success':
        assert result == {'status': 'updated', 'pages': 42}
        assert path.read_bytes() == b'updated document'
    else:
        assert result['status'] == 'manualUpdateRequired'
        assert path.read_bytes() == b'original document'
    assert not list(tmp_path.glob('word-fields-*'))


@pytest.mark.parametrize('owned', [True, False])
def test_word_cleanup_targets_recorded_new_process_and_checks_identity(tmp_path, monkeypatch, owned):
    import assess_agent.office as office
    owner = tmp_path / 'owner.json'
    owner.write_text(json.dumps({'owned': owned, 'pid': 12345, 'startedTicks': 638950000000000000}), encoding='utf-8')
    monkeypatch.setattr(office.shutil, 'which', lambda _: 'powershell.exe')
    calls = []
    monkeypatch.setattr(office.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    office.stop_owned_word(owner)
    assert len(calls) == int(owned)
    if owned:
        assert "-Id 12345" in calls[0][-1]
        assert "ProcessName -eq 'WINWORD'" in calls[0][-1]
        assert 'StartTime.ToUniversalTime().Ticks -eq 638950000000000000' in calls[0][-1]
        assert '-Name WINWORD' not in calls[0][-1]
