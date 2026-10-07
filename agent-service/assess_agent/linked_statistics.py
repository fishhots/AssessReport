"""Cohort-scoped joins of questionnaire and course evaluation records."""
from collections import defaultdict

from .report_statistics import column, cohort_rows, register
from .survey import percentage, present


COURSE_PATHS = ('专业核心课程评价 / 核心课程重要度及培养效果',
                '专业核心课程评价 / 各学院核心课程重要度及培养效果',
                '专业核心课程评价 / 专业课程培养效果')


def course_scope_members(registry, material_id):
    candidates = [t for t in registry.evidence.corpus.tables if t.material_id == material_id
                  and column(t, '您现在是否有工作') is not None and len(t.record_keys) == len(t.rows)]
    if len(candidates) != 1:
        return None, {}, 0
    table = candidates[0]
    si = column(table, '您现在是否有工作')
    yi = column(table, '届次')
    consistent = [i for i,c in enumerate(table.columns) if c.startswith('您硕士研究生专业与本科专业是否一致')]
    members = defaultdict(list)
    for key,row in zip(table.record_keys,table.rows):
        if not key or yi is None or row[yi] != str(registry.year):
            continue
        # All duplicate respondents are excluded, including identical copies.
        # Otherwise a one-to-many join could weight a record more than once.
        scope = 'employment' if row[si] == '受雇全职工作，与专业有关' else ''
        answers = {row[i] for i in consistent if row[i] in ('是','否')}
        if row[si] == '正在中国境内读研' and answers == {'是'}:
            scope = 'study'
        members[key].append(scope)
    duplicates = sum(len(v)>1 for v in members.values())
    return table, {k:v[0] for k,v in members.items() if len(v)==1}, duplicates


def course_rates(table, rows):
    oi = column(table,'课程是否满足工作或学习要求（1表示满足，0表示不满足）')
    ii,ti = column(table,'课程的重要度'),column(table,'课程的重要性')
    if oi is None or ii is None or ti is None:
        return None
    meanings = {'不重要':'0','有些重要':'1','重要':'1','非常重要':'1','极其重要':'1'}
    explicit = [r for r in rows if r[ii] in ('0','1') and r[ti] in meanings]
    if not explicit or any(meanings[r[ti]] != r[ii] for r in explicit):
        return None
    importance = percentage(sum(r[ii]=='1' for r in explicit),len(explicit))
    outcome = [r for r in explicit if r[ii]=='1' and r[oi] in ('0','1')]
    return importance, percentage(sum(r[oi]=='1' for r in outcome),len(outcome)),len(explicit),len(outcome)


