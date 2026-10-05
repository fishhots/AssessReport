from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
import zipfile
import warnings

from docx import Document
import pytest

from assess_agent.errors import AgentError
from assess_agent.evidence import EvidenceStore
from assess_agent.exporter import export_document
from assess_agent.models import SectionWriter
from assess_agent.parsers import parse_job
from assess_agent.schemas import Corpus, Evidence, JobRequest, ParsedTable, SectionDraft
from assess_agent.settings import CHAPTERS, Settings
from assess_agent.store import JobStore
from assess_agent.visuals import CATALOGUE, ITEMS, ChartDataRequest, VisualRegistry
from assess_agent.validation import validate_section
from assess_agent.workflow import run_workflow
from fake_model import ToolCallingTestModel, ChartToolCallingTestModel


def registry(tmp_path, columns, rows, category="DATA", filename="2025届测试数据.xlsx", sheet="指标"):
    material = str(uuid4())
    source = Evidence(id="E-test", material_id=material, name=filename, category=category, location="测试行",
                      text="\n".join(["|".join(columns), *("|".join(r) for r in rows)]))
    table = ParsedTable(material_id=material, name=filename, sheet=sheet, columns=columns, rows=rows,
                        source_ids=[source.id], start_row=2)
    store = EvidenceStore(Corpus(evidence=[source], tables=[table]))
    charts = VisualRegistry(store, tmp_path / "visuals", 2025, CHAPTERS)
    store.visuals = charts
    return charts, table


def spec(table, key="figure-2-3", **kwargs):
    return ChartDataRequest(template_id=key, material_id=table.material_id, sheet=table.sheet,
                            label_column=table.columns[0], value_columns=table.columns[1:], basis="测试数据的已聚合指标", **kwargs)


def test_complete_catalogue_preserves_fixed_chapters():
    assert len(ITEMS) == 154
    assert sum(i["kind"] == "figure" for i in ITEMS.values()) == 114
    assert sum(i["kind"] == "table" for i in ITEMS.values()) == 40
    assert all(i["chapter"] in CHAPTERS for i in ITEMS.values())
    assert ITEMS["figure-5-27"]["type"] == "scatter"
    assert ITEMS["figure-4-4"]["type"] == "mirror_bar"


def test_prepared_visual_uses_program_owned_asset_id(tmp_path):
    charts, table = registry(tmp_path, ["学院名称", "专业相关度（%）"], [["甲学院", "80"], ["乙学院", "60"]])
    charts.plan()
    dataset = charts.coverage["figure-2-3"]["datasetId"]
    draft = SectionDraft(title=CHAPTERS[2], blocks=[{"kind": "gap", "text": "缺少其他指标的样本信息。"}],
        visuals=[{"asset_id": dataset, "template_id": "figure-2-3"}])
    charts.attach(draft)
    assert len(draft.visuals) == 1
    assert draft.visuals[0].asset_id.startswith("V-")
    assert charts.validate(draft.visuals[0].asset_id, "figure-2-3", CHAPTERS[2])["valid"]
    assert not validate_section(draft, CHAPTERS[2], charts.evidence, 2025)


@pytest.mark.parametrize("key,columns,rows,kwargs", [
    ("figure-2-3", ["学院名称", "专业相关度（%）"], [["甲学院", "62"], ["乙学院", "NA"], ["丙学院", "80"]], {}),
    ("figure-1-9", ["服务", "比例（%）"], [["简历", "60"], ["面试", "80"]], {}),
    ("figure-1-5", ["去向", "比例（%）"], [["求职", "60"], ["考研", "30"], ["其他", "10"]], {}),
    ("figure-1-6", ["指标", "比例（%）"], [["参加考试", "70"]], {}),
    ("figure-5-6", ["学院", "满意（%）", "不满意（%）"], [["甲学院", "80", "20"], ["乙学院", "60", "40"]], {}),
    ("figure-4-4", ["能力", "重要度（%）", "达成度（%）"], [["沟通", "90", "80"], ["分析", "80", "70"]], {}),
    ("figure-5-27", ["学院", "重要度（%）", "有效性（%）"], [["甲学院", "90", "80"], ["乙学院", "80", "70"], ["丙学院", "85", "76"]], {}),
    ("figure-2-9", ["届次", "月收入（元）"], [["2022", "7000"], ["2024", "8000"], ["2025", "8500"]], {"cohort_column": "届次", "unit": "元"}),
    ("table-1-1", ["地区", "比例（%）"], [["北京", "75"], ["其他", "25"]], {"mode": "table"}),
])
def test_render_types_and_native_table(tmp_path, key, columns, rows, kwargs):
    charts, table = registry(tmp_path, columns, rows)
    prepared = charts.prepare(spec(table, key, **kwargs))
    rendered = charts.render(prepared["datasetId"])
    assert charts.validate(rendered["assetId"])["valid"]
    asset = charts.assets[rendered["assetId"]]
    if ITEMS[key]["kind"] == "figure":
        assert asset["files"]
        assert all((charts.directory / f["filename"]).read_bytes().startswith(b"\x89PNG") for f in asset["files"])
    else:
        assert not asset["files"]
        assert charts.datasets[prepared["datasetId"]]["rows"] == rows
    if ITEMS[key]["type"] == "line":
        assert prepared["labels"] == ["2022", "2023", "2024", "2025"]
        assert prepared["series"][0]["values"][1] is None


