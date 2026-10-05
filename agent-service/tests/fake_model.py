import json
import re
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ToolCallingTestModel(BaseChatModel):
    """离线测试专用：实际执行 LangChain 工具循环，不访问任何供应商。"""
    invalid_numbers: bool = False
    bound_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-test-only"

    def bind_tools(self, tools, **kwargs):
        self.bound_names = [item["function"]["name"] if isinstance(item, dict) else item.name for item in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.bound_names == ["SectionDraft"] and not isinstance(messages[-1], ToolMessage):
            context = "\n".join(str(m.content) for m in messages)
            source_id = re.search(r'"(?:id|evidenceId)":\s*"([EC]-[0-9a-f]+)"', context).group(1)
            title = re.search(r'"title":\s*"([^"]+)"', context).group(1)
            call = {"name": "SectionDraft", "id": str(uuid4()), "args": {
                "title": title, "blocks": [{"kind": "finding",
                "text": f"离线测试就业人数为 {999 if self.invalid_numbers else 80} 人。", "source_ids": [source_id]}]}}
        elif not isinstance(messages[-1], ToolMessage):
            call = {"name": "search_evidence", "args": {"query": "就业", "limit": 2}, "id": str(uuid4())}
        else:
            result = json.loads(messages[-1].content)
            source_id = result["evidence"][0]["id"]
            context = "\n".join(str(m.content) for m in messages if not isinstance(m, ToolMessage))
            title = re.search(r'"title":\s*"([^"]+)"', context).group(1)
            call = {"name": "SectionDraft", "id": str(uuid4()), "args": {
                "title": title,
                "blocks": [{"kind": "finding", "text": f"离线测试数据中的就业人数为 {999 if self.invalid_numbers else 80} 人。", "source_ids": [source_id]},
                           {"kind": "gap", "text": "此为代码流程测试，本章其他校本资料尚未提供。", "source_ids": []}],
                "tables": [],
            }}
        message = AIMessage(content="", tool_calls=[call], usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
        return ChatResult(generations=[ChatGeneration(message=message)])


class ChartToolCallingTestModel(BaseChatModel):
    """Only for tests: exercise the three real chart tools through LangChain."""
    prepared_spec: dict

    @property
    def _llm_type(self):
        return "offline-chart-test-only"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last = messages[-1]
        if not isinstance(last, ToolMessage):
            name, args = "prepare_chart_data", {"spec": self.prepared_spec}
        else:
            result = json.loads(last.content)
            if last.name == "prepare_chart_data":
                name, args = "render_chart", {"dataset_id":result["datasetId"]}
            elif last.name == "render_chart":
                name, args = "validate_chart", {"asset_id":result["assetId"]}
            elif last.name == "validate_chart":
                context = "\n".join(str(m.content) for m in messages if not isinstance(m, ToolMessage))
                title = re.search(r'"title":\s*"([^"]+)"', context).group(1)
                name, args = "SectionDraft", {"title":title,
                    "blocks":[{"kind":"finding", "text":"甲学院的毕业去向落实率为81%。", "source_ids":result["sourceIds"]}],
                    "visuals":[{"asset_id":result["assetId"], "template_id":result["templateId"]}]}
            else:
                raise AssertionError(f"Unexpected tool result: {last.name}")
        call = {"name":name, "args":args, "id":str(uuid4())}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call],
            usage_metadata={"input_tokens":1,"output_tokens":2,"total_tokens":3}))])
