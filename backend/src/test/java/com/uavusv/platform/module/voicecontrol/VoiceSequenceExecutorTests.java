package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.voiceintelligence.IntentService;

import org.junit.jupiter.api.Test;

class VoiceSequenceExecutorTests extends VoiceControlTests {
    IntentService intents;
    VoiceSequenceExecutor sequences;

    void prepareSequence() {
        intents = mock(IntentService.class);
        app = new VoiceCommandApplicationService(s, j, t, a, r, settings, intents);
        sequences = new VoiceSequenceExecutor(s, t, r, app);
        s.locked(() -> {
            ObjectNode current = s.get("voice_runtime_context", ref());
            current.put("_algorithmCode", "ESCORT_GUARD_SINGLE_DEVICE")
                    .put("state", "PREPARED")
                    .put("stateVersion", 4);
            ((com.fasterxml.jackson.databind.node.ArrayNode) current.path("capabilities"))
                    .add("DEVICE_COMMAND");
            s.save("voice_runtime_context", current);
            return null;
        });
    }

    @Test
    void startThenHoverAdvancesOnlyAfterAuthoritativeSuccess() {
        prepareSequence();
        ObjectNode proposal = sequenceProposal("UAV-001", "UAV_HOVER");
        ObjectNode parent = execution(confirm(proposal));
        assertEquals("SEQUENCE", parent.path("action").asText());
        assertEquals("EXECUTING", parent.path("state").asText());

        sequences.tick();
        parent = app.execution(parent.path("executionId").asText());
        String startExecutionId = parent.path("steps").path(0).path("executionId").asText();
        assertFalse(startExecutionId.isBlank());
        assertTrue(parent.path("steps").path(1).path("executionId").isNull());

        worker.tick();
        ObjectNode start = app.execution(startExecutionId);
        commandResult(start, "ACCEPTED", 1, "PREPARED", 4);
        sequences.tick();
        assertTrue(app.execution(parent.path("executionId").asText())
                .path("steps").path(1).path("executionId").isNull());

        commandResult(start, "SUCCEEDED", 2, "RUNNING", 5);
        sequences.tick();
        sequences.tick();
        parent = app.execution(parent.path("executionId").asText());
        String deviceExecutionId = parent.path("steps").path(1).path("executionId").asText();
        assertFalse(deviceExecutionId.isBlank());
        assertEquals("SUCCEEDED", parent.path("steps").path(0).path("state").asText());

        markChildSucceeded(deviceExecutionId);
        sequences.tick();
        parent = app.execution(parent.path("executionId").asText());
        assertEquals("SUCCEEDED", parent.path("state").asText());
        assertEquals("SUCCESS", parent.path("outcome").asText());
        assertEquals("SUCCEEDED", parent.path("steps").path(1).path("state").asText());
        j.validate("Execution", parent);
    }

    @Test
    void failedStartNeverCreatesDeviceCommand() {
        prepareSequence();
        ObjectNode proposal = sequenceProposal("UAV-001", "UAV_HOVER");
        ObjectNode parent = execution(confirm(proposal));
        sequences.tick();
        parent = app.execution(parent.path("executionId").asText());
        String startExecutionId = parent.path("steps").path(0).path("executionId").asText();
        s.locked(() -> {
            ObjectNode child = s.get("voice_execution", startExecutionId);
            child.put("state", "FAILED")
                    .put("outcome", "FAILED")
                    .put("errorCode", "START_FAILED")
                    .put("updatedAt", t.stamp());
            s.save("voice_execution", child);
            return null;
        });
        sequences.tick();
        parent = app.execution(parent.path("executionId").asText());
        assertEquals("FAILED", parent.path("state").asText());
        assertEquals("START_FAILED", parent.path("errorCode").asText());
        assertTrue(parent.path("steps").path(1).path("executionId").isNull());
        assertEquals(2, count("voice_proposal"));
        assertEquals(2, count("voice_execution"));
    }