def test_missing_stays_missing_and_long_major_chart_is_split(tmp_path):
    rows = [[f"专业{i}", str(60 + i)] for i in range(30)] + [["缺少数据的专业", "NA"]]
    charts, table = registry(tmp_path, ["专业名称", "专业相关度（%）"], rows)
    prepared = charts.prepare(spec(table, "figure-2-4"))
    assert prepared["missingCount"] == 1
    assert prepared["series"][0]["values"][-1] is None
    assert charts.render(prepared["datasetId"])["pageCount"] == 3


@pytest.mark.parametrize("filename,category,key,columns,rows,kwargs,code", [
    ("2025届数据.xlsx", "REFERENCE", "figure-2-3", ["学院", "相关度（%）"], [["甲", "80"]], {}, "CHART_SOURCE"),
    ("2024-2025两届合并.xlsx", "DATA", "figure-2-3", ["学院", "相关度（%）"], [["甲", "80"]], {}, "CHART_COHORT"),
    ("2024届数据.xlsx", "DATA", "figure-2-3", ["学院", "相关度（%）"], [["甲", "80"]], {}, "CHART_COHORT"),
    ("2025届数据.xlsx", "DATA", "figure-2-3", ["学院", "收入（元）"], [["甲", "80"]], {}, "CHART_UNIT"),
    ("2025届数据.xlsx", "DATA", "figure-2-3", ["学院", "相关度（%）"], [["甲", "101"]], {}, "CHART_RANGE"),
    ("2025届数据.xlsx", "DATA", "figure-1-5", ["去向", "比例（%）"], [["甲", "80"], ["乙", "80"]], {}, "CHART_PARTITION"),
    ("2025届数据.xlsx", "DATA", "figure-5-6", ["学院", "满意（%）", "不满意（%）"], [["甲", "80", "30"]], {}, "CHART_PARTITION"),
    ("2025届数据.xlsx", "DATA", "figure-2-9", ["届次", "收入（元）"], [["2025", "8000"]], {"unit":"元", "cohort_column":"届次"}, "CHART_COHORT"),
    ("2025届数据.xlsx", "DATA", "figure-2-3", ["学院", "满意度编码"], [["甲", "4"]], {"mode":"mean", "unit":"分"}, "CHART_CODEBOOK"),
])
def test_rejects_unsafe_statistical_bindings(tmp_path, filename, category, key, columns, rows, kwargs, code):
    charts, table = registry(tmp_path, columns, rows, category, filename)
    with pytest.raises(AgentError) as error:
        charts.prepare(spec(table, key, **kwargs))
    assert error.value.code == code


def test_multiselect_uses_people_not_total_choices(tmp_path):
    charts, table = registry(tmp_path, ["期待"], [["简历;面试;简历"], ["面试"], ["NA"]])
    prepared = charts.prepare(ChartDataRequest(template_id="figure-1-9", material_id=table.material_id,
        sheet=table.sheet, label_column="期待", mode="frequency", basis="有效回答者", multiple_response_separator=";"))
    assert prepared["validSamples"] == 2
    assert prepared["missingCount"] == 1
    assert dict(zip(prepared["labels"], prepared["series"][0]["values"])) == {"面试": 100, "简历": 50}
    # The same data is inappropriate for a partition/pie.
    bad = ChartDataRequest(template_id="figure-1-5", material_id=table.material_id, sheet=table.sheet,
                           label_column="期待", mode="frequency", basis="有效回答者", multiple_response_separator=";")
    with pytest.raises(AgentError) as error:
        charts.prepare(bad)
    assert error.value.code == "CHART_PARTITION"


