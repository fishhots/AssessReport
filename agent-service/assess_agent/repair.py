"""Limit content revisions to the blocks and tables that failed validation."""
from dataclasses import dataclass
import re

from .errors import AgentError
from .schemas import ContentBlock, SectionDraft


@dataclass
class RepairScope:
    original: SectionDraft
    blocks: list[int]
    tables: list[int]
    issues: list[str]
    whole: bool = False

    @property
    def evidence_ids(self):
        return list(dict.fromkeys(key for item in [*self.original.blocks, *self.original.tables]
                                  for key in item.source_ids))

    def input(self):
        if self.whole:
            return self.original
        return SectionDraft(title=self.original.title,
            blocks=[self.original.blocks[i] for i in self.blocks] or
                   [ContentBlock(kind="gap", text="本次仅修订表格，此占位段落不会输出。")],
            tables=[self.original.tables[i] for i in self.tables])

    def merge(self, revised: SectionDraft):
        if self.whole:
            return revised
        if revised.title != self.original.title or len(revised.blocks) != max(1, len(self.blocks)) or len(revised.tables) != len(self.tables):
            raise AgentError("MODEL_STRUCTURE", "内容修订未保持指定段落和表格范围，请重试")
        blocks, tables = self.original.blocks.copy(), self.original.tables.copy()
        for index, block in zip(self.blocks, revised.blocks):
            blocks[index] = block
        for index, table in zip(self.tables, revised.tables):
            tables[index] = table
        return self.original.model_copy(update={"blocks": blocks, "tables": tables})


def prepare_repair(draft: SectionDraft, issues: list[str]) -> RepairScope:
    targets = []
    for issue in issues:
        match = re.match(r"第 (\d+) (段|个表格)", issue)
        if not match:
            return RepairScope(draft, [], [], issues, whole=True)
        index, kind = int(match[1]) - 1, match[2]
        limit = len(draft.blocks) if kind == "段" else len(draft.tables)
        if not 0 <= index < limit:
            return RepairScope(draft, [], [], issues, whole=True)
        targets.append((kind, index, issue))
    blocks = sorted({index for kind, index, _ in targets if kind == "段"})
    tables = sorted({index for kind, index, _ in targets if kind == "个表格"})
    remapped = [re.sub(r"^第 \d+", f"第 {(blocks if kind == '段' else tables).index(index) + 1}", issue)
                for kind, index, issue in targets]
    return RepairScope(draft, blocks, tables, remapped)
