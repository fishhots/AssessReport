package cn.assessreport;

import cn.assessreport.auth.*;
import cn.assessreport.service.*;
import cn.assessreport.agent.AgentGateway;
import com.fasterxml.jackson.databind.*;
import jakarta.servlet.http.Cookie;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest(properties="app.agent.enabled=false")
@AutoConfigureMockMvc
class AuthIsolationTest {
    static final Path STORAGE;
    static { try { STORAGE=Files.createTempDirectory("assess-auth-test-"); } catch(Exception e){ throw new ExceptionInInitializerError(e); } }
    @DynamicPropertySource static void storage(DynamicPropertyRegistry r){ r.add("app.storage-directory",STORAGE::toString); }
    @Autowired MockMvc mvc;
    @Autowired ObjectMapper mapper;
    @Autowired AuthService auth;
    @Autowired UserContext users;
    @Autowired CatalogService catalog;
    @Autowired AgentGateway gateway;
    record LoggedIn(String id,Cookie cookie,String setCookie) {}
    private MockHttpServletRequestBuilder json(MockHttpServletRequestBuilder request,Object body)throws Exception{
        return request.contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsBytes(body));
    }
    private Map<String,Object> registration(String account,String invite){
        return Map.of("username","测试用户","account",account,"password","test-password-123","inviteCode",invite);
    }
    private LoggedIn registerAndLogin(boolean remember)throws Exception{
        String account="user-"+UUID.randomUUID();
        mvc.perform(json(post("/api/auth/register"),registration(account,"appmath"))).andExpect(status().isCreated());
        var response=mvc.perform(json(post("/api/auth/login"),Map.of("account",account,"password","test-password-123","rememberMe",remember)))
            .andExpect(status().isOk()).andReturn().getResponse();
        String setCookie=response.getHeader("Set-Cookie");
        String token=setCookie.split(";",2)[0].split("=",2)[1];
        return new LoggedIn(mapper.readTree(response.getContentAsString()).path("id").asText(),new Cookie(AuthService.COOKIE,token),setCookie);
    }
    @Test void registrationSessionsAndLogoutPersistWithoutPlaintextSecrets()throws Exception{
        String account="signup-"+UUID.randomUUID();
        mvc.perform(json(post("/api/auth/register"),registration(account,"wrong"))).andExpect(status().isBadRequest()).andExpect(jsonPath("$.message").value("邀请码不正确"));
        var invalid=new HashMap<>(registration(account,"appmath"));invalid.put("password","short");
        mvc.perform(json(post("/api/auth/register"),invalid)).andExpect(status().isBadRequest());
        mvc.perform(json(post("/api/auth/register"),registration(account,"appmath"))).andExpect(status().isCreated());
        mvc.perform(json(post("/api/auth/register"),registration(account.toUpperCase(Locale.ROOT),"appmath"))).andExpect(status().isConflict());
        mvc.perform(json(post("/api/auth/login"),Map.of("account",account,"password","wrong-password","rememberMe",true))).andExpect(status().isUnauthorized());
        var regular=registerAndLogin(false);var remembered=registerAndLogin(true);
        assertTrue(remembered.setCookie().contains("HttpOnly"));assertTrue(remembered.setCookie().contains("SameSite=Lax"));
        assertTrue(remembered.setCookie().contains("Max-Age=2592000"));assertFalse(regular.setCookie().contains("Max-Age"));
        mvc.perform(get("/api/auth/me").cookie(remembered.cookie())).andExpect(status().isOk()).andExpect(jsonPath("$.id").value(remembered.id()));
        var reloaded=new AuthService(STORAGE.toString(),mapper,"appmath");
        assertEquals(remembered.id(),reloaded.authenticate(remembered.cookie().getValue()).id());
        String stored=Files.readString(STORAGE.resolve("auth/accounts.json"))+Files.readString(STORAGE.resolve("auth/sessions.json"));
        assertFalse(stored.contains("test-password-123"));assertFalse(stored.contains(remembered.cookie().getValue()));
        mvc.perform(post("/api/auth/logout").cookie(remembered.cookie())).andExpect(status().isNoContent()).andExpect(header().string("Set-Cookie",org.hamcrest.Matchers.containsString("Max-Age=0")));
        mvc.perform(get("/api/auth/me").cookie(remembered.cookie())).andExpect(status().isUnauthorized());
        assertNull(new AuthService(STORAGE.toString(),mapper,"appmath").authenticate(remembered.cookie().getValue()));
        mvc.perform(get("/api/auth/me").cookie(regular.cookie())).andExpect(status().isOk());
    }
    @Test void allPrivateEndpointsRequireLoginAndRejectCrossSiteWrites()throws Exception{
        mvc.perform(get("/api/health")).andExpect(status().isOk());
        for(String path:List.of("/api/catalog","/api/materials","/api/reports","/api/models/qwen/config","/api/auth/me"))
            mvc.perform(get(path)).andExpect(status().isUnauthorized());
        mvc.perform(post("/api/reports")).andExpect(status().isUnauthorized());
        mvc.perform(json(post("/api/auth/register").header("Origin","https://unrelated.invalid"),registration("csrf-test","appmath"))).andExpect(status().isForbidden());
        var user=registerAndLogin(true);
        mvc.perform(post("/api/auth/logout").cookie(user.cookie()).header("Sec-Fetch-Site","cross-site")).andExpect(status().isForbidden());
        mvc.perform(post("/api/auth/logout").cookie(user.cookie()).header("Origin","http://localhost:8088").header("Host","localhost:8088")).andExpect(status().isNoContent());
    }
    @Test void materialAndReportOwnershipAreEnforcedAndReloadable()throws Exception{
        var alice=registerAndLogin(true);var bob=registerAndLogin(true);
        var file=new MockMultipartFile("files","alice.csv","text/csv","major,count\nCS,80".getBytes());
        String upload=mvc.perform(multipart("/api/materials").file(file).param("category","DATA").cookie(alice.cookie()))
            .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String materialId=mapper.readTree(upload).get(0).path("id").asText();
        var input=Map.of("school","甲校","year",2026,"modelId","qwen","templateId","graduate","materialIds",List.of(materialId),"chapters",List.of("第一章 社会需求适应"));
        String created=mvc.perform(json(post("/api/reports").cookie(alice.cookie()),input)).andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String taskId=mapper.readTree(created).path("id").asText();
        mvc.perform(get("/api/materials").cookie(bob.cookie()).header("X-Workspace-User",alice.id())).andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(0)).andExpect(header().string("X-Workspace-User",bob.id()));
        mvc.perform(get("/api/reports").cookie(bob.cookie())).andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(0));
        mvc.perform(get("/api/materials/"+materialId+"/download").cookie(bob.cookie())).andExpect(status().isNotFound());
        mvc.perform(delete("/api/materials/"+materialId).cookie(bob.cookie())).andExpect(status().isNotFound());
        mvc.perform(json(post("/api/reports").cookie(bob.cookie()),input)).andExpect(status().isNotFound());
        mvc.perform(get("/api/reports/"+taskId).cookie(bob.cookie())).andExpect(status().isNotFound());
        mvc.perform(post("/api/reports/"+taskId+"/run").cookie(bob.cookie())).andExpect(status().isNotFound());
        mvc.perform(post("/api/reports/"+taskId+"/cancel").cookie(bob.cookie())).andExpect(status().isNotFound());
        mvc.perform(delete("/api/reports/"+taskId).cookie(bob.cookie())).andExpect(status().isNotFound());
        mvc.perform(get("/api/reports/"+taskId+"/outputs/report").cookie(bob.cookie())).andExpect(status().isNotFound());
        mvc.perform(multipart("/api/materials").file(file).param("category","DATA").cookie(bob.cookie()).header("X-Expected-User",alice.id())).andExpect(status().isUnauthorized());
        assertTrue(Files.isRegularFile(STORAGE.resolve("users").resolve(alice.id()).resolve("uploads").resolve(materialId)));
        assertFalse(Files.exists(STORAGE.resolve("users").resolve(bob.id()).resolve("uploads").resolve(materialId)));
        users.set(auth.authenticate(alice.cookie().getValue()));
        try {
            var reload=new WorkspaceService(STORAGE.toString(),mapper,catalog,gateway,users);
            assertEquals(taskId,reload.report(taskId).id());assertEquals(materialId,reload.material(materialId).id());
        } finally {users.clear();}
        mvc.perform(get("/api/materials/"+materialId+"/download").cookie(alice.cookie())).andExpect(status().isOk()).andExpect(content().bytes(file.getBytes()));
    }
}
