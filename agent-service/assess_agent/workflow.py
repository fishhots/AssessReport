from pathlib import Path
from typing import TypedDict
import time

from langgraph.graph import StateGraph, START, END

from .errors import AgentError
from .evidence import EvidenceStore
from .exporter import export_report
from .export_resume import (export_snapshot_directory, load_export_snapshot,
                            copy_export_snapshot, save_export_checkpoint)
from .models import SectionWriter, validate_provider
from .checkpoints import (RetryCache, workflow_identity, section_key, section_payload, restore_section,
                          candidate_payload, restore_candidate)
from .parsers import parse_job
from .partial_content import salvage_section, skipped_section
from .repair import prepare_repair
from .schemas import ContentBlock, Corpus, JobRequest, SectionDraft
from .settings import REPORT_CONFIG, Settings
from .store import JobStore, write_json
from .validation import validate_section, template_coverage, extension_path
from .visuals import VisualRegistry
from .survey import SurveyAnalyzer
from .coverage_recovery import recover_coverage
from .topic_evidence import normalize_subsections
from .extension_style import normalize_extension_structure
from .report_style import formal_text


def verified_summaries(sections, limit=16):
    """Distribute summary evidence across chapter themes, including later topics."""
    output=[]
    for section in sections:
        groups={}
        for block in section['blocks']:
            if block['kind'] in ('finding','inference'):
                theme=(block.get('subsection') or '').split(' / ')[0]
                groups.setdefault(theme,[]).append({**block,'text':formal_text(block['text'])[:600]})
        selected=[]
        while len(selected)<limit and any(groups.values()):
            for blocks in groups.values():
                if blocks and len(selected)<limit:selected.append(blocks.pop(0))
        output.append({'title':section['title'],'blocks':selected})
    return output


class WorkflowState(TypedDict, total=False):
    corpus: dict
    report: list[dict]
    diagnosis: list[dict]
    extensions: list[dict]
    result: dict