def test_codebook_decoding_and_provenance(tmp_path):
    charts, table = registry(tmp_path, ["去向"], [["1"], ["2"], ["2"]])
    raw = ChartDataRequest(template_id="figure-1-5", material_id=table.material_id,
                           sheet=table.sheet, label_column="去向", mode="frequency", basis="有效回答者")
    with pytest.raises(AgentError) as error:
        charts.prepare(raw)
    assert error.value.code == "CHART_CODEBOOK"
    source = charts.evidence.corpus.evidence[0].model_copy(update={"id":"E-codebook", "text":"1=求职；2=升学"})
    charts.evidence.by_id[source.id] = source
    charts.evidence.corpus.evidence.append(source)
    charts.evidence.corpus.tables.append(ParsedTable(material_id=table.material_id, name=table.name, sheet="编码表",
        columns=["编码", "含义"], rows=[["1","求职"],["2","升学"]], start_row=2, source_ids=[source.id]))
    decoded = raw.model_copy(update={"codebook_material_id":table.material_id, "codebook_sheet":"编码表", "code_column":"编码", "meaning_column":"含义"})
    data = charts.prepare(decoded)
    assert set(data["labels"]) == {"求职", "升学"}
    assert "E-codebook" in charts.evidence.read(data["datasetId"]).parent_ids


def test_task_scope_and_file_tampering(tmp_path):
    charts, table = registry(tmp_path, ["学院", "相关度（%）"], [["甲", "80"]])
    charts.active_title = CHAPTERS[1]
    with pytest.raises(AgentError):
        charts.prepare(spec(table))
    charts.active_title = CHAPTERS[2]
    prepared = charts.prepare(spec(table))
    asset = charts.render(prepared["datasetId"])
    with pytest.raises(AgentError):
        charts.validate("foreign-asset")
    with pytest.raises(AgentError):
        charts.validate(asset["assetId"], "figure-2-4")
    file = charts.assets[asset["assetId"]]["files"][0]
    (charts.directory / file["filename"]).write_bytes(b"changed")
    with pytest.raises(AgentError) as error:
        charts.validate(asset["assetId"])
    assert error.value.code == "CHART_FILE"


def test_auto_binding_does_not_treat_major_rows_as_faculty(tmp_path):
    charts, table = registry(tmp_path, ["学院名称", "专业名称", "专业相关度（%）"], [["甲学院","计算机","80"], ["","软件工程","90"]])
    charts.plan()
    assert charts.coverage["figure-2-3"]["status"] == "missing"
    assert charts.coverage["figure-2-4"]["status"] == "prepared"
    draft = SectionDraft(title=CHAPTERS[2], blocks=[{"kind":"gap", "text":"仅有本届聚合指标。"}])
    charts.attach(draft)
    assert len(draft.visuals) == 1
    assert validate_section(draft, draft.title, charts.evidence, 2025) == []
    request = JobRequest(task_id=uuid4(), school="测试学校", year=2025, model_id="qwen", chapters=[draft.title],
                         materials=[{"id":table.material_id, "name":table.name, "category":"DATA"}])
    output = tmp_path / "report.docx"
    export_document(request, [draft], charts.evidence, output, "毕业生培养质量评价报告", [draft.title])
    document = Document(output)
    assert len(document.inline_shapes) == 1
    assert "图2-4 各专业工作与专业相关度" in [p.text for p in document.paragraphs]
    with zipfile.ZipFile(output) as archive:
        assert any(name.startswith("word/media/") for name in archive.namelist())


def test_word_table_remains_editable_and_repeats_headers(tmp_path):
    charts, table = registry(tmp_path, ["地区", "比例（%）"], [["北京","80"],["其他","20"]])
    prepared = charts.prepare(spec(table, "table-1-1", mode="table"))
    asset = charts.render(prepared["datasetId"])
    draft = SectionDraft(title=CHAPTERS[1], blocks=[{"kind":"gap","text":"仅有就业地区分布。"}],
                         visuals=[{"asset_id":asset["assetId"], "template_id":asset["templateId"]}])
    request = JobRequest(task_id=uuid4(), school="测试学校", year=2025, model_id="qwen", chapters=[draft.title],
                         materials=[{"id":table.material_id,"name":table.name,"category":"DATA"}])
    output = tmp_path / "table.docx"
    export_document(request, [draft], charts.evidence, output, "毕业生培养质量评价报告", [draft.title])
    document = Document(output)
    assert len(document.tables) == 2 and not document.inline_shapes
    assert document.tables[0].cell(1, 0).text == "北京"
    assert "tblHeader" in document.tables[0].rows[0]._tr.xml


