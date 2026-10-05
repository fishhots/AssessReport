"""Organize extension prose into sustained themes while retaining every block."""
import re

from .settings import DIAGNOSIS_TITLES

TOPIC_HEADINGS=('现状基础与主要问题','培养方向与学习成果','课程实践与实施路径','实施保障与评价')
DIAGNOSIS_HEADINGS=('现状与核心问题','改进方向与实施路径','条件保障与评价')


def explicit_outline(requirements):
    """A user-specified outline takes precedence over default consolidation."""
    return bool(re.search(r'(?:按|遵循|保留|使用).{0,15}(?:提纲|小节|章节结构|以下结构)|'
        r'(?:分为|设置|包含)\s*[一二三四五六七八九十\d]+\s*(?:个)?(?:小节|部分)|'
        r'(?:提纲|小节标题|章节结构)\s*[:：]',requirements))


def theme_index(path, ordinal, total, topic):
    if any(w in path for w in ('实施条件','保障','评价方式','评价体系','评价标准','评估','验证','监测','资源与','风险','制度')):
        return 3 if topic else 2
    if topic and any(w in path for w in ('培养定位','培养方向','专业结构','培养目标','培养模式','学习成果','情景','能力标准')):
        return 1
    if any(w in path for w in ('现状','基点','需求','信号','能力结构','培养过程约束','问题','基础','证据')):
        return 0
    if any(w in path for w in ('课程','实践','阶段','路径','推进','任务安排','改革','行动','建议')):
        return 2 if topic else 1
    return min((4 if topic else 3)-1,ordinal*(4 if topic else 3)//total)


def normalize_extension_structure(draft, requirements=''):
    topic=draft.title.startswith('第十章 ')
    standalone=draft.title in DIAGNOSIS_TITLES
    personalized=draft.title=='第九章 个性化报告'
    if not (topic or standalone or personalized) or explicit_outline(requirements):return draft
    entries=[e for e in [*draft.blocks,*draft.tables] if getattr(e,'kind',None)!='gap']
    for parent in list(DIAGNOSIS_TITLES) if personalized else ['']:
        selected=[e for e in entries if not parent or e.subsection==parent or e.subsection.startswith(parent+' / ')]
        def local_path(e):
            return e.subsection[len(parent)+3:] if parent and e.subsection.startswith(parent+' / ') else '' if parent else e.subsection
        paths=list(dict.fromkeys(local_path(e).split(' / ')[0] for e in selected if local_path(e)))
        if len(paths)<=(4 if topic else 3):continue
        headings=TOPIC_HEADINGS if topic else DIAGNOSIS_HEADINGS
        mapping={p:headings[theme_index(p,i,len(paths),topic)] for i,p in enumerate(paths)}
        for entry in selected:
            path=local_path(entry)
            if not path:continue
            short=mapping[path.split(' / ')[0]]
            entry.subsection=parent+' / '+short if parent else short
    return draft
