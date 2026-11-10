from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import unicodedata

from langchain.tools import tool

from .errors import AgentError
from .schemas import Corpus, Evidence, ParsedTable


def numeric_values(text: str) -> set[Decimal]:
    text = unicodedata.normalize("NFKC", text).replace(",", "")
    # Evidence/asset IDs and original figure/table numbers are reference metadata,
    # not statistical claims. Do not interpret digits inside their hashes as facts.
    text = re.sub(r"\b[ECV]-[0-9a-fA-F]{16,64}\b", "", text)
    text = re.sub(r"(?:图|表)\s*\d+(?:\s*[-—.]\s*\d+)+", "", text)
    return {Decimal(value) for value in re.findall(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", text)}


class EvidenceStore:
    def __init__(self, corpus: Corpus):
        self.corpus = corpus
        self.by_id = {source.id: source for source in corpus.evidence}
        self.visuals = None
        self.cancel_check = None
        self.content_gap_notes = {}
        self.survey = None
        self.topic_sources = {}
        self.topic_coverage = {}
        self.inventory = None
        # 由 workflow 在启用联网检索时注入：{'service', 'school', 'year'}
        self.web_tools_provider = None

    def read(self, evidence_id: str) -> Evidence:
        if evidence_id not in self.by_id:
            raise AgentError("EVIDENCE_ID", "证据 ID 不属于本次任务")
        return self.by_id[evidence_id]

    def for_model(self, evidence_id: str) -> dict:
        """Keep the full provenance chain server-side, not in every model message."""
        source=self.read(evidence_id)
        result=source.model_dump(by_alias=True)
        result['parentCount']=len(result['parentIds'])
        result['parentIds']=result['parentIds'][:3]
        if source.computed:
            try:
                def compact(value):
                    if isinstance(value,dict):
                        output={}
                        for key,entry in value.items():
                            if key in ('sourceIds','source_ids','parentIds','parent_ids') and isinstance(entry,list):
                                output[key]=entry[:3];output[key+'Count']=len(entry)
                            else:output[key]=compact(entry)
                        return output
                    if isinstance(value,list):return [compact(v) for v in value]
                    return value
                result['text']=json.dumps(compact(json.loads(source.text)),ensure_ascii=False)
            except (ValueError,TypeError):pass
        return result

    def search(self, query: str, category: str = "ALL", limit: int = 6) -> list[dict]:
        if not 1 <= limit <= 10 or len(query) > 300 or category not in ("ALL", "DATA", "SPEECH", "REFERENCE"):
            raise AgentError("TOOL_ARGUMENT", "检索参数无效")
        words = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", query.lower())
        tokens = set(words)
        tokens.update(word[i:i + 2] for word in words if len(word) > 2 for i in range(len(word) - 1))
        scored = []
        for item in self.corpus.evidence:
            if category != "ALL" and item.category != category:
                continue
            content = item.text.lower()
            score = sum(min(content.count(token), 5) * len(token) for token in tokens)
            if score:
                scored.append((score, item))
        # Wide survey chunks repeat hundreds of headers, which otherwise drown
        # out a relevant, compact question-level computed result.
        def priority(source):
            if not source.computed:return 1
            try:
                if json.loads(source.text).get('type') in ('field_frequency','qualitative_availability'):return 2
            except (ValueError,TypeError):pass
            return 0
        scored.sort(key=lambda pair: (priority(pair[1]), -pair[0], pair[1].id))
        return [self.for_model(item.id) for _, item in scored[:limit]]

    def table(self, material_id: str, sheet: str) -> ParsedTable:
        for table in self.corpus.tables:
            if table.material_id == material_id and table.sheet == sheet:
                return table
        raise AgentError("TABLE_ID", "表格不属于本次任务")

    def balanced_speech(self,query,max_files=8):
        """Select relevant passages across documents, instead of one PDF only."""
        words=re.findall(r'[A-Za-z0-9_]+|[\u4e00-\u9fff]+',query.lower())
        tokens=set(words)
        tokens.update(w[i:i+2] for w in words for i in range(len(w)-1))
        best={}
        for source in self.corpus.evidence:
            if source.computed or source.category!='SPEECH':continue
            score=sum(min(source.text.lower().count(token),5)*len(token) for token in tokens)
            if score and (source.material_id not in best or score>best[source.material_id][0]):
                best[source.material_id]=(score,source)
        return [self.for_model(s.id) for _,s in sorted(best.values(),key=lambda p:-p[0])[:max_files]]

    def computed(self, table: ParsedTable, content: dict) -> dict:
        if not table.source_ids:
            raise AgentError("TABLE_EMPTY", "表格没有可用数据行")
        source = self.read(table.source_ids[0])
        text = json.dumps(content, ensure_ascii=False, sort_keys=True)
        key = "C-" + sha256(f"{source.material_id}|{table.sheet}|{text}".encode()).hexdigest()[:16]
        result = Evidence(id=key, material_id=source.material_id, name=source.name, category=source.category,
                          location=f"工作表 {table.sheet}，原始行 {table.start_row}–{table.start_row + len(table.rows) - 1}；统计结果",
                          text=text, computed=True, parent_ids=table.source_ids)
        self.by_id[key] = result
        if not any(item.id == key for item in self.corpus.evidence):
            self.corpus.evidence.append(result)
        return {**content, "evidenceId": key, "location": result.location}

    def summarize(self, material_id: str, sheet: str, column: str, mode: str = "frequency",
                  group_by: str = "", filters: dict[str, str] | None = None) -> dict:
        table = self.table(material_id, sheet)
        for name in [column, group_by, *(filters or {}).keys()]:
            if name and name not in table.columns:
                raise AgentError("TABLE_COLUMN", f"未找到字段：{name}")
        if mode not in ("frequency", "numeric"):
            raise AgentError("TOOL_ARGUMENT", "仅支持 frequency 或 numeric 统计")
        index = table.columns.index(column)
        group_index = table.columns.index(group_by) if group_by else None
        rows = [row for row in table.rows if all(row[table.columns.index(k)] == v for k, v in (filters or {}).items())]
        groups = defaultdict(list)
        for row in rows:
            groups[row[group_index] if group_index is not None else "全部"].append(row[index])
        if len(groups) > 80:
            raise AgentError("TOOL_LIMIT", "分组过多，请增加筛选条件")
        output = []
        for group, values in groups.items():
            present = [v for v in values if v and v not in ("NA", "N/A", "无回答", "未回答")]
            item = {"group": group, "sampleCount": len(values), "validCount": len(present),
                    "missingCount": len(values) - len(present)}
            if mode == "frequency":
                counts = Counter(present)
                top = counts.most_common(30)
                remainder = len(present) - sum(n for _, n in top)
                item["counts"] = dict(top)
                if remainder:
                    item["counts"]["其余类别合计"] = remainder
                from .survey import percentage
                item["percentages"] = {label: percentage(n, len(present)) for label,n in item["counts"].items()}
                item["distinctCount"] = len(counts)
                item["otherCount"] = remainder
            else:
                numbers = []
                for value in present:
                    normalized = value.replace(",", "").strip()
                    if not re.fullmatch(r"-?\d+(?:\.\d+)?", normalized):
                        raise AgentError("TABLE_NUMERIC", "字段含非数值或带单位值，请先明确数值口径")
                    numbers.append(Decimal(normalized))
                item.update(validCount=len(numbers), mean=str(round(sum(numbers) / len(numbers), 4)) if numbers else None,
                            total=str(sum(numbers)), minimum=str(min(numbers)) if numbers else None,
                            maximum=str(max(numbers)) if numbers else None)
            output.append(item)
        return self.computed(table, {"column": column, "mode": mode, "groupBy": group_by,
                                     "filters": filters or {}, "groups": output, "matchedRows": len(rows)})

    def ratio(self, numerator: float, denominator: float, evidence_ids: list[str]) -> dict:
        parents = [self.read(key) for key in evidence_ids]
        if not parents or len(parents) > 12:
            raise AgentError("EVIDENCE_ID", "比例计算需要有效的来源证据")
        if any(source.category == "REFERENCE" for source in parents):
            raise AgentError("RATIO_EVIDENCE", "校本比例计算不能使用参考报告数值")
        a, b = Decimal(str(numerator)), Decimal(str(denominator))
        if not a.is_finite() or not b.is_finite() or a < 0 or b <= 0 or a > b:
            raise AgentError("RATIO_ARGUMENT", "比例计算需满足 0 ≤ 分子 ≤ 分母，且分母大于 0")
        known = set().union(*(numeric_values(source.text) for source in parents))
        if a not in known or b not in known:
            raise AgentError("RATIO_EVIDENCE", "分子与分母必须能在引用证据中找到")
        result = {"numerator": str(a), "denominator": str(b), "ratio": str(round(a / b, 6)),
                  "percentage": str(round(a / b * 100, 2)) + "%"}
        text = json.dumps(result, ensure_ascii=False)
        key = "C-" + sha256((text + "|".join(evidence_ids)).encode()).hexdigest()[:16]
        source = parents[0]
        item = Evidence(id=key, material_id=source.material_id, name=source.name, category=source.category,
                        location="比例计算；来源：" + "；".join(f"{p.name} {p.location}" for p in parents),
                        text=text, computed=True, parent_ids=evidence_ids)
        self.by_id[key] = item
        if not any(e.id == key for e in self.corpus.evidence):
            self.corpus.evidence.append(item)
        return {**result, "evidenceId": key}

    def tools(self):
        def guarded(fn, *args, **kwargs):
            if self.cancel_check:
                self.cancel_check()
            try:
                return fn(*args, **kwargs)
            except AgentError as error:
                return {"error": error.code, "message": error.message}

        @tool
        def list_sources() -> dict:
            """列出本次任务的文件、表格和字段概况。不能访问其他任务资料。"""
            files = {e.material_id: {"materialId": e.material_id, "name": e.name, "category": e.category}
                     for e in self.corpus.evidence if not e.computed}
            return {"materials": list(files.values()), "tables": [{"materialId": t.material_id, "sheet": t.sheet,
                    "columns": t.columns[:60], "columnCount": len(t.columns), "rowCount": len(t.rows)}
                    for t in self.corpus.tables], "warnings": self.corpus.warnings[:20]}

        @tool
        def inspect_data_coverage(material_id: str = '', sheet: str = '', column_query: str = '', offset: int = 0, limit: int = 30) -> dict:
            """分页查看全部Sheet、字段的统计状态、有效值数和计算证据；概览截断不等于缺失。"""
            if not self.inventory:return {'tables':[]}
            return guarded(self.inventory.inspect,material_id,sheet,column_query,offset,limit)

        @tool
        def read_statistics(evidence_id: str, key: str = 'items', offset: int = 0, limit: int = 30, item_index: int = -1) -> dict:
            """分页读取完整计算结果；key=items/rows/detailRows/labels；矩阵可用item_index读取子题。"""
            from .data_inventory import statistics_page
            return guarded(statistics_page,self,evidence_id,key,offset,limit,item_index)

        @tool
        def search_evidence(query: str, category: str = "ALL", limit: int = 6) -> dict:
            """按关键词检索本次证据。category 为 ALL/DATA/SPEECH/REFERENCE；limit 为 1–10。"""
            return guarded(lambda: {"evidence": self.search(query, category, limit)})

        @tool
        def read_evidence(evidence_id: str) -> dict:
            """读取一个本次任务的原始或计算证据 ID，含来源位置。"""
            return guarded(lambda: self.for_model(evidence_id))

        @tool
        def inspect_table(material_id: str, sheet: str, column_query: str = "", offset: int = 0) -> dict:
            """查看本次表格字段。column_query 可按关键词筛选字段；不返回个人身份字段。"""
            def inspect():
                table = self.table(material_id, sheet)
                if offset < 0: raise AgentError("TOOL_ARGUMENT", "字段偏移须为非负数")
                matched = [c for c in table.columns if not column_query or column_query in c]
                columns = matched[offset:offset+80]
                return {"columns": columns, "rowCount": len(table.rows), "columnCount": len(table.columns),
                        "matchedColumns":len(matched), "nextOffset":offset+80 if offset+80<len(matched) else None,
                        "firstDataRow": table.start_row, "evidenceIds": table.source_ids[:3]}
            return guarded(inspect)

        @tool
        def summarize_table(material_id: str, sheet: str, column: str, mode: str = "frequency",
                            group_by: str = "", filters: dict[str, str] | None = None) -> dict:
            """对指定列按 frequency（频数）或 numeric（纯数值）统计；可明确分组列与等值筛选。"""
            return guarded(self.summarize, material_id, sheet, column, mode, group_by, filters)

        @tool
        def calculate_ratio(numerator: float, denominator: float, evidence_ids: list[str]) -> dict:
            """计算有来源的比例。分子分母必须存在于证据，返回可引用的计算证据 ID。"""
            return guarded(self.ratio, numerator, denominator, evidence_ids)

        @tool
        def analyze_survey_question(material_id: str, sheet: str, question: str, scope: str = "") -> dict:
            """读取已按当届原始数据统计的题项：单选频数/百分比、多选拆列0/1、评价矩阵、明确量表均值。
            scope=正在中国境内读研 可排除意向考研；空白不作为否定，不自行解码未知数值，不使用参考报告数字。
            """
            if not self.survey: return {"error":"SURVEY_NOT_READY", "message":"本次资料暂无问卷题项统计"}
            return guarded(self.survey.analyze, material_id, sheet, question, scope)

        tools = [list_sources, inspect_data_coverage, read_statistics, search_evidence, read_evidence,
                 inspect_table, summarize_table, calculate_ratio, analyze_survey_question] + (
                     self.visuals.tools() if self.visuals else [])
        if self.web_tools_provider:
            from .web_tools import web_tools
            tools += web_tools(self, **self.web_tools_provider)
        return tools
