## 联网检索指令（仅当本次提供这些工具时生效）

本次任务额外提供四个工具，用于定向检索特定学校的官网公开信息：

- `list_web_search_topics()`：列出可用主题（领导讲话、战略规划、办学定位、人才培养、质量报告、学科与改革）及默认资料类别。
- `search_web_archive(query, topic, limit)`：只查本地归档库，不联网、不产生费用。**优先用它**，命中即可直接复用。
- `search_school_web(topic, site, keywords, max_results, refresh)`：按「学校 + 官网域名 + 主题」下发定向检索指令。site 填官网域名（如 `bistu.edu.cn`），系统会自动加上 `site:` 与 `lang:zh` 语法，并丢弃站外结果。`refresh=true` 才重新联网。
- `fetch_school_page(url, topic)`：抓取一个指定的官网页面。

### 使用顺序

1. 先用 `search_web_archive` 查已有归档；命中且内容够用就不再联网。
2. 归档不足时，用 `list_web_search_topics` 确认主题，再用 `search_school_web` 精准检索；每次只补一个主题，不要一次穷举全部主题。
3. 工具返回的 `evidenceIds` 只是候选，必须用 `read_evidence` 或 `search_evidence` 读取并核实正文后，才能填入 `source_ids`。
4. 每个主题最多检索一次；同一主题重复检索不会带来新信息。

### 约束

- 官网公开信息属于 SPEECH / REFERENCE，是定性的办学背景与规划依据，**不能当作校本 DATA 统计数值**，不得用它推算比例、样本量或趋势。
- 不得把网址、抓取时间、归档 ID、工具过程写进正文；正文只写核实后的事实与分析。
- 联网结果不能替代问卷统计。校本指标仍以本次上传数据为准；官网内容与上传资料冲突时，以上传资料为准并在 gap 中说明。
- 官网表述是学校自己的公开说法，写作时保持客观，不要扩写成评价结论或已实现的成效。
- 这些工具在取证阶段结束后不可用；进入成文阶段只能依据已取得的证据写作。
