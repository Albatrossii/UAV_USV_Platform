package com.uavusv.platform.module.sensor.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.uavusv.platform.module.sensor.dto.RadarItemResponse;
import com.uavusv.platform.module.sensor.dto.RadarOverviewResponse;
import org.springframework.stereotype.Service;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.time.Clock;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class SensorRuntimeService {

    private static final long FRESH_RADAR_MILLIS = 30_000;
    private static final Logger log = LoggerFactory.getLogger(SensorRuntimeService.class);

    private final Clock clock;
    private final Map<String, RadarState> radars = new LinkedHashMap<>();
    private final Map<String, SpectrumState> spectra = new LinkedHashMap<>();

    public SensorRuntimeService() {
        this(Clock.systemUTC());
    }

    SensorRuntimeService(Clock clock) {
        this.clock = clock;
    }

    public synchronized void observeRadarFrame(JsonNode frame) {
        String deviceId = text(frame, "device_id", text(frame, "deviceId", "radar"));
        long now = clock.millis();
        long timestampMs = timestampMs(frame, now);
        List<RadarItemResponse> obstacles = parseItems(frame.path("obstacles"), deviceId, "OBSTACLE", timestampMs);
        List<RadarItemResponse> detections = parseItems(frame.path("detections"), deviceId, "DETECTION", timestampMs);
        if (detections.isEmpty()) {
            detections = parseItems(frame.path("targets"), deviceId, "DETECTION", timestampMs);
        }
        radars.put(deviceId, new RadarState(now, timestampMs, obstacles, detections));
    }

    public synchronized void observePointCloudFrame(JsonNode frame) {
        JsonNode data = frame.has("data") ? frame.path("data") : frame;
        String streamId = text(data, "stream_id", text(data, "streamId", "pointcloud"));
        String deviceId = text(data, "vehicle_id",
                text(data, "vehicleId", text(data, "sensor_id", streamId)));
        long now = clock.millis();
        long timestampMs = timestampMs(data, timestampMs(frame, now));
        List<RadarItemResponse> points = parsePointCloud(data, streamId, deviceId, timestampMs);
        radars.put(deviceId, new RadarState(now, timestampMs, List.of(), points));
    }

    public synchronized void observeRadarScan(RadarScanInput scan) {
        long now = clock.millis();
        long timestampMs = scan.timestampMs() > 0 ? scan.timestampMs() : now;
        List<RadarItemResponse> points = radarScanPoints(scan, timestampMs);
        radars.put(scan.sensorId(), new RadarState(now, timestampMs, List.of(), points));
    }

    public synchronized void observeSpectrumFrame(JsonNode frame) {
        JsonNode data = frame.has("data") && frame.path("data").isObject()
                ? frame.path("data")
                : frame;
        JsonNode powersNode = data.path("powers_dbm");
        if (!powersNode.isArray() || powersNode.isEmpty()) {
            return;
        }
        String vehicleId = text(data, "vehicle_id", "");
        String streamId = text(data, "stream_id", "");
        if (vehicleId.isBlank() || streamId.isBlank()) {
            return;
        }
        List<Double> powers = new ArrayList<>(powersNode.size());
        for (JsonNode power : powersNode) {
            if (!power.isNumber() || !Double.isFinite(power.asDouble())) {
                return;
            }
            powers.add(power.asDouble());
        }
        long now = clock.millis();
        Double capturedAt = optionalNumber(data, "captured_at");
        long timestampMs = capturedAt == null
                ? timestampMs(frame, now)
                : epochMillis(capturedAt);
        String cacheKey = vehicleId + "\u0000" + streamId;
        boolean firstFrame = !spectra.containsKey(cacheKey);
        SpectrumState state = new SpectrumState(
                now,
                timestampMs,
                vehicleId,
                text(data, "sensor_id", text(data, "stream_id", "electronic_detector")),
                streamId,
                optionalLong(frame, "sequence"),
                optionalLong(data, "sequence"),
                capturedAt,
                optionalNumber(data, "start_hz"),
                optionalNumber(data, "stop_hz"),
                optionalNumber(data, "bin_hz"),
                optionalNumber(data, "rbw_hz"),
                optionalNumber(data, "ref_level_dbm"),
                optionalNumber(data, "peak_hz"),
                optionalNumber(data, "peak_dbm"),
                optionalNumber(data, "temperature_c"),
                List.copyOf(powers)
        );
        spectra.put(cacheKey, state);
        if (firstFrame) {
            log.info(
                    "SAN60 spectrum stream online vehicle={} sensor={} stream={} gatewaySeq={} san60Seq={} points={} bandHz={}-{} binHz={} peakHz={} peakDbm={} tempC={}",
                    state.vehicleId, state.sensorId, state.streamId, state.gatewaySequence, state.sequence,
                    state.powersDbm.size(), state.startHz, state.stopHz, state.binHz,
                    state.peakHz, state.peakDbm, state.temperatureC
            );
        }
    }

    public synchronized RadarOverviewResponse radarOverview() {
        long now = clock.millis();
        List<RadarState> freshStates = radars.values().stream()
                .filter(state -> now - state.receivedAtMs <= FRESH_RADAR_MILLIS)
                .toList();
        List<RadarItemResponse> items = new ArrayList<>();
        freshStates.forEach(state -> {
            items.addAll(state.obstacles);
            items.addAll(state.detections);
        });
        Double nearest = items.stream()
                .filter(item -> ("OBSTACLE".equals(item.kind()) || "POINTCLOUD".equals(item.kind())) && item.range() != null)
                .map(RadarItemResponse::range)
                .min(Comparator.naturalOrder())
                .orElse(null);
        String latestTargetId = freshStates.stream()
                .flatMap(state -> state.detections.stream())
                .max(Comparator.comparingLong(RadarItemResponse::timestampMs))
                .map(RadarItemResponse::id)
                .orElse("");
        long updatedAt = freshStates.stream()
                .mapToLong(state -> state.timestampMs)
                .max()
                .orElse(0);
        List<SpectrumState> freshSpectra = spectra.values().stream()
                .filter(state -> now - state.receivedAtMs <= FRESH_RADAR_MILLIS)
                .toList();
        SpectrumState freshSpectrum = freshSpectra.stream()
                .max(Comparator.comparingLong(state -> state.receivedAtMs))
                .orElse(null);
        if (freshSpectrum != null) {
            updatedAt = Math.max(updatedAt, freshSpectrum.timestampMs);
        }
        return new RadarOverviewResponse(
                !freshStates.isEmpty() || freshSpectrum != null,
                freshStates.size() + freshSpectra.size(),
                radars.size() + spectra.size(),
                updatedAt,
                (int) items.stream().filter(item -> "OBSTACLE".equals(item.kind())).count(),
                (int) items.stream().filter(item -> "DETECTION".equals(item.kind()) || "POINTCLOUD".equals(item.kind())).count(),
                nearest,
                latestTargetId,
                items,
                freshSpectrum != null,
                freshSpectrum == null ? "" : freshSpectrum.vehicleId,
                freshSpectrum == null ? "" : freshSpectrum.sensorId,
                freshSpectrum == null ? "" : freshSpectrum.streamId,
                freshSpectrum == null ? null : freshSpectrum.gatewaySequence,
                freshSpectrum == null ? null : freshSpectrum.sequence,
                freshSpectrum == null ? null : freshSpectrum.capturedAt,
                freshSpectrum == null ? null : freshSpectrum.startHz,
                freshSpectrum == null ? null : freshSpectrum.stopHz,
                freshSpectrum == null ? null : freshSpectrum.binHz,
                freshSpectrum == null ? null : freshSpectrum.rbwHz,
                freshSpectrum == null ? null : freshSpectrum.refLevelDbm,
                freshSpectrum == null ? null : freshSpectrum.peakHz,
                freshSpectrum == null ? null : freshSpectrum.peakDbm,
                freshSpectrum == null ? null : freshSpectrum.temperatureC,
                freshSpectrum == null ? List.of() : freshSpectrum.powersDbm
        );
    }

    private List<RadarItemResponse> parseItems(JsonNode array, String deviceId, String kind, long timestampMs) {
        if (!array.isArray()) {
            return List.of();
        }
        List<RadarItemResponse> items = new ArrayList<>();
        int index = 1;
        for (JsonNode item : array) {
            items.add(new RadarItemResponse(
                    text(item, "id", kind.toLowerCase() + "-" + index),
                    deviceId,
                    kind,
                    optionalNumber(item, "range"),
                    optionalNumber(item, "bearing"),
                    optionalNumber(item, "x"),
                    optionalNumber(item, "y"),
                    optionalNumber(item, "z"),
                    optionalNumber(item, "confidence"),
                    number(item, "timestamp_ms", timestampMs).longValue()
            ));
            index++;
        }
        return items;
    }

    private List<RadarItemResponse> parsePointCloud(JsonNode data, String streamId, String deviceId, long timestampMs) {
        JsonNode xyz = data.path("xyz");
        if (xyz.isArray()) {
            List<Double> values = new ArrayList<>(xyz.size());
            xyz.forEach(value -> values.add(value.asDouble()));
            return pointCloudItems(values, streamId, deviceId, timestampMs);
        }

        String encoded = data.path("data_base64").asText("");
        if (encoded.isBlank()) return List.of();
        if (!"xyz_f32_le_base64".equals(data.path("encoding").asText())) {
            throw new IllegalArgumentException("Unsupported lidar encoding");
        }
        int pointCount = data.path("point_count").asInt(-1);
        int stride = data.path("point_stride_bytes").asInt(-1);
        byte[] bytes = Base64.getDecoder().decode(encoded);
        if (pointCount < 0 || stride != 12 || bytes.length != pointCount * stride) {
            throw new IllegalArgumentException("Invalid lidar frame length");
        }
        ByteBuffer buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        List<Double> values = new ArrayList<>(pointCount * 3);
        while (buffer.hasRemaining()) values.add((double) buffer.getFloat());
        return pointCloudItems(values, streamId, deviceId, timestampMs);
    }

    private List<RadarItemResponse> radarScanPoints(RadarScanInput scan, long timestampMs) {
        if (!Double.isFinite(scan.angleMinRad()) || !Double.isFinite(scan.angleIncrementRad())) {
            return List.of();
        }
        List<RadarItemResponse> points = new ArrayList<>();
        List<Double> ranges = scan.rangesM();
        for (int index = 0; index < ranges.size(); index++) {
            double range = ranges.get(index);
            if (!Double.isFinite(range) || range < scan.rangeMinM() || range > scan.rangeMaxM()) {
                continue;
            }
            double angle = scan.angleMinRad() + index * scan.angleIncrementRad();
            double x = range * Math.cos(angle);
            double y = range * Math.sin(angle);
            points.add(new RadarItemResponse(
                    scan.sensorId() + "-scan-" + (index + 1),
                    scan.sensorId(),
                    "POINTCLOUD",
                    range,
                    Math.toDegrees(angle),
                    x,
                    y,
                    0.0,
                    null,
                    timestampMs
            ));
        }
        return points;
    }

    private List<RadarItemResponse> pointCloudItems(
            List<Double> xyz, String streamId, String deviceId, long timestampMs
    ) {
        List<RadarItemResponse> points = new ArrayList<>();
        int pointCount = xyz.size() / 3;
        for (int index = 0; index < pointCount; index++) {
            double x = xyz.get(index * 3);
            double y = xyz.get(index * 3 + 1);
            double z = xyz.get(index * 3 + 2);
            points.add(new RadarItemResponse(
                    streamId + "-" + (index + 1),
                    deviceId,
                    "POINTCLOUD",
                    Math.sqrt(x * x + y * y),
                    null,
                    x,
                    y,
                    z,
                    null,
                    timestampMs
            ));
        }
        return points;
    }

    private static String text(JsonNode node, String field, String fallback) {
        String value = node.path(field).asText("");
        return value.isBlank() ? fallback : value;
    }

    private static Number number(JsonNode node, String field, Number fallback) {
        JsonNode value = node.path(field);
        return value.isNumber() ? value.numberValue() : fallback;
    }

    private static long timestampMs(JsonNode node, Number fallback) {
        Number value = number(node, "timestamp_ms", number(node, "timestampMs", number(node, "timestamp", fallback)));
        return epochMillis(value.doubleValue());
    }

    private static long epochMillis(double timestamp) {
        return timestamp < 10_000_000_000D ? Math.round(timestamp * 1000D) : Math.round(timestamp);
    }

    private static Double optionalNumber(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isNumber() ? value.asDouble() : null;
    }

    private static Long optionalLong(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isIntegralNumber() ? value.asLong() : null;
    }

    private record RadarState(
            long receivedAtMs,
            long timestampMs,
            List<RadarItemResponse> obstacles,
            List<RadarItemResponse> detections
    ) {
    }

    private record SpectrumState(
            long receivedAtMs,
            long timestampMs,
            String vehicleId,
            String sensorId,
            String streamId,
            Long gatewaySequence,
            Long sequence,
            Double capturedAt,
            Double startHz,
            Double stopHz,
            Double binHz,
            Double rbwHz,
            Double refLevelDbm,
            Double peakHz,
            Double peakDbm,
            Double temperatureC,
            List<Double> powersDbm
    ) {
    }
}
