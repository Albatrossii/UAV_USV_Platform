package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.voiceintelligence.IntentService;
import org.junit.jupiter.api.Test;

class AlgorithmPresetControlTests extends VoiceControlTests {
    @Test
    void departureAcknowledgementReleasesChannelAndTracksExecutionByCommand() {
        freezesPresetDispatchesVersionedParametersAndAcceptsRunningReceipt();
        // Store a real preset execution fixture, then supply its independent
        // authoritative progress (transport success must not mean it started).
        var id = s.jdbc.queryForObject("SELECT id FROM voice_execution", String.class);
        var e = s.get("voice_execution", id);
        var pending = j.object().put("commandId", e.path("commandId").asText())
                .put("presetId", 1).put("status", "WAITING_DEPLOYMENT").putNull("errorCode");
        e.set("presetExecution", pending);
        s.save("voice_execution", e);
        j.validate("Execution", app.execution(id));
        assertEquals("WAITING_DEPLOYMENT", app.execution(id).path("presetExecution").path("status").asText());
        var pause = execution(confirm(proposal("PAUSE")));
        worker.tick();
        assertEquals("DISPATCHED", app.execution(pause.path("executionId").asText()).path("state").asText());
        assertEquals("PAUSE", sent.get(1).path("action").asText());
        var frame = j.object().put("sequence", 2);
        frame.putArray("agents").addObject().put("deviceCode", "UAV-001");
        frame.putObject("metrics").putObject("presetCommands")
                .set(e.path("commandId").asText(), pending.deepCopy().put("status", "EXECUTED"));
        r.frame(ref(), gen(), frame);
        assertEquals("EXECUTED", app.execution(id).path("presetExecution").path("status").asText());
        j.validate("Execution", app.execution(id));
    }

    @Test
    void stoppedRunCancelsWaitingPresetOutcome() {
        freezesPresetDispatchesVersionedParametersAndAcceptsRunningReceipt();
        var id = s.jdbc.queryForObject("SELECT id FROM voice_execution", String.class);
        var e = s.get("voice_execution", id);
        e.putObject("presetExecution").put("commandId", e.path("commandId").asText())
                .put("presetId", 1).put("status", "WAITING_DEPLOYMENT").putNull("errorCode");
        s.save("voice_execution", e);
        heartbeat("STOPPED", 5, 2);
        assertEquals("CANCELLED", app.execution(id).path("presetExecution").path("status").asText());
    }

    @Test
    void freezesPresetDispatchesVersionedParametersAndAcceptsRunningReceipt() {
        s.locked(() -> {
            var context = s.get("voice_runtime_context", ref());
            context.withArray("capabilities").add("ALGORITHM_PRESET");
            context.put("_algorithmCommandProfile", AlgorithmPresets.VERSION);
            context.putObject("_algorithmTargets").putObject("THREAT-001");
            s.save("voice_runtime_context", context);
            return null;
        });
        var intents = mock(IntentService.class);
        when(intents.requirePresetParameters(anyLong(), anyString(), any()))
                .thenReturn(AlgorithmPresets.parameters(j, 1));
        app = new VoiceCommandApplicationService(s, j, t, a, r, settings, intents);
        var body = request("PAUSE").put("intent", "ALGORITHM_PRESET");
        var p = app.createProposal(VoiceJson.uuid(), VoiceJson.uuid(), body).data();
        j.validate("Proposal", p);
        assertEquals(1, p.path("plan").path("parameters").path("presetId").asInt());
        var e = execution(confirm(p));
        worker.tick();
        assertEquals(1, sent.size());
        assertEquals("ALGORITHM_PRESET", sent.get(0).path("action").asText());
        assertEquals(p.path("plan").path("parameters"), sent.get(0).path("parameters"));
        j.validate("Command", sent.get(0));
        var receipt = event("COMMAND_RESULT");
        receipt.set("commandId", e.path("commandId"));
        receipt.put("eventSequence", 1).put("status", "SUCCEEDED").put("runtimeState", "RUNNING")
                .put("stateVersion", 4).put("lastFrameSequence", 1).putNull("errorCode");
        receipt.putArray("affectedDeviceCodes").add("UAV-001").add("USV-001");
        receipt.putObject("presetExecution").put("commandId", e.path("commandId").asText())
                .put("presetId", 1).put("status", "WAITING_DEPLOYMENT").putNull("errorCode");
        j.validate("CommandResult", receipt);
        worker.receive(ref(), gen(), receipt);
        assertEquals("SUCCEEDED", app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("WAITING_DEPLOYMENT", app.execution(e.path("executionId").asText()).path("presetExecution").path("status").asText());
        assertEquals("RUNNING", app.context(ref()).path("state").asText());
    }

    @Test
    void invalidNumericTypesVersionsAndMissingTargetsAreRejected() {
        assertEquals(7, AlgorithmPresets.parse("一号无人机追击二号敌船"));
        assertEquals(13, AlgorithmPresets.parse("执行十三号指令"));
        assertNull(AlgorithmPresets.parse("如果开始围捕"));
        assertNull(AlgorithmPresets.parse("二十一"));
        assertNull(AlgorithmPresets.parse("十十"));
        var context = j.object().put("_algorithmCommandProfile", AlgorithmPresets.VERSION);
        context.putArray("capabilities").add("ALGORITHM_PRESET");
        context.putObject("_algorithmTargets").putObject("THREAT-001");
        assertThrows(VoiceFailure.class, () -> AlgorithmPresets.validate(context, AlgorithmPresets.parameters(j,7)));
        var params = AlgorithmPresets.parameters(j,1).put("presetId", 1.5);
        assertThrows(VoiceFailure.class, () -> AlgorithmPresets.validate(context, params));
    }
}
