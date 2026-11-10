from typing import Literal
from uuid import UUID
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from .settings import CHAPTERS, PROVIDERS
from .web_contracts import WebSearchDirective


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class MaterialInput(ApiModel):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    category: Literal["DATA", "SPEECH", "REFERENCE"]
    extracted_text: str | None = Field(default=None, max_length=8_000_000)


class CustomTopic(ApiModel):
    title: str = Field(min_length=1, max_length=100)
    requirements: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def clean(self):
        self.title, self.requirements = self.title.strip(), self.requirements.strip()
        if not self.title or not self.requirements or any(ord(c) < 32 or ord(c)==127 for c in self.title):
            raise ValueError("自定义主题须填写有效的章节题目和分析要求")
        return self


class JobRequest(ApiModel):
    task_id: UUID
    school: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=2000, le=2100)
    model_id: str
    template_id: Literal["graduate"] = "graduate"
    chapters: list[str] = Field(min_length=1, max_length=9)
    materials: list[MaterialInput] = Field(min_length=1, max_length=100)
    instructions: str = Field(default="", max_length=4000)
    custom_topic: CustomTopic | None = None
    # 结构化联网检索指令。为 None 或 enabled=false 时完全不改变原有取证行为。
    web_search: WebSearchDirective | None = None

    def extension_titles(self) -> list[str]:
        titles = ["第九章 个性化报告"]
        if self.custom_topic:
            titles.append("第十章 " + self.custom_topic.title)
        return titles

    @model_validator(mode="after")
    def validate_contract(self):
        if not self.school.strip() or self.model_id not in PROVIDERS:
            raise ValueError("学校或模型配置无效")
        if any(chapter not in CHAPTERS for chapter in self.chapters):
            raise ValueError("报告章节必须使用原报告名称")
        self.chapters = [chapter for chapter in CHAPTERS if chapter in self.chapters]
        ids = [material.id for material in self.materials]
        if len(ids) != len(set(ids)):
            raise ValueError("资料 ID 不能重复")
        return self


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["finding", "inference", "recommendation", "gap"]
    text: str = Field(min_length=1, max_length=5000)
    source_ids: list[str] = Field(default_factory=list, max_length=15)
    subsection: str = Field(default="", max_length=120)
    # Editorial lead is separate from the numerical evidence paragraph.
    summary: str = Field(default="", max_length=60)

    @model_validator(mode='before')
    @classmethod
    def keep_noneditorial_summary_as_body(cls, value):
        if isinstance(value, dict) and isinstance(value.get('summary'),str) and isinstance(value.get('text'),str):
            summary=value['summary'].strip()
            if len(summary)>60 or re.search(r'[\d%％]',summary) or len(re.findall(r'[。！？]',summary))>1:
                # A presentation defect must not discard a verified paragraph.
                # Move it to prose so all its numerical claims still get checked.
                text=value['text'] if value['text'].startswith(summary) else summary+' '+value['text']
                if len(text)<=5000:
                    return {**value,'summary':'','text':text}
        return value


class ContentTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    columns: list[str] = Field(min_length=1, max_length=10)
    rows: list[list[str]] = Field(min_length=1, max_length=60)
    source_ids: list[str] = Field(min_length=1, max_length=15)
    subsection: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def rectangular(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("表格每行须与表头列数一致")
        if any(len(cell) > 300 for row in self.rows for cell in row):
            raise ValueError("表格单元格过长")
        return self


class ContentVisual(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=80)
    template_id: str = Field(min_length=1, max_length=40)


class SectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    blocks: list[ContentBlock] = Field(min_length=1, max_length=300)
    tables: list[ContentTable] = Field(default_factory=list, max_length=36)
    visuals: list[ContentVisual] = Field(default_factory=list, max_length=60)


class Evidence(ApiModel):
    id: str
    material_id: str
    name: str
    category: Literal["DATA", "SPEECH", "REFERENCE"]
    location: str
    text: str
    computed: bool = False
    parent_ids: list[str] = Field(default_factory=list)


class ParsedTable(ApiModel):
    material_id: str
    name: str
    sheet: str
    columns: list[str]
    rows: list[list[str]]
    start_row: int
    source_ids: list[str]
    # Workbook-scoped opaque joins; never included in evidence or model tools.
    record_keys: list[str] = Field(default_factory=list)


class Corpus(ApiModel):
    evidence: list[Evidence] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
