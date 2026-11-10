package cn.assessreport.controller;

import cn.assessreport.agent.AgentGateway;
import cn.assessreport.service.WorkspaceService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 联网检索的网页入口：下发定向指令、查看归档、把结果自动登记进当前用户资料库。 */
@RestController
@RequestMapping("/api/web-search")
public class WebSearchController {
    private static final int MAX_SYNC = 30;
    private final AgentGateway agent;
    private final WorkspaceService workspace;

    public WebSearchController(AgentGateway agent, WorkspaceService workspace) {
        this.agent = agent;
        this.workspace = workspace;
    }

    @GetMapping("/status")
    public Map<String, Object> status() { return agent.webSearchStatus(); }

    @PostMapping("/preview")
    public Map<String, Object> preview(@RequestBody Map<String, Object> directive) {
        return agent.webSearchPreview(requireDirective(directive));
    }

    @PostMapping("/test")
    public Map<String, Object> test() { return agent.webSearchTest(); }

    /** 执行检索，并把抓到的网页正文写成资料文件、登记为 Material，供后续任务直接勾选复用。 */
    @PostMapping("/run")
    public Map<String, Object> run(@RequestBody Map<String, Object> directive) throws IOException {
        return materialize(agent.webSearchRun(requireDirective(directive)));
    }

    @GetMapping("/archive")
    public Map<String, Object> archive(@RequestParam(defaultValue = "") String school,
                                       @RequestParam(defaultValue = "") String topic,
                                       @RequestParam(defaultValue = "50") int limit) {
        return agent.webArchive(school, topic, Math.max(1, Math.min(limit, 200)));
    }

    @GetMapping("/archive/{id}")
    public Map<String, Object> entry(@PathVariable String id) { return agent.webArchiveEntry(id); }

    /** 把任务执行中由智能体自行归档、尚未入库的网页批量登记进资料库。 */
    @PostMapping("/archive/sync")
    public Map<String, Object> sync() throws IOException {
        Map<String, Object> pending = agent.webArchivePending(MAX_SYNC);
        Map<String, Object> outcome = new LinkedHashMap<>();
        outcome.put("entries", pending.getOrDefault("entries", List.of()));
        return materialize(outcome);
    }

    @DeleteMapping("/archive/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String id) { agent.webArchiveDelete(id); }

    private Map<String, Object> materialize(Map<String, Object> outcome) throws IOException {
        List<Map<String, Object>> pages = pages(outcome);
        List<Map<String, Object>> fresh = new ArrayList<>();
        for (Map<String, Object> page : pages) {
            Object materialId = page.get("materialId");
            if (materialId == null || materialId.toString().isBlank()) fresh.add(page);
            if (fresh.size() >= MAX_SYNC) break;
        }
        WorkspaceService.ImportResult imported = workspace.importWebPages(fresh);
        if (!imported.receipts().isEmpty()) {
            try {
                agent.webArchiveMaterialized(Map.of("items", imported.receipts()));
            } catch (RuntimeException ignored) {
                // 回执失败不影响已写入的资料文件，下一次同步会重新登记。
            }
        }
        List<Map<String, Object>> brief = new ArrayList<>();
        for (Map<String, Object> page : pages) {
            Map<String, Object> copy = new LinkedHashMap<>(page);
            copy.remove("content");
            brief.add(copy);
        }
        Map<String, Object> response = new LinkedHashMap<>(outcome == null ? Map.of() : outcome);
        response.put("entries", brief);
        response.put("materials", imported.materials());
        response.put("imported", imported.materials().size());
        return response;
    }

    private static List<Map<String, Object>> pages(Map<String, Object> outcome) {
        Object raw = outcome == null ? null : outcome.get("entries");
        if (!(raw instanceof List<?> list)) return List.of();
        List<Map<String, Object>> pages = new ArrayList<>();
        for (Object item : list) {
            if (item instanceof Map<?, ?> map) {
                Map<String, Object> typed = new LinkedHashMap<>();
                map.forEach((key, value) -> typed.put(String.valueOf(key), value));
                pages.add(typed);
            }
        }
        return pages;
    }

    private static Map<String, Object> requireDirective(Map<String, Object> directive) {
        if (directive == null || directive.isEmpty())
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请填写联网检索指令");
        return directive;
    }
}
