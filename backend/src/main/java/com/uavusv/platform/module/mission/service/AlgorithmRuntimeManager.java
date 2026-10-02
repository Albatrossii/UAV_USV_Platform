package com.uavusv.platform.module.mission.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.voicecontrol.VoiceRuntimeBridge;
import com.uavusv.platform.module.voicecontrol.VoiceLines;
import com.uavusv.platform.module.voicecontrol.VoiceFailure;
import org.springframework.beans.factory.annotation.Autowired;
import com.uavusv.platform.common.exception.BusinessException;
import com.uavusv.platform.common.exception.ErrorCode;
import com.uavusv.platform.module.mission.dto.response.AlgorithmRuntimeStatusResponse;
import com.uavusv.platform.module.mission.entity.MissionRun;
import com.uavusv.platform.module.mission.repository.MissionRunRepository;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeoutException;

@Service
public class AlgorithmRuntimeManager {
    private static final Logger log = LoggerFactory.getLogger(AlgorithmRuntimeManager.class);
    private static final int STDERR_TAIL_LINES = 80;
    private final ObjectMapper objectMapper;
    private final MissionRunRepository missionRunRepository;
    private final AlgorithmCatalogService algorithmCatalogService;
    private final String pythonCommand;
    private final Path runnerPath;
    private final Map<Long, RuntimeHandle> handles = new ConcurrentHashMap<>();
    private static final List<String> EXTERNAL_ALGORITHMS = List.of(
            "GB_SFLA_CS", "ESCORT_GUARD",
            "GB_SFLA_CS_SINGLE_DEVICE", "ESCORT_GUARD_SINGLE_DEVICE"
    );
    private static final List<String> SINGLE_DEVICE_COMMANDS = List.of(
            "UAV_HOVER", "UAV_RETURN", "UAV_RESUME", "UAV_LAND",
            "USV_HOLD", "USV_RETURN", "USV_RESUME", "USV_STOP"
    );
    @Autowired(required = false)
    private VoiceRuntimeBridge voiceBridge;

    public AlgorithmRuntimeManager(
            ObjectMapper objectMapper,
            MissionRunRepository missionRunRepository,
            AlgorithmCatalogService algorithmCatalogService,
            @Value("${app.algorithm.python-command:python}") String pythonCommand,
            @Value("${app.algorithm.runner-path:../algorithm-service/runner.py}") String runnerPath
    ) {
        this.objectMapper = objectMapper;
        this.missionRunRepository = missionRunRepository;
        this.algorithmCatalogService = algorithmCatalogService;
        this.pythonCommand = pythonCommand;
        this.runnerPath = resolveRunnerPath(runnerPath);
    }

