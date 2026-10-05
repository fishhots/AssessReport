"""Task-scoped, source-backed chart data and deterministic rendering."""
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
import json
import math
import re
import threading

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from .errors import AgentError
from .schemas import ContentVisual
from .settings import SERVICE_ROOT

CATALOGUE = json.loads((SERVICE_ROOT / "config/visuals.json").read_text(encoding="utf-8"))
ITEMS = {item["id"]: item for item in CATALOGUE["items"]}
MISSING = {"", "NA", "N/A", "NULL", "NONE", "NAN", "无回答", "未回答", "—", "-"}
RENDER_LOCK = threading.Lock()


class ChartDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str
    material_id: str
    sheet: str
    label_column: str
    label_group_column: str = ""
    value_columns: list[str] = Field(default_factory=list, max_length=10)
    mode: str = "values"  # values, frequency, mean, table
    unit: str = "%"
    basis: str = Field(min_length=1, max_length=500)
    filters: dict[str, str] = Field(default_factory=dict, max_length=12)
    cohort_column: str = ""
    multiple_response_separator: str = Field(default="", max_length=5)
    # Encoded survey answers must be decoded from an actual codebook table.
    codebook_material_id: str = ""
    codebook_sheet: str = ""
    code_column: str = ""
    meaning_column: str = ""


def number(value: str, unit: str) -> float | None:
    value = value.strip()
    if value.upper() in MISSING:
        return None
    normalized = value.replace(",", "").replace("％", "%")
    if normalized.endswith("%"):
        if unit != "%":
            raise AgentError("CHART_UNIT", "百分比字段与指定单位不一致")
        normalized = normalized[:-1]
    elif normalized.endswith(("元", "人", "分")):
        if not normalized.endswith(unit):
            raise AgentError("CHART_UNIT", "数据中的单位与指定单位不一致")
        normalized = normalized[:-1]
    try:
        result = float(normalized)
    except ValueError as exc:
        raise AgentError("CHART_NUMBER", "指标包含未解码选项或非数值，不能直接绘制") from exc
    if not math.isfinite(result) or result < 0 or (unit == "%" and result > 100):
        raise AgentError("CHART_RANGE", "数值超出指标范围，缺失值须保留为空")
    return round(result, 4)


