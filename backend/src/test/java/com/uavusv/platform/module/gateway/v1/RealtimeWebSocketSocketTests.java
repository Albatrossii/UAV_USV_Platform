package com.uavusv.platform.module.gateway.v1;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.web.servlet.DispatcherServletAutoConfiguration;
import org.springframework.boot.autoconfigure.web.servlet.ServletWebServerFactoryAutoConfiguration;
import org.springframework.boot.autoconfigure.websocket.servlet.WebSocketServletAutoConfiguration;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.ApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.web.servlet.config.annotation.EnableWebMvc;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

import javax.sql.DataSource;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;
import java.util.Queue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(
        classes = RealtimeWebSocketSocketTests.Config.class,
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {
                "spring.config.name=realtime-websocket-isolated-test",
                "spring.main.banner-mode=off",
                "server.address=127.0.0.1"
        })
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class RealtimeWebSocketSocketTests {

    @Autowired private RealtimeWebSocketHandler handler;
    @Autowired private ObjectMapper objectMapper;
    @Autowired private ApplicationContext applicationContext;
    @LocalServerPort private int port;

    @Test
    void isolatedContextHasNoDatabaseOrBusinessRuntimeBeans() {
        List<Class<?>> applicationTypes = Arrays.stream(applicationContext.getBeanDefinitionNames())
                .map(applicationContext::getType)
                .filter(Objects::nonNull)
                .filter(type -> type.getPackageName().startsWith("com.uavusv.platform"))
                .toList();

        assertThat(applicationTypes).containsExactlyInAnyOrder(Config.class, RealtimeWebSocketHandler.class);
        assertThat(applicationContext.getBeansOfType(DataSource.class)).isEmpty();
    }

    @Test
    void realSocketProbeMeasuresRoundTripAndPreservesBroadcastIsolation() throws Exception {
        HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
        URI endpoint = URI.create("ws://127.0.0.1:" + port + "/api/v1/realtime");
        RecordingListener requester = new RecordingListener();
        RecordingListener observer = new RecordingListener();
        WebSocket requesterSocket = connect(client, endpoint, requester);
        WebSocket observerSocket = null;
        try {
            observerSocket = connect(client, endpoint, observer);
            // Receiving its own pong also establishes that the observer is registered server-side.
            CompletableFuture<ReceivedMessage> observerReady = observer.expectMessage();
            observerSocket.sendText("{\"type\":\"realtime.ping\",\"id\":\"observer-ready\"}", true)
                    .get(5, TimeUnit.SECONDS);
            assertPong(observerReady.get(5, TimeUnit.SECONDS), "observer-ready");

            CompletableFuture<ReceivedMessage> pong = requester.expectMessage();
            CompletableFuture<ReceivedMessage> observerBroadcast = observer.expectMessage();
            long started = System.nanoTime();
            requesterSocket.sendText("{\"type\":\"realtime.ping\",\"id\":\"socket-rtt-1\"}", true)
                    .get(5, TimeUnit.SECONDS);
            ReceivedMessage reply = pong.get(5, TimeUnit.SECONDS);
            assertPong(reply, "socket-rtt-1");
            long roundTripNanos = reply.receivedAtNanos() - started;
            assertThat(roundTripNanos).isPositive().isLessThan(TimeUnit.SECONDS.toNanos(5));

            GatewayEnvelope envelope = new GatewayEnvelope(
                    "1.0", GatewayMessageType.TELEMETRY_TARGET_BATCH, "isolated-test", null,
                    "test-run", "fleet.targets.test", 1,
                    objectMapper.createObjectNode().putArray("targets").addObject().put("id", "test-target"));
            CompletableFuture<ReceivedMessage> requesterBroadcast = requester.expectMessage();
            handler.broadcast(envelope);

            String expectedBroadcast = objectMapper.writeValueAsString(envelope);
            assertThat(requesterBroadcast.get(5, TimeUnit.SECONDS).payload()).isEqualTo(expectedBroadcast);
            // WebSocket ordering makes this a barrier: a broadcast probe would arrive here first.
            assertThat(observerBroadcast.get(5, TimeUnit.SECONDS).payload()).isEqualTo(expectedBroadcast);
            assertThat(requester.unexpectedMessages).isEmpty();
            assertThat(observer.unexpectedMessages).isEmpty();
            System.out.printf("Isolated realtime WebSocket RTT: %.3f ms (loopback port %d)%n",
                    roundTripNanos / 1_000_000.0, port);
        } finally {
            requesterSocket.abort();
            if (observerSocket != null) {
                observerSocket.abort();
            }
        }
    }

    private WebSocket connect(HttpClient client, URI endpoint, RecordingListener listener) throws Exception {
        return client.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(5))
                .buildAsync(endpoint, listener).get(5, TimeUnit.SECONDS);
    }

    private void assertPong(ReceivedMessage message, String id) throws Exception {
        assertThat(objectMapper.readTree(message.payload())).isEqualTo(objectMapper.createObjectNode()
                .put("type", "realtime.pong").put("id", id));
    }

    private record ReceivedMessage(String payload, long receivedAtNanos) {}

    private static class RecordingListener implements WebSocket.Listener {
        private final Queue<CompletableFuture<ReceivedMessage>> pending = new ConcurrentLinkedQueue<>();
        private final List<ReceivedMessage> unexpectedMessages = new CopyOnWriteArrayList<>();
        private final StringBuilder fragments = new StringBuilder();

        CompletableFuture<ReceivedMessage> expectMessage() {
            CompletableFuture<ReceivedMessage> next = new CompletableFuture<>();
            pending.add(next);
            return next;
        }

        @Override
        public void onOpen(WebSocket webSocket) {
            webSocket.request(1);
        }

        @Override
        public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
            fragments.append(data);
            if (last) {
                ReceivedMessage received = new ReceivedMessage(fragments.toString(), System.nanoTime());
                fragments.setLength(0);
                CompletableFuture<ReceivedMessage> next = pending.poll();
                if (next == null) {
                    unexpectedMessages.add(received);
                } else {
                    next.complete(received);
                }
            }
            webSocket.request(1);
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public void onError(WebSocket webSocket, Throwable error) {
            pending.forEach(next -> next.completeExceptionally(error));
        }
    }

    // Explicit imports only: no component scan, broad auto-configuration, database, or runtime services.
    @Configuration(proxyBeanMethods = false)
    @EnableWebMvc
    @EnableWebSocket
    @Import({ServletWebServerFactoryAutoConfiguration.class, DispatcherServletAutoConfiguration.class,
            WebSocketServletAutoConfiguration.class, RealtimeWebSocketHandler.class})
    static class Config implements WebSocketConfigurer {
        private final RealtimeWebSocketHandler handler;

        Config(RealtimeWebSocketHandler handler) {
            this.handler = handler;
        }

        @Bean
        static ObjectMapper objectMapper() {
            return new ObjectMapper();
        }

        @Override
        public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
            registry.addHandler(handler, "/api/v1/realtime");
        }
    }
}
