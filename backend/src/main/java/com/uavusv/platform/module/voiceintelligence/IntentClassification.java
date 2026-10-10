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
        String deviceCommandType,
        com.fasterxml.jackson.databind.node.ArrayNode sequenceSteps) {
    IntentClassification(String status, String reason, String message, String action,
            String targetDeviceCode, String deviceCommandType) {
        this(status, reason, message, action, targetDeviceCode, deviceCommandType, null);
    }
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
        if ("SEQUENCE".equals(action)) {
            var steps = json.mapper.createArrayNode();
            steps.addObject().put("index", 0).put("action", "START");
            steps.addObject()
                    .put("index", 1)
                    .put("action", "DEVICE_COMMAND")
                    .put("targetDeviceCode", targetDeviceCode)
                    .put("deviceCommandType", deviceCommandType);
            if (sequenceSteps != null) steps = sequenceSteps.deepCopy();
            node.put("intent", "COMMAND_SEQUENCE")
                    .put("action", action)
                    .set("steps", steps);
            node.putNull("confidence");
        } else if ("DEVICE_COMMAND".equals(action)) {
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
