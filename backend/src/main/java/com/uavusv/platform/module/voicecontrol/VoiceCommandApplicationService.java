package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.voiceintelligence.IntentService;

import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.*;

@Service
public class VoiceCommandApplicationService {
    static final String POLICY_VERSION = "voice-p0.v1";
    private final VoiceStore s;
    private final VoiceJson j;
    private final VoiceTime t;
    private final VoiceAccess a;
    private final RuntimeContextRegistry r;
    private final VoiceSettings settings;
    private IntentService intents;

    public record Reply(int status, ObjectNode data) {}

    @org.springframework.beans.factory.annotation.Autowired
    public VoiceCommandApplicationService(
            VoiceStore s,
            VoiceJson j,
            VoiceTime t,
            VoiceAccess a,
            RuntimeContextRegistry r,
            VoiceSettings settings) {
        this.s = s;
        this.j = j;
        this.t = t;
        this.a = a;
        this.r = r;
        this.settings = settings;
    }

    VoiceCommandApplicationService(
            VoiceStore s,
            VoiceJson j,
            VoiceTime t,
            VoiceAccess a,
            RuntimeContextRegistry r,
            VoiceSettings settings,
            IntentService intents) {
        this(s, j, t, a, r, settings);
        this.intents = intents;
    }

    @org.springframework.beans.factory.annotation.Autowired(required = false)
    void setIntentService(IntentService intents) {
        this.intents = intents;
    }

    @PreAuthorize("isAuthenticated()")
    public List<ObjectNode> contexts() {
        long u = a.user(false);
        return s.locked(
                () ->
                        s
                                .query(
                                        "SELECT data_json FROM voice_runtime_context WHERE"
                                            + " owner_id=?",
                                        u)
                                .stream()
                                .map(r::view)
                                .toList());
    }

    @PreAuthorize("isAuthenticated()")
    public ObjectNode context(String ref) {
        long u = a.user(false);
        return s.locked(() -> r.view(r.require(ref, u)));
    }

    @PreAuthorize("isAuthenticated()")
    public ObjectNode proposal(String id) {
        long u = a.user(false);
        return s.locked(
                () -> {
                    var p = owned("voice_proposal", id, u);
                    expire(p);
                    return RuntimeContextRegistry.publicView(p);
                });
    }

    @PreAuthorize("isAuthenticated()")
    public ObjectNode execution(String id) {
        long u = a.user(false);
        return s.locked(() -> executionView(owned("voice_execution", id, u)));
    }

    @PreAuthorize("hasRole('ADMIN')")
    public Reply createProposal(String key, ObjectNode body) {
        return createProposal(key, null, body);
    }

