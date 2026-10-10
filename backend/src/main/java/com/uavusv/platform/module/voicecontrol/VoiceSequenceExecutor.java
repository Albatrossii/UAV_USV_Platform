package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;
import java.util.Set;

/** Durable 2–4 step coordinator; never replay an already-created child command. */
@Service
public class VoiceSequenceExecutor {
    private static final Logger log = LoggerFactory.getLogger(VoiceSequenceExecutor.class);
    private static final Set<String> TERMINAL =
            Set.of("SUCCEEDED", "REJECTED", "FAILED", "INVALIDATED", "TIMED_OUT");

    private final VoiceStore store;
    private final VoiceTime time;
    private final RuntimeContextRegistry runtimes;
    private final VoiceCommandApplicationService commands;

    public VoiceSequenceExecutor(
            VoiceStore store,
            VoiceTime time,
            RuntimeContextRegistry runtimes,
            VoiceCommandApplicationService commands) {
        this.store = store;
        this.time = time;
        this.runtimes = runtimes;
        this.commands = commands;
    }

    @Scheduled(fixedDelayString = "${app.voicecontrol.sequence-poll-ms:200}")
    public synchronized void tick() {
        List<String> ids = store.locked(
                () -> store.query(
                                "SELECT data_json FROM voice_execution WHERE state='EXECUTING'")
                        .stream()
                        .filter(e -> "SEQUENCE".equals(e.path("action").asText()))
                        .map(e -> e.path("executionId").asText())
                        .toList());
        for (String id : ids) {
            try {
                store.locked(() -> {
                    advance(id);
                    return null;
                });
            } catch (RuntimeException failure) {
                log.warn(
                        "Voice sequence reconciliation will retry executionId={} type={}",
                        id,
                        failure.getClass().getSimpleName());
            }
        }
    }

