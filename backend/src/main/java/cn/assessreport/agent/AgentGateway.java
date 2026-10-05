package cn.assessreport.agent;

import cn.assessreport.domain.Workspace.*;
import java.util.List;
import java.util.Map;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.*;

/** Spring Boot 与独立 LangChain 执行服务之间的任务契约。 */
public interface AgentGateway {
    default List<Step> initialSteps() {
        return List.of(new Step("资料上传与任务配置", "COMPLETED"), new Step("资料解析与证据整理", "WAITING"),
            new Step("模板与 Skill 加载", "WAITING"), new Step("培养质量报告撰写", "WAITING"),
            new Step("个性化与主题报告", "WAITING"), new Step("内容校验与修订", "WAITING"), new Step("Word 导出", "WAITING"));
    }
    default String initialStatus() { return "DRAFT"; }
    default String statusMessage() { return "任务已保存，可以开始生成"; }
    @JsonIgnoreProperties(ignoreUnknown = true)
    record Provider(String id, boolean configured, String model) {}
    record ModelConfig(String id, String model, String baseUrl, boolean hasApiKey, String keyMasked,
                       Map<String, Object> extraBody, boolean configured, long revision, String updatedAt,
                       String reasoningMode, String reasoningEffort, Integer thinkingBudget,
                       String channel, String activeChannel) {}
    record ModelConfigInput(@NotBlank @Size(max = 512) String model,
                            @NotBlank @Size(max = 2048) String baseUrl,
                            @Size(max = 4096) String apiKey, Map<String, Object> extraBody,
                            @NotNull @Min(0) Long revision,
                            @Pattern(regexp = "auto|on|off") String reasoningMode,
                            @Pattern(regexp = "low|medium|high") String reasoningEffort,
                            @Min(1024) @Max(16000) Integer thinkingBudget,
                            @Pattern(regexp = "official|zhizengzeng") String channel) {
        public ModelConfigInput {
            extraBody = extraBody == null ? Map.of() : extraBody;
            reasoningMode = reasoningMode == null ? "auto" : reasoningMode;
            reasoningEffort = reasoningEffort == null ? "medium" : reasoningEffort;
            thinkingBudget = thinkingBudget == null ? 4096 : thinkingBudget;
            channel = channel == null ? "official" : channel;
        }
        @Override public String toString() { return "ModelConfigInput[credentials=REDACTED]"; }
    }
    @JsonIgnoreProperties(ignoreUnknown = true)
    record Job(String taskId, String status, List<Step> steps, String message) {}
    record Source(String id, String name, Category category, String extractedText) {}
    record Request(String taskId, String school, int year, String modelId, String templateId,
                   List<String> chapters, List<Source> materials, String instructions, CustomTopic customTopic) {}
    List<Provider> providers();
    List<Job> jobs();
    Job submit(Request request);
    Job retry(String id);
    Job cancel(String id);
    void delete(String id);
    byte[] download(String id, String kind);
    Map<String, Object> testModel(String provider);
    ModelConfig modelConfig(String provider);
    default ModelConfig modelConfig(String provider, String channel) { return modelConfig(provider); }
    ModelConfig saveModelConfig(String provider, ModelConfigInput config);
}
