package com.uavusv.platform.module.gateway.v1;

import jakarta.annotation.PreDestroy;
import com.fasterxml.jackson.databind.JsonNode;
import com.uavusv.platform.module.mission.service.MissionRuntimeReconciler;
import com.uavusv.platform.module.monitoring.service.RuntimeStateService;
import com.uavusv.platform.module.sensor.service.RadarScanInput;
import com.uavusv.platform.module.sensor.service.SensorRuntimeService;
import com.uavusv.platform.module.visualsensor.service.VisualSensorService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

@Component
@org.springframework.boot.autoconfigure.condition.ConditionalOnExpression(
        "('${app.ros.transport:v1}' == 'v1' || '${app.ros.transport:v1}' == 'dual-test') && ${app.gateway.v1.enabled:false}")
public class RosGatewayV1WebSocketClient implements WebSocket.Listener {

    private static final Logger log = LoggerFactory.getLogger(RosGatewayV1WebSocketClient.class);
    private static final long CAMERA_FRAME_LOG_INTERVAL_MILLIS = 5_000;
    private static final long TARGET_BATCH_LOG_INTERVAL_MILLIS = 5_000;

    private final GatewayEnvelopeDecoder gatewayEnvelopeDecoder;
    private final GatewayProtobufDecoder gatewayProtobufDecoder;
    private final GatewaySequenceGuard gatewaySequenceGuard;
    private final RealtimeHub realtimeHub;
    private final ApplicationEventPublisher eventPublisher;
    private final RuntimeStateService runtimeStateService;
    private final MissionRuntimeReconciler missionRuntimeReconciler;
    private final VisualSensorService visualSensorService;
    private final SensorRuntimeService sensorRuntimeService;
    private final URI endpoint;
    private final boolean stateAuthority;
    private final HttpClient httpClient;
    private final ScheduledExecutorService reconnectExecutor;
    private final AtomicBoolean connecting = new AtomicBoolean(false);
    private final AtomicBoolean reconnectScheduled = new AtomicBoolean(false);
    private final StringBuilder messageBuffer = new StringBuilder();
    private final ByteArrayOutputStream binaryMessageBuffer = new ByteArrayOutputStream();
    private final Map<String, Long> cameraFrameLogTimes = new ConcurrentHashMap<>();
    private final Map<String, Long> targetBatchLogTimes = new ConcurrentHashMap<>();
    private volatile WebSocket socket;
    private volatile boolean closing;

    record CameraJpegPayload(
            String cameraId,
            String jpegBase64,
            int width,
            int height,
            long timestampMs
    ) {
    }

    public RosGatewayV1WebSocketClient(
            GatewayEnvelopeDecoder gatewayEnvelopeDecoder,
            GatewayProtobufDecoder gatewayProtobufDecoder,
            GatewaySequenceGuard gatewaySequenceGuard,
            RealtimeHub realtimeHub,
            ApplicationEventPublisher eventPublisher,
            RuntimeStateService runtimeStateService,
            MissionRuntimeReconciler missionRuntimeReconciler,
            VisualSensorService visualSensorService,
            SensorRuntimeService sensorRuntimeService,
            @Value("${app.gateway.v1.websocket-url:ws://127.0.0.1:8765/uav_usv/v1}") String websocketUrl,
            @Value("${app.ros.state-authority:v1}") String stateAuthority
    ) {
        this.gatewayEnvelopeDecoder = gatewayEnvelopeDecoder;
        this.gatewayProtobufDecoder = gatewayProtobufDecoder;
        this.gatewaySequenceGuard = gatewaySequenceGuard;
        this.realtimeHub = realtimeHub;
        this.eventPublisher = eventPublisher;
        this.runtimeStateService = runtimeStateService;
        this.missionRuntimeReconciler = missionRuntimeReconciler;
        this.visualSensorService = visualSensorService;
        this.sensorRuntimeService = sensorRuntimeService;
        this.endpoint = URI.create(websocketUrl);
        this.stateAuthority = "v1".equalsIgnoreCase(stateAuthority);
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .build();
        this.reconnectExecutor = Executors.newSingleThreadScheduledExecutor(task -> {
            Thread thread = new Thread(task, "ros-gateway-v1-reconnect");
            thread.setDaemon(true);
            return thread;
        });
    }

