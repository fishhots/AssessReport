package cn.assessreport;

import cn.assessreport.agent.AgentGateway;
import cn.assessreport.service.*;
import com.fasterxml.jackson.databind.*;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import java.nio.file.*;
import java.util.Map;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest(properties = "app.agent.enabled=false")
@AutoConfigureMockMvc
class WorkspaceApiTest {
    private static final Path STORAGE;
    static { try { STORAGE = Files.createTempDirectory("assess-report-test-"); } catch (Exception e) { throw new ExceptionInInitializerError(e); } }
    @DynamicPropertySource static void storage(DynamicPropertyRegistry registry) { registry.add("app.storage-directory", () -> STORAGE.toString()); }
    @Autowired MockMvc mvc;
    @Autowired ObjectMapper mapper;
    @Autowired CatalogService catalog;
    @Autowired AgentGateway agent;

    @Test void uploadPrepareAndReloadTask() throws Exception {
        mvc.perform(get("/api/catalog")).andExpect(status().isOk()).andExpect(jsonPath("$.models.length()").value(6))
            .andExpect(jsonPath("$.templates.length()").value(1)).andExpect(jsonPath("$.templates[0].name").value("毕业生培养质量评价报告"))
            .andExpect(jsonPath("$.templates[0].chapters.length()").value(9)).andExpect(jsonPath("$.templates[0].chapters[0]").value("核心发现"))
            .andExpect(jsonPath("$.templates[0].chapters[8]").value("第八章 技术报告")).andExpect(jsonPath("$.agentConnected").value(false));
        var file = new MockMultipartFile("files", "../graduate.csv", "text/csv", "major,employment\nCS,95\n".getBytes());
        String uploaded = mvc.perform(multipart("/api/materials").file(file).param("category", "DATA"))
            .andExpect(status().isCreated()).andExpect(jsonPath("$[0].name").value("graduate.csv")).andReturn().getResponse().getContentAsString();
        String id = mapper.readTree(uploaded).get(0).get("id").asText();
        mvc.perform(get("/api/materials/" + id + "/download")).andExpect(status().isOk()).andExpect(content().bytes(file.getBytes()));
        Map<String, Object> input = Map.of("school", "北京信息科技大学", "year", 2025, "modelId", "qwen", "templateId", "graduate", "materialIds", List.of(id), "chapters", List.of("第三章 升学分析", "第一章 社会需求适应", "核心发现"), "instructions", "关注培养质量");
        String report = mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(input)))
            .andExpect(status().isCreated()).andExpect(jsonPath("$.status").value("DRAFT"))
            .andExpect(jsonPath("$.chapters.length()").value(3)).andExpect(jsonPath("$.chapters[0]").value("核心发现"))
            .andExpect(jsonPath("$.chapters[1]").value("第一章 社会需求适应")).andExpect(jsonPath("$.chapters[2]").value("第三章 升学分析"))
            .andExpect(jsonPath("$.steps[0].status").value("COMPLETED")).andExpect(jsonPath("$.steps[1].status").value("WAITING"))
            .andReturn().getResponse().getContentAsString();
        String reportId = mapper.readTree(report).get("id").asText();
        mvc.perform(get("/api/reports/" + reportId)).andExpect(status().isOk()).andExpect(jsonPath("$.materialIds[0]").value(id));
        mvc.perform(delete("/api/materials/" + id)).andExpect(status().isConflict());
        WorkspaceService reloaded = new WorkspaceService(STORAGE.toString(), mapper, catalog, agent);
        assertEquals(reportId, reloaded.report(reportId).id());
        assertEquals("graduate.csv", reloaded.material(id).name());

        Map<String, Object> invalidModel = new java.util.HashMap<>(input); invalidModel.put("modelId", "invalid");
        mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(invalidModel))).andExpect(status().isBadRequest());
        Map<String, Object> invalidChapter = new java.util.HashMap<>(input); invalidChapter.put("chapters", List.of("任意章节"));
        mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(invalidChapter))).andExpect(status().isBadRequest());
        Map<String, Object> invalidTemplate = new java.util.HashMap<>(input); invalidTemplate.put("templateId", "diagnosis");
        mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(invalidTemplate))).andExpect(status().isBadRequest());
        Map<String, Object> noChapters = new java.util.HashMap<>(input); noChapters.put("chapters", List.of());
        mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(noChapters))).andExpect(status().isBadRequest());
        mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content("{}" )).andExpect(status().isBadRequest());
        mvc.perform(get("/api/materials/unknown/download")).andExpect(status().isNotFound());
    }

    @Test void invalidUploadDoesNotPartiallySave() throws Exception {
        int before = mapper.readTree(mvc.perform(get("/api/materials")).andReturn().getResponse().getContentAsString()).size();
        mvc.perform(multipart("/api/materials").file(new MockMultipartFile("files", "custom.docx", "application/octet-stream", "test".getBytes())).param("category", "TEMPLATE")).andExpect(status().isBadRequest());
        mvc.perform(multipart("/api/materials")
            .file(new MockMultipartFile("files", "valid.csv", "text/csv", "a,b".getBytes()))
            .file(new MockMultipartFile("files", "invalid.exe", "application/octet-stream", "bad".getBytes()))
            .param("category", "DATA")).andExpect(status().isBadRequest());
        mvc.perform(multipart("/api/materials").file(new MockMultipartFile("files", "empty.txt", "text/plain", new byte[0])).param("category", "DATA")).andExpect(status().isBadRequest());
        int after = mapper.readTree(mvc.perform(get("/api/materials")).andReturn().getResponse().getContentAsString()).size();
        assertEquals(before, after);
        String uploaded = mvc.perform(multipart("/api/materials").file(new MockMultipartFile("files", "deletable.txt", "text/plain", "sample".getBytes())).param("category", "REFERENCE"))
            .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String id = mapper.readTree(uploaded).get(0).get("id").asText();
        mvc.perform(delete("/api/materials/" + id)).andExpect(status().isNoContent());
        mvc.perform(get("/api/materials/" + id + "/download")).andExpect(status().isNotFound());
    }

    @Test void customTopicIsValidatedAndSurvivesReload() throws Exception {
        String uploaded = mvc.perform(multipart("/api/materials").file(new MockMultipartFile("files", "topic.txt", "text/plain", "school data".getBytes())).param("category", "SPEECH"))
            .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String materialId = mapper.readTree(uploaded).get(0).get("id").asText();
        var input = new java.util.HashMap<String, Object>(Map.of("school", "主题测试学校", "year", 2025,
            "modelId", "deepseek", "templateId", "graduate", "materialIds", List.of(materialId),
            "chapters", List.of("第五章 教学培养反馈")));
        input.put("customTopic", Map.of("title", "  未来AI人才培养方向  ", "requirements", "  分析课程与实践的实施路径。  "));
        String report = mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(input)))
            .andExpect(status().isCreated()).andExpect(jsonPath("$.customTopic.title").value("未来AI人才培养方向"))
            .andExpect(jsonPath("$.customTopic.requirements").value("分析课程与实践的实施路径。"))
            .andReturn().getResponse().getContentAsString();
        String id = mapper.readTree(report).get("id").asText();
        var reloaded = new WorkspaceService(STORAGE.toString(), mapper, catalog, agent);
        assertEquals("未来AI人才培养方向", reloaded.report(id).customTopic().title());
        for (String title : List.of(" ", "line\nbreak", "a".repeat(101))) {
            input.put("customTopic", Map.of("title", title, "requirements", "分析需求"));
            mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(input))).andExpect(status().isBadRequest());
        }
        input.put("customTopic", Map.of("title", "有效题目", "requirements", " "));
        mvc.perform(post("/api/reports").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(input))).andExpect(status().isBadRequest());
    }
}
