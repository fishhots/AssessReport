package cn.assessreport;

import cn.assessreport.service.WorkspaceService;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest
@AutoConfigureMockMvc
class AgentIntegrationTest extends AuthenticatedApiTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Map<String, Map<String, Object>> JOBS = new ConcurrentHashMap<>();
    private static final AtomicInteger SUBMISSIONS = new AtomicInteger();
    private static final AtomicBoolean REJECT = new AtomicBoolean();
    private static final HttpServer SERVER;
    private static final Path STORAGE;
    private static volatile Map<?, ?> lastRequest;
    private static volatile String lastWorkspaceUser;
    private static final Map<String, Map<String, Object>> MODEL_CONFIGS = new ConcurrentHashMap<>();
    private static volatile Map<?, ?> lastModelInput;
    private static volatile String lastModelQuery;
    private static volatile String modelTestErrorCode = "MODEL_ERROR";
    static {
        try {
            STORAGE = Files.createTempDirectory("assess-agent-integration-");
            SERVER = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            SERVER.createContext("/", exchange -> {
                byte[] bytes;
                int status = 200;
                String path = exchange.getRequestURI().getPath();
                lastWorkspaceUser = exchange.getRequestHeaders().getFirst("X-Workspace-User");
                if (!"integration-token".equals(exchange.getRequestHeaders().getFirst("X-Agent-Token"))) {
                    status = 401; bytes = JSON.writeValueAsBytes(Map.of("code", "UNAUTHORIZED"));
                } else if (path.equals("/models")) {
                    bytes = JSON.writeValueAsBytes(List.of(Map.of("id", "qwen", "configured", true, "model", "offline-stub")));
                } else if (path.startsWith("/models/") && path.endsWith("/test")) {
                    status = 422;
                    bytes = JSON.writeValueAsBytes(Map.of("code", modelTestErrorCode, "message", "private-key-should-never-appear"));
                } else if (path.startsWith("/models/") && path.endsWith("/config")) {
                    lastModelQuery = exchange.getRequestURI().getRawQuery();
                    String provider = path.split("/")[2];
                    var current = MODEL_CONFIGS.getOrDefault(provider, Map.of("id", provider, "model", "", "baseUrl", "", "hasApiKey", false,
                        "keyMasked", "", "extraBody", Map.of(), "configured", false, "revision", 0, "updatedAt", ""));
                    if (exchange.getRequestMethod().equals("PUT")) {
                        lastModelInput = JSON.readValue(exchange.getRequestBody(), Map.class);
                        if (!lastModelInput.get("revision").toString().equals(current.get("revision").toString())) {
                            status = 409;
                        } else {
                            current = Map.of("id", provider, "model", lastModelInput.get("model"), "baseUrl", lastModelInput.get("baseUrl"),
                                "hasApiKey", true, "keyMasked", "••••••••", "extraBody", lastModelInput.get("extraBody"),
                                "configured", true, "revision", Integer.parseInt(current.get("revision").toString()) + 1, "updatedAt", "2026-10-01T00:00:00Z");
                            var withChannel = new java.util.HashMap<String, Object>(current);
                            withChannel.put("channel", lastModelInput.get("channel"));
                            withChannel.put("activeChannel", lastModelInput.get("channel"));
                            current = Map.copyOf(withChannel);
                            MODEL_CONFIGS.put(provider, current);
                        }
                    }
                    bytes = JSON.writeValueAsBytes(status == 409 ? Map.of("code", "MODEL_CONFIG_CONFLICT", "message", "private-key-should-never-appear") : current);
                } else if (path.equals("/jobs") && exchange.getRequestMethod().equals("POST")) {
                    if (REJECT.get()) {
                        status = 422; bytes = JSON.writeValueAsBytes(Map.of("code", "MODEL_NOT_CONFIGURED", "message", "private-key-should-never-appear"));
                    } else {
                        lastRequest = JSON.readValue(exchange.getRequestBody(), Map.class);
                        String id = lastRequest.get("taskId").toString();
                        JOBS.put(id, job(id, "QUEUED")); SUBMISSIONS.incrementAndGet();
                        status = 202; bytes = JSON.writeValueAsBytes(JOBS.get(id));
                    }
                } else if (path.equals("/jobs")) {
                    bytes = JSON.writeValueAsBytes(JOBS.values());
                } else if (path.endsWith("/retry")) {
                    String id = path.split("/")[2];
                    JOBS.put(id, job(id, "QUEUED")); status = 202; bytes = JSON.writeValueAsBytes(JOBS.get(id));
                } else if (path.endsWith("/cancel")) {
                    String id = path.split("/")[2];
                    String current = JOBS.get(id).get("status").toString();
                    JOBS.put(id, job(id, current.equals("QUEUED") ? "CANCELLED" : "CANCELLING"));
                    bytes = JSON.writeValueAsBytes(JOBS.get(id));
                } else if (path.startsWith("/jobs/") && exchange.getRequestMethod().equals("DELETE")) {
                    JOBS.remove(path.split("/")[2]); status = 204; bytes = new byte[0];
                } else if (path.contains("/artifacts/")) {
                    bytes = ("test-binary-" + path.substring(path.lastIndexOf('/') + 1)).getBytes(StandardCharsets.UTF_8);
                } else { status = 404; bytes = new byte[0]; }
                exchange.getResponseHeaders().set("Content-Type", path.contains("/artifacts/") ? "application/octet-stream" : "application/json");
                exchange.sendResponseHeaders(status, status == 204 ? -1 : bytes.length);
                try (var output = exchange.getResponseBody()) { output.write(bytes); }
            });
            SERVER.start();
        } catch (Exception e) { throw new ExceptionInInitializerError(e); }
    }
    private static Map<String, Object> job(String id, String status) {
        return Map.of("taskId", id, "status", status, "steps", List.of(Map.of("name", "Word 导出", "status", status.equals("SUCCEEDED") ? "COMPLETED" : "WAITING")), "message", "执行状态", "attempt", 1, "result", Map.of());
    }
    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("app.storage-directory", () -> STORAGE.toString());
        registry.add("app.agent.base-url", () -> "http://127.0.0.1:" + SERVER.getAddress().getPort());
        registry.add("app.agent.token", () -> "integration-token");
    }
    @AfterAll static void stopServer() { SERVER.stop(0); }
    @Autowired ObjectMapper mapper;
    @Autowired WorkspaceService workspace;
    @Autowired cn.assessreport.agent.AgentGateway gateway;
    @Autowired cn.assessreport.service.CatalogService catalog;

    private WorkspaceService ownWorkspace() { users.set(testUser); return workspace; }

    private String createTask() throws Exception {
        var upload = mvc.perform(multipart("/api/materials").file(new MockMultipartFile("files", "test.csv", "text/csv", "指标,人数\n就业,80\n".getBytes(StandardCharsets.UTF_8))).param("category", "DATA"))
            .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String materialId = mapper.readTree(upload).get(0).path("id").asText();
        var input = Map.of("school", "接口测试学校", "year", 2025, "modelId", "qwen", "templateId", "graduate", "materialIds", List.of(materialId), "chapters", List.of("第二章 就业质量"));
        var report = mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(input)))
            .andExpect(status().isCreated()).andExpect(jsonPath("$.status").value("DRAFT")).andReturn().getResponse().getContentAsString();
        return mapper.readTree(report).path("id").asText();
    }
    @Test void submitProgressRetryAndTwoDownloads() throws Exception {
        String id = createTask();
        mvc.perform(get("/api/catalog")).andExpect(jsonPath("$.agentConnected").value(true)).andExpect(jsonPath("$.models[0].configured").value(true));
        mvc.perform(get("/api/reports/" + id + "/outputs/report")).andExpect(status().isConflict());
        mvc.perform(post("/api/reports/" + id + "/run")).andExpect(status().isOk()).andExpect(jsonPath("$.status").value("QUEUED"));
        assertEquals(testUser.id(), lastWorkspaceUser);
        int submitted = SUBMISSIONS.get();
        mvc.perform(post("/api/reports/" + id + "/run")).andExpect(status().isOk());
        assertEquals(submitted, SUBMISSIONS.get());
        assertEquals(List.of("第二章 就业质量"), lastRequest.get("chapters"));
        assertFalse(mapper.writeValueAsString(lastRequest).contains(STORAGE.toString()));
        JOBS.put(id, job(id, "FAILED"));
        mvc.perform(get("/api/reports/" + id)).andExpect(jsonPath("$.status").value("FAILED"));
        mvc.perform(post("/api/reports/" + id + "/run")).andExpect(jsonPath("$.status").value("QUEUED"));
        JOBS.put(id, job(id, "SUCCEEDED"));
        mvc.perform(get("/api/reports/" + id)).andExpect(jsonPath("$.status").value("SUCCEEDED"));
        for (String kind : List.of("report", "diagnosis")) {
            mvc.perform(get("/api/reports/" + id + "/outputs/" + kind)).andExpect(status().isOk())
                .andExpect(content().bytes(("test-binary-" + kind).getBytes(StandardCharsets.UTF_8)))
                .andExpect(header().string("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"));
        }
        mvc.perform(get("/api/reports/" + id + "/outputs/unknown")).andExpect(status().isNotFound());
        mvc.perform(post("/api/reports/unknown/run")).andExpect(status().isNotFound());
        assertEquals("SUCCEEDED", ownWorkspace().report(id).status());
    }
    @Test void rejectedSubmissionPreservesDraftAndSanitizesErrors() throws Exception {
        String id = createTask(); REJECT.set(true);
        try {
            var response = mvc.perform(post("/api/reports/" + id + "/run")).andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.message").value("所选模型尚未配置，请在模型设置中填写配置信息")).andReturn().getResponse().getContentAsString();
            assertFalse(response.contains("private-key"));
            assertEquals("DRAFT", ownWorkspace().report(id).status());
        } finally { REJECT.set(false); }
    }
    @Test void cancelRetryAndDeletePreserveUploadedMaterials() throws Exception {
        String id = createTask();
        String materialId = ownWorkspace().report(id).materialIds().get(0);
        mvc.perform(post("/api/reports/" + id + "/run")).andExpect(jsonPath("$.status").value("QUEUED"));
        mvc.perform(delete("/api/reports/" + id)).andExpect(status().isConflict());
        mvc.perform(post("/api/reports/" + id + "/cancel")).andExpect(jsonPath("$.status").value("CANCELLED"));
        mvc.perform(post("/api/reports/" + id + "/run")).andExpect(jsonPath("$.status").value("QUEUED"));
        JOBS.put(id, job(id, "RUNNING"));
        mvc.perform(post("/api/reports/" + id + "/cancel")).andExpect(jsonPath("$.status").value("CANCELLING"));
        mvc.perform(delete("/api/reports/" + id)).andExpect(status().isConflict());
        JOBS.put(id, job(id, "CANCELLED"));
        mvc.perform(delete("/api/reports/" + id)).andExpect(status().isNoContent());
        mvc.perform(get("/api/reports/" + id)).andExpect(status().isNotFound());
        assertFalse(JOBS.containsKey(id));
        assertTrue(Files.exists(ownWorkspace().filePath(materialId)));
        assertNotNull(ownWorkspace().material(materialId));
        var reloaded = new WorkspaceService(STORAGE.toString(), mapper, catalog, gateway, users);
        assertThrows(org.springframework.web.server.ResponseStatusException.class, () -> reloaded.report(id));
        assertNotNull(reloaded.material(materialId));
    }
    @Test void modelTestDistinguishesNetworkParametersAndQuota() throws Exception {
        var cases = Map.of(
            "MODEL_QUOTA", "模型 API 余额不足或使用额度已用尽，请检查模型平台的余额和额度",
            "MODEL_RATE_LIMIT", "模型调用已限流，请稍后重试",
            "MODEL_BAD_REQUEST", "模型接口拒绝了请求参数，请检查模型名称、接口地址和额外参数",
            "MODEL_THINKING_CONFIG", "模型接口的思考模式或工具协议不兼容，请检查接口支持的思考参数，或在模型设置中关闭额外深度分析后保存并测试",
            "MODEL_NOT_FOUND", "模型或接口不存在，请检查模型名称和接口基础地址",
            "MODEL_CONNECTION", "无法连接模型接口，请检查网络、代理和接口地址",
            "MODEL_UNAVAILABLE", "模型平台服务暂时不可用，请稍后重试",
            "MODEL_STRUCTURE", "模型未通过工具调用与结构化输出测试，请重试或更换模型",
            "MODEL_ERROR", "模型调用失败，请重试；若持续失败，请查看智能体服务日志"
        );
        try {
            for (var entry : cases.entrySet()) {
                modelTestErrorCode = entry.getKey();
                var response = mvc.perform(post("/api/models/deepseek/test"))
                    .andExpect(status().isUnprocessableEntity()).andExpect(jsonPath("$.message").value(entry.getValue()))
                    .andReturn().getResponse().getContentAsString();
                assertFalse(response.contains("private-key"));
            }
        } finally { modelTestErrorCode = "MODEL_ERROR"; }
    }
    @Test void legacyWordBridgeExtractsOriginalSampleLocally() throws Exception {
        Path sample = Path.of("../Doc/北京信息科技大学2025届毕业生培养质量评价报告.doc");
        org.junit.jupiter.api.Assumptions.assumeTrue(Files.isRegularFile(sample));
        var uploaded = mvc.perform(multipart("/api/materials").file(new MockMultipartFile("files", "sample.doc", "application/msword", Files.readAllBytes(sample))).param("category", "REFERENCE"))
            .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String materialId = mapper.readTree(uploaded).get(0).path("id").asText();
        var input = Map.of("school", "桥接测试学校", "year", 2025, "modelId", "qwen", "templateId", "graduate", "materialIds", List.of(materialId), "chapters", List.of("第二章 就业质量"));
        var report = mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(input)))
            .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String id = mapper.readTree(report).path("id").asText();
        mvc.perform(post("/api/reports/" + id + "/run")).andExpect(status().isOk());
        Map<?, ?> source = (Map<?, ?>)((List<?>)lastRequest.get("materials")).get(0);
        String text = source.get("extractedText").toString();
        assertTrue(text.length() > 10000);
        assertTrue(text.contains("第二章") && text.contains("就业质量"));
    }
    @Test void relayChannelForwardsThroughSpringWithoutCredentialEcho() throws Exception {
        mvc.perform(get("/api/models/gpt/config").param("channel", "zhizengzeng"))
            .andExpect(status().isOk());
        assertEquals("channel=zhizengzeng", lastModelQuery);
        var input = Map.of("model", "relay-model", "baseUrl", "https://api.zhizengzeng.com/v1",
            "apiKey", "relay-key-through-spring", "revision", 0, "channel", "zhizengzeng");
        var response = mvc.perform(put("/api/models/gpt/config").contentType(MediaType.APPLICATION_JSON)
                .content(mapper.writeValueAsBytes(input)))
            .andExpect(status().isOk()).andExpect(jsonPath("$.channel").value("zhizengzeng"))
            .andExpect(jsonPath("$.activeChannel").value("zhizengzeng"))
            .andReturn().getResponse().getContentAsString();
        assertEquals("zhizengzeng", lastModelInput.get("channel"));
        assertFalse(response.contains("relay-key-through-spring"));
        mvc.perform(get("/api/models/gpt/config").param("channel", "official")).andExpect(status().isOk());
        assertEquals("channel=official", lastModelQuery);
        mvc.perform(put("/api/models/gpt/config").contentType(MediaType.APPLICATION_JSON)
                .content(mapper.writeValueAsBytes(Map.of("model", "relay-model", "baseUrl", "https://example.invalid",
                    "revision", 1, "channel", "invalid"))))
            .andExpect(status().isBadRequest());
    }
    @Test void editableModelConfigForwardsAndMasksCredentials() throws Exception {
        mvc.perform(get("/api/models/deepseek/config")).andExpect(status().isOk()).andExpect(jsonPath("$.hasApiKey").value(false));
        var input = Map.of("model", "chosen-model", "baseUrl", "https://example.invalid/v1", "apiKey", "test-secret-through-spring", "extraBody", Map.of("enable_thinking", false), "revision", 0,
            "reasoningMode", "on", "reasoningEffort", "high", "thinkingBudget", 8192);
        var response = mvc.perform(put("/api/models/deepseek/config").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(input)))
            .andExpect(status().isOk()).andExpect(jsonPath("$.configured").value(true)).andExpect(jsonPath("$.hasApiKey").value(true))
            .andExpect(jsonPath("$.apiKey").doesNotExist()).andExpect(jsonPath("$.revision").value(1)).andReturn().getResponse().getContentAsString();
        assertEquals("test-secret-through-spring", lastModelInput.get("apiKey"));
        assertEquals("on", lastModelInput.get("reasoningMode"));
        assertEquals("high", lastModelInput.get("reasoningEffort"));
        assertEquals(8192, lastModelInput.get("thinkingBudget"));
        assertFalse(response.contains("test-secret"));
        mvc.perform(get("/api/models/deepseek/config")).andExpect(jsonPath("$.model").value("chosen-model"));
        var changed = Map.of("model", "changed-model", "baseUrl", "https://other.invalid/v1", "revision", 1);
        mvc.perform(put("/api/models/deepseek/config").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(changed)))
            .andExpect(status().isOk()).andExpect(jsonPath("$.revision").value(2));
        assertNull(lastModelInput.get("apiKey"));
        assertEquals(Map.of(), lastModelInput.get("extraBody"));
        var conflict = mvc.perform(put("/api/models/deepseek/config").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(changed)))
            .andExpect(status().isConflict()).andExpect(jsonPath("$.message").value("配置已更新，请重新打开后修改"))
            .andReturn().getResponse().getContentAsString();
        assertFalse(conflict.contains("private-key"));
        mvc.perform(get("/api/models/unknown/config")).andExpect(status().isBadRequest());
        var invalid = Map.of("model", "", "baseUrl", "https://example.invalid/v1", "apiKey", "test-secret-through-spring", "revision", 2);
        var validation = mvc.perform(put("/api/models/deepseek/config").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(invalid)))
            .andExpect(status().isBadRequest()).andReturn().getResponse().getContentAsString();
        assertFalse(validation.contains("test-secret"));
        mvc.perform(put("/api/models/deepseek/config").contentType(MediaType.APPLICATION_JSON).content("{\"apiKey\":\"test-secret-through-spring\","))
            .andExpect(status().isBadRequest()).andExpect(jsonPath("$.message").value("提交内容格式不正确，请检查后重试"));
    }

    @Test void customTopicReachesAgentAndIsPreservedOnRetry() throws Exception {
        var upload = mvc.perform(multipart("/api/materials").file(new MockMultipartFile("files", "topic.txt", "text/plain", "测试学校培养定位".getBytes(StandardCharsets.UTF_8))).param("category", "SPEECH"))
            .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String materialId = mapper.readTree(upload).get(0).path("id").asText();
        var topic = Map.of("title", "未来AI人才培养方向", "requirements", "分析学习成果、课程路径和评价方式。");
        var input = Map.of("school", "接口测试学校", "year", 2025, "modelId", "qwen", "templateId", "graduate",
            "materialIds", List.of(materialId), "chapters", List.of("第五章 教学培养反馈"), "customTopic", topic);
        var report = mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(input)))
            .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String id = mapper.readTree(report).path("id").asText();
        mvc.perform(post("/api/reports/" + id + "/run")).andExpect(status().isOk());
        assertEquals(topic, lastRequest.get("customTopic"));
        JOBS.put(id, job(id, "FAILED"));
        mvc.perform(post("/api/reports/" + id + "/run")).andExpect(status().isOk());
        assertEquals(topic, ownWorkspace().report(id).customTopic() == null ? null :
            Map.of("title", ownWorkspace().report(id).customTopic().title(), "requirements", ownWorkspace().report(id).customTopic().requirements()));
    }
}
