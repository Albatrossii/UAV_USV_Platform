package com.uavusv.platform.module.voiceintelligence;

import jakarta.servlet.http.*;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.*;
import java.util.*;
import java.util.concurrent.*;

@RestController
public class AsrController {
    private final AsrService service;
    private final IntentService intents;

    public AsrController(
            AsrService s,
            org.springframework.beans.factory.ObjectProvider<IntentService> intentProvider) {
        service = s;
        this.intents = intentProvider.getIfAvailable();
    }

    @PostMapping(AsrIngressFilter.PATH)
    public ResponseEntity<Map<String, Object>> transcribe(HttpServletRequest request)
            throws IOException {
        long user = service.authorize();
        String id = request.getHeader("X-Request-ID"), key = request.getHeader("Idempotency-Key");
        if (id == null
                || !AudioMultipart.UUID.matcher(id).matches()
                || !id.equals(key)
                || Collections.list(request.getHeaders("X-Request-ID")).size() != 1
                || Collections.list(request.getHeaders("Idempotency-Key")).size() != 1
                || request.getQueryString() != null) throw AsrFailure.invalid();
        if (request.getContentLengthLong() > AudioMultipart.MAX_BODY)
            throw new AsrFailure(413, "VOICE_AUDIO_TOO_LARGE");
        byte[] body = (byte[]) request.getAttribute(AsrUploadFilter.BODY);
        Long deadline = (Long) request.getAttribute(AsrUploadFilter.DEADLINE);
        if (body == null || deadline == null) throw AsrFailure.invalid();
        var a = AudioMultipart.parse((String) request.getAttribute(AsrIngressFilter.TYPE), body);
        if (!id.equals(a.requestId())) throw AsrFailure.invalid();
        String selected = request.getHeader("X-ASR-Provider");
        if (Collections.list(request.getHeaders("X-ASR-Provider")).size() > 1
                || (selected != null && !Set.of("local", "aliyun").contains(selected)))
            throw AsrFailure.invalid();
        if (selected != null)
            a = new SpeechProvider.Audio(a.requestId(), a.locale(), a.mime(), a.bytes(), selected);
        return AsrResponses.response(service.transcribe(user, a, deadline));
    }

    @PostMapping("/api/voice/intelligence/interpretations")
    public ResponseEntity<Map<String, Object>> interpret(
            HttpServletRequest request, @RequestBody com.fasterxml.jackson.databind.JsonNode body) {
        long user = service.authorize();
        if (intents == null) throw new AsrFailure(503, "VOICE_INTELLIGENCE_DISABLED");
        String id = request.getHeader("X-Request-ID");
        if (id == null
                || !id.equals(request.getHeader("Idempotency-Key"))
                || Collections.list(request.getHeaders("X-Request-ID")).size() != 1
                || Collections.list(request.getHeaders("Idempotency-Key")).size() != 1
                || request.getQueryString() != null) throw AsrFailure.invalid();
        return AsrResponses.response(intents.interpret(user, id, body), id);
    }
}
