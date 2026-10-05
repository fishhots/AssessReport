"""Bindings from computed questionnaire results to the fixed report catalogue."""
from .survey import percentage

# Topic labels and original numbers are fixed; values are always computed from
# the current task's uploaded tables. Missing classifications are not guessed.
BINDINGS = {
    'figure-1-2': ('您现在是否有工作', ''),
    'figure-1-7': ('考研服务', '准备中国境内考研'),
    'figure-1-9': ('提供何种求职服务', '无工作且继续寻找工作'),
    'figure-1-10': ('您目前的打算', ''),
    'table-1-5': ('您现在就业的行业类', ''),
    'table-1-6': ('您现在就业的行业名称', ''),
    'table-1-8': ('您现在从事的职业类', ''),
    'table-1-9': ('您现在从事的职业名称', ''),
    'figure-1-11': ('工作单位性质', ''),
    'figure-1-12': ('总公司', ''),
    'figure-2-2': ('您选择专业无关工作的最主要原因', ''),
    'figure-2-6': ('您对就业现状不满意的主要原因', ''),
    'figure-2-15': ('您认为不适应的主要原因', ''),
    'figure-2-19': ('您离职的主要原因是什么', ''),
    'figure-3-13': ('您考研的最主要目的', '正在中国境内读研'),
    'figure-3-14': ('在选择硕士研究生院校时', '正在中国境内读研'),
    'figure-4-2': ('本专业教师将以上元素融入课堂教学的方式', ''),
    'figure-4-13': ('期间参加过哪类社团活动', ''),
    'figure-5-4': ('专业教学中应该改进', ''),
    'figure-5-5': ('在校期间参与过的产教融合项目类型', ''),
    'figure-5-6': ('您对合作课程以下方面', ''),
    'figure-5-7': ('您对实训基地/实验室项目以下方面', ''),
    'figure-5-8': ('您对产业学院/定制班项目以下方面', ''),
    'figure-5-9': ('您对实习/竞赛项目以下方面', ''),
    'figure-5-10': ('请您对校内实习实践进行评价', ''),
    'figure-5-11': ('请您对校外实习实践进行评价', ''),
    'figure-5-12': ('您的校外实习实践来自于', ''),
    'figure-5-13': ('在校期间参与过的“AI+X”课程或项目类型', ''),
    'figure-5-14': ('您在工作中使用AI工具/技术的主要应用场景', ''),
    'figure-5-16': ('您认为AI时代需强化的能力和素养', ''),
    'figure-5-17': ('您认为学校在AI培养方面需要加强', ''),
    'figure-5-18': ('本专业的教师提供的以下课外指导', ''),
    'figure-5-19': ('请评价您在读期间的专业课任课教师的教学方法', ''),
    'figure-5-20': ('您与任课老师课下交流的频度', ''),
    'figure-5-28': ('专业课对您在以下哪些方面帮助较大', ''),
    'figure-5-29': ('您接受过哪些类型的创新创业教育', ''),
    'figure-5-30': ('在校期间所接受的创新创业教育对您哪些方面帮助较大', ''),
    'figure-5-31': ('创新创业教育活动的以下方面进行评价', ''),
    'table-5-10': ('请您对学习过程中使用的教材情况进行评价', ''),
    'figure-6-9': ('就业辅导机构何种求职服务', ''),
    'figure-6-11': ('大学毕业后第一份工作来自于', ''),
    'figure-6-14': ('学生工作需要改进的方面', ''),
    'figure-6-15': ('请您对学校提供的数字化资源内容', ''),
    'figure-6-16': ('数字化建设情况进行评价', ''),
    'figure-6-17': ('请评价学校网络学习空间', ''),
    'figure-6-18': ('下述设施是否能满足', ''),
    'figure-6-20': ('生活服务需要改进的方面', ''),
}

POSITIVE = {'很满意','满意','非常满意','非常符合','符合','非常满足','满足','完全满足','比较满足','非常支持','支持','比较支持','是','很有帮助','有帮助','帮助很大','帮助非常大','比较适应','非常适应','非常同意','同意'}
NEGATIVE = {'很不满意','不满意','非常不满意','一般','不符合','非常不符合','不满足','非常不满足',
            '不支持','完全不支持','不太支持','不太满足','帮助不大','没有帮助','很不适应','不适应',
            '完全不适应','非常不同意','不同意','否','非常不支持'}
