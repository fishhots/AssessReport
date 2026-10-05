package cn.assessreport.agent;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

@Component
@ConditionalOnProperty(name = "app.agent.enabled", havingValue = "false")
public class PreparationOnlyGateway implements AgentGateway {
    private ResponseStatusException unavailable() { return new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "智能体服务未启动"); }
    public List<Provider> providers() { throw unavailable(); }
    public List<Job> jobs() { throw unavailable(); }
    public Job submit(Request request) { throw unavailable(); }
    public Job retry(String id) { throw unavailable(); }
    public Job cancel(String id) { throw unavailable(); }
    public void delete(String id) { throw unavailable(); }
    public byte[] download(String id, String kind) { throw unavailable(); }
    public Map<String, Object> testModel(String provider) { throw unavailable(); }
    public ModelConfig modelConfig(String provider) { throw unavailable(); }
    public ModelConfig saveModelConfig(String provider, ModelConfigInput config) { throw unavailable(); }
}
