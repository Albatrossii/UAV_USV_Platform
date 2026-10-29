package com.uavusv.platform.module.gimbalcamera.service;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.stereotype.Service;

import java.util.Base64;

/** Holds only the independent gimbal stream; it never enters the six-camera catalog. */
@Service
public class GimbalCameraFrameService {
    public static final String DEVICE_ID = "gimbal_01";
    public static final String STREAM_ID = "gimbal_01_a10tr";

    private byte[] jpeg;
    private int width;
    private int height;
    private long receivedAtMillis;

    public synchronized boolean accept(JsonNode envelope) {
        if (envelope == null) return false;
        String messageType = envelope.path("message_type").asText("");
        JsonNode frame = envelope.has("data") && envelope.path("data").isObject()
                ? envelope.path("data") : envelope;
        if (!messageType.isBlank() && !"camera_frame".equals(messageType)) return false;
        if (!DEVICE_ID.equals(frame.path("vehicle_id").asText())
                || !STREAM_ID.equals(frame.path("stream_id").asText())
                || !"image/jpeg".equalsIgnoreCase(frame.path("encoding").asText())) return false;
        int nextWidth = frame.path("width").asInt();
        int nextHeight = frame.path("height").asInt();
        String encoded = frame.path("data_base64").asText("");
        if (nextWidth < 1 || nextHeight < 1 || encoded.isBlank()) return false;
        try {
            byte[] decoded = Base64.getDecoder().decode(encoded);
            if (decoded.length < 4 || (decoded[0] & 0xff) != 0xff || (decoded[1] & 0xff) != 0xd8
                    || (decoded[decoded.length - 2] & 0xff) != 0xff || (decoded[decoded.length - 1] & 0xff) != 0xd9) return false;
            jpeg = decoded;
            width = nextWidth;
            height = nextHeight;
            receivedAtMillis = System.currentTimeMillis();
            return true;
        } catch (IllegalArgumentException exception) {
            return false;
        }
    }

    public synchronized byte[] latestFrame(long sinceMillis) {
        return jpeg == null || receivedAtMillis <= sinceMillis ? null : jpeg.clone();
    }

    public synchronized long receivedAtMillis() { return receivedAtMillis; }
    public synchronized int width() { return width; }
    public synchronized int height() { return height; }
}
