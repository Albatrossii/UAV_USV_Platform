package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import org.springframework.stereotype.Service;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

@Service
public class RuntimeContextRegistry {
    public static final String PROTOCOL = "algorithm.command.v1";
    final Map<String, Channel> channels = new ConcurrentHashMap<>();
    final VoiceStore store;
    final VoiceJson json;
    final VoiceTime time;
    final VoiceAccess access;
    final VoiceSettings settings;

    public static final class Channel {
        public final Object lock = new Object();
        final String ref, generation;
        final Consumer<JsonNode> sender;
        final BooleanSupplier alive;
        volatile Long heartbeatNanos, sceneNanos;
        volatile boolean faulted;

        Channel(String ref, String gen, Consumer<JsonNode> sender, BooleanSupplier alive) {
            this.ref = ref;
            this.generation = gen;
            this.sender = sender;
            this.alive = alive;
        }
    }

    public RuntimeContextRegistry(
            VoiceStore s, VoiceJson j, VoiceTime t, VoiceAccess a, VoiceSettings settings) {
        store = s;
        json = j;
        time = t;
        access = a;
        this.settings = settings;
    }

    public ObjectNode register(long runId, String algorithmCode, String configHash) {
        long user = access.user(true);
        if (runId <= 0) throw VoiceFailure.bad();
        return store.locked(
                () -> {
                    for (var old : store.query("SELECT data_json FROM voice_runtime_context")) {
                        var ch = channels.get(old.path("_id").asText());
                        if (ch != null
                                && ch.alive.getAsBoolean()
                                && old.path("_owner").asLong() != user)
                            throw VoiceFailure.conflict("RUNTIME_BUSY");
                    }
                    String ref = VoiceJson.uuid();
                    var c = json.object();
                    c.put("_id", ref)
                            .put("_owner", user)
                            .put("_algorithmCode", algorithmCode)
                            .put("_configHash", configHash)
                            .put("_nextSequence", 0);
                    c.put("runtimeRef", ref)
                            .put("runtimeGeneration", VoiceJson.uuid())
                            .put("contextVersion", 1)
                            .put("stateVersion", 0);
                    c.put("runtimeScope", "MISSION_CENTER")
                            .put("runtimeKind", "STANDALONE_ALGORITHM")
                            .put("executionBackend", "PYTHON_SIMULATION");
                    c.put("algorithmRunId", Long.toString(runId))
                            .putNull("missionId")
                            .putNull("missionRunId")
                            .put("state", "PREPARED");
                    c.put("protocolVersion", settings.enabled() ? PROTOCOL : "legacy")
                            .putNull("lastHeartbeatReceivedAt")
                            .put("latestFrameSequence", 0)
                            .put("sceneReady", false);
                    c.putArray("capabilities");
                    c.putArray("_members");
                    store.context(c);
                    return c;
                });
    }

    public void attach(
            String ref, String generation, Consumer<JsonNode> sender, BooleanSupplier alive) {
        channels.put(ref, new Channel(ref, generation, sender, alive));
    }

    public Channel channel(String ref) {
        return channels.get(ref);
    }

    public ObjectNode require(String ref, long user) {
        VoiceJson.uuid(ref);
        var c = store.get("voice_runtime_context", ref);
        if (c == null || c.path("_owner").asLong() != user)
            throw new VoiceFailure(404, "RESOURCE_NOT_FOUND");
        return c;
    }

    public void owner(String ref) {
        store.locked(
                () -> {
                    require(ref, access.user(false));
                    return null;
                });
    }

    public boolean freshScene(ObjectNode c) {
        var ch = channel(c.path("_id").asText());
        return ch != null
                && ch.sceneNanos != null
                && time.nanos() - ch.sceneNanos <= 10_000_000_000L;
    }

    public ObjectNode view(ObjectNode c) {
        var v = publicView(c);
        v.put("sceneReady", freshScene(c));
        return v;
    }

    public static ObjectNode publicView(ObjectNode n) {
        var copy = n.deepCopy();
        var remove = new ArrayList<String>();
        copy.fieldNames()
                .forEachRemaining(
                        k -> {
                            if (k.startsWith("_")) remove.add(k);
                        });
        copy.remove(remove);
        return copy;
    }