def test_workflow_exports_chart_and_keeps_missing_coverage(tmp_path):
    upload = tmp_path / "uploads"
    upload.mkdir()
    ids = [str(uuid4()), str(uuid4())]
    (upload / ids[0]).write_text("指标,人数\n就业,80\n有效样本,100\n", encoding="utf-8")
    (upload / ids[1]).write_text("学院名称,毕业去向落实率（%）\n甲学院,81\n乙学院,90\n", encoding="utf-8")
    request = JobRequest(task_id=uuid4(), school="测试学校", year=2025, model_id="qwen", chapters=[CHAPTERS[1]],
        materials=[{"id":ids[0],"name":"2025届人数.csv","category":"DATA"}, {"id":ids[1],"name":"2025届学院指标.csv","category":"DATA"}])
    settings = Settings(upload_root=upload, data_root=tmp_path / "agent", update_word_fields=False)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    result = run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel())
    assert result["visuals"]["expected"] == 24
    assert result["visuals"]["rendered"] == 1
    assert result["visuals"]["missing"] == 23
    assert "charts-tables" in result["skillVersions"]
    root = settings.jobs_root() / str(request.task_id) / "attempt-1"
    assert len(Document(root / "report.docx").inline_shapes) == 1
    assert not (root / "diagnosis.docx").exists()
    assert json.loads((root / "visual-coverage.json").read_text(encoding="utf-8"))


def test_three_chart_tools_run_through_langchain(tmp_path):
    charts, table = registry(tmp_path, ["学院名称", "毕业去向落实率（%）"], [["甲学院","81"],["乙学院","90"]])
    request = JobRequest(task_id=uuid4(), school="测试学校", year=2025, model_id="qwen", chapters=[CHAPTERS[1]],
                         materials=[{"id":table.material_id,"name":table.name,"category":"DATA"}])
    model = ChartToolCallingTestModel(prepared_spec=spec(table, "figure-1-3").model_dump())
    writer = SectionWriter(Settings(data_root=tmp_path), request, charts.evidence, model=model)
    draft = writer.write(CHAPTERS[1], "落实率")
    assert len(draft.visuals) == 1
    assert writer.budget.calls == 4
    assert validate_section(draft, draft.title, charts.evidence, 2025) == []


def test_actual_merged_labels_and_same_major_across_colleges(tmp_path):
    from openpyxl import Workbook
    upload = tmp_path / "uploads"
    upload.mkdir()
    material = str(uuid4())
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "各专业主要指标表"
    sheet.append(["学院名称","专业名称","专业相关度（%）"])
    sheet.append(["甲学院","计算机","80"])
    sheet.append([None,"软件工程",None])
    sheet.append(["乙学院","计算机","70"])
    sheet.merge_cells("A2:A3")
    workbook.save(upload / material)
    request = JobRequest(task_id=uuid4(),school="测试",year=2025,model_id="qwen",chapters=[CHAPTERS[2]],
                         materials=[{"id":material,"name":"2025届指标.xlsx","category":"DATA"}])
    store = EvidenceStore(parse_job(request, Settings(upload_root=upload)))
    table = store.corpus.tables[0]
    assert table.rows[1] == ["甲学院","软件工程",""]
    charts = VisualRegistry(store,tmp_path / "visuals",2025,request.chapters)
    charts.plan()
    assert charts.coverage["figure-2-4"]["status"] == "prepared"
    data = charts.datasets[charts.coverage["figure-2-4"]["datasetId"]]
    assert data["labels"] == ["计算机（甲学院）","计算机（乙学院）","软件工程"]
    assert data["series"][0]["values"] == [80,70,None]


def test_chart_dataset_integrity_is_checked_again(tmp_path):
    charts, table = registry(tmp_path,["学院","相关度（%）"],[["甲","80"]])
    data = charts.prepare(spec(table))
    asset = charts.render(data["datasetId"])
    charts.datasets[data["datasetId"]]["series"][0]["values"][0] = 90
    with pytest.raises(AgentError) as error:
        charts.validate(asset["assetId"])
    assert error.value.code == "CHART_DATA_CHANGED"


def test_school_average_is_a_source_backed_benchmark(tmp_path):
    charts, table = registry(tmp_path,["学院名称","专业相关度（%）"],[["本校平均","75.5"],["甲学院","80"],["乙学院","70"]])
    prepared = charts.prepare(spec(table))
    assert prepared["labels"] == ["甲学院","乙学院"]
    assert prepared["benchmarks"] == [75.5]
    assert "75.5" in charts.evidence.read(prepared["datasetId"]).text


def test_all_missing_continuation_is_skipped_without_losing_missing_count(tmp_path):
    rows = [["本校平均","75"]] + [[f"专业{i}","80" if i < 10 else "NA"] for i in range(27)]
    charts, table = registry(tmp_path,["专业名称","专业相关度（%）"],rows)
    data = charts.prepare(spec(table,"figure-2-4"))
    with warnings.catch_warnings(record=True) as captured:
        rendered = charts.render(data["datasetId"])
    assert rendered["pageCount"] == 2 and data['missingCount'] == 17
    assert not any("Tight layout" in str(w.message) for w in captured)
    from PIL import Image
    for file in charts.assets[rendered["assetId"]]["files"]:
        with Image.open(charts.directory/file["filename"]) as image:
            assert image.width < 2000
