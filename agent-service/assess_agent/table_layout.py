"""Editable comparison tables with the reference report's grouped headers.

Presentation only: all denominators and original long rows remain in datasets
and audit records. No averaging, imputation or changed cohort is performed.
"""
from collections import defaultdict
from fractions import Fraction


def ai_frequency_summary(tasks):
    """Summarize tasks using actual counts, never averaged/rounded percentages."""
    from .survey import percentage
    groups=defaultdict(list)
    for item in tasks:
        counts=item['counts']; n=item['validCount']
        if n<5 or sum(counts.values())!=n or not set(counts)<= {'总是','经常','偶尔','从不'}:
            return None
        groups[item['group']].append((item['task'],Fraction(counts.get('总是',0)+counts.get('经常',0),n),n))
    output=[]
    for group,values in sorted(groups.items()):
        high=max(v[1] for v in values);low=min(v[1] for v in values)
        def describe(rate):
            tied=sorted((v for v in values if v[1]==rate),key=lambda v:v[0])[:2]
            return '；'.join(f'{task}（{percentage(rate.numerator,rate.denominator):.2f}%）' for task,_,_ in tied)
        output.append([group,describe(high),describe(low) if low<high else '各任务高频使用比例一致'])
    return {'columns':['职业类','高频使用比例相对较高的任务','高频使用比例相对较低的任务'],
            'rows':output,'headerGroups':None} if output else None


def distribution_table_view(data):
    """Main-body distributions show the leading categories, retaining raw data.

    The header becomes [具体信息, {year}届] where the year column holds the
    percentage of all valid answers for that cohort. Multi-year comparison is
    supported when ``data`` carries ``multiYear`` = {year: {category: pct}}; one
    extra column per other cohort is appended (value looked up by category,
    '—' when absent) so the table reads as yearly share side by side.
    """
    from .quantitative_review import parsed_number
    key=data.get('templateId')
    titles={'table-1-1':'省份','table-1-2':'城市','table-1-3':'行业类','table-1-4':'职业类',
            'table-1-5':'行业类','table-1-6':'行业','table-1-8':'职业类','table-1-9':'职业'}
    if key not in titles:return None
    columns=data.get('columns',[]);rows=data.get('rows',[])
    indices=[i for i,c in enumerate(columns) if '比例' in c or '占比' in c]
    if len(indices)!=1 or not rows:return None
    i=indices[0]
    if any(len(r)!=len(columns) or parsed_number(r[i]) is None for r in rows):return None
    year=str(data.get('cohort') or '2025')
    ranked=sorted(rows,key=lambda r:(-parsed_number(r[i]),r[0]))
    selected=ranked[:5]
    if len(ranked)>5:
        tied=[r for r in ranked[5:] if parsed_number(r[i])==parsed_number(selected[-1][i])]
        if len(selected)+len(tied)<=10:selected+=tied
    note='列示比例最高的前五项，排名并列时一并列示' if len(selected)>5 else '列示比例最高的前五项'
    if len(ranked)>len(selected) and parsed_number(ranked[len(selected)][i])==parsed_number(selected[-1][i]):
        note+='；并列项按名称排序选取'
    out_columns=[titles[key],f'{year}届']
    out_rows=[[r[0],r[i]] for r in selected]
    # Forward-compatible yearly columns: append one per other cohort when the
    # caller supplies aligned per-category percentages.
    multi_year=data.get('multiYear')
    if isinstance(multi_year,dict):
        for yr,by_category in sorted(((y,c) for y,c in multi_year.items() if str(y)!=year)):
            out_columns.append(f'{yr}届')
            for row in out_rows:
                row.append(str(by_category.get(row[0],'—')))
    return {'columns':out_columns,'rows':out_rows,
            'headerGroups':None,'note':note if len(rows)>len(selected) else ''}


def legacy_frequency_view(data):
    """Old saved exports can at least pivot frequency options into columns."""
    from .quantitative_review import parsed_number
    if data.get('templateId')!='table-5-5' or data.get('columns')!=['职业类','工作任务','使用频率','比例（%）','有效回答人数']:
        return None
    lookup={};samples={}
    for group,task,choice,value,n in data.get('rows',[]):
        if choice not in ('总是','经常','偶尔','从不') or parsed_number(value) is None:return None
        key=(group,task)
        if key in samples and samples[key]!=n:return None
        if choice in lookup.setdefault(key,{}) and lookup[key][choice]!=value:return None
        samples[key]=n;lookup[key][choice]=value
    if not lookup or any(abs(sum(parsed_number(v) for v in choices.values())-100)>.05 for choices in lookup.values()):return None
    return {'columns':['职业类','主要工作任务','总是（%）','经常（%）','偶尔（%）','从不（%）'],
            'rows':[[group,task,*[lookup[group,task].get(c,'0.00') for c in ('总是','经常','偶尔','从不')]]
                    for group,task in sorted(lookup)],'headerGroups':None}


