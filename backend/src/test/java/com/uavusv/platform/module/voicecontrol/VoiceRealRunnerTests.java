package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.module.mission.repository.MissionRunRepository;
import com.uavusv.platform.module.mission.service.*;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandResponse;
import com.uavusv.platform.module.runtimecontrol.entity.CommandStatus;
import com.uavusv.platform.module.runtimecontrol.service.RuntimeControlService;
import com.uavusv.platform.module.voiceintelligence.IntentService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.test.util.ReflectionTestUtils;
import java.nio.file.*;
import java.util.*;
import java.util.function.BooleanSupplier;

/** Real Java process manager and Python adapter; fresh H2, no HTTP or Unity simulation. */
@EnabledIfEnvironmentVariable(named = "P0_REAL_RUNNER", matches = ".+")
class VoiceRealRunnerTests extends VoiceControlTests {
    private void until(BooleanSupplier condition, String reason) throws Exception {
        long end = System.nanoTime() + 10_000_000_000L;
        while (!condition.getAsBoolean()) {
            if (System.nanoTime() >= end) fail(reason);
            Thread.sleep(25);
        }
    }

    @Test
    void singleDeviceVoiceConfirmationReachesRealRunnerAndWaitsForFrameReceipt() throws Exception {
        String source = VoiceJson.uuid();
        var intents = mock(IntentService.class);
        app = new VoiceCommandApplicationService(s, j, t, a, r, settings, intents);
        var runtimeService = mock(RuntimeControlService.class);
        @SuppressWarnings("unchecked")
        ObjectProvider<RuntimeControlService> provider = mock(ObjectProvider.class);
        when(provider.getObject()).thenReturn(runtimeService);
        worker = new VoiceDispatcher(s, j, t, r, provider);
        var manager = new AlgorithmRuntimeManager(
                new ObjectMapper(), mock(MissionRunRepository.class), mock(AlgorithmCatalogService.class),
                System.getenv().getOrDefault("PYTHON_COMMAND", "python"), System.getenv("P0_REAL_RUNNER"));
        ReflectionTestUtils.setField(manager, "voiceBridge", new VoiceRuntimeBridge(r, app, worker, j));
        when(intents.requireTargetedCandidate(eq(1L), eq(source), any()))
                .thenReturn(new IntentService.TargetedCandidate("UAV-001", "UAV_HOVER"));
        when(runtimeService.issueCommand(any(), any())).thenAnswer(invocation -> {
            var request = invocation.<com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandRequest>getArgument(0);
            var result = manager.controlDevice(
                    request.runId(), request.deviceCode(), request.commandType().name());
            var selected = (com.fasterxml.jackson.databind.JsonNode) null;
            for (var agent : result.latestFrame().path("agents")) {
                if ("HOLDING".equals(agent.path("status").asText())) {
                    selected = agent;
                    break;
                }
            }
            assertNotNull(selected, "Authoritative frame did not report any held device");
            return new RuntimeCommandResponse(
                    1L, "single-device-test", request.commandType(), request.runtimeScope(),
                    request.runtimeInstanceId(), CommandStatus.SUCCEEDED, "applied", null,
                    java.time.LocalDateTime.now());
        });
        try {
            var prepared = manager.prepare(
                    990032L, "ESCORT_GUARD",
                    Map.<String, Object>of("standaloneVirtualSimulation", true, "seed", 42));
            String ref = prepared.runtimeRef();
            until(() -> r.channel(ref).heartbeatNanos != null, "No single-device runtime heartbeat");
            manager.action(990032L, "START");
            worker.tick();
            until(() -> "RUNNING".equals(app.context(ref).path("state").asText()), "Fleet did not start");
            var proposal = app.createProposal(VoiceJson.uuid(), source, j.object()
                    .put("runtimeRef", ref)
                    .put("runtimeGeneration", prepared.runtimeGeneration())
                    .put("expectedContextVersion", app.context(ref).path("contextVersion").asLong())
                    .put("intent", "SINGLE_DEVICE_CONTROL")).data();
            assertEquals("AWAITING_CONFIRMATION", proposal.path("status").asText());
            assertTrue(proposal.path("requiresConfirmation").asBoolean());
            assertEquals("UAV-001", proposal.path("plan").path("targetDeviceCode").asText());

            var confirmed = app.confirm(
                    proposal.path("proposalId").asText(), VoiceJson.uuid(), confirmation(proposal)).data();
            String executionId = confirmed.path("execution").path("executionId").asText();
            worker.tick();
            until(() -> "SUCCEEDED".equals(app.execution(executionId).path("state").asText()),
                    "Single-device voice command did not receive authoritative success");

            var execution = app.execution(executionId);
            assertEquals("DEVICE_COMMAND", execution.path("action").asText());
            assertEquals("SUCCESS", execution.path("outcome").asText());
            var requestCaptor = org.mockito.ArgumentCaptor.forClass(
                    com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandRequest.class);
            verify(runtimeService, times(1)).issueCommand(requestCaptor.capture(), eq("alice"));
            assertEquals("UAV-001", requestCaptor.getValue().deviceCode());
            assertEquals("UAV_HOVER", requestCaptor.getValue().commandType().name());
            assertEquals("ALGORITHM_RUN:990032", requestCaptor.getValue().runtimeInstanceId());
        } finally {
            manager.close();
        }
    }

