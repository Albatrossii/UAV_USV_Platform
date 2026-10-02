package com.uavusv.platform.module.voiceintelligence;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.voicecontrol.VoiceJson;

import java.util.Map;

record IntentClassification(
        String status,
        String reason,
        String message,
        String action,
        String targetDeviceCode,
        String deviceCommandType) {
    IntentClassification(String status, String reason, String message, String action) {
        this(status, reason, message, action, null, null);
    }
    private static final Map<String, String> INTENTS =
            Map.of(
                    "START", "MISSION_START",
                    "PAUSE", "MISSION_PAUSE",
                    "RESUME", "MISSION_RESUME",
                    "STOP", "MISSION_STOP");

    ObjectNode data(String id, String text, String provider, String model, VoiceJson json) {
        ObjectNode node = json.object();
        node.put("status", status).put("requestId", id);
        if ("DEVICE_COMMAND".equals(action)) {
            node.put("intent", "SINGLE_DEVICE_CONTROL")
                    .put("action", action)
                    .put("targetDeviceCode", targetDeviceCode)
                    .put("deviceCommandType", deviceCommandType)
                    .putNull("confidence");
        } else if (action != null) {
            node.put("intent", INTENTS.get(action))
                    .put("action", action)
                    .putNull("confidence");
        } else {
            node.put("reason", reason).put("message", message);
        }
        return node.put("normalizedText", text).put("provider", provider).put("model", model);
    }
}
