package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.voicecontrol.RuntimeContextRegistry;
import com.uavusv.platform.module.voicecontrol.VoiceAccess;
import com.uavusv.platform.module.voicecontrol.VoiceJson;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.util.Map;

class IntentServiceTests {
    static final String ID = "11111111-1111-4111-8111-111111111111";
    static final String REF = "22222222-2222-4222-8222-222222222222";
    static final String GEN = "33333333-3333-4333-8333-333333333333";

    VoiceAccess access;
    AsrSettings settings;
    RuntimeContextRegistry runtimes;
    VoiceJson json;
    IntentService service;

    @BeforeEach
    void setup() {
        access = mock(VoiceAccess.class);
        settings = new AsrSettings();
        settings.setEnabled(true);
        runtimes = mock(RuntimeContextRegistry.class);
        json = new VoiceJson();
        service = new IntentService(access, settings, runtimes, json);
    }

    @Test
    void fourSupportedActionsBecomeCandidates() {
        assertCandidate("开始任务", "START", "MISSION_START");
        assertCandidate("暂停当前任务", "PAUSE", "MISSION_PAUSE");
        assertCandidate("恢复运行", "RESUME", "MISSION_RESUME");
        assertCandidate("停止任务", "STOP", "MISSION_STOP");
    }

    @Test
    void unsafeLanguageNeverBecomesCandidate() {
        assertStatus("不要停止任务", "NOT_ACTIONABLE", "NEGATED_ACTION");
        assertStatus("暂停然后继续", "NEEDS_CLARIFICATION", "AMBIGUOUS_ACTION");
        assertStatus("让一号无人艇暂停", "UNSUPPORTED", "UNSUPPORTED_TARGETING");
        assertStatus("攻击目标", "UNSUPPORTED", "UNSUPPORTED_CAPABILITY");
        assertStatus("今天天气如何", "NEEDS_CLARIFICATION", "NO_SUPPORTED_ACTION");
    }

    @Test
    void idempotentReplayAndConflict() {
        var first = service.interpret(7, ID, request("暂停任务", null));
        var replay = service.interpret(7, ID, request("暂停任务", null));
        assertSame(first, replay);
        AsrFailure conflict =
                assertThrows(
                        AsrFailure.class,
                        () -> service.interpret(7, ID, request("停止任务", null)));
        assertEquals("IDEMPOTENCY_CONFLICT", conflict.code);
    }

    @Test
    void contextGenerationAndCapabilityAreAuthoritative() {
        ObjectNode runtime = runtime("PAUSE");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        assertCandidate(service.interpret(7, ID, request("暂停任务", hint())), "PAUSE", "MISSION_PAUSE");

        ObjectNode stale = hint();
        stale.put("runtimeGeneration", "44444444-4444-4444-8444-444444444444");
        AsrFailure changed =
                assertThrows(
                        AsrFailure.class,
                        () ->
                                service.interpret(
                                        7,
                                        "55555555-5555-4555-8555-555555555555",
                                        request(
                                                "55555555-5555-4555-8555-555555555555",
                                                "暂停任务",
                                                stale)));
        assertEquals("VOICE_CONTEXT_CHANGED", changed.code);
    }

    @Test
    void proposalSourceMustMatchUserActionAndContext() {
        ObjectNode runtime = runtime("PAUSE");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        service.interpret(7, ID, request("暂停任务", hint()));
        assertDoesNotThrow(() -> service.requireCandidate(7, ID, "PAUSE", runtime));
        AsrFailure wrong =
                assertThrows(
                        AsrFailure.class,
                        () -> service.requireCandidate(7, ID, "STOP", runtime));
        assertEquals("VOICE_INTERPRETATION_INVALID", wrong.code);
        assertThrows(
                AsrFailure.class, () -> service.requireCandidate(8, ID, "PAUSE", runtime));
    }

