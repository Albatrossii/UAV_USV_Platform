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
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

class AliyunTokenManagerTests {
    private HttpServer server;
    private AsrSettings settings;
    private final AtomicInteger calls = new AtomicInteger();
    private final AtomicReference<String> rawQuery = new AtomicReference<>();

    @BeforeEach
    void setup() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.start();
        settings = new AsrSettings();
        settings.setAliyunToken("");
        settings.setAliyunAccessKeyId("my_access_key_id");
        settings.setAliyunAccessKeySecret("my_access_key_secret");
    }

    @AfterEach
    void close() {
        server.stop(0);
    }

    @Test
    void manualTokenRemainsBackwardCompatibleAndSkipsOpenApi() {
        settings.setAliyunToken("manual-token");
        var manager = manager(Clock.systemUTC(), "unused");
        assertEquals("manual-token", manager.token(deadline()));
        assertEquals(0, calls.get());
    }

    @Test
    void createTokenUsesOfficialSignatureAndCachesUntilRefreshWindow() {
        Instant now = Instant.parse("2019-04-18T08:32:31Z");
        reply(call -> "signed-token", call -> now.getEpochSecond() + 3600);
        var manager = manager(Clock.fixed(now, ZoneOffset.UTC),
                "b924c8c3-6d03-4c5d-ad36-d984d3116788");

        assertEquals("signed-token", manager.token(deadline()));
        assertEquals("signed-token", manager.token(deadline()));
        assertEquals(1, calls.get());
        assertTrue(rawQuery.get().contains("Signature=hHq4yNsPitlfDJ2L0nQPdugdEzM%3D"));
        assertFalse(rawQuery.get().contains("my_access_key_secret"));
    }

    @Test
    void refreshesAfterServerExpiryMinusConfiguredSkew() {
        Instant start = Instant.parse("2026-10-04T09:00:00Z");
        MutableClock clock = new MutableClock(start);
        reply(call -> "token-" + call, call -> start.getEpochSecond() + 600L * call);
        var manager = manager(clock, "fixed-nonce");

        assertEquals("token-1", manager.token(deadline()));
        clock.set(start.plusSeconds(299));
        assertEquals("token-1", manager.token(deadline()));
        clock.set(start.plusSeconds(301));
        assertEquals("token-2", manager.token(deadline()));
        assertEquals(2, calls.get());
    }

    @Test
    void malformedOrExpiredTokenResponseIsUnavailable() {
        server.createContext("/", exchange -> {
            calls.incrementAndGet();
            byte[] body = "{\"Token\":{\"Id\":\"expired\",\"ExpireTime\":1}}"
                    .getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        var manager = manager(Clock.systemUTC(), "fixed-nonce");
        AsrFailure error = assertThrows(AsrFailure.class, () -> manager.token(deadline()));
        assertEquals(503, error.status);
        assertEquals("VOICE_PROVIDER_UNAVAILABLE", error.code);
    }

    private void reply(
            java.util.function.IntFunction<String> token,
            java.util.function.IntToLongFunction expiry) {
        server.createContext("/", exchange -> {
            int call = calls.incrementAndGet();
            assertEquals("GET", exchange.getRequestMethod());
            rawQuery.set(exchange.getRequestURI().getRawQuery());
            String response = "{\"Token\":{\"Id\":\"" + token.apply(call)
                    + "\",\"ExpireTime\":" + expiry.applyAsLong(call) + "}}";
            byte[] body = response.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
    }

    private AliyunTokenManager manager(Clock clock, String nonce) {
        return new AliyunTokenManager(
                settings,
                new ObjectMapper(),
                URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/"),
                clock,
                () -> nonce);
    }

    private static long deadline() {
        return System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
    }

    private static final class MutableClock extends Clock {
        private final AtomicReference<Instant> instant;

        MutableClock(Instant instant) {
            this.instant = new AtomicReference<>(instant);
        }

        void set(Instant value) {
            instant.set(value);
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            if (!ZoneOffset.UTC.equals(zone)) throw new IllegalArgumentException("UTC only");
            return this;
        }

        @Override
        public Instant instant() {
            return instant.get();
        }
    }
}