SINGLE_RATES = {
    'figure-3-4': ('您硕士研究生专业与本科专业是否一致/您考研', '正在中国境内读研', {'是'}),
    'figure-3-7': ('您是否在', '正在中国境内读研', {'是'}),
    'figure-3-9': ('您对自己的现状', '正在中国境内读研', {'很满意','满意'}),
    'figure-4-11': ('您目前已有明确的职业', '', {'非常符合','符合'}),
    'figure-5-1': ('您认为所学专业培养的人才是否符合社会需求', '', {'非常符合','符合'}),
    'figure-5-3': ('的教学满意程度', '', {'很满意','满意'}),
    'figure-6-1': ('的总体满意程度', '', {'很满意','满意'}),
    'figure-6-3': ('您是否愿意在同等分数同类院校中推荐', '', {'愿意'}),
    'figure-6-5': ('您是否推荐本专业给亲朋好友去就读', '', {'愿意'}),
    'figure-6-7': ('就业服务工作的满意程度', '', {'很满意','满意'}),
    'figure-6-12': ('学生工作的满意程度', '', {'很满意','满意'}),
    'figure-6-19': ('生活服务的满意程度', '', {'很满意','满意'}),
}


def find_question(analyzer, keyword, scope):
    matches = [q for q in analyzer.questions if keyword in q['question'] and
               (q.get('scope') == scope if scope else q.get('scope') == '本届该题有效回答样本')]
    return matches[0] if len(matches) == 1 else None


def plan_survey(registry):
    analyzer = registry.evidence.survey
    if not analyzer: return
    overall={'figure-1-1':'毕业去向落实率','figure-2-1':'专业相关度','figure-2-5':'就业满意度',
             'figure-2-9':'月收入','figure-2-14':'就业岗位适应性','figure-2-18':'离职率',
             'figure-6-1':'校友满意度','figure-6-3':'校友推荐度'}
    for key,column in overall.items():
        if key not in registry.coverage or registry.coverage[key]['status']!='missing':continue
        matches=[(q,c) for q in analyzer.aggregates for c in q['metrics'] if c.split('（')[0]==column or c.split('(')[0]==column]
        if len(matches)!=1:continue
        q,c=matches[0]
        from .visuals import number
        unit='元' if column=='月收入' else '%'
        value=number(q['metrics'][c],unit)
        if value is None:continue
        registry.register_statistics(key,q,[str(registry.year)],[{'name':column,'values':[value]}],q['basis']+'仅当届，未作跨届趋势比较。',0,0,kind='bar',unit=unit)
    for key, (keyword, scope) in BINDINGS.items():
        if key not in registry.coverage or registry.coverage[key]['status'] != 'missing': continue
        q = find_question(analyzer, keyword, scope)
        if not q: continue
        item = registry.item(key)
        labels, series, native, valid, missing = [], [], [], q.get('validCount',0), q.get('unansweredCount',0)
        basis = f"{registry.year}届；{q['question']}；{q['scope']}。" + q['basis']
        if q['type'] in ('single','multiple'):
            labels = [r['label'] for r in q['items']]
            series = [{'name':'比例', 'values':[r['percentage'] for r in q['items']]}]
            native = [[r['label'],str(r['count']),str(r['percentage'])] for r in q['items']]
            if key=='figure-5-29':
                series[0]['name']='参与比例'
                basis+='本图仅展示类型参与比例；有效性使用后续帮助评价题分析，不能用参与率代替有效性。'
        elif q['type'] == 'matrix':
            entries = [r for r in q['items'] if r['validCount']]
            if not entries: continue
            # Each subquestion may have a different denominator; a stacked chart
            # shows the full response distribution, never merges valid counts.
            labels = [r['label'] for r in entries]
            valid = 0
            if item['type']=='table':
                choices=list(dict.fromkeys(c['label'] for r in entries for c in r['items']))
                if key=='table-5-10' and set(choices)<={'非常符合','符合','不符合','非常不符合'}:
                    choices=[c for c in ('非常符合','符合','不符合','非常不符合') if c in choices]
                if len(choices)>8:continue
                native=[[r['label'],*[str(next((c['percentage'] for c in r['items'] if c['label']==choice),0)) for choice in choices]] for r in entries]
            elif item['type'] == 'stacked_bar':
                choices = list(dict.fromkeys(c['label'] for r in entries for c in r['items']))
                if len(choices)>8: continue
                for choice in choices:
                    series.append({'name':choice,'values':[next((c['percentage'] for c in r['items'] if c['label']==choice),0) for r in entries]})
            else:
                # Known textual response labels only. Undocumented coded matrices
                # must not be reported as satisfaction rates.
                if not all(any(c['label'] in POSITIVE | NEGATIVE for c in r['items']) for r in entries): continue
                series=[{'name':'正向评价比例','values':[percentage(sum(c['count'] for c in r['items'] if c['label'] in POSITIVE),r['validCount']) for r in entries],
                         'sampleCounts':[r['validCount'] for r in entries]}]
                native=[[r['label'],str(r['validCount']),str(v)] for r,v in zip(entries,series[0]['values'])]
                basis += '正向选项：' + '、'.join(sorted({c['label'] for r in entries for c in r['items'] if c['label'] in POSITIVE}))+'；各子题分别采用有效回答人数。'
        else: continue
        if not labels: continue
        columns=['类别','人数','比例（%）'] if q['type']!='matrix' else ['评价项目','有效回答人数','正向评价比例（%）']
        if q['type']=='matrix' and item['type']=='table':
            columns=['评价项目',*[choice+'（%）' for choice in choices]]
        registry.register_statistics(key, q, labels, series, basis, valid, missing,
                                     multiple=q['type']=='multiple',columns=columns,rows=native,
                                     kind='horizontal_bar' if item['type']=='stacked_bar' and q['type']!='matrix' else None)
    for key,(keyword,scope,positive) in SINGLE_RATES.items():
        if key not in registry.coverage or registry.coverage[key]['status']!='missing': continue
        q=find_question(analyzer,keyword,scope)
        if not q or q['type']!='single': continue
        n=sum(r['count'] for r in q['items'] if r['label'] in positive)
        basis=f"{registry.year}届；{q['question']}；{q['scope']}；正向选项：{'、'.join(sorted(positive))}；分母为该题有效回答人数。仅当届，未作跨届趋势比较。"
        registry.register_statistics(key,q,[str(registry.year)],[{'name':'比例','values':[percentage(n,q['validCount'])]}],basis,q['validCount'],q['unansweredCount'],kind='bar')
    key='figure-3-1'
    studies=[q for q in analyzer.questions if q['type']=='study_status']
    if key in registry.coverage and registry.coverage[key]['status']=='missing' and len(studies)==1:
        q=studies[0];row=q['items'][0]
        registry.register_statistics(key,q,[str(registry.year)],[{'name':'境内读研比例','values':[row['percentage']]}],q['basis']+'仅当届，未作跨届趋势比较。',q['validCount'],0,kind='bar')
    plan_grouped(registry)


