from .evidence import numeric_values
from .schemas import SectionDraft
from .errors import AgentError
from .settings import REPORT_CONFIG
from .quantitative_review import quantitative_issues
import re


def subsection_paths(title):
    if title == "第九章 个性化报告":
        return [c['title'] for c in REPORT_CONFIG['diagnosis']]
    chapter = next((c for c in REPORT_CONFIG['chapters'] if c['title']==title), {})
    basic = [path for group in chapter.get('outline',[]) for path in
             [group['title'],*(group['title']+' / '+c for c in group.get('children',[]))]]
    return [entry for path in basic for entry in [path,*(path+' / '+topic['title']
            for topic in chapter.get('topics',[]) if topic['parent']==path)]]


def container_only_paths(title):
    """Reference grouping headings that have no prose before their children."""
    chapter = next((c for c in REPORT_CONFIG['chapters'] if c['title']==title), {})
    paths = subsection_paths(title) if chapter else []
    parent_body_paths = set(chapter.get('parentBodyPaths', []))
    return {path for path in paths if path not in parent_body_paths and
            any(child.startswith(path+' / ') for child in paths)}


def valid_subsection(title, path):
    if not path:
        return True
    if any(c['title'] == title for c in REPORT_CONFIG['chapters']):
        return path in subsection_paths(title)
    parts = path.split(' / ')
    safe = len(parts) <= 3 and all(p.strip() == p and p and len(p) <= 80 and
        not re.search(r'[\x00-\x1f\x7f]', p) for p in parts)
    if title == '第九章 个性化报告':
        return safe and parts[0] in subsection_paths(title)
    if title in [c['title'] for c in REPORT_CONFIG['diagnosis']]:
        children=parts[1:] if parts[0]==title else parts
        return safe and len(children)<=2 and len(' / '.join([title,*children]))<=120
    return safe


def extension_path(title, path):
    return title if not path or path==title else path if path.startswith(title+' / ') else title+' / '+path


def subsection_aliases(draft):
    """Coalesce equivalent extension headings without changing fixed titles.

    Keep the first spelling and all content. Only whitespace and a single
    Chinese year numeral are equivalent; distinct themes/parents stay distinct.
    """
    if any(c['title'] == draft.title for c in REPORT_CONFIG['chapters']):
        return {}
    numerals = dict(zip('一二三四五六七八九十', range(1, 11)))
    known, aliases = {}, {}
    for block in [*draft.blocks, *draft.tables]:
        if getattr(block, 'kind', None) == 'gap' or not block.subsection:
            continue
        parts = block.subsection.split(' / ')
        for size in range(1, len(parts)+1):
            original = ' / '.join(parts[:size])
            key = ' / '.join(re.sub(r'([一二三四五六七八九十])(?=年)',
                lambda m: str(numerals[m[1]]), re.sub(r'\s+', '', p)) for p in parts[:size])
            parent = aliases.get(' / '.join(parts[:size-1]), ' / '.join(parts[:size-1]))
            proposed = ' / '.join([parent, parts[size-1]]) if parent else parts[size-1]
            aliases[original] = known.setdefault(key, proposed)
    return aliases


def document_paths(draft):
    """Preserve the fixed template; extensions may use their own thematic headings."""
    fixed = subsection_paths(draft.title)
    if any(c['title'] == draft.title for c in REPORT_CONFIG['chapters']):
        return fixed
    dynamic = []
    aliases = subsection_aliases(draft)
    for block in [*draft.blocks, *draft.tables]:
        if getattr(block,'kind',None)=='gap':
            continue
        if block.subsection and valid_subsection(draft.title, block.subsection):
            parts = block.subsection.split(' / ')
            for size in range(1, len(parts) + 1):
                original = ' / '.join(parts[:size])
                path = aliases.get(original, original)
                if path not in dynamic:
                    dynamic.append(path)
    if fixed:
        return [path for parent in fixed for path in
                [parent, *[p for p in dynamic if p.startswith(parent + ' / ')]]]
    return dynamic


def template_coverage(draft):
    paths = subsection_paths(draft.title)
    leaves = [path for path in paths if not any(child.startswith(path+' / ') for child in paths)]
    occupied = {block.subsection or (paths[0] if paths else '') for block in draft.blocks if block.kind != 'gap'}
    fixed = any(c['title'] == draft.title for c in REPORT_CONFIG['chapters'])
    written = {path for path in leaves if path in occupied or
               (not fixed and any(p.startswith(path+' / ') for p in occupied))}
    return {'requiredTopics': len(leaves), 'writtenTopics': len(written),
            'unwrittenTopics': [path for path in leaves if path not in written]}