PRACTICE = [
    ('教师实践经验丰富，指导效果好','教师实践经验丰富，指导效果好','校内实习实践评价'),
    ('实践教学过程中，有充分的动手操作机会','实践教学过程中，有充分的动手操作机会','校内实习实践评价'),
    ('实践教学设备/工具/软件等是行业普遍使用的','实践教学设备/工具/软件与工作匹配','校内实习实践评价'),
    ('实践教学内容与目前工作岗位需求契合度高','岗位需求契合度高','校内实习实践评价'),
    ('实习实践内容与所学专业相关','与专业对口','校外实习实践评价'),
    ('实习实践制度规范，有明确实习目标、任务、考核标准','制度规范','校外实习实践评价'),
    ('校外指导老师经验丰富、业务素质好、责任心强','校外指导老师经验丰富','校外实习实践评价'),
    ('实习实践过程能够了解到行业先进技术','了解行业先进技术','校外实习实践评价'),
]


def comparison_table_view(data):
    from .template_tables import reference_table_view
    view = reference_table_view(data)
    if view is not None:return view
    key = data.get('templateId')
    original = {'columns':data.get('columns', []), 'rows':data.get('rows', []), 'headerGroups':None}
    summarized=distribution_table_view(data) or legacy_frequency_view(data)
    if summarized:return summarized
    if key=='table-5-9' and data.get('columns')==['学院名称','核心课程重要度（%）','核心课程满足度（%）','重要度有效记录数','满足度有效记录数']:
        return {'columns':['学院','核心课程重要度（%）','核心课程满足度（%）'],
                'rows':[row[:3] for row in data['rows']],'headerGroups':None}
    if key=='table-7-8' and data.get('columns')==['专业','评价项目或主要需求','比例（%）','有效回答人数']:
        from .quantitative_review import parsed_number
        rows=data.get('rows',[])
        if rows and all(len(row)==4 and parsed_number(row[2]) is not None for row in rows) and len({r[1] for r in rows})==1:
            return {'columns':['专业','教学满意度（%）'],
                    'rows':[[row[0],f'{parsed_number(row[2]):.2f}'] for row in rows],'headerGroups':None}
    if key in ('table-7-13','table-7-14') and data.get('columns',[]) in (
            ['专业','主要行业','比例（%）','有效回答人数'],['专业','主要职业','比例（%）','有效回答人数']):
        from .quantitative_review import parsed_number
        if any(len(row)!=4 or parsed_number(row[2]) is None for row in data['rows']):return original
        groups={}
        for label,option,value,_ in data['rows']:
            groups.setdefault(label,[]).append(f'{option}（{parsed_number(value):.2f}%）')
        return {'columns':['专业名称','就业的主要行业' if key=='table-7-13' else '从事的主要职业'],
                'rows':[[label,'；'.join(items)] for label,items in groups.items()],'headerGroups':None}
    if key not in ('table-7-10','table-7-11','table-5-1','table-5-2','table-5-3','table-5-4',
                   'table-5-6','table-5-7','table-5-8','table-5-11','table-6-1') or data.get('columns',[])[1:] != ['评价项目或主要需求','比例（%）','有效回答人数']:
        return original
    rows = data['rows']
    projects = list(dict.fromkeys(row[1] for row in rows))
    if key in ('table-7-11','table-5-3'):
        # Use exact item labels from the current questionnaire; never map an
        # unrecognized item onto another outcome merely to fill the template.
        fields = [(source,label,group) for source,label,group in PRACTICE]
        recognized = {source for source,_,_ in fields}
        fields += [(p,p,'其他评价项目') for p in projects if p not in recognized]
    elif key=='table-7-10':
        fields = [('教师指导总体满足度','教师指导总体满足度',''),
                  ('师生高频交流的比例','师生高频交流的比例',''),
                  *[(p,p,'教师指导满足度') for p in ('学习指导','职业规划','就业指导')]]
        fields += [(p,p,'其他评价项目') for p in projects if p not in {f[0] for f in fields}]
    elif key=='table-6-1':
        short=['视频课件质量与清晰度','在线课程专业覆盖','虚拟仿真教学效果','数字教材参考资源','资源更新与时效',
               '数字资源获取','一网通办效率','个性化学习体验','信息技术应用能力']
        expected=['在线课程视频/课件的质量和清晰度','在线课程的专业覆盖面','虚拟仿真实验项目的真实感和教学效果','数字教材或电子参考资源的丰富程度','资源的更新频率和时效性',
                  '能获得丰富的数字资源','一网通办，提高学生管理与服务效率','能满足个性化学习，提升学习体验','提升学生信息技术应用能力']
        fields=[(p,label,'数字化资源内容' if i<5 else '数字化建设评价') for i,(p,label) in enumerate(zip(expected,short))]
        fields += [(p,p,'其他评价项目') for p in projects if p not in expected]
    elif key=='table-5-1':
        fields=[(p,'教学满意度' if '教学满意程度' in p else p,'教学满意度' if '教学满意程度' in p else '教学改进需求') for p in projects]
    elif key=='table-5-11':
        fields=[(p,'教材先进性' if '行业发展同步' in p else '教材实用性' if '实用' in p else p,'教材内容评价') for p in projects]
    elif key in ('table-5-4','table-5-6','table-5-7'):
        import re
        fields=[(p,re.sub(r'[（(].*[）)]$', '', p), 'AI应用场景' if key=='table-5-4' else 'AI能力素养需求' if key=='table-5-6' else 'AI教学培养需求') for p in projects]
    elif key=='table-5-2':
        fields=[(p,p,'实习实践活动满意度') for p in projects]
    else:
        fields = [(p,p,'评价项目') for p in projects]
    # A duplicate pair with different values is ambiguous. Retain the long view
    # rather than selecting an arbitrary result or combining denominators.
    lookup = {}
    for label,project,value,*_ in rows:
        if (label,project) in lookup and lookup[label,project] != value:
            return original
        lookup[label,project] = value
    labels = list(dict.fromkeys(row[0] for row in rows))
    def wide_view(selected):
        output=[[label,*[lookup.get((label,p),'—') for p,_,_ in selected]] for label in labels]
        groups=[(0,1,data['columns'][0])]
        for i,(_,name,group) in enumerate(selected,1):
            title=group or name
            if group and groups[-1][2]==group:
                start,size,text=groups[-1];groups[-1]=(start,size+1,text)
            else:groups.append((i,1,title))
        return {'columns':[data['columns'][0],*[name for _,name,_ in selected]],'rows':output,'headerGroups':groups}
    view=wide_view(fields)
    if len(fields)>9:
        view['panels']=[wide_view(fields[i:i+6]) for i in range(0,len(fields),6)]
    return view


