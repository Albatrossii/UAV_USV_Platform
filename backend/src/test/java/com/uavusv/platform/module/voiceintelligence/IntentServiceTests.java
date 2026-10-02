package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.voicecontrol.RuntimeContextRegistry;
import com.uavusv.platform.module.voicecontrol.VoiceAccess;
import com.uavusv.platform.module.voicecontrol.VoiceJson;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.Map;
import java.time.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

class IntentServiceTests {
    static final String ID = "11111111-1111-4111-8111-111111111111";
    static final String REF = "22222222-2222-4222-8222-222222222222";
    static final String GEN = "33333333-3333-4333-8333-333333333333";

    VoiceAccess access;
    AsrSettings settings;
    RuntimeContextRegistry runtimes;
    VoiceJson json;
    IntentService service;
    TestClock clock;

    @BeforeEach
    void setup() {
        access = mock(VoiceAccess.class);
        settings = new AsrSettings();
        settings.setEnabled(true);
        runtimes = mock(RuntimeContextRegistry.class);
        json = new VoiceJson();
        clock = new TestClock(Instant.parse("2026-09-25T00:00:00Z"));
        service = new IntentService(access, settings, runtimes, json, clock);
    }

    @Test
    void fourSupportedActionsBecomeCandidates() {
        assertCandidate("开始任务", "START", "MISSION_START");
        assertCandidate("暂停当前任务", "PAUSE", "MISSION_PAUSE");
        assertCandidate("恢复运行", "RESUME", "MISSION_RESUME");
        assertCandidate("停止任务", "STOP", "MISSION_STOP");
    }

