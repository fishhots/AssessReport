import re
import json
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
import pytest

from assess_agent.evidence import EvidenceStore
from assess_agent.errors import AgentError
from assess_agent.models import MAX_RESEARCH_ROUNDS, SectionWriter
from assess_agent.parsers import parse_job
from assess_agent.settings import CHAPTERS
from tests.test_agent import setup_task


class CompletionTestModel(BaseChatModel):
    mode: str = "loop"
    rounds: int = 0
    bound_names: list[str] = Field(default_factory=list)
    bindings: list[list[str]] = Field(default_factory=list)
    choices: list[str | None] = Field(default_factory=list)
    required_blocks: int = 1

    @property
    def _llm_type(self):
        return "offline-section-completion-test"

    def bind_tools(self, tools, **kwargs):
        self.bound_names = [item["function"]["name"] if isinstance(item, dict) else item.name for item in tools]
        self.bindings.append(self.bound_names.copy())
        self.choices.append(kwargs.get("tool_choice"))
        structured = next((item for item in tools if isinstance(item, dict) and item["function"]["name"] == "SectionDraft"), None)
        if structured:
            self.required_blocks = structured["function"]["parameters"]["properties"]["blocks"].get("minItems", 1)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.rounds += 1
        if self.bound_names == ["SectionDraft"]:
            assert not any(isinstance(message, (AIMessage, ToolMessage)) for message in messages)
        context = "\n".join(str(message.content) for message in messages)
        title = re.search(r'"title":\s*"([^"]+)"', context).group(1)
        if self.mode == "length" and self.rounds == 1:
            message = AIMessage(content="truncated-output", response_metadata={"finish_reason": "length"})
        elif self.mode == "ignore":
            message = AIMessage(content="natural-language-only")
        elif self.mode == "stale" and self.bound_names == ["SectionDraft"]:
            message = AIMessage(content="", tool_calls=[{"name": "search_evidence",
                "args": {"query": "就业", "limit": 2}, "id": str(uuid4())}])
        else:
            structured = self.bound_names == ["SectionDraft"] or self.mode.startswith("invalid")
            if structured:
                invalid = self.mode == "invalid_always" or self.mode == "invalid_once" and self.rounds == 1
                block_count = 1 if self.mode == "wrong_scope_once" and self.rounds == 1 else self.required_blocks
                arguments = {"title": title} if invalid else {"title": title, "blocks": [
                    {"kind": "gap", "text": "本测试用于验证有限取证后完成章节。", "source_ids": []},
                ] * block_count}
                call = {"name": "SectionDraft", "args": arguments, "id": str(uuid4())}
            else:
                query = "就业" if self.mode == "repeat" else f"就业 人数 {self.rounds}"
                call = {"name": "search_evidence", "args": {"query": query, "limit": 2}, "id": str(uuid4())}
            message = AIMessage(content="", tool_calls=[call], response_metadata={"finish_reason": "tool_calls"})
        return ChatResult(generations=[ChatGeneration(message=message)])


def make_writer(tmp_path, mode="loop"):
    settings, request = setup_task(tmp_path)
    model = CompletionTestModel(mode=mode)
    writer = SectionWriter(settings, request, EvidenceStore(parse_job(request, settings)), model=model)
    return writer, model


def test_endless_research_switches_to_schema_only_before_graph_limit(tmp_path):
    writer, model = make_writer(tmp_path)
    result = writer.write(CHAPTERS[1], "就业")
    assert result.title == CHAPTERS[1]
    assert model.rounds == MAX_RESEARCH_ROUNDS + 1
    assert model.bindings[-1] == ["SectionDraft"]
    assert model.choices[-1] == "SectionDraft"
    assert writer.call_trace[-1]["phase"] == "finalize"


def test_duplicate_tool_requests_finish_earlier(tmp_path):
    writer, model = make_writer(tmp_path, "repeat")
    writer.write(CHAPTERS[1], "就业")
    assert model.rounds == 3
    assert model.bindings[-1] == ["SectionDraft"]


def test_invalid_schema_is_repaired_without_repeating_research(tmp_path):
    writer, model = make_writer(tmp_path, "invalid_once")
    result = writer.write(CHAPTERS[1], "就业")
    assert result.blocks and model.rounds == 2
    assert model.bindings[-1] == ["SectionDraft"]


@pytest.mark.parametrize("mode,expected_rounds", [("invalid_always", 3), ("ignore", 3)])
def test_invalid_provider_output_is_bounded_and_does_not_export_fake_content(tmp_path, mode, expected_rounds):
    writer, model = make_writer(tmp_path, mode)
    with pytest.raises(AgentError) as error:
        writer.write(CHAPTERS[1], "就业")
    assert error.value.code == "MODEL_STRUCTURE"
    assert model.rounds == expected_rounds


