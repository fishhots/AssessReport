import pytest

from assess_agent.evidence import EvidenceStore
from assess_agent.errors import AgentError
from assess_agent.schemas import Corpus, Evidence, SectionDraft
from assess_agent.validation import validate_section


def test_reference_numbers_cannot_be_laundered_through_mixed_citations():
    store = EvidenceStore(Corpus(evidence=[
        Evidence(id="data", material_id="local", name="校本数据", category="DATA", location="数据行", text="就业人数 80，有效样本 100"),
        Evidence(id="reference", material_id="example", name="参考报告", category="REFERENCE", location="样例正文", text="示例人数 999，样本 1000"),
    ]))
    draft = SectionDraft(title="第二章 就业质量", blocks=[
        {"kind": "finding", "text": "本校就业人数为 999 人。", "source_ids": ["data", "reference"]}],
        tables=[{"title": "就业人数", "columns": ["人数"], "rows": [["999"]], "source_ids": ["reference"]}])
    issues = validate_section(draft, draft.title, store, 2025)
    assert any("数字" in issue for issue in issues)
    assert any("表格" in issue for issue in issues)
    with pytest.raises(AgentError) as error:
        store.ratio(999, 1000, ["data", "reference"])
    assert error.value.code == "RATIO_EVIDENCE"