    public void check(ObjectNode c, String action, boolean scene) {
        access.require(c.path("_owner").asLong(), true);
        if (!PROTOCOL.equals(c.path("protocolVersion").asText()))
            throw new VoiceFailure(422, "PROTOCOL_UNSUPPORTED");
        boolean capable = false;
        for (var n : c.path("capabilities")) capable |= n.asText().equals(action);
        if (!capable) throw new VoiceFailure(422, "UNSUPPORTED_CAPABILITY");
        var ch = channel(c.path("_id").asText());
        if (ch == null
                || ch.faulted
                || !ch.alive.getAsBoolean()
                || ch.heartbeatNanos == null
                || time.nanos() - ch.heartbeatNanos > 5_000_000_000L)
            throw VoiceFailure.conflict("RUNTIME_UNAVAILABLE");
        var allowed =
                switch (action) {
                    case "START" -> Set.of("PREPARED", "PREVIEW");
                    case "PAUSE" -> Set.of("RUNNING");
                    case "RESUME" -> Set.of("PAUSED");
                    case "STOP" -> Set.of("PREPARED", "PREVIEW", "RUNNING", "PAUSED");
                    default -> Set.<String>of();
                };
        if (!allowed.contains(c.path("state").asText()))
            throw VoiceFailure.conflict("INVALID_STATE");
        if (c.path("_members").isEmpty()) throw VoiceFailure.conflict("CONTEXT_CHANGED");
        boolean localSimulationResume = action.equals("RESUME")
                && "STANDALONE_ALGORITHM".equals(c.path("runtimeKind").asText())
                && "PYTHON_SIMULATION".equals(c.path("executionBackend").asText());
        // Starting a new run needs a fresh rendered scene. A local simulation
        // resume only needs the same live, paused algorithm instance; renderer
        // recovery can catch up from its latest frame after the command.
        if (scene
                && ((action.equals("START")
                                && (!freshScene(c)
                                        || c.path("latestFrameSequence").asLong() < 1))
                        || (action.equals("RESUME")
                                && !localSimulationResume
                                && !freshScene(c))))
            throw VoiceFailure.conflict("SCENE_NOT_READY");
    }

    /** Backwards-compatible test seam for generic mission-context fixtures. */
    public ObjectNode register(long runId, String configHash) {
        return register(runId, "", configHash);
    }

    public void checkDevice(ObjectNode c, String targetCode, String commandType) {
        access.require(c.path("_owner").asLong(), true);
        if (!PROTOCOL.equals(c.path("protocolVersion").asText()))
            throw new VoiceFailure(422, "PROTOCOL_UNSUPPORTED");
        if (!Set.of("ESCORT_GUARD", "GB_SFLA_CS", "ESCORT_GUARD_SINGLE_DEVICE", "GB_SFLA_CS_SINGLE_DEVICE")
                .contains(c.path("_algorithmCode").asText()))
            throw new VoiceFailure(422, "UNSUPPORTED_TARGETING");
        if (!"RUNNING".equals(c.path("state").asText()))
            throw VoiceFailure.conflict("INVALID_STATE");
        boolean capable = false;
        for (var n : c.path("capabilities")) capable |= "DEVICE_COMMAND".equals(n.asText());
        if (!capable) throw new VoiceFailure(422, "UNSUPPORTED_CAPABILITY");
        boolean member = false;
        for (var n : c.path("_members")) member |= targetCode.equals(n.asText());
        if (!member) throw VoiceFailure.conflict("CONTEXT_CHANGED");
        boolean compatible = targetCode.toUpperCase(Locale.ROOT).startsWith("UAV")
                ? commandType.startsWith("UAV_")
                : targetCode.toUpperCase(Locale.ROOT).startsWith("USV") && commandType.startsWith("USV_");
        if (!compatible) throw new VoiceFailure(422, "UNSUPPORTED_TARGETING");
        if (!Set.of("UAV_HOVER", "UAV_RESUME", "UAV_RETURN", "UAV_LAND",
                "USV_HOLD", "USV_RESUME", "USV_RETURN", "USV_STOP").contains(commandType))
            throw new VoiceFailure(422, "UNSUPPORTED_CAPABILITY");
        var ch = channel(c.path("_id").asText());
        if (ch == null || ch.faulted || !ch.alive.getAsBoolean())
            throw VoiceFailure.conflict("RUNTIME_UNAVAILABLE");
        if (ch.heartbeatNanos == null || time.nanos() - ch.heartbeatNanos > 5_000_000_000L)
            throw VoiceFailure.conflict("HEARTBEAT_STALE");
        if (c.path("latestFrameSequence").asLong() < 1)
            throw VoiceFailure.conflict("SCENE_NOT_READY");
    }

