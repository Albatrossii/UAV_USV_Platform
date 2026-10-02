package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import org.springframework.stereotype.Service;

import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/** Adapter used by the existing process manager; no alternate process ownership. */
@Service
public class VoiceRuntimeBridge {
    private final RuntimeContextRegistry registry;
    private final VoiceCommandApplicationService commands;
    private final VoiceDispatcher dispatcher;
    private final VoiceJson json;

    public VoiceRuntimeBridge(
            RuntimeContextRegistry registry,
            VoiceCommandApplicationService commands,
            VoiceDispatcher dispatcher,
            VoiceJson json) {
        this.registry = registry;
        this.commands = commands;
        this.dispatcher = dispatcher;
        this.json = json;
    }

    public ObjectNode register(long runId, String algorithmCode, JsonNode config) {
        return registry.register(runId, algorithmCode, configHash(config));
    }

    public String configHash(JsonNode config) {
        try {
            return java.util.HexFormat.of()
                    .formatHex(
                            java.security.MessageDigest.getInstance("SHA-256")
                                    .digest(
                                            config.toString()
                                                    .getBytes(
                                                            java.nio.charset.StandardCharsets
                                                                    .UTF_8)));
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    public void guard(ObjectNode context) {
        long user = registry.access.user(true);
        if (context == null || context.path("_owner").asLong() != user)
            throw VoiceFailure.conflict("RUNTIME_BUSY");
    }

    public void read(ObjectNode context) {
        if (context != null) registry.owner(context.path("runtimeRef").asText());
    }

    public boolean v1(ObjectNode context) {
        return context != null
                && RuntimeContextRegistry.PROTOCOL.equals(context.path("protocolVersion").asText());
    }

    public void attach(ObjectNode c, Consumer<JsonNode> send, BooleanSupplier alive) {
        registry.attach(
                c.path("runtimeRef").asText(), c.path("runtimeGeneration").asText(), send, alive);
    }

    public void event(ObjectNode c, JsonNode event) {
        dispatcher.receive(
                c.path("runtimeRef").asText(), c.path("runtimeGeneration").asText(), event);
    }

    public void frame(ObjectNode c, JsonNode frame) {
        dispatcher.frame(
                c.path("runtimeRef").asText(), c.path("runtimeGeneration").asText(), frame);
    }

    public void ended(ObjectNode c) {
        if (c != null)
            dispatcher.ended(c.path("runtimeRef").asText(), c.path("runtimeGeneration").asText());
    }

    public void manual(ObjectNode c, String action) {
        commands.manual(c.path("runtimeRef").asText(), action);
    }

    /** Read one authorized persisted snapshot, not the pre-handshake registration object. */
    public ObjectNode statusSnapshot(ObjectNode c) {
        if (c == null) return null;
        return registry.store.locked(() -> {
            var current = registry.require(c.path("runtimeRef").asText(), registry.access.user(false));
            if (!c.path("runtimeGeneration").equals(current.path("runtimeGeneration")))
                throw VoiceFailure.conflict("GENERATION_MISMATCH");
            return current.deepCopy();
        });
    }

    public String state(ObjectNode c) {
        return registry.store.locked(
                () ->
                        registry.store
                                .get("voice_runtime_context", c.path("runtimeRef").asText())
                                .path("state")
                                .asText());
    }

    public JsonNode parse(String line) { return json.read(line); }

    public void malformed(ObjectNode c) { event(c, json.object().put("kind", "MALFORMED")); }

    public boolean ready(ObjectNode c, JsonNode event) {
        try {
            json.validate("RuntimeReady", event);
            return c.path("runtimeRef").equals(event.path("runtimeRef"))
                    && c.path("runtimeGeneration").equals(event.path("runtimeGeneration"));
        } catch (VoiceFailure e) {
            return false;
        }
    }
}
