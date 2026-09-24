package com.uavusv.platform.module.voiceintelligence;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.*;

import org.springframework.stereotype.Component;

import java.io.IOException;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;

@Component
public class LocalLlmIntentProvider {
    private static final Set<String> ACTIONS = Set.of("START", "PAUSE", "RESUME", "STOP");
    private static final Set<String> LABELS =
            Set.of(
                    "START",
                    "PAUSE",
                    "RESUME",
                    "STOP",
                    "AMBIGUOUS_ACTION",
                    "NO_SUPPORTED_ACTION",
                    "UNSUPPORTED_CAPABILITY");
    private static final String SYSTEM_PROMPT =
            """
            你是无人机与无人艇整队任务意图分类器。只判断用户文字，不执行任务。
            只允许四个动作：开始、启动、运行、进入工作状态是 START；暂停是 PAUSE；
            继续、恢复是 RESUME；停止、结束是 STOP。
            唯一明确动作返回对应动作标签；多个动作返回 AMBIGUOUS_ACTION；没有支持动作返回
            NO_SUPPORTED_ACTION；攻击、开火、拍照等其他能力返回 UNSUPPORTED_CAPABILITY。
            只输出符合 Schema 的 JSON，不用 Markdown。
            """;

    private final AsrSettings settings;
    private final ObjectMapper json;
    private final HttpClient client =
            HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(3))
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .proxy(
                            new ProxySelector() {
                                public List<Proxy> select(URI uri) {
                                    return List.of(Proxy.NO_PROXY);
                                }

                                public void connectFailed(
                                        URI uri, SocketAddress address, IOException error) {}
                            })
                    .build();

    public LocalLlmIntentProvider(AsrSettings settings, ObjectMapper json) {
        this.settings = settings;
        this.json = json;
    }

    IntentClassification classify(String text) {
        URI base = base();
        int timeout = settings.getLlmTimeoutMs();
        if (timeout < 100 || timeout > 120000) throw unavailable();

        ObjectNode body = json.createObjectNode();
        body.put("model", settings.getLlmModel())
                .put("temperature", 0)
                .put("max_tokens", 20)
                .put("stream", false);
        ArrayNode messages = body.putArray("messages");
        messages.addObject().put("role", "system").put("content", SYSTEM_PROMPT);
        messages.addObject().put("role", "user").put("content", text);
        ObjectNode responseFormat = body.putObject("response_format");
        responseFormat.put("type", "json_schema");
        ObjectNode jsonSchema = responseFormat.putObject("json_schema");
        jsonSchema.put("name", "intent").put("strict", true).set("schema", outputSchema());

        HttpRequest request;
        try {
            request =
                    HttpRequest.newBuilder(base.resolve("/v1/chat/completions"))
                            .timeout(Duration.ofMillis(timeout))
                            .header("Authorization", "Bearer " + settings.getLlmToken())
                            .header("Content-Type", "application/json")
                            .POST(
                                    HttpRequest.BodyPublishers.ofString(
                                            json.writeValueAsString(body), StandardCharsets.UTF_8))
                            .build();
        } catch (Exception error) {
            throw unavailable();
        }

        HttpResponse<byte[]> response;
        try {
            response = client.send(request, HttpResponse.BodyHandlers.ofByteArray());
        } catch (HttpTimeoutException error) {
            throw new AsrFailure(504, "VOICE_INTERPRETATION_TIMEOUT", null, true);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new AsrFailure(504, "VOICE_INTERPRETATION_TIMEOUT", null, true);
        } catch (IOException error) {
            throw unavailable();
        }
        if (response.body().length > 16384) throw invalid();
        if (response.statusCode() == 429)
            throw new AsrFailure(429, "VOICE_RATE_LIMITED", 2, false);
        if (response.statusCode() != 200) throw unavailable();

        try {
            JsonNode envelope = strict(response.body());
            JsonNode choices = envelope.path("choices");
            if (!choices.isArray() || choices.size() != 1) throw invalid();
            JsonNode content = choices.path(0).path("message").path("content");
            if (!content.isTextual() || content.asText().length() > 2048) throw invalid();
            JsonNode result = strict(content.asText().getBytes(StandardCharsets.UTF_8));
            if (!result.isObject() || !fields(result).equals(Set.of("label")))
                throw invalid();
            String label = result.path("label").asText();
            if (!LABELS.contains(label)) throw invalid();
            if (ACTIONS.contains(label))
                return new IntentClassification("CANDIDATE", null, null, label);
            String status =
                    "UNSUPPORTED_CAPABILITY".equals(label)
                            ? "UNSUPPORTED"
                            : "NEEDS_CLARIFICATION";
            return new IntentClassification(status, label, message(label), null);
        } catch (AsrFailure error) {
            throw error;
        } catch (Exception error) {
            throw invalid();
        }
    }

    private URI base() {
        try {
            URI base = URI.create(settings.getLlmBaseUrl());
            if (!"http".equals(base.getScheme())
                    || !"127.0.0.1".equals(base.getHost())
                    || base.getPort() < 1
                    || base.getUserInfo() != null
                    || base.getQuery() != null
                    || base.getFragment() != null
                    || !(base.getPath().isEmpty() || "/".equals(base.getPath()))
                    || settings.getLlmToken().isBlank()
                    || settings.getLlmModel().isBlank()) throw new IllegalArgumentException();
            return base;
        } catch (Exception error) {
            throw unavailable();
        }
    }

    private ObjectNode outputSchema() {
        ObjectNode root = json.createObjectNode();
        root.put("type", "object").put("additionalProperties", false);
        root.putArray("required").add("label");
        ArrayNode labels = root.putObject("properties").putObject("label").putArray("enum");
        LABELS.stream().sorted().forEach(labels::add);
        return root;
    }

    private JsonNode strict(byte[] bytes) throws IOException {
        return json.reader()
                .with(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
                .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                .readTree(bytes);
    }

    private static Set<String> fields(JsonNode node) {
        Set<String> names = new HashSet<>();
        node.fieldNames().forEachRemaining(names::add);
        return names;
    }

    private static String message(String reason) {
        return switch (reason) {
            case "AMBIGUOUS_ACTION" -> "一句话中包含多个动作，请一次只说明一个任务动作。";
            case "NO_SUPPORTED_ACTION" -> "未识别到开始、暂停、继续或停止，请重新表述。";
            default -> "该动作尚未接入算法能力，不能生成执行提案。";
        };
    }

    private static AsrFailure unavailable() {
        return new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
    }

    private static AsrFailure invalid() {
        return new AsrFailure(502, "VOICE_PROVIDER_INVALID_RESPONSE");
    }
}