    public Reply createProposal(String key, String interpretationId, ObjectNode body) {
        long u = a.user(true);
        settings.requireEnabled();
        VoiceJson.uuid(key);
        j.validate("ProposalRequest", body);
        return s.locked(
                () -> {
                    settings.requireEnabled();
                    var c = r.require(body.path("runtimeRef").asText(), u);
                    var hashed = body.deepCopy();
                    if (interpretationId != null) hashed.put("_interpretationId", interpretationId);
                    String hash = j.hash(hashed), old = s.replay(u, "propose", key, hash);
                    if (old != null) {
                        var p = owned("voice_proposal", old, u);
                        expire(p);
                        return new Reply(200, RuntimeContextRegistry.publicView(p));
                    }
                    if (!c.path("runtimeGeneration").equals(body.path("runtimeGeneration")))
                        throw VoiceFailure.conflict("GENERATION_MISMATCH");
                    if (c.path("contextVersion").asLong()
                            != body.path("expectedContextVersion").asLong())
                        throw VoiceFailure.conflict("CONTEXT_CHANGED");
                    String intent = body.path("intent").asText();
                    boolean targeted = "SINGLE_DEVICE_CONTROL".equals(intent);
                    boolean sequence = "COMMAND_SEQUENCE".equals(intent);
                    String action = targeted ? "DEVICE_COMMAND" : sequence ? "SEQUENCE" : intent.substring(8);
                    IntentService.TargetedCandidate target = null;
                    if (interpretationId != null) {
                        if (intents == null) throw VoiceFailure.conflict("VOICE_INTERPRETATION_INVALID");
                        try {
                            intents.requireCandidate(u, interpretationId, action, c);
                        } catch (com.uavusv.platform.module.voiceintelligence.AsrFailure e) {
                            throw VoiceFailure.conflict(e.code);
                        }
                    }
                    if (targeted) {
                        if (intents == null || interpretationId == null)
                            throw VoiceFailure.conflict("VOICE_INTERPRETATION_INVALID");
                        try {
                            target = intents.requireTargetedCandidate(u, interpretationId, c);
                        } catch (com.uavusv.platform.module.voiceintelligence.AsrFailure e) {
                            throw VoiceFailure.conflict(e.code);
                        }
                        r.checkDevice(c, target.deviceCode(), target.commandType());
                    } else if (sequence) {
                        if (intents == null || interpretationId == null)
                            throw VoiceFailure.conflict("VOICE_INTERPRETATION_INVALID");
                        try {
                            target = intents.requireSequenceCandidate(u, interpretationId, c);
                        } catch (com.uavusv.platform.module.voiceintelligence.AsrFailure e) {
                            throw VoiceFailure.conflict(e.code);
                        }
                        r.check(c, "START", true);
                        r.checkSequenceTarget(c, target.deviceCode(), target.commandType());
                    } else {
                        r.check(c, action, true);
                    }
                    var p = sequence
                            ? newSequenceProposal(c, u, target.deviceCode(), target.commandType())
                            : targeted
                            ? newProposal(c, u, action, target.deviceCode(), target.commandType())
                            : newProposal(c, u, action);
                    if (interpretationId != null) p.put("interpretationId", interpretationId);
                    s.proposal(p);
                    s.remember(u, "propose", key, hash, p.path("_id").asText());
                    return new Reply(201, RuntimeContextRegistry.publicView(p));
                });
    }

    @PreAuthorize("hasRole('ADMIN')")
    public Reply confirm(String id, String key, ObjectNode body) {
        long u = a.user(true);
        settings.requireEnabled();
        VoiceJson.uuid(key);
        j.validate("ConfirmRequest", body);
        return s.locked(
                () -> {
                    settings.requireEnabled();
                    var p = owned("voice_proposal", id, u);
                    var c = r.require(p.path("plan").path("runtimeRef").asText(), u);
                    String op = "confirm:" + id, hash = j.hash(body);
                    String replay = s.replay(u, op, key, hash);
                    checkPlan(p, body);
                    if ("CONFIRMED".equals(p.path("status").asText())) {
                        if (replay == null) s.remember(u, op, key, hash, id);
                        return new Reply(200, combined(p));
                    }
                    pending(p);
                    var plan = (ObjectNode) p.path("plan");
                    if (!plan.path("runtimeGeneration").equals(c.path("runtimeGeneration"))) {
                        invalidate(p);
                        throw VoiceFailure.conflict("GENERATION_MISMATCH");
                    }
                    boolean targeted = "DEVICE_COMMAND".equals(plan.path("action").asText());
                    boolean sequence = "SEQUENCE".equals(plan.path("action").asText());
                    boolean frozenDevicesValid = targeted
                            ? plan.path("explicitDeviceCodes").size() == 1
                                && plan.path("explicitDeviceCodes").get(0).asText()
                                    .equals(plan.path("targetDeviceCode").asText())
                            : plan.path("explicitDeviceCodes").equals(c.path("_members"));
                    if (!POLICY_VERSION.equals(plan.path("policyVersion").asText())
                            || plan.path("contextVersion").asLong() != c.path("contextVersion").asLong()
                            || !frozenDevicesValid) {
                        invalidate(p);
                        throw VoiceFailure.conflict("CONTEXT_CHANGED");
                    }
                    if (targeted)
                        r.checkDevice(c, plan.path("targetDeviceCode").asText(), plan.path("deviceCommandType").asText());
                    else if (sequence) {
                        JsonNode step = plan.path("steps").path(1);
                        r.check(c, "START", true);
                        r.checkSequenceTarget(
                                c,
                                step.path("targetDeviceCode").asText(),
                                step.path("deviceCommandType").asText());
                    }
                    else r.check(c, plan.path("action").asText(), true);
                    if (s.busy(c.path("_id").asText()))
                        throw VoiceFailure.conflict("EXECUTION_IN_PROGRESS");
                    if (sequence) enqueueSequence(p, c);
                    else enqueue(p, c, false);
                    s.remember(u, op, key, hash, id);
                    return new Reply(202, combined(p));
                });
    }

