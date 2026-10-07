package cn.assessreport.auth;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.constraints.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.time.*;
import java.util.*;

@Service
public class AuthService {
    public static final String COOKIE = "assess_session";
    public record RegisterInput(@NotBlank @Size(max=50) String username,
        @NotBlank @Pattern(regexp="[A-Za-z0-9_.-]{3,64}") String account,
        @NotBlank @Size(min=8,max=128) String password, @NotBlank @Size(max=64) String inviteCode) {}
    public record LoginInput(@NotBlank @Size(max=64) String account,
        @NotBlank @Size(max=128) String password, boolean rememberMe) {}
    public record Account(String id, String username, String account, String salt, String passwordHash, Instant createdAt) {
        UserContext.User publicUser() { return new UserContext.User(id,username,account); }
    }
    public record Session(String tokenHash, String userId, Instant expiresAt) {}
    public record Accounts(List<Account> accounts) {}
    public record Sessions(List<Session> sessions) {}
    public record LoginResult(UserContext.User user, String token, Duration lifetime, boolean rememberMe) {}
    private record Failures(int count, Instant until) {}
    private final Path directory;
    private final ObjectMapper mapper;
    private final String inviteCode;
    private final SecureRandom random = new SecureRandom();
    private final Map<String,Failures> failures = new HashMap<>();
    private Accounts accounts;
    private Sessions sessions;

    public AuthService(@Value("${app.storage-directory}") String root, ObjectMapper mapper,
            @Value("${app.auth.invite-code:appmath}") String inviteCode) throws IOException {
        this.directory=Path.of(root).toAbsolutePath().normalize().resolve("auth");
        this.mapper=mapper; this.inviteCode=inviteCode;
        Files.createDirectories(directory);
        accounts=Files.exists(directory.resolve("accounts.json"))
            ? mapper.readValue(directory.resolve("accounts.json").toFile(),Accounts.class) : new Accounts(List.of());
        sessions=Files.exists(directory.resolve("sessions.json"))
            ? mapper.readValue(directory.resolve("sessions.json").toFile(),Sessions.class) : new Sessions(List.of());
    }
    public synchronized UserContext.User register(RegisterInput input) throws IOException {
        if (!MessageDigest.isEqual(inviteCode.getBytes(StandardCharsets.UTF_8),input.inviteCode().getBytes(StandardCharsets.UTF_8)))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"邀请码不正确");
        String account=input.account().trim().toLowerCase(Locale.ROOT);
        if (accounts.accounts().stream().anyMatch(a -> a.account().equals(account)))
            throw new ResponseStatusException(HttpStatus.CONFLICT,"该账号已注册");
        byte[] salt=new byte[16]; random.nextBytes(salt);
        Account created=new Account(UUID.randomUUID().toString(),input.username().trim(),account,
            Base64.getEncoder().encodeToString(salt),Base64.getEncoder().encodeToString(passwordHash(input.password(),salt)),Instant.now());
        List<Account> next=new ArrayList<>(accounts.accounts());next.add(created);
        Accounts updated=new Accounts(List.copyOf(next));write("accounts.json",updated);accounts=updated;
        return created.publicUser();
    }
    public synchronized LoginResult login(LoginInput input,String address) throws IOException {
        String account=input.account().trim().toLowerCase(Locale.ROOT);
        Instant now=Instant.now();
        failures.entrySet().removeIf(entry -> entry.getValue().until().isBefore(now));
        String failureKey=address+"|"+account;
        Failures previous=failures.get(failureKey);
        if (previous!=null && previous.count()>=10)
            throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS,"登录尝试过多，请稍后再试");
        Account found=accounts.accounts().stream().filter(a -> a.account().equals(account)).findFirst().orElse(null);
        byte[] salt=found==null ? new byte[16] : Base64.getDecoder().decode(found.salt());
        byte[] actual=passwordHash(input.password(),salt);
        if (found==null || !MessageDigest.isEqual(actual,Base64.getDecoder().decode(found.passwordHash()))) {
            if (failures.size()>5000) failures.clear();
            failures.put(failureKey,new Failures(previous==null?1:previous.count()+1,now.plus(Duration.ofMinutes(15))));
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED,"账号或密码不正确");
        }
        failures.remove(failureKey);
        byte[] bytes=new byte[32];random.nextBytes(bytes);
        String token=HexFormat.of().formatHex(bytes);
        Duration lifetime=input.rememberMe()?Duration.ofDays(30):Duration.ofHours(12);
        List<Session> next=new ArrayList<>(sessions.sessions().stream().filter(s -> s.expiresAt().isAfter(now)).toList());
        next.add(new Session(tokenHash(token),found.id(),now.plus(lifetime)));
        Sessions updated=new Sessions(List.copyOf(next));write("sessions.json",updated);sessions=updated;
        return new LoginResult(found.publicUser(),token,lifetime,input.rememberMe());
    }
    public synchronized UserContext.User authenticate(String token) {
        if (token==null || !token.matches("[0-9a-f]{64}")) return null;
        String hash=tokenHash(token);
        Session session=sessions.sessions().stream().filter(s -> s.tokenHash().equals(hash) && s.expiresAt().isAfter(Instant.now())).findFirst().orElse(null);
        if (session==null) return null;
        return accounts.accounts().stream().filter(a -> a.id().equals(session.userId())).findFirst().map(Account::publicUser).orElse(null);
    }
    public synchronized void logout(String token) throws IOException {
        if (token==null) return;
        String hash=tokenHash(token);
        Sessions updated=new Sessions(sessions.sessions().stream().filter(s -> !s.tokenHash().equals(hash)).toList());
        write("sessions.json",updated);sessions=updated;
    }
    private byte[] passwordHash(String password,byte[] salt) {
        PBEKeySpec spec=new PBEKeySpec(password.toCharArray(),salt,600000,256);
        try {return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();}
        catch (GeneralSecurityException e) {throw new IllegalStateException("Password hashing unavailable",e);}
        finally {spec.clearPassword();}
    }
    private String tokenHash(String token) {
        try {return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(token.getBytes(StandardCharsets.UTF_8)));}
        catch (GeneralSecurityException e) {throw new IllegalStateException(e);}
    }
    private void write(String name,Object data) throws IOException {
        Path target=directory.resolve(name),temporary=directory.resolve(name+".tmp");
        mapper.writerWithDefaultPrettyPrinter().writeValue(temporary.toFile(),data);
        try {Files.move(temporary,target,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);}
        catch (AtomicMoveNotSupportedException e) {Files.move(temporary,target,StandardCopyOption.REPLACE_EXISTING);}
    }
}
