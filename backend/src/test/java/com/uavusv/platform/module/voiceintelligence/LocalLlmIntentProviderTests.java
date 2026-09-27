package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;

import org.junit.jupiter.api.*;

import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;

class LocalLlmIntentProviderTests {
    HttpServer server;
    AsrSettings settings;
    LocalLlmIntentProvider provider;
    AtomicInteger calls = new AtomicInteger();

    @BeforeEach
    void setup() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.start();
        settings = new AsrSettings();
        settings.setLlmBaseUrl("http://127.0.0.1:" + server.getAddress().getPort());
        settings.setLlmToken("test-secret");
        settings.setLlmModel("qwen-test");
        settings.setLlmTimeoutMs(1000);
        provider = new LocalLlmIntentProvider(settings, new ObjectMapper());
    }

    @AfterEach
    void close() {
        server.stop(0);
    }

    void reply(int status, String content) {
        server.createContext(
                "/v1/chat/completions",
                exchange -> {
                    calls.incrementAndGet();
                    assertEquals(
                            "Bearer test-secret",
                            exchange.getRequestHeaders().getFirst("Authorization"));
                    String request =
                            new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                    assertTrue(request.contains("qwen-test"));
                    var requestJson = new ObjectMapper().readTree(request);
                    assertEquals(
                            "json_schema",
                            requestJson.path("response_format").path("type").asText());
                    assertEquals(
                            7,
                            requestJson
                                    .path("response_format")
                                    .path("json_schema")
                                    .path("schema")
                                    .path("properties")
                                    .path("label")
                                    .path("enum")
                                    .size());
                    byte[] bytes = content.getBytes(StandardCharsets.UTF_8);
                    exchange.sendResponseHeaders(status, bytes.length);
                    exchange.getResponseBody().write(bytes);
                    exchange.close();
                });
    }

    String envelope(String result) throws Exception {
        String escaped = new ObjectMapper().writeValueAsString(result);
        return "{\"choices\":[{\"message\":{\"content\":" + escaped + "}}]}";
    }

    @Test
    void candidateParsedFromRealLoopbackHttp() throws Exception {
        reply(
                200,
                envelope(
                        "{\"label\":\"START\"}"));
        IntentClassification result = provider.classify("请让编队立即执行");
        assertEquals("CANDIDATE", result.status());
        assertEquals("START", result.action());
        assertEquals(1, calls.get());
    }

    @Test
    void clarificationParsedWithoutModelMessage() throws Exception {
        reply(
                200,
                envelope(
                        "{\"label\":\"NO_SUPPORTED_ACTION\"}"));
        IntentClassification result = provider.classify("报告情况");
        assertEquals("NO_SUPPORTED_ACTION", result.reason());
        assertEquals("未识别到开始、暂停、继续或停止，请重新表述。", result.message());
    }

    @Test
    void invalidStructureNeverBecomesCandidate() throws Exception {
        reply(
                200,
                envelope(
                        "{\"label\":\"START\",\"extra\":true}"));
        AsrFailure failure = assertThrows(AsrFailure.class, () -> provider.classify("开始"));
        assertEquals(502, failure.status);
        assertEquals("VOICE_PROVIDER_INVALID_RESPONSE", failure.code);
    }

    @Test
    void rateLimitMappedWithoutRetry() throws Exception {
        reply(429, "{}");
        AsrFailure failure = assertThrows(AsrFailure.class, () -> provider.classify("开始"));
        assertEquals(429, failure.status);
        assertEquals("VOICE_RATE_LIMITED", failure.code);
        assertEquals(1, calls.get());
    }

    @Test
    void timeoutIsUncertainAndNotRetried() {
        settings.setLlmTimeoutMs(100);
        server.createContext(
                "/v1/chat/completions",
                exchange -> {
                    calls.incrementAndGet();
                    try {
                        Thread.sleep(300);
                    } catch (InterruptedException ignored) {
                    }
                    exchange.close();
                });
        AsrFailure failure = assertThrows(AsrFailure.class, () -> provider.classify("开始"));
        assertEquals(504, failure.status);
        assertEquals("VOICE_INTERPRETATION_TIMEOUT", failure.code);
        assertTrue(failure.uncertain);
        assertTrue(calls.get() <= 1, "timed-out inference must never be retried");
    }

    @Test
    void nonLoopbackConfigurationRejectedBeforeRequest() {
        settings.setLlmBaseUrl("http://example.com:18083");
        AsrFailure failure = assertThrows(AsrFailure.class, () -> provider.classify("开始"));
        assertEquals(503, failure.status);
        assertEquals(0, calls.get());
    }
}