def test_truncated_output_gets_one_clean_finalization(tmp_path):
    writer, model = make_writer(tmp_path, "length")
    result = writer.write(CHAPTERS[1], "就业")
    assert result.title == CHAPTERS[1] and model.rounds == 2
    assert model.bindings[-1] == ["SectionDraft"]


def test_stale_evidence_calls_during_finalization_are_never_executed(tmp_path):
    writer, model = make_writer(tmp_path, "stale")
    searches = []
    original = writer.evidence.search
    def record_search(*args, **kwargs):
        searches.append(args)
        return original(*args, **kwargs)
    writer.evidence.search = record_search
    with pytest.raises(AgentError) as error:
        writer.write(CHAPTERS[1], "就业")
    assert error.value.code == "MODEL_STRUCTURE"
    assert model.rounds == MAX_RESEARCH_ROUNDS + 3
    # One initial retrieval plus four actual research requests; none from finalization.
    assert len(searches) == MAX_RESEARCH_ROUNDS + 1
    assert model.choices[-3:] == ["SectionDraft"] * 3


def test_content_revision_counts_are_constrained_and_repaired_in_model_loop(tmp_path):
    writer, model = make_writer(tmp_path, "wrong_scope_once")
    draft = {"title": CHAPTERS[1], "blocks": [
        {"kind": "gap", "text": "待修订甲", "source_ids": []},
        {"kind": "gap", "text": "待修订乙", "source_ids": []}], "tables": []}
    result = writer.write(CHAPTERS[1], "就业", issues=["本次修订两个段落"], draft=draft)
    assert len(result.blocks) == 2 and not result.tables
    assert model.rounds == 2 and model.required_blocks == 2


def test_summary_repair_schema_allows_exactly_one_new_analysis_block(tmp_path):
    writer,model=make_writer(tmp_path)
    draft={'title':CHAPTERS[1],'blocks':[{'kind':'recommendation','subsection':'本章小结',
        'text':'完善就业指导。'}],'tables':[]}
    result=writer.write(CHAPTERS[1],'就业',issues=['第 1 段 本章小结必须先有分析段'],draft=draft)
    assert len(result.blocks)==2 and model.required_blocks==2 and model.rounds==1


def test_thinking_summary_repair_adds_analysis_without_expanding_other_content(tmp_path):
    writer,_=make_writer(tmp_path)
    source_id=writer.evidence.search('就业')[0]['id']
    draft={'title':CHAPTERS[1],'blocks':[{'kind':'recommendation','subsection':'本章小结',
        'text':'完善就业指导。'}],'tables':[]}
    class Recorder:
        def __init__(self,reply):self.reply,self.requests=reply,[]
        def invoke(self,messages):
            self.requests.append(messages)
            return AIMessage(content=self.reply,
                usage_metadata={'input_tokens':10,'output_tokens':20,'total_tokens':30})
    writer.analysis_model=Recorder('根据已有就业结果概括本章。')
    composer=Recorder(json.dumps({'title':CHAPTERS[1],'blocks':[
        {'kind':'finding','subsection':'本章小结','text':'就业人数80人。','source_ids':[source_id]},
        draft['blocks'][0]]},ensure_ascii=False))
    writer.model=composer
    result=writer.write(CHAPTERS[1],'就业',issues=['第 1 段 本章小结必须先有分析段'],draft=draft)
    assert len(result.blocks)==2 and [b.kind for b in result.blocks]==['finding','recommendation']
    schema=json.loads(composer.requests[0][-1].content.split('\n',1)[1])
    assert schema['properties']['blocks']['minItems']==schema['properties']['blocks']['maxItems']==2
    assert len(composer.requests)==1


def test_reference_ids_and_figure_numbers_do_not_become_statistical_values(tmp_path):
    from assess_agent.evidence import numeric_values
    from assess_agent.schemas import SectionDraft
    from assess_agent.validation import validate_section
    assert numeric_values("2025届就业人数80人，见图1-3、表2-1（E-073873e3c65cf7a8）") == {2025, 80}
    assert numeric_values("80人，V-baba31c1ab64f1cb6fcc") == {80}
    writer, _ = make_writer(tmp_path)
    source = writer.evidence.search("就业")[0]["id"]
    draft = SectionDraft(title=CHAPTERS[1], blocks=[{"kind": "finding",
        "text": f"就业人数80人，见图1-3（{source}）。", "source_ids": [source]}])
    assert not validate_section(draft, CHAPTERS[1], writer.evidence, 2025)
    draft.blocks[0].text = f"就业人数999人，见图1-3（{source}）。"
    assert validate_section(draft, CHAPTERS[1], writer.evidence, 2025)
