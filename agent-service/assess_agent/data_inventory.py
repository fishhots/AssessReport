"""Audit every retained field; keep calculation coverage separate from citations."""
from collections import Counter
import json
import re

from .errors import AgentError
from .survey import frequency, present

STRUCTURAL = {'届次','学历层次','学校名称','学院名称','专业名称','专业大类名称','专业中类名称','一流专业显示名称'}
QUALITATIVE = ('自填','有何建议','备注')


class DataInventory:
    def __init__(self, store, year):
        self.store,self.year=store,year
        self.tables=[]
        self.qualitative=[]
        self.documents=[]

    def build(self):
        files={}
        for source in self.store.corpus.evidence:
            if source.computed:continue
            entry=files.setdefault(source.material_id,{'materialId':source.material_id,'name':source.name,
                'category':source.category,'indexedChunks':0,'indexedCharacters':0})
            entry['indexedChunks']+=1;entry['indexedCharacters']+=len(source.text)
        self.documents=list(files.values())
        registered={}
        if self.store.survey:
            for q in self.store.survey.questions:
                for c in q.get('columns',[]):
                    registered.setdefault((q['materialId'],q['sheet'],c),[]).append(q['evidenceId'])
        for table in self.store.corpus.tables:
            if self.store.cancel_check:self.store.cancel_check()
            if not table.source_ids or self.store.read(table.source_ids[0]).category!='DATA':continue
            if '届次' in table.columns:
                yi=table.columns.index('届次')
                rows=[r for r in table.rows if r[yi]==str(self.year)]
                scope=f'{self.year}届'
            elif '合并' in table.name+table.sheet:
                rows=table.rows;scope='原表两届合并；不能拆为单届或作为跨届变化'
            else:
                rows=table.rows
                years=set(re.findall(r'20\d{2}',table.name+table.sheet))
                scope=f'{self.year}届原表' if years=={str(self.year)} else '原表范围；届次待确认'
            fields=[]
            for i,name in enumerate(table.columns):
                values=[r[i] for r in rows]
                count=sum(present(v) for v in values)
                entry={'column':name,'validCount':count,'unansweredCount':len(rows)-count,
                       'evidenceIds':registered.get((table.material_id,table.sheet,name),[])}
                if not count:
                    entry.update(status='empty',reason='该范围无有效值')
                elif any(w in name for w in QUALITATIVE):
                    entry.update(status='qualitative',reason='文本意见供定性检索，不把不同意见的频数当作总体指标')
                    result=self.store.computed(table,{'type':'qualitative_availability','column':name,
                        'scope':scope,'validCount':count,'unansweredCount':len(rows)-count,
                        'basis':'有效文本数量，不提取个人身份或自动赋予情绪/因果结论。'})
                    entry['evidenceIds']=[result['evidenceId']]
                    if '有何建议' in name:
                        themes={'课程教学':['课程','教学','教材'],'实践培养':['实习','实践','实验'],
                                '教师指导':['教师','老师','导师'],'就业支持':['就业','求职','招聘'],
                                '生活设施':['食堂','宿舍','住宿','设施'],'数字资源':['网络','系统','数字','信息化']}
                        comments=[v for v in values if present(v)]
                        items=[{'label':theme,'count':sum(any(w in v for w in words) for v in comments)} for theme,words in themes.items()]
                        result=self.store.computed(table,{'type':'comment_keywords','column':name,'scope':scope,
                            'validCount':len(comments),'items':items,
                            'basis':'逐条文本关键词提及数；主题可重叠，未提及不能解释为无意见，不作为总体满意度或正负情绪结论。'})
                        entry['evidenceIds'].append(result['evidenceId'])
                        self.qualitative.append(result)
                elif entry['evidenceIds']:
                    entry.update(status='grouping' if name in STRUCTURAL else 'calculated',reason='题项级完整统计已登记')
                else:
                    result=self.store.computed(table,{'type':'field_frequency','column':name,'scope':scope,
                        **frequency(values),'basis':'完整字段频数；原表指标值不平均，未知编码不赋予含义；各题有效值为分母。'})
                    entry.update(status='grouping' if name in STRUCTURAL else 'calculated',
                                 reason='结构分组字段' if name in STRUCTURAL else '完整频数已登记；未明确的编码只展示原值分布',
                                 evidenceIds=[result['evidenceId']])
                fields.append(entry)
            self.tables.append({'materialId':table.material_id,'name':table.name,'sheet':table.sheet,
                'sourceRowCount':len(table.rows),'scopeRowCount':len(rows),'scope':scope,
                'fieldCount':len(fields),'anonymousJoinAvailable':len(table.record_keys)==len(table.rows) and bool(table.record_keys),
                'fields':fields})
        return self

    def inspect(self, material_id='', sheet='', column_query='', offset=0, limit=30):
        if offset<0 or not 1<=limit<=80:raise AgentError('TOOL_ARGUMENT','分页范围无效')
        tables=[t for t in self.tables if (not material_id or t['materialId']==material_id) and (not sheet or t['sheet']==sheet)]
        fields=[{'materialId':t['materialId'],'sheet':t['sheet'],**f} for t in tables for f in t['fields'] if not column_query or column_query in f['column']]
        return {'matchedCount':len(fields),'fields':fields[offset:offset+limit],
                'nextOffset':offset+limit if offset+limit<len(fields) else None}

    def summary(self):
        return [{'materialId':t['materialId'],'name':t['name'],'sheet':t['sheet'],
                 'scope':t['scope'],'sourceRows':t['sourceRowCount'],'analysisRows':t['scopeRowCount'],
                 'fieldCount':t['fieldCount'],'fieldStatuses':dict(Counter(f['status'] for f in t['fields'])),
                 'anonymousJoinAvailable':t['anonymousJoinAvailable']} for t in self.tables]

    def audit(self, sections=()):
        cited={key for s in sections for item in [*s.blocks,*s.tables] for key in item.source_ids}
        cited.update(v.asset_id for s in sections for v in s.visuals)
        if self.store.visuals:
            for key in list(cited):
                if key in self.store.visuals.assets:cited.add(self.store.visuals.assets[key]['datasetId'])
        pending=list(cited)
        while pending:
            source=self.store.by_id.get(pending.pop())
            if source:
                for parent in source.parent_ids:
                    if parent not in cited:cited.add(parent);pending.append(parent)
        materials={s.material_id for k,s in self.store.by_id.items() if k in cited}
        return {'version':1,'tables':self.tables,'summary':self.summary(),'documents':self.documents,
                'reportCitedMaterials':sorted(materials),'reportCitedEvidenceCount':len(cited),
                'policy':'统计覆盖不等同于正文引用；所有工作表、保留字段与未能计算的原因逐项登记。'}


