package com.uavusv.platform.module.sensor.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/** Adapter for the supplied SAN60 engine. Coordinates and calculations stay in the engine. */
@Service
public class San60Service {
    private final ObjectMapper mapper;
    private final URI base;
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
    private volatile byte[] plotly;

    public San60Service(ObjectMapper mapper,
            @Value("${app.san60.service-url:http://127.0.0.1:8775}") String serviceUrl) {
        this.mapper = mapper;
        this.base = URI.create(serviceUrl);
        if (!"http".equals(base.getScheme()) || !Set.of("localhost", "127.0.0.1", "[::1]").contains(base.getHost())
                || base.getUserInfo() != null || base.getQuery() != null || base.getFragment() != null
                || !(base.getPath().isEmpty() || base.getPath().equals("/"))) {
            throw new IllegalArgumentException("SAN60 service-url must be an HTTP loopback origin");
        }
    }

    public ObjectNode status() {
        ObjectNode result = mapper.createObjectNode();
        result.put("serviceUrl", base.toString());
        try {
            result.set("state", json("GET", "/api/state", null));
            result.put("connected", true);
            result.put("message", "SAN60 服务已连接");
        } catch (San60Exception exception) {
            result.put("connected", false);
            result.putNull("state");
            result.put("message", exception.getMessage());
        }
        return result;
    }

    public JsonNode control(JsonNode input) {
        if (!input.isObject()) throw new San60Exception(400, "控制参数必须是 JSON 对象");
        ObjectNode command = input.deepCopy();
        String action = command.path("action").asText();
        if (!Set.of("bag", "stop", "clear", "settings", "demo").contains(action)) {
            throw new San60Exception(400, "不支持的 SAN60 操作");
        }
        if (action.equals("bag")) {
            double speed = command.path("speed").asDouble(1);
            if (!Double.isFinite(speed) || speed < 0.1 || speed > 100) {
                throw new San60Exception(400, "回放倍速须为 0.1–100");
            }
            command.put("speed", speed);
            if (command.path("path").asText().isBlank()) {
                String path = json("GET", "/api/state", null).path("capabilities").path("default_bag").asText();
                if (path.isBlank()) throw new San60Exception(400, "未配置默认 BAG，请填写完整路径");
                command.put("path", path);
            }
        }
        return json("POST", "/api/control", command);
    }

    public JsonNode ingest(JsonNode input) {
        if (!input.isObject() && !input.isArray()) throw new San60Exception(400, "输入须为 JSON 对象或数组");
        return json("POST", "/api/ingest", input);
    }

    public byte[] plotly() {
        if (plotly == null) plotly = request("GET", "/plotly.min.js", null).body();
        return plotly;
    }

    private JsonNode json(String method, String path, JsonNode body) {
        try {
            return mapper.readTree(request(method, path, body).body());
        } catch (IOException exception) {
            throw new San60Exception(502, "SAN60 返回了无法解析的数据");
        }
    }

    private HttpResponse<byte[]> request(String method, String path, JsonNode body) {
        try {
            byte[] payload = body == null ? new byte[0] : mapper.writeValueAsBytes(body);
            if (payload.length > 4 * 1024 * 1024) throw new San60Exception(400, "单次输入不能超过 4 MiB");
            HttpRequest.Builder request = HttpRequest.newBuilder(base.resolve(path)).timeout(Duration.ofSeconds(8));
            if (method.equals("POST")) request.header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofByteArray(payload));
            else request.GET();
            // No browser Origin is forwarded: the bundled engine accepts its own loopback Host only.
            HttpResponse<byte[]> response = client.send(request.build(), HttpResponse.BodyHandlers.ofByteArray());
            if (response.statusCode() != 200) {
                String message;
                try { message = mapper.readTree(response.body()).path("error").asText("SAN60 请求失败"); }
                catch (IOException exception) { message = "SAN60 请求失败"; }
                throw new San60Exception(response.statusCode() < 500 ? 400 : 502, message);
            }
            return response;
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new San60Exception(502, "SAN60 请求已中断");
        } catch (IOException exception) {
            throw new San60Exception(502, "SAN60 服务未连接，请运行 scripts/start-san60.ps1；服务恢复后页面会自动重连");
        }
    }

    public static class San60Exception extends RuntimeException {
        private final int status;
        public San60Exception(int status, String message) { super(message); this.status = status; }
        public int status() { return status; }
    }
}
