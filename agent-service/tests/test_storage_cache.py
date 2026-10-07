"""Storage lifecycle checks use no external models or real user data."""
from hashlib import sha256
import json
from uuid import uuid4

import pytest

from assess_agent.cache_io import cache_path, read_cache_json, write_cache_json
from assess_agent.cache_management import cleanup_successful_task, cleanup_unfinished_task
from assess_agent.checkpoints import RetryCache
from assess_agent.errors import AgentError
from assess_agent.store import JobStore, write_json
from test_agent import setup_task


def test_compressed_cache_is_small_atomic_and_reads_legacy(tmp_path):
    path = tmp_path / 'corpus.json'
    payload = {'text': '学校培养质量分析' * 100000}
    write_json(path, payload)
    original_size = path.stat().st_size
    assert read_cache_json(path) == payload
    write_cache_json(path, payload)
    assert not path.exists()
    assert cache_path(path).stat().st_size < original_size / 10
    assert read_cache_json(path) == payload
    assert not list(tmp_path.glob('*.tmp'))
    cache_path(path).write_bytes(b'broken gzip')
    with pytest.raises((OSError, ValueError)):
        read_cache_json(path)


def succeeded_job(tmp_path):
    settings, request = setup_task(tmp_path)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    task = settings.jobs_root() / str(request.task_id)
    current = task / 'attempt-1'
    current.mkdir(parents=True)
    content = b'final downloadable report'
    (current / 'report.docx').write_bytes(content)
    (current / 'corpus.json').write_bytes(b'redundant raw data')
    (current / 'visuals').mkdir()
    (current / 'visuals' / 'chart.png').write_bytes(b'embedded image')
    previous = task / 'attempt-0'
    previous.mkdir()
    (previous / 'corpus.json').write_bytes(b'obsolete data')
    jobs.finish(str(request.task_id), 1, {'artifacts': {'report': {'sha256': sha256(content).hexdigest()}}})
    return settings, request, jobs, task, content


def test_success_keeps_only_final_download_and_does_not_touch_other_users(tmp_path):
    settings, request, jobs, task, content = succeeded_job(tmp_path)
    sibling = task.parent / str(uuid4()) / 'attempt-1'
    sibling.mkdir(parents=True)
    (sibling / 'report.docx').write_bytes(b'other task')
    assert cleanup_successful_task(settings.jobs_root(), jobs, str(request.task_id), 1)
    assert [p.relative_to(task).as_posix() for p in task.rglob('*') if p.is_file()] == ['attempt-1/report.docx']
    assert (task / 'attempt-1' / 'report.docx').read_bytes() == content
    assert (sibling / 'report.docx').read_bytes() == b'other task'
    assert jobs.get(str(request.task_id))['status'] == 'SUCCEEDED'
    assert cleanup_successful_task(settings.jobs_root(), jobs, str(request.task_id), 1)


def test_missing_or_corrupted_download_prevents_all_cleanup(tmp_path):
    settings, request, jobs, task, _ = succeeded_job(tmp_path)
    (task / 'attempt-1' / 'report.docx').write_bytes(b'corrupt')
    with pytest.raises(ValueError):
        cleanup_successful_task(settings.jobs_root(), jobs, str(request.task_id), 1)
    assert (task / 'attempt-0' / 'corpus.json').is_file()
    assert (task / 'attempt-1' / 'corpus.json').is_file()


def test_failed_retry_consolidates_all_completed_keys_before_removing_old_attempts(tmp_path):
    settings, request = setup_task(tmp_path)
    jobs = JobStore(settings.data_root)
    task_id = str(request.task_id)
    jobs.create(request)
    task = settings.jobs_root() / task_id
    old = RetryCache(task / 'attempt-1', 'same-inputs')
    # Include an old uncompressed response and a checkpoint for a later chapter.
    write_json(old.directory / 'checkpoints' / 'response-old.json',
               {'identity': old.identity, 'value': {'answer': 'paid response'}})
    old.save('section', 'later-chapter', {'answer': 'completed later chapter'})
    jobs.fail(task_id, 1, AgentError('MODEL_TIMEOUT', 'offline timeout'))
    jobs.retry(task_id)
    new = RetryCache(task / 'attempt-2', 'same-inputs')
    (new.directory / 'visuals').mkdir()
    (new.directory / 'visuals' / 'temporary.png').write_bytes(b'can be regenerated')
    jobs.fail(task_id, 2, AgentError('MODEL_TIMEOUT', 'offline timeout again'))
    assert cleanup_unfinished_task(settings.jobs_root(), jobs, task_id, 2)
    assert not old.directory.exists()
    assert not (new.directory / 'visuals').exists()
    resumed = RetryCache(task / 'attempt-3', 'same-inputs')
    assert resumed.load('response', 'old') == {'answer': 'paid response'}
    assert resumed.load('section', 'later-chapter') == {'answer': 'completed later chapter'}
    assert not cleanup_unfinished_task(settings.jobs_root(), jobs, task_id, 1)


def test_running_task_caches_are_preserved(tmp_path):
    settings, request = setup_task(tmp_path)
    jobs = JobStore(settings.data_root)
    task_id = str(request.task_id)
    jobs.create(request)
    cache = RetryCache(settings.jobs_root() / task_id / 'attempt-1', 'inputs')
    cache.save('section', 'one', {'answer': 'progress'})
    assert not cleanup_successful_task(settings.jobs_root(), jobs, task_id, 1)
    assert not cleanup_unfinished_task(settings.jobs_root(), jobs, task_id, 1)
    assert cache.load('section', 'one') == {'answer': 'progress'}
