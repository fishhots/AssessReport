"""Local retry checkpoints, scoped to the exact inputs and writing configuration."""
from hashlib import sha256
import json

from pydantic import ValidationError

from .errors import AgentError
from .schemas import Evidence, SectionDraft
from .settings import SERVICE_ROOT
from .store import write_json
from .validation import validate_section


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def workflow_identity(request, corpus, settings, model_config):
    resources = [*SERVICE_ROOT.glob('assess_agent/*.py'), *SERVICE_ROOT.glob('prompts/*.md'),
                 *SERVICE_ROOT.glob('skills/*/SKILL.md'), *SERVICE_ROOT.glob('config/*')]
    return digest({'version': 1, 'request': request.model_dump(mode='json'),
        'corpus': corpus.model_dump(mode='json'),
        'model': {k: v for k, v in model_config.items() if k != 'key'},
        'outputLimits': [settings.max_output_tokens, settings.analysis_output_tokens],
        'resources': {str(p.relative_to(SERVICE_ROOT)): sha256(p.read_bytes()).hexdigest()
                      for p in sorted(resources) if p.is_file()}})


class RetryCache:
    def __init__(self, directory, identity):
        self.directory, self.identity = directory, identity
        current = int(directory.name.removeprefix('attempt-'))
        self.previous = sorted((p for p in directory.parent.glob('attempt-*')
            if p.name.removeprefix('attempt-').isdigit() and int(p.name.removeprefix('attempt-')) < current),
            key=lambda p: int(p.name.removeprefix('attempt-')), reverse=True)

    @staticmethod
    def key(value):
        return digest(value)

    def load(self, kind, key):
        for directory in [self.directory, *self.previous]:
            path = directory / 'checkpoints' / f'{kind}-{key}.json'
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                if saved['identity'] == self.identity:
                    # Carry reused checkpoints forward so another interruption
                    # does not lose this progress. No provider request is made.
                    if directory != self.directory:
                        self.save(kind, key, saved['value'])
                    return saved['value']
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def save(self, kind, key, value):
        write_json(self.directory / 'checkpoints' / f'{kind}-{key}.json',
                   {'identity': self.identity, 'value': value})


def section_key(cache, config, diagnosis=False, related_sections=None):
    return cache.key({'config': config, 'diagnosis': diagnosis, 'relatedSections': related_sections})


def section_payload(draft, evidence, gap=None, coverage=None):
    registry = evidence.visuals
    datasets = [registry.datasets[registry.assets[v.asset_id]['datasetId']] for v in draft.visuals] if registry else []
    required = {k for entry in [*draft.blocks, *draft.tables] for k in entry.source_ids}
    required.update(d['datasetId'] for d in datasets)
    pending = list(required)
    while pending:
        source = evidence.by_id.get(pending.pop())
        if source and source.computed:
            for parent in source.parent_ids:
                if parent not in required:
                    required.add(parent)
                    pending.append(parent)
    return {'draft': draft.model_dump(), 'computedEvidence': [s.model_dump(mode='json')
        for s in evidence.corpus.evidence if s.computed and s.id in required], 'datasets': datasets,
        'gap': gap, 'coverage': coverage, 'gapNote': evidence.content_gap_notes.get(draft.title)}


def restore_section(saved, evidence, title, year):
    """Recheck cached facts and recreate visuals in the current attempt directory."""
    return _restore_payload(saved, evidence, title, year, validated=True)


def candidate_payload(draft, evidence):
    """Save unfinished public prose and registered charts, never model reasoning.

    Charts are owned by the program. Unknown model-supplied chart IDs are not
    portable resources; registered charts are recreated and checked on resume.
    """
    registry = evidence.visuals
    visuals = [v for v in draft.visuals if registry and v.asset_id in registry.assets
               and registry.assets[v.asset_id]['templateId'] == v.template_id]
    return section_payload(draft.model_copy(update={'visuals': visuals}), evidence)


def restore_candidate(saved, evidence, title, year):
    """Restore a draft for further review, not as accepted report content."""
    return _restore_payload(saved, evidence, title, year, validated=False)


def _restore_payload(saved, evidence, title, year, *, validated):
    try:
        draft = SectionDraft.model_validate(saved['draft'])
        computed = [Evidence.model_validate(s) for s in saved['computedEvidence']]
        available = set(evidence.by_id) | {s.id for s in computed}
        material_ids = {s.material_id for s in evidence.corpus.evidence if not s.computed}
        if draft.title != title or any(not s.computed or s.material_id not in material_ids or
                not s.parent_ids or any(p not in available for p in s.parent_ids) for s in computed):
            return None
        for source in computed:
            if source.id not in evidence.by_id:
                evidence.by_id[source.id] = source
                evidence.corpus.evidence.append(source)
        # Check prose/tables before recreating any chart resources.
        if validated and validate_section(draft.model_copy(update={'visuals': []}), title, evidence, year):
            return None
        registry = evidence.visuals
        if registry:
            registry.active_title = title
            datasets = {d['templateId']: d for d in saved.get('datasets', [])}
            for visual in draft.visuals:
                data = datasets[visual.template_id]
                if data['datasetId'] not in evidence.by_id:
                    return None
                registry.datasets[data['datasetId']] = data
                registry.coverage[visual.template_id].update(status='prepared', datasetId=data['datasetId'], reason='')
                visual.asset_id = registry.render(data['datasetId'])['assetId']
            draft = registry.attach(draft)
        if validated and validate_section(draft, title, evidence, year):
            return None
        if saved.get('gapNote'):
            evidence.content_gap_notes[title] = saved['gapNote']
        if saved.get('coverage'):
            evidence.topic_coverage[title] = saved['coverage']
        return draft
    except (AgentError, ValidationError, KeyError, TypeError, ValueError):
        return None