def statistics_page(store, evidence_id, key='items', offset=0, limit=30, item_index=-1):
    source=store.read(evidence_id)
    if not source.computed or source.category!='DATA':raise AgentError('STATISTICS_SOURCE','仅能读取本次DATA计算证据')
    if offset<0 or not 1<=limit<=80 or key not in ('items','rows','detailRows','labels'):
        raise AgentError('TOOL_ARGUMENT','统计分页范围无效')
    data=json.loads(source.text)
    if item_index>=0:
        items=data.get('items',[])
        if item_index>=len(items) or not isinstance(items[item_index],dict):raise AgentError('TOOL_ARGUMENT','子题不存在')
        data=items[item_index]
    entries=data.get(key,[])
    result={k:v for k,v in data.items() if k not in ('items','rows','detailRows','labels','series','sourceIds','parentIds')}
    result.update(evidenceId=evidence_id,totalCount=len(entries),offset=offset,entries=entries[offset:offset+limit],
                  nextOffset=offset+limit if offset+limit<len(entries) else None)
    if key=='labels':result['series']=[{**s,'values':s['values'][offset:offset+limit],
        **({'sampleCounts':s['sampleCounts'][offset:offset+limit]} if 'sampleCounts' in s else {})} for s in data.get('series',[])]
    return result
