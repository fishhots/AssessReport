package cn.assessreport.controller;

import cn.assessreport.auth.*;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import java.io.IOException;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    private final AuthService auth;
    private final UserContext users;
    private final boolean cookieSecure;
    public AuthController(AuthService auth,UserContext users,@Value("${app.auth.cookie-secure:false}") boolean cookieSecure) {
        this.auth=auth;this.users=users;this.cookieSecure=cookieSecure;
    }
    @PostMapping("/register") @ResponseStatus(HttpStatus.CREATED)
    public UserContext.User register(@Valid @RequestBody AuthService.RegisterInput input) throws IOException {return auth.register(input);}
    @PostMapping("/login")
    public ResponseEntity<UserContext.User> login(@Valid @RequestBody AuthService.LoginInput input,HttpServletRequest request) throws IOException {
        var login=auth.login(input,request.getRemoteAddr());
        var cookie=ResponseCookie.from(AuthService.COOKIE,login.token()).httpOnly(true).secure(cookieSecure||request.isSecure()).sameSite("Lax").path("/");
        if (login.rememberMe()) cookie.maxAge(login.lifetime());
        return ResponseEntity.ok().header(HttpHeaders.SET_COOKIE,cookie.build().toString()).body(login.user());
    }
    @GetMapping("/me") public UserContext.User me() {return users.require();}
    @PostMapping("/logout")
    public ResponseEntity<Void> logout(HttpServletRequest request) throws IOException {
        auth.logout(AuthenticationFilter.token(request));
        var cookie=ResponseCookie.from(AuthService.COOKIE,"").httpOnly(true).secure(cookieSecure||request.isSecure()).sameSite("Lax").path("/").maxAge(0).build();
        return ResponseEntity.noContent().header(HttpHeaders.SET_COOKIE,cookie.toString()).build();
    }
}
