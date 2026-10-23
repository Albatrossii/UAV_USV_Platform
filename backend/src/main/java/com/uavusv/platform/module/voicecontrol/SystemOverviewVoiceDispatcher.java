package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.common.exception.BusinessException;
import com.uavusv.platform.module.mission.dto.response.MissionActionResponse;
import com.uavusv.platform.module.mission.service.MissionService;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandRequest;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandResponse;
import com.uavusv.platform.module.runtimecontrol.entity.CommandStatus;
import com.uavusv.platform.module.runtimecontrol.entity.CommandType;
import com.uavusv.platform.module.runtimecontrol.entity.RuntimeScope;
import com.uavusv.platform.module.runtimecontrol.repository.ControlCommandRepository;
import com.uavusv.platform.module.runtimecontrol.service.RuntimeControlService;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Dispatches confirmed SYSTEM_OVERVIEW plans through the existing mission/runtime services. */
@Service
public class SystemOverviewVoiceDispatcher {
    private static final Logger log = LoggerFactory.getLogger(SystemOverviewVoiceDispatcher.class);
    private static final Set<String> TERMINAL = Set.of(
            "SUCCEEDED", "REJECTED", "FAILED", "INVALIDATED", "TIMED_OUT");

    private final VoiceStore store;
    private final VoiceTime time;
    private final RuntimeContextRegistry contexts;
    private final MissionService missions;
    private final RuntimeControlService runtimeControls;
    private final ControlCommandRepository commands;
    private final SystemOverviewVoiceContextService contextSync;

    public SystemOverviewVoiceDispatcher(
            VoiceStore store,
            VoiceTime time,
            RuntimeContextRegistry contexts,
            MissionService missions,
            RuntimeControlService runtimeControls,
            ControlCommandRepository commands,
            SystemOverviewVoiceContextService contextSync) {
        this.store = store;
        this.time = time;
        this.contexts = contexts;
        this.missions = missions;
        this.runtimeControls = runtimeControls;
        this.commands = commands;
        this.contextSync = contextSync;
    }

    @Scheduled(fixedDelayString = "${app.voicecontrol.poll-ms:200}")
    public synchronized void tick() {
        var executions = store.locked(() -> store.query(
                "SELECT data_json FROM voice_execution WHERE state IN"
                        + " ('QUEUED','DISPATCHED','ACCEPTED','EXECUTING','TIMED_OUT')"));
        for (ObjectNode execution : executions) {
            ObjectNode context = store.locked(() -> store.get(
                    "voice_runtime_context", execution.path("runtimeRef").asText()));
            if (!contexts.systemOverview(context) || "SEQUENCE".equals(execution.path("action").asText())) continue;
            if ("QUEUED".equals(execution.path("state").asText())) dispatch(execution.path("_id").asText());
            else reconcile(execution.path("_id").asText());
        }
    }

    private void dispatch(String executionId) {
        ObjectNode call = store.locked(() -> claim(executionId));
        if (call == null) return;
        long owner = call.path("owner").asLong();
        Map<String, Object> operator = operator(owner);
        if (operator == null) {
            complete(executionId, "REJECTED", "OPERATOR_NO_LONGER_AUTHORIZED");
            return;
        }
        String username = String.valueOf(operator.get("username"));
        SecurityContext previous = SecurityContextHolder.getContext();
        SecurityContext authorized = SecurityContextHolder.createEmptyContext();
        authorized.setAuthentication(new UsernamePasswordAuthenticationToken(
                username, "", AuthorityUtils.createAuthorityList("ROLE_ADMIN")));
        SecurityContextHolder.setContext(authorized);
        try {
            RuntimeCommandResponse response = "DEVICE_COMMAND".equals(call.path("action").asText())
                    ? dispatchDevice(call, username)
                    : dispatchMission(call, username);
            if (response == null) complete(executionId, "SUCCEEDED", null);
            else rememberCommand(executionId, response);
            refreshContext(call);
        } catch (BusinessException failure) {
            complete(executionId, "REJECTED", failure.getErrorCode().getCode());
            log.info("Confirmed system-overview voice command was rejected executionId={} code={}",
                    executionId, failure.getErrorCode().getCode());
        } catch (VoiceFailure failure) {
            complete(executionId, "REJECTED", failure.code);
        } catch (RuntimeException failure) {
            // A transport or transaction may fail after an external component accepted the command.
            // Never retry the physical action under a new key.
            complete(executionId, "TIMED_OUT", "SYSTEM_OVERVIEW_RESULT_UNKNOWN");
            log.warn("Confirmed system-overview voice command has uncertain result executionId={} type={}",
                    executionId, failure.getClass().getSimpleName());
        } finally {
            SecurityContextHolder.setContext(previous);
        }
    }

