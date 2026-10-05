"""Export only verified content when a section has insufficient evidence."""
from .schemas import ContentBlock, SectionDraft
from .validation import validate_section


def skipped_section(title: str, hint: str, generation_error=False) -> SectionDraft:
    reason = "本章分析本次未能完成，已保留章节位置，相关结论暂未输出。" if generation_error else "现有已上传资料不足以形成可核验的本章分析，相关结论暂未输出。"
    return SectionDraft(title=title, blocks=[ContentBlock(kind="gap", text=reason + "后续可补充：" + hint)])


def salvage_section(draft: SectionDraft, title: str, store, year: int, hint: str):
    blocks, tables, visuals = [], [], []
    placeholder = ContentBlock(kind="gap", text="校验占位")
    for block in draft.blocks:
        candidate = SectionDraft(title=title, blocks=[block])
        if not validate_section(candidate, title, store, year):
            blocks.append(block)
    for table in draft.tables:
        candidate = SectionDraft(title=title, blocks=[placeholder], tables=[table])
        if not validate_section(candidate, title, store, year):
            tables.append(table)
    seen = set()
    for visual in draft.visuals:
        candidate = SectionDraft(title=title, blocks=[placeholder], visuals=[visual])
        if visual.template_id not in seen and not validate_section(candidate, title, store, year):
            visuals.append(visual)
            seen.add(visual.template_id)
    skipped = {"blocks": len(draft.blocks) - len(blocks), "tables": len(draft.tables) - len(tables),
               "visuals": len(draft.visuals) - len(visuals)}
    text = "部分结论、表格或图表未能从已上传资料中取得可核验依据，已跳过；本章已核实的内容予以保留。后续可补充：" + hint
    if len(blocks) < 96:
        blocks.append(ContentBlock(kind="gap", text=text))
    else:
        store.content_gap_notes[title] = text
    return SectionDraft(title=title, blocks=blocks, tables=tables, visuals=visuals), skipped