class VisualRegistry:
    def __init__(self, evidence, directory: Path, year: int, chapters: list[str]):
        self.evidence, self.directory, self.year = evidence, directory, year
        self.chapters = chapters
        self.datasets: dict[str, dict] = {}
        self.assets: dict[str, dict] = {}
        self.coverage = {i["id"]: {"templateId": i["id"], "caption": i["caption"],
                         "chapter": i["chapter"], "status": "missing", "reason": "本次尚未取得符合该图表口径的已验证统计结果；需核对字段、分母与指标定义。"}
                         for i in ITEMS.values() if i["chapter"] in chapters}
        self.active_title = ""

    def item(self, key: str) -> dict:
        if key not in self.coverage:
            raise AgentError("CHART_SCOPE", "图表不属于本次勾选章节")
        item = ITEMS[key]
        if self.active_title and item["chapter"] != self.active_title:
            raise AgentError("CHART_SCOPE", "仅能为当前评价报告章节生成图表")
        return item

    def _data_table(self, material_id, sheet):
        table = self.evidence.table(material_id, sheet)
        if not table.source_ids or any(self.evidence.read(key).category != "DATA" for key in table.source_ids):
            raise AgentError("CHART_SOURCE", "校本图表必须来自本次上传的 DATA 数据，不能借用参考报告")
        return table

    def prepare(self, spec: ChartDataRequest) -> dict:
        item = self.item(spec.template_id)
        table = self._data_table(spec.material_id, spec.sheet)
        if spec.mode not in {"values", "frequency", "mean", "table"} or spec.unit not in {"%", "元", "人", "分", "文本"} or (spec.unit == "文本" and spec.mode != "table"):
            raise AgentError("CHART_ARGUMENT", "统计方式或单位无效")
        if (item["kind"] == "table") != (spec.mode == "table"):
            raise AgentError("CHART_ARGUMENT", "表格使用 table 模式，图形使用数值或统计模式")
        names = [spec.label_column, spec.label_group_column, *spec.value_columns, *spec.filters, spec.cohort_column]
        if any(name and name not in table.columns for name in names):
            raise AgentError("TABLE_COLUMN", "字段不存在，请先查看上传表格的实际列名")
        if spec.mode != "table":
            for column in spec.value_columns:
                declared = re.findall(r"[（(]\s*([%％元人分])\s*[）)]", column)
                if declared and declared[0].replace("％", "%") != spec.unit:
                    raise AgentError("CHART_UNIT", "实际字段单位与绘图单位不一致，不能混用")
        descriptor = table.name + " " + table.sheet
        pooled = "合并" in descriptor
        if pooled and (item["type"] == "line" or not item["pooledCohorts"]):
            raise AgentError("CHART_COHORT", "两届合并数据不能用于单届图表或年度趋势")
        source_years = set(map(int, re.findall(r"(?<!\d)(20\d{2})(?!\d)", descriptor)))
        if item["type"] != "line" and not pooled:
            if spec.cohort_column:
                if spec.filters.get(spec.cohort_column) != str(self.year):
                    raise AgentError("CHART_COHORT", "请明确筛选本次报告届次")
            elif source_years != {self.year}:
                raise AgentError("CHART_COHORT", "文件或工作表须明确届次，或指定届次列与筛选条件")
        rows = [row for row in table.rows if all(row[table.columns.index(k)] == v for k, v in spec.filters.items())]
        if not rows:
            raise AgentError("CHART_EMPTY", "筛选后没有有效数据")
        idx = table.columns.index(spec.label_column)
        excluded = 0
        mapping = {}
        mapping_sources = []
        if spec.codebook_material_id:
            codebook = self._data_table(spec.codebook_material_id, spec.codebook_sheet)
            if spec.code_column not in codebook.columns or spec.meaning_column not in codebook.columns:
                raise AgentError("CHART_CODEBOOK", "编码表的编码列或含义列不存在")
            ci, mi = codebook.columns.index(spec.code_column), codebook.columns.index(spec.meaning_column)
            for row in codebook.rows:
                if row[ci] and row[mi]:
                    if row[ci] in mapping and mapping[row[ci]] != row[mi]:
                        raise AgentError("CHART_CODEBOOK", "编码含义存在歧义，请按题目筛选编码表")
                    mapping[row[ci]] = row[mi]
            mapping_sources = codebook.source_ids
        labels, series, native_rows = [], [], []
        benchmarks = []
        missing = 0
        valid_samples = 0
        multi = bool(spec.multiple_response_separator) or item["multipleResponse"]
        if spec.mode == "frequency":
            if spec.unit != "%" or spec.value_columns:
                raise AgentError("CHART_ARGUMENT", "频数模式输出有效回答人数为分母的百分比")
            if item["multipleResponse"] and not spec.multiple_response_separator:
                raise AgentError("CHART_MULTIPLE", "多选字段须指定实际选项分隔符，不能当成单选")
            counts = Counter()
            for row in rows:
                value = row[idx].strip()
                if value.upper() in MISSING:
                    missing += 1
                    continue
                choices = {c.strip() for c in (value.split(spec.multiple_response_separator) if spec.multiple_response_separator else [value]) if c.strip()}
                if not choices:
                    missing += 1
                    continue
                decoded = []
                for choice in choices:
                    if mapping:
                        if choice not in mapping:
                            raise AgentError("CHART_CODEBOOK", "选项编码未能在指定编码表中找到")
                        choice = mapping[choice]
                    elif re.fullmatch(r"\d+", choice):
                        raise AgentError("CHART_CODEBOOK", "数值选项须用上传的编码表解码，不能猜测含义")
                    decoded.append(choice)
                counts.update(set(decoded))
                valid_samples += 1
            if not valid_samples:
                raise AgentError("CHART_EMPTY", "该字段没有有效回答")
            labels = sorted(counts, key=lambda x: (-counts[x], x))
            series = [{"name": spec.label_column, "values": [round(counts[x] / valid_samples * 100, 4) for x in labels]}]
        elif spec.mode == "mean":
            if len(spec.value_columns) != 1:
                raise AgentError("CHART_ARGUMENT", "均值模式需要一个明确的数值指标列")
            column = spec.value_columns[0]
            if spec.unit == "%" or not any(mark in column for mark in {"月收入", "金额", "元", "人数", "得分", "成绩", "（分）", "(分)"}):
                raise AgentError("CHART_CODEBOOK", "均值只支持明确量纲的数值，不能对满意度选项编码求平均")
            groups = defaultdict(list)
            vi = table.columns.index(column)
            for row in rows:
                value = number(row[vi], spec.unit)
                if not row[idx].strip() or value is None:
                    missing += 1
                    continue
                groups[row[idx]].append(value)
                valid_samples += 1
            labels = sorted(groups)
            series = [{"name": column, "values": [round(sum(groups[x]) / len(groups[x]), 4) for x in labels],
                       "sampleCounts": [len(groups[x]) for x in labels]}]
        else:
            if not spec.value_columns or len(set(spec.value_columns)) != len(spec.value_columns):
                raise AgentError("CHART_ARGUMENT", "请指定不重复的实际指标列")
            vi = [table.columns.index(c) for c in spec.value_columns]
            series = [{"name": c, "values": []} for c in spec.value_columns]
            duplicates = Counter(row[idx].strip() for row in rows)
            if spec.mode == "values":
                group_index = table.columns.index(spec.label_group_column) if spec.label_group_column else idx
                averages = [row for row in rows if row[idx] in {"本校平均", "全校平均"} or row[group_index] in {"本校平均", "全校平均"}]
                if len(averages) == 1:
                    benchmarks = [number(averages[0][i], spec.unit) for i in vi]
            for row in rows:
                label = row[idx].strip()
                if not label or label in {"本校平均", "全校平均", "合计", "总计"}:
                    excluded += 1
                    continue
                if duplicates[label] > 1 and spec.label_group_column:
                    group = row[table.columns.index(spec.label_group_column)].strip()
                    if not group:
                        raise AgentError("CHART_DUPLICATE", "同名标签缺少所属分组，不能自行推断")
                    label += "（" + group + "）"
                if label in labels:
                    raise AgentError("CHART_DUPLICATE", "标签重复，请明确分组或增加筛选条件")
                labels.append(label)
                if spec.mode == "table":
                    if any(len(row[i]) > 300 for i in [idx, *vi]):
                        raise AgentError("CHART_TABLE", "表格单元格过长，请筛选简明指标列")
                    native_rows.append([label, *(row[i] or "—" for i in vi)])
                else:
                    for s, i in zip(series, vi):
                        v = number(row[i], spec.unit)
                        missing += v is None
                        s["values"].append(v)
        if not labels or len(labels) > 100 or len(series) > 8:
            raise AgentError("CHART_LIMIT", "没有可绘制标签，或标签/指标数量过多，请增加筛选")
        if item["type"] == "line":
            if spec.mode != "values" or spec.cohort_column != spec.label_column:
                raise AgentError("CHART_COHORT", "趋势图必须使用实际届次列作为横轴")
            if any(not re.fullmatch(r"20\d{2}", label) for label in labels):
                raise AgentError("CHART_COHORT", "趋势横轴须为明确的四位届次")
            observed = list(map(int, labels))
            if len(observed) < 2 or any(y > self.year for y in observed):
                raise AgentError("CHART_COHORT", "趋势至少需要两个有效届次，不能加入未来届次")
            ordered = list(range(min(observed), max(observed) + 1))
            if len(ordered) > 12:
                raise AgentError("CHART_LIMIT", "趋势范围超过十二届，请筛选")
            for s in series:
                old = dict(zip(observed, s["values"]))
                s["values"] = [old.get(y) for y in ordered]
                if sum(v is not None for v in s["values"]) < 2:
                    raise AgentError("CHART_COHORT", "每条趋势线至少需要两个真实数值")
            labels = list(map(str, ordered))
        if item["type"] in {"horizontal_bar", "bar"} and spec.mode != "table":
            order = sorted(range(len(labels)), key=lambda i: (series[0]["values"][i] is None, -(series[0]["values"][i] or 0)))
            labels = [labels[i] for i in order]
            for s in series:
                s["values"] = [s["values"][i] for i in order]
                if "sampleCounts" in s:
                    s["sampleCounts"] = [s["sampleCounts"][i] for i in order]
        data = {"templateId": item["id"], "type": item["type"], "labels": labels, "series": series,
                "cohort": None if pooled or item['type']=='line' else self.year, "pooledCohorts": pooled,
                "benchmarks": benchmarks,
                "unit": spec.unit, "basis": spec.basis, "multipleResponse": multi, "missingCount": missing,
                "matchedRows": len(rows), "validSamples": valid_samples if spec.mode in ('frequency','mean') else None,
                "excludedSummaryRows": excluded,
                "columns": [spec.label_column, *spec.value_columns], "rows": native_rows,
                "binding": spec.model_dump(), "sourceIds": table.source_ids + mapping_sources}
        self._validate_data(data)
        evidence = self.evidence.computed(table, data)
        key = evidence["evidenceId"]
        # Mapping provenance is part of the same computed evidence chain.
        self.evidence.read(key).parent_ids = list(dict.fromkeys(table.source_ids + mapping_sources))
        data["datasetId"] = key
        self.datasets[key] = data
        self.coverage[item["id"]].update(status="prepared", reason="", datasetId=key)
        return self.describe(data)

    @staticmethod
    def _validate_data(data):
        kind = data["type"]
        if kind == "table":
            if len(data["columns"]) > 10 or not data["rows"]:
                raise AgentError("CHART_TABLE", "表格须有数据且不超过十列")
            return
        series = data["series"]
        if not any(v is not None for s in series for v in s["values"]):
            raise AgentError("CHART_EMPTY", "所有指标均缺失，不能生成空图")
        if kind in {"pie", "donut"}:
            values = series[0]["values"]
            if data["multipleResponse"] or len(series) != 1 or data["unit"] != "%" or any(v is None for v in values):
                raise AgentError("CHART_PARTITION", "饼图/环形图只支持完整的互斥百分比，不能绘制多选结果")
            if kind == "donut" and len(values) == 1:
                data["labels"].append("其余")
                values.append(round(100 - values[0], 4))
            if abs(sum(values) - 100) > 0.15 or (kind == "donut" and len(values) != 2):
                raise AgentError("CHART_PARTITION", "构成比例须合计100%，环形指标须有两部分")
        if kind == "stacked_bar":
            if data["multipleResponse"] or data["unit"] != "%" or len(series) < 2:
                raise AgentError("CHART_PARTITION", "堆积图须为同分母的互斥构成比例")
            for i in range(len(data["labels"])):
                values = [s["values"][i] for s in series]
                if any(v is None for v in values) or abs(sum(values) - 100) > 0.2:
                    raise AgentError("CHART_PARTITION", "堆积图每组比例须完整且合计100%")
        if kind in {"scatter", "mirror_bar"} and len(series) != 2:
            raise AgentError("CHART_SERIES", "散点图或双向条形图需要两个同单位指标")
        if kind == "scatter" and not any(a is not None and b is not None for a, b in zip(series[0]["values"], series[1]["values"])):
            raise AgentError("CHART_EMPTY", "散点图没有两个指标同时有效的样本")

    @staticmethod
    def describe(data):
        return {k: data[k] for k in ("datasetId", "templateId", "labels", "series", "benchmarks", "unit", "basis", "missingCount", "validSamples")}

    def render(self, dataset_id: str) -> dict:
        if dataset_id not in self.datasets:
            raise AgentError("CHART_DATASET", "数据集不属于本次任务，请先整理图表数据")
        data = self.datasets[dataset_id]
        item = self.item(data["templateId"])
        self._validate_data(data)
        key = "V-" + sha256((dataset_id + CATALOGUE["version"]).encode()).hexdigest()[:20]
        if key not in self.assets:
            files = []
            if item["kind"] == "figure":
                from .chart_render import draw
                self.directory.mkdir(parents=True, exist_ok=True)
                with RENDER_LOCK:
                    try:
                        files = draw(data, item, self.directory, key)
                    except AgentError:
                        raise
                    except Exception as exc:
                        raise AgentError("CHART_RENDER", "图表绘制失败，请检查数据与中文字体配置") from exc
            caption=item['caption']
            if '合并' in caption and '合并' not in data['basis']:
                caption=re.sub(r'（20\d{2}\s*届[、，]\s*20\d{2}\s*届合并）',f'（{self.year}届）',caption)
            self.assets[key] = {"assetId": key, "templateId": item["id"], "datasetId": dataset_id,
                                "files": files, "caption": caption, "sourceIds": [dataset_id]}
        self.coverage[item["id"]].update(status="rendered", assetId=key, reason="")
        self.flush()
        return {"assetId": key, "templateId": item["id"], "caption": item["caption"], "pageCount": len(self.assets[key]["files"]), "sourceIds": [dataset_id]}

    def validate(self, asset_id: str, template_id: str = "", title: str = "") -> dict:
        if asset_id not in self.assets:
            raise AgentError("CHART_ASSET", "图表资源不属于本次任务")
        asset = self.assets[asset_id]
        item = ITEMS[asset["templateId"]]
        if (template_id and template_id != item["id"]) or (title and title != item["chapter"]):
            raise AgentError("CHART_SCOPE", "图表编号或章节不一致")
        data = self.datasets[asset["datasetId"]]
        source_data = json.loads(self.evidence.read(asset["datasetId"]).text)
        if source_data != {k: v for k, v in data.items() if k != "datasetId"}:
            raise AgentError("CHART_DATA_CHANGED", "图表数据与登记的计算证据不一致")
        self._validate_data(data)
        for file in asset["files"]:
            path = (self.directory / file["filename"]).resolve()
            if not path.is_relative_to(self.directory.resolve()) or not path.is_file() or sha256(path.read_bytes()).hexdigest() != file["sha256"]:
                raise AgentError("CHART_FILE", "图表文件丢失或已被修改")
        return {"valid": True, "assetId": asset_id, "templateId": item["id"], "unit": data["unit"], "sourceIds": asset["sourceIds"]}

    def plan(self):
        for item in ITEMS.values():
            binding = item.get("automaticBinding")
            if not binding or item["id"] not in self.coverage:
                continue
            matches = []
            for table in self.evidence.corpus.tables:
                if not all(c in table.columns for c in [binding["labelColumn"], *binding["valueColumns"]]):
                    continue
                if "合并" in table.name + table.sheet:
                    continue
                # A major sheet also has an 学院名称 column; it is not a faculty aggregate.
                if binding["labelColumn"] == "学院名称" and "专业名称" in table.columns:
                    continue
                if str(self.year) not in table.name + table.sheet:
                    continue
                if all(self.evidence.read(k).category == "DATA" for k in table.source_ids):
                    matches.append(table)
            if len(matches) != 1:
                self.coverage[item["id"]]["reason"] = "缺少同届次的指标表" if not matches else "存在多份同口径数据，需明确采用哪份资料"
                continue
            table = matches[0]
            try:
                self.prepare(ChartDataRequest(template_id=item["id"], material_id=table.material_id, sheet=table.sheet,
                             label_column=binding["labelColumn"], value_columns=binding["valueColumns"],
                             label_group_column="学院名称" if binding["labelColumn"] == "专业名称" and "学院名称" in table.columns else "",
                             unit=binding["unit"], basis=f"{self.year}届毕业生；{table.sheet}中的已聚合指标，原表未列出各指标有效样本数"))
            except AgentError as error:
                self.coverage[item["id"]]["reason"] = error.message
        from .survey_visuals import plan_survey
        plan_survey(self)
        from .report_statistics import plan_report_statistics
        plan_report_statistics(self)
        self.flush()

    def register_statistics(self, template_id, question, labels, series, basis, valid, missing,
                            multiple=False, columns=None, rows=None, kind=None, unit='%'):
        """Program-owned data from a registered, computed question, never a model array."""
        item=self.item(template_id)
        table=self._data_table(question['materialId'],question['sheet'])
        source=self.evidence.read(question['evidenceId'])
        if not source.computed or source.category!='DATA':
            raise AgentError('CHART_SOURCE','图表统计必须来自本次已计算DATA题项')
        data={'templateId':template_id,'type':kind or item['type'],'labels':labels,'series':series,
              'cohort':self.year,'pooledCohorts':False,
              'benchmarks':[],'unit':unit,'basis':basis,'multipleResponse':multiple,'missingCount':missing,
              'matchedRows':question.get('populationCount',len(table.rows)),'validSamples':valid if valid else None,
              'excludedSummaryRows':0,'columns':columns or [],'rows':rows or [],
              'binding':{'questionEvidenceId':source.id},'sourceIds':[source.id]}
        self._validate_data(data)
        result=self.evidence.computed(table,data)
        self.evidence.read(result['evidenceId']).parent_ids=[source.id]
        self.evidence.read(result['evidenceId']).location += '；'+item['caption']
        data['datasetId']=result['evidenceId'];self.datasets[result['evidenceId']]=data
        self.coverage[template_id].update(status='prepared',reason='',datasetId=result['evidenceId'])
        return self.describe(data)

    def context(self, title):
        rows=[]
        for item in ITEMS.values():
            if item['chapter']!=title or item['id'] not in self.coverage:continue
            row={**self.coverage[item['id']],'type':item['type'],'subsection':item.get('subsection',''),
                 'multipleResponse':item['multipleResponse'],'requiredData':item['requiredData']}
            if row.get('datasetId') in self.datasets:
                data=self.datasets[row['datasetId']]
                row['statistics']={'evidenceId':data['datasetId'],'unit':data['unit'],'basis':data['basis'],
                    'labels':data['labels'][:15],'series':[{**s,'values':s['values'][:15],**({'sampleCounts':s['sampleCounts'][:15]} if 'sampleCounts' in s else {})} for s in data['series']],
                    'benchmarks':data['benchmarks'],'labelCount':len(data['labels']),'validSamples':data['validSamples']}
            rows.append(row)
        return rows

    def attach(self, draft):
        selected = {v.template_id: v for v in draft.visuals}
        for row in self.context(draft.title):
            if row["status"] in {"prepared", "rendered"}:
                asset = self.render(row["datasetId"])
                # These resources come from verified task data. The program owns
                # their IDs; a model may mistakenly copy datasetId into asset_id.
                selected[row["templateId"]] = ContentVisual(asset_id=asset["assetId"], template_id=asset["templateId"])
        draft.visuals = list(selected.values())
        draft.visuals.sort(key=lambda v: list(ITEMS).index(v.template_id) if v.template_id in ITEMS else 999)
        return draft

    def flush(self):
        root = self.directory.parent
        root.mkdir(parents=True, exist_ok=True)
        (root / "visual-coverage.json").write_text(json.dumps(list(self.coverage.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "visual-assets.json").write_text(json.dumps({"datasets": self.datasets, "assets": self.assets}, ensure_ascii=False, indent=2), encoding="utf-8")

    def finalize(self, sections):
        selected = {v.template_id: v.asset_id for section in sections for v in section.visuals}
        for key, row in self.coverage.items():
            if key in selected:
                self.validate(selected[key], key, row["chapter"])
                asset = self.assets[selected[key]]
                row.update(status="rendered", assetId=selected[key], datasetId=asset["datasetId"], reason="")
            elif row["status"] != "missing":
                row.update(status="missing", reason="尚未形成可交付的图表")
        self.flush()

    def summary(self):
        rows = list(self.coverage.values())
        return {"expected": len(rows), "rendered": sum(r["status"] == "rendered" for r in rows),
                "missing": sum(r["status"] == "missing" for r in rows), "catalogueVersion": CATALOGUE["version"]}

    def tools(self):
        def guarded(fn, *args):
            try:
                return fn(*args)
            except AgentError as error:
                return {"error": error.code, "message": error.message}

        @tool
        def prepare_chart_data(spec: ChartDataRequest) -> dict:
            """按原报告图表编号，从本次DATA表的实际字段整理图表/表格数据；不能传入自编数值。
            values读取聚合值；frequency计算有效回答比例；mean计算有量纲的均值；table读取原生表格。
            年度趋势须指定实际cohort_column。数值选项须通过实际codebook表解码。返回datasetId及计算证据。
            """
            return guarded(self.prepare, spec)

        @tool
        def render_chart(dataset_id: str) -> dict:
            """用已整理的本任务datasetId生成对应模板图形或可编辑Word表格资源，返回assetId；不能输入路径或数据。"""
            return guarded(self.render, dataset_id)

        @tool
        def validate_chart(asset_id: str) -> dict:
            """校验本任务已生成图表的来源、单位、构成比例和文件完整性；返回可用的资源ID。"""
            return guarded(self.validate, asset_id)

        return [prepare_chart_data, render_chart, validate_chart]
