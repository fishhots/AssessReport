"""Supplement fixed report topics with cohort-scoped, program-calculated results.

These bindings consume actual DATA columns. They never infer an institution
classification or reinterpret an undocumented numeric scale.
"""
from collections import Counter, defaultdict
import re

from .survey import percentage, present


def column(table, prefix):
    if prefix in table.columns:
        return table.columns.index(prefix)
    matches = [i for i, name in enumerate(table.columns) if name.startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def cohort_rows(registry, table):
    if not table.source_ids or any(registry.evidence.read(k).category != 'DATA' for k in table.source_ids):
        return []
    if '届次' in table.columns:
        i = table.columns.index('届次')
        return [r for r in table.rows if r[i] == str(registry.year)]
    if '合并' not in table.name + table.sheet and set(re.findall(r'20\d{2}', table.name + table.sheet)) == {str(registry.year)}:
        return table.rows
    return []


def register(registry, key, table, rows, columns, basis, series=None, labels=None, kind=None, multiple=False, valid=0, unit='%', details=None, cohorts=None):
    if key not in registry.coverage or registry.coverage[key]['status'] != 'missing' or not rows:
        return
    result = registry.evidence.computed(table, {'cohort': registry.year, 'topic': registry.item(key)['caption'],
        'columns': columns, 'rows': rows, 'basis': basis, 'type': 'report_topic_statistics',**(details or {})})
    registry.register_statistics(key, {'materialId': table.material_id, 'sheet': table.sheet,
        'evidenceId': result['evidenceId']}, labels or [r[0] for r in rows], series or [],
        basis, valid, 0, columns=columns, rows=rows, kind=kind, multiple=multiple, unit=unit, cohorts=cohorts)


def leading_major_labeler(table, rows):
    mi=table.columns.index('一流专业显示名称')
    ai=table.columns.index('学院名称') if '学院名称' in table.columns else None
    pi=table.columns.index('专业名称') if '专业名称' in table.columns else mi
    groups=defaultdict(set)
    for row in rows:
        if present(row[mi]):
            groups[row[mi]].add((row[ai] if ai is not None else '',row[pi]))
    def label(row):
        if not present(row[mi]):return ''
        if len(groups[row[mi]])<=1:return row[mi]
        return ((row[ai]+' / ') if ai is not None else '')+row[pi]
    return label


def destination_population(registry, table, value_column, leading=False):
    """Pool two real adjacent cohorts only when each has one unambiguous source.

    Reference destination tables pool two cohorts, unlike the annual matrices.
    Recount respondents rather than averaging rounded cohort percentages.
    """
    from types import SimpleNamespace
    required=['您现在是否有工作','专业名称',value_column]
    if '学院名称' in table.columns:required.append('学院名称')
    if leading:required.append('一流专业显示名称')
    versions={}
    for year in (registry.year-1,registry.year):
        candidates=[]
        for candidate in registry.evidence.corpus.tables:
            if any(column(candidate,c) is None for c in required):continue
            rows=cohort_rows(SimpleNamespace(year=year,evidence=registry.evidence),candidate)
            if rows:
                # Older questionnaires may have fewer unrelated questions.
                # Align named fields instead of requiring identical sheets.
                mapping=[column(candidate,c) for c in table.columns]
                projected=[[r[i] if i is not None else '' for i in mapping] for r in rows]
                candidates.append((candidate,projected))
        if len(candidates)!=1:return table,cohort_rows(registry,table),[registry.year]
        versions[year]=candidates[0]
    parents=list(dict.fromkeys(k for candidate,_ in versions.values() for k in candidate.source_ids))
    joint=table.model_copy(update={'source_ids':parents})
    return joint,[r for _,rows in versions.values() for r in rows],list(versions)


def plan_report_statistics(registry):
    from .survey_visuals import find_question, POSITIVE, NEGATIVE
    analyzer = registry.evidence.survey
    if not analyzer:
        return
    # These are genuinely additional requirements, not a claim that raw fields
    # were absent. Store them with the specific missing chart for later review.
    explicit_gaps={
        'figure-3-6':'需提供读研院校的权威类型分类对照，不能凭院校名称猜测“双一流”等类别。',
        'figure-3-8':'需提供读研院校层次分类对照后计算高水平院校比例。',
        'figure-4-5':'需明确通用能力达成度的计算规则；现有量表均值不能直接替代达成率。',
        'figure-4-7':'需明确工程类专业范围与毕业要求达成度计算规则。',
    }
    for key in ('figure-4-3','figure-4-4'):
        explicit_gaps[key]='需确认通用能力达成度和重要度的计算口径；可报告已明确量表的自评均值，不能改名为百分比达成度。'
    for key in ('figure-4-6','figure-4-8'):
        explicit_gaps[key]='需确认专业范围和毕业要求/核心能力达成度的计算规则；量表均值与百分比达成度不能替换。'
    explicit_gaps['figure-3-10']='需读研院校类型分类对照，不能凭院校名称推断类型并计算分组满意度。'
    for key in ('table-7-5','table-7-6','table-7-7'):
        explicit_gaps[key]='需明确对应能力达成度的算法和专业范围；现有需求/掌握量表的均值与分布仍可分析。'
    for key in ('table-8-1','table-8-2'):
        explicit_gaps[key]='现有问卷可统计有效样本构成；缺少学院/专业实际毕业生总数，不能推算回收率或代表性。'
    for key,reason in explicit_gaps.items():
        if key in registry.coverage and registry.coverage[key]['status']=='missing':registry.coverage[key]['reason']=reason
    disciplines,classification_sources=declared_disciplines(registry)
    from .linked_statistics import plan_linked_courses
    plan_linked_courses(registry)
    for table in registry.evidence.corpus.tables:
        rows = cohort_rows(registry, table)
        if not rows:
            continue
        plan_moral_increment(registry, table, rows,disciplines,classification_sources)
        plan_club_satisfaction(registry, table, rows)
        major_label=leading_major_labeler(table,rows) if '一流专业显示名称' in table.columns else None
        status = column(table, '您现在是否有工作')
        if status is not None:
            employment = [r for r in rows if r[status].startswith(('受雇', '有全职工作', '自由职业'))]
            location = column(table, '工作单位境内所在地')
            if location is not None:
                from .regional_statistics import plan_regions
                plan_regions(registry, table, employment, location,
                    column(table, '您现在就业的行业类'), column(table, '您现在从事的职业类'),
                    major_label)
            leading = column(table, '您所在单位是否为行业一流企业')
            if leading is not None:
                values = [r[leading] for r in employment if r[leading] in ('是', '否', '不清楚')]
                if values:
                    yes = sum(v == '是' for v in values)
                    register(registry, 'figure-1-13', table,
                        [[label, str(values.count(label)), str(percentage(values.count(label),len(values)))] for label in ('是','否','不清楚')],
                        ['自报回答','人数','比例（%）'],
                        f'{registry.year}届就业样本；行业一流企业为受访者自报，不是权威名单核定；不清楚计入分母。',
                        labels=[str(registry.year)], series=[{'name':'自报行业一流企业就业比例','values':[percentage(yes,len(values))]}],
                        kind='bar', valid=len(values))
                industry = column(table, '您现在就业的行业类')
                if industry is not None:
                    counts = Counter(r[industry] for r in employment if r[leading]=='是' and present(r[industry]))
                    total = sum(counts.values())
                    register(registry, 'figure-1-14', table,
                        [[label,str(n),str(percentage(n,total))] for label,n in counts.most_common()],
                        ['行业类','有效回答人数','比例（%）'],
                        f'{registry.year}届；自报在行业一流企业就业者，以其行业类有效回答为分母。',
                        series=[{'name':'比例','values':[percentage(n,total) for _,n in counts.most_common()]}],valid=total)
            # Each major's employment distribution; a single uploaded cohort is
            # explicitly labelled as such, even when the reference pooled two.
            for key, prefix in [('table-1-7','您现在就业的行业名称'), ('table-1-10','您现在从事的职业名称')]:
                ci = column(table, prefix)
                if ci is None or '专业名称' not in table.columns:
                    continue
                destination_table,destination_rows,destination_years=destination_population(registry,table,prefix)
                destination_employment=[r for r in destination_rows if r[status].startswith(('受雇','有全职工作','自由职业'))]
                destination_basis='、'.join(f'{y}届' for y in destination_years)+('合并' if len(destination_years)>1 else '')
                mi = table.columns.index('专业名称')
                ai = table.columns.index('学院名称') if '学院名称' in table.columns else None
                groups = defaultdict(list)
                for row in destination_employment:
                    if present(row[mi]) and present(row[ci]):
                        groups[(row[ai] if ai is not None else '',row[mi])].append(row[ci])
                output = []
                native=[]
                for (faculty, major), values in sorted(groups.items()):
                    ranked=Counter(values).most_common()
                    for label, count in ranked:
                        output.append([faculty,major,label,str(percentage(count,len(values))),str(len(values))])
                    # All singletons provide no concentrated destination. Keep
                    # their complete distribution in evidence, not a long list
                    # masquerading as the major's main industry/occupation.
                    if len(values)<5 or ranked[0][1]<2:continue
                    leaders=sorted(label for label,n in ranked if n==ranked[0][1])
                    if key=='table-1-10':native.extend([faculty,major,label] for label in leaders)
                    else:native.append([faculty,major,'；'.join(leaders)])
                register(registry,key,destination_table,native,['学院名称','专业名称','本校该专业毕业生就业的主要行业' if '行业' in prefix else '本校该专业毕业生从事的主要职业'],
                    f'{destination_basis}就业样本；使用具体行业/职业名称，列各专业人数最多的项目，并列时并列呈现；有效回答少于5人或所有去向均只有1条记录时不列主要去向，完整分布保留内部。',
                    details={'detailColumns':['学院','专业','行业或职业','比例（%）','有效回答人数'],'detailRows':output},cohorts=destination_years)
            unemployed = [r for r in rows if r[status].startswith(('无工作','准备'))]
            counts = Counter(r[status] for r in unemployed)
            total = sum(counts.values())
            output = [[label,str(n),str(percentage(n,total))] for label,n in counts.most_common()]
            register(registry,'figure-1-5',table,output,['去向','人数','比例（%）'],
                f'{registry.year}届毕业状态为无工作或准备考研、留学者；分母为该群体有效回答人数。',
                series=[{'name':'比例','values':[percentage(n,total) for _,n in counts.most_common()]}],valid=total)
            specs = [('figure-1-6','您是否参加过研究生考试',lambda r:r[status]=='准备中国境内考研',lambda v:v=='是'),
                     ('figure-1-8','您在求职过程中一共收到多少份工作录用通知',lambda r:r[status].startswith('无工作，继续寻找工作'),lambda v:float(v)>0)]
            for key,prefix,predicate,positive in specs:
                ci = column(table,prefix)
                if ci is None:
                    continue
                values = [r[ci] for r in rows if predicate(r) and present(r[ci])]
                try:
                    if key=='figure-1-6' and any(v not in ('是','否') for v in values): continue
                    if key=='figure-1-8' and any(not re.fullmatch(r'\d+(?:\.\d+)?',v) for v in values): continue
                    numerator=sum(positive(v) for v in values)
                except ValueError:
                    continue
                if not values: continue
                value=percentage(numerator,len(values))
                register(registry,key,table,[['是',str(numerator),str(value)],['否',str(len(values)-numerator),str(percentage(len(values)-numerator,len(values)))]],
                    ['回答','人数','比例（%）'],f'{registry.year}届；仅准备考研者' if key=='figure-1-6' else f'{registry.year}届；仅无工作且继续寻找工作者',
                    series=[{'name':'比例','values':[value,percentage(len(values)-numerator,len(values))]}],labels=['是','否'],valid=len(values))
            for key,prefix in [('figure-2-10','您现在从事的职业类'),('figure-2-11','您现在就业的行业类')]:
                ci=column(table,prefix)
                income=[i for i,c in enumerate(table.columns) if '月收入' in c and not any(t in c for t in ('期待','满意','原因'))]
                if ci is None or len(income)!=1:continue
                groups=defaultdict(list)
                for row in employment:
                    if present(row[ci]) and re.fullmatch(r'\d+(?:\.\d+)?',row[income[0]]):groups[row[ci]].append(float(row[income[0]]))
                output=[[label,str(round(sum(v)/len(v),2)),str(len(v))] for label,v in groups.items() if len(v)>=5]
                output.sort(key=lambda r:-float(r[1]))
                register(registry,key,table,output,['类别','月收入（元）','有效回答人数'],
                    f'{registry.year}届就业样本；各行业或职业有效月收入回答的算术均值；有效回答少于5人不展示。',
                    series=[{'name':'月收入','values':[float(r[1]) for r in output],'sampleCounts':[int(r[2]) for r in output]}],kind='horizontal_bar',unit='元')
            if '一流专业显示名称' in table.columns:
                mi=table.columns.index('一流专业显示名称')
                for key,prefix in [('table-7-13','您现在就业的行业名称'),('table-7-14','您现在从事的职业名称')]:
                    ci=column(table,prefix)
                    if ci is None:continue
                    destination_table,destination_rows,destination_years=destination_population(registry,table,prefix,leading=True)
                    destination_employment=[r for r in destination_rows if r[status].startswith(('受雇','有全职工作','自由职业'))]
                    destination_major=leading_major_labeler(table,destination_rows)
                    destination_basis='、'.join(f'{y}届' for y in destination_years)+('合并' if len(destination_years)>1 else '')
                    groups=defaultdict(list)
                    for row in destination_employment:
                        if present(row[mi]) and present(row[ci]):groups[destination_major(row)].append(row[ci])
                    output=[];details=[]
                    for major,values in sorted(groups.items()):
                        ranked=Counter(values).most_common()
                        details.extend([major,label,str(percentage(n,len(values))),str(len(values))] for label,n in ranked)
                        if len(values)<5 or ranked[0][1]<2:continue
                        for label,n in ranked:
                            if n!=ranked[0][1]:break
                            output.append([major,label,str(percentage(n,len(values))),str(len(values))])
                    register(registry,key,destination_table,output,['专业','主要行业' if '行业' in prefix else '主要职业','比例（%）','有效回答人数'],
                        f'{destination_basis}就业样本；使用具体行业/职业名称，按资料明确标注的一流专业分组，列人数最多的项目，并列时并列呈现；有效回答少于5人或所有去向均只有1条记录时不列主要去向。',
                        cohorts=destination_years,details={'detailRows':details,'detailColumns':['专业名称','行业或职业名称','比例（%）','有效回答人数']})
        # Course records have declared binary outcome codes, and the importance
        # code is accepted only when its textual meanings agree row by row.
        if '课程名称' in table.columns:
            outcome=column(table,'课程是否满足工作或学习要求（1表示满足，0表示不满足）')
            importance=column(table,'课程的重要度')
            importance_text=column(table,'课程的重要性')
            if outcome is not None:
                if '一流专业显示名称' in table.columns:
                    mi=table.columns.index('一流专业显示名称');ci=table.columns.index('课程名称')
                    groups=defaultdict(list)
                    for row in rows:
                        if present(row[mi]) and present(row[ci]) and row[outcome] in ('0','1'):
                            groups[(major_label(row),row[ci])].append(row[outcome])
                    majors=defaultdict(list)
                    for (major,course),values in groups.items():
                        if len(values)>=5:
                            majors[major].append((course,percentage(values.count('1'),len(values)),len(values)))
                    output=[];detail=[]
                    for major,values in sorted(majors.items()):
                        ranked=sorted(values,key=lambda v:(-v[1],v[0]))
                        count=max(1,min(2,len(values)//2))
                        higher=ranked[:count]
                        lower=sorted((v for v in values if v[1] < higher[-1][1]),key=lambda v:(v[1],v[0]))[:count]
                        describe=lambda selected:'；'.join(f'{course}（{rate:g}%）' for course,rate,count in selected) or '—'
                        output.append([major,describe(higher),describe(lower)])
                        detail.extend([[major,course,str(rate),str(count)] for course,rate,count in ranked])
                    if output and 'table-7-9' in registry.coverage:
                        evidence=registry.evidence.computed(table,{'cohort':registry.year,'type':'course_task_details',
                            'columns':['专业','课程满足比例相对较高的项目','课程满足比例相对较低的项目'],'rows':output,
                            'detailColumns':['专业','课程','满足要求比例（%）','有效课程评价记录数'],'detailRows':detail,
                            'basis':'按课程独立统计的评价记录；不能替代表7-9两类对口人群的重要度和满足度。'})
                        item=registry.item('table-7-9')
                        registry.evidence.topic_sources.setdefault(item['chapter'],{}).setdefault(item['subsection'],[]).append(evidence['evidenceId'])
                        if registry.coverage['table-7-9']['status']=='missing':
                            registry.coverage['table-7-9']['reason']='需匿名关联及明确毕业状态，分别筛选对口就业/对口读研与对口就业人群。'
                for group in ('学院名称','课程名称'):
                    if group not in table.columns:continue
                    gi=table.columns.index(group);groups=defaultdict(list)
                    for row in rows:
                        if present(row[gi]):groups[row[gi]].append(row)
                    output=[]
                    for label,group_rows in sorted(groups.items()):
                        values=[r[outcome] for r in group_rows if r[outcome] in ('0','1')]
                        if len(values)<5:continue
                        good=sum(v=='1' for v in values)
                        item=[label,str(percentage(good,len(values))),str(len(values))]
                        if importance is not None and importance_text is not None:
                            iv=[r[importance] for r in group_rows if r[importance] in ('0','1') and present(r[importance_text])]
                            consistent=all((r[importance]=='0')==(r[importance_text]=='不重要') for r in group_rows if r[importance] in ('0','1') and present(r[importance_text]))
                            if not consistent:iv=[]
                            item.extend([str(percentage(sum(v=='1' for v in iv),len(iv))) if iv else '—',str(len(iv))])
                        output.append(item)
                    if group=='学院名称':
                        cols=['学院','课程满足工作或学习要求比例（%）','有效课程评价记录数']
                        if importance is not None and importance_text is not None:cols+=['认为课程有重要性的比例（%）','有效重要性评价记录数']
                        if 'table-5-9' in registry.coverage and registry.coverage['table-5-9']['status']=='missing':
                            registry.coverage['table-5-9']['reason']='已有课程评价记录；需匿名关联及明确毕业状态，才能分别计算两类对口人群的课程重要度与满足度。'
                    else:
                        result=registry.evidence.computed(table,{'cohort':registry.year,'topicPath':'专业核心课程评价 / 专业课程培养效果',
                            'type':'course_results','columns':['课程','满足要求比例（%）','有效课程评价记录数'],
                            'rows':[r[:3] for r in output],'basis':'按课程分别计算有效课程评价记录中的满足比例；记录数不是人数。'})
                        registry.evidence.topic_sources.setdefault('第五章 教学培养反馈',{}).setdefault('专业核心课程评价 / 专业课程培养效果',[]).append(result['evidenceId'])
                        registry.evidence.topic_sources.setdefault('第五章 教学培养反馈',{}).setdefault('专业核心课程评价 / 核心课程重要度及培养效果',[]).append(result['evidenceId'])
        # Known textual career-ability answers can be summarized without inventing
        # the separate engineering/general ability achievement formulas.
        if '职业能力名称' in table.columns and '学院名称' in table.columns:
            plan_ability_details(registry,table,rows)
            ci=column(table,'职业能力是否满足目前的工作需要')
            if ci is None:
                ci=table.columns.index('职业能力是否满足目前的工作需要') if '职业能力是否满足目前的工作需要' in table.columns else None
            if ci is not None:
                groups=defaultdict(list);gi=table.columns.index('学院名称')
                for row in rows:
                    if present(row[gi]) and row[ci] in ('满足','不满足'):groups[row[gi]].append(row[ci])
                output=[[label,str(percentage(sum(v=='满足' for v in values),len(values))),str(len(values))] for label,values in sorted(groups.items()) if len(values)>=5]
                result=registry.evidence.computed(table,{'cohort':registry.year,'type':'career_ability_records',
                    'topicPath':'职业能力 / 各学院职业能力达成度','columns':['学院','职业能力满足工作需要比例（%）','有效能力评价记录数'],
                    'rows':output,'basis':'按职业能力评价记录计算满足比例，记录数不是毕业生人数；不冒称另有定义的综合达成度。'})
                if output:registry.evidence.topic_sources.setdefault('第四章 五育成效',{}).setdefault('职业能力 / 各学院职业能力达成度',[]).append(result['evidenceId'])
                values=[r[ci] for r in rows if r[ci] in ('满足','不满足')]
                basis=f'{registry.year}届职业能力评价记录；以有效能力评价记录计算满足工作需要的比例，记录数不是毕业生人数；不替代其他未明确算法的能力综合达成度。'
                if values:
                    value=percentage(values.count('满足'),len(values))
                    register(registry,'figure-4-9',table,[[str(registry.year),str(value),str(len(values))]],
                        ['届次','职业能力满足工作需要比例（%）','有效能力评价记录数'],basis,
                        labels=[str(registry.year)],series=[{'name':'职业能力满足工作需要比例','values':[value]}],kind='bar',valid=len(values))
                register(registry,'figure-4-10',table,output,['学院','职业能力满足工作需要比例（%）','有效能力评价记录数'],
                    basis+'各学院有效评价记录少于5条不展示。',
                    series=[{'name':'职业能力满足工作需要比例','values':[float(r[1]) for r in output],'sampleCounts':[int(r[2]) for r in output]}],kind='horizontal_bar')
    plan_leading_major_metrics(registry)
    plan_service_effectiveness(registry)
    # Grouped questionnaire tables preserve each question's denominator, including
    # complete 0/1 multi-hot rows and per-subquestion positive-answer rates.
    specs={
        'table-5-1': [('的教学满意程度','学院名称'),('专业教学中应该改进','学院名称')],
        'table-5-2': [('您对以下实习和实践环节是否满意','专业大类名称')],
        'table-5-3': [('请您对校内实习实践进行评价','学院名称'),('请您对校外实习实践进行评价','学院名称')],
        'table-5-4': [('您在工作中使用AI工具/技术的主要应用场景','您现在从事的职业一级分类')],
        'table-5-6': [('您认为AI时代需强化的能力和素养','学院名称')],
        'table-5-7': [('您认为学校在AI培养方面需要加强','学院名称')],
        'table-5-8': [('请评价您在读期间的专业课任课教师的教学方法','学院名称'),('本专业的教师提供的以下课外指导','学院名称')],
        'table-5-11': [('请您对学习过程中使用的教材情况进行评价','学院名称')],
        'table-6-1': [('数字化建设情况进行评价','学院名称')],
        'table-7-8': [('的教学满意程度','一流专业显示名称')],
        'table-7-10': [('请评价您在读期间的专业课任课教师的教学方法','一流专业显示名称'),('本专业的教师提供的以下课外指导','一流专业显示名称')],
        'table-7-11': [('请您对校内实习实践进行评价','一流专业显示名称'),('请您对校外实习实践进行评价','一流专业显示名称')],
    }
    for key,questions in specs.items():
        if key not in registry.coverage:continue
        output=[];table=None;multiple=False
        for keyword,group_column in questions:
            q=find_question(analyzer,keyword,'')
            if not q:continue
            candidate=registry.evidence.table(q['materialId'],q['sheet'])
            if table is not None and (candidate.material_id,candidate.sheet)!=(table.material_id,table.sheet):
                continue
            table=candidate;rows=cohort_rows(registry,table)
            if group_column not in table.columns:continue
            gi=table.columns.index(group_column);indices=[table.columns.index(c) for c in q['columns']]
            groups=defaultdict(list)
            major_label=leading_major_labeler(table,rows) if group_column=='一流专业显示名称' else None
            for row in rows:
                if present(row[gi]):groups[major_label(row) if major_label else row[gi]].append(row)
            for label,group_rows in [('本校平均',rows),*sorted(groups.items())]:
                if q['type']=='multiple':
                    valid=[r for r in group_rows if all(r[i] in ('0','1','0.0','1.0') for i in indices)]
                    if len(valid)<5:continue
                    multiple=True
                    ranked=sorted([(q['items'][j]['label'],sum(float(r[i])==1 for r in valid)) for j,i in enumerate(indices)],key=lambda p:(-p[1],p[0]))
                    output.extend([[label,option,str(percentage(count,len(valid))),str(len(valid))] for option,count in ranked])
                elif q['type'] in ('single','matrix'):
                    for j,i in enumerate(indices):
                        values=[r[i] for r in group_rows if present(r[i])]
                        if key=='table-5-2':
                            # 未参加 is outside the satisfaction population,
                            # rather than an unsatisfied response.
                            values=[v for v in values if v in POSITIVE | NEGATIVE]
                        if len(values)<5 or not any(v in POSITIVE | NEGATIVE for v in values):continue
                        item=q['question'] if q['type']=='single' else q['items'][j]['label']
                        output.append([label,item,str(percentage(sum(v in POSITIVE for v in values),len(values))),str(len(values))])
        if table is not None:
            if key in ('table-5-8','table-7-10'):
                extra,parents=teacher_comparison_rows(registry,table,rows,group_column,output)
                output.extend(extra)
                table=table.model_copy(update={'source_ids':list(dict.fromkeys(table.source_ids+parents))})
            group_name='学院' if all(c=='学院名称' for _,c in questions) else '专业' if all(c=='一流专业显示名称' for _,c in questions) else '学科门类' if all(c=='专业大类名称' for _,c in questions) else '职业类'
            register(registry,key,table,output,[group_name,'评价项目或主要需求','比例（%）','有效回答人数'],
                f'{registry.year}届；本校平均及各组各题有效回答分别为分母；多选保留全部选项，评价项为正向回答比例；有效回答少于5人不展示。'+
                ('实习实践环节满意度仅统计实际参加并回答满意度者，“未参加”不计入分母；不同实验环节保持独立。' if key=='table-5-2' else '')+
                ('教师指导总体满足度需明确原始指标或计算规则，不能用三个分项比例的均值替代；高频交流为每月至少一次（含每周至少一次）。' if key in ('table-5-8','table-7-10') else ''),multiple=multiple)
    # The upload measures frequency per work task, rather than a single overall
    # frequency question. Preserve task denominators; never average all answers
    # into a graduate-level usage rate.
    q=find_question(analyzer,'结合您所从事的职业，请评价在以下工作任务中使用AI技术/工具的频率','')
    if q and q['type']=='matrix':
        table=registry.evidence.table(q['materialId'],q['sheet'])
        entries=[r for r in q['items'] if r['validCount']>=5]
        choices=list(dict.fromkeys(c['label'] for r in entries for c in r['items']))
        if entries and 2<=len(choices)<=8:
            # 汇总所有工作任务的频率回答，得到总体频率分布（按任务-回答计），
            # 不再逐任务列举；4 根柱子分别代表 总是/经常/偶尔/从不。
            grand={c:0.0 for c in choices}; total=0
            for r in entries:
                n=r['validCount']; total+=n
                for c in r['items']:
                    if c['label'] in grand:
                        grand[c['label']]+=n*(c.get('percentage',0)/100.0)
            values=[round(grand[c]/total*100,2) if total else 0 for c in choices]
            register(registry,'figure-5-15',table,[[c] for c in choices],['频率'],
                f'{registry.year}届；汇总各工作任务回答，按任务-回答统计各频率选项占比，不区分具体任务；有效回答少于5人的任务不计入。',
                series=[{'name':'AI应用频率','values':values}], labels=list(choices), kind='bar')
        gi=column(table,'您现在从事的职业一级分类')
        if gi is None:gi=column(table,'您现在从事的职业类')
        if gi is not None:
            task_counts=[]
            for j,c in enumerate(q['columns']):
                ci=table.columns.index(c);groups=defaultdict(list)
                for row in cohort_rows(registry,table):
                    if present(row[gi]) and present(row[ci]):groups[row[gi]].append(row[ci])
                for group,values in sorted(groups.items()):
                    if len(values)<5 or not set(values)<={'总是','经常','偶尔','从不'}:continue
                    counts=Counter(values)
                    task_counts.append({'group':group,'task':q['items'][j]['label'],
                                        'validCount':len(values),'counts':dict(counts)})
            # 直接展开为四列频率百分比，去掉有效回答人数；每个任务一行。
            freq_order=['总是','经常','偶尔','从不']; rows=[]
            for tc in sorted(task_counts,key=lambda t:(t['group'],-(t['counts'].get('总是',0)+t['counts'].get('经常',0))/t['validCount'],t['task'])):
                n=tc['validCount']; counts=tc['counts']
                rows.append([tc['group'],tc['task'],*(str(percentage(counts.get(f,0),n)) for f in freq_order)])
            register(registry,'table-5-5',table,rows,
                ['职业分类','主要工作任务','总是（%）','经常（%）','偶尔（%）','从不（%）'],
                f'{registry.year}届；按资料的{table.columns[gi]}及工作任务分别统计各频率选项占比（总是/经常/偶尔/从不），分母为该任务有效回答人数；有效回答少于5人或频率编码不明时不展示。',
                details={'taskCounts':task_counts,'groupingColumn':table.columns[gi]})


def declared_disciplines(registry):
    """Use uploaded classifications; same-name majors are faculty-qualified."""
    members=defaultdict(set);sources=[]
    for table in registry.evidence.corpus.tables:
        if not all(c in table.columns for c in ('学院名称','专业名称','专业大类名称')):
            continue
        ai,mi,di=(table.columns.index(c) for c in ('学院名称','专业名称','专业大类名称'))
        li=table.columns.index('学历层次') if '学历层次' in table.columns else None
        rows=cohort_rows(registry,table)
        mappings={(r[ai],r[mi],r[di]) for r in rows if present(r[ai]) and present(r[mi]) and present(r[di]) and (li is None or r[li]=='本科')}
        if mappings:
            result=registry.evidence.computed(table,{'cohort':registry.year,'type':'declared_discipline_members',
                'columns':['学院','专业','学科门类'],'rows':[list(r) for r in sorted(mappings)],
                'basis':'上传资料的专业大类名称；本科当届；学院和专业联合匹配，不凭名称推断学科归属。'})
            sources.append(result['evidenceId'])
            for faculty,major,discipline in mappings:members[faculty,major].add(discipline)
    return {key:next(iter(values)) for key,values in members.items() if len(values)==1},sources


def plan_ability_details(registry,table,rows):
    ci=column(table,'职业能力是否满足目前的工作需要')
    if ci is None:return
    for group in ('职业能力名称','职业类名称','职业名称','专业名称'):
        gi=column(table,group)
        if gi is None:continue
        ai=column(table,'学院名称');groups=defaultdict(list)
        for row in rows:
            if row[ci] not in ('满足','不满足') or not present(row[gi]):continue
            label=(row[ai]+' / '+row[gi]) if group=='专业名称' and ai is not None else row[gi]
            groups[label].append(row[ci])
        output=[[label,str(percentage(values.count('满足'),len(values))),str(len(values))]
                for label,values in sorted(groups.items()) if len(values)>=5]
        if not output:continue
        result=registry.evidence.computed(table,{'cohort':registry.year,'type':'career_ability_detail',
            'groupBy':group,'columns':[group,'职业能力满足工作需要比例（%）','有效能力评价记录数'],
            'rows':output,'basis':'按能力评价记录分别统计，少于5条不展示；记录数不是人数，不冒称未定义的综合达成度。'})
        for path in ('职业能力 / 职业能力达成度','职业能力 / 各学院职业能力达成度'):
            registry.evidence.topic_sources.setdefault('第四章 五育成效',{}).setdefault(path,[]).append(result['evidenceId'])


def plan_service_effectiveness(registry):
    for table in registry.evidence.corpus.tables:
        rows=cohort_rows(registry,table)
        if not rows:continue
        output=[]
        for i,name in enumerate(table.columns):
            if not name.endswith('是否有效') or not any(w in name for w in ('就业','求职','招聘','职业生涯','薪资')):continue
            values=[r[i] for r in rows if r[i] in ('是','否')]
            if len(values)<5:continue
            output.append([name.removesuffix('是否有效'),str(percentage(values.count('是'),len(values))),str(len(values))])
        register(registry,'figure-6-10',table,output,['服务类型','有效性（%）','有效回答人数'],
            f'{registry.year}届；各项服务是否有效题的有效作答分别为分母；不是服务参与率；有效回答少于5人不展示。',
            series=[{'name':'有效性','values':[float(r[1]) for r in output],'sampleCounts':[int(r[2]) for r in output]}],kind='horizontal_bar')


def plan_moral_increment(registry, table, rows, disciplines=None,classification_sources=None):
    """Any explicitly selected improvement vs mutually exclusive 'none'."""
    indices=[i for i,c in enumerate(table.columns) if c.startswith('大学帮助您在哪些素养方面有明显的提升（')]
    none=[i for i in indices if table.columns[i].endswith('（没有任何提升）')]
    if len(indices)<2 or len(none)!=1:
        return
    valid=[r for r in rows if all(r[i] in ('0','1','0.0','1.0') for i in indices)]
    if not valid:
        return
    ni=none[0];positive=[i for i in indices if i!=ni]
    # Empty multi-hot rows and contradictory none+positive responses cannot be
    # assumed to be valid negative answers; fail closed rather than guess.
    if any(not any(float(r[i]) for i in indices) or (float(r[ni]) and any(float(r[i]) for i in positive)) for r in valid):
        if 'figure-4-1' in registry.coverage and registry.coverage['figure-4-1']['status']=='missing':
            registry.coverage['figure-4-1']['reason']='素养题存在全零或“没有提升”与正向选项同时选中的回答，需确认有效作答处理口径。'
        return
    good=sum(any(float(r[i])==1 for i in positive) for r in valid)
    value=percentage(good,len(valid))
    register(registry,'figure-4-1',table,[['至少一项素养有明显提升',str(good),str(value)]],
        ['素养增值','有效回答人数','比例（%）'],
        f'{registry.year}届；完整0/1作答且“没有任何提升”与正向项互斥；分子为至少选择一项素养提升的有效回答，分母为该题完整有效作答人数。',
        labels=[str(registry.year)],series=[{'name':'素养增值比例','values':[value]}],kind='bar',valid=len(valid))
    if not disciplines or not all(c in table.columns for c in ('学院名称','专业名称')):
        return
    ai,mi=(table.columns.index(c) for c in ('学院名称','专业名称'))
    groups=defaultdict(list)
    for row in valid:
        if label:=disciplines.get((row[ai],row[mi])):groups[label].append(row)
    groups={label:values for label,values in sorted(groups.items()) if len(values)>=5}
    if not groups:
        return
    joint=table.model_copy(update={'source_ids':list(dict.fromkeys([*table.source_ids,*(classification_sources or [])]))})
    basis=f'{registry.year}届；按上传资料明确学科分类关联学院及专业；各门类素养题完整有效回答为分母，有效回答少于5人不展示；未匹配学科的回答不纳入分组。'
    output=[[label,str(percentage(sum(any(float(r[i])==1 for i in positive) for r in values),len(values)))] for label,values in groups.items()]
    counts={label:len(values) for label,values in groups.items()}
    register(registry,'table-4-1',joint,output,['学科门类',f'{registry.year}届素养增值比例（%）'],basis,
        details={'validCounts':counts,'unclassifiedCount':sum((r[ai],r[mi]) not in disciplines for r in valid)})
    pairs=[]
    for label,values in groups.items():
        ranked=[(table.columns[i].split('（',1)[1].removesuffix('）'),percentage(sum(float(r[i])==1 for r in values),len(values))) for i in positive]
        pairs.append((label,sorted(ranked,key=lambda r:(-r[1],r[0]))))
    # Paired option/rate columns mirror the original discipline comparison;
    # each group has independently sorted items and its own denominator.
    columns=[c for label,_ in pairs for c in ('选项',label)]
    output=[[cell for _,ranked in pairs for cell in (ranked[i][0],f'{ranked[i][1]:.2f}')] for i in range(len(positive))]
    register(registry,'table-4-2',joint,output,columns,basis+'各素养选项为多选比例，不求和。',multiple=True,
        details={'validCounts':counts})


def plan_club_satisfaction(registry, table, rows):
    """Each club type has its own valid satisfaction answers/denominator."""
    output=[]
    for i,name in enumerate(table.columns):
        match=re.fullmatch(r'您对(.+社团(?:（[^）]*）)?)是否满意',name)
        if not match:
            continue
        values=[r[i] for r in rows if present(r[i])]
        choices={'是','否','很满意','满意','非常满意','一般','不满意','很不满意','非常不满意'}
        if len(values)<5 or any(v not in choices for v in values):
            continue
        value=percentage(sum(v in {'是','很满意','满意','非常满意'} for v in values),len(values))
        output.append([match[1],str(value),str(len(values))])
    register(registry,'figure-4-14',table,output,['社团类型','满意度（%）','有效回答人数'],
        f'{registry.year}届；分别以各类社团满意度题的有效回答为分母；“是否满意”的是为正向，或采用很满意、满意、非常满意；有效回答少于5人不展示，不合成为全体毕业生总体社团满意度。',
        series=[{'name':'满意度','values':[float(r[1]) for r in output],'sampleCounts':[int(r[2]) for r in output]}],kind='horizontal_bar')


def plan_leading_major_metrics(registry):
    """Join declared membership to the actual cohort's major metric table."""
    pairs = set()
    parents = []
    display_names=defaultdict(set)
    for table in registry.evidence.corpus.tables:
        if not all(c in table.columns for c in ('学院名称','专业名称','一流专业显示名称')):
            continue
        rows = cohort_rows(registry, table)
        ai, mi, fi = (table.columns.index(c) for c in ('学院名称','专业名称','一流专业显示名称'))
        chosen = {(r[ai],r[mi]) for r in rows if present(r[ai]) and present(r[mi]) and present(r[fi])}
        if chosen:
            pairs.update(chosen)
            for row in rows:
                if (row[ai],row[mi]) in chosen and present(row[fi]):display_names[row[ai],row[mi]].add(row[fi])
            result = registry.evidence.computed(table, {'type':'declared_leading_major_membership',
                'cohort':registry.year, 'columns':['学院','专业'], 'rows':[list(p) for p in sorted(chosen)],
                'basis':'来自资料明确标注的一流专业显示名称；不自动推断国家级或省级认定层次。'})
            parents.append(result['evidenceId'])
    if not pairs:
        return
    specs = [('table-7-1','毕业去向落实率（%）'),('table-7-2','专业相关度（%）'),
             ('table-7-3','月收入（元）'),('table-7-4','就业满意度（%）'),
             ('table-7-5','通用能力达成度（%）'),('table-7-6','工程类专业毕业要求达成度（%）'),
             ('table-7-7','工科类专业核心能力达成度（%）')]
    for key, metric in specs:
        if key not in registry.coverage or registry.coverage[key]['status'] != 'missing':
            continue
        matches = [t for t in registry.evidence.corpus.tables if all(c in t.columns for c in ('学院名称','专业名称',metric))
                   and '一流专业显示名称' not in t.columns and cohort_rows(registry,t)]
        if len(matches) != 1:
            continue
        table=matches[0]; ai,mi,vi=(table.columns.index(c) for c in ('学院名称','专业名称',metric))
        rows=[r for r in cohort_rows(registry,table) if (r[ai],r[mi]) in pairs]
        keys=[(r[ai],r[mi]) for r in rows]
        if len(keys) != len(set(keys)):
            registry.coverage[key]['reason']='同一学院专业存在重复指标行，需明确采用口径。'
            continue
        names={pair:next(iter(values)) if len(values)==1 else pair[1] for pair,values in display_names.items()}
        collisions=Counter(names.values())
        output=[]
        for r in rows:
            name=names.get((r[ai],r[mi]),r[mi])
            if collisions[name]>1:name=f'{r[mi]}（{r[ai]}）'
            output.append([name,r[vi] or '—'])
        average_sources=[];averages=set()
        for candidate in registry.evidence.corpus.tables:
            if metric not in candidate.columns:continue
            ci=candidate.columns.index(metric)
            for r in cohort_rows(registry,candidate):
                if any(r[candidate.columns.index(c)]=='本校平均' for c in ('学院名称','专业名称') if c in candidate.columns) and present(r[ci]):
                    averages.add(r[ci]);average_sources+=candidate.source_ids
        if len(averages)==1:output.insert(0,['本校平均',next(iter(averages))])
        joint=table.model_copy(update={'source_ids':list(dict.fromkeys(table.source_ids+average_sources))})
        register(registry,key,joint,output,['专业名称',metric],
            f'{registry.year}届专业主要指标原值；以资料标注的一流专业成员和学院、专业联合匹配；采用明确当届值，不平均不同专业。',
            details={'membershipEvidenceIds':parents,'schoolAverageSourceIds':list(dict.fromkeys(average_sources))},
            unit='元' if '元' in metric else '%')
        if registry.coverage[key]['status']=='prepared':
            data=registry.datasets[registry.coverage[key]['datasetId']]
            source=registry.evidence.read(data['binding']['questionEvidenceId'])
            source.parent_ids=list(dict.fromkeys([*source.parent_ids,*parents]))


def teacher_comparison_rows(registry,table,rows,group_column,existing):
    """Reference teacher indicators retain their separate response denominators."""
    from .quantitative_review import parsed_number
    if group_column not in table.columns:return [],[]
    output=[];parents=[];aggregates=defaultdict(set)
    labels={r[0] for r in existing}
    labeler=leading_major_labeler(table,rows) if group_column=='一流专业显示名称' else None
    ai=column(table,'学院名称');mi=column(table,'专业名称')
    membership={(r[ai],r[mi]):labeler(r) for r in rows if labeler and ai is not None and mi is not None and labeler(r)}
    for candidate in registry.evidence.corpus.tables:
        vi=column(candidate,'教师指导总体满足度（%）')
        if vi is None:continue
        ca,cm=column(candidate,'学院名称'),column(candidate,'专业名称')
        if ca is None:continue
        if not labeler and cm is not None:continue
        for row in cohort_rows(registry,candidate):
            label='本校平均' if row[ca]=='本校平均' else membership.get((row[ca],row[cm]),'') if labeler and cm is not None else row[ca] if not labeler else ''
            value=parsed_number(row[vi])
            if label in labels and value is not None and 0<=value<=100:
                aggregates[label].add(value);parents+=candidate.source_ids
    for label,values in aggregates.items():
        if len(values)==1:output.append([label,'教师指导总体满足度',str(next(iter(values))),'—'])
    fi=column(table,'您与任课老师课下交流的频度');gi=column(table,group_column)
    if fi is None or gi is None:return output,list(dict.fromkeys(parents))
    groups=defaultdict(list);groups['本校平均']=rows
    for row in rows:
        if present(row[gi]):groups[labeler(row) if labeler else row[gi]].append(row)
    known={'每周至少一次','每月至少一次','每学期至少一次','每年至少一次'}
    for label,group in groups.items():
        values=[r[fi] for r in group if present(r[fi])]
        if len(values)>=5 and set(values)<=known:
            output.append([label,'师生高频交流的比例',str(percentage(sum(v in {'每周至少一次','每月至少一次'} for v in values),len(values))),str(len(values))])
    return output,list(dict.fromkeys(parents))
