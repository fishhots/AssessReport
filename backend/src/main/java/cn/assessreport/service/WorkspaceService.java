package cn.assessreport.service;

import cn.assessreport.agent.AgentGateway;
import cn.assessreport.auth.UserContext;
import cn.assessreport.domain.Workspace.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;
import org.apache.poi.hwpf.extractor.WordExtractor;
import java.io.IOException;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;

@Service
public class WorkspaceService {
    private static final Set<String> EXTENSIONS = Set.of("xlsx", "xls", "csv", "pdf", "doc", "docx", "txt");
    private final Path root;
    private final ObjectMapper mapper;
    private final CatalogService catalog;
    private final AgentGateway agent;
    private final UserContext users;
    private final Map<String,UserWorkspace> workspaces = new HashMap<>();
    private static class UserWorkspace {
        final Path root;
        State state;
        UserWorkspace(Path root,State state) {this.root=root;this.state=state;}
    }

    public WorkspaceService(@Value("${app.storage-directory}") String directory, ObjectMapper mapper,
            CatalogService catalog, AgentGateway agent, UserContext users) throws IOException {
        this.root = Path.of(directory).toAbsolutePath().normalize();
        this.mapper = mapper;
        this.catalog = catalog;
        this.agent = agent;
        this.users = users;
        Files.createDirectories(root.resolve("users"));
    }
    private synchronized UserWorkspace scope() {
        String id=users.require().id();
        if (!UUID.fromString(id).toString().equals(id)) throw new IllegalArgumentException("用户标识无效");
        return workspaces.computeIfAbsent(id,key -> {
            Path directory=root.resolve("users").resolve(key);
            try {
                Files.createDirectories(directory.resolve("uploads"));
                Path file=directory.resolve("workspace.json");
                return new UserWorkspace(directory,Files.exists(file)?mapper.readValue(file.toFile(),State.class):new State(List.of(),List.of()));
            } catch (IOException e) {throw new java.io.UncheckedIOException(e);}
        });
    }
    public synchronized List<Material> materials() { return scope().state.materials(); }
    public synchronized List<ReportTask> reports() throws IOException {
        if (scope().state.reports().stream().anyMatch(r -> Set.of("QUEUED", "RUNNING", "CANCELLING").contains(r.status()))) {
            try {
                for (AgentGateway.Job job : agent.jobs()) {
                    if (scope().state.reports().stream().anyMatch(r -> r.id().equals(job.taskId()))) update(job);
                }
            } catch (ResponseStatusException unavailable) { /* Keep the last persisted progress. */ }
        }
        return scope().state.reports();
    }
    public synchronized Material material(String id) {
        return scope().state.materials().stream().filter(m -> m.id().equals(id)).findFirst()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资料不存在"));
    }
    public Path filePath(String id) { return scope().root.resolve("uploads").resolve(material(id).id()); }
    public synchronized List<Material> upload(List<MultipartFile> files, Category category) throws IOException {
        if (files.isEmpty() || files.size() > 20) throw new IllegalArgumentException("每次请上传 1 至 20 个文件");
        for (MultipartFile file : files) {
            String name = safeName(file.getOriginalFilename());
            String extension = name.substring(name.lastIndexOf('.') + 1).toLowerCase(Locale.ROOT);
            if (!EXTENSIONS.contains(extension)) throw new IllegalArgumentException("不支持的文件格式：" + name);
            if (file.isEmpty()) throw new IllegalArgumentException("文件不能为空：" + name);
            if (file.getSize() > 50L * 1024 * 1024) throw new IllegalArgumentException("单个文件不能超过 50 MB");
        }
        List<Material> added = new ArrayList<>();
        try {
            for (MultipartFile file : files) {
                Material material = new Material(UUID.randomUUID().toString(), safeName(file.getOriginalFilename()), file.getSize(), category, Instant.now());
                added.add(material);
                file.transferTo(scope().root.resolve("uploads").resolve(material.id()));
            }
            List<Material> all = new ArrayList<>(added);
            all.addAll(scope().state.materials());
            save(new State(List.copyOf(all), scope().state.reports()));
            return List.copyOf(added);
        } catch (IOException | RuntimeException failure) {
            for (Material material : added) {
                try { Files.deleteIfExists(scope().root.resolve("uploads").resolve(material.id())); }
                catch (IOException cleanup) { failure.addSuppressed(cleanup); }
            }
            throw failure;
        }
    }
    public synchronized void delete(String id) throws IOException {
        material(id);
        if (scope().state.reports().stream().anyMatch(r -> r.materialIds().contains(id)))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "该资料已被报告任务引用，暂时无法删除");
        save(new State(scope().state.materials().stream().filter(m -> !m.id().equals(id)).toList(), scope().state.reports()));
        Files.deleteIfExists(scope().root.resolve("uploads").resolve(id));
    }
    public synchronized ReportTask create(ReportInput input) throws IOException {
        catalog.validateModel(input.modelId());
        ReportTemplate template = catalog.template(input.templateId());
        if (!template.chapters().containsAll(input.chapters())) throw new IllegalArgumentException("报告章节必须使用固定模板中的原始名称");
        input.materialIds().forEach(this::material);
        ReportTask report = new ReportTask(UUID.randomUUID().toString(), input.school().trim() + input.year() + "届" + template.name(),
            input.school().trim(), input.year(), input.modelId(), input.templateId(), List.copyOf(new LinkedHashSet<>(input.materialIds())),
            template.chapters().stream().filter(input.chapters()::contains).toList(), Objects.requireNonNullElse(input.instructions(), ""), agent.initialStatus(), Instant.now(), agent.initialSteps(), agent.statusMessage(), input.customTopic());
        List<ReportTask> reports = new ArrayList<>(List.of(report));
        reports.addAll(scope().state.reports());
        save(new State(scope().state.materials(), List.copyOf(reports)));
        return report;
    }
    public synchronized ReportTask report(String id) {
        return scope().state.reports().stream().filter(r -> r.id().equals(id)).findFirst()
            .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "报告任务不存在"));
    }
    public synchronized ReportTask run(String id) throws IOException {
        reports();
        ReportTask report = report(id);
        if (Set.of("QUEUED", "RUNNING", "CANCELLING", "SUCCEEDED").contains(report.status())) return report;
        AgentGateway.Job job;
        if (Set.of("FAILED", "INTERRUPTED", "CANCELLED").contains(report.status())) job = agent.retry(id);
        else {
            List<AgentGateway.Source> sources = new ArrayList<>();
            for (String materialId : report.materialIds()) {
                Material material = material(materialId);
                String text = null;
                if (material.name().toLowerCase(Locale.ROOT).endsWith(".doc")) {
                    try (var input = Files.newInputStream(filePath(materialId)); var extractor = new WordExtractor(input)) {
                        text = extractor.getText();
                        if (text.length() > 8_000_000) throw new IllegalArgumentException("Word 资料内容过大，请拆分后上传");
                    } catch (IOException | RuntimeException invalid) {
                        throw new IllegalArgumentException("无法解析 Word 资料：" + material.name() + "，请转为 DOCX 后上传");
                    }
                }
                sources.add(new AgentGateway.Source(material.id(), material.name(), material.category(), text));
            }
            job = agent.submit(new AgentGateway.Request(report.id(), report.school(), report.year(), report.modelId(), report.templateId(),
                report.chapters(), sources, report.instructions(), report.customTopic()));
        }
        return update(job);
    }
    public synchronized ReportTask cancel(String id) throws IOException {
        reports();
        ReportTask task = report(id);
        if (task.status().equals("CANCELLED")) return task;
        if (!Set.of("QUEUED", "RUNNING", "CANCELLING").contains(task.status()))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "当前任务已经结束，无需终止");
        return update(agent.cancel(id));
    }
    public synchronized void deleteReport(String id) throws IOException {
        reports();
        ReportTask task = report(id);
        if (Set.of("QUEUED", "RUNNING", "CANCELLING").contains(task.status()))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "请先终止任务，等待终止完成后再删除记录");
        if (!Set.of("DRAFT", "AWAITING_AGENT").contains(task.status())) agent.delete(id);
        save(new State(scope().state.materials(), scope().state.reports().stream().filter(r -> !r.id().equals(id)).toList()));
    }
    public synchronized byte[] output(String id, String kind) throws IOException {
        if (!Set.of("report", "diagnosis").contains(kind)) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "输出类型无效");
        reports();
        if (!report(id).status().equals("SUCCEEDED")) throw new ResponseStatusException(HttpStatus.CONFLICT, "报告尚未生成完成");
        return agent.download(id, kind);
    }
    private ReportTask update(AgentGateway.Job job) throws IOException {
        ReportTask before = report(job.taskId());
        if (job.status() == null || job.steps() == null || job.message() == null)
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "智能体返回了无效的任务状态");
        ReportTask after = new ReportTask(before.id(), before.title(), before.school(), before.year(), before.modelId(), before.templateId(),
            before.materialIds(), before.chapters(), before.instructions(), job.status(), before.createdAt(), job.steps(), job.message(), before.customTopic());
        if (!after.equals(before)) save(new State(scope().state.materials(), scope().state.reports().stream().map(r -> r.id().equals(after.id()) ? after : r).toList()));
        return after;
    }
    private void save(State next) throws IOException {
        Path root=scope().root;
        Path temporary = root.resolve("workspace.json.tmp");
        mapper.writerWithDefaultPrettyPrinter().writeValue(temporary.toFile(), next);
        try { Files.move(temporary, root.resolve("workspace.json"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING); }
        catch (AtomicMoveNotSupportedException e) { Files.move(temporary, root.resolve("workspace.json"), StandardCopyOption.REPLACE_EXISTING); }
        scope().state = next;
    }
    private String safeName(String original) {
        if (original == null || original.isBlank()) throw new IllegalArgumentException("文件名不能为空");
        String name = original.replace('\\', '/');
        name = name.substring(name.lastIndexOf('/') + 1).replaceAll("[\\p{Cntrl}]", "");
        if (name.isBlank() || name.length() > 255) throw new IllegalArgumentException("文件名长度不合法");
        return name;
    }
}
