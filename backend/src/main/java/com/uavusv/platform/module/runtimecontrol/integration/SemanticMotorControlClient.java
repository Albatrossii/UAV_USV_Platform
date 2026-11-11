package com.uavusv.platform.module.runtimecontrol.integration;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.runtimecontrol.dispatch.CommandDispatchResult;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandRequest;
import com.uavusv.platform.module.runtimecontrol.entity.CommandType;
import com.uavusv.platform.module.runtimecontrol.entity.RuntimeScope;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Locale;

@Component
public class SemanticMotorControlClient {

    static final String PHYSICAL_DEVICE_CODE = "uav-01";
    static final String SYSTEM_OVERVIEW_INSTANCE = "overview-unity-01";

    private final boolean enabled;
    private final String gatewayUrl;
    private final String gatewayToken;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;

    public SemanticMotorControlClient(
            @Value("${app.motor-control.enabled:false}") boolean enabled,
            @Value("${app.motor-control.gateway-url:}") String gatewayUrl,
            @Value("${app.motor-control.gateway-token:}") String gatewayToken,
            ObjectMapper objectMapper
    ) {
        this.enabled = enabled;
        this.gatewayUrl = gatewayUrl == null ? "" : gatewayUrl.strip().replaceAll("/+$", "");
        this.gatewayToken = gatewayToken == null ? "" : gatewayToken.strip();
        this.objectMapper = objectMapper;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .build();
    }

    public boolean supports(RuntimeCommandRequest request) {
        return request.commandType() == CommandType.UAV_TAKEOFF
                && request.runtimeScope() == RuntimeScope.SYSTEM_OVERVIEW
                && SYSTEM_OVERVIEW_INSTANCE.equals(request.runtimeInstanceId())
                && PHYSICAL_DEVICE_CODE.equals(normalize(request.deviceCode()));
    }

    public CommandDispatchResult dispatch(String commandKey, RuntimeCommandRequest request) throws Exception {
        if (!supports(request)) {
            return CommandDispatchResult.rejected(
                    "MOTOR_CONTROL_SCOPE_MISMATCH",
                    "该指令不属于真实机载电脑的系统总览电机测试范围"
            );
        }
        if (!enabled) {
            return CommandDispatchResult.rejected(
                    "MOTOR_CONTROL_DISABLED",
                    "真实机载电脑电机控制网关未启用"
            );
        }
        if (gatewayUrl.isBlank() || gatewayToken.isBlank()) {
            return CommandDispatchResult.rejected(
                    "MOTOR_CONTROL_NOT_CONFIGURED",
                    "真实机载电脑电机控制网关未配置"
            );
        }

        ObjectNode payload = objectMapper.createObjectNode();
        payload.put("command_id", commandKey);
        payload.put("action", "MOTOR_TEST_5S");
        payload.put("device_code", PHYSICAL_DEVICE_CODE);

        HttpRequest httpRequest = HttpRequest.newBuilder()
                .uri(URI.create(gatewayUrl + "/v1/motor-test"))
                .timeout(Duration.ofSeconds(15))
                .header("Authorization", "Bearer " + gatewayToken)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(payload)))
                .build();

        HttpResponse<String> response;
        try {
            response = httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString());
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw exception;
        }

        JsonNode body;
        try {
            body = objectMapper.readTree(response.body());
        } catch (Exception exception) {
            body = objectMapper.createObjectNode();
        }
        String detail = body.path("detail").asText("语义链路电机控制网关返回 HTTP " + response.statusCode());
        if (response.statusCode() >= 200 && response.statusCode() < 300 && body.path("success").asBoolean(false)) {
            return CommandDispatchResult.acknowledged(detail);
        }
        String errorCode = body.path("error_code").asText("MOTOR_CONTROL_GATEWAY_REJECTED");
        return CommandDispatchResult.rejected(errorCode, detail);
    }

    private String normalize(String value) {
        return value == null ? "" : value.strip().toLowerCase(Locale.ROOT);
    }
}
