"""Reference table shapes, independent of the statistical storage schema.

Only header labels, widths and formatting come from the reference document.
Values always come from a task's computed datasets; missing cohorts stay blank.
"""
from copy import deepcopy
from collections import defaultdict
from pathlib import Path
import json
import re

from .quantitative_review import parsed_number

_CONFIG = json.loads((Path(__file__).resolve().parents[1]/'config/table-templates.json').read_text(encoding='utf-8'))
TABLE_TEMPLATES = _CONFIG['tables']
TARGET_CHAPTERS = {'1', '5', '6', '7'}
ANNUAL_METRICS = {f'table-7-{i}' for i in (1,2,3,4,5,6,7,8)}
DISTRIBUTIONS = {f'table-1-{i}' for i in (1,2,3,4,5,6,8,9)}


def table_template(key, year=2025):
    if key not in TABLE_TEMPLATES or key.split('-')[1] not in TARGET_CHAPTERS:
        return None
    result = deepcopy(TABLE_TEMPLATES[key])
    def shift(text):
        return re.sub(r'(20\d{2})届',lambda m:f'{int(m[1])+int(year)-_CONFIG["referenceYear"]}届',text)
    result['columns'] = [shift(c) for c in result['columns']]
    if result['headerGroups']:
        result['headerGroups'] = [(start,size,shift(title)) for start,size,title in result['headerGroups']]
    return result


def value_text(value):
    number = parsed_number(str(value)) if value is not None else None
    return f'{number:.2f}' if number is not None else '—'


def normalized(text):
    return re.sub(r'\s+','',str(text)).replace('（','(').replace('）',')').replace('／','/')


def display_group_label(value):
    """Hide internal faculty qualifiers in professional-name table cells."""
    text=str(value).strip()
    if '\x1f' in text:
        text=text.split('\x1f',1)[0].strip()
    if ' / ' in text:
        left,right=text.rsplit(' / ',1)
        if '学院' in left and right.strip():
            text=right.strip()
    return re.sub(r'[（(][^（）()]*学院[）)]$','',text).strip()


def sources_by_cohort(data, year):
    sources = {str(year):data}
    for cohort, source in data.get('byCohort',{}).items():
        if isinstance(source,dict):sources[str(cohort)] = source
    return sources


def available_cohorts(data, year):
    """Return actual uploaded cohorts represented by this report dataset."""
    cohorts=set()
    for value in data.get('availableCohorts',[]):
        if re.fullmatch(r'20\d{2}',str(value)):cohorts.add(int(value))
    for value in data.get('byCohort',{}):
        if re.fullmatch(r'20\d{2}',str(value)):cohorts.add(int(value))
    for value in data.get('multiYear',{}):
        if re.fullmatch(r'20\d{2}',str(value)):cohorts.add(int(value))
    for value in data.get('cohorts',[]):
        if re.fullmatch(r'20\d{2}',str(value)):cohorts.add(int(value))
    current=data.get('cohort')
    if re.fullmatch(r'20\d{2}',str(current or '')):cohorts.add(int(current))
    elif data.get('rows'):
        cohorts.add(int(year))
    return sorted(cohorts) or [int(year)]


def use_uploaded_cohort_columns(profile, data, key, year):
    if key in DISTRIBUTIONS or key in ANNUAL_METRICS:
        profile['columns']=[profile['columns'][0],*[f'{y}\u5c4a' for y in available_cohorts(data,year)]]
    elif key=='table-7-12':
        profile['columns']=[*profile['columns'][:2],*[f'{y}\u5c4a' for y in available_cohorts(data,year)]]
    return profile


def numeric_pairs(source, key):
    cols = source.get('columns',[]); rows = source.get('rows',[])
    result = {}
    for row in rows:
        if len(row)!=len(cols) or not row:continue
        if row[0] in ('','—'):continue
        if key in DISTRIBUTIONS:
            index=next((i for i,c in enumerate(cols) if '比例' in c or '占比' in c),len(cols)-1)
            label=row[0]
            if label in ('原始地点尚不能分类','未分类','无法分类'):continue
        elif len(cols)>=3 and cols[0] in ('学院','学院名称') and cols[1] in ('专业','专业名称'):
            label=row[1];index=2
            same_name={r[0] for r in rows if len(r)==len(cols) and r[1]==label}
            if len(same_name)>1:label=f'{label}（{row[0]}）'
        elif len(cols)==4 and cols[1]=='评价项目或主要需求':
            label=row[0];index=2
            if key=='table-7-8' and not ('教学' in row[1] and '满意' in row[1]):
                continue
        else:
            label=row[0];index=1
        if index>=len(row):continue
        value=parsed_number(str(row[index]))
        if label in result and result[label]!=value:
            result[label]=None
        else:result[label]=value
    return result