    @PreAuthorize("hasRole('ADMIN')")
    public Reply cancel(String id, String key, ObjectNode body) {
        long u = a.user(true);
        settings.requireEnabled();
        VoiceJson.uuid(key);
        j.validate("CancelRequest", body);
        return s.locked(
                () -> {
                    settings.requireEnabled();
                    var p = owned("voice_proposal", id, u);
                    String op = "cancel:" + id, hash = j.hash(body);
                    String replay = s.replay(u, op, key, hash);
                    checkPlan(p, body);
                    if ("CONFIRMED".equals(p.path("status").asText()))
                        throw VoiceFailure.conflict("ALREADY_CONFIRMED");
                    if ("CANCELLED".equals(p.path("status").asText())) {
                        if (replay == null) s.remember(u, op, key, hash, id);
                        return new Reply(200, RuntimeContextRegistry.publicView(p));
                    }
                    pending(p);
                    p.put("status", "CANCELLED");
                    s.save("voice_proposal", p);
                    s.remember(u, op, key, hash, id);
                    return new Reply(200, RuntimeContextRegistry.publicView(p));
                });
    }

    /**
     * Existing authenticated manual route uses the same queue, without masquerading as a voice
     * confirmation.
     */
    @PreAuthorize("hasRole('ADMIN')")
    public ObjectNode manual(String ref, String action) {
        long u = a.user(true);
        if (!Set.of("START", "PAUSE", "RESUME", "STOP").contains(action))
            throw new VoiceFailure(422, "UNSUPPORTED_CAPABILITY");
        return s.locked(
                () -> {
                    var c = r.require(ref, u);
                    r.check(c, action, false);
                    if (s.busy(ref)) throw VoiceFailure.conflict("EXECUTION_IN_PROGRESS");
                    var p = newProposal(c, u, action);
                    p.put("_source", "MANUAL");
                    s.proposal(p);
                    enqueue(p, c, true);
                    s.audit(ref, "MANUAL_ACTION", action, t.stamp());
                    return combined(p);
                });
    }

    ObjectNode newProposal(ObjectNode c, long u, String action) {
        return newProposal(c, u, action, null, null);
    }

    ObjectNode newProposal(
            ObjectNode c, long u, String action, String targetDeviceCode, String deviceCommandType) {
        var plan = j.object();
        plan.put("runtimeRef", c.path("runtimeRef").asText())
                .put("runtimeGeneration", c.path("runtimeGeneration").asText());
        plan.set("contextVersion", c.path("contextVersion"));
        plan.set("stateVersion", c.path("stateVersion"));
        plan.put("action", action);
        if ("DEVICE_COMMAND".equals(action)) {
            plan.put("targetDeviceCode", targetDeviceCode)
                    .put("deviceCommandType", deviceCommandType);
            var targetOnly = j.mapper.createArrayNode().add(targetDeviceCode);
            plan.set("explicitDeviceCodes", targetOnly);
        } else {
            plan.set("explicitDeviceCodes", c.path("_members").deepCopy());
        }
        plan.put("policyVersion", POLICY_VERSION);
        var p = j.object();
        String id = VoiceJson.uuid();
        p.put("_id", id)
                .put("_owner", u)
                .put("proposalId", id)
                .put("status", "AWAITING_CONFIRMATION")
                .put("planVersion", 1)
                .put("planHash", j.hash(plan));
        p.set("plan", plan);
        p.put("requiresConfirmation", true)
                .put("createdAt", t.stamp())
                .put("expiresAt", t.now().plusSeconds(30).toString())
                .putNull("executionId");
        return p;
    }

