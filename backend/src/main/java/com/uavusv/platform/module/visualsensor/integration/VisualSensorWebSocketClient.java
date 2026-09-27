package com.uavusv.platform.module.visualsensor.integration;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.visualsensor.service.VisualSensorService;
import com.uavusv.platform.module.sensor.service.SensorRuntimeService;
import com.uavusv.platform.module.gateway.v1.GatewayEnvelope;
import com.uavusv.platform.module.gateway.v1.GatewayMessageType;
import com.uavusv.platform.module.gateway.v1.RealtimeHub;
import java.time.Instant;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
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

@Component
@ConditionalOnProperty(name = "app.visual-sensor.websocket-enabled", havingValue = "true")
public class VisualSensorWebSocketClient implements WebSocket.Listener {

    private static final Logger log = LoggerFactory.getLogger(VisualSensorWebSocketClient.class);
    private final ObjectMapper objectMapper;
    private final VisualSensorService visualSensorService;
    private final SensorRuntimeService sensorRuntimeService;
    private final RealtimeHub realtimeHub;
    private final URI endpoint;
    private final HttpClient httpClient;
    private final ScheduledExecutorService executor;
    private final AtomicBoolean connecting = new AtomicBoolean();
    private final StringBuilder messageBuffer = new StringBuilder();
    private volatile WebSocket socket;
    private volatile boolean shuttingDown;
    private volatile String lastFocusedCamera = "";

