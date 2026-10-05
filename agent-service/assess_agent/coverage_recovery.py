"""Complete supported omissions, keeping accepted facts and explicit failure reasons."""
from .errors import AgentError
from .schemas import ContentBlock, SectionDraft
from .topic_evidence import fallback_findings, normalize_subsections, topic_packets
from .validation import template_coverage, validate_section
from .repair import prepare_repair
from .checkpoints import candidate_payload, restore_candidate


def recover_coverage(writer, draft, config, year, check=lambda: None, *, progress=None, checkpoint=None):
    store=writer.evidence
    normalize_subsections(draft,store)
    missing=set(template_coverage(draft)['unwrittenTopics'])
    packets={p['subsection']:p for p in topic_packets(store,draft.title)}
    supported=[path for path in packets if path in missing and packets[path]['source_ids']]
    progress=progress or {}
    audit=list(progress.get('audit',[]))
    pending=progress.get('batch')

    def save_batch(candidate=None, paths=None, repaired=False):
        if checkpoint:
            checkpoint(draft,{'audit':audit,'batch':{
                'paths':paths,'section':candidate_payload(candidate,store),
                'repairCompleted':repaired} if candidate else None})

    for offset in range(0,len(supported),8):
        check()
        batch=supported[offset:offset+8]
        candidate=restore_candidate(pending['section'],store,draft.title,year) if (
            isinstance(pending,dict) and pending.get('paths')==batch and isinstance(pending.get('section'),dict)) else None
        repaired=bool(candidate and pending.get('repairCompleted'))
        if candidate is None:
            try:
                candidate=writer.write(draft.title,config['query'],guidance=config.get('guidance',''),focus_paths=batch)
            except AgentError as error:
                if error.code!='MODEL_STRUCTURE':raise
        written=set()
        if candidate:
            normalize_subsections(candidate,store)
            save_batch(candidate,batch,repaired)
            issues=validate_section(candidate,draft.title,store,year)
            if issues and not repaired:
                scope=prepare_repair(candidate,issues)
                try:
                    revised=writer.write(draft.title,config['query'],issues=scope.issues,draft=scope.input().model_dump(),
                        repair_source_ids=scope.evidence_ids,guidance='只修复本批遗漏小节中未通过校验的内容，保留原主题和路径。')
                    candidate=scope.merge(revised)
                    normalize_subsections(candidate,store)
                except AgentError as error:
                    if error.code!='MODEL_STRUCTURE':raise
                save_batch(candidate,batch,True)
            for block in candidate.blocks:
                if block.subsection not in batch or block.kind=='gap':continue
                if not validate_section(SectionDraft(title=draft.title,blocks=[block]),draft.title,store,year):
                    draft.blocks.append(block);written.add(block.subsection)
        for path in batch:
            fallback=False
            if path not in written:
                for block in fallback_findings(packets[path]):
                    if not validate_section(SectionDraft(title=draft.title,blocks=[block]),draft.title,store,year):
                        draft.blocks.append(block);written.add(path);fallback=True
            audit.append({'subsection':path,'status':'completed' if path in written else 'generation_incomplete',
                          'method':'verified_statistics_fallback' if fallback else 'model_completion',
                          'sourceIds':packets[path]['source_ids']})
        save_batch()
        pending=None
    # A chapter summary never requires a new uploaded document. Its already
    # verified findings are sufficient to preserve a factual short summary.
    occupied={b.subsection for b in draft.blocks if b.kind!='gap'}
    findings=[b for b in draft.blocks if b.kind=='finding' and b.subsection!='本章小结']
    if '本章小结' in missing and '本章小结' not in occupied and findings:
        selected=[];topics=set()
        for block in findings:
            if block.subsection in topics:continue
            topics.add(block.subsection);selected.append(block)
            if len(selected)==3:break
        text=''.join(b.text.split('。',1)[0]+'。' for b in selected)
        sources=list(dict.fromkeys(k for b in selected for k in b.source_ids))[:15]
        summary=ContentBlock(kind='finding',text=text,source_ids=sources,subsection='本章小结')
        if not validate_section(SectionDraft(title=draft.title,blocks=[summary]),draft.title,store,year):
            draft.blocks.insert(0,summary);audit.append({'subsection':'本章小结','status':'completed','method':'verified_summary'})
    remaining=template_coverage(draft)['unwrittenTopics']
    audit.extend({'subsection':path,'status':'no_verified_statistics','reason':'尚未形成该项目的统计结果；不能据此断言用户未上传资料。'}
                 for path in remaining if path not in supported)
    coverage={**template_coverage(draft),'recovery':audit}
    store.topic_coverage[draft.title]=coverage
    # Retire model-wide failure/coverage messages after recovering actual facts.
    if not remaining and any(b.kind!='gap' for b in draft.blocks):
        draft.blocks=[b for b in draft.blocks if b.kind!='gap' or b.subsection]
    save_batch()
    return draft,coverage
