package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

class AliyunAsrProviderTests {
    private HttpServer server;
    private AsrSettings settings;
    private AliyunAsrProvider provider;
    private final AtomicInteger calls = new AtomicInteger();
    private final AtomicReference<byte[]> uploaded = new AtomicReference<>();
    private final String requestId = UUID.randomUUID().toString();

    @BeforeEach
    void setup() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.start();
        settings = new AsrSettings();
        settings.setProvider("aliyun");
        settings.setAliyunAppKey("testappkey");
        settings.setAliyunToken("test-token");
        provider = new AliyunAsrProvider(settings, new ObjectMapper(),
                URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/stream/v1/asr"));
    }

    @AfterEach
    void close() {
        server.stop(0);
    }

    private void reply(int httpStatus, String body) {
        server.createContext("/stream/v1/asr", exchange -> {
            calls.incrementAndGet();
            assertEquals("POST", exchange.getRequestMethod());
            assertEquals("test-token", exchange.getRequestHeaders().getFirst("X-NLS-Token"));
            assertEquals("application/octet-stream", exchange.getRequestHeaders().getFirst("Content-Type"));
            assertTrue(exchange.getRequestURI().getRawQuery().contains("appkey=testappkey"));
            assertTrue(exchange.getRequestURI().getRawQuery().contains("format=wav&sample_rate=16000"));
            uploaded.set(exchange.getRequestBody().readAllBytes());
            byte[] response = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(httpStatus, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
    }

    private SpeechProvider.Transcript call(String mime, byte[] audio) {
        return provider.transcribe(new SpeechProvider.Audio(requestId, "zh-CN", mime, audio),
                System.nanoTime() + TimeUnit.SECONDS.toNanos(10));
    }

    private static byte[] wav(int samples) {
        ByteBuffer out = ByteBuffer.allocate(44 + samples * 2).order(ByteOrder.LITTLE_ENDIAN);
        out.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(36 + samples * 2);
        out.put("WAVEfmt ".getBytes(StandardCharsets.US_ASCII)).putInt(16);
        out.putShort((short) 1).putShort((short) 1).putInt(16000);
        out.putInt(32000).putShort((short) 2).putShort((short) 16);
        out.put("data".getBytes(StandardCharsets.US_ASCII)).putInt(samples * 2);
        for (int i = 0; i < samples; i++) out.putShort((short) (i % 50));
        return out.array();
    }

    @Test
    void qualifiedWavPassesThroughWithCloudMetadata() {
        byte[] audio = wav(16000);
        reply(200, "{\"task_id\":\"abc\",\"status\":20000000,\"message\":\"SUCCESS\",\"result\":\"开始任务\"}");
        var result = call("audio/wav", audio);
        assertEquals("开始任务", result.text());
        assertEquals(1000, result.durationMs());
        assertEquals("aliyun-asr", result.provider());
        assertEquals("aliyun-shiyinshi-v1", result.model());
        assertArrayEquals(audio, uploaded.get());
        assertEquals(1, calls.get());
    }

    @Test
    void blankResultReleasesFinishedAudioAsNoSpeech() {
        reply(200, "{\"status\":20000000,\"result\":\"  \",\"message\":\"SUCCESS\"}");
        AsrFailure error = assertThrows(AsrFailure.class, () -> call("audio/wav", wav(16000)));
        assertEquals(422, error.status);
        assertEquals("VOICE_NO_SPEECH", error.code);
        assertFalse(error.uncertain);
    }

    @Test
    void expiredTokenAndRateLimitAreDistinct() {
        reply(200, "{\"status\":40000001,\"result\":\"\",\"message\":\"token invalid\"}");
        AsrFailure expired = assertThrows(AsrFailure.class, () -> call("audio/wav", wav(16000)));
        assertEquals(503, expired.status);
        assertEquals("VOICE_PROVIDER_UNAVAILABLE", expired.code);
        server.removeContext("/stream/v1/asr");
        reply(200, "{\"status\":40000005,\"result\":\"\",\"message\":\"rate limited\"}");
        AsrFailure limited = assertThrows(AsrFailure.class, () -> call("audio/wav", wav(16000)));
        assertEquals(429, limited.status);
        assertEquals(2, limited.retryAfter);
    }

    @Test
    void malformedSuccessAndHttpFailureAreControlled() {
        reply(200, "not json");
        AsrFailure malformed = assertThrows(AsrFailure.class, () -> call("audio/wav", wav(16000)));
        assertEquals(502, malformed.status);
        assertTrue(malformed.uncertain);
        server.removeContext("/stream/v1/asr");
        reply(500, "upstream failed");
        AsrFailure http = assertThrows(AsrFailure.class, () -> call("audio/wav", wav(16000)));
        assertEquals(503, http.status);
    }

    @Test
    void officialClientTimeoutAndHttpRateLimitAreMapped() {
        reply(200, "{\"status\":40000004,\"result\":\"\",\"message\":\"client timeout\"}");
        AsrFailure timeout = assertThrows(AsrFailure.class, () -> call("audio/wav", wav(16000)));
        assertEquals(504, timeout.status);
        assertEquals("VOICE_TRANSCRIPTION_TIMEOUT", timeout.code);
        server.removeContext("/stream/v1/asr");
        reply(429, "not json");
        AsrFailure limited = assertThrows(AsrFailure.class, () -> call("audio/wav", wav(16000)));
        assertEquals(429, limited.status);
        assertEquals("VOICE_RATE_LIMITED", limited.code);
    }

    @Test
    void moreThanSixtySecondsIsRejectedBeforeCloud() {
        AsrFailure error = assertThrows(AsrFailure.class,
                () -> call("audio/wav", wav(16000 * 60 + 1)));
        assertEquals(413, error.status);
        assertEquals("VOICE_AUDIO_TOO_LONG", error.code);
        assertEquals(0, calls.get());
    }

    @Test
    void missingConverterHasExplicitErrorForBrowserWebm() {
        settings.setAliyunFfmpegPath("missing-ffmpeg-for-test-" + UUID.randomUUID());
        AsrFailure error = assertThrows(AsrFailure.class,
                () -> call("audio/webm", new byte[] {1, 2, 3}));
        assertEquals(503, error.status);
        assertEquals("VOICE_AUDIO_CONVERTER_UNAVAILABLE", error.code);
        assertEquals(0, calls.get());
    }

    @Test
    void realFfmpegConvertsWebmTo16kMonoPcmWavWhenAvailable() throws Exception {
        String ffmpeg = System.getenv("TEST_FFMPEG_PATH");
        org.junit.jupiter.api.Assumptions.assumeTrue(ffmpeg != null && !ffmpeg.isBlank());
        settings.setAliyunFfmpegPath(ffmpeg);
        Process source = new ProcessBuilder(ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=700:duration=0.4",
                "-c:a", "libopus", "-f", "webm", "pipe:1")
                .redirectError(ProcessBuilder.Redirect.DISCARD).start();
        byte[] webm = source.getInputStream().readAllBytes();
        assertEquals(0, source.waitFor());
        reply(200, "{\"status\":20000000,\"result\":\"测试\",\"message\":\"SUCCESS\"}");
        var result = call("audio/webm", webm);
        assertEquals("aliyun-asr", result.provider());
        assertTrue(result.durationMs() >= 390 && result.durationMs() <= 410);
        byte[] converted = uploaded.get();
        assertEquals("RIFF", new String(converted, 0, 4, StandardCharsets.US_ASCII));
        assertEquals(16000, ByteBuffer.wrap(converted, 24, 4).order(ByteOrder.LITTLE_ENDIAN).getInt());
        assertEquals(1, ByteBuffer.wrap(converted, 22, 2).order(ByteOrder.LITTLE_ENDIAN).getShort());
        assertEquals(16, ByteBuffer.wrap(converted, 34, 2).order(ByteOrder.LITTLE_ENDIAN).getShort());
    }
}
