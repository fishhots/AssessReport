"""Formal report presentation; provenance remains in structured audit records."""
import re


RECOMMENDATION_LABEL = re.compile(r'^\s*(?:(?:【\s*建议\s*】|\[\s*建议\s*\])\s*)+')


AUDIT_CLAUSE = re.compile(
    r'^(?:取自|来源(?:为|于)|数据来源(?:为|于)|来自(?:本校|原表|工作表))|'
    r'(?:原表|原始表|资料|本次资料|上传资料)(?:中)?(?:未列|未提供|亦未提供)[^，；。]*(?:有效样本|样本数|可比|历届|历史|对照|样本量)|'
    r'原表不含.*有效样本|(?:故|因此)?(?:本次)?仅呈现当届水平|'
    r'^(?:本次)?不作(?:跨届|趋势|同比)|^(?:该)?(?:单位|数值)与.*(?:表|口径).*一致|'
    r'^(?:本次)?(?:尚未|未能)形成.*可核验|'
    r'^(?:本次|由于未作|且未作).*(?:不能断言|不宜断言).*统计.*显著|'
    r'^(?:上述|以上|本次)(?:判断|分析|结论|结果).*(?:均基于|不涉及统计|未进行统计)|'
    r'^(?:需说明口径|口径说明|证据边界|分析边界)[:：]|'
    r'(?:不含|不涉及|未进行).*(?:直接考核证据|认证达成度计算|显著性检验)')


def chapter_introduction_paragraphs(text):
    """Split the program-owned chapter opening like the reference report.

    The reference uses a short context paragraph followed by a separate
    paragraph beginning with “本章…”.  Keep a single paragraph when the
    configured copy has no explicit scope sentence.
    """
    text = (text or '').strip()
    if not text:
        return []
    match = re.search(r'(?<=。)(本章(?:围绕|从|重点|主要))', text)
    if not match:
        return [text]
    return [part for part in (text[:match.start()].strip(), text[match.start():].strip()) if part]


def formal_text(text):
    text = re.sub(r'\*\*(.*?)\*\*',r'\1',text,flags=re.S)
    labelled = bool(RECOMMENDATION_LABEL.match(text))
    text = RECOMMENDATION_LABEL.sub('', text)
    # Remove only identifiable process/provenance clauses, never actual sample
    # scopes such as studying vs intending to study, or pooled vs annual data.
    sentences=[]
    for sentence in re.split(r'(?<=[。！？])',text):
        if not sentence.strip():continue
        ending=sentence[-1] if sentence[-1] in '。！？' else ''
        body=sentence[:-1] if ending else sentence
        clauses=re.split(r'[，；]',body)
        kept=[c for c in clauses if c.strip() and not AUDIT_CLAUSE.search(c.strip())]
        if kept:sentences.append('，'.join(kept)+ending)
    body = ''.join(sentences).strip()
    return ('【建议】' if labelled and body else '') + body


def recommendation_paragraph(block, text, show_label=False):
    """Display a selected action paragraph without marking every recommendation."""
    body = RECOMMENDATION_LABEL.sub('', text).strip()
    return ('【建议】' if block.kind == 'recommendation' and show_label and body else '') + body


def qualitative_lead(block, text):
    """Never turn a row of metrics into a bold editorial judgement."""
    if block.kind not in ('finding', 'inference'):
        return '', text
    if block.summary:
        summary = RECOMMENDATION_LABEL.sub('', formal_text(block.summary)).strip()
        if summary and not re.search(r'[\d%％]', summary):
            summary = summary.rstrip('。！？') + '。'
            remainder = text[len(summary):] if text.startswith(summary) else text
            return summary, remainder
    match = re.match(r'^([^。！？：:]{1,60})[。！？：:]', text)
    if match and not re.search(r'[\d%％]', match.group(1)):
        return match.group(1).strip() + '。', text[match.end():]
    return '', text


def core_heading_conclusions(section):
    """Reuse verified editorial leads as dynamic core-finding heading text."""
    if section.title != '核心发现':
        return {}
    from .validation import subsection_paths
    paths = subsection_paths(section.title)
    leaves = {p for p in paths if not any(child.startswith(p+' / ') for child in paths)}
    conclusions = {}
    for path in leaves:
        blocks = [b for b in section.blocks if b.subsection == path and b.kind in ('finding', 'inference')]
        # Prefer the explicit editorial summary. Legacy drafts may already have
        # the same qualitative conclusion as the opening sentence of their text.
        for block in sorted(blocks, key=lambda b: not bool(b.summary)):
            topic = path.split(' / ')[-1]
            text = formal_text(block.text)
            if text.startswith(topic+'：') or text.startswith(topic+':'):
                text = text[len(topic)+1:].lstrip()
            lead, _ = qualitative_lead(block, text)
            lead = lead.rstrip('。！？').strip()
            if lead.startswith(topic+'：') or lead.startswith(topic+':'):
                lead = lead[len(topic)+1:].strip()
            if lead and lead != topic and len(lead) <= 60 and not re.search(r'[\d%％\n\r]', lead):
                conclusions[path] = lead
                break
    return conclusions


def without_heading_conclusion(text, conclusion, topic=''):
    """Move a repeated opening sentence into the heading without losing detail."""
    candidate = text
    if topic and (text.startswith(topic+'：') or text.startswith(topic+':')):
        candidate = text[len(topic)+1:].lstrip()
    for ending in ('。', '！', '？', '：', ':'):
        prefix = conclusion + ending
        if candidate.startswith(prefix):
            return candidate[len(prefix):].lstrip()
    return '' if candidate == conclusion else text


def visual_note(data):
    notes=[]
    if data.get('multipleResponse'):notes.append('本题为多选，各项比例之和可超过100%')
    if data.get('type')=='scatter':notes.append('虚线为本图有效点的均值')
    if data.get('missingCount') and any(v is None for s in data.get('series',[]) for v in s['values']):
        notes.append('空缺表示无有效指标值，不计作零')
    basis=data.get('basis','')
    if data.get('templateId')=='table-5-5' and '高频为总是或经常' in basis:
        notes.append('高频指总是或经常使用；各工作任务分别统计，列示高频比例最高与最低的代表任务，并列至多列两项')
    if '合并' in basis:notes.append('本图表采用合并届次数据')
    if '记录数不是' in basis or '记录数不是' in basis.replace('，','') or '课程评价记录' in basis:
        notes.append('按有效评价记录统计，评价记录数不等同于毕业生人数')
    if '少于5' in basis:notes.append('有效回答少于5的组别未列入')
    if ('各工作任务' in basis or '工作任务分别' in basis or '职业类及工作任务' in basis) and '高频为总是或经常' not in basis:
        notes.append('按工作任务分别统计，每项以该任务有效回答为分母')
    return '注：'+'；'.join(notes)+'。' if notes else ''