    private ObjectNode newSequenceProposal(
            ObjectNode c, long u, String targetDeviceCode, String deviceCommandType) {
        var plan = j.object();
        plan.put("runtimeRef", c.path("runtimeRef").asText())
                .put("runtimeGeneration", c.path("runtimeGeneration").asText());
        plan.set("contextVersion", c.path("contextVersion"));
        plan.set("stateVersion", c.path("stateVersion"));
        plan.put("action", "SEQUENCE");
        plan.set("explicitDeviceCodes", c.path("_members").deepCopy());
        plan.put("policyVersion", POLICY_VERSION);
        var steps = j.mapper.createArrayNode();
        steps.addObject().put("index", 0).put("action", "START");
        steps.addObject()
                .put("index", 1)
                .put("action", "DEVICE_COMMAND")
                .put("targetDeviceCode", targetDeviceCode)
                .put("deviceCommandType", deviceCommandType);
        plan.set("steps", steps);
        var p = j.object();
        String id = VoiceJson.uuid();
        p.put("_id", id)
                .put("_owner", u)
                .put("proposalId", id)
                .put("status", "AWAITING_CONFIRMATION")
                .put("planVersion", 1)
                .put("planHash", j.hash(plan));
        p.set("plan", plan);
        p.put("requiresConfirmation", true)
                .put("createdAt", t.stamp())
                .put("expiresAt", t.now().plusSeconds(30).toString())
                .putNull("executionId");
        return p;
    }

    void enqueue(ObjectNode p, ObjectNode c, boolean manual) {
        String id = VoiceJson.uuid();
        var e = j.object();
        e.put("_id", id)
                .put("_owner", p.path("_owner").asLong())
                .put("_manual", manual)
                .put("_eventSequence", 0)
                .put("_queryCount", 0);
        e.set("_plan", p.path("plan").deepCopy());
        e.put("executionId", id)
                .put("proposalId", p.path("proposalId").asText())
                .put("commandId", VoiceJson.uuid());
        e.put("runtimeRef", c.path("runtimeRef").asText())
                .put("runtimeGeneration", c.path("runtimeGeneration").asText())
                .put("action", p.path("plan").path("action").asText());
        e.put("state", "QUEUED")
                .put("outcome", "UNKNOWN")
                .putNull("errorCode")
                .putNull("timedOutAt");
        e.put(
                "presentationStatus",
                Set.of("START", "RESUME").contains(e.path("action").asText())
                        ? "PENDING"
                        : "NOT_REQUIRED");
        e.put("createdAt", t.stamp()).put("updatedAt", t.stamp());
        s.execution(e);
        p.put("status", "CONFIRMED").put("executionId", id);
        s.save("voice_proposal", p);
    }

    private void enqueueSequence(ObjectNode p, ObjectNode c) {
        String id = VoiceJson.uuid();
        var e = j.object();
        e.put("_id", id)
                .put("_owner", p.path("_owner").asLong())
                .put("_manual", false)
                .put("_sequenceStartedAt", t.stamp());
        e.set("_plan", p.path("plan").deepCopy());
        e.put("executionId", id)
                .put("proposalId", p.path("proposalId").asText())
                .put("commandId", VoiceJson.uuid())
                .put("runtimeRef", c.path("runtimeRef").asText())
                .put("runtimeGeneration", c.path("runtimeGeneration").asText())
                .put("action", "SEQUENCE")
                .put("state", "EXECUTING")
                .put("outcome", "UNKNOWN")
                .putNull("errorCode")
                .putNull("timedOutAt")
                .put("presentationStatus", "NOT_REQUIRED")
                .put("currentStepIndex", 0)
                .put("sequenceStatus", "EXECUTING_START")
                .put("createdAt", t.stamp())
                .put("updatedAt", t.stamp());
        var steps = j.mapper.createArrayNode();
        for (JsonNode frozen : p.path("plan").path("steps")) {
            var step = frozen.deepCopy();
            ((ObjectNode) step).put("state", "PENDING").putNull("executionId").putNull("errorCode");
            steps.add(step);
        }
        e.set("steps", steps);
        s.execution(e, false);
        p.put("status", "CONFIRMED").put("executionId", id);
        s.save("voice_proposal", p);
    }