    private ObjectNode claim(String executionId) {
        ObjectNode execution = store.get("voice_execution", executionId);
        if (execution == null || !"QUEUED".equals(execution.path("state").asText())) return null;
        ObjectNode context = store.get("voice_runtime_context", execution.path("runtimeRef").asText());
        ObjectNode plan = (ObjectNode) execution.path("_plan");
        try {
            if (!time.now().isBefore(Instant.parse(execution.path("createdAt").asText()).plusSeconds(10)))
                throw VoiceFailure.conflict("DISPATCH_DEADLINE_EXCEEDED");
            if (!execution.path("runtimeGeneration").equals(context.path("runtimeGeneration")))
                throw VoiceFailure.conflict("GENERATION_MISMATCH");
            if (!VoiceCommandApplicationService.POLICY_VERSION.equals(plan.path("policyVersion").asText())
                    || !plan.path("contextVersion").equals(context.path("contextVersion"))
                    || !plan.path("stateVersion").equals(context.path("stateVersion")))
                throw VoiceFailure.conflict("CONTEXT_CHANGED");
            if ("DEVICE_COMMAND".equals(execution.path("action").asText())) {
                contexts.checkDevice(context, plan.path("targetDeviceCode").asText(),
                        plan.path("deviceCommandType").asText());
            } else {
                String action = execution.path("action").asText();
                contexts.check(context, action, false);
            }
        } catch (VoiceFailure failure) {
            execution.put("state", "INVALIDATED")
                    .put("outcome", "REJECTED")
                    .put("errorCode", failure.code)
                    .put("updatedAt", time.stamp());
            store.save("voice_execution", execution);
            store.jdbc.update("UPDATE voice_outbox SET status='CANCELLED' WHERE execution_id=? AND status='READY'", executionId);
            return null;
        }
        if (store.jdbc.update(
                "UPDATE voice_outbox SET status='SENDING',claimed_at=? WHERE execution_id=? AND status='READY'",
                time.stamp(), executionId) != 1) return null;
        execution.put("state", "DISPATCHED")
                .put("_sendStartedAt", time.stamp())
                .put("updatedAt", time.stamp());
        store.save("voice_execution", execution);

        ObjectNode call = contexts.json.object();
        call.put("executionId", executionId)
                .put("owner", execution.path("_owner").asLong())
                .put("action", execution.path("action").asText())
                .put("missionId", context.path("missionId").asLong())
                .put("runtimeInstanceId", context.path("_runtimeInstanceId").asText("overview-unity-01"))
                .put("sceneReady", context.path("sceneReady").asBoolean());
        if ("DEVICE_COMMAND".equals(execution.path("action").asText())) {
            call.put("targetDeviceCode", plan.path("targetDeviceCode").asText())
                    .put("deviceCommandType", plan.path("deviceCommandType").asText());
        }
        return call;
    }

    private RuntimeCommandResponse dispatchDevice(ObjectNode call, String username) {
        return runtimeControls.issueCommand(new RuntimeCommandRequest(
                CommandType.valueOf(call.path("deviceCommandType").asText()),
                null,
                call.path("targetDeviceCode").asText(),
                "{\"source\":\"SYSTEM_OVERVIEW_VOICE\"}",
                "经操作员确认的系统总览单设备语音命令",
                RuntimeScope.SYSTEM_OVERVIEW,
                call.path("runtimeInstanceId").asText()), username);
    }

    private RuntimeCommandResponse dispatchMission(ObjectNode call, String username) {
        long missionId = call.path("missionId").asLong();
        MissionActionResponse result = switch (call.path("action").asText()) {
            case "START" -> missions.startMission(missionId, username, "SYSTEM_OVERVIEW:VOICE",
                    call.path("runtimeInstanceId").asText());
            case "PAUSE" -> missions.pauseMission(missionId, username, "SYSTEM_OVERVIEW:VOICE");
            case "RESUME" -> missions.resumeMission(missionId, username, "SYSTEM_OVERVIEW:VOICE");
            case "STOP" -> missions.cancelMission(missionId, username, "SYSTEM_OVERVIEW:VOICE");
            default -> throw new VoiceFailure(422, "UNSUPPORTED_CAPABILITY");
        };
        return result.command();
    }

