import pytest

from assess_agent.errors import AgentError
from assess_agent.repair import prepare_repair
from assess_agent.schemas import SectionDraft


def original():
    return SectionDraft(title="第二章 就业质量", blocks=[
        {"kind": "finding", "text": "已通过内容", "source_ids": ["first"]},
        {"kind": "inference", "text": "需修订的近似数值", "source_ids": ["second"]}],
        tables=[{"title": "指标", "columns": ["人数"], "rows": [["999"]], "source_ids": ["table"]}],
        visuals=[{"template_id": "figure-2-3", "asset_id": "verified-asset"}])


def test_only_failed_blocks_are_revised_and_other_content_survives():
    draft = original()
    scope = prepare_repair(draft, ["第 2 段以下数字未能在对应引用中找到：50"])
    assert scope.input().blocks == [draft.blocks[1]]
    assert scope.issues[0].startswith("第 1 段")
    assert scope.evidence_ids == ["first", "second", "table"]
    revised = SectionDraft(title=draft.title, blocks=[{"kind": "inference", "text": "已依据证据改为定性判断", "source_ids": ["second"]}])
    result = scope.merge(revised)
    assert result.blocks[0] == draft.blocks[0]
    assert result.blocks[1] == revised.blocks[0]
    assert result.tables == draft.tables and result.visuals == draft.visuals


def test_table_only_revision_does_not_export_placeholder_block():
    draft = original()
    scope = prepare_repair(draft, ["第 1 个表格以下数值缺少对应原始或计算证据：999"])
    revised = scope.input()
    revised.tables[0] = revised.tables[0].model_copy(update={"rows": [["80"]]})
    result = scope.merge(revised)
    assert result.blocks == draft.blocks
    assert result.tables[0].rows == [["80"]]


def test_unexpected_revision_scope_is_rejected():
    scope = prepare_repair(original(), ["第 2 段事实或判断缺少引用"])
    with pytest.raises(AgentError) as error:
        scope.merge(original())
    assert error.value.code == "MODEL_STRUCTURE"


def test_title_and_visual_issues_fall_back_to_whole_section_revision():
    draft = original()
    scope = prepare_repair(draft, ["标题须逐字等于：第二章 就业质量"])
    assert scope.whole and scope.input() == draft