    String enqueueSequenceChild(ObjectNode parent, ObjectNode c, JsonNode frozenStep) {
        String action = frozenStep.path("action").asText();
        ObjectNode child = "DEVICE_COMMAND".equals(action)
                ? newProposal(
                        c,
                        parent.path("_owner").asLong(),
                        action,
                        frozenStep.path("targetDeviceCode").asText(),
                        frozenStep.path("deviceCommandType").asText())
                : newProposal(c, parent.path("_owner").asLong(), action);
        child.put("_source", "SEQUENCE_CHILD")
                .put("_parentExecutionId", parent.path("executionId").asText())
                .put("status", "CONFIRMED");
        s.proposal(child);
        enqueue(child, c, false);
        return child.path("executionId").asText();
    }

    private ObjectNode combined(ObjectNode p) {
        var result = j.object();
        result.set("proposal", RuntimeContextRegistry.publicView(p));
        result.set(
                "execution",
                executionView(s.get("voice_execution", p.path("executionId").asText())));
        return result;
    }

    private ObjectNode executionView(ObjectNode e) {
        if ("SUCCEEDED".equals(e.path("state").asText())
                && java.util.Set.of("START", "RESUME").contains(e.path("action").asText())
                && "PENDING".equals(e.path("presentationStatus").asText())) {
            String deadline = e.path("_presentationDeadlineAt").asText("");
            // Historical successes have no trustworthy start time: never invent a new window.
            if (deadline.isEmpty() || !t.now().isBefore(java.time.Instant.parse(deadline))) {
                e.put("presentationStatus", "STALE").put("updatedAt", t.stamp());
                s.save("voice_execution", e);
            }
        }
        var v = RuntimeContextRegistry.publicView(e);
        var c = s.get("voice_runtime_context", e.path("runtimeRef").asText());
        if ("REPORTED_APPLIED".equals(v.path("presentationStatus").asText())
                && (!r.freshScene(c) || !e.path("_presentationBinding").equals(c.path("_binding"))))
            v.put("presentationStatus", "STALE");
        return v;
    }

    private ObjectNode owned(String table, String id, long u) {
        VoiceJson.uuid(id);
        var n = s.get(table, id);
        if (n == null || n.path("_owner").asLong() != u)
            throw new VoiceFailure(404, "RESOURCE_NOT_FOUND");
        return n;
    }

    private void checkPlan(ObjectNode p, ObjectNode b) {
        if (b.path("expectedPlanVersion").asInt() != 1
                || !p.path("planHash").equals(b.path("expectedPlanHash")))
            throw VoiceFailure.conflict("PLAN_MISMATCH");
    }

    private void expire(ObjectNode p) {
        if ("AWAITING_CONFIRMATION".equals(p.path("status").asText())
                && !t.now().isBefore(Instant.parse(p.path("expiresAt").asText()))) {
            p.put("status", "EXPIRED");
            s.save("voice_proposal", p);
        }
    }

    private void pending(ObjectNode p) {
        expire(p);
        String status = p.path("status").asText();
        if (!"AWAITING_CONFIRMATION".equals(status))
            throw VoiceFailure.conflict("PROPOSAL_" + status);
    }

    private void invalidate(ObjectNode p) {
        p.put("status", "INVALIDATED");
        s.save("voice_proposal", p);
    }
}