    public VisualSensorWebSocketClient(
            ObjectMapper objectMapper,
            VisualSensorService visualSensorService,
            SensorRuntimeService sensorRuntimeService,
            RealtimeHub realtimeHub,
            @Value("${app.visual-sensor.websocket-url:ws://127.0.0.1:8766/visual_sensors}") String endpoint
    ) {
        this.objectMapper = objectMapper;
        this.visualSensorService = visualSensorService;
        this.sensorRuntimeService = sensorRuntimeService;
        this.realtimeHub = realtimeHub;
        this.endpoint = URI.create(endpoint);
        this.httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
        this.executor = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "visual-sensor-websocket");
            thread.setDaemon(true);
            return thread;
        });
    }

    @PostConstruct
    public void start() {
        executor.schedule(this::connect, 0, TimeUnit.SECONDS);
        executor.scheduleAtFixedRate(this::syncSubscription, 1, 1, TimeUnit.SECONDS);
    }

    private void connect() {
        if (shuttingDown || socket != null || !connecting.compareAndSet(false, true)) {
            return;
        }
        httpClient.newWebSocketBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .buildAsync(endpoint, this)
                .whenComplete((ignored, error) -> {
                    connecting.set(false);
                    if (error != null) {
                        visualSensorService.observeGateway(false, "无法连接 " + endpoint);
                        executor.schedule(this::connect, 2, TimeUnit.SECONDS);
                    }
                });
    }

    @Override
    public void onOpen(WebSocket webSocket) {
        socket = webSocket;
        visualSensorService.observeGateway(true, "视觉传感器网关在线");
        log.info("Connected to visual sensor WebSocket {}", endpoint);
        sendSubscription(true);
        webSocket.request(1);
    }

    @Override
    public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
        messageBuffer.append(data);
        if (last) {
            String payload = messageBuffer.toString();
            messageBuffer.setLength(0);
            try {
                JsonNode root = objectMapper.readTree(payload);
                acceptMessage(root);
            } catch (Exception exception) {
                log.debug("Ignored invalid visual sensor frame: {}", exception.getMessage());
            }
        }
        webSocket.request(1);
        return CompletableFuture.completedFuture(null);
    }

    void acceptMessage(JsonNode root) {
        String type = root.path("message_type").asText(root.path("type").asText());
        JsonNode data = root.has("message_type") ? root.path("data") : root;
        if ("camera_frame".equals(type)) {
            if (!root.has("message_type")) {
                visualSensorService.observeFrame(data);
                return;
            }
            String encoding = data.path("encoding").asText("");
            if (!"image/jpeg".equalsIgnoreCase(encoding) && !"jpeg".equalsIgnoreCase(encoding)) return;
            String camera = data.path("vehicle_id").asText("");
            if (camera.isBlank()) camera = data.path("stream_id").asText("");
            visualSensorService.observeJpegFrame(camera, data.path("data_base64").asText(""),
                    data.path("width").asInt(), data.path("height").asInt(), 0, 0);
        } else if ("pointcloud_frame".equals(type)) {
            sensorRuntimeService.observePointCloudFrame(data);
        } else if ("radar_frame".equals(type)) {
            sensorRuntimeService.observeRadarFrame(data);
        } else if ("perception_targets".equals(type)) {
            acceptTargets(root, data);
        }
    }

    private void acceptTargets(JsonNode root, JsonNode data) {
        // Prefer a current v1 stream when it exists; this adapter supplies the
        // observation stream on gateways that publish targets only through /ws.
        Instant now = Instant.now();
        var previous = realtimeHub.latestTargetBatch();
        if (previous.isPresent() && !"gateway-ws-perception".equals(previous.get().source())
                && previous.get().timestamp().isAfter(now.minusSeconds(3))) return;
        if (!data.path("targets").isArray()) return;
        ObjectNode payload = objectMapper.createObjectNode();
        String frameId = data.path("frame_id").asText("");
        payload.put("frameId", frameId);
        payload.put("snapshotTime", now.toString());
        ArrayNode targets = payload.putArray("targets");
        for (JsonNode input : data.path("targets")) {
            String id = input.path("track_id").asText("");
            if (id.isBlank()) continue;
            ObjectNode target = targets.addObject();
            target.put("id", id);
            // /ws timestamps can use the simulator clock; freshness tracks arrival.
            target.put("timestamp", now.toString());
            if (input.has("timestamp")) target.set("sourceTimestamp", input.get("timestamp"));
            target.put("frameId", input.path("frame_id").asText(frameId));
            JsonNode position = input.path("position");
            boolean valid = "map".equals(target.path("frameId").asText());
            for (String axis : new String[]{"x", "y", "z"}) {
                valid &= position.path(axis).isNumber() && Double.isFinite(position.path(axis).asDouble());
            }
            target.put("coordinateValid", valid);
            if (valid) target.set("position", position);
            target.put("classification", input.path("class_name").asText(""));
            target.put("sourceStream", data.path("selected_source").asText("perception_targets"));
            if (input.path("confidence").isNumber()) target.set("confidence", input.path("confidence"));
            if (input.path("velocity").isObject()) target.putObject("velocity").set("linear", input.path("velocity"));
        }
        realtimeHub.publish(new GatewayEnvelope("v1", GatewayMessageType.TELEMETRY_TARGET_BATCH,
                "gateway-ws-perception", now, null, "perception_targets", frameId,
                root.path("sequence").asLong(), payload));
    }

    @Override
    public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
        disconnect("连接关闭: " + statusCode + " " + reason);
        return CompletableFuture.completedFuture(null);
    }

    @Override
    public void onError(WebSocket webSocket, Throwable error) {
        disconnect("连接异常: " + error.getMessage());
    }

    private void syncSubscription() {
        if (socket == null) {
            connect();
            return;
        }
        String focus = visualSensorService.focusedCameraId();
        if (!focus.equals(lastFocusedCamera)) {
            sendSubscription(false);
        }
    }

    private void sendSubscription(boolean force) {
        WebSocket current = socket;
        if (current == null) {
            return;
        }
        String focus = visualSensorService.focusedCameraId();
        if (!force && focus.equals(lastFocusedCamera)) {
            return;
        }
        ObjectNode frame = objectMapper.createObjectNode();
        frame.put("type", "sensor_subscription");
        frame.put("focused_camera_id", focus);
        frame.put("thumbnail_fps", 2);
        frame.put("focused_fps", 12);
        ArrayNode cameras = frame.putArray("camera_ids");
        visualSensorService.cameraIds().forEach(cameras::add);
        current.sendText(frame.toString(), true);
        lastFocusedCamera = focus;
    }

    private void disconnect(String detail) {
        socket = null;
        lastFocusedCamera = "";
        visualSensorService.observeGateway(false, detail);
        if (!shuttingDown) {
            executor.schedule(this::connect, 2, TimeUnit.SECONDS);
        }
    }

    @PreDestroy
    public void stop() {
        shuttingDown = true;
        WebSocket current = socket;
        socket = null;
        if (current != null) {
            current.sendClose(WebSocket.NORMAL_CLOSURE, "platform stopping");
        }
        executor.shutdownNow();
    }
}