    @EventListener(ApplicationReadyEvent.class)
    public void connect() {
        if (closing || socket != null || !connecting.compareAndSet(false, true)) {
            return;
        }
        httpClient.newWebSocketBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .buildAsync(endpoint, this)
                .whenComplete((webSocket, error) -> {
                    connecting.set(false);
                    if (error != null) {
                        log.warn("ROS Gateway v1 connection failed: {}", error.getMessage());
                        scheduleReconnect();
                    }
                });
    }

    public void reconnect() {
        WebSocket current = socket;
        socket = null;
        runtimeStateService.observeGatewayConnection(false);
        if (current != null) {
            current.abort();
        }
        scheduleReconnect();
    }

    public void sendBinaryEnvelope(byte[] payload) {
        WebSocket current = socket;
        if (current == null) {
            throw new IllegalStateException("ROS Gateway v1 WebSocket is not connected");
        }
        if (payload == null || payload.length == 0) {
            throw new IllegalArgumentException("ROS Gateway v1 binary payload must not be empty");
        }
        try {
            current.sendBinary(ByteBuffer.wrap(payload), true).get(3, TimeUnit.SECONDS);
        } catch (Exception exception) {
            throw new IllegalStateException("Failed to send ROS Gateway v1 binary envelope", exception);
        }
    }

    @Override
    public void onOpen(WebSocket webSocket) {
        socket = webSocket;
        runtimeStateService.observeGatewayConnection(true);
        log.info("ROS Gateway v1 connected endpoint={}", endpoint);
        webSocket.request(1);
    }

