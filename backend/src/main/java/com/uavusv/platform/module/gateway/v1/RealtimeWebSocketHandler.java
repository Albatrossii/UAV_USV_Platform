package com.uavusv.platform.module.gateway.v1;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.ObjectReader;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

@Component
public class RealtimeWebSocketHandler extends TextWebSocketHandler {

    private static final int MAX_PROBE_MESSAGE_BYTES = 256;
    private static final Pattern PROBE_ID = Pattern.compile("[A-Za-z0-9._:-]{1,64}");

    private final Set<WebSocketSession> sessions = ConcurrentHashMap.newKeySet();
    private final ObjectMapper objectMapper;
    private final ObjectReader probeReader;

    public RealtimeWebSocketHandler(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
        this.probeReader = objectMapper.reader()
                .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                .with(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        sessions.add(session);
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        sessions.remove(session);
    }

    @Override
    public void handleTransportError(WebSocketSession session, Throwable exception) {
        sessions.remove(session);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        if (!message.isLast() || message.getPayloadLength() > MAX_PROBE_MESSAGE_BYTES) {
            return;
        }
        try {
            JsonNode probe = probeReader.readTree(message.getPayload());
            if (probe == null || !probe.isObject()
                    || !"realtime.ping".equals(probe.path("type").asText())) {
                return;
            }
            JsonNode id = probe.path("id");
            if (!id.isTextual() || !PROBE_ID.matcher(id.textValue()).matches()) {
                return;
            }
            // A probe measures this browser connection only; it never enters the gateway pipeline.
            var pong = objectMapper.createObjectNode()
                    .put("type", "realtime.pong")
                    .put("id", id.textValue());
            send(session, new TextMessage(objectMapper.writeValueAsString(pong)));
        } catch (Exception ignored) {
            // Unknown or malformed client messages must not affect realtime broadcasting.
        }
    }

    public void broadcast(GatewayEnvelope envelope) {
        if (sessions.isEmpty()) {
            return;
        }
        try {
            TextMessage message = new TextMessage(objectMapper.writeValueAsString(envelope));
            for (WebSocketSession session : sessions) {
                send(session, message);
            }
        } catch (Exception ignored) {
            // Broadcasting is best-effort; upstream gateway intake must not block on clients.
        }
    }

    private void send(WebSocketSession session, TextMessage message) {
        if (!session.isOpen()) {
            sessions.remove(session);
            return;
        }
        try {
            synchronized (session) {
                session.sendMessage(message);
            }
        } catch (Exception exception) {
            sessions.remove(session);
        }
    }
}
