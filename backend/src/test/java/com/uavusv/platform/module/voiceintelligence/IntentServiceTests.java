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

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
            "一号无人机暂不返航", "一号无人机不返航", "一号无人机不降落",
            "一号无人机没有返航", "一号无人机尚未降落", "别让一号无人机返航",
            "一号无人机无需返航", "一号无人机无须降落", "禁止一号无人机返航",
            "取消一号无人机降落", "一号无人机勿返航", "一号无人机莫降落",
            "暂缓一号无人机返航", "一号无人机不要返回", "一号无人艇不用停船",
            "未开始任务", "没启动任务", "不执行任务", "不暂停任务", "不继续任务",
            "不恢复任务", "不停止任务", "不终止任务", "不结束任务",
            "一号无人机不悬停", "一号无人艇不驻留", "一号无人艇不待命",
            "一号无人艇不保持", "一号无人艇不归队",
            "开始任务，然后一号无人机暂不返航", "一号无人机悬停，然后不降落",
            "一号无人机暂 不 返航"
    })
    void negatedActionsNeverAuthorizeTargetedOrSequenceCandidates(String text) {
        ObjectNode runtime = runtime("START");
        ((com.fasterxml.jackson.databind.node.ArrayNode) runtime.path("capabilities"))
                .add("PAUSE").add("RESUME").add("STOP").add("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "GB_SFLA_CS");
        runtime.putArray("_members").add("UAV-001").add("USV-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);

        var data = (ObjectNode) service.interpret(7, ID, request(text, hint())).body().get("data");

        assertEquals("NOT_ACTIONABLE", data.path("status").asText());
        assertEquals("NEGATED_ACTION", data.path("reason").asText());
        assertEquals("VOICE_INTERPRETATION_INVALID", assertThrows(AsrFailure.class,
                () -> service.requireTargetedCandidate(7, ID, runtime)).code);
        assertEquals("VOICE_INTERPRETATION_INVALID", assertThrows(AsrFailure.class,
                () -> service.requireSequenceCandidate(7, ID, runtime)).code);
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.CsvSource({
            "一号无人鸡暂不返航,一号无人机暂不返航",
            "一号机无人不降落,一号无人机不降落",
            "一号无人庭无须返航,一号无人艇无须返航",
            "取消一号艇无人驻留,取消一号无人艇驻留",
            "开始任务然后一号无人停不归队,开始任务然后一号无人艇不归队"
    })
    void asrNounNormalizationPreservesNegatedActions(String text, String normalized) {
        negatedActionsNeverAuthorizeTargetedOrSequenceCandidates(text);
        var data = (ObjectNode) service.interpret(7, ID, request(text, hint())).body().get("data");
        assertEquals(normalized, data.path("normalizedText").asText());
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
            "一号无人机暂不返航", "一号无人机不降落", "一号无人鸡不返航", "开始任务然后一号无人机不返航"
    })
    void localModelConfigurationStillRejectsNegatedActionsWithoutCallingTheModel(String text) {
        settings.setIntentProvider("local-llm");
        LocalLlmIntentProvider model = mock(LocalLlmIntentProvider.class);
        service = new IntentService(access, settings, runtimes, json, model, clock);

        negatedActionsNeverAuthorizeTargetedOrSequenceCandidates(text);
        var data = (ObjectNode) service.interpret(7, ID, request(text, hint())).body().get("data");

        assertEquals("local-rules", data.path("provider").asText());
        verifyNoInteractions(model);
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
            "请让一号无人机返航", "一号无人机请返航", "一号无人机马上返航", "请让一号无人机马上返航"
    })
    void affirmativeReturnCommandsStillAuthorizeTargetedCandidates(String text) {
        ObjectNode runtime = runtime("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "GB_SFLA_CS");
        runtime.putArray("_members").add("UAV-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);

        var data = (ObjectNode) service.interpret(7, ID, request(text, hint())).body().get("data");

        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals(new IntentService.TargetedCandidate("UAV-001", "UAV_RETURN"),
                service.requireTargetedCandidate(7, ID, runtime));
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {"ESCORT_GUARD", "GB_SFLA_CS", "ESCORT_GUARD_SINGLE_DEVICE", "GB_SFLA_CS_SINGLE_DEVICE"})
    void singleDeviceCandidateIsBoundToTheLiveSingleDeviceRun(String algorithm) {
        ObjectNode runtime = runtime("DEVICE_COMMAND");
        runtime.put("_algorithmCode", algorithm);
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
    void commonAsrDeviceNounErrorsAreNormalizedWithoutWeakeningSafetyChecks() {
        ObjectNode runtime = runtime("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "GB_SFLA_CS_SINGLE_DEVICE");
        runtime.putArray("_members").add("UAV-001").add("USV-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);

        for (Map.Entry<String, String> sample : Map.ofEntries(
                Map.entry("让一号艇无人驻留。", "USV_HOLD"),
                Map.entry("让一号无人庭归队", "USV_RESUME"),
                Map.entry("让一号无人停返航", "USV_RETURN"),
                Map.entry("让一号机无人悬停", "UAV_HOVER"),
                Map.entry("让一号无人鸡归队", "UAV_RESUME")).entrySet()) {
            String id = java.util.UUID.randomUUID().toString();
            ObjectNode data = (ObjectNode) service.interpret(
                    7, id, request(id, sample.getKey(), hint())).body().get("data");
            assertEquals("CANDIDATE", data.path("status").asText(), sample.getKey());
            assertEquals("SINGLE_DEVICE_CONTROL", data.path("intent").asText(), sample.getKey());
            assertEquals(sample.getValue(), data.path("deviceCommandType").asText(), sample.getKey());
            assertTrue(data.path("normalizedText").asText().contains(
                    sample.getValue().startsWith("UAV") ? "无人机" : "无人艇"));
        }

        String negatedId = java.util.UUID.randomUUID().toString();
        ObjectNode negated = (ObjectNode) service.interpret(
                7, negatedId, request(negatedId, "不要让一号艇无人驻留", hint())).body().get("data");
        assertEquals("NOT_ACTIONABLE", negated.path("status").asText());

        String ambiguousId = java.util.UUID.randomUUID().toString();
        ObjectNode ambiguous = (ObjectNode) service.interpret(
                7, ambiguousId, request(ambiguousId, "让一号艇无人驻留返航", hint())).body().get("data");
        assertEquals("NEEDS_CLARIFICATION", ambiguous.path("status").asText());
        assertEquals("AMBIGUOUS_ACTION", ambiguous.path("reason").asText());
    }

    @Test
    void controlledSequenceAlsoNormalizesCommonAsrDeviceNounErrors() {
        ObjectNode runtime = runtime("START");
        ((com.fasterxml.jackson.databind.node.ArrayNode) runtime.path("capabilities"))
                .add("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "GB_SFLA_CS_SINGLE_DEVICE");
        runtime.putArray("_members").add("USV-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        for (String text : java.util.List.of(
                "开始任务，然后让一号艇无人驻留。", "开始任务让1号无人停驻留",
                "开始任务，一号无人艇驻留", "开始任务一号无人艇驻留")) {
            String id = java.util.UUID.randomUUID().toString();
            ObjectNode data = (ObjectNode) service.interpret(
                    7, id, request(id, text, hint())).body().get("data");
            assertEquals("CANDIDATE", data.path("status").asText(), text);
            assertEquals("COMMAND_SEQUENCE", data.path("intent").asText(), text);
            assertEquals("START", data.path("steps").path(0).path("action").asText(), text);
            assertEquals("USV-001", data.path("steps").path(1).path("targetDeviceCode").asText(), text);
            assertEquals("USV_HOLD", data.path("steps").path(1).path("deviceCommandType").asText(), text);
            assertTrue(data.path("normalizedText").asText().contains("无人艇"));
        }
    }

    @Test
    void contextualHoldRepairPreservesIdentityAndNeverGuessesOutsideWholeCommand() {
        ObjectNode runtime = runtime("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "GB_SFLA_CS");
        runtime.putArray("_members").add("UAV-001").add("USV-001").add("USV-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        for (String text : java.util.List.of("一号无人挺住。留。", "一号无人艇住留", "一号无人艇驻。 留。", "请让1号无人挺驻留")) {
            String id = java.util.UUID.randomUUID().toString();
            var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
            assertEquals("CANDIDATE", data.path("status").asText(), text);
            assertEquals("USV-001", data.path("targetDeviceCode").asText(), text);
            assertEquals("USV_HOLD", data.path("deviceCommandType").asText(), text);
            assertTrue(data.path("normalizedText").asText().contains("无人艇驻留"));
        }
        for (String text : java.util.List.of("挺住", "无人挺住。留。", "一号设备住留", "一号无人机住。留。",
                "不要让一号无人挺住。留。", "一号无人挺住。留？", "一号无人挺住留吗", "如果一号无人挺住留",
                "一号无人挺住留或者返航", "一号无人挺住留返航", "一号无人挺归队", "一号无人挺返航",
                "开始任务，然后一号无人挺住。留。", "一号无人挺住留，然后二号无人艇驻留",
                "报告一号无人挺住留", "一号和二号无人挺住留", "一号无人挺住。不要。留。", "99号无人挺住留")) {
            String id = java.util.UUID.randomUUID().toString();
            var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
            assertNotEquals("CANDIDATE", data.path("status").asText(), text);
        }
        String secondId = java.util.UUID.randomUUID().toString();
        var second = (ObjectNode) service.interpret(7, secondId, request(secondId, "请让二号无人挺住。留。", hint())).body().get("data");
        assertEquals("USV-002", second.path("targetDeviceCode").asText());
    }

    @Test
    void contextualHoldRepairsCommonAsrActionSyllablesOnlyForTheExactNumberedBoat() {
        ObjectNode runtime = contextualBoatRuntime();
        for (String noun : java.util.List.of("艇", "挺", "庭", "廷", "停", "听", "船")) {
            for (String first : java.util.List.of("驻", "住", "注", "主")) {
                for (String second : java.util.List.of("留", "流")) {
                    String text = "二号无人" + noun + first + "。" + second + "。";
                    String id = java.util.UUID.randomUUID().toString();
                    ObjectNode original = request(id, text, hint());
                    var data = (ObjectNode) service.interpret(7, id, original).body().get("data");
                    assertEquals(text, original.path("text").asText(), "retain the original ASR text");
                    assertEquals("CANDIDATE", data.path("status").asText(), text);
                    assertEquals("二号无人艇驻留", data.path("normalizedText").asText(), text);
                    assertEquals(new IntentService.TargetedCandidate("USV-002", "USV_HOLD"),
                            service.requireTargetedCandidate(7, id, runtime), text);
                }
            }
        }
        for (String text : java.util.List.of("二号无人船主流。", "请让第2号无人听注．,流！", " 请 让 002 号 无人船 主 留。")) {
            String id = java.util.UUID.randomUUID().toString();
            service.interpret(7, id, request(id, text, hint()));
            assertEquals(new IntentService.TargetedCandidate("USV-002", "USV_HOLD"),
                    service.requireTargetedCandidate(7, id, runtime), text);
        }
    }

    @Test
    void ambiguousBoatNounsRequireAWholeNumberedUtteranceAndAnExactSupportedAction() {
        ObjectNode runtime = contextualBoatRuntime();
        Map<String, String> actions = Map.of(
                "驻留", "USV_HOLD", "待命", "USV_HOLD", "保持", "USV_HOLD", "暂停", "USV_HOLD",
                "归队", "USV_RESUME", "继续", "USV_RESUME", "返航", "USV_RETURN", "返回", "USV_RETURN",
                "停止", "USV_STOP", "停船", "USV_STOP");
        for (String noun : java.util.List.of("无人听", "无人船")) {
            actions.forEach((action, expected) -> {
                String text = "请让二号" + noun + action + "。";
                String id = java.util.UUID.randomUUID().toString();
                var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
                assertEquals("DEVICE_COMMAND", data.path("action").asText(), text);
                assertEquals("请让二号无人艇" + action, data.path("normalizedText").asText(), text);
                assertEquals(new IntentService.TargetedCandidate("USV-002", expected),
                        service.requireTargetedCandidate(7, id, runtime), text);
            });
        }
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
            "二号无人船主流？", "二号无人听注留?", "二号无人艇主流？", "二号无人停驻留？",
            "二号无人船暂停？", "二号无人听继续?", "二号无人艇驻留？", "暂停任务？", "开始任务?"
    })
    void originalQuestionsRemainNonActionableAfterRepeatedNormalization(String text) {
        ObjectNode runtime = contextualBoatRuntime();
        String id = java.util.UUID.randomUUID().toString();
        var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
        assertEquals("NOT_ACTIONABLE", data.path("status").asText(), text);
        assertEquals("QUESTION_ACTION", data.path("reason").asText(), text);
        String normalized = data.path("normalizedText").asText();
        assertTrue(normalized.contains("?") || normalized.contains("？"));
        String repeatedId = java.util.UUID.randomUUID().toString();
        var repeated = (ObjectNode) service.interpret(7, repeatedId, request(repeatedId, normalized, hint())).body().get("data");
        assertEquals("NOT_ACTIONABLE", repeated.path("status").asText());
        assertThrows(AsrFailure.class, () -> service.requireTargetedCandidate(7, id, runtime));
        assertThrows(AsrFailure.class, () -> service.requireCandidate(7, id, "PAUSE", runtime));
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
            "主流", "无人船主流", "二号设备主流", "二号无人机主流", "二号无人船主流吗",
            "不要二号无人船主流", "如果二号无人船主流", "报告二号无人船主流", "二号无人船主流或者返航",
            "二号无人船主流返航", "二号无人船主流，然后一号无人艇驻留", "开始任务然后二号无人船主流",
            "开始任务然后二号无人艇主流", "开始任务然后二号无人听驻留",
            "报告二号无人船继续", "如果二号无人听暂停", "二号无人船继续吗", "无人听继续",
            "一号和二号无人船驻留", "二号无人船暂停或者继续", "99号无人船主流", "0号无人听驻留",
            "十二号无人船暂停", "1000号无人船暂停", "二号无人船悬停", "二号无人听降落"
    })
    void newAsrRepairsNeverEscapeTheirSingleAffirmativeDeviceBoundary(String text) {
        ObjectNode runtime = contextualBoatRuntime();
        String id = java.util.UUID.randomUUID().toString();
        var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
        assertNotEquals("CANDIDATE", data.path("status").asText(), text);
        assertThrows(AsrFailure.class, () -> service.requireTargetedCandidate(7, id, runtime), text);
        assertThrows(AsrFailure.class, () -> service.requireSequenceCandidate(7, id, runtime), text);
        for (String action : java.util.List.of("START", "PAUSE", "RESUME", "STOP"))
            assertThrows(AsrFailure.class, () -> service.requireCandidate(7, id, action, runtime), text);
    }

    @Test
    void negativeBoatCommandsKeepPrecedenceOverQuestionAndUnknownNounGates() {
        contextualBoatRuntime();
        for (String text : java.util.List.of("不要让二号无人船继续？", "二号无人听不归队", "二号无人停不驻留？")) {
            String id = java.util.UUID.randomUUID().toString();
            var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
            assertEquals("NOT_ACTIONABLE", data.path("status").asText(), text);
            assertEquals("NEGATED_ACTION", data.path("reason").asText(), text);
        }
    }

    private ObjectNode contextualBoatRuntime() {
        ObjectNode runtime = runtime("START");
        ((com.fasterxml.jackson.databind.node.ArrayNode) runtime.path("capabilities"))
                .add("PAUSE").add("RESUME").add("STOP").add("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "ESCORT_GUARD");
        runtime.putArray("_members").add("UAV-002").add("USV-001").add("USV-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        return runtime;
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
            "一号无人机返航，然后让二号无人船驻留",
            "一号无人船返航，二号无人船驻留",
            "开始任务然后二号无人船驻留", "开始任务后二号无人船驻留",
            "开始任务让二号无人船驻留",
            "先让一号无人机返航，再让二号无人船驻留",
            "一号无人机返航，等待三秒，然后让二号无人船驻留",
            "请让一号无人船驻留；让二号无人船返航。"
    })
    void boatAliasWorksInCompleteClausesWithoutDroppingOrderOrWait(String text) {
        ObjectNode runtime = contextualBoatRuntime();
        ((com.fasterxml.jackson.databind.node.ArrayNode) runtime.path("_members")).add("UAV-001");
        String id = java.util.UUID.randomUUID().toString();
        var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText(), text);
        assertEquals("SEQUENCE", data.path("action").asText(), text);
        assertFalse(data.path("normalizedText").asText().contains("无人船"));
        var steps = service.requireSequenceSteps(7, id, runtime);
        assertEquals(text.contains("等待三秒") ? 3 : 2, steps.size());
        assertEquals("USV-002", steps.get(steps.size() - 1).path("targetDeviceCode").asText());
        assertEquals(text.startsWith("先"), steps.get(1).path("waitForPrevious").asBoolean());
        if (text.contains("等待三秒")) {
            assertEquals("WAIT", steps.get(1).path("action").asText());
            assertEquals(3, steps.get(1).path("waitSeconds").asInt());
        }
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
            "不要一号无人船返航，然后二号无人船驻留",
            "一号无人船返航，然后二号无人船驻留吗？",
            "如果一号无人船返航，然后二号无人船驻留",
            "一号无人船返航，然后无人船驻留",
            "一号无人船返航，然后99号无人船驻留",
            "一号无人船返航，然后二号无人船悬停",
            "一号无人船返航，然后二号无人船驻留或者返航",
            "一号无人船返航，二号无人船驻留，等待三秒，二号无人船归队，暂停任务"
    })
    void multiClauseBoatRepairNeverDropsUnsafeOrUnsupportedParts(String text) {
        ObjectNode runtime = contextualBoatRuntime();
        String id = java.util.UUID.randomUUID().toString();
        var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
        assertNotEquals("CANDIDATE", data.path("status").asText(), text);
        assertThrows(AsrFailure.class, () -> service.requireSequenceSteps(7, id, runtime));
    }

    @Test
    void controlledTwoStepSequenceProducesFrozenCandidate() {
        ObjectNode runtime = runtime("START");
        ((com.fasterxml.jackson.databind.node.ArrayNode) runtime.path("capabilities"))
                .add("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "ESCORT_GUARD_SINGLE_DEVICE");
        runtime.putArray("_members").add("UAV-001").add("USV-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);

        for (String text : java.util.List.of(
                "开始任务后一号无人机悬停",
                "先开始任务再让第一架无人机悬停",
                "开始任务，然后UAV001悬停")) {
            String id = java.util.UUID.randomUUID().toString();
            ObjectNode data = (ObjectNode) service.interpret(7, id, request(id, text, hint()))
                    .body().get("data");
            assertEquals("CANDIDATE", data.path("status").asText());
            assertEquals("COMMAND_SEQUENCE", data.path("intent").asText());
            assertEquals("SEQUENCE", data.path("action").asText());
            assertEquals("rules-sequence-v1", data.path("model").asText());
            assertEquals("START", data.path("steps").path(0).path("action").asText());
            assertEquals("UAV-001", data.path("steps").path(1).path("targetDeviceCode").asText());
            assertEquals("UAV_HOVER", data.path("steps").path(1).path("deviceCommandType").asText());
            assertEquals(text.startsWith("先"), data.path("steps").path(1).path("waitForPrevious").asBoolean());
            assertDoesNotThrow(() -> service.requireCandidate(7, id, "SEQUENCE", runtime));
            assertEquals(
                    new IntentService.TargetedCandidate("UAV-001", "UAV_HOVER"),
                    service.requireSequenceCandidate(7, id, runtime));
        }
    }

    @Test
    void controlledSequenceRejectsUnsafeOrUnapprovedCombinations() {
        ObjectNode runtime = runtime("START");
        ((com.fasterxml.jackson.databind.node.ArrayNode) runtime.path("capabilities"))
                .add("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "ESCORT_GUARD_SINGLE_DEVICE");
        runtime.putArray("_members").add("UAV-001").add("USV-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);

        String negatedId = java.util.UUID.randomUUID().toString();
        ObjectNode negated = (ObjectNode) service.interpret(
                7, negatedId, request(negatedId, "不要开始任务然后一号无人机悬停", hint()))
                .body().get("data");
        assertEquals("NOT_ACTIONABLE", negated.path("status").asText());

        String returnId = java.util.UUID.randomUUID().toString();
        ObjectNode returned = (ObjectNode) service.interpret(
                7, returnId, request(returnId, "开始任务后一号无人机返航", hint()))
                .body().get("data");
        assertEquals("CANDIDATE", returned.path("status").asText());
        assertEquals("UAV_RETURN", returned.path("steps").path(1).path("deviceCommandType").asText());

        String ambiguousId = java.util.UUID.randomUUID().toString();
        ObjectNode ambiguous = (ObjectNode) service.interpret(
                7,
                ambiguousId,
                request(ambiguousId, "开始任务后一号和二号无人机悬停", hint()))
                .body().get("data");
        assertEquals("NEEDS_CLARIFICATION", ambiguous.path("status").asText());
    }

    @Test
    void compoundCommandsNeverSilentlyDropAnUnsupportedClauseOrFifthStep() {
        ObjectNode runtime = runtime("START");
        ((com.fasterxml.jackson.databind.node.ArrayNode) runtime.path("capabilities")).add("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "GB_SFLA_CS_SINGLE_DEVICE");
        runtime.putArray("_members").add("UAV-001").add("USV-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        for (String text : java.util.List.of(
                "暂停任务让一号无人艇驻留",
                "开始任务，一号无人艇驻留，等待2秒，归队，再驻留",
                "开始任务让一号无人艇驻留然后拍照")) {
            String id = java.util.UUID.randomUUID().toString();
            ObjectNode data = (ObjectNode) service.interpret(
                    7, id, request(id, text, hint())).body().get("data");
            assertEquals("NEEDS_CLARIFICATION", data.path("status").asText(), text);
            assertEquals("AMBIGUOUS_ACTION", data.path("reason").asText(), text);
        }
    }

    @Test
    void sharedRegressionCorpusKeepsEveryStepAndDeviceIdentity() throws Exception {
        ObjectNode runtime = runtime("START");
        ((com.fasterxml.jackson.databind.node.ArrayNode) runtime.path("capabilities"))
                .add("PAUSE").add("RESUME").add("STOP").add("DEVICE_COMMAND");
        runtime.put("_algorithmCode", "GB_SFLA_CS");
        runtime.putArray("_members").add("UAV-001").add("USV-001").add("USV-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var path = java.nio.file.Path.of("../docs/voice-control-p1/voice-command-regression.json");
        var corpus = json.mapper.readTree(java.nio.file.Files.readString(path));
        for (var sample : corpus) {
            String id = java.util.UUID.randomUUID().toString();
            String text = sample.path("text").asText();
            var data = (ObjectNode) service.interpret(7, id, request(id, text, hint())).body().get("data");
            assertEquals(sample.path("status").asText(), data.path("status").asText(), text);
            if (!sample.has("actions")) continue;
            var actual = "SEQUENCE".equals(data.path("action").asText()) ? data.path("steps") : json.mapper.createArrayNode().add(data);
            assertEquals(sample.path("actions").size(), actual.size(), text);
            for (int i = 0; i < actual.size(); i++) {
                var step = actual.get(i);
                assertEquals(sample.path("actions").get(i).asText(), step.path("deviceCommandType").asText(step.path("action").asText()), text);
                assertEquals(sample.path("targets").get(i).asText(), step.path("targetDeviceCode").asText(""), text);
            }
        }
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

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
        "一号无人机追击一号机船。", "请让1号无人机追击第1号机船", "一号无人机追击一号敌船",
        "一号无人机追击一号的船。", "请让1号无人机追击第1号的船",
        "让一架无人机追击一号敌船。", "请让一架无人机追击一号敌船。"
    })
    void chaseAsrRepairPrecedesGenericAircraftAlias(String text) {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        runtime.putObject("_algorithmTargets").putObject("THREAT-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var data = (ObjectNode)service.interpret(7, ID, request(text, hint())).body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals("ALGORITHM_PRESET", data.path("action").asText());
        assertEquals(1, service.requirePresetParameters(7, ID, runtime).path("presetId").asInt());
        assertTrue(data.path("normalizedText").asText().endsWith("敌船"));
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {"恢复首位。", "请恢复首位！", "恢复守卫"})
    void guardHomophoneIsPresetRatherThanMissionResume(String text) {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var data = (ObjectNode)service.interpret(7, ID, request(text, hint())).body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals("ALGORITHM_PRESET", data.path("action").asText());
        assertEquals(19, data.path("parameters").path("presetId").asInt());
        assertTrue(data.path("normalizedText").asText().endsWith("守卫"));
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {"的喘", "d穿", "地船", "di chuan", "ＤＩＣＨＵＡＮ"})
    void chaseSecondTargetHomophoneDoesNotReuseFirstTarget(String noun) {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        runtime.putObject("_algorithmTargets").putObject("THREAT-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var data = (ObjectNode)service.interpret(7, ID, request("一号无人机追击二号" + noun + "。", hint())).body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals(7, data.path("parameters").path("presetId").asInt());
        assertEquals("一号无人机追击二号敌船", data.path("normalizedText").asText());
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
        "将一二三号无人艇分配给二号的穿。", "将一二三号无人艇分配给二号的船。", "将一二三号无人艇分配给二号机船。"
    })
    void bindingHomophonesPreserveGroupAndTarget(String text) {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        runtime.putObject("_algorithmTargets").putObject("THREAT-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var data = (ObjectNode)service.interpret(7, ID, request(text, hint())).body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals(14, data.path("parameters").path("presetId").asInt());
        assertEquals("将一二三号无人艇分配给二号敌船", data.path("normalizedText").asText());
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
        "不要一号无人机追击一号机船", "一号无人机追击一号机船吗？",
        "如果一号无人机追击一号机船", "一号无人机追击三号机船",
        "一号无人机追击一号客船", "一号无人机追击机船",
        "不要一号无人机追击一号的船", "一号无人机追击一号的船吗？",
        "如果一号无人机追击一号的船", "一号无人机追击三号的船", "一号无人机追击的船"
    })
    void chaseRepairDoesNotGuessOtherTargetsOrConditions(String text) {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        runtime.putObject("_algorithmTargets").putObject("THREAT-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var data = (ObjectNode)service.interpret(7, ID, request(text, hint())).body().get("data");
        assertNotEquals("CANDIDATE", data.path("status").asText());
    }

    @Test
    void algorithmPresetsUseAuthoritativeContextAndPreserveSequenceParameters() {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        runtime.putObject("_algorithmTargets").putObject("THREAT-001");
        ((ObjectNode)runtime.path("_algorithmTargets")).putObject("THREAT-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        for (int preset = 0; preset <= 20; preset++) {
            String id = java.util.UUID.randomUUID().toString();
            var data = (ObjectNode)service.interpret(7, id, request(id, "执行"+preset+"号指令", hint())).body().get("data");
            assertEquals("ALGORITHM_PRESET", data.path("action").asText());
            assertEquals(preset, data.path("parameters").path("presetId").asInt());
            assertEquals(data.path("parameters"), service.requirePresetParameters(7, id, runtime));
        }
        String id = java.util.UUID.randomUUID().toString();
        var result = (ObjectNode)service.interpret(7, id, request(id, "执行十三号指令，然后开始围捕", hint())).body().get("data");
        assertEquals("SEQUENCE", result.path("action").asText());
        assertEquals(13, result.path("steps").get(0).path("parameters").path("presetId").asInt());
        assertEquals(20, result.path("steps").get(1).path("parameters").path("presetId").asInt());
        for (String text : java.util.List.of("不要追击一号敌船", "不要执行七号指令", "不要第十三号指令", "不要13号命令", "是否开始围捕？")) {
            id = java.util.UUID.randomUUID().toString();
            result = (ObjectNode)service.interpret(7,id,request(id,text,hint())).body().get("data");
            assertNotEquals("CANDIDATE", result.path("status").asText());
        }
        ((ObjectNode)runtime.path("_algorithmTargets")).remove("THREAT-002");
        id = java.util.UUID.randomUUID().toString();
        result = (ObjectNode)service.interpret(7,id,request(id,"一号无人机追击二号敌船",hint())).body().get("data");
        assertEquals("UNSUPPORTED",result.path("status").asText());
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
        "执行第二十一号指令", "开始执行第二十一号指令", "恢复执行九十九号命令",
        "开始执行一个指令", "开始任务，然后执行一个指令",
        "开始任务，然后开始执行第二十一号指令", "启动XXI", "启动13.5", "执行-13"
    })
    void invalidOrAmbiguousPresetWordsCannotFallBackToMissionActions(String text) {
        assertStatus(text, "NEEDS_CLARIFICATION", "AMBIGUOUS_ACTION");
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
        "执行第13条指令", "执行第十三条指令", "执行第十三号指令", "执行第十三个指令",
        "执行第XIII条指令", "执行第ⅩⅢ号指令", "执行第１３条指令", "指令xiii",
        "请帮我执行第十三条指令吧", "开始执行第十三号指令", "执行第壹拾叁条命令"
    })
    void flexiblePresetLanguageUsesTheSameAuthoritativePlan(String text) {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        runtime.putObject("_algorithmTargets").putObject("THREAT-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var data = (ObjectNode)service.interpret(7, ID, request(text, hint())).body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals("ALGORITHM_PRESET", data.path("action").asText());
        assertEquals(13, data.path("parameters").path("presetId").asInt());
        assertEquals(13, service.requirePresetParameters(7, ID, runtime).path("presetId").asInt());
    }

    @Test
    void mixedNumericWritingWorksInsideOrderedCommands() {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        runtime.putObject("_algorithmTargets").putObject("THREAT-001");
        ((ObjectNode)runtime.path("_algorithmTargets")).putObject("THREAT-002");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var data = (ObjectNode)service.interpret(7, ID,
                request("执行第十三条指令，然后执行第ⅩⅣ个指令，再执行20号命令", hint())).body().get("data");
        assertEquals("SEQUENCE", data.path("action").asText());
        for (int i = 0; i < 3; i++)
            assertEquals(new int[]{13,14,20}[i], data.path("steps").path(i).path("parameters").path("presetId").asInt());
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
        "执行围捕观察全过程。", "执行围捕，观察全过程。", "请开始围捕，查看整个过程"
    })
    void captureViewingDescriptionIsOnePresetNotAnUnsupportedSequence(String text) {
        ObjectNode runtime = runtime("ALGORITHM_PRESET");
        runtime.put("_algorithmCommandProfile", "escort-20261020");
        runtime.putObject("_algorithmTargets").putObject("THREAT-001");
        when(runtimes.require(REF, 7)).thenReturn(runtime);
        var data = (ObjectNode)service.interpret(7, ID, request(text, hint())).body().get("data");
        assertEquals("CANDIDATE", data.path("status").asText());
        assertEquals("ALGORITHM_PRESET", data.path("action").asText());
        assertEquals(20, service.requirePresetParameters(7, ID, runtime).path("presetId").asInt());
        assertFalse(data.has("steps"));
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
        "不要第十三条指令", "别第XIII个命令", "取消第ⅩⅢ号指令", "不要指令XIII",
        "不要第壹拾叁条指令", "不要第１３条指令"
    })
    void numericAliasesRetainNegation(String text) {
        assertStatus(text, "NOT_ACTIONABLE", "NEGATED_ACTION");
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
