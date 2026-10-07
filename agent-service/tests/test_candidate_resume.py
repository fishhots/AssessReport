"""Retain accepted public writing through repair and coverage interruptions."""
import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage

from assess_agent.checkpoints import (RetryCache, candidate_payload, restore_candidate,
    section_key, workflow_identity)
from assess_agent.coverage_recovery import recover_coverage
from assess_agent.errors import AgentError
from assess_agent.cache_management import cleanup_terminal_task
from assess_agent.evidence import EvidenceStore
from assess_agent.models import SectionWriter
from assess_agent.parsers import parse_job
from assess_agent.schemas import SectionDraft
from assess_agent.settings import CHAPTERS, REPORT_CONFIG
from assess_agent.store import JobStore
from assess_agent.validation import validate_section
from assess_agent.workflow import run_workflow
from fake_model import ToolCallingTestModel
from test_agent import setup_task


def finding(title, key, number=80, subsection=''):
    return SectionDraft(title=title, blocks=[{'kind': 'finding', 'text': f'就业人数{number}人。',
        'source_ids': [key], 'subsection': subsection}])


def test_resume_keeps_main_composition_and_first_repair(tmp_path, monkeypatch):
    settings, request = setup_task(tmp_path)
    primary = request.chapters[0]
    calls = []

    def write(self, title, query, diagnosis=False, question='', issues=None, draft=None, **kwargs):
        key = self.evidence.search('就业')[0]['id']
        if title != primary:
            return finding(title, key)
        calls.append(('repair' if issues else 'compose', deepcopy(draft)))
        if len(calls) == 1:
            return finding(title, key, 999)
        if len(calls) == 2:
            return finding(title, key, 998)
        if len(calls) == 3:
            raise AgentError('MODEL_UNAVAILABLE', '离线模拟中转503')
        assert draft['blocks'][0]['text'] == '就业人数998人。'
        return finding(title, key, subsection=draft['blocks'][0]['subsection'])

    monkeypatch.setattr(SectionWriter, 'write', write)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    with pytest.raises(AgentError) as failure:
        run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel())
    jobs.fail(str(request.task_id), 1, failure.value)
    cleanup_terminal_task(settings, jobs, str(request.task_id), 1)
    jobs.retry(str(request.task_id))
    result = run_workflow(request, settings, jobs, 2, model=ToolCallingTestModel())
    assert [c[0] for c in calls] == ['compose', 'repair', 'repair', 'repair']
    assert result['resume']['drafts'] == [{'title': primary, 'stage': 'review', 'repairsCompleted': 1}]
    saved = json.loads((settings.jobs_root() / str(request.task_id) / 'attempt-2/report-sections.json').read_text(encoding='utf-8'))
    assert '998' not in json.dumps(saved) and '999' not in json.dumps(saved)


def test_coverage_resume_keeps_completed_batches_and_pending_composition(tmp_path, monkeypatch):
    import assess_agent.coverage_recovery as module
    settings, request = setup_task(tmp_path)
    store = EvidenceStore(parse_job(request, settings))
    key = store.search('就业')[0]['id']
    paths = [f'主题{i}' for i in range(10)]
    title = '补写测试'

    def coverage(draft):
        occupied = {b.subsection for b in draft.blocks if b.kind != 'gap'}
        remaining = [p for p in paths if p not in occupied]
        return {'requiredTopics': 10, 'writtenTopics': 10 - len(remaining), 'unwrittenTopics': remaining}

    monkeypatch.setattr(module, 'template_coverage', coverage)
    monkeypatch.setattr(module, 'topic_packets', lambda *args: [{'subsection': p, 'source_ids': [key]} for p in paths])
    monkeypatch.setattr(module, 'fallback_findings', lambda packet: [])
    calls, snapshot = [], {}

    class Writer:
        evidence = store
        def write(self, title, query, **kwargs):
            if kwargs.get('issues'):
                calls.append('repair')
                if calls.count('repair') == 1:
                    raise AgentError('MODEL_UNAVAILABLE', '离线模拟中转503')
                revised = SectionDraft.model_validate(kwargs['draft'])
                for b in revised.blocks:
                    b.text = '就业人数80人。'
                return revised
            batch = kwargs['focus_paths']
            calls.append(batch)
            return SectionDraft(title=title, blocks=[{'kind': 'finding',
                'text': f'就业人数{80 if len(batch) == 8 else 999}人。',
                'source_ids': [key], 'subsection': p} for p in batch])

    def checkpoint(draft, progress):
        snapshot.update(draft=draft.model_copy(deep=True), progress=deepcopy(progress))

    draft = SectionDraft(title=title, blocks=[{'kind': 'gap', 'text': '等待补写。'}])
    with pytest.raises(AgentError):
        recover_coverage(Writer(), draft, {'query': '就业'}, 2025, checkpoint=checkpoint)
    assert len(snapshot['progress']['audit']) == 8
    assert snapshot['progress']['batch']['paths'] == paths[8:]
    draft, audit = recover_coverage(Writer(), snapshot['draft'], {'query': '就业'}, 2025,
        progress=snapshot['progress'], checkpoint=checkpoint)
    assert calls == [paths[:8], paths[8:], 'repair', 'repair']
    assert audit['writtenTopics'] == 10 and not audit['unwrittenTopics']
    assert len([b for b in draft.blocks if b.subsection in paths]) == 10
    assert not validate_section(draft, title, store, 2025)


