package cn.assessreport.domain;

import jakarta.validation.constraints.*;
import jakarta.validation.Valid;
import java.time.Instant;
import java.util.List;

public final class Workspace {
    private Workspace() {}
    public enum Category { DATA, SPEECH, REFERENCE }
    public record Material(String id, String name, long size, Category category, Instant createdAt) {}
    public record ModelOption(String id, String name, String provider, String description, String accent, boolean configured) {}
    public record ReportTemplate(String id, String name, String description, String tag, List<String> chapters) {}
    public record Catalog(List<ModelOption> models, List<ReportTemplate> templates, boolean agentConnected) {}
    public record Step(String name, String status) {}
    public record ReportTask(String id, String title, String school, int year, String modelId,
            String templateId, List<String> materialIds, List<String> chapters, String instructions,
            String status, Instant createdAt, List<Step> steps, String message, CustomTopic customTopic) {}
    public record CustomTopic(@NotBlank @Size(max = 100) @Pattern(regexp = "[^\\p{Cntrl}]+") String title,
                              @NotBlank @Size(max = 4000) String requirements) {
        public CustomTopic { title = title == null ? null : title.trim(); requirements = requirements == null ? null : requirements.trim(); }
    }
    public record ReportInput(
            @NotBlank @Size(max = 100) String school,
            @Min(2000) @Max(2100) int year,
            @NotBlank String modelId,
            @NotBlank String templateId,
            @NotEmpty @Size(max = 100) List<@NotBlank String> materialIds,
            @NotEmpty @Size(max = 20) List<@NotBlank String> chapters,
            @Size(max = 4000) String instructions, @Valid CustomTopic customTopic) {}
    public record State(List<Material> materials, List<ReportTask> reports) {}
}