    private void advance(String id) {
        ObjectNode parent = store.get("voice_execution", id);
        if (parent == null || !"EXECUTING".equals(parent.path("state").asText())) return;
        if (!time.now().isBefore(Instant.parse(parent.path("createdAt").asText()).plusSeconds(600))) {
            fail(parent, "TIMED_OUT", "SEQUENCE_TIMEOUT");
            return;
        }
        ObjectNode context = store.get("voice_runtime_context", parent.path("runtimeRef").asText());
        if (context == null
                || !parent.path("runtimeGeneration").equals(context.path("runtimeGeneration"))) {
            fail(parent, "INVALIDATED", "GENERATION_MISMATCH");
            return;
        }
        int index = parent.path("currentStepIndex").asInt();
        if (index < 0 || index >= parent.path("steps").size()) {
            fail(parent, "FAILED", "SEQUENCE_STATE_INVALID");
            return;
        }
        ObjectNode step = (ObjectNode) parent.path("steps").path(index);
        JsonNode frozenStep = parent.path("_plan").path("steps").path(index);
        if ("WAIT".equals(frozenStep.path("action").asText())) {
            // A user stop/disconnection must never become a delayed resume.
            String expected = parent.path("_waitingRuntimeState").asText("");
            String current = context.path("state").asText();
            if (!Set.of("RUNNING", "PAUSED").contains(current)
                    || (!expected.isEmpty() && !expected.equals(current))) {
                fail(parent, "INVALIDATED", "INVALID_STATE");
                return;
            }
            parent.put("_waitingRuntimeState", current);
            if (!step.has("startedAt")) step.put("startedAt", time.stamp()).put("state", "EXECUTING");
            if (time.now().isBefore(Instant.parse(step.path("startedAt").asText()).plusSeconds(frozenStep.path("waitSeconds").asInt()))) {
                parent.put("sequenceStatus", "WAITING_STEP_PRECONDITION").put("updatedAt", time.stamp());
                store.save("voice_execution", parent);
                return;
            }
            completeStep(parent, step, index);
            return;
        }
        String childId = step.path("executionId").asText("");
        if (childId.isEmpty()) {
            try {
                if (!"DEVICE_COMMAND".equals(frozenStep.path("action").asText())) {
                    runtimes.check(context, frozenStep.path("action").asText(), true);
                } else {
                    String target = frozenStep.path("targetDeviceCode").asText();
                    String commandType = frozenStep.path("deviceCommandType").asText();
                    if (!VoiceSequencePolicy.DEVICE_COMMANDS.contains(commandType))
                        throw VoiceFailure.conflict("SEQUENCE_POLICY_VIOLATION");
                    runtimes.checkDevice(context, target, commandType);
                }
                childId = commands.enqueueSequenceChild(parent, context, frozenStep);
                parent.put("_stepFrameAtDispatch", context.path("latestFrameSequence").asLong());
                step.put("executionId", childId)
                        .put("state", "QUEUED")
                        .put("startedAt", time.stamp());
                parent.put(
                                "sequenceStatus",
                                index == 0 ? "EXECUTING_START" : "EXECUTING_DEVICE_COMMAND")
                        .put("updatedAt", time.stamp());
                store.save("voice_execution", parent);
            } catch (VoiceFailure failure) {
                fail(parent, "INVALIDATED", failure.code);
            }
            return;
        }

        ObjectNode child = store.get("voice_execution", childId);
        if (child == null) {
            fail(parent, "FAILED", "SEQUENCE_CHILD_MISSING");
            return;
        }
        String childState = child.path("state").asText();
        step.put("state", childState);
        step.set("errorCode", child.path("errorCode").deepCopy());
        parent.put("updatedAt", time.stamp());
        if (!TERMINAL.contains(childState)) {
            store.save("voice_execution", parent);
            return;
        }
        if (!"SUCCEEDED".equals(childState)) {
            String parentState = "TIMED_OUT".equals(childState) ? "TIMED_OUT"
                    : "INVALIDATED".equals(childState) ? "INVALIDATED"
                    : childState;
            fail(parent, parentState, child.path("errorCode").asText("SEQUENCE_STEP_FAILED"));
            return;
        }
        // A successful command receipt is not an arrival receipt. Movement
        // commands wait for a newer authoritative frame before continuing.
        if ("DEVICE_COMMAND".equals(frozenStep.path("action").asText())
                && Set.of("UAV_RETURN", "USV_RETURN", "UAV_RESUME", "USV_RESUME", "UAV_LAND")
                        .contains(frozenStep.path("deviceCommandType").asText())
                && (context.path("latestFrameSequence").asLong() <= parent.path("_stepFrameAtDispatch").asLong()
                    || !VoiceSequencePolicy.deviceCompleted(context, frozenStep))) {
            step.put("state", "EXECUTING");
            parent.put("sequenceStatus", "WAITING_STEP_PRECONDITION");
            store.save("voice_execution", parent);
            return;
        }
        completeStep(parent, step, index);
    }

    private void completeStep(ObjectNode parent, ObjectNode step, int index) {
        parent.remove("_stepFrameAtDispatch");
        parent.remove("_waitingRuntimeState");
        step.put("completedAt", time.stamp());
        step.put("state", "SUCCEEDED").putNull("errorCode");
        if (index + 1 < parent.path("steps").size()) {
            parent.put("currentStepIndex", index + 1)
                    .put("sequenceStatus", "WAITING_STEP_PRECONDITION")
                    .put("updatedAt", time.stamp());
            store.save("voice_execution", parent);
            return;
        }
        parent.put("state", "SUCCEEDED")
                .put("outcome", "SUCCESS")
                .put("sequenceStatus", "SUCCEEDED")
                .putNull("errorCode")
                .put("updatedAt", time.stamp());
        store.save("voice_execution", parent);
    }

    private void fail(ObjectNode parent, String state, String errorCode) {
        parent.put("state", state)
                .put("outcome", "TIMED_OUT".equals(state) ? "UNKNOWN"
                        : "REJECTED".equals(state) || "INVALIDATED".equals(state)
                                ? "REJECTED" : "FAILED")
                .put("sequenceStatus", "FAILED")
                .put("errorCode", errorCode)
                .put("updatedAt", time.stamp());
        if ("TIMED_OUT".equals(state)) parent.put("timedOutAt", time.stamp());
        store.save("voice_execution", parent);
    }
}