def validate_section(draft: SectionDraft, title: str, store, year: int, *, review_semantics=True,
                     review_structure=True) -> list[str]:
    issues = []
    if draft.title != title:
        issues.append(f"标题须逐字等于：{title}")
    nonleaf_paths = container_only_paths(title)
    summary_blocks = [block for block in draft.blocks
                      if block.subsection == '本章小结' and block.kind != 'gap']
    if review_structure and any(block.kind == 'recommendation' for block in summary_blocks) and not any(
            block.kind in ('finding', 'inference') for block in summary_blocks):
        index = next(i for i, block in enumerate(draft.blocks, 1)
                     if block.subsection == '本章小结' and block.kind == 'recommendation')
        issues.append(f'第 {index} 段 本章小结必须先有 finding/inference 分析段，再写 recommendation【建议】段；不能直接以建议开篇')
    for index, block in enumerate(draft.blocks, 1):
        if not valid_subsection(title, block.subsection):
            message=('须使用 fixedOutline 原小节名称' if title in [c['title'] for c in REPORT_CONFIG['chapters']]
                     else '须使用简短有效的逻辑标题，诊断维度内部最多两级，合并路径不超过120字')
            issues.append(f"第 {index} 段 subsection {message}")
        if review_structure and block.kind != 'gap' and block.subsection in nonleaf_paths:
            issues.append(f"第 {index} 段位于二级父标题“{block.subsection}”下；该标题后须直接进入首个下级指标标题，请把内容移到对应叶子小节或删除重复概述")
        if block.summary and (re.search(r'[\d%％]', block.summary) or
                len(re.findall(r'[。！？]', block.summary)) > 1):
            issues.append(f"第 {index} 段 summary 须为不含数字的一句简短定性结论，数值放入 text")
        if block.kind in ("finding", "inference") and not block.source_ids:
            issues.append(f"第 {index} 段事实或判断缺少引用")
        known = [store.by_id[key] for key in block.source_ids if key in store.by_id]
        if len(known) != len(block.source_ids):
            issues.append(f"第 {index} 段引用不存在或不属于本次任务")
        if block.kind in ("finding", "inference"):
            if review_semantics:
                issues.extend(f'第 {index} 段 {issue}' for issue in quantitative_issues(block.summary+'。'+block.text, known))
            if re.search(r'同期上行|逐年(?:上升|下降|增长|提升)|(?:较上届|较去年|同比|持续|连续)[^。；]{0,12}(?:上升|下降|增长|提升)',block.text):
                comparable = [s for s in known if s.category=='DATA' and '合并' not in s.name+s.location]
                if not any(len(set(re.findall(r'20\d{2}',s.text))) >= 2 for s in comparable):
                    issues.append(f"第 {index} 段趋势判断缺少两个明确届次的可比DATA证据，改为当届描述；两届合并值不能代替趋势")
            numbers = numeric_values(block.text) - {year}
            allowed = set().union(*(numeric_values(source.text) for source in known if source.category != "REFERENCE")) if known else set()
            if numbers - allowed:
                unmatched = "、".join(str(value) for value in sorted(numbers - allowed))
                issues.append(f"第 {index} 段以下数字未能在对应引用中找到：{unmatched}。请删除或改写这些未经核实的数值，或引用确实包含这些数值的原始/计算证据；不得自行计算差值")
            if numbers and known and all(source.category == "REFERENCE" for source in known):
                issues.append(f"第 {index} 段不能仅以参考报告数字作为本次校本指标")
    for index, table in enumerate(draft.tables, 1):
        if not valid_subsection(title, table.subsection):
            issues.append(f"第 {index} 个表格 subsection 须使用 fixedOutline 原小节名称")
        # Numbered catalogue tables are program-owned. Freeform supplemental
        # tables stay unnumbered, preventing reused or invented original numbers.
        if re.search(r'(?:图|表)\s*\d+\s*[-—]\s*\d+',table.title):
            issues.append(f"第 {index} 个表格不得自拟原图表编号，请使用不带编号的说明性表题；原编号由图表工具管理")
        known = [store.by_id[key] for key in table.source_ids if key in store.by_id]
        if len(known) != len(table.source_ids):
            issues.append(f"第 {index} 个表格引用无效")
        numbers = numeric_values(" ".join(cell for row in table.rows for cell in row)) - {year}
        allowed = set().union(*(numeric_values(source.text) for source in known if source.category != "REFERENCE")) if known else set()
        if numbers - allowed:
            unmatched = "、".join(str(value) for value in sorted(numbers - allowed))
            issues.append(f"第 {index} 个表格以下数值缺少对应原始或计算证据：{unmatched}。请修正对应单元格，无依据的数值单元格标为—，不得另造数据")
    seen = set()
    for visual in draft.visuals:
        if visual.template_id in seen:
            issues.append("同一图表编号不能重复插入")
        seen.add(visual.template_id)
        try:
            if not store.visuals:
                raise AgentError("CHART_ASSET", "图表尚未生成")
            store.visuals.validate(visual.asset_id, visual.template_id, title)
        except AgentError as error:
            issues.append(error.message)
    return issues