    @Test
    void failedDeviceCommandKeepsCompletedStartAndFailsParent() {
        prepareSequence();
        ObjectNode parent = execution(confirm(sequenceProposal("UAV-001", "UAV_HOVER")));
        sequences.tick();
        parent = app.execution(parent.path("executionId").asText());
        String startExecutionId = parent.path("steps").path(0).path("executionId").asText();

        worker.tick();
        ObjectNode start = app.execution(startExecutionId);
        commandResult(start, "SUCCEEDED", 1, "RUNNING", 5);
        sequences.tick();
        sequences.tick();
        parent = app.execution(parent.path("executionId").asText());
        String deviceExecutionId = parent.path("steps").path(1).path("executionId").asText();
        assertFalse(deviceExecutionId.isBlank());

        s.locked(() -> {
            ObjectNode child = s.get("voice_execution", deviceExecutionId);
            child.put("state", "FAILED")
                    .put("outcome", "FAILED")
                    .put("errorCode", "HOVER_FAILED")
                    .put("updatedAt", t.stamp());
            s.save("voice_execution", child);
            return null;
        });
        sequences.tick();

        parent = app.execution(parent.path("executionId").asText());
        assertEquals("FAILED", parent.path("state").asText());
        assertEquals("FAILED", parent.path("outcome").asText());
        assertEquals("HOVER_FAILED", parent.path("errorCode").asText());
        assertEquals("SUCCEEDED", parent.path("steps").path(0).path("state").asText());
        assertEquals("FAILED", parent.path("steps").path(1).path("state").asText());
        assertEquals(3, count("voice_proposal"));
        assertEquals(3, count("voice_execution"));
    }

    private ObjectNode sequenceProposal(String target, String command) {
        var steps = j.mapper.createArrayNode();
        steps.addObject().put("index", 0).put("action", "START");
        steps.addObject().put("index", 1).put("action", "DEVICE_COMMAND")
                .put("targetDeviceCode", target).put("deviceCommandType", command);
        return sequenceProposal(steps);
    }

    private ObjectNode sequenceProposal(com.fasterxml.jackson.databind.node.ArrayNode steps) {
        String interpretationId = VoiceJson.uuid();
        when(intents.requireSequenceSteps(eq(1L), eq(interpretationId), any(ObjectNode.class))).thenReturn(steps);
        ObjectNode request = j.object()
                .put("runtimeRef", ref())
                .put("runtimeGeneration", gen())
                .put("expectedContextVersion", app.context(ref()).path("contextVersion").asLong())
                .put("intent", "COMMAND_SEQUENCE");
        return app.createProposal(VoiceJson.uuid(), interpretationId, request).data();
    }

    @Test
    void fourStepsWaitWithoutDispatchAndNeverDuplicateChildren() {
        prepareSequence();
        var steps = j.mapper.createArrayNode();
        steps.addObject().put("index", 0).put("action", "START");
        steps.addObject().put("index", 1).put("action", "DEVICE_COMMAND").put("targetDeviceCode", "UAV-001").put("deviceCommandType", "UAV_HOVER");
        steps.addObject().put("index", 2).put("action", "WAIT").put("waitSeconds", 3);
        steps.addObject().put("index", 3).put("action", "PAUSE");
        String id = execution(confirm(sequenceProposal(steps))).path("executionId").asText();
        sequences.tick();
        worker.tick();
        commandResult(app.execution(app.execution(id).path("steps").path(0).path("executionId").asText()), "SUCCEEDED", 1, "RUNNING", 5);
        sequences.tick(); sequences.tick();
        markChildSucceeded(app.execution(id).path("steps").path(1).path("executionId").asText());
        sequences.tick(); sequences.tick();
        assertEquals(2, app.execution(id).path("currentStepIndex").asInt());
        t.advance(2999); sequences.tick();
        assertEquals(3, count("voice_execution"));
        t.advance(1); heartbeat("RUNNING", 5, 2); sequences.tick(); sequences.tick();
        String pause = app.execution(id).path("steps").path(3).path("executionId").asText();
        assertFalse(pause.isEmpty());
        sequences.tick(); sequences.tick();
        assertEquals(4, count("voice_execution"));
        markChildSucceeded(pause); sequences.tick();
        var result = app.execution(id);
        assertEquals("SUCCEEDED", result.path("state").asText());
        j.validate("Execution", result);
    }