def plan_linked_courses(registry):
    for table in registry.evidence.corpus.tables:
        if '课程名称' not in table.columns or not cohort_rows(registry,table):
            continue
        primary,members,duplicates = course_scope_members(registry,table.material_id)
        keys = ['figure-5-'+str(n) for n in range(21,28)]
        if primary is None or len(table.record_keys)!=len(table.rows):
            for key in keys:
                if key in registry.coverage:
                    registry.coverage[key]['reason']='需课程评价与当届问卷的匿名关联键，才能筛选对口就业/实际对口读研人群；已有课程记录仍单独统计。'
            continue
        yi = column(table,'届次')
        joined = defaultdict(list)
        unmatched=0
        for key,row in zip(table.record_keys,table.rows):
            if yi is None or row[yi]!=str(registry.year):continue
            if key not in members:
                unmatched+=1
            elif members[key]:
                joined[members[key]].append(row)
        both = joined['employment']+joined['study']
        joint = table.model_copy(update={'source_ids':list(dict.fromkeys(table.source_ids+primary.source_ids))})
        basis = (f'{registry.year}届；同一工作簿匿名键一对多关联；对口就业仅受雇全职且与专业有关，'
                 '对口读研仅实际境内读研且明确专业一致；重要度为“有些重要”至“极其重要”的记录比例，'
                 '培养效果以认为课程重要且有效回答满足题的记录为分母；评价记录数不是毕业生人数。')
        details={'duplicateRespondentKeys':duplicates,'unmatchedCourseRecords':unmatched}
        plan_scoped_course_tables(registry,joint,both,joined['employment'],basis,details)
        for scope,rows,start in [('combined',both,21),('employment',joined['employment'],23),('study',joined['study'],25)]:
            rates=course_rates(table,rows)
            if rates is None:
                for n in (start,start+1):
                    key='figure-5-'+str(n)
                    if key in registry.coverage:registry.coverage[key]['reason']='对口样本不足，或课程重要度编码与文字含义不能一致核验。'
                continue
            importance,outcome,n,m = rates
            for key,value,count,metric in [('figure-5-'+str(start),importance,n,'核心课程重要度'),
                                           ('figure-5-'+str(start+1),outcome,m,'核心课程满足度')]:
                if value is None:continue
                register(registry,key,joint,[[str(registry.year),str(value),str(count)]],
                         ['届次',metric+'（%）','有效课程评价记录数'],basis+'人群：'+scope+'；仅当届。',
                         labels=[str(registry.year)],series=[{'name':metric,'values':[value]}],kind='bar',valid=count,details=details)
        for group in ('学院名称','专业名称','课程名称'):
            gi=column(table,group)
            if gi is None:continue
            groups=defaultdict(list)
            ai=column(table,'学院名称')
            for row in both:
                if present(row[gi]):
                    label=(row[ai]+' / '+row[gi]) if group=='专业名称' and ai is not None else row[gi]
                    groups[label].append(row)
            output=[]
            for label,rows in sorted(groups.items()):
                rates=course_rates(table,rows)
                if rates is None:continue
                importance,outcome,n,m=rates
                if n<5:continue
                output.append([label,str(importance),str(outcome) if outcome is not None and m>=5 else '—',str(n),str(m)])
            if not output:continue
            cols=[group,'核心课程重要度（%）','核心课程满足度（%）','重要度有效记录数','满足度有效记录数']
            result=registry.evidence.computed(joint,{'cohort':registry.year,'type':'linked_course_results',
                'scope':'对口就业及实际对口读研','columns':cols,'rows':output,'basis':basis,
                'groupBy':group,**details})
            for path in COURSE_PATHS:
                registry.evidence.topic_sources.setdefault('第五章 教学培养反馈',{}).setdefault(path,[]).append(result['evidenceId'])
            if group=='学院名称':
                pairs=[r for r in output if r[2]!='—']
                register(registry,'figure-5-27',joint,pairs,cols,basis+'散点虚线为各学院点的均值。',
                         labels=[r[0] for r in pairs],series=[{'name':'核心课程重要度','values':[float(r[1]) for r in pairs]},
                         {'name':'核心课程满足度','values':[float(r[2]) for r in pairs]}],kind='scatter',details=details)


def plan_scoped_course_tables(registry,table,combined,employment,basis,details):
    """The reference compares two populations, each with two separate rates."""
    from .report_statistics import leading_major_labeler
    for key,group in [('table-5-9','学院名称'),('table-7-9','一流专业显示名称')]:
        gi=column(table,group)
        if gi is None:continue
        labeler=leading_major_labeler(table,combined+employment) if group=='一流专业显示名称' else None
        scopes=[]
        for rows in (combined,employment):
            groups=defaultdict(list);groups['本校平均']=rows
            for row in rows:
                if present(row[gi]):groups[labeler(row) if labeler else row[gi]].append(row)
            scopes.append(groups)
        labels=list(dict.fromkeys(label for groups in scopes for label in groups))
        output=[];counts={}
        for label in labels:
            values=[];counts[label]=[]
            for groups in scopes:
                rates=course_rates(table,groups.get(label,[]))
                if rates is None:
                    values+=['—','—'];counts[label].append([0,0])
                else:
                    importance,outcome,n,m=rates
                    values += [str(importance) if n>=5 else '—',str(outcome) if outcome is not None and m>=5 else '—']
                    counts[label].append([n,m])
            if any(v!='—' for v in values):output.append([label,*values])
        register(registry,key,table,output,[group,'对口就业/对口读研重要度','对口就业/对口读研满足度','对口就业重要度','对口就业满足度'],
            basis+'表内对口就业/对口读研与对口就业分别筛选并独立统计，不用课程名称排行替代。',
            details={**details,'scopedRecordCounts':counts})
