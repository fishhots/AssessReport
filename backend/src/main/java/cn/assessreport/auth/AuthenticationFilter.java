package cn.assessreport.auth;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.annotation.Order;
import org.springframework.core.Ordered;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import java.io.IOException;
import java.util.*;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE+20)
public class AuthenticationFilter extends OncePerRequestFilter {
    private final AuthService auth;
    private final UserContext users;
    private final ObjectMapper mapper;
    private final Set<String> allowedOrigins;
    public AuthenticationFilter(AuthService auth,UserContext users,ObjectMapper mapper,
            @Value("${app.auth.allowed-origins:http://127.0.0.1:5173,http://localhost:5173}") String origins) {
        this.auth=auth;this.users=users;this.mapper=mapper;
        allowedOrigins=Set.of(origins.split(","));
    }
    public static String token(HttpServletRequest request) {
        if (request.getCookies()==null) return null;
        return Arrays.stream(request.getCookies()).filter(c -> c.getName().equals(AuthService.COOKIE)).map(Cookie::getValue).findFirst().orElse(null);
    }
    @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws ServletException,IOException {
        String path=request.getRequestURI().substring(request.getContextPath().length());
        if (path.equals("/api/health")) {chain.doFilter(request,response);return;}
        response.setHeader("Cache-Control","no-store");
        if (!Set.of("GET","HEAD","OPTIONS").contains(request.getMethod())) {
            String origin=request.getHeader("Origin");
            String ownOrigin=request.getScheme()+"://"+request.getHeader("Host");
            if ("cross-site".equals(request.getHeader("Sec-Fetch-Site")) || (origin!=null && !origin.equals(ownOrigin) && !allowedOrigins.contains(origin))) {
                reject(response,403,"请求来源无效，请从系统页面操作");return;
            }
        }
        if (Set.of("/api/auth/register","/api/auth/login").contains(path)) {chain.doFilter(request,response);return;}
        UserContext.User user=auth.authenticate(token(request));
        if (user==null) {reject(response,401,"登录已失效，请重新登录");return;}
        String expected=request.getHeader("X-Expected-User");
        if (expected!=null && !expected.equals(user.id())) {reject(response,401,"登录账号已变更，请重新登录");return;}
        response.setHeader("X-Workspace-User",user.id());
        users.set(user);
        try {chain.doFilter(request,response);} finally {users.clear();}
    }
    private void reject(HttpServletResponse response,int status,String message) throws IOException {
        response.setStatus(status);response.setContentType("application/json");response.setCharacterEncoding("UTF-8");
        mapper.writeValue(response.getWriter(),Map.of("message",message));
    }
}