def annual_view(data, profile, key, year):
    sources=sources_by_cohort(data,year)
    values={cohort:numeric_pairs(source,key) for cohort,source in sources.items()}
    for cohort, pairs in data.get('multiYear',{}).items():
        if isinstance(pairs,dict):values[str(cohort)]={k:parsed_number(str(v)) for k,v in pairs.items()}
    latest=values.get(str(year),{})
    if key in DISTRIBUTIONS:
        ranking=latest or next((values[y] for y in sorted(values,reverse=True) if int(y)<=year and values[y]),{})
        ranked=sorted(ranking,key=lambda label:(-(ranking[label] if ranking[label] is not None else -1),label))
        labels=ranked[:5]
    else:
        labels=list(dict.fromkeys(label for pairs in values.values() for label in pairs))
        labels.sort(key=lambda label:(label!='本校平均',-(latest.get(label) if latest.get(label) is not None else -1),label))
    cohorts=[re.search(r'20\d{2}',c)[0] for c in profile['columns'][1:]]
    return [[display_group_label(label),*[value_text(values.get(cohort,{}).get(label)) for cohort in cohorts]] for label in labels]


def project_alias(project, key):
    text=re.sub(r'\((?:%|％|元)\)$','',normalized(project))
    if key=='table-5-1' and '教学满意程度' in text:return '教学满意度'
    if key=='table-7-8' and '教学满意程度' in text:return '教学满意度'
    aliases={
        '实践教学设备/工具/软件等是行业普遍使用的':'实践教学设备/工具/软件与工作匹配',
        '实践教学内容与目前工作岗位需求契合度高':'岗位需求契合度高',
        '实习实践内容与所学专业相关':'与专业对口',
        '实习实践制度规范，有明确实习目标、任务、考核标准':'制度规范',
        '校外指导老师经验丰富、业务素质好、责任心强':'校外指导老师经验丰富',
        '实习实践过程能够了解到行业先进技术':'了解行业先进技术',
    }
    if text in aliases:return normalized(aliases[text])
    if key=='table-5-11':
        if '行业发展同步' in text or '先进' in text:return '课程教材先进性'
        if '实用' in text:return '课程教材实用性'
    if key in ('table-5-4','table-5-6','table-5-7'):
        text=re.sub(r'\(.*\)$','',text)
    return text


def project_view(data, profile, key):
    lookup={};labels=[]
    cols=data.get('columns',[])
    for row in data.get('rows',[]):
        if len(row)!=len(cols) or not row:continue
        if len(row)==4 and cols[1]=='评价项目或主要需求':
            label,project,value,_=row;entries=[(project,value)]
        else:
            label=row[0];start=1
            if len(cols)>1 and cols[0] in ('学院','学院名称') and cols[1] in ('专业','专业名称'):
                label=row[1];start=2
                if len({r[0] for r in data['rows'] if len(r)==len(cols) and r[1]==label})>1:
                    label=f'{label}（{row[0]}）'
            entries=list(zip(cols[start:],row[start:]))
        if label not in labels:labels.append(label)
        for project,value in entries:
            pair=(label,project_alias(project,key));number=parsed_number(str(value))
            if pair in lookup and lookup[pair]!=number:lookup[pair]=None
            else:lookup[pair]=number
    columns=profile['columns'][1:]
    output=[[display_group_label(label),*[value_text(lookup.get((label,normalized(c)))) for c in columns]] for label in labels]
    index=1
    output.sort(key=lambda row:(row[0]!='本校平均',-(parsed_number(row[index]) or 0),row[0]))
    return output


def practice_ranking(data, profile):
    values=defaultdict(dict)
    for row in data.get('rows',[]):
        if len(row)==4 and row[0]!='本校平均' and parsed_number(str(row[2])) is not None:
            # These two experiment questions have different branches in the
            # questionnaire; never collapse 课程实验 and 课程实验（项目）.
            label={'毕业论文（设计）':'毕业论文/设计'}.get(row[1],row[1])
            values[row[0]][label]=parsed_number(str(row[2]))
    disciplines=profile['columns'][1::2]
    ranks={d:sorted(values[d].items(),key=lambda pair:(-pair[1],pair[0]))[:6] for d in disciplines}
    output=[]
    for i in range(max((len(v) for v in ranks.values()),default=0)):
        row=[]
        for d in disciplines:
            row.extend([ranks[d][i][0],value_text(ranks[d][i][1])] if i<len(ranks[d]) else ['—','—'])
        output.append(row)
    return output


def course_view(data, profile):
    cols=data.get('columns',[]);output=[]
    expected=['对口就业/对口读研重要度','对口就业/对口读研满足度','对口就业重要度','对口就业满足度']
    for row in data.get('rows',[]):
        if len(row)!=len(cols):continue
        # A legacy list of best/worst course names cannot answer these scoped
        # measures. Preserve the names in audit, never substitute their rates.
        if len(cols)==5 and cols[1:]==expected:
            output.append([display_group_label(row[0]),*[value_text(v) for v in row[1:]]])
        elif cols[1:]==['核心课程重要度（%）','核心课程满足度（%）','重要度有效记录数','满足度有效记录数']:
            output.append([display_group_label(row[0]),value_text(row[1]),value_text(row[2]),'—','—'])
        else:output.append([display_group_label(row[0]),*['—']*4])
    output.sort(key=lambda row:(row[0]!='本校平均',-(parsed_number(row[1]) or 0),row[0]))
    return output


