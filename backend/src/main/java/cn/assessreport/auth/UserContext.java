package cn.assessreport.auth;

import org.springframework.stereotype.Component;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

@Component
public class UserContext {
    public record User(String id, String username, String account) {}
    private final ThreadLocal<User> current = new ThreadLocal<>();
    public User require() {
        User user = current.get();
        if (user == null) throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "请先登录");
        return user;
    }
    public void set(User user) { current.set(user); }
    public void clear() { current.remove(); }
}