def plan_grouped(registry):
    """Comparisons use row-level responses and per-group valid denominators."""
    from collections import defaultdict
    from .survey import present
    analyzer=registry.evidence.survey
    specs={
        'figure-3-2':('study_status','学院名称',None),
        'figure-3-3':('study_status','专业名称',None),
        'figure-3-5':('您硕士研究生专业与本科专业是否一致/您考研','学院名称',{'是'}),
        'figure-3-11':('您对自己的现状','您的研究生类型',{'很满意','满意'}),
        'figure-3-12':('您对自己的现状','学院名称',{'很满意','满意'}),
        'figure-4-12':('您目前已有明确的职业','学院名称',{'符合','非常符合'}),
        'figure-5-2':('您认为所学专业培养的人才是否符合社会需求','学院名称',{'符合','非常符合'}),
        'figure-6-6':('您是否推荐本专业给亲朋好友去就读','专业名称',{'愿意'}),
        'figure-6-8':('就业服务工作的满意程度','学院名称',{'很满意','满意'}),
        'figure-6-13':('学生工作的满意程度','学院名称',{'很满意','满意'}),
    }
    for key,(keyword,group_column,positive) in specs.items():
        if key not in registry.coverage or registry.coverage[key]['status']!='missing':continue
        scope='正在中国境内读研' if key.startswith('figure-3-') and positive else ''
        matches=[q for q in analyzer.questions if q['type']=='study_status'] if keyword=='study_status' else []
        q=matches[0] if len(matches)==1 else find_question(analyzer,keyword,scope)
        if not q:continue
        table=registry._data_table(q['materialId'],q['sheet'])
        if group_column not in table.columns or len(q.get('columns',[]))!=1:continue
        gi=table.columns.index(group_column);ci=table.columns.index(q['columns'][0]);yi=table.columns.index('届次') if '届次' in table.columns else None
        si=next((i for i,c in enumerate(table.columns) if c.startswith('您现在是否有工作')),None)
        groups=defaultdict(list)
        for row in table.rows:
            if yi is not None and row[yi]!=str(registry.year):continue
            if scope and (si is None or row[si]!=scope):continue
            if present(row[gi]) and present(row[ci]):groups[row[gi]].append(row[ci])
        entries=[]
        for label,values in groups.items():
            if len(values)<5:continue
            numerator=sum(v in positive for v in values) if positive else sum(v=='正在中国境内读研' for v in values)
            entries.append((label,percentage(numerator,len(values)),len(values)))
        entries.sort(key=lambda r:-r[1])
        if not entries or len(entries)>100:continue
        basis=f'{registry.year}届；{q["question"]}；'+(scope+'；' if scope else '')+f'各{group_column}该题有效回答为分母，样本少于5不展示；仅当届，未作趋势比较。'
        registry.register_statistics(key,q,[r[0] for r in entries],[{'name':'比例','values':[r[1] for r in entries],'sampleCounts':[r[2] for r in entries]}],basis,0,0)
