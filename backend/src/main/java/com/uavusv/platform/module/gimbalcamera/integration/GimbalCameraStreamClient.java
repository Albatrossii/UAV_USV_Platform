package com.uavusv.platform.module.gimbalcamera.integration;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.module.gimbalcamera.service.GimbalCameraFrameService;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** Receives the gateway's broadcast camera_frame JSON without sending control messages. */
@Component
@ConditionalOnProperty(name = "app.gimbal-camera.stream-enabled", havingValue = "true")
public class GimbalCameraStreamClient implements WebSocket.Listener {
    private static final Logger log = LoggerFactory.getLogger(GimbalCameraStreamClient.class);
    private final ObjectMapper mapper;
    private final GimbalCameraFrameService frames;
    private final URI endpoint;
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final ScheduledExecutorService executor = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread thread = new Thread(r, "gimbal-camera-stream");
        thread.setDaemon(true);
        return thread;
    });
    private final AtomicBoolean connecting = new AtomicBoolean();
    private final StringBuilder message = new StringBuilder();
    private volatile WebSocket socket;
    private volatile boolean stopping;

    public GimbalCameraStreamClient(ObjectMapper mapper, GimbalCameraFrameService frames,
            @Value("${app.gimbal-camera.stream-websocket-url:ws://10.16.88.211:8765/ws}") String endpoint) {
        this.mapper = mapper;
        this.frames = frames;
        this.endpoint = URI.create(endpoint);
    }

    @PostConstruct public void start() { executor.schedule(this::connect, 0, TimeUnit.SECONDS); }

    private void connect() {
        if (stopping || socket != null || !connecting.compareAndSet(false, true)) return;
        client.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(3)).buildAsync(endpoint, this)
                .whenComplete((ws, error) -> {
                    connecting.set(false);
                    if (error != null && !stopping) {
                        log.warn("Gimbal video gateway connection failed: {}", error.getClass().getSimpleName());
                        executor.schedule(this::connect, 3, TimeUnit.SECONDS);
                    }
                });
    }

    @Override public void onOpen(WebSocket webSocket) {
        socket = webSocket;
        log.info("Connected to gimbal video gateway {}", endpoint);
        webSocket.request(1);
    }

    @Override public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
        message.append(data);
        if (last) {
            try {
                if (frames.accept(mapper.readTree(message.toString()))) {
                    log.debug("Received gimbal JPEG frame {}x{}", frames.width(), frames.height());
                }
            } catch (Exception ignored) {
                // Ignore non-frame JSON messages such as gateway_hello and fleet_snapshot.
            } finally {
                message.setLength(0);
            }
        }
        webSocket.request(1);
        return CompletableFuture.completedFuture(null);
    }

    @Override public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
        disconnect();
        return CompletableFuture.completedFuture(null);
    }

    @Override public void onError(WebSocket webSocket, Throwable error) { disconnect(); }

    private void disconnect() {
        socket = null;
        if (!stopping) executor.schedule(this::connect, 3, TimeUnit.SECONDS);
    }

    @PreDestroy public void stop() {
        stopping = true;
        WebSocket current = socket;
        socket = null;
        if (current != null) current.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown");
        executor.shutdownNow();
    }
}
