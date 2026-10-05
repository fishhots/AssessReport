"""Compare labelled values within each verified metric, before formal writing.

These checks deliberately use explicit labels and units. They do not infer
synonyms, causal relationships, statistical significance or benchmark grades.
"""
import json
import re
from collections import defaultdict


def parsed_number(value):
    text = str(value).strip().replace(',', '')
    if not re.fullmatch(r'-?\d+(?:\.\d+)?(?:[%％元级])?', text):
        return None
    return float(re.sub(r'[%％元级]$', '', text))


def metric_groups(data):
    """Full-data groups; never rank a prompt's truncated row sample."""
    groups = defaultdict(list)
    labels, series = data.get('labels', []), data.get('series', [])
    if labels and series:
        for s in series:
            for label, value in zip(labels, s.get('values', [])):
                if (number := parsed_number(value)) is not None:
                    groups[(s['name'], data.get('unit', '%'))].append((label, number))
        return groups
    items = data.get('items', [])
    for item in items:
        if data.get('type')=='paired_scale':
            for field,metric,unit in [('masteryMean','配对掌握均值','级'),('demandMean','配对需求均值','级'),
                ('demandMinusMasteryMean','配对需求减掌握均值','级'),('masteryAtLeastDemandPercentage','配对掌握不低于需求回答比例','%')]:
                if (value:=parsed_number(item.get(field))) is not None:groups[(metric,unit)].append((item['label'],value))
        elif 'mean' in item:
            label, _, phase = item['label'].partition(' / ')
            if (value:=parsed_number(item['mean'])) is not None:
                groups[(phase or data.get('question', '量表均值'), item.get('unit', '级'))].append((label, value))
        elif 'percentage' in item:
            if (value:=parsed_number(item['percentage'])) is not None:
                groups[(data.get('question', '比例'), '%')].append((item['label'], value))
        elif 'positivePercentage' in item:
            if (value:=parsed_number(item['positivePercentage'])) is not None:
                groups[(data.get('question','评价')+' / 正向评价比例','%')].append((item['label'],value))
            if (value:=parsed_number(item.get('nonPositivePercentage'))) is not None:
                groups[(data.get('question','评价')+' / 非正向评价比例','%')].append((item['label'],value))
    if groups:
        return groups
    columns, rows = data.get('columns', []), data.get('rows', [])
    # Metadata / denominators are not outcome metrics. Preserve faculty+major
    # and subquestion scope when creating comparison groups from long tables.
    label_indices = [i for i,c in enumerate(columns) if any(w in c for w in ('学院', '专业', '类别', '课程')) and not any(w in c for w in ('比例','满足','相关','人数'))]
    project = next((i for i,c in enumerate(columns) if c in ('评价项目或主要需求', '评价项目')), None)
    for i,c in enumerate(columns):
        unit = '%' if '%' in c or '％' in c else '元' if '月收入' in c else ''
        if not unit or not label_indices:
            continue
        for row in rows:
            if (value := parsed_number(row[i])) is None:
                continue
            label = ' / '.join(str(row[j]) for j in label_indices)
            metric = str(row[project]) if project is not None else c
            groups[(metric, unit)].append((label, value))
    return groups


def comparison_cards(data):
    output = []
    for (metric, unit), values in metric_groups(data).items():
        if not values:
            continue
        ordered = sorted(values, key=lambda r:(-r[1],r[0]))
        high, low = ordered[0][1], ordered[-1][1]
        output.append({'metric':metric, 'unit':unit, 'validGroups':len(values),
            'highestValue':high, 'lowestValue':low,
            'highestLabels':[label for label,value in values if value == high][:10],
            'lowestLabels':[label for label,value in values if value == low][:10],
            'highestTieCount':sum(value == high for _,value in values),
            'lowestTieCount':sum(value == low for _,value in values),
            'top':[{'label':label,'value':value} for label,value in ordered[:3]],
            'bottom':[{'label':label,'value':value} for label,value in ordered[-3:]],
            'scope':'完整结果中同一指标的有效组；缺失值不参与排名'})
    return output


