package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.node.ObjectNode;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/voice")
public class VoiceController {
    private final VoiceCommandApplicationService app;
    private final VoicePresentationService presentation;
    private final SystemOverviewVoiceContextService systemOverviewContexts;
    private final VoiceJson json;
    private final VoiceTime time;

    public VoiceController(
            VoiceCommandApplicationService app,
            VoicePresentationService presentation,
            SystemOverviewVoiceContextService systemOverviewContexts,
            VoiceJson json,
            VoiceTime time) {
        this.app = app;
        this.presentation = presentation;
        this.systemOverviewContexts = systemOverviewContexts;
        this.json = json;
        this.time = time;
    }

    private ResponseEntity<ObjectNode> response(int status, Object data) {
        var n = json.object();
        n.put("code", "SUCCESS").put("message", "操作成功");
        n.set("data", json.mapper.valueToTree(data));
        n.put("timestamp", time.stamp());
        return ResponseEntity.status(status).header("Cache-Control", "no-store").body(n);
    }

    private ResponseEntity<ObjectNode> response(VoiceCommandApplicationService.Reply r) {
        return response(r.status(), r.data());
    }

    @GetMapping("/algorithm-presets")
    public ResponseEntity<ObjectNode> presets() { return response(200, AlgorithmPresets.catalog(json)); }

    @GetMapping("/contexts")
    public ResponseEntity<ObjectNode> contexts() {
        return response(200, app.contexts());
    }

    @GetMapping("/contexts/{ref}")
    public ResponseEntity<ObjectNode> context(@PathVariable String ref) {
        return response(200, app.context(ref));
    }

    @PostMapping(value = "/contexts/system-overview/sync", consumes = "application/json")
    public ResponseEntity<ObjectNode> syncSystemOverview(@RequestBody String body) {
        return response(200, systemOverviewContexts.sync(json.read(body)));
    }

    @GetMapping("/commands/{id}")
    public ResponseEntity<ObjectNode> proposal(@PathVariable String id) {
        return response(200, app.proposal(id));
    }

    @GetMapping("/executions/{id}")
    public ResponseEntity<ObjectNode> execution(@PathVariable String id) {
        return response(200, app.execution(id));
    }

    @PostMapping(value = "/commands/proposals", consumes = "application/json")
    public ResponseEntity<ObjectNode> create(
            @RequestHeader("Idempotency-Key") String key,
            @RequestHeader(value = "X-Voice-Interpretation-ID", required = false) String interpretationId,
            @RequestBody String body) {
        return response(app.createProposal(key, interpretationId, json.read(body)));
    }

    @PostMapping(value = "/commands/{id}/confirm", consumes = "application/json")
    public ResponseEntity<ObjectNode> confirm(
            @PathVariable String id,
            @RequestHeader("Idempotency-Key") String key,
            @RequestBody String body) {
        return response(app.confirm(id, key, json.read(body)));
    }

    @PostMapping(value = "/commands/{id}/cancel", consumes = "application/json")
    public ResponseEntity<ObjectNode> cancel(
            @PathVariable String id,
            @RequestHeader("Idempotency-Key") String key,
            @RequestBody String body) {
        return response(app.cancel(id, key, json.read(body)));
    }

    @GetMapping("/contexts/{ref}/presentation/binding")
    public ResponseEntity<ObjectNode> binding(@PathVariable String ref) {
        return response(200, presentation.binding(ref));
    }

    @PostMapping(value = "/contexts/{ref}/presentation/bindings", consumes = "application/json")
    public ResponseEntity<ObjectNode> bind(
            @PathVariable String ref,
            @RequestHeader("Idempotency-Key") String key,
            @RequestBody String body) {
        return response(200, presentation.bind(ref, key, json.read(body)));
    }

    @PostMapping(value = "/contexts/{ref}/presentation/challenges", consumes = "application/json")
    public ResponseEntity<ObjectNode> challenge(
            @PathVariable String ref,
            @RequestHeader("Idempotency-Key") String key,
            @RequestBody String body) {
        return response(200, presentation.challenge(ref, key, json.read(body)));
    }

    @PostMapping(value = "/contexts/{ref}/presentation/reports", consumes = "application/json")
    public ResponseEntity<ObjectNode> report(
            @PathVariable String ref,
            @RequestHeader("Idempotency-Key") String key,
            @RequestBody String body) {
        return response(200, presentation.report(ref, key, json.read(body)));
    }
}
