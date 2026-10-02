package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.node.ObjectNode;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.uavusv.platform.module.voiceintelligence.AsrFailure;
import com.uavusv.platform.module.voiceintelligence.IntentService;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class VoiceInterpretationSourceTests extends VoiceControlTests {
    IntentService intents;

    @BeforeEach
    void attachIntentSourceVerifier() {
        intents = mock(IntentService.class);
        app = new VoiceCommandApplicationService(s, j, t, a, r, settings, intents);
    }

    @Test
    void verifiedInterpretationIsLinkedToProposal() {
        String source = "11111111-1111-4111-8111-111111111111";
        var result = app.createProposal(VoiceJson.uuid(), source, request("PAUSE")).data();
        assertEquals(source, result.path("interpretationId").asText());
        assertEquals("AWAITING_CONFIRMATION", result.path("status").asText());
        assertEquals(0, count("voice_execution"));
        verify(intents).requireCandidate(eq(1L), eq(source), eq("PAUSE"), any());
    }

    @Test
    void proposalFromCandidateExecutesOnlyAfterExplicitConfirmation() {
        String source = "11111111-1111-4111-8111-111111111111";
        var proposal = app.createProposal(VoiceJson.uuid(), source, request("PAUSE")).data();
        assertEquals("AWAITING_CONFIRMATION", proposal.path("status").asText());
        assertEquals(0, count("voice_execution"));
        assertTrue(sent.isEmpty());

        var confirmed =
                app.confirm(
                                proposal.path("proposalId").asText(),
                                VoiceJson.uuid(),
                                confirmation(proposal))
                        .data();
        assertEquals("CONFIRMED", confirmed.path("proposal").path("status").asText());
        assertEquals("QUEUED", confirmed.path("execution").path("state").asText());
        assertEquals(1, count("voice_execution"));
        assertTrue(sent.isEmpty(), "confirmation queues; dispatch occurs separately");
    }

    @Test
    void spoofedOrExpiredInterpretationIsRejected() {
        String source = "11111111-1111-4111-8111-111111111111";
        doThrow(new AsrFailure(409, "VOICE_INTERPRETATION_INVALID"))
                .when(intents)
                .requireCandidate(eq(1L), eq(source), eq("PAUSE"), any());
        VoiceFailure failure =
                assertThrows(
                        VoiceFailure.class,
                        () -> app.createProposal(VoiceJson.uuid(), source, request("PAUSE")));
        assertEquals("VOICE_INTERPRETATION_INVALID", failure.code);
    }

    @Test
    void interpretationIdParticipatesInProposalIdempotencyHash() {
        String key = VoiceJson.uuid();
        app.createProposal(
                key, "11111111-1111-4111-8111-111111111111", request("PAUSE"));
        VoiceFailure failure =
                assertThrows(
                        VoiceFailure.class,
                        () ->
                                app.createProposal(
                                        key,
                                        "22222222-2222-4222-8222-222222222222",
                                        request("PAUSE")));
        assertEquals("IDEMPOTENCY_CONFLICT", failure.code);
    }

    @Test
    void singleDeviceProposalFreezesOneAuthenticatedTargetAndWaitsForConfirmation() {
        String source = "11111111-1111-4111-8111-111111111111";
        makeSingleDeviceRuntime();
        when(intents.requireTargetedCandidate(eq(1L), eq(source), any()))
                .thenReturn(new IntentService.TargetedCandidate("UAV-001", "UAV_HOVER"));

        var proposal =
                app.createProposal(
                                VoiceJson.uuid(),
                                source,
                                targetedRequest())
                        .data();

        assertEquals("AWAITING_CONFIRMATION", proposal.path("status").asText());
        assertTrue(proposal.path("requiresConfirmation").asBoolean());
        assertEquals("DEVICE_COMMAND", proposal.path("plan").path("action").asText());
        assertEquals("UAV-001", proposal.path("plan").path("targetDeviceCode").asText());
        assertEquals("UAV_HOVER", proposal.path("plan").path("deviceCommandType").asText());
        assertEquals(1, proposal.path("plan").path("explicitDeviceCodes").size());
        assertEquals(0, count("voice_execution"));
        assertTrue(sent.isEmpty());

        var confirmed = confirm(proposal);
        assertEquals("CONFIRMED", confirmed.path("proposal").path("status").asText());
        assertEquals("QUEUED", confirmed.path("execution").path("state").asText());
        assertEquals(1, count("voice_execution"));
        assertTrue(sent.isEmpty(), "confirmation queues; dispatch occurs separately");
    }

    @Test
    void singleDeviceProposalIsInvalidatedWhenItsTargetLeavesTheRuntime() {
        String source = "11111111-1111-4111-8111-111111111111";
        makeSingleDeviceRuntime();
        when(intents.requireTargetedCandidate(eq(1L), eq(source), any()))
                .thenReturn(new IntentService.TargetedCandidate("UAV-001", "UAV_HOVER"));
        var proposal =
                app.createProposal(
                                VoiceJson.uuid(),
                                source,
                                targetedRequest())
                        .data();

        var replacementFrame = j.read("{\"sequence\":2,\"agents\":[{\"deviceCode\":\"USV-001\"}]}");
        r.frame(ref(), gen(), replacementFrame);

        error("CONTEXT_CHANGED", () -> confirm(proposal));
        assertEquals(
                "INVALIDATED",
                s.get("voice_proposal", proposal.path("proposalId").asText())
                        .path("status")
                        .asText());
        assertEquals(0, count("voice_execution"));
        assertTrue(sent.isEmpty());
    }

    private void makeSingleDeviceRuntime() {
        c = s.get("voice_runtime_context", ref());
        c.put("_algorithmCode", "ESCORT_GUARD");
        c.put("state", "RUNNING");
        c.putArray("capabilities").add("DEVICE_COMMAND");
        s.save("voice_runtime_context", c);
    }

    private ObjectNode targetedRequest() {
        return j.object()
                .put("runtimeRef", ref())
                .put("runtimeGeneration", gen())
                .put("expectedContextVersion", app.context(ref()).path("contextVersion").asLong())
                .put("intent", "SINGLE_DEVICE_CONTROL");
    }
}