def test_pending_coverage_draft_cannot_bypass_numeric_review(tmp_path, monkeypatch):
    settings, request = setup_task(tmp_path)
    store = EvidenceStore(parse_job(request, settings))
    config = next(c for c in REPORT_CONFIG['chapters'] if c['title'] == request.chapters[0])
    identity = workflow_identity(request, store.corpus, settings, {'provider': 'offline-test'})
    cache = RetryCache(settings.jobs_root() / str(request.task_id) / 'attempt-1', identity)
    key = store.search('就业')[0]['id']
    cache.save('candidate', section_key(cache, config), {'version': 1, 'stage': 'coverage',
        'repairsCompleted': 2, 'section': candidate_payload(finding(config['title'], key, 999), store)})
    main_calls = []

    def write(self, title, query, diagnosis=False, question='', issues=None, draft=None, **kwargs):
        if title == config['title']:
            assert issues and draft, 'A resumed candidate must be reviewed, not exported or regenerated.'
            main_calls.append(title)
        return finding(title, key, subsection=draft['blocks'][0]['subsection'] if draft else '')

    monkeypatch.setattr(SectionWriter, 'write', write)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel())
    assert main_calls == [config['title']]


def test_pending_draft_preserves_unvalidated_text_but_rejects_foreign_provenance(tmp_path):
    settings, request = setup_task(tmp_path)
    store = EvidenceStore(parse_job(request, settings))
    table = store.corpus.tables[0]
    data = store.summarize(table.material_id, table.sheet, '人数', 'numeric')
    draft = finding(request.chapters[0], data['evidenceId'], 999)
    saved = candidate_payload(draft, store)
    current = EvidenceStore(parse_job(request, settings))
    resumed = restore_candidate(saved, current, draft.title, 2025)
    assert resumed and validate_section(resumed, draft.title, current, 2025)
    saved['computedEvidence'][0]['parent_ids'] = ['foreign-task']
    assert restore_candidate(saved, EvidenceStore(parse_job(request, settings)), draft.title, 2025) is None


@pytest.mark.parametrize('code,fallback', [('MODEL_UNAVAILABLE', True), ('MODEL_AUTH', False),
                                        ('MODEL_TIMEOUT', False)])
def test_analysis_503_uses_verified_evidence_without_repeating_analysis(tmp_path, code, fallback):
    settings, request = setup_task(tmp_path)
    store = EvidenceStore(parse_job(request, settings))
    key = store.search('就业')[0]['id']

    class Compose:
        calls = 0
        def invoke(self, messages):
            self.calls += 1
            return AIMessage(content=finding(request.chapters[0], key).model_dump_json())

    class Analysis:
        calls = 0
        def invoke(self, messages):
            self.calls += 1
            raise AgentError(code, '离线模拟')

    compose, analysis = Compose(), Analysis()
    writer = SectionWriter(settings, request, store, model=compose)
    writer.analysis_model = analysis
    if fallback:
        draft = writer.write(request.chapters[0], '就业')
        assert not validate_section(draft, draft.title, store, 2025)
        assert writer.call_trace[0]['analysisFallback'] == 'verified_evidence'
    else:
        with pytest.raises(AgentError) as error:
            writer.write(request.chapters[0], '就业')
        assert error.value.code == code
    assert analysis.calls == 1 and compose.calls == int(fallback)
