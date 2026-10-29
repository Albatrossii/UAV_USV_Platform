package com.uavusv.platform.module.gimbalcamera;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.module.gimbalcamera.dto.GimbalCameraCommandRequest;
import com.uavusv.platform.module.gimbalcamera.service.GimbalCameraService;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GimbalCameraServiceTests {
    @Test
    void disabledGatewayReportsOfflineAndDoesNotSendCommands() {
        var service = new GimbalCameraService(new ObjectMapper(), false, "", "");

        assertFalse(service.status().configured());
        assertFalse(service.status().connected());
        var error = assertThrows(ResponseStatusException.class,
                () -> service.send(new GimbalCameraCommandRequest("PTZ_HOME", Map.of())));
        assertEquals(503, error.getStatusCode().value());
    }

    @Test
    void rejectsCommandsOutsideAllowlist() {
        var service = new GimbalCameraService(new ObjectMapper(), true, "http://127.0.0.1:1", "");
        var error = assertThrows(ResponseStatusException.class,
                () -> service.send(new GimbalCameraCommandRequest("FIRMWARE_UPGRADE", Map.of())));
        assertEquals(400, error.getStatusCode().value());
    }

    @Test
    void readsAuthenticatedStatusWhileControlIsDisabled() throws Exception {
        var receivedToken = new AtomicReference<String>();
        var commandCalls = new AtomicInteger();
        var adapterControlEnabled = new AtomicBoolean(false);
        var safetyFault = new AtomicReference<String>();
        var commandResponseStatus = new AtomicInteger(500);
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/status", exchange -> {
            receivedToken.set(exchange.getRequestHeaders().getFirst("X-Adapter-Token"));
            var body = ("{\"configured\":true,\"connected\":true,"
                    + "\"deviceName\":\"gimbal_01\",\"controlProtocol\":\"A10TR\","
                    + "\"streamProtocol\":\"image/jpeg\",\"playbackUrl\":null,"
                    + "\"yawDegrees\":null,\"pitchDegrees\":null,\"rollDegrees\":null,"
                    + "\"opticalZoom\":null,\"infraredZoom\":null,\"imageMode\":null,"
                    + "\"detail\":\"test status\",\"tcpReachable\":true,"
                    + "\"tcpSessionOpen\":false,\"reachabilityDetail\":\"probe succeeded\","
                    + "\"enabled\":true,\"controlEnabled\":" + adapterControlEnabled.get() + ",\"motionActive\":false,"
                    + "\"watchdogTimeoutSec\":0.8,\"safetyFault\":" + (safetyFault.get() == null ? "null" : "\"" + safetyFault.get() + "\"") + ","
                    + "\"protocol\":\"A10TR TCP wrapper\","
                    + "\"supportedCommands\":[\"PTZ_MOVE\",\"PTZ_STOP\",\"PTZ_HOME\"],"
                    + "\"zoomControlEnabled\":false}").getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, body.length);
            try (var output = exchange.getResponseBody()) {
                output.write(body);
            }
        });
        server.createContext("/commands", exchange -> {
            commandCalls.incrementAndGet();
            exchange.sendResponseHeaders(commandResponseStatus.get(), -1);
            exchange.close();
        });
        server.start();
        try {
            var url = "http://127.0.0.1:" + server.getAddress().getPort();
            var service = new GimbalCameraService(new ObjectMapper(), false, url, "unit-test-token");

            var status = service.status();

            assertFalse(status.configured());
            assertTrue(status.connected());
            assertFalse(status.controlEnabled());
            assertFalse(status.zoomControlEnabled());
            assertTrue(status.supportedCommands().contains("PTZ_HOME"));
            assertEquals(0.8, status.watchdogTimeoutSec());
            assertTrue(status.detail().contains("控制开关未启用"));
            assertEquals("unit-test-token", receivedToken.get());
            assertEquals(0, commandCalls.get());
            var error = assertThrows(ResponseStatusException.class,
                    () -> service.send(new GimbalCameraCommandRequest("PTZ_HOME", Map.of())));
            assertEquals(503, error.getStatusCode().value());
            assertEquals(0, commandCalls.get());

            var platformEnabledService = new GimbalCameraService(
                    new ObjectMapper(), true, url, "unit-test-token");
            var adapterDisabledError = assertThrows(ResponseStatusException.class,
                    () -> platformEnabledService.send(new GimbalCameraCommandRequest("PTZ_HOME", Map.of())));
            assertEquals(503, adapterDisabledError.getStatusCode().value());
            assertEquals(0, commandCalls.get());

            adapterControlEnabled.set(true);
            var unsupportedError = assertThrows(ResponseStatusException.class,
                    () -> platformEnabledService.send(new GimbalCameraCommandRequest("PTZ_GOTO", Map.of())));
            assertEquals(400, unsupportedError.getStatusCode().value());
            assertEquals(0, commandCalls.get());

            safetyFault.set("watchdog stop failed");
            var safetyError = assertThrows(ResponseStatusException.class,
                    () -> platformEnabledService.send(new GimbalCameraCommandRequest("PTZ_HOME", Map.of())));
            assertEquals(503, safetyError.getStatusCode().value());
            assertTrue(safetyError.getReason().contains("安全故障"));
            assertEquals(0, commandCalls.get());
            var stopAttemptError = assertThrows(ResponseStatusException.class,
                    () -> platformEnabledService.send(new GimbalCameraCommandRequest("PTZ_STOP", Map.of())));
            assertEquals(502, stopAttemptError.getStatusCode().value());
            assertEquals(1, commandCalls.get());

            safetyFault.set(null);
            commandResponseStatus.set(400);
            var invalidParameterError = assertThrows(ResponseStatusException.class,
                    () -> platformEnabledService.send(new GimbalCameraCommandRequest("PTZ_MOVE", Map.of("direction", "SIDEWAYS", "speed", 3))));
            assertEquals(400, invalidParameterError.getStatusCode().value());
            assertEquals(2, commandCalls.get());

            commandResponseStatus.set(503);
            var adapterDisabledCommandError = assertThrows(ResponseStatusException.class,
                    () -> platformEnabledService.send(new GimbalCameraCommandRequest("PTZ_HOME", Map.of())));
            assertEquals(503, adapterDisabledCommandError.getStatusCode().value());
            assertEquals(3, commandCalls.get());
        } finally {
            server.stop(0);
        }
    }
}