    @Override
    public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
        synchronized (messageBuffer) {
            messageBuffer.append(data);
            if (last) {
                String payload = messageBuffer.toString();
                messageBuffer.setLength(0);
                handleTextPayload(payload);
            }
        }
        webSocket.request(1);
        return CompletableFuture.completedFuture(null);
    }

    @Override
    public CompletionStage<?> onBinary(WebSocket webSocket, ByteBuffer data, boolean last) {
        byte[] chunk = new byte[data.remaining()];
        data.get(chunk);
        synchronized (binaryMessageBuffer) {
            binaryMessageBuffer.writeBytes(chunk);
            if (last) {
                byte[] payload = binaryMessageBuffer.toByteArray();
                binaryMessageBuffer.reset();
                handleBinaryPayload(payload);
            }
        }
        webSocket.request(1);
        return CompletableFuture.completedFuture(null);
    }

    @Override
    public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
        handleDisconnect("connection closed: " + statusCode + " " + reason);
        return CompletableFuture.completedFuture(null);
    }

    @Override
    public void onError(WebSocket webSocket, Throwable error) {
        handleDisconnect("connection error: " + error.getMessage());
    }

    @PreDestroy
    public void close() {
        closing = true;
        WebSocket current = socket;
        socket = null;
        runtimeStateService.observeGatewayConnection(false);
        reconnectExecutor.shutdownNow();
        if (current != null) {
            current.sendClose(WebSocket.NORMAL_CLOSURE, "platform stopping");
        }
    }

    private void handleTextPayload(String payload) {
        try {
            GatewayEnvelope envelope = gatewayEnvelopeDecoder.decode(payload);
            log.info("ROS Gateway v1 message type {}", envelope.type().wireName());
            GatewaySequenceGuard.SequenceCheckResult result = gatewaySequenceGuard.inspect(envelope);
            if (result.accepted()) {
                handleAcceptedEnvelope(envelope);
            } else {
                log.warn("ROS Gateway v1 rejected sequence result {}", result.status());
            }
        } catch (IllegalArgumentException exception) {
            log.warn("Ignored invalid ROS Gateway v1 message: {}", exception.getMessage());
        }
    }

    private void handleBinaryPayload(byte[] payload) {
        try {
            GatewayEnvelope envelope = gatewayProtobufDecoder.decode(payload);
            log.info("ROS Gateway v1 binary message type {}", envelope.type().wireName());
            GatewaySequenceGuard.SequenceCheckResult result = gatewaySequenceGuard.inspect(envelope);
            if (result.accepted()) {
                handleAcceptedEnvelope(envelope);
            } else {
                log.warn("ROS Gateway v1 rejected binary sequence result {}", result.status());
            }
        } catch (IllegalArgumentException exception) {
            log.warn("Ignored invalid ROS Gateway v1 binary message: {}", exception.getMessage());
        }
    }

    private void handleAcceptedEnvelope(GatewayEnvelope envelope) {
        if (observeHighVolumeSensor(envelope)) {
            return;
        }
        logTargetBatch(envelope);
        realtimeHub.publish(envelope);
        if (stateAuthority) observeRuntimeState(envelope);
        publishControlAckEvent(envelope);
        reconcileMissionStatus(envelope);
    }

    private boolean observeHighVolumeSensor(GatewayEnvelope envelope) {
        if (envelope.type() == GatewayMessageType.MEDIA_CAMERA_JPEG) {
            CameraJpegPayload camera = cameraJpegPayload(envelope);
            boolean accepted = visualSensorService.observeJpegFrame(
                    camera.cameraId(),
                    camera.jpegBase64(),
                    camera.width(),
                    camera.height(),
                    camera.timestampMs(),
                    -1
            );
            if (accepted) {
                visualSensorService.observeGateway(true, "ROS Gateway v1 media.camera_jpeg 在线");
            }
            logCameraFrame(envelope, camera.cameraId(), camera.width(), camera.height(), camera.jpegBase64().length(), accepted);
            return true;
        }
        if (envelope.type() == GatewayMessageType.PERCEPTION_RADAR_SCAN) {
            JsonNode payload = envelope.payload();
            sensorRuntimeService.observeRadarScan(new RadarScanInput(
                    text(payload, "sensorId"),
                    payload.path("timestampMs").asLong(envelope.timestamp().toEpochMilli()),
                    payload.path("angleMinRad").asDouble(),
                    payload.path("angleIncrementRad").asDouble(),
                    payload.path("rangeMinM").asDouble(),
                    payload.path("rangeMaxM").asDouble(),
                    doubles(payload.path("rangesM")),
                    doubles(payload.path("intensities"))
            ));
            return true;
        }
        return false;
    }

    private void handleDisconnect(String detail) {
        socket = null;
        runtimeStateService.observeGatewayConnection(false);
        visualSensorService.observeGateway(false, "ROS Gateway v1 disconnected: " + detail);
        if (!closing) {
            log.warn("ROS Gateway v1 disconnected: {}", detail);
            scheduleReconnect();
        }
    }

    private void publishControlAckEvent(GatewayEnvelope envelope) {
        if (envelope.type() != GatewayMessageType.CONTROL_ACK
                && envelope.type() != GatewayMessageType.CONTROL_FEEDBACK
                && envelope.type() != GatewayMessageType.CONTROL_RESULT) {
            return;
        }
        JsonNode payload = envelope.payload();
        String commandKey = text(payload, "commandId");
        if (commandKey.isBlank()) {
            log.warn("ROS Gateway v1 {} ignored because commandId is empty", envelope.type().wireName());
            return;
        }
        String status = text(payload, "status").toUpperCase();
        if (status.isBlank()) return;
        String message = text(payload, "message");
        String code = text(payload, "code");
        String detail = message.isBlank()
                ? envelope.type().wireName() + " " + status
                : message;
        String errorCode = isFailureStatus(status)
                ? (code.isBlank() ? "ROS_GATEWAY_V1_COMMAND_FAILED" : code)
                : null;
        eventPublisher.publishEvent(new RosGatewayV1ControlAckEvent(
                commandKey, envelope.runId(), status, detail, errorCode));
    }

    private void observeRuntimeState(GatewayEnvelope envelope) {
        if (envelope.type() == GatewayMessageType.GATEWAY_HEARTBEAT) {
            runtimeStateService.observeGatewayHeartbeat(
                    text(envelope.payload(), "instanceId"),
                    envelope.sequence()
            );
            return;
        }
        if (envelope.type() == GatewayMessageType.TELEMETRY_POSE_BATCH) {
            runtimeStateService.observeGatewayPoseBatch(envelope.payload(), envelope.sequence());
            return;
        }
        if (envelope.type() == GatewayMessageType.DEVICE_STATUS) {
            runtimeStateService.observeGatewayDeviceStatus(
                    envelope.payload(), envelope.source(), envelope.streamId(), envelope.sequence());
        }
    }

    private void reconcileMissionStatus(GatewayEnvelope envelope) {
        if (envelope.type() != GatewayMessageType.MISSION_STATUS) {
            return;
        }
        JsonNode payload = envelope.payload();
        try {
            missionRuntimeReconciler.reconcileMissionStatus(
                    text(payload, "missionId", "mission_id"),
                    envelope.runId(),
                    text(payload, "runId", "run_id"),
                    text(payload, "activeCommandId", "active_command_id"),
                    text(payload, "state"),
                    text(payload, "phase"),
                    "ROS_GATEWAY_V1"
            );
        } catch (RuntimeException exception) {
            log.warn("Unable to reconcile ROS Gateway v1 mission.status runId={} state={}: {}",
                    envelope.runId(), text(payload, "state"), exception.getMessage());
        }
    }

    private boolean isFailureStatus(String status) {
        return switch (status) {
            case "FAILED", "TIMEOUT", "CANCELLED", "REJECTED", "EXPIRED" -> true;
            default -> false;
        };
    }

    static CameraJpegPayload cameraJpegPayload(GatewayEnvelope envelope) {
        JsonNode payload = envelope.payload();
        return new CameraJpegPayload(
                text(payload, "cameraId", "camera_id"),
                text(payload, "jpegBase64", "jpeg_base64", "jpegData", "jpeg_data"),
                number(payload, 0, "width").intValue(),
                number(payload, 0, "height").intValue(),
                number(payload, envelope.timestamp().toEpochMilli(), "timestampMs", "timestamp_ms").longValue()
        );
    }

    static String text(JsonNode payload, String... fieldNames) {
        if (payload == null) {
            return "";
        }
        for (String fieldName : fieldNames) {
            JsonNode value = payload.path(fieldName);
            if (value.isTextual() && !value.asText().isBlank()) {
                return value.asText().trim();
            }
        }
        return "";
    }

    static Number number(JsonNode payload, Number fallback, String... fieldNames) {
        if (payload == null) {
            return fallback;
        }
        for (String fieldName : fieldNames) {
            JsonNode value = payload.path(fieldName);
            if (value.isNumber()) {
                return value.numberValue();
            }
        }
        return fallback;
    }

    private List<Double> doubles(JsonNode array) {
        if (array == null || !array.isArray()) {
            return List.of();
        }
        List<Double> values = new ArrayList<>(array.size());
        array.forEach(value -> values.add(value.asDouble()));
        return values;
    }

    private void logCameraFrame(
            GatewayEnvelope envelope,
            String cameraId,
            int width,
            int height,
            int base64Length,
            boolean accepted
    ) {
        String key = cameraId == null || cameraId.isBlank() ? envelope.streamId() : cameraId.trim();
        long now = System.currentTimeMillis();
        Long previous = cameraFrameLogTimes.get(key);
        if (previous != null && now - previous < CAMERA_FRAME_LOG_INTERVAL_MILLIS) {
            return;
        }
        cameraFrameLogTimes.put(key, now);
        log.info(
                "Gateway camera frame: streamId={} sequence={} cameraId={} size={}x{} base64Length={} accepted={}",
                envelope.streamId(),
                envelope.sequence(),
                cameraId == null || cameraId.isBlank() ? "<empty>" : cameraId.trim(),
                width,
                height,
                base64Length,
                accepted
        );
    }

    private void logTargetBatch(GatewayEnvelope envelope) {
        if (envelope.type() != GatewayMessageType.TELEMETRY_TARGET_BATCH) return;
        String key = envelope.source() + ":" + envelope.streamId();
        long now = System.currentTimeMillis();
        Long previous = targetBatchLogTimes.get(key);
        if (previous != null && now - previous < TARGET_BATCH_LOG_INTERVAL_MILLIS) return;
        targetBatchLogTimes.put(key, now);
        JsonNode targets = envelope.payload().path("targets");
        JsonNode target = targets.isArray() && !targets.isEmpty() ? targets.path(0) : null;
        log.info(
                "Gateway target batch: streamId={} sequence={} targetCount={} id={} frameId={} "
                        + "coordinateValid={} position={} sourceTimestamp={}",
                envelope.streamId(), envelope.sequence(), targets.isArray() ? targets.size() : 0,
                target == null ? "<none>" : text(target, "id"),
                target == null ? "<none>" : text(target, "frameId"),
                target != null && target.path("coordinateValid").asBoolean(false),
                target == null ? "<none>" : target.path("position"),
                target == null ? "<none>" : text(target, "timestamp")
        );
    }

    private void scheduleReconnect() {
        if (closing || !reconnectScheduled.compareAndSet(false, true)) {
            return;
        }
        reconnectExecutor.schedule(() -> {
            reconnectScheduled.set(false);
            connect();
        }, 2, TimeUnit.SECONDS);
    }
}
