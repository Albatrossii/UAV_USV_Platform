package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;

import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

class LocalAsrProviderTests {
    HttpServer server;
    AsrSettings settings;
    LocalAsrProvider provider;
    AtomicInteger calls = new AtomicInteger();
    String id = AudioMultipartTests.ID;

    @BeforeEach
    void setup() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.start();
        settings = new AsrSettings();
        settings.setBaseUrl("http://127.0.0.1:" + server.getAddress().getPort());
        settings.setToken("test-secret");
        settings.setModelRevision("r1");
        provider = new LocalAsrProvider(settings, new ObjectMapper());
    }

    @AfterEach
    void close() {
        server.stop(0);
    }

    void reply(int status, String body) {
        server.createContext(
                "/internal/asr/transcriptions",
                e -> {
                    calls.incrementAndGet();
                    assertEquals(
                            "Bearer test-secret", e.getRequestHeaders().getFirst("Authorization"));
                    long ms = Long.parseLong(e.getRequestHeaders().getFirst("X-ASR-Timeout-Ms"));
                    assertTrue(ms > 0 && ms <= 120000);
                    e.getRequestBody().readAllBytes();
                    byte[] b = body.getBytes(StandardCharsets.UTF_8);
                    e.sendResponseHeaders(status, b.length);
                    e.getResponseBody().write(b);
                    e.close();
                });
    }

    SpeechProvider.Transcript call() {
        return provider.transcribe(
                new SpeechProvider.Audio(id, "zh-CN", "audio/mpeg", new byte[] {1}),
                System.nanoTime() + TimeUnit.SECONDS.toNanos(3));
    }

    String success(String requestId) {
        return "{\"requestId\":\""
                + requestId
                + "\",\"text\":\"暂停任务\",\"durationMs\":2000,\"modelRevision\":\"r1\"}";
    }

    @Test
    void realHttpSuccessAndNoRetries() {
        reply(200, success(id));
        assertEquals("暂停任务", call().text());
        assertEquals(1, calls.get());
    }

    @ParameterizedTest
    @CsvSource({
        "ASR_BUSY,429,VOICE_RATE_LIMITED",
        "ASR_UNAVAILABLE,503,VOICE_PROVIDER_UNAVAILABLE",
        "ASR_TIMEOUT,504,VOICE_TRANSCRIPTION_TIMEOUT",
        "ASR_NO_SPEECH,422,VOICE_NO_SPEECH",
        "ASR_AUDIO_TOO_LARGE,413,VOICE_AUDIO_TOO_LARGE",
        "ASR_AUDIO_TOO_LONG,413,VOICE_AUDIO_TOO_LONG",
        "ASR_AUDIO_FORMAT_UNSUPPORTED,415,VOICE_AUDIO_FORMAT_UNSUPPORTED",
        "ASR_TRANSCRIPT_TOO_LONG,422,VOICE_TRANSCRIPT_TOO_LONG"
    })
    void mappedErrors(String internal, int status, String code) {
        reply(status, "{\"requestId\":null,\"code\":\"" + internal + "\",\"message\":\"test\"}");
        AsrFailure e = assertThrows(AsrFailure.class, this::call);
        assertEquals(status, e.status);
        assertEquals(code, e.code);
        assertEquals(1, calls.get());
    }

    @Test
    void idMismatchNeverSuccess() {
        reply(200, success("wrong"));
        assertEquals(502, assertThrows(AsrFailure.class, this::call).status);
    }

    @Test
    void unknownFieldRejected() {
        reply(200, success(id).replace("2000", "2000,\"extra\":true"));
        assertEquals(502, assertThrows(AsrFailure.class, this::call).status);
    }

    @Test
    void oversizedBodyRejected() {
        reply(200, "x".repeat(20000));
        assertEquals(502, assertThrows(AsrFailure.class, this::call).status);
    }

    @Test
    void unauthorizedInternalIsDeploymentFailure() {
        reply(401, "{\"requestId\":null,\"code\":\"ASR_UNAVAILABLE\",\"message\":\"配置错误\"}");
        assertEquals(503, assertThrows(AsrFailure.class, this::call).status);
    }

    @Test
    void nonLoopbackRefusedBeforeRequest() {
        settings.setBaseUrl("http://example.com:18082");
        assertEquals(503, assertThrows(AsrFailure.class, this::call).status);
        assertEquals(0, calls.get());
    }

    @Test
    void configuredOnboardAddressRequiresExplicitOptIn() {
        settings.setBaseUrl("http://localhost:" + server.getAddress().getPort());
        assertEquals(503, assertThrows(AsrFailure.class, this::call).status);
        assertEquals(0, calls.get());
        settings.setAllowRemoteLocalAsr(true);
        reply(200, success(id));
        assertEquals("暂停任务", call().text());
        assertEquals(1, calls.get());
    }

    @Test
    void timeoutQuarantinedNoRetry() {
        server.createContext(
                "/internal/asr/transcriptions",
                e -> {
                    calls.incrementAndGet();
                    try {
                        Thread.sleep(300);
                    } catch (InterruptedException ignored) {
                    }
                    e.close();
                });
        var e =
                assertThrows(
                        AsrFailure.class,
                        () ->
                                provider.transcribe(
                                        new SpeechProvider.Audio(
                                                id, "zh-CN", "audio/mpeg", new byte[] {1}),
                                        System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(100)));
        assertEquals(504, e.status);
        assertTrue(e.uncertain);
        assertTrue(calls.get() <= 1);
    }
}
