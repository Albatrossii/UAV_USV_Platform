package com.uavusv.platform.module.mission;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.common.exception.BusinessException;
import com.uavusv.platform.common.exception.ErrorCode;
import com.uavusv.platform.module.mission.dto.response.AlgorithmRuntimeStatusResponse;
import com.uavusv.platform.module.mission.entity.AlgorithmDefinition;
import com.uavusv.platform.module.mission.entity.MissionRun;
import com.uavusv.platform.module.mission.entity.MissionStage;
import com.uavusv.platform.module.mission.repository.MissionRunRepository;
import com.uavusv.platform.module.mission.service.AlgorithmCatalogService;
import com.uavusv.platform.module.mission.service.AlgorithmRuntimeManager;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class AlgorithmRuntimeManagerTests {

    @Test
    void shouldRunCaptureAdapterAndExposeAuthoritativeFrame() throws Exception {
        long runId = 91001L;
        MissionRunRepository runRepository = mock(MissionRunRepository.class);
        AlgorithmCatalogService catalogService = mock(AlgorithmCatalogService.class);
        when(runRepository.findById(runId)).thenReturn(Optional.of(run("GB_SFLA_CS")));
        when(catalogService.requireEnabled("GB_SFLA_CS")).thenReturn(mock(AlgorithmDefinition.class));
        AlgorithmRuntimeManager manager = manager(runRepository, catalogService);
        try {
            AlgorithmRuntimeStatusResponse prepared = manager.prepare(
                    runId,
                    "GB_SFLA_CS",
                    Map.of("seed", 42, "targetBehavior", "STATIC")
            );
            assertEquals("PREPARED", prepared.state());
            assertEquals(null, prepared.runtimeRef());
            assertEquals(null, prepared.runtimeGeneration());
            assertEquals(null, prepared.protocolVersion());
            assertTrue(prepared.capabilities().isEmpty());
            manager.action(runId, "START");

            JsonNode frame = null;
            for (int attempt = 0; attempt < 60 && frame == null; attempt++) {
                Thread.sleep(100);
                frame = manager.latestFrame(runId, 0);
            }
            assertNotNull(frame);
            assertEquals("GB_SFLA_CS", frame.path("algorithmCode").asText());
            assertEquals(6, frame.path("agents").size());
            // A 3+3 active-capture run intentionally owns one moving enemy.
            // Additional enemies are introduced only by the adaptive scale thresholds.
            assertEquals(1, frame.path("targets").size());
            assertFalse(manager.framesAfter(runId, 0).isEmpty());
            long observedSequence = frame.path("sequence").asLong();
            assertTrue(manager.framesAfter(runId, observedSequence).stream()
                    .allMatch(bufferedFrame -> bufferedFrame.path("sequence").asLong() > observedSequence));

            long sequenceBeforeRepeatedPrepare = observedSequence;
            AlgorithmRuntimeStatusResponse repeatedPrepare = manager.prepare(
                    runId,
                    "GB_SFLA_CS",
                    Map.of("seed", 42, "targetBehavior", "STATIC")
            );
            assertEquals("RUNNING", repeatedPrepare.state());
            assertTrue(repeatedPrepare.latestSequence() >= sequenceBeforeRepeatedPrepare);
        } finally {
            try { manager.action(runId, "CANCEL"); } catch (RuntimeException ignored) {}
            manager.close();
        }
    }

    @Test
    void shouldRejectUnknownRunBeforeStartingAlgorithm() {
        MissionRunRepository runRepository = mock(MissionRunRepository.class);
        AlgorithmCatalogService catalogService = mock(AlgorithmCatalogService.class);
        when(runRepository.findById(404L)).thenReturn(Optional.empty());
        AlgorithmRuntimeManager manager = manager(runRepository, catalogService);

        BusinessException exception = assertThrows(
                BusinessException.class,
                () -> manager.prepare(404L, "GB_SFLA_CS", Map.of())
        );

        assertEquals(ErrorCode.NOT_FOUND, exception.getErrorCode());
        assertEquals("任务运行批次不存在：404", exception.getMessage());
        verifyNoInteractions(catalogService);
    }

    @Test
    void shouldRejectAlgorithmThatDoesNotMatchRunSnapshot() {
        MissionRunRepository runRepository = mock(MissionRunRepository.class);
        AlgorithmCatalogService catalogService = mock(AlgorithmCatalogService.class);
        when(runRepository.findById(15L)).thenReturn(Optional.of(run("GB_SFLA_CS")));
        AlgorithmRuntimeManager manager = manager(runRepository, catalogService);

        BusinessException exception = assertThrows(
                BusinessException.class,
                () -> manager.prepare(15L, "ESCORT_GUARD", Map.of())
        );

        assertEquals(ErrorCode.BAD_REQUEST, exception.getErrorCode());
        assertEquals(
                "算法代码与任务运行快照不一致：请求=ESCORT_GUARD，运行快照=GB_SFLA_CS",
                exception.getMessage()
        );
        verifyNoInteractions(catalogService);
    }

    @Test
    void shouldRejectDisabledCatalogAlgorithm() {
        MissionRunRepository runRepository = mock(MissionRunRepository.class);
        AlgorithmCatalogService catalogService = mock(AlgorithmCatalogService.class);
        when(runRepository.findById(16L)).thenReturn(Optional.of(run("GB_SFLA_CS")));
        when(catalogService.requireEnabled("GB_SFLA_CS")).thenThrow(
                new BusinessException(ErrorCode.BAD_REQUEST, "所选算法已停用：GB_SFLA_CS")
        );
        AlgorithmRuntimeManager manager = manager(runRepository, catalogService);

        BusinessException exception = assertThrows(
                BusinessException.class,
                () -> manager.prepare(16L, "GB_SFLA_CS", Map.of())
        );

        assertEquals(ErrorCode.BAD_REQUEST, exception.getErrorCode());
        assertEquals("所选算法已停用：GB_SFLA_CS", exception.getMessage());
    }

    @Test
    void shouldReplacePreviousStandaloneSimulationWhenRunIdChanges() throws Exception {
        long firstRunId = 92001L;
        long secondRunId = 92002L;
        MissionRunRepository runRepository = mock(MissionRunRepository.class);
        AlgorithmCatalogService catalogService = mock(AlgorithmCatalogService.class);
        when(catalogService.requireEnabled("GB_SFLA_CS")).thenReturn(mock(AlgorithmDefinition.class));
        AlgorithmRuntimeManager manager = manager(runRepository, catalogService);
        Map<String, Object> config = Map.of(
                "standaloneVirtualSimulation", true,
                "seed", 42,
                "targetBehavior", "STATIC"
        );
        try {
            assertEquals("PREPARED", manager.prepare(firstRunId, "GB_SFLA_CS", config).state());
            assertEquals("PREPARED", manager.prepare(secondRunId, "GB_SFLA_CS", config).state());

            BusinessException exception = assertThrows(
                    BusinessException.class,
                    () -> manager.status(firstRunId)
            );
            assertEquals(ErrorCode.NOT_FOUND, exception.getErrorCode());
            assertNotNull(manager.status(secondRunId).latestFrame());
        } finally {
            manager.close();
        }
    }

    @Test
    void unityNativeResponseKeepsLegacyFieldsAndEmptyVoiceMetadata() {
        var manager = manager(mock(MissionRunRepository.class), mock(AlgorithmCatalogService.class));
        var response = manager.prepare(93001L, "UNITY_SIMPLE_ENCIRCLEMENT",
                Map.of("standaloneVirtualSimulation", true));
        assertEquals("UNITY_NATIVE", response.state());
        var json = new ObjectMapper().valueToTree(response);
        assertEquals(10, json.size());
        assertTrue(json.get("runtimeRef").isNull());
        assertTrue(json.get("runtimeGeneration").isNull());
        assertTrue(json.get("protocolVersion").isNull());
        assertTrue(json.get("capabilities").isArray());
        assertTrue(json.get("capabilities").isEmpty());
        manager.close();
    }

    private AlgorithmRuntimeManager manager(
            MissionRunRepository runRepository,
            AlgorithmCatalogService catalogService
    ) {
        Path runner = resolveRepositoryFile(
                "algorithm-service/runner.py",
                "../algorithm-service/runner.py",
                "../../algorithm-service/runner.py"
        );
        return new AlgorithmRuntimeManager(
                new ObjectMapper(),
                runRepository,
                catalogService,
                resolvePythonExecutable(),
                runner.toString()
        );
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {"ESCORT_GUARD", "GB_SFLA_CS", "ESCORT_GUARD_SINGLE_DEVICE", "GB_SFLA_CS_SINGLE_DEVICE"})
    void shouldApplySingleDeviceCommandOnlyAfterAuthoritativeFrameReceipt(String algorithm) throws Exception {
        long runId = 92003L;
        MissionRunRepository runRepository = mock(MissionRunRepository.class);
        AlgorithmCatalogService catalogService = mock(AlgorithmCatalogService.class);
        when(catalogService.requireEnabled("ESCORT_GUARD_SINGLE_DEVICE"))
                .thenReturn(mock(AlgorithmDefinition.class));
        AlgorithmRuntimeManager manager = manager(runRepository, catalogService);
        try {
            manager.prepare(
                    runId,
                    algorithm,
                    Map.of("standaloneVirtualSimulation", true, "seed", 42));
            assertTrue(manager.isStandaloneSingleDeviceRun(runId));
            assertThrows(com.uavusv.platform.common.exception.BusinessException.class,
                    () -> manager.controlDevice(runId, "UAV-001", "UAV_HOVER"));
            manager.action(runId, "START");

            AlgorithmRuntimeStatusResponse afterCommand =
                    manager.controlDevice(runId, "uav_001", "UAV_HOVER");

            assertNotNull(afterCommand.latestFrame());
            assertEquals(algorithm, afterCommand.latestFrame().path("algorithmCode").asText());
            JsonNode selected = afterCommand.latestFrame().path("agents").get(0);
            assertEquals("UAV-001", selected.path("deviceCode").asText());
            assertEquals("HOLDING", selected.path("status").asText());
            assertEquals("OPERATOR_CONTROLLED", selected.path("role").asText());
        } finally {
            manager.close();
        }
    }

    private Path resolveRepositoryFile(String... candidates) {
        for (String candidate : candidates) {
            Path path = Path.of(candidate).toAbsolutePath().normalize();
            if (Files.isRegularFile(path)) return path;
        }
        throw new IllegalStateException("Could not locate repository file from working directory: "
                + Path.of("").toAbsolutePath());
    }

    private String resolvePythonExecutable() {
        String configured = System.getenv("ALGORITHM_PYTHON");
        if (configured != null && !configured.isBlank()) {
            return configured;
        }
        String locator = System.getProperty("os.name", "").toLowerCase().contains("win")
                ? "where.exe"
                : "which";
        try {
            Process process = new ProcessBuilder(locator, "python").redirectErrorStream(true).start();
            try (var reader = process.inputReader()) {
                String candidate;
                while ((candidate = reader.readLine()) != null) {
                    Path path = Path.of(candidate.trim());
                    if (Files.isRegularFile(path) && !path.toString().contains("WindowsApps")) {
                        return path.toString();
                    }
                }
            }
        } catch (IOException ignored) {
            // Portable fallback for environments where the locator command is
            // unavailable but python itself is executable from PATH.
        }
        return "python";
    }

    private MissionRun run(String algorithmCode) {
        return new MissionRun(
                1L,
                null,
                1,
                MissionStage.PREPARE,
                "test",
                "mission-unity-test",
                algorithmCode,
                "2026.07.27"
        );
    }
}