    public void frame(String ref, String gen, JsonNode frame) {
        store.locked(
                () -> {
                    var c = store.get("voice_runtime_context", ref);
                    if (c == null || !gen.equals(c.path("runtimeGeneration").asText())) return null;
                    long sequence = frame.path("sequence").asLong();
                    if (sequence <= c.path("latestFrameSequence").asLong()) return null;
                    var codes = new TreeSet<String>();
                    for (var agent : frame.path("agents")) {
                        String code = agent.path("deviceCode").asText();
                        if (code.matches("[A-Za-z0-9_.:-]{1,96}")) codes.add(code);
                    }
                    if (codes.size() > 200) {
                        var ch = channel(ref);
                        if (ch != null) ch.faulted = true;
                        return null;
                    }
                    var members = json.mapper.valueToTree(codes);
                    if (!members.equals(c.path("_members"))) {
                        c.set("_members", members);
                        bump(c);
                    }
                    c.put("latestFrameSequence", sequence);
                    store.save("voice_runtime_context", c);
                    return null;
                });
    }

    void state(ObjectNode c, String state, long version) {
        if (version < c.path("stateVersion").asLong()) return;
        if (!state.equals(c.path("state").asText()) || version != c.path("stateVersion").asLong()) {
            c.put("state", state).put("stateVersion", version);
            bump(c);
        }
    }

    static void bump(ObjectNode c) {
        c.put("contextVersion", c.path("contextVersion").asLong() + 1);
    }

    public void ended(String ref, String gen) {
        store.locked(
                () -> {
                    var c = store.get("voice_runtime_context", ref);
                    if (c == null || !gen.equals(c.path("runtimeGeneration").asText())) return null;
                    var ch = channel(ref);
                    if (ch != null) {
                        ch.faulted = true;
                        ch.sceneNanos = null;
                    }
                    if (!Set.of("STOPPED", "CANCELLED", "COMPLETED", "FAILED")
                            .contains(c.path("state").asText())) c.put("state", "LOST");
                    bump(c);
                    store.save("voice_runtime_context", c);
                    for (var p :
                            store.query(
                                    "SELECT data_json FROM voice_proposal WHERE runtime_ref=?",
                                    ref))
                        if ("AWAITING_CONFIRMATION".equals(p.path("status").asText())) {
                            p.put("status", "INVALIDATED");
                            store.save("voice_proposal", p);
                        }
                    for (var e :
                            store.query(
                                    "SELECT data_json FROM voice_execution WHERE runtime_ref=?",
                                    ref)) {
                        if ("QUEUED".equals(e.path("state").asText())) {
                            e.put("state", "INVALIDATED")
                                    .put("outcome", "REJECTED")
                                    .put("errorCode", "RUNTIME_UNAVAILABLE")
                                    .put("updatedAt", time.stamp());
                            store.save("voice_execution", e);
                            store.jdbc.update(
                                    "UPDATE voice_outbox SET status='CANCELLED' WHERE"
                                        + " execution_id=? AND status='READY'",
                                    e.path("_id").asText());
                        } else if (Set.of("DISPATCHED", "ACCEPTED", "EXECUTING")
                                .contains(e.path("state").asText())) {
                            e.put("state", "TIMED_OUT")
                                    .put("outcome", "UNKNOWN")
                                    .put("errorCode", "RUNTIME_UNAVAILABLE")
                                    .put("timedOutAt", time.stamp())
                                    .put("updatedAt", time.stamp());
                            store.save("voice_execution", e);
                        }
                    }
                    return null;
                });
    }
}