def duplicate_statistic_table(table, datasets):
    """Drop only literal/statistical excerpts; keep analytical action matrices."""
    from .quantitative_review import parsed_number
    if not table.rows or not table.source_ids:
        return False
    if reference_statistic_excerpt(table,datasets):return True
    for data in datasets:
        view=comparison_table_view(data)
        # Exact copies stay duplicates even if two computations used different
        # evidence IDs. No approximate number/text matching is performed.
        if any(table.columns==v.get('columns') and all(row in v.get('rows',[]) for row in table.rows)
               for v in (data,view)):
            return True
    candidates = [d for d in datasets if set(table.source_ids) &
                  {d.get('datasetId'),d.get('binding',{}).get('questionEvidenceId'),*d.get('sourceIds',[])}
                  and d.get('type')=='table']
    for data in candidates:
        if table.columns == data.get('columns') and all(row in data.get('rows',[]) for row in table.rows):
            return True
        # Wide/long restatements with label + project + one existing value.
        if len(table.columns)!=3:
            continue
        facts = {(row[0],row[1],parsed_number(row[2])) for row in data.get('rows',[]) if len(row)>=4 and parsed_number(row[2]) is not None}
        if facts and all((row[0],row[1],parsed_number(row[2])) in facts for row in table.rows):
            return True
    return False


