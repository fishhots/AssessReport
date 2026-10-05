"""Question-level statistics, with explicit denominators and task provenance.

Only registered DATA is read. No reference-report values, free text answers or
implicit classification of universities/professional accreditation is used.
"""
from collections import Counter, defaultdict
from decimal import Decimal
import re

from .errors import AgentError
from .settings import REPORT_CONFIG

MISSING = {'', 'NA', 'N/A', '无回答', '未回答', '—', '-', 'NULL', 'NONE', 'NAN'}
TOPICS = {
    '第一章 社会需求适应': ['是否有工作', '未收到录用', '不接受已有录用', '考研服务', '求职服务', '目前的打算', '所在地', '行业', '职业名称', '职业类', '工作单位性质', '雇员数', '一流企业'],
    '第二章 就业质量': ['专业无关', '现状', '收入', '是否适应', '不适应', '离职', '职业期待'],
    '第三章 升学分析': ['升学', '是否有工作', '研究生', '硕士', '读研', '考研', '留学', '转换专业', '现状'],
    '第四章 五育成效': ['素养', '思政', '课堂教学的方式', '社团', '能力', '规划', '育人工作'],
    '第五章 教学培养反馈': ['教学', '社会需求', '产教', '合作课程', '实训基地', '产业学院', '实习', '实践', 'AI', '人工智能', '教材', '核心课程', '专业课', '教师', '任课老师', '创新创业'],
    '第六章 满意度评价': ['总体满意', '推荐', '就业服务', '求职服务', '是否有效', '第一份工作来自', '学生工作', '生活服务', '数字化', '网络学习', '设施'],
}


def present(value):
    return value.strip().upper() not in MISSING


def percentage(count, total):
    return float(round(Decimal(count) * 100 / Decimal(total), 2)) if total else None


def split_question(column):
    """Only a final, balanced option suffix; handles nested parentheses."""
    if '_' in column:
        head, tail = column.split('_',1)
        stem, option = split_question(head)
        if option: return stem, option + ' / ' + tail
    normalized = column.replace('（', '(').replace('）', ')')
    if not normalized.endswith(')'):
        return column, ''
    depth = 0
    for i in range(len(normalized) - 1, -1, -1):
        if normalized[i] == ')': depth += 1
        elif normalized[i] == '(':
            depth -= 1
            if depth == 0:
                return column[:i], column[i + 1:-1]
    return column, ''


def frequency(values, top_n=None):
    counts = Counter(v.strip() for v in values if present(v))
    total = sum(counts.values())
    items = counts.most_common(top_n)
    others = total - sum(c for _, c in items)
    rows = [{'label': v, 'count': c, 'percentage': percentage(c, total)} for v, c in items]
    if others:
        rows.append({'label': '其余类别合计', 'count': others, 'percentage': percentage(others, total)})
    return {'validCount': total, 'unansweredCount': len(values) - total, 'items': rows,
            'distinctCount': len(counts), 'topN': top_n, 'otherCount': others,
            'totalPercentage': percentage(total,total)}


