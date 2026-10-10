package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.Set;

/** Validate the complete frozen plan before any step is dispatched. */
public final class VoiceSequencePolicy {
    private VoiceSequencePolicy() {}
    public static final Set<String> DEVICE_COMMANDS = Set.of(
            "UAV_HOVER", "UAV_RESUME", "UAV_RETURN", "UAV_LAND",
            "USV_HOLD", "USV_RESUME", "USV_RETURN", "USV_STOP");

    public static void validate(JsonNode steps, ObjectNode context, RuntimeContextRegistry runtimes) {
        if (!steps.isArray() || steps.size() < 2 || steps.size() > 4)
            throw VoiceFailure.conflict("SEQUENCE_POLICY_VIOLATION");
        String state = context.path("state").asText();
        for (int i = 0; i < steps.size(); i++) {
            JsonNode step = steps.get(i);
            String action = step.path("action").asText();
            if (step.path("index").asInt(-1) != i) throw VoiceFailure.conflict("SEQUENCE_POLICY_VIOLATION");
            if ("WAIT".equals(action)) {
                int seconds = step.path("waitSeconds").asInt();
                if (i == 0 || seconds < 1 || seconds > 60 || !Set.of("RUNNING", "PAUSED").contains(state))
                    throw VoiceFailure.conflict("SEQUENCE_POLICY_VIOLATION");
            } else if ("DEVICE_COMMAND".equals(action)) {
                if (!"RUNNING".equals(state)) throw VoiceFailure.conflict("INVALID_STATE");
                runtimes.checkSequenceTarget(context, step.path("targetDeviceCode").asText(), step.path("deviceCommandType").asText());
            } else {
                Set<String> allowed = switch (action) {
                    case "START" -> Set.of("PREPARED", "PREVIEW");
                    case "PAUSE" -> Set.of("RUNNING");
                    case "RESUME" -> Set.of("PAUSED");
                    case "STOP" -> Set.of("PREPARED", "PREVIEW", "RUNNING", "PAUSED");
                    default -> Set.of();
                };
                boolean capable = false;
                for (JsonNode capability : context.path("capabilities")) capable |= action.equals(capability.asText());
                if (!allowed.contains(state) || !capable) throw VoiceFailure.conflict("INVALID_STATE");
                state = switch (action) {
                    case "START", "RESUME" -> "RUNNING";
                    case "PAUSE" -> "PAUSED";
                    default -> "STOPPED";
                };
            }
        }
        JsonNode first = steps.get(0);
        if ("DEVICE_COMMAND".equals(first.path("action").asText()))
            runtimes.checkDevice(context, first.path("targetDeviceCode").asText(), first.path("deviceCommandType").asText());
        else runtimes.check(context, first.path("action").asText(), true);
    }

    public static boolean deviceCompleted(ObjectNode context, JsonNode step) {
        String code = step.path("targetDeviceCode").asText();
        JsonNode device = context.path("_deviceStates").path(code);
        String motion = device.path("motionState").asText();
        return switch (step.path("deviceCommandType").asText()) {
            case "UAV_RETURN", "USV_RETURN", "UAV_LAND" -> "RETURNED".equals(motion);
            case "UAV_RESUME", "USV_RESUME" -> "ACTIVE".equals(motion) || "ALGORITHM".equals(motion);
            case "UAV_HOVER", "USV_HOLD" -> "HOLDING".equals(motion);
            case "USV_STOP" -> "STOPPED".equals(motion);
            default -> false;
        };
    }
}