    @Test
    void commonExplicitPhrasesSkipTheLocalModelRoundTrip() {
        settings.setIntentProvider("local-llm");
        LocalLlmIntentProvider model = mock(LocalLlmIntentProvider.class);
        service = new IntentService(access, settings, runtimes, json, model, clock);

        assertEquals("local-rules", data("暂停当前任务").path("provider").asText());
        assertEquals("PAUSE", data("暂停当前任务").path("action").asText());
        assertEquals("local-rules", data("恢复运行").path("provider").asText());
        assertEquals("RESUME", data("恢复运行").path("action").asText());
        verifyNoInteractions(model);
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
    void singleDeviceCandidateIsBoundToTheLiveSingleDeviceRun() {
        ObjectNode runtime = runtime("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "ESCORT_GUARD_SINGLE_DEVICE");
        runtime.putArray("_members").add("UAV-001").add("USV-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        String id = java.util.UUID.randomUUID().toString();
        var outcome = service.interpret(7, id, request(id, "UAV-001 悬停", hint()));
        var data = (ObjectNode) outcome.body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals("SINGLE_DEVICE_CONTROL", data.path("intent").asText());
        assertEquals("DEVICE_COMMAND", data.path("action").asText());
        assertEquals("UAV-001", data.path("targetDeviceCode").asText());
        assertEquals("UAV_HOVER", data.path("deviceCommandType").asText());
        assertEquals("local-rules", data.path("provider").asText());
        assertEquals(new IntentService.TargetedCandidate("UAV-001", "UAV_HOVER"),
                service.requireTargetedCandidate(7, id, runtime));
        assertThrows(AsrFailure.class,
                () -> service.requireTargetedCandidate(8, id, runtime));

        String pauseId = java.util.UUID.randomUUID().toString();
        var pause = (ObjectNode) service.interpret(7, pauseId,
                request(pauseId, "USV-002 暂停", hint())).body().get("data");
        assertEquals("SINGLE_DEVICE_CONTROL", pause.path("intent").asText());
        assertEquals("USV-002", pause.path("targetDeviceCode").asText());
        assertEquals("USV_HOLD", pause.path("deviceCommandType").asText());

        String chineseOrdinalId = java.util.UUID.randomUUID().toString();
        var chineseOrdinal = (ObjectNode) service.interpret(7, chineseOrdinalId,
                request(chineseOrdinalId, "第一架无人机悬停", hint())).body().get("data");
        assertEquals("SINGLE_DEVICE_CONTROL", chineseOrdinal.path("intent").asText());
        assertEquals("UAV-001", chineseOrdinal.path("targetDeviceCode").asText());
        assertEquals("UAV_HOVER", chineseOrdinal.path("deviceCommandType").asText());

        String vesselOrdinalId = java.util.UUID.randomUUID().toString();
        var vesselOrdinal = (ObjectNode) service.interpret(7, vesselOrdinalId,
                request(vesselOrdinalId, "第二艘无人艇返航", hint())).body().get("data");
        assertEquals("USV-002", vesselOrdinal.path("targetDeviceCode").asText());
        assertEquals("USV_RETURN", vesselOrdinal.path("deviceCommandType").asText());

        String multipleActionsId = java.util.UUID.randomUUID().toString();
        var multipleActions = (ObjectNode) service.interpret(7, multipleActionsId,
                request(multipleActionsId, "第一架无人机悬停返航", hint())).body().get("data");
        assertEquals("NEEDS_CLARIFICATION", multipleActions.path("status").asText());
        assertEquals("AMBIGUOUS_ACTION", multipleActions.path("reason").asText());
    }

    @Test
    void singleDeviceParserClarifiesUnknownAndMultipleTargetsWithoutDowngrading() {
        ObjectNode runtime = runtime("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "ESCORT_GUARD_SINGLE_DEVICE");
        runtime.putArray("_members").add("UAV-001").add("UAV-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        String unknownId = java.util.UUID.randomUUID().toString();
        var unknown = (ObjectNode) service.interpret(7, unknownId,
                request(unknownId, "UAV-009 悬停", hint())).body().get("data");
        assertEquals("NEEDS_CLARIFICATION", unknown.path("status").asText());
        assertEquals("AMBIGUOUS_TARGET", unknown.path("reason").asText());
        String multipleId = java.util.UUID.randomUUID().toString();
        var multiple = (ObjectNode) service.interpret(7, multipleId,
                request(multipleId, "UAV-001 与 UAV-002 悬停", hint())).body().get("data");
        assertEquals("NEEDS_CLARIFICATION", multiple.path("status").asText());
        String negatedId = java.util.UUID.randomUUID().toString();
        var negated = (ObjectNode) service.interpret(7, negatedId,
                request(negatedId, "不要让一号无人机悬停", hint())).body().get("data");
        assertEquals("NOT_ACTIONABLE", negated.path("status").asText());
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

        ObjectNode changedGeneration = runtime.deepCopy();
        changedGeneration.put("runtimeGeneration", "44444444-4444-4444-8444-444444444444");
        AsrFailure changed =
                assertThrows(
                        AsrFailure.class,
                        () -> service.requireCandidate(7, ID, "PAUSE", changedGeneration));
        assertEquals("VOICE_INTERPRETATION_INVALID", changed.code);
    }

    @Test
    void expiredCandidateCannotCreateProposal() {
        ObjectNode runtime = runtime("PAUSE");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        service.interpret(7, ID, request("暂停任务", hint()));

        clock.advance(Duration.ofMinutes(31));
        AsrFailure expired =
                assertThrows(
                        AsrFailure.class,
                        () -> service.requireCandidate(7, ID, "PAUSE", runtime));
        assertEquals("VOICE_INTERPRETATION_INVALID", expired.code);
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

    @Test
    void concurrentRequestsCannotExceedGlobalCapacity() throws Exception {
        for (int i = 0; i < 999; i++) {
            String id = java.util.UUID.randomUUID().toString();
            service.interpret(7, id, request(id, "暂停任务", null));
        }

        CountDownLatch bothInsideContextLookup = new CountDownLatch(2);
        CountDownLatch release = new CountDownLatch(1);
        ObjectNode runtime = runtime("PAUSE");
        when(runtimes.require(REF, 7))
                .thenAnswer(
                        invocation -> {
                            bothInsideContextLookup.countDown();
                            if (!release.await(5, TimeUnit.SECONDS))
                                throw new IllegalStateException("concurrency test timed out");
                            return runtime;
                        });

        var pool = Executors.newFixedThreadPool(2);
        try {
            String firstId = java.util.UUID.randomUUID().toString();
            String secondId = java.util.UUID.randomUUID().toString();
            var first = pool.submit(() -> service.interpret(7, firstId, request(firstId, "暂停任务", hint())));
            var second = pool.submit(() -> service.interpret(7, secondId, request(secondId, "暂停任务", hint())));
            assertTrue(bothInsideContextLookup.await(5, TimeUnit.SECONDS));
            release.countDown();

            int successes = 0;
            int limited = 0;
            for (var future : java.util.List.of(first, second)) {
                try {
                    assertCandidate(future.get(5, TimeUnit.SECONDS), "PAUSE", "MISSION_PAUSE");
                    successes++;
                } catch (java.util.concurrent.ExecutionException error) {
                    assertInstanceOf(AsrFailure.class, error.getCause());
                    assertEquals("VOICE_RATE_LIMITED", ((AsrFailure) error.getCause()).code);
                    limited++;
                }
            }
            assertEquals(1, successes);
            assertEquals(1, limited);
        } finally {
            release.countDown();
            pool.shutdownNow();
        }
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

    private ObjectNode data(String text) {
        String id = java.util.UUID.randomUUID().toString();
        return (ObjectNode) service.interpret(7, id, request(id, text, null))
                .body().get("data");
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

    private static final class TestClock extends Clock {
        private Instant now;

        TestClock(Instant now) { this.now = now; }

        void advance(Duration duration) { now = now.plus(duration); }

        @Override public ZoneId getZone() { return ZoneOffset.UTC; }

        @Override public Clock withZone(ZoneId zone) { return this; }

        @Override public Instant instant() { return now; }
    }
}