class SurveyAnalyzer:
    def __init__(self, store, year, school):
        self.store, self.year, self.school = store, year, school
        self.questions = []
        self.issues = []
        self.aggregates = []
        self.scale_comparisons = []

    def build(self):
        for table in self.store.corpus.tables:
            if self.store.cancel_check: self.store.cancel_check()
            if not table.source_ids or any(self.store.read(k).category != 'DATA' for k in table.source_ids): continue
            if '学院名称' in table.columns and '专业名称' not in table.columns and '合并' not in table.name+table.sheet and set(re.findall(r'20\d{2}',table.name+table.sheet))=={str(self.year)}:
                label_index=table.columns.index('学院名称')
                summaries=[r for r in table.rows if r[label_index] in ('本校平均','全校平均')]
                if len(summaries)==1:
                    metrics={c:summaries[0][i] for i,c in enumerate(table.columns) if i!=label_index and present(summaries[0][i])}
                    result=self.store.computed(table,{'cohort':self.year,'type':'school_aggregate','metrics':metrics,
                        'basis':'原表本校平均行；优先用于同名正式校本指标，不平均学院均值；原表未列有效样本时不编造样本数。'})
                    self.aggregates.append({'materialId':table.material_id,'sheet':table.sheet,'columns':[],**result})
            # Aggregated tables remain handled by the chart registry. Survey rows
            # must have a declared cohort, or a unique year in the source filename.
            if '届次' in table.columns:
                yi = table.columns.index('届次')
                rows = [r for r in table.rows if r[yi] == str(self.year)]
            elif len(table.columns) >= 20 and '合并' not in table.name + table.sheet and set(re.findall(r'20\d{2}', table.name + table.sheet)) == {str(self.year)}:
                rows = table.rows
            else: continue
            if not rows: continue
            groups = defaultdict(list)
            for i, column in enumerate(table.columns):
                if any(t in column for t in ('自填', '有何建议', '高考', '邮件定制', '成绩状况', '父母', '民族', '性别')): continue
                stem, option = split_question(column)
                groups[stem].append((i, column, option))
            status = next((c for c in table.columns if c.startswith('您现在是否有工作')), '')
            for stem, columns in groups.items():
                self._register(table, rows, stem, columns)
                self._paired_scale(table,rows,stem,columns)
            if status:
                si = table.columns.index(status)
                studying = [r for r in rows if r[si] == '正在中国境内读研']
                # Survey questions mix actual study and intention in one column.
                # Never merge them into "already enrolled" metrics.
                for stem, columns in groups.items():
                    if any(x in stem for x in ('硕士', '研究生', '考研', '读研', '转换专业', '现状')):
                        self._register(table, studying, stem, columns, scope='正在中国境内读研', population=len(studying))
                for label, predicate, keyword in [
                    ('准备中国境内考研', lambda value: value == '准备中国境内考研', '考研服务'),
                    ('无工作且继续寻找工作', lambda value: value.startswith('无工作，继续寻找工作'), '求职服务')]:
                    filtered = [r for r in rows if predicate(r[si])]
                    for stem, columns in groups.items():
                        if keyword in stem:
                            self._register(table, filtered, stem, columns, scope=label, population=len(filtered))
                for label, pattern in [('受雇全职工作样本', r'^受雇全职工作'), ('境外留学样本', r'^正在港澳台深造或国外留学')]:
                    filtered = [r for r in rows if re.search(pattern, r[si])]
                    for stem, columns in groups.items():
                        if stem.startswith('您对自己的现状') or (label=='境外留学样本' and any(t in stem for t in ('留学','主修','转换专业'))):
                            self._register(table, filtered, stem, columns, scope=label, population=len(filtered))
                self._study_metrics(table, rows, status)
        return self

    def _register(self, table, rows, stem, columns, scope='', population=None):
        if not rows: return
        indices = [i for i, _, _ in columns]
        values = {v.strip() for r in rows for i in indices if present(v := r[i])}
        if not values: return
        binary = len(columns) >= 2 and values <= {'0', '1', '0.0', '1.0'} and any(float(v) == 1 for v in values)
        # A multi-hot question requires a complete 0/1 row. Missing cells are not
        # unselected options, nor are off-branch rows treated as negative answers.
        if binary:
            valid = [r for r in rows if all(present(r[i]) for i in indices)]
            if not valid: return
            stats = {'type': 'multiple', 'validCount': len(valid), 'unansweredCount': len(rows) - len(valid),
                     'encoding': '拆列多选题：1=选中，0=未选；仅完整编码行参与统计',
                     'items': [{'label': option or col, 'count': sum(float(r[i]) == 1 for r in valid),
                                'percentage': percentage(sum(float(r[i]) == 1 for r in valid), len(valid))}
                               for i, col, option in columns]}
        elif len(columns) == 1 and not all(re.fullmatch(r'-?\d+(\.\d+)?', v) for v in values):
            stats = {'type': 'single', **frequency([r[indices[0]] for r in rows])}
        elif len(columns) >= 2 and not all(re.fullmatch(r'-?\d+(\.\d+)?', v) for v in values):
            stats = {'type': 'matrix', 'items': [dict(label=option or col, **frequency([r[i] for r in rows])) for i, col, option in columns]}
        elif all(re.fullmatch(r'-?\d+(\.\d+)?', v) for v in values) and any(t in stem for t in ('分5级', '分7级', '收入')):
            stats = {'type': 'numeric', 'items': []}
            for i, col, option in columns:
                numbers = [Decimal(r[i]) for r in rows if present(r[i])]
                if not numbers: continue
                maximum = 5 if '分5级' in stem else 7 if '分7级' in stem else None
                if maximum and any(n < 1 or n > maximum for n in numbers): continue
                stats['items'].append({'label': option or col, 'validCount': len(numbers),
                    'mean': float(round(sum(numbers) / len(numbers), 2)), 'unit': '元' if '收入' in stem else '级',
                    'meaning': '需求/掌握水平的原量表均值；不等于达成率或重要度百分比'})
            if not stats['items']: return
        else:
            return  # Undocumented numeric codes and arbitrary means are excluded.
        # Textual importance must not suppress explicit numerical subscales.
        if stats['type']=='matrix' and any(t in stem for t in ('分5级','分7级')):
            maximum=5 if '分5级' in stem else 7
            for entry,i in zip(stats['items'],indices):
                valid=[r[i] for r in rows if present(r[i])]
                if valid and all(re.fullmatch(r'\d+(?:\.\d+)?',v) and 1<=Decimal(v)<=maximum for v in valid):
                    entry.update(mean=float(round(sum(Decimal(v) for v in valid)/len(valid),2)),unit='级',
                        scaleMinimum=1,scaleMaximum=maximum,
                        meaning='该子题原量表均值；不等于达成率或重要度百分比')
        from .survey_visuals import POSITIVE, NEGATIVE
        if stats['type'] in ('single','matrix'):
            for entry in ([stats] if stats['type']=='single' else stats['items']):
                positive=[r for r in entry['items'] if r['label'] in POSITIVE]
                if any(r['label'] in POSITIVE | NEGATIVE for r in entry['items']):
                    entry['positiveLabels']=[r['label'] for r in positive]
                    entry['positiveCount']=sum(r['count'] for r in positive)
                    entry['positivePercentage']=percentage(entry['positiveCount'],entry['validCount'])
                    entry['nonPositiveCount']=entry['validCount']-entry['positiveCount']
                    entry['nonPositivePercentage']=percentage(entry['nonPositiveCount'],entry['validCount'])
        descriptor = stem.replace('{$学校名称}',self.school).replace('{$届次}',str(self.year))
        if '分5级' in stem or '分7级' in stem: descriptor = '能力量表：' + descriptor
        stats.update(question=descriptor, scope=scope or '本届该题有效回答样本', cohort=self.year,
                     populationCount=population if population is not None else len(rows),
                     basis='比例以该题/子题有效回答数为分母；未作答可能包含问卷跳题，不等同于所有毕业生资料缺失。多选比例不求和。')
        result = self.store.computed(table, stats)
        # Location identifies the question as well as its sheet; not hundreds of
        # indistinguishable references to the same source rows.
        self.store.read(result['evidenceId']).location += '；题项 ' + descriptor[:100] + ('；' + scope if scope else '')
        self.questions.append({'materialId': table.material_id, 'sheet': table.sheet, 'columns': [c for _, c, _ in columns], **result})

    def _study_metrics(self, table, rows, status):
        si = table.columns.index(status)
        valid = [r for r in rows if present(r[si])]
        if not valid: return
        counts = Counter(r[si] for r in valid)
        result = self.store.computed(table, {'question': '当届实际升学去向', 'type': 'study_status', 'cohort': self.year,
            'validCount': len(valid), 'items': [{'label': label, 'count': counts[label], 'percentage': percentage(counts[label], len(valid))}
                for label in ('正在中国境内读研', '正在港澳台深造或国外留学', '正在读第二学士学位')],
            'basis': '实际毕业状态有效回答人数；不含准备考研、计划留学；问卷样本比例，不冒称全体毕业生行政统计率。'})
        self.questions.append({'materialId': table.material_id, 'sheet': table.sheet, 'columns': [status], **result})

    def _paired_scale(self,table,rows,stem,columns):
        if not any(t in stem for t in ('分5级','分7级')):return
        maximum=5 if '分5级' in stem else 7
        pairs=defaultdict(lambda:defaultdict(list))
        for i,name,option in columns:
            if ' / ' not in option:continue
            ability,phase=option.rsplit(' / ',1)
            if '掌握' in phase:pairs[ability]['mastery'].append(i)
            elif '需要' in phase or '需求' in phase:pairs[ability]['demand'].append(i)
        items=[]
        for ability,phases in pairs.items():
            if len(phases['mastery'])!=1 or len(phases['demand'])!=1:continue
            mi,di=phases['mastery'][0],phases['demand'][0]
            values=[]
            for row in rows:
                if not all(re.fullmatch(r'\d+(?:\.\d+)?',row[i]) for i in (mi,di)):continue
                m,d=Decimal(row[mi]),Decimal(row[di])
                if 1<=m<=maximum and 1<=d<=maximum:values.append((m,d))
            if not values:continue
            items.append({'label':ability,'validCount':len(values),
                'masteryMean':float(round(sum(m for m,d in values)/len(values),2)),
                'demandMean':float(round(sum(d for m,d in values)/len(values),2)),
                'demandMinusMasteryMean':float(round(sum(d-m for m,d in values)/len(values),2)),
                'masteryAtLeastDemandCount':sum(m>=d for m,d in values),
                'masteryAtLeastDemandPercentage':percentage(sum(m>=d for m,d in values),len(values))})
        if not items:return
        content={'type':'paired_scale','question':'配对能力分析：'+stem,'cohort':self.year,'items':items,
            'scaleMaximum':maximum,'unit':'级','scope':'本届两项均有效的配对回答',
            'basis':'同一条问卷同一能力的需求与掌握配对；逐项有效分母；掌握不低于需求的回答比例不是原报告未定义算法的综合达成度。'}
        result=self.store.computed(table,content)
        self.scale_comparisons.append({'materialId':table.material_id,'sheet':table.sheet,
            'columns':[c for _,c,_ in columns],**result})
        # Make the same verified analysis available to focused section recovery,
        # which deliberately omits the general chapter statistics context.
        from .validation import subsection_paths
        names=' '.join(i['label'] for i in items)
        family='通用能力' if maximum==7 else '工程类专业' if '工程' in names else '商科类专业' if '商科' in stem+names or ('经济原理' in names and '金融知识' in names) else ''
        if family:
            title='第四章 五育成效'
            for path in subsection_paths(title):
                if family in path:
                    self.store.topic_sources.setdefault(title,{}).setdefault(path,[]).append(result['evidenceId'])

    def context(self, title, query=''):
        keywords = TOPICS.get(title, [])
        if title == '核心发现': keywords = ['当届实际升学', '教学满意', '总体满意', '社会需求', '就业服务', '规划']
        if title == '第八章 技术报告':
            return {'statisticsPolicy': '按届次筛选；每题有效回答为分母；多选0/1拆列；不将问卷样本数冒称实际毕业生人数；无行政总体时不能报告回收率/样本代表性。', 'questionCount': len(self.questions)}
        if title == '第七章 国家级一流本科专业建设点分析': keywords = ['一流专业']
        dimensions = [d['title'] for d in REPORT_CONFIG['diagnosis']]
        if title in dimensions:
            keywords = (TOPICS['第一章 社会需求适应'] + ['专业','职业','就业','培养目标'] if title == dimensions[0]
                        else TOPICS['第四章 五育成效'] if title == dimensions[1]
                        else TOPICS['第五章 教学培养反馈'] + ['能力','实践','课程'])
        if not keywords and query:
            words = re.findall(r'[A-Za-z0-9]+|[\u4e00-\u9fff]+',query[:500])
            keywords = list(dict.fromkeys(token for word in words for token in
                            ([word] if len(word)<3 else [word,*(word[i:i+2] for i in range(len(word)-1))])))
        selected = [q for q in self.questions+self.scale_comparisons if any(k in q['question'] for k in keywords)]
        if title.startswith('第三章'):
            actual={(q['materialId'],q['sheet'],q['question']) for q in selected if q.get('scope') in ('正在中国境内读研','境外留学样本')}
            selected=[q for q in selected if q.get('scope') != '受雇全职工作样本' and
                      (q.get('scope')!='本届该题有效回答样本' or (q['materialId'],q['sheet'],q['question']) not in actual)]
        if title.startswith('第二章'):
            selected=[q for q in selected if not q['question'].startswith('您对自己的现状') or q.get('scope')=='受雇全职工作样本']
        # Actual study questions first; cap prompt payload, never the underlying
        # question index available through the tool.
        selected.sort(key=lambda q: (q.get('scope') != '正在中国境内读研',q.get('type')!='paired_scale', q['question']))
        limit = 40 if title in dimensions else 70 if title.startswith('第五章') else 50
        output = []
        for aggregate in self.aggregates:
            metrics={k:v for k,v in aggregate['metrics'].items() if any(w in k for w in keywords)}
            if title=='核心发现':metrics=aggregate['metrics']
            if metrics:
                output.append({**aggregate,'metrics':metrics})
        for q in selected[:limit]:
            item = {k:v for k,v in q.items() if k not in ('columns','location')}
            from .quantitative_review import comparison_cards
            item['comparisonCards']=comparison_cards(q)
            if len(item.get('items',[])) > 24:
                item['itemCount'] = len(item['items'])
                item['items'] = item['items'][:24]
                item['moreItems'] = 'analyze_survey_question 可读取完整统计；当前概览截断不能推断其余题项不存在'
            output.append(item)
        return output

    def analyze(self, material_id, sheet, question, scope=''):
        matches = [q for q in self.questions+self.scale_comparisons if q['materialId'] == material_id and q['sheet'] == sheet
                   and question in q['question'] and (not scope or q.get('scope') == scope)]
        if not matches: raise AgentError('SURVEY_QUESTION', '没有符合条件的已统计题项，请查看全部字段或明确题目与样本范围')
        if len(matches) > 10: raise AgentError('SURVEY_LIMIT', '匹配题项过多，请使用更完整的题干')
        return {'questions': matches}