    public synchronized AlgorithmRuntimeStatusResponse prepare(Long runId, String algorithmCode, Map<String, Object> config) {
        log.info("Algorithm prepare entered: runId={} algorithmCode={} runnerPath={} pythonCommand={}",
                runId, algorithmCode, runnerPath, pythonCommand);
        Map<String, Object> runtimeConfig = config == null ? Map.of() : config;
        boolean standaloneVirtualSimulation = Boolean.TRUE.equals(
                runtimeConfig.get("standaloneVirtualSimulation")
        ) || "true".equalsIgnoreCase(
                String.valueOf(runtimeConfig.get("standaloneVirtualSimulation"))
        );
        if (!standaloneVirtualSimulation) {
            MissionRun run = requireMatchingRun(runId, algorithmCode);
            algorithmCatalogService.requireEnabled(run.getAlgorithmCode());
        } else {
            algorithmCatalogService.requireEnabled(baseAlgorithmCode(algorithmCode));
        }
        if ("UNITY_SIMPLE_ENCIRCLEMENT".equals(algorithmCode)) {
            return new AlgorithmRuntimeStatusResponse(runId, algorithmCode, "UNITY_NATIVE", 0, null, null);
        }
        if (!EXTERNAL_ALGORITHMS.contains(algorithmCode)) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "不支持的外部算法：" + algorithmCode);
        }
        RuntimeHandle existing = handles.get(runId);
        if (voiceBridge != null) {
            if (existing != null && existing.standaloneVirtualSimulation) voiceBridge.guard(existing.voiceContext);
            if (standaloneVirtualSimulation) {
                for (RuntimeHandle active : handles.values()) {
                    if (active.standaloneVirtualSimulation && active.process.isAlive()) voiceBridge.guard(active.voiceContext);
                }
            }
        }
        if (existing != null
                && existing.process.isAlive()
                && algorithmCode.equals(existing.algorithmCode)
                && existing.error.get() == null
                && (voiceBridge == null || !voiceBridge.v1(existing.voiceContext)
                    || existing.voiceContext.path("_configHash").asText().equals(voiceBridge.configHash(objectMapper.valueToTree(runtimeConfig))))) {
            return status(runId);
        }
        stopExisting(runId, "prepare replacing existing runtime");
        if (standaloneVirtualSimulation) {
            stopOtherStandaloneSimulations(runId);
        }
        if (!Files.isRegularFile(runnerPath)) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "算法运行器不存在：" + runnerPath);
        }
        Path configFile = null;
        ObjectNode voiceContext = standaloneVirtualSimulation && voiceBridge != null
                ? voiceBridge.register(runId, algorithmCode, objectMapper.valueToTree(runtimeConfig)) : null;
        try {
            configFile = Files.createTempFile("uav-usv-algorithm-", ".json");
            Files.write(configFile, objectMapper.writeValueAsBytes(runtimeConfig));
            List<String> command = new ArrayList<>();
            command.add(pythonCommand);
            command.add(runnerPath.toString());
            command.add("--algorithm");
            command.add(algorithmCode);
            command.add("--run-id");
            command.add(String.valueOf(runId));
            command.add("--config-file");
            command.add(configFile.toString());
            command.add("--fps");
            command.add("10");
            if (voiceBridge != null && voiceBridge.v1(voiceContext)) {
                command.add("--command-protocol"); command.add("v1");
                command.add("--runtime-ref"); command.add(voiceContext.path("runtimeRef").asText());
                command.add("--runtime-generation"); command.add(voiceContext.path("runtimeGeneration").asText());
            }
            ProcessBuilder builder = new ProcessBuilder(command);
            builder.directory(runnerPath.getParent().toFile());
            builder.environment().put("PYTHONUTF8", "1");
            builder.environment().put("PYTHONUNBUFFERED", "1");
            builder.environment().put("MPLCONFIGDIR", Path.of(System.getProperty("java.io.tmpdir"), "uav-usv-matplotlib").toString());
            Process process = builder.start();
            logPythonExecutableAndVersion(runId, process);
            log.info("Algorithm process started: runId={} algorithmCode={} pid={}", runId, algorithmCode, process.pid());
            RuntimeHandle handle = new RuntimeHandle(runId, algorithmCode, standaloneVirtualSimulation, process,
                    new BufferedWriter(new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8)));
            handle.voiceContext = voiceContext;
            if (voiceContext != null) voiceBridge.attach(voiceContext,
                    event -> send(handle, objectMapper.convertValue(event, new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {})),
                    process::isAlive);
            handles.put(runId, handle);
            log.info("Algorithm handle stored: runId={} pid={} handlesSize={}", runId, process.pid(), handles.size());
            startReaders(handle);
            // The first Python start may build the Matplotlib font cache and
            // import the vendor simulation, so 12 seconds is too short on a
            // cold Windows environment.
            if (!handle.ready.await(60, TimeUnit.SECONDS)) {
                stopExisting(runId, "runner ready timeout");
                throw new BusinessException(ErrorCode.BAD_REQUEST, "算法运行器启动超时");
            }
            if (!handle.initialFrameReady.await(60, TimeUnit.SECONDS)) {
                stopExisting(runId, "initial frame timeout");
                throw new BusinessException(ErrorCode.BAD_REQUEST, "Algorithm initial frame timeout");
            }
            if (handle.error.get() != null) {
                String runtimeError = handle.error.get();
                stopExisting(runId, "runtime error: " + runtimeError);
                throw new BusinessException(ErrorCode.BAD_REQUEST, "算法运行器启动失败：" + handle.error.get());
            }
            return status(runId);
        } catch (IOException exception) {
            if (voiceContext != null) voiceBridge.ended(voiceContext);
            throw new BusinessException(ErrorCode.BAD_REQUEST, "无法启动算法运行器：" + exception.getMessage());
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new BusinessException(ErrorCode.BAD_REQUEST, "等待算法运行器时被中断");
        } finally {
            if (configFile != null) {
                try {
                    Files.deleteIfExists(configFile);
                } catch (IOException ignored) {
                    // The temporary file is only needed during process startup.
                }
            }
        }
    }

    public AlgorithmRuntimeStatusResponse action(Long runId, String action) {
        RuntimeHandle handle = requireHandle(runId);
        String normalized = action.toUpperCase();
        if (!List.of("START", "PAUSE", "RESUME", "CANCEL", "STOP").contains(normalized)) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "不支持的算法运行指令：" + action);
        }
        if (voiceBridge != null && voiceBridge.v1(handle.voiceContext)) {
            voiceBridge.manual(handle.voiceContext, normalized);
            return status(runId);
        }
        if (voiceBridge != null && handle.voiceContext != null) voiceBridge.guard(handle.voiceContext);
        send(handle, Map.of("action", normalized));
        if ("CANCEL".equals(normalized)) handle.state.set("CANCELLED");
        if ("STOP".equals(normalized)) handle.state.set("STOPPED");
        return status(runId);
    }

    public boolean isStandaloneSingleDeviceRun(Long runId) {
        RuntimeHandle handle = runId == null ? null : handles.get(runId);
        return handle != null
                && handle.standaloneVirtualSimulation
                && handle.process.isAlive()
                && handle.algorithmCode.endsWith("_SINGLE_DEVICE");
    }

    public AlgorithmRuntimeStatusResponse controlDevice(
            Long runId, String deviceCode, String commandType) {
        RuntimeHandle handle = requireHandle(runId);
        if (!handle.standaloneVirtualSimulation
                || !handle.algorithmCode.endsWith("_SINGLE_DEVICE")) {
            throw new BusinessException(
                    ErrorCode.BAD_REQUEST, "当前算法运行未启用单设备控制");
        }
        String code = deviceCode == null
                ? "" : deviceCode.trim().toUpperCase().replace('_', '-');
        String command = commandType == null ? "" : commandType.trim().toUpperCase();
        if (!SINGLE_DEVICE_COMMANDS.contains(command)) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "不支持的单设备控制指令：" + command);
        }
        if ((command.startsWith("UAV_") && !code.startsWith("UAV-"))
                || (command.startsWith("USV_") && !code.startsWith("USV-"))) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "控制指令与目标设备类型不匹配");
        }
        JsonNode frame = handle.latestFrame.get();
        boolean known = frame != null && frame.path("agents").isArray();
        if (known) {
            known = false;
            for (JsonNode agent : frame.path("agents")) {
                String frameCode = agent.path("deviceCode").asText(agent.path("code").asText());
                if (code.equalsIgnoreCase(frameCode)) {
                    known = true;
                    break;
                }
            }
        }
        if (!known) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "算法运行中不存在设备：" + code);
        }
        if (voiceBridge != null && handle.voiceContext != null) {
            voiceBridge.guard(handle.voiceContext);
        }
        String requestId = UUID.randomUUID().toString();
        PendingDeviceCommand pending = new PendingDeviceCommand(code, command);
        handle.pendingDeviceCommands.put(requestId, pending);
        try {
            send(handle, Map.of(
                    "kind", "DEVICE_COMMAND",
                    "action", "DEVICE_COMMAND",
                    "requestId", requestId,
                    "deviceCode", code,
                    "commandType", command
            ));
            JsonNode receipt = pending.receipt.get(8, TimeUnit.SECONDS);
            if (!receipt.path("success").asBoolean()) {
                throw new BusinessException(
                        ErrorCode.BAD_REQUEST,
                        receipt.path("detail").asText("算法拒绝单设备指令"));
            }
        } catch (TimeoutException exception) {
            throw new DeviceCommandTimeoutException(
                    "单设备命令已发送，但未在 8 秒内收到权威帧回执；结果未知，不应自动重发");
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new DeviceCommandTimeoutException("等待单设备命令回执时被中断；结果未知");
        } catch (ExecutionException exception) {
            if (exception.getCause() instanceof DeviceCommandTimeoutException timeout) throw timeout;
            throw new BusinessException(
                    ErrorCode.BAD_REQUEST,
                    "单设备命令回执校验失败：" + exception.getCause().getMessage());
        } finally {
            handle.pendingDeviceCommands.remove(requestId, pending);
        }
        return status(runId);
    }

    public static final class DeviceCommandTimeoutException extends RuntimeException {
        public DeviceCommandTimeoutException(String message) { super(message); }
    }

    public AlgorithmRuntimeStatusResponse placeThreat(Long runId, double x, double y) {
        RuntimeHandle handle = requireHandle(runId);
        if (voiceBridge != null && voiceBridge.v1(handle.voiceContext)) throw new VoiceFailure(422, "UNSUPPORTED_CAPABILITY");
        if (voiceBridge != null && handle.voiceContext != null) voiceBridge.guard(handle.voiceContext);
        if (!"ESCORT_GUARD".equals(baseAlgorithmCode(handle.algorithmCode))) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "只有护航守卫算法支持动态放置威胁目标");
        }
        send(handle, Map.of("action", "PLACE_THREAT", "x", x, "y", y));
        return status(runId);
    }

    public AlgorithmRuntimeStatusResponse activateCapture(Long runId, String threatCode) {
        RuntimeHandle handle = requireHandle(runId);
        if (voiceBridge != null && voiceBridge.v1(handle.voiceContext)) throw new VoiceFailure(422, "UNSUPPORTED_CAPABILITY");
        if (voiceBridge != null && handle.voiceContext != null) voiceBridge.guard(handle.voiceContext);
        if (!"ESCORT_GUARD".equals(baseAlgorithmCode(handle.algorithmCode))) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "只有护航守卫算法支持主动围捕");
        }
        Map<String, Object> command = new java.util.HashMap<>();
        command.put("action", "ACTIVE_CAPTURE");
        if (threatCode != null && !threatCode.isBlank()) {
            command.put("threatCode", threatCode.trim().toUpperCase());
        }
        send(handle, command);
        return status(runId);
    }

    public AlgorithmRuntimeStatusResponse status(Long runId) {
        RuntimeHandle handle = requireHandle(runId);
        ObjectNode snapshot = voiceBridge == null ? null : voiceBridge.statusSnapshot(handle.voiceContext);
        boolean v1 = voiceBridge != null && voiceBridge.v1(snapshot);
        boolean negotiated = v1 && snapshot.path("_ready").asBoolean();
        List<String> capabilities = new ArrayList<>();
        if (negotiated) snapshot.path("capabilities").forEach(value -> capabilities.add(value.asText()));
        return new AlgorithmRuntimeStatusResponse(runId, handle.algorithmCode,
                v1 ? snapshot.path("state").asText() : handle.state.get(),
                handle.latestSequence.get(), handle.error.get(), handle.latestFrame.get(),
                negotiated ? snapshot.path("runtimeRef").asText() : null,
                negotiated ? snapshot.path("runtimeGeneration").asText() : null,
                negotiated ? snapshot.path("protocolVersion").asText() : null,
                capabilities);
    }

    public JsonNode latestFrame(Long runId, long afterSequence) {
        RuntimeHandle handle = requireHandle(runId);
        if (voiceBridge != null) voiceBridge.read(handle.voiceContext);
        return handle.latestSequence.get() > afterSequence ? handle.latestFrame.get() : null;
    }

    public List<JsonNode> framesAfter(Long runId, long afterSequence) {
        RuntimeHandle handle = requireHandle(runId);
        if (voiceBridge != null) voiceBridge.read(handle.voiceContext);
        List<JsonNode> frames = new ArrayList<>();
        synchronized (handle.frameBuffer) {
            for (JsonNode frame : handle.frameBuffer) {
                if (frame.path("sequence").asLong() > afterSequence) frames.add(frame);
            }
        }
        return frames;
    }

    private MissionRun requireMatchingRun(Long runId, String algorithmCode) {
        if (runId == null) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "任务运行批次编号不能为空");
        }
        MissionRun run = missionRunRepository.findById(runId)
                .orElseThrow(() -> new BusinessException(
                        ErrorCode.NOT_FOUND,
                        "任务运行批次不存在：" + runId
                ));
        if (algorithmCode == null || algorithmCode.isBlank()) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "算法代码不能为空");
        }
        String snapshotAlgorithmCode = run.getAlgorithmCode();
        if (!algorithmCode.equals(snapshotAlgorithmCode)) {
            throw new BusinessException(
                    ErrorCode.BAD_REQUEST,
                    "算法代码与任务运行快照不一致：请求=" + algorithmCode
                            + "，运行快照=" + (snapshotAlgorithmCode == null ? "未设置" : snapshotAlgorithmCode)
            );
        }
        return run;
    }

    private static String baseAlgorithmCode(String algorithmCode) {
        if (algorithmCode == null) return "";
        return algorithmCode.endsWith("_SINGLE_DEVICE")
                ? algorithmCode.substring(0, algorithmCode.length() - "_SINGLE_DEVICE".length())
                : algorithmCode;
    }

    private void signalProcessFailure(RuntimeHandle handle, String fallback) {
        String stderr = stderrTail(handle);
        String detail = stderr.isBlank() ? fallback : fallback + "; stderr tail:\n" + stderr;
        handle.error.compareAndSet(null, detail);
        handle.ready.countDown();
        handle.initialFrameReady.countDown();
        log.error("Algorithm runtime process/reader failure: runId={} pid={} detail={}",
                handle.runId, handle.process.pid(), detail);
    }

    private void startReaders(RuntimeHandle handle) {
        handle.process.onExit().thenAccept(process ->
                log.info("Algorithm process exited: runId={} pid={} exitCode={}",
                        handle.runId, process.pid(), process.exitValue()));
        Thread outputThread = new Thread(() -> {
            try (var reader = new java.io.BufferedInputStream(handle.process.getInputStream())) {
                String line;
                while ((line = VoiceLines.read(reader)) != null) {
                    JsonNode event;
                    try {
                        event = voiceBridge != null && voiceBridge.v1(handle.voiceContext)
                                ? voiceBridge.parse(line) : objectMapper.readTree(line);
                        if (event == null) continue;
                    } catch (Exception ignored) {
                        if (voiceBridge != null && voiceBridge.v1(handle.voiceContext)) {
                            voiceBridge.malformed(handle.voiceContext);
                            continue;
                        }
                        // Vendor algorithms may print diagnostics. Only NDJSON
                        // events belong to the runtime protocol.
                        log.debug("Algorithm stdout non-JSON: runId={} pid={} line={}",
                                handle.runId, handle.process.pid(), line);
                        continue;
                    }
                    if (voiceBridge != null && voiceBridge.v1(handle.voiceContext) && event.has("kind")) {
                        voiceBridge.event(handle.voiceContext, event);
                        if ("RUNTIME_READY".equals(event.path("kind").asText()) && voiceBridge.ready(handle.voiceContext, event)) {
                            handle.state.set(event.path("state").asText());
                            handle.ready.countDown();
                        }
                        continue;
                    }
                    String eventType = event.path("event").asText();
                    if ("runtimeReady".equals(eventType)) {
                        if (voiceBridge != null && voiceBridge.v1(handle.voiceContext)) continue;
                        handle.state.set(event.path("state").asText("PREPARED"));
                        log.info("Algorithm runtimeReady received: runId={} pid={} state={}",
                                handle.runId, handle.process.pid(), handle.state.get());
                        handle.ready.countDown();
                    } else if ("frame".equals(eventType)) {
                        JsonNode frame = event.path("payload");
                        if (voiceBridge != null && handle.voiceContext != null) voiceBridge.frame(handle.voiceContext, frame);
                        handle.latestFrame.set(frame);
                        long sequence = frame.path("sequence").asLong();
                        handle.latestSequence.set(sequence);
                        synchronized (handle.frameBuffer) {
                            handle.frameBuffer.addLast(frame);
                            while (handle.frameBuffer.size() > 300) handle.frameBuffer.removeFirst();
                        }
                        if (sequence >= 1) {
                            handle.initialFrameReady.countDown();
                            if (handle.firstFrameLogged.compareAndSet(false, true)) {
                                log.info("Algorithm first frame received: runId={} pid={} sequence={}",
                                        handle.runId, handle.process.pid(), sequence);
                            }
                        }
                    } else if ("deviceCommandResult".equals(eventType)) {
                        handleDeviceCommandReceipt(handle, event);
                    } else if ("stateChanged".equals(eventType) || "runtimeStopped".equals(eventType)) {
                        if (voiceBridge != null && voiceBridge.v1(handle.voiceContext)) continue;
                        handle.state.set(event.path("state").asText(handle.state.get()));
                    }
                }
                if (handle.ready.getCount() > 0) {
                    handle.stderrDone.await(2, TimeUnit.SECONDS);
                    String detail = handle.lastStderr.get();
                    handle.error.compareAndSet(null, detail == null
                            ? "算法进程在就绪前退出"
                            : detail);
                    handle.ready.countDown();
                }
                if (handle.initialFrameReady.getCount() > 0) {
                    signalProcessFailure(handle, "algorithm stdout reached EOF before initial frame");
                }
            } catch (Exception exception) {
                signalProcessFailure(handle, "algorithm stdout reader failed: " + exception.getMessage());
            } finally {
                handle.pendingDeviceCommands.forEach((requestId, pending) ->
                        pending.receipt.completeExceptionally(
                                new DeviceCommandTimeoutException(
                                        "算法运行已结束，未收到单设备权威帧回执；执行结果未知")));
                handle.pendingDeviceCommands.clear();
                if (voiceBridge != null) voiceBridge.ended(handle.voiceContext);
            }
        }, "algorithm-out-" + handle.runId);
        outputThread.setDaemon(true);
        outputThread.start();
        Thread errorThread = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(handle.process.getErrorStream(), StandardCharsets.UTF_8))) {
                String line;
                String last = null;
                while ((line = reader.readLine()) != null) {
                    last = line;
                    handle.lastStderr.set(line);
                    synchronized (handle.stderrTail) {
                        handle.stderrTail.addLast(line);
                        while (handle.stderrTail.size() > STDERR_TAIL_LINES) handle.stderrTail.removeFirst();
                    }
                }
                if (handle.process.exitValue() != 0 && last != null) handle.error.compareAndSet(null, last);
            } catch (Exception ignored) {
                // Process shutdown closes the stream normally.
            } finally {
                handle.stderrDone.countDown();
            }
        }, "algorithm-err-" + handle.runId);
        errorThread.setDaemon(true);
        errorThread.start();
    }

    private void handleDeviceCommandReceipt(RuntimeHandle handle, JsonNode event) {
        String requestId = event.path("requestId").asText();
        PendingDeviceCommand pending = handle.pendingDeviceCommands.get(requestId);
        if (pending == null
                || event.path("runId").asLong(-1) != handle.runId
                || !pending.deviceCode.equalsIgnoreCase(event.path("deviceCode").asText())
                || !pending.commandType.equals(event.path("commandType").asText())) {
            log.warn("Ignoring unmatched single-device receipt: runId={} requestId={}", handle.runId, requestId);
            return;
        }
        if (event.path("success").asBoolean()) {
            long frameSequence = event.path("frameSequence").asLong(-1);
            JsonNode frame = handle.latestFrame.get();
            boolean targetStatusMatches = false;
            if (frame != null && frame.path("sequence").asLong(-1) >= frameSequence) {
                for (JsonNode agent : frame.path("agents")) {
                    String code = agent.path("deviceCode").asText(agent.path("code").asText());
                    if (pending.deviceCode.equalsIgnoreCase(code)
                            && event.path("deviceStatus").asText().equals(agent.path("status").asText())) {
                        targetStatusMatches = true;
                        break;
                    }
                }
            }
            if (frameSequence < 1 || !targetStatusMatches) {
                pending.receipt.completeExceptionally(
                        new IllegalStateException("回执未与目标设备的最新权威帧匹配"));
                return;
            }
        }
        pending.receipt.complete(event.deepCopy());
    }

    private void send(RuntimeHandle handle, Map<String, Object> payload) {
        if (!handle.process.isAlive()) throw new BusinessException(ErrorCode.BAD_REQUEST, "算法运行器已经停止");
        try {
            synchronized (handle.writer) {
                handle.writer.write(objectMapper.writeValueAsString(payload));
                handle.writer.newLine();
                handle.writer.flush();
            }
        } catch (IOException exception) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "算法指令发送失败：" + exception.getMessage());
        }
    }

    private RuntimeHandle requireHandle(Long runId) {
        RuntimeHandle handle = handles.get(runId);
        if (handle == null) throw new BusinessException(ErrorCode.NOT_FOUND, "算法运行实例不存在，请重新执行任务");
        return handle;
    }

    private void stopExisting(Long runId, String reason) {
        RuntimeHandle previous = handles.remove(runId);
        if (previous == null) {
            log.debug("Algorithm stopExisting found no handle: runId={} reason={}", runId, reason);
            return;
        }
        log.warn("Algorithm handle removed: runId={} pid={} reason={} state={} error={} stderrTail={}",
                runId, previous.process.pid(), reason, previous.state.get(), previous.error.get(), stderrTail(previous));
        try { previous.writer.close(); } catch (IOException ignored) {}
        try {
            // Closing stdin lets the owning Runner finish its final receipt and exit.
            // Do not kill it during normal teardown immediately after a successful STOP.
            if (!previous.process.waitFor(2, TimeUnit.SECONDS)) {
                previous.process.destroy();
                if (!previous.process.waitFor(2, TimeUnit.SECONDS)) previous.process.destroyForcibly();
            }
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            previous.process.destroyForcibly();
        }
        if (voiceBridge != null) voiceBridge.ended(previous.voiceContext);
    }

    private String stderrTail(RuntimeHandle handle) {
        synchronized (handle.stderrTail) {
            return String.join(System.lineSeparator(), handle.stderrTail);
        }
    }

    private void logPythonExecutableAndVersion(Long runId, Process runnerProcess) {
        String executable = runnerProcess.info().command().orElse(pythonCommand);
        log.info("Algorithm Python executable: runId={} executable={}", runId, executable);
        Thread versionThread = new Thread(() -> {
            try {
                Process versionProcess = new ProcessBuilder(pythonCommand, "--version")
                        .redirectErrorStream(true)
                        .start();
                boolean completed = versionProcess.waitFor(5, TimeUnit.SECONDS);
                if (!completed) versionProcess.destroyForcibly();
                String version = completed
                        ? new String(versionProcess.getInputStream().readAllBytes(), StandardCharsets.UTF_8).trim()
                        : "version query timed out";
                log.info("Algorithm Python version: runId={} executable={} version={}", runId, executable, version);
            } catch (Exception exception) {
                log.warn("Unable to query algorithm Python version: runId={} executable={} error={}",
                        runId, executable, exception.getMessage());
            }
        }, "algorithm-python-version-" + runId);
        versionThread.setDaemon(true);
        versionThread.start();
    }

    private void stopOtherStandaloneSimulations(Long retainedRunId) {
        List<Long> staleRunIds = handles.values().stream()
                .filter(handle -> handle.standaloneVirtualSimulation)
                .map(handle -> handle.runId)
                .filter(runId -> !runId.equals(retainedRunId))
                .toList();
        staleRunIds.forEach(runId -> stopExisting(runId, "replaced by standalone simulation " + retainedRunId));
    }

    private static Path resolveRunnerPath(String configuredPath) {
        Path configured = Path.of(configuredPath).toAbsolutePath().normalize();
        if (Files.isRegularFile(configured)) return configured;
        Path workingDirectory = Path.of("").toAbsolutePath().normalize();
        List<Path> candidates = List.of(
                workingDirectory.resolve("algorithm-service/runner.py").normalize(),
                workingDirectory.resolve("../algorithm-service/runner.py").normalize(),
                workingDirectory.resolve("../../algorithm-service/runner.py").normalize()
        );
        return candidates.stream().filter(Files::isRegularFile).findFirst().orElse(configured);
    }

    @PreDestroy
    public void close() {
        new ArrayList<>(handles.keySet()).forEach(runId -> stopExisting(runId, "application shutdown"));
    }

    private static final class RuntimeHandle {
        ObjectNode voiceContext;
        final Long runId;
        final String algorithmCode;
        final boolean standaloneVirtualSimulation;
        final Process process;
        final BufferedWriter writer;
        final CountDownLatch ready = new CountDownLatch(1);
        final CountDownLatch initialFrameReady = new CountDownLatch(1);
        final CountDownLatch stderrDone = new CountDownLatch(1);
        final AtomicReference<String> state = new AtomicReference<>("STARTING");
        final AtomicReference<String> error = new AtomicReference<>();
        final AtomicReference<String> lastStderr = new AtomicReference<>();
        final AtomicBoolean firstFrameLogged = new AtomicBoolean();
        final AtomicReference<JsonNode> latestFrame = new AtomicReference<>();
        final AtomicLong latestSequence = new AtomicLong();
        final Deque<JsonNode> frameBuffer = new ArrayDeque<>();
        final Deque<String> stderrTail = new ArrayDeque<>();
        final Map<String, PendingDeviceCommand> pendingDeviceCommands = new ConcurrentHashMap<>();

        RuntimeHandle(
                Long runId,
                String algorithmCode,
                boolean standaloneVirtualSimulation,
                Process process,
                BufferedWriter writer
        ) {
            this.runId = runId;
            this.algorithmCode = algorithmCode;
            this.standaloneVirtualSimulation = standaloneVirtualSimulation;
            this.process = process;
            this.writer = writer;
        }
    }

    private static final class PendingDeviceCommand {
        final String deviceCode;
        final String commandType;
        final CompletableFuture<JsonNode> receipt = new CompletableFuture<>();

        PendingDeviceCommand(String deviceCode, String commandType) {
            this.deviceCode = deviceCode;
            this.commandType = commandType;
        }
    }
}