def reference_statistic_excerpt(table,datasets):
    """Keep the reference tables instead of redundant model-made restatements.

    Match explicit table IDs, exact distribution pairs, or the combined major
    metric table already represented by the separate reference annual tables.
    Narrative course/action matrices are outside these rules.
    """
    import re
    from .template_tables import table_template, normalized
    from .quantitative_review import parsed_number
    reference={d.get('templateId'):d for d in datasets if d.get('type')=='table' and table_template(d.get('templateId',''))}
    labels=2 if len(table.columns)>1 and table.columns[1] in ('专业','专业名称','工作任务','主要工作任务') else 1
    numeric_body=bool(table.rows) and all(
        len(row)>labels and all(parsed_number(str(v)) is not None or v in ('—','-','－','') for v in row[labels:])
        for row in table.rows)
    for chapter,number in re.findall(r'表\s*(\d+)\s*[-－—]\s*(\d+)',table.title):
        if numeric_body and f'table-{chapter}-{number}' in reference:return True
    # A model-created raw value grid under a chart is a duplicate presentation,
    # including multi-series stacked bars such as figure 5-6.
    def metric_name(text):
        return re.sub(r'\((?:%|％|元|人|分)\)$','',normalized(text))
    for data in datasets:
        series=data.get('series',[])
        if data.get('type')=='table' or not series or len(table.columns)!=len(series)+1:
            continue
        series_by_name={metric_name(s.get('name','')):s for s in series}
        headers=[metric_name(c) for c in table.columns[1:]]
        if len(series_by_name)!=len(series) or set(headers)!=set(series_by_name):
            continue
        labels=data.get('labels',[])
        facts={(str(label),name):parsed_number(str(value))
               for name,s in series_by_name.items()
               for label,value in zip(labels,s.get('values',[]))}
        if table.rows and all(len(row)==len(table.columns) and row[0] in labels and
                              all(parsed_number(str(row[i])) is not None and
                                  parsed_number(str(row[i]))==facts.get((row[0],headers[i-1]))
                                  for i in range(1,len(row))) for row in table.rows):
            return True
    if len(table.columns)==2 and ('比例' in table.columns[1] or '占比' in table.columns[1]):
        for key,data in reference.items():
            if not key.startswith('table-1-'):continue
            ci=next((i for i,c in enumerate(data.get('columns',[])) if '比例' in c or '占比' in c),None)
            if ci is None:continue
            facts={(r[0],parsed_number(str(r[ci]))) for r in data.get('rows',[]) if len(r)>ci}
            if facts and all(parsed_number(r[1]) is not None and (r[0],parsed_number(r[1])) in facts for r in table.rows):return True
    if len(table.columns)==2 and numeric_body:
        for data in datasets:
            if data.get('type')=='table' or not re.match(r'^figure-(?:1|5|6|7)-',data.get('templateId','')):continue
            if len(data.get('series',[]))!=1:continue
            series=data['series'][0]
            header=metric_name(table.columns[1])
            units=re.findall(r'\((%|％|元|人|分)\)$',normalized(table.columns[1]))
            if units and units[0].replace('％','%')!=data.get('unit','%'):continue
            if header not in ('比例','占比') and header!=metric_name(series.get('name','')):continue
            if header in ('比例','占比'):
                from .visuals import ITEMS
                caption=re.sub(r'^图\s*\d+-\d+\s*','',ITEMS.get(data['templateId'],{}).get('caption',''))
                if caption and normalized(caption) not in normalized(table.title):continue
            facts={(label,parsed_number(str(value))) for label,value in zip(data.get('labels',[]),series.get('values',[])) if value is not None}
            if len(data.get('benchmarks',[]))==1 and data['benchmarks'][0] is not None:
                facts.add(('本校平均',parsed_number(str(data['benchmarks'][0]))))
            if facts and all((row[0],parsed_number(row[1])) in facts for row in table.rows):return True
    if len(table.columns)>=3 and table.columns[0] in ('学院','学院名称') and table.columns[1] in ('专业','专业名称'):
        metric_tables={'毕业去向落实率':'table-7-1','专业相关度':'table-7-2','月收入':'table-7-3',
                       '就业满意度':'table-7-4','教学满意度':'table-7-8'}
        metrics=[re.sub(r'\((?:%|％|元)\)$','',normalized(c)).replace('工作与专业相关度','专业相关度') for c in table.columns[2:]]
        if all(metric in metric_tables and metric_tables[metric] in reference for metric in metrics):
            return all(all(parsed_number(str(v)) is not None or v in ('—','-','－','') for v in row[2:]) for row in table.rows)
    return False
