package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.device.entity.DeviceType;
import com.uavusv.platform.module.mission.dto.response.MissionDetailResponse;
import com.uavusv.platform.module.mission.entity.MissionStatus;
import com.uavusv.platform.module.mission.service.MissionService;

import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.stereotype.Service;

import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Builds the auditable voice runtime snapshot for the real system-overview mission.
 * The browser can refresh liveness, but every authoritative field is reloaded from
 * the mission service before the context is written.
 */
@Service
public class SystemOverviewVoiceContextService {
    private static final Set<String> REQUEST_FIELDS = Set.of("missionId", "runtimeInstanceId", "sceneReady");

    private final RuntimeContextRegistry registry;
    private final MissionService missions;

    public SystemOverviewVoiceContextService(RuntimeContextRegistry registry, MissionService missions) {
        this.registry = registry;
        this.missions = missions;
    }

    @PreAuthorize("hasRole('ADMIN')")
    public ObjectNode sync(ObjectNode request) {
        validate(request);
        long user = registry.access.user(true);
        long missionId = request.path("missionId").asLong();
        String runtimeInstanceId = request.path("runtimeInstanceId").asText("overview-unity-01").trim();
        MissionDetailResponse detail = missions.getMission(missionId);

        return registry.store.locked(() -> {
            ObjectNode context = find(user, missionId);
            boolean created = context == null;
            if (created) context = newContext(user, missionId);

            ArrayNode members = members(detail);
            String nextState = runtimeState(detail.mission().status());
            String algorithmCode = detail.mission().algorithmCode() == null
                    ? "" : detail.mission().algorithmCode();
            Long missionRunId = detail.currentRun() == null ? null : detail.currentRun().id();
            boolean materialChange = !context.path("_members").equals(members)
                    || !nextState.equals(context.path("state").asText())
                    || !algorithmCode.equals(context.path("_algorithmCode").asText())
                    || !runtimeInstanceId.equals(context.path("_runtimeInstanceId").asText())
                    || (missionRunId == null
                        ? !context.path("missionRunId").isNull()
                        : missionRunId.longValue() != context.path("missionRunId").asLong(-1));
            boolean stateChanged = !nextState.equals(context.path("state").asText());

            context.put("_algorithmCode", algorithmCode)
                    .put("_runtimeInstanceId", runtimeInstanceId)
                    .put("algorithmRunId", "SYSTEM_OVERVIEW:" + missionId)
                    .put("state", nextState)
                    .put("lastHeartbeatReceivedAt", registry.time.stamp())
                    // These shared protocol fields are not applicable to a real
                    // mission and deliberately stay false/zero.
                    .put("latestFrameSequence", 0)
                    .put("sceneReady", false);
            context.set("_members", members);
            if (missionRunId == null) context.putNull("missionRunId");
            else context.put("missionRunId", missionRunId);
            context.set("capabilities", capabilities());

            if (!created && materialChange) RuntimeContextRegistry.bump(context);
            if (!created && stateChanged) {
                context.put("stateVersion", context.path("stateVersion").asLong() + 1);
            }
            if (created) registry.store.context(context);
            else registry.store.save("voice_runtime_context", context);
            return registry.view(context);
        });
    }

    private ObjectNode find(long user, long missionId) {
        return registry.store.query("SELECT data_json FROM voice_runtime_context WHERE owner_id=?", user)
                .stream()
                .filter(context -> registry.systemOverview(context)
                        && context.path("missionId").asLong(-1) == missionId)
                .findFirst()
                .orElse(null);
    }

    private ObjectNode newContext(long user, long missionId) {
        String ref = VoiceJson.uuid();
        ObjectNode context = registry.json.object();
        context.put("_id", ref)
                .put("_owner", user)
                .put("_algorithmCode", "")
                .put("_configHash", "SYSTEM_OVERVIEW:" + missionId)
                .put("_runtimeInstanceId", "overview-unity-01")
                .put("_nextSequence", 0);
        context.put("runtimeRef", ref)
                .put("runtimeGeneration", VoiceJson.uuid())
                .put("contextVersion", 1)
                .put("stateVersion", 0)
                .put("runtimeScope", "SYSTEM_OVERVIEW")
                .put("runtimeKind", "REAL_MISSION")
                .put("executionBackend", "ROS_GATEWAY")
                .put("algorithmRunId", "SYSTEM_OVERVIEW:" + missionId)
                .put("missionId", missionId)
                .putNull("missionRunId")
                .put("state", "PREVIEW")
                .put("protocolVersion", RuntimeContextRegistry.PROTOCOL)
                .putNull("lastHeartbeatReceivedAt")
                .put("latestFrameSequence", 0)
                .put("sceneReady", false);
        context.putArray("capabilities");
        context.putArray("_members");
        return context;
    }

    private ArrayNode members(MissionDetailResponse detail) {
        boolean hasRequired = detail.devices().stream().anyMatch(device -> device.required());
        var codes = detail.devices().stream()
                .filter(device -> !hasRequired || device.required())
                .filter(device -> device.type() == DeviceType.UAV || device.type() == DeviceType.USV)
                .map(device -> normalizeCode(device.code()))
                .filter(code -> !code.isBlank())
                .sorted(Comparator.naturalOrder())
                .collect(java.util.stream.Collectors.toCollection(LinkedHashSet::new));
        return registry.json.mapper.valueToTree(codes);
    }

    private ArrayNode capabilities() {
        ArrayNode result = registry.json.mapper.createArrayNode();
        result.add("START").add("PAUSE").add("RESUME").add("STOP").add("DEVICE_COMMAND");
        return result;
    }

    private String runtimeState(MissionStatus status) {
        return switch (status) {
            case DRAFT -> "PREVIEW";
            case READY -> "PREPARED";
            case RUNNING -> "RUNNING";
            case PAUSED -> "PAUSED";
            case COMPLETED -> "COMPLETED";
            case FAILED -> "FAILED";
            case CANCELLED -> "CANCELLED";
        };
    }

    private String normalizeCode(String code) {
        return code == null ? "" : code.trim().toUpperCase(Locale.ROOT).replace('_', '-');
    }

    private void validate(ObjectNode request) {
        Set<String> fields = new java.util.HashSet<>();
        request.fieldNames().forEachRemaining(fields::add);
        if (!REQUEST_FIELDS.containsAll(fields)
                || !request.path("missionId").isIntegralNumber()
                || request.path("missionId").asLong() <= 0
                || (request.has("runtimeInstanceId")
                    && (!request.path("runtimeInstanceId").isTextual()
                        || !request.path("runtimeInstanceId").asText().matches("[A-Za-z0-9_.:-]{1,96}")))
                || (request.has("sceneReady") && !request.path("sceneReady").isBoolean())) {
            throw VoiceFailure.bad();
        }
    }
}