    @Test
    void returnReceiptWaitsForNewPhysicalArrivalFrame() {
        prepareSequence();
        var steps = j.mapper.createArrayNode();
        steps.addObject().put("index", 0).put("action", "START");
        steps.addObject().put("index", 1).put("action", "DEVICE_COMMAND").put("targetDeviceCode", "UAV-001").put("deviceCommandType", "UAV_RETURN");
        steps.addObject().put("index", 2).put("action", "PAUSE");
        String id = execution(confirm(sequenceProposal(steps))).path("executionId").asText();
        sequences.tick(); worker.tick();
        commandResult(app.execution(app.execution(id).path("steps").path(0).path("executionId").asText()), "SUCCEEDED", 1, "RUNNING", 5);
        sequences.tick(); sequences.tick();
        markChildSucceeded(app.execution(id).path("steps").path(1).path("executionId").asText());
        sequences.tick();
        assertEquals(1, app.execution(id).path("currentStepIndex").asInt());
        var frame = j.object().put("sequence", 2);
        frame.putArray("agents").addObject().put("deviceCode", "UAV-001").put("status", "RETURNING");
        ((com.fasterxml.jackson.databind.node.ArrayNode) frame.path("agents")).addObject().put("deviceCode", "USV-001").put("status", "ACTIVE");
        r.frame(ref(), gen(), frame); sequences.tick();
        assertEquals(1, app.execution(id).path("currentStepIndex").asInt());
        frame.put("sequence", 3);
        ((ObjectNode) frame.path("agents").path(0)).put("status", "RETURNED");
        r.frame(ref(), gen(), frame); sequences.tick();
        assertEquals(2, app.execution(id).path("currentStepIndex").asInt());
    }

    @Test
    void rejectsImpossibleOrderingBeforeStartingAnything() {
        prepareSequence();
        var steps = j.mapper.createArrayNode();
        steps.addObject().put("index", 0).put("action", "START");
        steps.addObject().put("index", 1).put("action", "START");
        assertThrows(VoiceFailure.class, () -> sequenceProposal(steps));
        assertEquals(0, count("voice_execution"));
    }

    @Test
    void stoppedDuringWaitInvalidatesRemainingCommands() {
        prepareSequence();
        var steps = j.mapper.createArrayNode();
        steps.addObject().put("index", 0).put("action", "START");
        steps.addObject().put("index", 1).put("action", "WAIT").put("waitSeconds", 2);
        steps.addObject().put("index", 2).put("action", "PAUSE");
        String id = execution(confirm(sequenceProposal(steps))).path("executionId").asText();
        sequences.tick(); worker.tick();
        commandResult(app.execution(app.execution(id).path("steps").path(0).path("executionId").asText()), "SUCCEEDED", 1, "RUNNING", 5);
        sequences.tick(); sequences.tick();
        heartbeat("STOPPED", 6, 2); t.advance(2500); sequences.tick();
        assertEquals("INVALIDATED", app.execution(id).path("state").asText());
        assertTrue(app.execution(id).path("steps").path(2).path("executionId").isNull());
    }

    private void commandResult(
            ObjectNode child, String status, long eventSequence, String state, long stateVersion) {
        ObjectNode result = event("COMMAND_RESULT");
        result.set("commandId", child.path("commandId"));
        result.put("eventSequence", eventSequence)
                .put("status", status)
                .put("runtimeState", state)
                .put("stateVersion", stateVersion)
                .put("lastFrameSequence", 1)
                .putNull("errorCode");
        result.putArray("affectedDeviceCodes").add("UAV-001").add("USV-001");
        worker.receive(ref(), gen(), result);
    }

    private void markChildSucceeded(String id) {
        s.locked(() -> {
            ObjectNode child = s.get("voice_execution", id);
            child.put("state", "SUCCEEDED")
                    .put("outcome", "SUCCESS")
                    .putNull("errorCode")
                    .put("updatedAt", t.stamp());
            s.save("voice_execution", child);
            return null;
        });
    }
}
