"""Bound default verbosity while honoring an explicit user word count."""
import math
import re


def writing_limits(title, diagnosis, requirements=''):
    if not diagnosis:
        return {'maxBlocks':96,'maxParagraphCharacters':600,'maxTables':12,'targetCharacters':'每指标120–250字；小结300–600字'}
    topic=title.startswith('第十章 ')
    target=1800 if topic else 1200
    counts=re.findall(r'(\d+)\s*(?:[–—~～至到-]\s*(\d+)\s*)?字',requirements)
    requested=max((int(b or a) for a,b in counts),default=0)
    return {'maxBlocks':max(10 if topic else 8,min(96,math.ceil(requested/300))) if requested else (10 if topic else 8),
            'maxParagraphCharacters':max(450,min(1200,requested)) if requested else 450,
            'maxTables':4 if requested>target else 2,
            'targetCharacters':f'按用户明确要求约{requested}字' if requested else ('1200–1800字' if topic else '800–1200字'),
            'maxSubsections':4 if topic else 3,
            'paragraphsPerSubsection':'通常2–3段；同一小节连续解释证据、含义与措施，不为每段另起标题；用户明确提纲优先',
            'evidencePolicy':'每维度选2–3项关键证据；其余篇幅用于行动、条件、学习成果和评价方式，不复述前文统计。'}