    @Test
    void expiredCandidateCannotAuthorizeProposal() {
        Instant acceptedAt = Instant.parse("2026-09-24T00:00:00Z");
        Clock clock = mock(Clock.class);
        when(clock.instant())
                .thenReturn(acceptedAt, acceptedAt, acceptedAt.plusSeconds(1801));
        service = new IntentService(access, settings, runtimes, json, clock);

        service.interpret(7, ID, request("暂停任务", null));
        AsrFailure expired =
                assertThrows(
                        AsrFailure.class,
                        () -> service.requireCandidate(7, ID, "PAUSE", runtime("PAUSE")));
        assertEquals("VOICE_INTERPRETATION_INVALID", expired.code);
    }

    @Test
    void localLlmCandidateStillUsesJavaSafetyAndContractEnvelope() {
        settings.setIntentProvider("local-llm");
        LocalLlmIntentProvider llm = mock(LocalLlmIntentProvider.class);
        when(llm.classify("请让编队立即执行"))
                .thenReturn(new IntentClassification("CANDIDATE", null, null, "START"));
        service = new IntentService(access, settings, runtimes, json, llm, java.time.Clock.systemUTC());

        String requestId = java.util.UUID.randomUUID().toString();
        var outcome = service.interpret(7, requestId, request(requestId, "请让编队立即执行", null));
        var data = (ObjectNode) outcome.body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals("START", data.path("action").asText());
        assertEquals("local-llm", data.path("provider").asText());
        assertEquals(settings.getLlmModel(), data.path("model").asText());
        verify(llm).classify("请让编队立即执行");

        String negatedId = java.util.UUID.randomUUID().toString();
        var negated =
                service.interpret(
                        7, negatedId, request(negatedId, "不要停止任务", null));
        assertEquals(
                "NOT_ACTIONABLE",
                ((ObjectNode) negated.body().get("data")).path("status").asText());
        verifyNoMoreInteractions(llm);
    }

    @Test
    void unknownFieldsAndHeaderBodyMismatchAreRejected() {
        ObjectNode unknown = request("暂停任务", null);
        unknown.put("vendor", "forbidden");
        assertEquals(
                "VOICE_INVALID_REQUEST",
                assertThrows(AsrFailure.class, () -> service.interpret(7, ID, unknown)).code);
        assertEquals(
                "VOICE_INVALID_REQUEST",
                assertThrows(
                                AsrFailure.class,
                                () ->
                                        service.interpret(
                                                7,
                                                "66666666-6666-4666-8666-666666666666",
                                                request("暂停任务", null)))
                        .code);
    }

    private void assertCandidate(String text, String action, String intent) {
        String id = java.util.UUID.randomUUID().toString();
        assertCandidate(service.interpret(7, id, request(id, text, null)), action, intent);
    }

    private void assertCandidate(AsrResponses.Outcome outcome, String action, String intent) {
        var data = (ObjectNode) outcome.body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals(action, data.path("action").asText());
        assertEquals(intent, data.path("intent").asText());
        assertTrue(data.path("confidence").isNull());
    }

    private void assertStatus(String text, String status, String reason) {
        String id = java.util.UUID.randomUUID().toString();
        var data = (ObjectNode) service.interpret(7, id, request(id, text, null)).body().get("data");
        assertEquals(status, data.path("status").asText());
        assertEquals(reason, data.path("reason").asText());
    }

    private ObjectNode request(String text, ObjectNode runtime) {
        return request(ID, text, runtime);
    }

    private ObjectNode request(String id, String text, ObjectNode runtime) {
        ObjectNode n = json.object();
        n.put("requestId", id).put("text", text).put("locale", "zh-CN");
        n.putArray("allowedActions").add("START").add("PAUSE").add("RESUME").add("STOP");
        n.putArray("availableDeviceCodes").add("UAV-001").add("USV-001");
        if (runtime == null) n.putNull("runtimeContext");
        else n.set("runtimeContext", runtime);
        return n;
    }

    private ObjectNode hint() {
        return json.object()
                .put("runtimeRef", REF)
                .put("runtimeGeneration", GEN)
                .put("contextVersion", 4);
    }

    private ObjectNode runtime(String capability) {
        ObjectNode n = hint();
        n.putArray("capabilities").add(capability);
        return n;
    }
}