def region_view(data, profile, year):
    values=defaultdict(dict)
    for cohort, source in sources_by_cohort(data,year).items():
        cols=source.get('columns',[])
        for row in source.get('rows',[]):
            if len(row)==4 and len(cols)==4:
                values[row[0],row[1]][cohort]=parsed_number(str(row[2]))
            elif len(row)==2:
                for province,value in re.findall(r'([^；]+)[（(](\d+(?:\.\d+)?)%[）)]',row[1]):
                    values[row[0],province][cohort]=float(value)
    groups=defaultdict(list)
    for pair,by_year in values.items():
        if pair[1] not in ('原始地点尚不能分类','未分类'):groups[pair[0]].append(pair)
    cohorts=[re.search(r'20\d{2}',c)[0] for c in profile['columns'][2:]]
    output=[]
    for label,pairs in sorted(groups.items(),key=lambda item:(item[0]!='本校平均',item[0])):
        ranking_year=next((c for c in reversed(cohorts) if any(values[pair].get(c) is not None for pair in pairs)),str(year))
        pairs.sort(key=lambda pair:(-(values[pair].get(ranking_year) or 0),pair[1]))
        for pair in pairs[:3]:
            output.append([display_group_label(label),pair[1],*[value_text(values[pair].get(c)) for c in cohorts]])
    return output


def destination_view(data, key):
    rows=data.get('rows',[]);grouped=defaultdict(list)
    for row in rows:
        if key in ('table-1-7','table-1-10') and len(row)==3:
            grouped[tuple(row[:2])].append(row[2])
        elif key in ('table-7-13','table-7-14') and len(row)>=2:
            grouped[(row[0],)].append(row[1])
    if key=='table-1-10':
        return [[*label,option] for label,options in grouped.items() for option in
                dict.fromkeys(part for value in options for part in value.split('；'))]
    return [[*label,'；'.join(dict.fromkeys(options))] for label,options in grouped.items()]


def frequency_view(data):
    cols=data.get('columns',[])
    if len(cols)==6:
        return [[r[0],r[1],*[value_text(v) for v in r[2:]]] for r in data.get('rows',[]) if len(r)==6]
    # Older checkpoints stored each answer choice on a separate row. Pivot
    # only complete, consistent distributions; a missing choice means zero
    # only when all observed choices add up to 100 with the same denominator.
    groups=defaultdict(list)
    for row in data.get('rows',[]):
        if len(row)==5:groups[tuple(row[:2])].append(row)
    output=[];choices=('总是','经常','偶尔','从不')
    for label,rows in groups.items():
        rates={r[2]:parsed_number(str(r[3])) for r in rows}
        valid=(len(rates)==len(rows) and set(rates)<=set(choices) and
               all(v is not None for v in rates.values()) and
               abs(sum(v or 0 for v in rates.values())-100)<0.02 and
               len({r[4] for r in rows})==1)
        output.append([*label,*[value_text(rates.get(c,0)) if valid else '—' for c in choices]])
    return output


def reference_table_view(data):
    key=data.get('templateId','');year=int(data.get('reportYear') or data.get('cohort') or _CONFIG['referenceYear'])
    profile=table_template(key,year)
    if profile is None:return None
    profile=use_uploaded_cohort_columns(profile,data,key,year)
    if key in DISTRIBUTIONS or key in ANNUAL_METRICS:rows=annual_view(data,profile,key,year)
    elif key=='table-5-2':rows=practice_ranking(data,profile)
    elif key in ('table-5-9','table-7-9'):rows=course_view(data,profile)
    elif key=='table-7-12':rows=region_view(data,profile,year)
    elif key in ('table-1-7','table-1-10','table-7-13','table-7-14'):rows=destination_view(data,key)
    elif key=='table-5-5':
        rows=frequency_view(data)
    elif key=='table-5-10':
        lookup={}
        for row in data.get('rows',[]):
            if len(row)!=len(data.get('columns',[])):continue
            label='课程教材先进性' if '行业发展同步' in row[0] or '先进' in row[0] else '课程教材实用性' if '实用' in row[0] else row[0]
            lookup[label]={normalized(c).replace('(%)',''):v for c,v in zip(data['columns'][1:],row[1:])}
        rows=[[label,*[value_text(lookup[label].get(normalized(c))) for c in profile['columns'][1:]]]
              for label in ('课程教材实用性','课程教材先进性') if label in lookup]
    else:rows=project_view(data,profile,key)
    if not rows:
        rows=[['本校平均' if key in ANNUAL_METRICS or key in ('table-5-9','table-7-9') else '—',*['—']*(len(profile['columns'])-1)]]
    return {'columns':profile['columns'],'rows':rows,'headerGroups':profile['headerGroups'],
            'templateId':key,'unit':profile['unit'],'mergeColumns':profile['mergeColumns']}