def run_workflow(request: JobRequest, settings: Settings, jobs: JobStore, attempt: int, model=None) -> dict:
    task_id = str(request.task_id)
    directory = settings.jobs_root() / task_id / f"attempt-{attempt}"
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    runtime = {}
    content_gaps = []
    coverage = {}

    def check_time():
        jobs.ensure_active(task_id, attempt)
        if time.monotonic() - started >= settings.job_timeout:
            raise AgentError("JOB_TIMEOUT", "任务执行超过时间上限")

    previous_export = export_snapshot_directory(request, settings.jobs_root(), attempt)
    if previous_export is not None:
        check_time()
        for index in range(1, 6):
            jobs.phase(task_id, attempt, index, 'COMPLETED')
        jobs.phase(task_id, attempt, 6, 'RUNNING')
        sections, evidence, metadata = load_export_snapshot(request, previous_export)
        check_time()
        evidence.cancel_check = lambda: jobs.ensure_active(task_id, attempt)
        copy_export_snapshot(previous_export, directory, sections, evidence)
        write_json(directory / 'report-sections.json', [s.model_dump() for s in sections[:len(request.chapters)]])
        write_json(directory / 'extension-sections.json', [s.model_dump() for s in sections[len(request.chapters):]])
        save_export_checkpoint(request, directory, [s.model_dump() for s in sections], evidence, metadata)
        check_time()
        artifacts = export_report(request, sections, evidence, directory, refresh_fields=settings.update_word_fields)
        check_time()
        result = {**metadata, 'artifacts': artifacts, 'templateVersion': REPORT_CONFIG['version'],
            'warnings': evidence.corpus.warnings, 'visuals': evidence.visuals.summary(),
            'previousUsage': metadata.get('previousUsage', metadata.get('usage', {})),
            'usage': {'calls': 0, 'inputTokens': 0, 'outputTokens': 0},
            'resume': {'mode': 'export', 'sourceAttempt': int(previous_export.name.removeprefix('attempt-')),
                       'sections': [s.title for s in sections], 'responses': 0},
            'elapsedSeconds': round(time.monotonic() - started, 2)}
        write_json(directory / 'manifest.json', result)
        jobs.phase(task_id, attempt, 6, 'COMPLETED')
        return result

    def parse(state):
        corpus = parse_job(request, settings)
        check_time()
        model_config = validate_provider(settings, request.model_id) if model is None else {'provider': 'offline-test'}
        runtime['model_config'] = model_config
        runtime['cache'] = RetryCache(directory, workflow_identity(request, corpus, settings, model_config))
        runtime['resumed_titles'] = []
        runtime['resumed_drafts'] = []
        runtime["evidence"] = EvidenceStore(corpus)
        runtime["evidence"].cancel_check = lambda: jobs.ensure_active(task_id, attempt)
        runtime["evidence"].survey = SurveyAnalyzer(runtime["evidence"], request.year, request.school).build()
        runtime["evidence"].visuals = VisualRegistry(runtime["evidence"], directory / "visuals", request.year, request.chapters)
        runtime["evidence"].visuals.plan()
        from .data_inventory import DataInventory
        runtime['evidence'].inventory=DataInventory(runtime['evidence'],request.year).build()
        write_json(directory/'data-utilization.json',runtime['evidence'].inventory.audit())
        write_json(directory / "corpus.json", corpus.model_dump(by_alias=True))
        return {"corpus": corpus.model_dump(by_alias=True)}

    def load_skills(state):
        writer = SectionWriter(settings, request, runtime["evidence"], model=model, provider_config=runtime['model_config'])
        writer.budget.deadline = started + settings.job_timeout
        writer.budget.cancel_check = lambda: jobs.ensure_active(task_id, attempt)
        writer.cache = runtime['cache']
        for name in ("source-evidence", "table-analysis", "quality-report", "deep-diagnosis", "charts-tables", "review-export"):
            writer.skill(name)
        runtime["writer"] = writer
        return {}

    def validated_write(config, diagnosis=False, related_sections=None):
        writer = runtime["writer"]
        cache = runtime['cache']
        cache_key = section_key(cache, config, diagnosis, related_sections)
        saved = cache.load('section', cache_key)
        cached_draft = restore_section(saved, runtime['evidence'], config['title'], request.year) if saved else None
        if cached_draft:
            if diagnosis:
                normalize_extension_structure(cached_draft,request.instructions+' '+config.get('question',''))
            if saved.get('gap'):
                content_gaps.append(saved['gap'])
                write_json(directory / 'content-gaps.json', content_gaps)
            if saved.get('coverage'):
                coverage[config['title']] = saved['coverage']
                write_json(directory / 'template-coverage.json', coverage)
            runtime['resumed_titles'].append(config['title'])
            return cached_draft.model_dump()
        hint = config.get("missingDataHint", "本章相关指标的校本数据、有效样本和统计口径。")
        generation_error = None
        progress = cache.load('candidate', cache_key)
        draft = None
        if (isinstance(progress, dict) and progress.get('version') == 1 and
                progress.get('stage') in ('review', 'coverage') and
                type(progress.get('repairsCompleted')) is int and 0 <= progress['repairsCompleted'] <= 2 and
                not progress.get('generationError') and isinstance(progress.get('section'), dict)):
            draft = restore_candidate(progress['section'], runtime['evidence'], config['title'], request.year)
        if draft is not None:
            runtime['resumed_drafts'].append({'title': config['title'], 'stage': progress['stage'],
                                            'repairsCompleted': progress['repairsCompleted']})
        else:
            progress = {'stage': 'review', 'repairsCompleted': 0}
            try:
                draft = writer.write(config["title"], config["query"], diagnosis, config.get("question", ""),
                                     guidance=config.get("guidance", ""), related_sections=related_sections)
            except AgentError as error:
                if error.code != "MODEL_STRUCTURE":
                    raise
                generation_error = error.code
                draft = skipped_section(config["title"], hint, generation_error=True)
            finally:
                write_json(directory / "model-calls.json", writer.call_trace)
        if not diagnosis:
            normalize_subsections(draft,runtime['evidence'])
            draft = runtime["evidence"].visuals.attach(draft)
        if diagnosis:
            normalize_extension_structure(draft,request.instructions+' '+config.get('question',''))
        write_json(directory / "review-candidate.json", draft.model_dump())
        issues = validate_section(draft, config["title"], runtime["evidence"], request.year)
        repairs_completed = progress['repairsCompleted']
        # An unfinished candidate is always reviewed again. A stale/tampered
        # coverage checkpoint cannot bypass content validation or reach export.
        if progress['stage'] == 'coverage' and issues:
            progress = {'stage': 'review', 'repairsCompleted': 0}
            repairs_completed = 0

        def save_candidate(stage, skipped=None, recovery=None):
            write_json(directory / 'review-candidate.json', draft.model_dump())
            cache.save('candidate', cache_key, {'version': 1, 'stage': stage,
                'repairsCompleted': repairs_completed, 'generationError': generation_error,
                'validationIssues': issues, 'skipped': skipped,
                'coverageProgress': recovery, 'section': candidate_payload(draft, runtime['evidence'])})

        if progress['stage'] == 'review':
            save_candidate('review')
        for _ in range(repairs_completed, 2) if progress['stage'] == 'review' else ():
            if not issues:
                break
            scope = prepare_repair(draft, issues)
            try:
                revised = writer.write(config["title"], config["query"], diagnosis, config.get("question", ""),
                                       scope.issues, scope.input().model_dump(), guidance=config.get("guidance", ""),
                                       related_sections=related_sections, repair_source_ids=scope.evidence_ids)
                write_json(directory / "repair-candidate.json", revised.model_dump())
                draft = scope.merge(revised)
                repairs_completed += 1
            except AgentError as error:
                if error.code != "MODEL_STRUCTURE":
                    raise
                generation_error = error.code
                break
            finally:
                write_json(directory / "model-calls.json", writer.call_trace)
            if not diagnosis:
                draft = runtime["evidence"].visuals.attach(draft)
            write_json(directory / "review-candidate.json", draft.model_dump())
            issues = validate_section(draft, config["title"], runtime["evidence"], request.year)
            save_candidate('review')
        if progress['stage'] == 'coverage':
            issues = progress.get('validationIssues', [])
            skipped = progress.get('skipped') or {"blocks": 0, "tables": 0, "visuals": 0}
        elif issues:
            write_json(directory / "validation-errors.json", {"title": config["title"], "issues": issues})
            draft, skipped = salvage_section(draft, config["title"], runtime["evidence"], request.year, hint)
        else:
            skipped = {"blocks": 0, "tables": 0, "visuals": 0}
        if not diagnosis:
            recovery = progress.get('coverageProgress') if progress['stage'] == 'coverage' else None
            save_candidate('coverage', skipped, recovery)

            def checkpoint_coverage(updated, recovery_state):
                nonlocal draft
                draft = updated
                save_candidate('coverage', skipped, recovery_state)

            try:
                draft,coverage[draft.title] = recover_coverage(writer,draft,config,request.year,check_time,
                    progress=recovery, checkpoint=checkpoint_coverage)
            finally:
                write_json(directory / "model-calls.json", writer.call_trace)
            unwritten = coverage[draft.title]['unwrittenTopics']
            write_json(directory / 'template-coverage.json', coverage)
        gap = None
        if issues or generation_error or any(block.kind == "gap" for block in draft.blocks) or runtime['evidence'].content_gap_notes.get(draft.title) or (not diagnosis and unwritten):
            gap = {"title": config["title"], "kind": "diagnosis" if diagnosis else "report",
                   "skipped": skipped, "suggestedMaterials": hint, "generationError": generation_error}
            content_gaps.append(gap)
            write_json(directory / "content-gaps.json", content_gaps)
        write_json(directory / "review-candidate.json", draft.model_dump())
        incomplete = any(row.get('status') == 'generation_incomplete'
                         for row in coverage.get(draft.title, {}).get('recovery', []))
        if diagnosis and config['title'].startswith('第十章 ') and not any(b.kind != 'gap' for b in draft.blocks):
            raise AgentError('TOPIC_INCOMPLETE', '自定义专题未形成正文；已完成章节已保存，可继续生成。')
        if not generation_error and not incomplete:
            cache.save('section', cache_key, section_payload(draft, runtime['evidence'], gap, coverage.get(draft.title)))
        return draft.model_dump()

    def report(state):
        sections = {}
        for config in REPORT_CONFIG["chapters"]:
            if config["title"] in request.chapters and config["title"] != "核心发现":
                check_time()
                sections[config["title"]] = validated_write(config)
                write_json(directory / "report-sections.json", list(sections.values()))
        if "核心发现" in request.chapters:
            summaries = verified_summaries(sections.values())
            config = next(c for c in REPORT_CONFIG["chapters"] if c["title"] == "核心发现")
            sections["核心发现"] = validated_write(config, related_sections=summaries)
        ordered = [sections[title] for title in request.chapters]
        write_json(directory / "report-sections.json", ordered)
        return {"report": ordered}

    def extensions(state):
        sections = []
        summaries=verified_summaries(state['report'])
        for config in REPORT_CONFIG["diagnosis"]:
            check_time()
            sections.append(validated_write(config, True,related_sections=summaries))
            write_json(directory / "diagnosis-sections.json", sections)
        personalized = SectionDraft(title=request.extension_titles()[0], blocks=[
            {**block, 'subsection': extension_path(section['title'],block.get('subsection',''))}
            for section in sections for block in section['blocks']],
            tables=[{**table, 'subsection': extension_path(section['title'],table.get('subsection',''))}
            for section in sections for table in section.get('tables', [])])
        for section in sections:
            note = runtime['evidence'].content_gap_notes.get(section['title'])
            if note:
                personalized.blocks.append(ContentBlock(kind='gap', text=note, subsection=section['title']))
        appended = [personalized.model_dump()]
        if request.custom_topic:
            check_time()
            config = {'title': request.extension_titles()[1], 'query': request.custom_topic.title,
                'question': request.custom_topic.requirements,
                'guidance': '这是用户自定义的专题章节。围绕题目和要求综合校本证据，明确现状、情景假设、路径建议、实施条件和评价方式。'
                            '默认组织为约四个相互衔接的主要小节，每节通常2–3段，不把每个内容块单独变成标题；用户明确提纲优先。'
                            '自然叙述建议，只有分析后确需突出独立重点行动段时才标注【建议】，同一主要主题最多一次，连续规划段不重复标记。缺少未来规划资料不构成整章跳过的理由，可依据已验证章节提出情景、路径与评价建议。'
                            '未来规划属于建议或情景，不能写成已发生的事实；不得编造政策、产业预测、课程和学分。',
                'missingDataHint': '与本主题有关的学校定位、培养与课程现状，以及产业或技术发展依据。'}
            appended.append(validated_write(config, True, related_sections=summaries))
        write_json(directory / 'extension-sections.json', appended)
        return {"diagnosis": sections, "extensions": appended}

    def validate(state):
        for entry in state["report"] + state["extensions"]:
            draft = SectionDraft.model_validate(entry)
            if validate_section(draft, draft.title, runtime["evidence"], request.year):
                raise AgentError("CONTENT_VALIDATION", "最终内容校验失败")
        runtime["evidence"].visuals.finalize([SectionDraft.model_validate(s) for s in state["report"]])
        if runtime['evidence'].inventory:
            write_json(directory/'data-utilization.json',runtime['evidence'].inventory.audit(
                [SectionDraft.model_validate(s) for s in state['report']+state['extensions']]))
        write_json(directory / "corpus.json", runtime["evidence"].corpus.model_dump(by_alias=True))
        writer = runtime['writer']
        save_export_checkpoint(request, directory, state['report'] + state['extensions'], runtime['evidence'], {
            'model': writer.metadata, 'templateVersion': REPORT_CONFIG['version'],
            'contentGaps': content_gaps, 'templateCoverage': coverage, 'skillVersions': writer.versions,
            'usage': {'calls': writer.budget.calls, 'inputTokens': writer.budget.input_tokens,
                      'outputTokens': writer.budget.output_tokens}})
        return {}

    def export(state):
        writer = runtime["writer"]
        artifacts = export_report(request, [SectionDraft.model_validate(s) for s in state["report"] + state["extensions"]],
                                  runtime["evidence"], directory, refresh_fields=settings.update_word_fields)
        result = {"artifacts": artifacts, "model": writer.metadata, "templateVersion": REPORT_CONFIG["version"],
                  "contentGaps": content_gaps,
                  "templateCoverage": coverage,
                  "skillVersions": writer.versions, "warnings": runtime["evidence"].corpus.warnings,
                  "visuals": runtime["evidence"].visuals.summary(),
                  "usage": {"calls": writer.budget.calls, "inputTokens": writer.budget.input_tokens,
                            "outputTokens": writer.budget.output_tokens},
                  "resume": {'sections': runtime['resumed_titles'],
                             'drafts': runtime['resumed_drafts'],
                             'responses': sum(bool(t.get('cacheHit')) for t in writer.call_trace)},
                  "elapsedSeconds": round(time.monotonic() - started, 2)}
        write_json(directory / "manifest.json", result)
        return {"result": result}

    graph = StateGraph(WorkflowState)
    previous = START
    for index, (name, handler) in enumerate([
        ("parse", parse), ("skills", load_skills), ("report", report),
        ("extensions", extensions), ("validate", validate), ("export", export),
    ], 1):
        def run_node(state, fn=handler, phase_index=index):
            check_time()
            jobs.phase(task_id, attempt, phase_index, "RUNNING")
            delta = fn(state)
            check_time()
            write_json(directory / f"stage-{phase_index}.json", {**state, **delta})
            jobs.phase(task_id, attempt, phase_index, "COMPLETED")
            return delta
        graph.add_node(name, run_node)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    result = graph.compile().invoke({})["result"]
    return result
