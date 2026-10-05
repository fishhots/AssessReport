"""Balanced evidence packets and recoverable coverage for fixed report topics."""
import json
import re

from .schemas import ContentBlock
from .validation import subsection_paths
from .quantitative_review import comparison_cards


def compact_result(source):
    result = json.loads(source.text)
    output = {k:v for k,v in result.items() if k not in ('binding','sourceIds','source_ids','parentIds','items','rows','labels','series','detailRows')}
    # Prompt examples include both ends of a comparison, with the full verified
    # dataset remaining available internally and through read_evidence.
    for key in ('items','rows','labels','detailRows'):
        entries = result.get(key, [])
        if entries:
            indices = (list(range(len(entries))) if len(entries) <= 20 else
                       [*range(6), len(entries)-2, len(entries)-1])
            output[key] = [entries[i] for i in indices]
            output[key+'Count'] = len(entries)
            output[key+'Complete'] = len(indices) == len(entries)
            if key=='labels':
                output['series']=[{**s,'values':[s['values'][i] for i in indices],
                                  **({'sampleCounts':[s['sampleCounts'][i] for i in indices]} if 'sampleCounts' in s else {})} for s in result.get('series',[])]
    output['evidenceId'] = source.id
    output['comparisonCards'] = comparison_cards(result)
    output['missingValueMeaning'] = '空缺仅表示无有效指标值；没有明确缺失原因时，不得断言样本不足或不存在该专业。'
    return output


def topic_packets(store, title, focus=None):
    paths = subsection_paths(title)
    leaves = [p for p in paths if not any(c.startswith(p+' / ') for c in paths)]
    if focus is not None:
        leaves = [p for p in leaves if p in focus]
    visuals = store.visuals.context(title) if store.visuals else []
    output = []
    for path in leaves:
        keys = list(store.topic_sources.get(title,{}).get(path,[]))
        keys += [v['statistics']['evidenceId'] for v in visuals if v.get('subsection')==path and v.get('statistics')]
        keys = list(dict.fromkeys(keys))
        parents=[]
        for key in keys:
            data=json.loads(store.read(key).text)
            parent=data.get('binding',{}).get('questionEvidenceId')
            if parent in store.by_id:parents.append(parent)
        keys=list(dict.fromkeys(keys+parents))
        output.append({'subsection':path,'support':'verified_statistics' if keys else 'needs_evidence',
                       'source_ids':keys[:6], 'results':[compact_result(store.read(k)) for k in keys[:6]]})
    return output


def normalize_subsections(draft, store=None):
    """Move content to an unambiguous original leaf; never rename template titles."""
    paths=subsection_paths(draft.title)
    packets=topic_packets(store,draft.title) if store is not None else []
    for block in [*draft.blocks,*draft.tables]:
        path=block.subsection
        if not path:
            text=block.text if isinstance(block,ContentBlock) else block.title
            named=[p for p in paths if p.split(' / ')[-1] in text and not any(c.startswith(p+' / ') for c in paths)]
            evidence_matches=[p['subsection'] for p in packets if set(p['source_ids']) & set(block.source_ids)]
            if len(named)==1:block.subsection=named[0]
            elif len(evidence_matches)==1:block.subsection=evidence_matches[0]
            continue
        descendants=[p for p in paths if p.startswith(path+' / ') and not any(c.startswith(p+' / ') for c in paths)]
        if len(descendants)==1:
            block.subsection=descendants[0]
        elif descendants:
            text=block.text if isinstance(block,ContentBlock) else block.title
            matches=[p for p in descendants if p.split(' / ')[-1] in text]
            if len(matches)==1:block.subsection=matches[0]
    return draft


def fallback_findings(packet):
    """When the model fails, retain a small factual result instead of losing DATA."""
    blocks=[]
    for data in packet['results'][:2]:
        labels,series=data.get('labels',[]),data.get('series',[])
        if labels and series:
            first=series[0]
            entries=[(label,value) for label,value in zip(labels,first['values']) if value is not None]
            if entries:
                selected=sorted(entries,key=lambda r:-r[1])[:3]
                unit=data.get('unit','%')
                metric=packet['subsection'].split(' / ')[-1]
                sentence=(f'{selected[0][0]}届{metric}为{selected[0][1]:g}{unit}。' if len(selected)==1 and re.fullmatch(r'20\d{2}',selected[0][0])
                          else metric+'中，'+'；'.join(f'{label}的{first["name"]}为{value:g}{unit}' for label,value in selected)+'。')
                # A display of actual study status must retain the survey scope.
                scope='在本届受访毕业生中，' if '实际毕业状态' in data.get('basis','') else ''
                blocks.append(ContentBlock(kind='finding',text=scope+sentence,source_ids=[data['evidenceId']],subsection=packet['subsection']))
        elif data.get('rows') and data.get('columns'):
            cols=data['columns'];rows=data['rows']
            descriptions=[]
            value_index=next((i for i,c in enumerate(cols) if '比例' in c and '%' in c),None)
            for row in rows[:3]:
                if value_index is not None:
                    label='、'.join(cell for i,cell in enumerate(row[:value_index]) if cell!='—')
                    descriptions.append(f'{label}的{cols[value_index].replace("（%）","")}为{row[value_index]}%')
                elif len(cols)==3 and cols[:2]==['学院名称','专业名称']:
                    descriptions.append(f'{row[1]}毕业生的主要就业'+('行业' if '行业' in cols[2] else '职业')+'为'+row[2])
                else:descriptions.append('，'.join(f'{name}为{cell}' for name,cell in zip(cols,row) if cell!='—'))
            blocks.append(ContentBlock(kind='finding',text='；'.join(descriptions)+'。',source_ids=[data['evidenceId']],subsection=packet['subsection']))
    return blocks
