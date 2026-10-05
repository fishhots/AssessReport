package cn.assessreport.controller;

import cn.assessreport.domain.Workspace.*;
import cn.assessreport.agent.AgentGateway.ModelConfig;
import cn.assessreport.agent.AgentGateway.ModelConfigInput;
import cn.assessreport.service.*;
import jakarta.validation.Valid;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.*;

@RestController
@RequestMapping("/api")
public class WorkspaceController {
    private final WorkspaceService workspace;
    private final CatalogService catalog;
    public WorkspaceController(WorkspaceService workspace, CatalogService catalog) { this.workspace = workspace; this.catalog = catalog; }
    @GetMapping("/health") public Map<String, Object> health() { return Map.of("status", "UP", "agentConnected", catalog.get().agentConnected()); }
    @GetMapping("/catalog") public Catalog catalog() { return catalog.get(); }
    @GetMapping("/materials") public List<Material> materials() { return workspace.materials(); }
    @PostMapping(value = "/materials", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.CREATED)
    public List<Material> upload(@RequestParam List<MultipartFile> files, @RequestParam Category category) throws IOException { return workspace.upload(files, category); }
    @DeleteMapping("/materials/{id}") @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String id) throws IOException { workspace.delete(id); }
    @GetMapping("/materials/{id}/download") public ResponseEntity<Resource> download(@PathVariable String id) {
        Material material = workspace.material(id);
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_OCTET_STREAM)
            .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment().filename(material.name(), StandardCharsets.UTF_8).build().toString())
            .body(new FileSystemResource(workspace.filePath(id)));
    }
    @GetMapping("/reports") public List<ReportTask> reports() throws IOException { return workspace.reports(); }
    @GetMapping("/reports/{id}") public ReportTask report(@PathVariable String id) throws IOException { workspace.reports(); return workspace.report(id); }
    @PostMapping("/reports") @ResponseStatus(HttpStatus.CREATED)
    public ReportTask create(@Valid @RequestBody ReportInput input) throws IOException { return workspace.create(input); }
    @PostMapping("/reports/{id}/run") public ReportTask run(@PathVariable String id) throws IOException { return workspace.run(id); }
    @PostMapping("/reports/{id}/cancel") public ReportTask cancel(@PathVariable String id) throws IOException { return workspace.cancel(id); }
    @DeleteMapping("/reports/{id}") @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteReport(@PathVariable String id) throws IOException { workspace.deleteReport(id); }
    @GetMapping("/reports/{id}/outputs/{kind}") public ResponseEntity<byte[]> output(@PathVariable String id, @PathVariable String kind) throws IOException {
        byte[] content = workspace.output(id, kind);
        ReportTask task = workspace.report(id);
        String title = kind.equals("report") ? "毕业生培养质量评价报告" : "深度诊断框架";
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
            .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment().filename(task.school() + task.year() + "届" + title + ".docx", StandardCharsets.UTF_8).build().toString())
            .body(content);
    }
    @PostMapping("/models/{id}/test") public Map<String, Object> testModel(@PathVariable String id) { return catalog.testModel(id); }
    @GetMapping("/models/{id}/config") public ModelConfig modelConfig(@PathVariable String id, @RequestParam(required = false) String channel) {
        return catalog.modelConfig(id, channel);
    }
    @PutMapping("/models/{id}/config") public ModelConfig saveModelConfig(@PathVariable String id, @Valid @RequestBody ModelConfigInput input) {
        return catalog.saveModelConfig(id, input);
    }
}
