package com.uavusv.platform.module.gateway.v1;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import java.io.IOException;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RealtimeWebSocketHandlerTests {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final RealtimeWebSocketHandler handler = new RealtimeWebSocketHandler(objectMapper);

    @Test
    void repliesOnlyToTheProbingSessionWithoutEchoingExtraPayload() throws Exception {
        WebSocketSession requester = connectedSession();
        WebSocketSession observer = connectedSession();

        handler.handleMessage(requester, new TextMessage("""
                {"type":"realtime.ping","id":"probe-1:abc_2.3","payload":{"command":"ignored"}}
                """));

        ArgumentCaptor<TextMessage> reply = ArgumentCaptor.forClass(TextMessage.class);
        verify(requester).sendMessage(reply.capture());
        assertThat(objectMapper.readTree(reply.getValue().getPayload()))
                .isEqualTo(objectMapper.readTree("""
                        {"type":"realtime.pong","id":"probe-1:abc_2.3"}
                        """));
        verify(observer, never()).sendMessage(any());
    }

    @Test
    void acceptsMaximumLengthProbeId() throws Exception {
        WebSocketSession session = connectedSession();
        String id = "a".repeat(64);

        handler.handleMessage(session, new TextMessage("{\"type\":\"realtime.ping\",\"id\":\"" + id + "\"}"));

        ArgumentCaptor<TextMessage> reply = ArgumentCaptor.forClass(TextMessage.class);
        verify(session).sendMessage(reply.capture());
        assertThat(objectMapper.readTree(reply.getValue().getPayload()).path("id").textValue()).isEqualTo(id);
    }

    @ParameterizedTest
    @MethodSource("invalidMessages")
    void ignoresInvalidOrUnknownMessagesWithoutAffectingBroadcast(String payload) throws Exception {
        WebSocketSession requester = connectedSession();
        WebSocketSession observer = connectedSession();

        assertThatCode(() -> handler.handleMessage(requester, new TextMessage(payload))).doesNotThrowAnyException();

        verify(requester, never()).sendMessage(any());
        verify(observer, never()).sendMessage(any());
        GatewayEnvelope envelope = telemetryEnvelope();
        handler.broadcast(envelope);
        TextMessage broadcast = new TextMessage(objectMapper.writeValueAsString(envelope));
        verify(requester).sendMessage(broadcast);
        verify(observer).sendMessage(broadcast);
    }

    @Test
    void ignoresPartialProbeMessages() throws Exception {
        WebSocketSession session = connectedSession();

        handler.handleMessage(session, new TextMessage("{\"type\":\"realtime.ping\",\"id\":\"1\"}", false));

        verify(session, never()).sendMessage(any());
    }

    @Test
    void usesTheSameSessionLockForProbeRepliesAndBroadcasts() throws Exception {
        WebSocketSession session = connectedSession();
        doAnswer(invocation -> {
            assertThat(Thread.holdsLock(session)).isTrue();
            return null;
        }).when(session).sendMessage(any());

        handler.handleMessage(session, new TextMessage("{\"type\":\"realtime.ping\",\"id\":\"1\"}"));
        handler.broadcast(telemetryEnvelope());

        verify(session, times(2)).sendMessage(any());
    }

    @Test
    void removesFailedProbeSessionAndKeepsOtherBroadcastRecipients() throws Exception {
        WebSocketSession requester = connectedSession();
        WebSocketSession observer = connectedSession();
        doThrow(new IOException("closed transport")).when(requester).sendMessage(any());

        assertThatCode(() -> handler.handleMessage(requester,
                new TextMessage("{\"type\":\"realtime.ping\",\"id\":\"1\"}"))).doesNotThrowAnyException();
        handler.broadcast(telemetryEnvelope());

        verify(requester).sendMessage(any());
        verify(observer).sendMessage(any());
    }

    @Test
    void doesNotSendProbeReplyToClosedSession() throws Exception {
        WebSocketSession session = connectedSession();
        when(session.isOpen()).thenReturn(false);

        handler.handleMessage(session, new TextMessage("{\"type\":\"realtime.ping\",\"id\":\"1\"}"));
        handler.broadcast(telemetryEnvelope());

        verify(session, never()).sendMessage(any());
    }

    private WebSocketSession connectedSession() {
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.isOpen()).thenReturn(true);
        handler.afterConnectionEstablished(session);
        return session;
    }

    private GatewayEnvelope telemetryEnvelope() {
        return new GatewayEnvelope("1.0", GatewayMessageType.TELEMETRY_TARGET_BATCH, "ros", null,
                "run-1", "fleet.targets.epoch-a", 1, objectMapper.createObjectNode());
    }

    private static Stream<String> invalidMessages() {
        return Stream.of(
                "", "not-json", "null", "[]", "42", "{}",
                "{\"type\":\"control.command\",\"id\":\"1\"}",
                "{\"type\":\"realtime.pong\",\"id\":\"1\"}",
                "{\"type\":\"realtime.ping\"}",
                "{\"type\":\"realtime.ping\",\"id\":null}",
                "{\"type\":\"realtime.ping\",\"id\":1}",
                "{\"type\":\"realtime.ping\",\"id\":{}}",
                "{\"type\":\"realtime.ping\",\"id\":\"\"}",
                "{\"type\":\"realtime.ping\",\"id\":\"has space\"}",
                "{\"type\":\"realtime.ping\",\"id\":\"bad\\nline\"}",
                "{\"type\":\"realtime.ping\",\"id\":\"" + "a".repeat(65) + "\"}",
                "{\"type\":\"realtime.ping\",\"id\":\"1\",\"padding\":\"" + "a".repeat(256) + "\"}",
                "{\"type\":\"realtime.ping\",\"id\":\"1\"} {}",
                "{\"type\":\"realtime.ping\",\"id\":\"1\"} invalid",
                "{\"type\":\"realtime.ping\",\"id\":\"1\",\"id\":\"2\"}");
    }
}