def quantitative_issues(text, known):
    data = []
    for source in known:
        if source.category != 'DATA' or not source.computed:
            continue
        try:
            parsed = json.loads(source.text)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            data.append(parsed)
    issues = []
    if re.search(r'处于中等水平|总体(?:处于)?中等水平|(?:整体|总体)可控|已达标',text):
        explicit = any(d.get('ratingThresholds') or any('中等' in str(i.get('label','')) for i in d.get('items',[])) for d in data)
        if not explicit:
            issues.append('绝对等级或可控判断缺少明确评价阈值，改为当届水平或同口径相对比较')
    asserted='。'.join(c for c in re.split(r'[。；，]',text) if not re.search(r'未(?:作|进行)|不(?:涉及|作|能断言)|不能(?:断言|判断)',c))
    if re.search(r'显著(?:高于|低于|差异)|(?:差异|差距)[^。；，]{0,3}显著|统计(?:学)?显著', asserted):
        tests = [d for d in data if d.get('type') == 'statistical_test' and
                 isinstance(d.get('pValue'), (int,float)) and 0 <= d['pValue'] <= d.get('alpha',.05) <= .05]
        if not tests:
            issues.append('差异显著的判断缺少对应显著性检验证据，改为同口径的定性高低比较')
    groups = []
    for d in data:
        # Identically named percentages in different charts/populations are
        # separate groups. Combining them would corrupt an otherwise correct
        # faculty ranking with a professional or national comparison value.
        groups.extend((metric,unit,values) for (metric,unit),values in metric_groups(d).items())
    for sentence in re.split(r'[。；]', text):
        threshold=re.search(r'月收入[^，。；]{0,6}(?:均超过|超过|高于|突破)(?:一)?万元',sentence)
        if threshold:
            scope=sentence[:threshold.start()]
            for _,unit,facts in groups:
                if unit != '元':continue
                for label,value in facts:
                    parts=label.split(' / ')
                    if len(parts)==2 and all(p in scope for p in parts) and value<=10000:
                        issues.append(f'“{label}”的月收入未超过万元，不能将其包含在该概括中；改为准确专业范围或原值')
        claims = list(re.finditer(r'(\d+(?:\.\d+)?)\s*([%％元级])', sentence))
        for claim in claims:
            unit = '%' if claim[2] == '％' else claim[2]
            value = float(claim[1])
            prefix = sentence[max(0,claim.start()-120):claim.start()]
            # Match a complete explicit source label followed closely by its
            # value. Ignore enumeration clauses and ambiguous renamed labels.
            matches = []
            for metric, group_unit, facts in groups:
                if unit != group_unit:
                    continue
                for label, expected in facts:
                    if len(label) < 3 or ' / ' in label or re.fullmatch(r'\d+',label):
                        continue
                    position = prefix.rfind(label)
                    if position < 0:
                        continue
                    before=prefix[:position]
                    if before.endswith(('（','(')) or (len(label)<=10 and before and re.search(r'[\u4e00-\u9fff]$',before)):
                        continue
                    tail = prefix[position+len(label):]
                    if len(tail)>24 or re.search(r'\d|[%％元级]|、', tail) or ('，' in tail and not re.search(r'最高|最低',tail)):
                        continue
                    # A label mentioned as background, embedded in a longer
                    # faculty name, or part of a paired enumeration is not a
                    # one-to-one labelled scalar assertion.
                    if re.search(r'分别|均为|原因|和$|及$',tail) or re.search(r'[和及、]$',prefix[:position]):
                        continue
                    neutral=re.sub(r'[“”"「」：:（()）\s，]', '',tail)
                    neutral=re.sub(r'比例|比率|覆盖率|均值|水平|自评|掌握|正向评价|满意度|满足度|为|达到|达|占|最高|最低|的|仅|总体|平均|约|能力','',neutral)
                    if neutral:
                        continue
                    matches.append((position,label,expected,metric,facts))
            if not matches:
                continue
            latest = max(m[0]+len(m[1]) for m in matches)
            matches = [m for m in matches if m[0]+len(m[1]) == latest]
            longest = max(len(m[1]) for m in matches)
            matches = [m for m in matches if len(m[1]) == longest]
            expected_values = {m[2] for m in matches}
            if value not in expected_values:
                issues.append(f'“{matches[0][1]}”的数值与指标标签不对应，请按同一标签核对，不能交换不同选项的数值')
                continue
            tail=prefix[matches[0][0]+len(matches[0][1]):]
            suffix=re.split(r'[，,]',sentence[claim.end():claim.end()+10])[0]
            extrema = re.search(r'最高|最低', tail+suffix)
            if extrema:
                if re.search(r'其余|已列|以上|上述',prefix[:matches[0][0]]):
                    continue
                compatible = [m for m in matches if m[2] == value]
                # If a label appears in different demand/mastery series, an
                # explicit value identifies the series, rather than pooling it.
                correct = any(value == (max if extrema[0]=='最高' else min)(v for label,v in m[4]
                    if not re.search(r'以上均未(?:参加|参与)|没有任何提升',label) or label==m[1]) for m in compatible)
                if not correct:
                    issues.append(f'“{matches[0][1]}”的{extrema[0]}判断与完整同指标结果不一致，改为核实后的排序或相对高低描述')
    return list(dict.fromkeys(issues))