    private void rememberCommand(String executionId, RuntimeCommandResponse response) {
        store.locked(() -> {
            ObjectNode execution = store.get("voice_execution", executionId);
            if (execution == null || TERMINAL.contains(execution.path("state").asText())) return null;
            execution.put("_runtimeCommandKey", response.commandKey());
            applyStatus(execution, response.status(), response.errorCode());
            store.save("voice_execution", execution);
            if (TERMINAL.contains(execution.path("state").asText())) {
                store.jdbc.update("UPDATE voice_outbox SET status='SENT' WHERE execution_id=? AND status='SENDING'", executionId);
            }
            return null;
        });
    }

    private void reconcile(String executionId) {
        ObjectNode execution = store.locked(() -> store.get("voice_execution", executionId));
        if (execution == null || TERMINAL.contains(execution.path("state").asText())) return;
        String key = execution.path("_runtimeCommandKey").asText();
        if (key.isBlank()) {
            String started = execution.path("_sendStartedAt").asText();
            if (!started.isBlank() && !time.now().isBefore(Instant.parse(started).plusSeconds(10))) {
                complete(executionId, "TIMED_OUT", "SYSTEM_OVERVIEW_RESULT_UNKNOWN");
            }
            return;
        }
        commands.findByCommandKey(key).ifPresent(command -> store.locked(() -> {
            ObjectNode current = store.get("voice_execution", executionId);
            if (current == null || TERMINAL.contains(current.path("state").asText())) return null;
            applyStatus(current, command.getStatus(), command.getErrorCode());
            store.save("voice_execution", current);
            if (TERMINAL.contains(current.path("state").asText())) {
                store.jdbc.update("UPDATE voice_outbox SET status='SENT' WHERE execution_id=? AND status='SENDING'", executionId);
            }
            return null;
        }));
    }

    private void applyStatus(ObjectNode execution, CommandStatus status, String errorCode) {
        switch (status) {
            case SUCCEEDED -> execution.put("state", "SUCCEEDED").put("outcome", "SUCCESS").putNull("errorCode");
            case REJECTED -> execution.put("state", "REJECTED").put("outcome", "REJECTED")
                    .put("errorCode", errorCode == null ? "SYSTEM_OVERVIEW_COMMAND_REJECTED" : errorCode);
            case FAILED, CANCELLED -> execution.put("state", "FAILED").put("outcome", "FAILED")
                    .put("errorCode", errorCode == null ? "SYSTEM_OVERVIEW_COMMAND_FAILED" : errorCode);
            case TIMEOUT -> execution.put("state", "TIMED_OUT").put("outcome", "UNKNOWN")
                    .put("errorCode", errorCode == null ? "ACK_TIMEOUT" : errorCode)
                    .put("timedOutAt", time.stamp());
            case ACCEPTED -> execution.put("state", "ACCEPTED");
            case EXECUTING -> execution.put("state", "EXECUTING");
            case PENDING, DISPATCHED -> execution.put("state", "DISPATCHED");
        }
        execution.put("updatedAt", time.stamp());
    }

    private void complete(String executionId, String state, String errorCode) {
        store.locked(() -> {
            ObjectNode execution = store.get("voice_execution", executionId);
            if (execution == null || TERMINAL.contains(execution.path("state").asText())) return null;
            execution.put("state", state)
                    .put("outcome", "SUCCEEDED".equals(state) ? "SUCCESS"
                            : "REJECTED".equals(state) ? "REJECTED"
                            : "TIMED_OUT".equals(state) ? "UNKNOWN" : "FAILED")
                    .put("updatedAt", time.stamp());
            if (errorCode == null) execution.putNull("errorCode");
            else execution.put("errorCode", errorCode);
            if ("TIMED_OUT".equals(state)) execution.put("timedOutAt", time.stamp());
            store.save("voice_execution", execution);
            store.jdbc.update("UPDATE voice_outbox SET status=? WHERE execution_id=? AND status IN ('READY','SENDING')",
                    "TIMED_OUT".equals(state) ? "UNCERTAIN" : "SENT", executionId);
            return null;
        });
    }

    private Map<String, Object> operator(long owner) {
        List<Map<String, Object>> rows = store.jdbc.queryForList(
                "SELECT username,role,enabled FROM app_user WHERE id=?", owner);
        if (rows.isEmpty()) return null;
        Object enabled = rows.get(0).get("enabled");
        boolean active = enabled instanceof Boolean value ? value
                : enabled instanceof Number number && number.intValue() != 0;
        return active && "ADMIN".equals(String.valueOf(rows.get(0).get("role"))) ? rows.get(0) : null;
    }

    private void refreshContext(ObjectNode call) {
        ObjectNode request = contexts.json.object();
        request.put("missionId", call.path("missionId").asLong())
                .put("runtimeInstanceId", call.path("runtimeInstanceId").asText())
                .put("sceneReady", call.path("sceneReady").asBoolean());
        contextSync.sync(request);
    }
}
