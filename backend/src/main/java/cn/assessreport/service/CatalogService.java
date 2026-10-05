package cn.assessreport.service;

import cn.assessreport.domain.Workspace.*;
import cn.assessreport.agent.AgentGateway;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.stereotype.Service;
import java.util.List;

@Service
public class CatalogService {
    private final AgentGateway agent;
    public CatalogService(AgentGateway agent) { this.agent = agent; }
    private final Catalog catalog = new Catalog(List.of(
        new ModelOption("qwen", "Qwen", "阿里百炼", "通义千问系列", "purple", false),
        new ModelOption("deepseek", "DeepSeek", "深度求索", "DeepSeek 系列", "blue", false),
        new ModelOption("glm", "GLM", "智谱 AI", "GLM 系列", "cyan", false),
        new ModelOption("gpt", "GPT", "OpenAI", "GPT 系列", "green", false),
        new ModelOption("claude", "Claude", "Anthropic", "Claude 系列", "orange", false),
        new ModelOption("gemini", "Gemini", "Google", "Gemini 系列", "pink", false)
    ), List.of(
        new ReportTemplate("graduate", "毕业生培养质量评价报告", "沿用现有报告的章节名称与顺序，根据勾选范围输出报告内容。", "固定模板", List.of(
            "核心发现",
            "第一章 社会需求适应",
            "第二章 就业质量",
            "第三章 升学分析",
            "第四章 五育成效",
            "第五章 教学培养反馈",
            "第六章 满意度评价",
            "第七章 国家级一流本科专业建设点分析",
            "第八章 技术报告"
        ))
    ), false);
    public Catalog get() {
        try {
            var providers = agent.providers();
            var models = catalog.models().stream().map(m -> new ModelOption(m.id(), m.name(), m.provider(), m.description(), m.accent(),
                providers.stream().anyMatch(p -> p.id().equals(m.id()) && p.configured()))).toList();
            return new Catalog(models, catalog.templates(), true);
        } catch (ResponseStatusException unavailable) { return catalog; }
    }
    public java.util.Map<String, Object> testModel(String id) { validateModel(id); return agent.testModel(id); }
    public AgentGateway.ModelConfig modelConfig(String id) { validateModel(id); return agent.modelConfig(id); }
    public AgentGateway.ModelConfig modelConfig(String id, String channel) { validateModel(id); return agent.modelConfig(id, channel); }
    public AgentGateway.ModelConfig saveModelConfig(String id, AgentGateway.ModelConfigInput input) {
        validateModel(id); return agent.saveModelConfig(id, input);
    }
    public ReportTemplate template(String id) {
        return catalog.templates().stream().filter(t -> t.id().equals(id)).findFirst()
                .orElseThrow(() -> new IllegalArgumentException("当前仅支持毕业生培养质量评价报告模板"));
    }
    public void validateModel(String id) {
        if (catalog.models().stream().noneMatch(m -> m.id().equals(id)))
            throw new IllegalArgumentException("请选择有效的大模型");
    }
}
