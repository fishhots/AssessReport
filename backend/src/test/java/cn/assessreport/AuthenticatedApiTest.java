package cn.assessreport;

import cn.assessreport.auth.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.Cookie;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;
import org.springframework.web.filter.CharacterEncodingFilter;
import java.util.UUID;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

abstract class AuthenticatedApiTest {
    @Autowired private WebApplicationContext context;
    @Autowired private AuthenticationFilter filter;
    @Autowired private AuthService auth;
    @Autowired protected UserContext users;
    protected UserContext.User testUser;
    protected MockMvc mvc;

    @BeforeEach void authenticateFixture() throws Exception {
        String account = "test-" + UUID.randomUUID();
        testUser = auth.register(new AuthService.RegisterInput("测试用户",account,"test-password-123","appmath"));
        var session = auth.login(new AuthService.LoginInput(account,"test-password-123",false),"test");
        mvc = MockMvcBuilders.webAppContextSetup(context)
            .addFilters(new CharacterEncodingFilter("UTF-8",true,false),filter)
            .defaultResponseCharacterEncoding(java.nio.charset.StandardCharsets.UTF_8)
            .defaultRequest(get("/").cookie(new Cookie(AuthService.COOKIE,session.token()))).build();
    }
    @AfterEach void clearUserContext() { users.clear(); }
}
