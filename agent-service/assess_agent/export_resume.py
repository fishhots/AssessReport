"""Resume a validated report at export without constructing a model client."""
from hashlib import sha256
import json
from pathlib import Path
import shutil

from pydantic import ValidationError

from .checkpoints import digest
from .errors import AgentError
from .evidence import EvidenceStore
from .schemas import Corpus, SectionDraft
from .store import write_json
from .validation import validate_section
from .visuals import VisualRegistry

SNAPSHOT_FILES = ('corpus.json', 'visual-assets.json', 'visual-coverage.json')
OPTIONAL_SNAPSHOT_FILES = ('data-utilization.json',)


def export_snapshot_directory(request, jobs_root, before_attempt):
    root = jobs_root / str(request.task_id)
    previous = sorted((p for p in root.glob('attempt-*') if p.is_dir() and
        p.name.removeprefix('attempt-').isdigit() and
        int(p.name.removeprefix('attempt-')) < before_attempt),
        key=lambda p: int(p.name.removeprefix('attempt-')), reverse=True)
    for directory in previous:
        if (directory / 'export-checkpoint.json').is_file():
            return directory
        # Compatibility with reports validated before export checkpoints existed.
        if (directory / 'stage-5.json').is_file() and (directory / 'report.audit.json').is_file():
            return directory
    return None


def save_export_checkpoint(request, directory, sections, evidence, metadata):
    files=(*SNAPSHOT_FILES,*(name for name in OPTIONAL_SNAPSHOT_FILES if (directory/name).is_file()))
    write_json(directory / 'export-checkpoint.json', {
        'version': 1, 'requestHash': digest(request.model_dump(mode='json')),
        'sections': sections, 'metadata': metadata, 'gapNotes': evidence.content_gap_notes,
        'files': {name: sha256((directory / name).read_bytes()).hexdigest() for name in files}})


def load_export_snapshot(request, directory):
    try:
        checkpoint = directory / 'export-checkpoint.json'
        if checkpoint.is_file():
            saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            if saved['version'] != 1 or saved['requestHash'] != digest(request.model_dump(mode='json')):
                raise ValueError('snapshot scope')
            if any(name not in (*SNAPSHOT_FILES,*OPTIONAL_SNAPSHOT_FILES) for name in saved['files']):raise ValueError('snapshot files')
            for name in (*SNAPSHOT_FILES,*(n for n in OPTIONAL_SNAPSHOT_FILES if n in saved['files'])):
                if sha256((directory / name).read_bytes()).hexdigest() != saved['files'][name]:
                    raise ValueError('snapshot changed')
        else:
            stage = json.loads((directory / 'stage-5.json').read_text(encoding='utf-8'))
            audit = json.loads((directory / 'report.audit.json').read_text(encoding='utf-8'))
            sections = stage['report'] + stage['extensions']
            if sections != audit['sections']:
                raise ValueError('legacy snapshot scope')
            def optional(name, fallback):
                file = directory / name
                return json.loads(file.read_text(encoding='utf-8')) if file.is_file() else fallback
            traces = optional('model-calls.json', [])
            gap_notes = {}
            for file in (directory / 'checkpoints').glob('section-*.json'):
                value = json.loads(file.read_text(encoding='utf-8'))['value']
                if value.get('gapNote'):
                    gap_notes[value['draft']['title']] = value['gapNote']
            saved = {'sections': sections, 'gapNotes': gap_notes, 'metadata': {
                'model': {'provider': request.model_id, 'restoredFromSnapshot': True},
                'contentGaps': optional('content-gaps.json', []),
                'templateCoverage': optional('template-coverage.json', audit.get('coverage', {})),
                'skillVersions': {},
                'usage': {'calls': sum(not t.get('cacheHit') for t in traces),
                    'inputTokens': sum(t.get('inputTokens', 0) for t in traces),
                    'outputTokens': sum(t.get('outputTokens', 0) for t in traces)}}}
        sections = [SectionDraft.model_validate(s) for s in saved['sections']]
        if [s.title for s in sections] != request.chapters + request.extension_titles():
            raise ValueError('chapter scope')
        evidence = EvidenceStore(Corpus.model_validate_json((directory / 'corpus.json').read_text(encoding='utf-8')))
        evidence.topic_coverage = saved['metadata'].get('templateCoverage', {})
        evidence.content_gap_notes = saved.get('gapNotes', {})
        registry = VisualRegistry(evidence, directory / 'visuals', request.year, request.chapters)
        evidence.visuals = registry
        visuals = json.loads((directory / 'visual-assets.json').read_text(encoding='utf-8'))
        registry.assets, registry.datasets = visuals['assets'], visuals['datasets']
        registry.coverage = {r['templateId']: r for r in
            json.loads((directory / 'visual-coverage.json').read_text(encoding='utf-8'))}
        for section in sections:
            # A hashed, previously reviewed export snapshot is frozen content.
            # New editorial checks apply to new writing, not export-only retry.
            if validate_section(section, section.title, evidence, request.year, review_semantics=False):
                raise ValueError('saved content validation')
        return sections, evidence, saved['metadata']
    except (OSError, ValueError, KeyError, TypeError, ValidationError, AgentError) as error:
        raise AgentError('EXPORT_CHECKPOINT',
            '已生成正文的恢复文件不完整或未通过校验，本次未重新调用模型；请检查任务缓存') from error


def copy_export_snapshot(source, target, sections, evidence):
    """Carry the verified snapshot and embedded assets into the new attempt."""
    for name in SNAPSHOT_FILES:
        shutil.copyfile(source / name, target / name)
    for name in OPTIONAL_SNAPSHOT_FILES:
        if (source/name).is_file():shutil.copyfile(source/name,target/name)
    copied = set()
    for section in sections:
        for visual in section.visuals:
            for file in evidence.visuals.assets[visual.asset_id]['files']:
                filename = file['filename']
                if filename not in copied:
                    destination = (target / 'visuals' / filename).resolve()
                    if not destination.is_relative_to((target / 'visuals').resolve()):
                        raise AgentError('EXPORT_CHECKPOINT', '已保存图表的路径无效，本次未重新调用模型')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / 'visuals' / filename, destination)
                    copied.add(filename)
    evidence.visuals.directory = target / 'visuals'
    # Export does not need a survey analyzer, parsing, tools, or writing skills.