    @Test
    void realRunnerReadyHeartbeatAndFourActions() throws Exception {
        var runner = Path.of(System.getenv("P0_REAL_RUNNER")).toAbsolutePath();
        assertTrue(Files.isRegularFile(runner), "Runner file missing");
        var report = j.object().put("runner", runner.toString()).put("startedAt", java.time.Instant.now().toString());
        var steps = report.putArray("actions");
        var manager = new AlgorithmRuntimeManager(new ObjectMapper(), mock(MissionRunRepository.class),
                mock(AlgorithmCatalogService.class), System.getenv().getOrDefault("PYTHON_COMMAND", "python"), runner.toString());
        ReflectionTestUtils.setField(manager, "voiceBridge", new VoiceRuntimeBridge(r, app, worker, j));
        try {
            var prepared = manager.prepare(990031L, "GB_SFLA_CS",
                    Map.<String, Object>of("standaloneVirtualSimulation", true, "seed", 42));
            report.set("prepare", j.mapper.valueToTree(prepared));
            assertEquals("PREPARED", prepared.state());
            assertEquals(RuntimeContextRegistry.PROTOCOL, prepared.protocolVersion());
            assertEquals(List.of("START", "PAUSE", "RESUME", "STOP", "DEVICE_COMMAND"), prepared.capabilities());
            String ref = prepared.runtimeRef();
            String generation = prepared.runtimeGeneration();
            UUID.fromString(ref); UUID.fromString(generation);
            until(() -> r.channel(ref).heartbeatNanos != null, "No real heartbeat received");
            report.set("contextAfterHeartbeat", app.context(ref()));
            var expected = new TreeSet<String>();
            for (var agent : prepared.latestFrame().path("agents")) {
                String code = agent.path("deviceCode").asText();
                assertTrue(code.matches("[A-Za-z0-9_.:-]{1,96}"),
                        "Real adapter frame missing valid deviceCode; legacy code=" + agent.path("code").asText());
                expected.add(code);
            }
            assertFalse(expected.isEmpty(), "Empty authoritative device membership");
            var commandIds = new HashSet<String>();
            int version = 0;
            for (String action : List.of("START", "PAUSE", "RESUME", "STOP")) {
                manager.action(990031L, action);
                var executions = s.locked(() -> s.query(
                        "SELECT data_json FROM voice_execution WHERE runtime_ref=?", ref));
                var e = executions.stream().filter(x -> action.equals(x.path("action").asText())).findFirst().orElseThrow();
                String id = e.path("executionId").asText();
                assertTrue(commandIds.add(e.path("commandId").asText()));
                worker.tick();
                until(() -> "SUCCEEDED".equals(app.execution(id).path("state").asText()),
                        action + " did not reach SUCCEEDED: " + app.execution(id));
                var actual = app.execution(id);
                steps.add(actual);
                assertEquals("SUCCESS", actual.path("outcome").asText());
                assertEquals(generation, actual.path("runtimeGeneration").asText());
                var receipts = s.locked(() -> s.query(
                        "SELECT data_json FROM voice_command_event WHERE execution_id=? ORDER BY event_sequence", id));
                var success = receipts.stream().filter(x -> "SUCCEEDED".equals(x.path("status").asText())).findFirst().orElseThrow();
                assertTrue(receipts.stream().anyMatch(x -> "ACCEPTED".equals(x.path("status").asText())));
                assertEquals(e.path("commandId"), success.path("commandId"));
                assertEquals(ref, success.path("runtimeRef").asText());
                assertEquals(generation, success.path("runtimeGeneration").asText());
                assertEquals(j.mapper.valueToTree(expected), success.path("affectedDeviceCodes"));
                assertEquals(++version, success.path("stateVersion").asInt());
                String target = action.equals("PAUSE") ? "PAUSED" : action.equals("STOP") ? "STOPPED" : "RUNNING";
                assertEquals(target, success.path("runtimeState").asText());
                assertEquals(target, app.context(ref).path("state").asText());
                report.set(action + "Receipts", j.mapper.valueToTree(receipts));
                if (action.equals("PAUSE")) {
                    long previous = s.locked(() -> s.get("voice_runtime_context", ref)).path("_heartbeatSequence").asLong();
                    until(() -> s.locked(() -> s.get("voice_runtime_context", ref)).path("_heartbeatSequence").asLong() > previous,
                            "No heartbeat while paused");
                }
            }
            var handles=(Map<?,?>)ReflectionTestUtils.getField(manager,"handles");
            assertNotNull(handles);
            var handle=handles.get(990031L);
            Process process=(Process)ReflectionTestUtils.getField(handle,"process");
            assertNotNull(process);
            assertTrue(process.waitFor(10,java.util.concurrent.TimeUnit.SECONDS),"Runner did not exit after STOP receipt");
            var stderrDone=(java.util.concurrent.CountDownLatch)ReflectionTestUtils.getField(handle,"stderrDone");
            assertNotNull(stderrDone);stderrDone.await(2,java.util.concurrent.TimeUnit.SECONDS);
            report.put("runnerPid",process.pid()).put("runnerExitCode",process.exitValue());
            var stderrTail=ReflectionTestUtils.getField(handle,"stderrTail");
            report.set("stderrTail",j.mapper.valueToTree(stderrTail));
            assertEquals(0,process.exitValue(),"STOP succeeded but Runner exit was nonzero; inspect evidence stderrTail");
            report.put("result", "PASS");
        } catch (Exception | AssertionError failure) {
            report.put("result", "FAIL").put("failure", failure.toString());
            throw failure;
        } finally {
            manager.close();
            var output = Path.of("target/p0-real-runner-report.json");
            Files.createDirectories(output.getParent());
            j.mapper.writerWithDefaultPrettyPrinter().writeValue(output.toFile(), report);
        }
    }
    @Test void legacyPrepareAndManualFourActionsRemainAvailableWhenFeatureOff() throws Exception {
        settings.setEnabled(false);
        var businessRuns=mock(MissionRunRepository.class);
        var manager=new AlgorithmRuntimeManager(j.mapper,businessRuns,mock(AlgorithmCatalogService.class),System.getenv().getOrDefault("PYTHON_COMMAND", "python"),System.getenv("P0_REAL_RUNNER"));
        ReflectionTestUtils.setField(manager,"voiceBridge",new VoiceRuntimeBridge(r,app,worker,j));
        try {
            var prepared=manager.prepare(990071L,"GB_SFLA_CS",Map.of("standaloneVirtualSimulation",true,"seed",42));
            assertNull(prepared.runtimeRef());assertNull(prepared.runtimeGeneration());assertNull(prepared.protocolVersion());assertTrue(prepared.capabilities().isEmpty());
            var handles=(Map<?,?>)ReflectionTestUtils.getField(manager,"handles");var handle=handles.get(990071L);var process=(Process)ReflectionTestUtils.getField(handle,"process");assertNotNull(process);assertTrue(process.isAlive());
            login("bob");error("RUNTIME_BUSY",()->manager.prepare(990072L,"GB_SFLA_CS",Map.of("standaloneVirtualSimulation",true)));
            assertTrue(process.isAlive());assertEquals(1,handles.size());login("alice");
            for(String action:List.of("START","PAUSE","RESUME","STOP")) {
                manager.action(990071L,action);String expected=action.equals("PAUSE")?"PAUSED":action.equals("STOP")?"STOPPED":"RUNNING";
                until(()->manager.status(990071L).state().equals(expected),"Legacy action failed: "+action);
            }
            assertTrue(process.waitFor(10,java.util.concurrent.TimeUnit.SECONDS));assertEquals(0,process.exitValue());
            assertEquals(0,count("voice_execution"));verifyNoInteractions(businessRuns);
        } finally {login("alice");manager.close();}
    }
    @Test void businessMissionRunNeverRegistersAsIndependentVoiceRuntime() throws Exception {
        var runs=mock(MissionRunRepository.class);
        var business=mock(com.uavusv.platform.module.mission.entity.MissionRun.class);
        when(business.getAlgorithmCode()).thenReturn("GB_SFLA_CS");when(runs.findById(990091L)).thenReturn(Optional.of(business));
        var manager=new AlgorithmRuntimeManager(j.mapper,runs,mock(AlgorithmCatalogService.class),System.getenv().getOrDefault("PYTHON_COMMAND", "python"),System.getenv("P0_REAL_RUNNER"));
        ReflectionTestUtils.setField(manager,"voiceBridge",new VoiceRuntimeBridge(r,app,worker,j));
        int contexts=count("voice_runtime_context");
        try {
            var prepared=manager.prepare(990091L,"GB_SFLA_CS",Map.of("seed",42));
            assertNotNull(prepared.latestFrame());assertNull(prepared.runtimeRef());assertNull(prepared.runtimeGeneration());assertNull(prepared.protocolVersion());assertTrue(prepared.capabilities().isEmpty());
            assertEquals(contexts,count("voice_runtime_context"));
            assertTrue(app.contexts().stream().noneMatch(x->x.path("algorithmRunId").asText().equals("990091")));
            error("RESOURCE_NOT_FOUND",()->app.createProposal(VoiceJson.uuid(),j.object().put("runtimeRef",VoiceJson.uuid()).put("runtimeGeneration",VoiceJson.uuid()).put("expectedContextVersion",1).put("intent","MISSION_START")));
            assertEquals(0,count("voice_execution"));verify(runs).findById(990091L);
        } finally {manager.close();}
    }
    @Test void cleanupAfterFinalStopAllowsActualRunnerToExitNormally() throws Exception {
        var manager=new AlgorithmRuntimeManager(j.mapper,mock(MissionRunRepository.class),mock(AlgorithmCatalogService.class),System.getenv().getOrDefault("PYTHON_COMMAND", "python"),repositoryFile("backend/src/test/resources/voicecontrol/delayed_exit_runner.py").toString());
        ReflectionTestUtils.setField(manager,"voiceBridge",new VoiceRuntimeBridge(r,app,worker,j));
        try {
            var prepared=manager.prepare(990092L,"GB_SFLA_CS",Map.of("standaloneVirtualSimulation",true,"seed",42));
            String ref=prepared.runtimeRef();until(()->r.channel(ref).heartbeatNanos!=null,"heartbeat absent");
            Object handle=((Map<?,?>)ReflectionTestUtils.getField(manager,"handles")).get(990092L);Process process=(Process)ReflectionTestUtils.getField(handle,"process");
            var e=execution(app.manual(ref,"STOP"));worker.tick();until(()->app.execution(e.path("executionId").asText()).path("state").asText().equals("SUCCEEDED"),"STOP result absent");
            assertTrue(process.isAlive(),"Controlled teardown window not reached");
            manager.close();assertTrue(process.waitFor(5,java.util.concurrent.TimeUnit.SECONDS));
            Files.writeString(Path.of("target/stop-cleanup-exit.json"),j.object().put("pid",process.pid()).put("exitCode",process.exitValue()).put("executionId",e.path("executionId").asText()).put("outcome",app.execution(e.path("executionId").asText()).path("outcome").asText()).toPrettyString());
            assertEquals(0,process.exitValue(),"Cleanup killed a Runner after its successful final STOP receipt");
            assertEquals("SUCCEEDED",app.execution(e.path("executionId").asText()).path("state").asText());
        } finally {manager.close();}
    }
    @Test void ownerlessLegacyProcessCannotBeClaimedByPrepare() throws Exception {
        var manager=new AlgorithmRuntimeManager(j.mapper,mock(MissionRunRepository.class),mock(AlgorithmCatalogService.class),System.getenv().getOrDefault("PYTHON_COMMAND", "python"),System.getenv("P0_REAL_RUNNER"));
        ReflectionTestUtils.setField(manager,"voiceBridge",new VoiceRuntimeBridge(r,app,worker,j));
        try {
            var config=Map.<String,Object>of("standaloneVirtualSimulation",true,"seed",42);
            var prepared=manager.prepare(990093L,"GB_SFLA_CS",config);
            Object handle=((Map<?,?>)ReflectionTestUtils.getField(manager,"handles")).get(990093L);Process process=(Process)ReflectionTestUtils.getField(handle,"process");
            org.springframework.test.util.ReflectionTestUtils.setField(handle,"voiceContext",null);
            s.locked(()->{var stored=s.get("voice_runtime_context",prepared.runtimeRef());stored.remove("_owner");s.save("voice_runtime_context",stored);return null;});
            int contexts=count("voice_runtime_context");
            error("RUNTIME_BUSY",()->manager.prepare(990093L,"GB_SFLA_CS",config));assertTrue(process.isAlive());assertEquals(contexts,count("voice_runtime_context"));
            error("RESOURCE_NOT_FOUND",()->app.context(prepared.runtimeRef()));assertFalse(s.get("voice_runtime_context",prepared.runtimeRef()).has("_owner"));
            assertNull(manager.status(990093L).runtimeRef());assertEquals(0,count("voice_execution"));
        } finally {manager.close();}
    }
}
