package cn.assessreport.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpStatus;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

@Component
@ConditionalOnProperty(name = "app.agent.enabled", havingValue = "true", matchIfMissing = true)
public class HttpAgentGateway implements AgentGateway {
    private final RestClient client;
    private final RestClient modelClient;
    public HttpAgentGateway(@Value("${app.agent.base-url}") String baseUrl,
            @Value("${app.agent.token:}") String token, ObjectMapper mapper) {
        var factory = new JdkClientHttpRequestFactory(HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build());
        factory.setReadTimeout(Duration.ofSeconds(5));
        client = RestClient.builder().baseUrl(baseUrl).requestFactory(factory).defaultHeader("X-Agent-Token", token)
            .defaultStatusHandler(status -> status.isError(), (request, response) -> {
                String message = "智能体请求失败，请检查服务后重试";
                try {
                    var error = mapper.readTree(response.getBody());
                    message = switch (error.path("code").asText("")) {
                        case "MODEL_NOT_CONFIGURED" -> "所选模型尚未配置，请在模型设置中填写配置信息";
                        case "MODEL_AUTH" -> "模型认证失败，请在模型设置中检查密钥和账号权限";
                        case "MODEL_QUOTA" -> "模型 API 余额不足或使用额度已用尽，请检查模型平台的余额和额度";
                        case "MODEL_RATE_LIMIT" -> "模型调用已限流，请稍后重试";
                        case "MODEL_TIMEOUT" -> "模型调用超时，请稍后重试";
                        case "MODEL_NOT_FOUND" -> "模型或接口不存在，请检查模型名称和接口基础地址";
                        case "MODEL_BAD_REQUEST" -> "模型接口拒绝了请求参数，请检查模型名称、接口地址和额外参数";
                        case "MODEL_THINKING_CONFIG" -> "模型接口的思考模式或工具协议不兼容，请检查接口支持的思考参数，或在模型设置中关闭额外深度分析后保存并测试";
                        case "MODEL_CONNECTION" -> "无法连接模型接口，请检查网络、代理和接口地址";
                        case "MODEL_UNAVAILABLE" -> "模型平台服务暂时不可用，请稍后重试";
                        case "MODEL_CONFIG" -> "模型配置格式有误，请检查模型名称、密钥、接口地址与额外参数";
                        case "MODEL_CONFIG_CONFLICT" -> "配置已更新，请重新打开后修改";
                        case "MODEL_KEY_ERROR" -> "模型密钥读取失败，请检查配置备份";
                        case "MODEL_TEST", "MODEL_STRUCTURE" -> "模型未通过工具调用与结构化输出测试，请重试或更换模型";
                        case "MODEL_ERROR" -> "模型调用失败，请重试；若持续失败，请查看智能体服务日志";
                        case "UNAUTHORIZED" -> "智能体服务认证失败，请检查服务配置";
                        case "QUEUE_FULL" -> "任务队列已满，请稍后再试";
                        case "JOB_RETRY", "JOB_CONFLICT" -> "任务状态不允许此次操作，请刷新后重试";
                        case "JOB_ACTIVE" -> "请先终止任务，等待终止完成后再删除记录";
                        case "JOB_CANCEL" -> "当前任务已经结束，无需终止";
                        case "JOB_NOT_FOUND" -> "报告任务不存在";
                        case "JOB_DELETE" -> "报告缓存路径无效，暂时无法删除";
                        case "ARTIFACT_PENDING" -> "输出尚未完成";
                        case "ARTIFACT_CHANGED", "ARTIFACT_MISSING" -> "输出文件校验失败，请重新执行";
                        default -> "智能体请求失败，请检查配置和资料后重试";
                    };
                } catch (Exception ignored) { }
                throw new ResponseStatusException(response.getStatusCode(), message);
            }).build();
        var modelFactory = new JdkClientHttpRequestFactory(HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build());
        modelFactory.setReadTimeout(Duration.ofSeconds(200));
        modelClient = client.mutate().requestFactory(modelFactory).build();
    }
    private <T> T call(Supplier<T> action) {
        try { return action.get(); }
        catch (RestClientException failure) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "无法连接智能体服务，请检查服务是否启动");
        }
    }
    public List<Provider> providers() {
        return call(() -> client.get().uri("/models").retrieve().body(new ParameterizedTypeReference<List<Provider>>() {}));
    }
    public List<Job> jobs() {
        return call(() -> client.get().uri("/jobs").retrieve().body(new ParameterizedTypeReference<List<Job>>() {}));
    }
    public Job submit(Request request) { return call(() -> client.post().uri("/jobs").body(request).retrieve().body(Job.class)); }
    public Job retry(String id) { return call(() -> client.post().uri("/jobs/{id}/retry", id).retrieve().body(Job.class)); }
    public Job cancel(String id) { return call(() -> client.post().uri("/jobs/{id}/cancel", id).retrieve().body(Job.class)); }
    public void delete(String id) { call(() -> client.delete().uri("/jobs/{id}", id).retrieve().toBodilessEntity()); }
    public byte[] download(String id, String kind) {
        return call(() -> client.get().uri("/jobs/{id}/artifacts/{kind}", id, kind).retrieve().body(byte[].class));
    }
    public Map<String, Object> testModel(String provider) {
        // A fixed small prompt may require the provider's normal request timeout.
        return call(() -> modelClient.post().uri("/models/{provider}/test", provider).retrieve().body(new ParameterizedTypeReference<Map<String, Object>>() {}));
    }
    public ModelConfig modelConfig(String provider) {
        return modelConfig(provider, null);
    }
    public ModelConfig modelConfig(String provider, String channel) {
        return call(() -> client.get().uri(builder -> {
            builder.path("/models/{provider}/config");
            if (channel != null) builder.queryParam("channel", channel);
            return builder.build(provider);
        }).retrieve().body(ModelConfig.class));
    }
    public ModelConfig saveModelConfig(String provider, ModelConfigInput config) {
        return call(() -> client.put().uri("/models/{provider}/config", provider).body(config).retrieve().body(ModelConfig.class));
    }
}
