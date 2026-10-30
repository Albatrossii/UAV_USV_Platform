package com.uavusv.platform.module.monitoring.integration;

import com.uavusv.platform.common.api.ApiResponse;
import com.uavusv.platform.module.monitoring.dto.request.IntegrationHeartbeatRequest;
import com.uavusv.platform.module.monitoring.service.RuntimeStateService;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandAckRequest;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandResponse;
import com.uavusv.platform.module.runtimecontrol.service.RuntimeControlService;
import com.uavusv.platform.module.sensor.service.SensorRuntimeService;
import com.fasterxml.jackson.databind.JsonNode;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.beans.factory.annotation.Autowired;
import com.uavusv.platform.common.exception.BusinessException;
import com.uavusv.platform.common.exception.ErrorCode;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

@RestController
@RequestMapping("/api/integration")
public class IntegrationController {

    private final RuntimeStateService runtimeStateService;
    private final RuntimeControlService runtimeControlService;
    private final SensorRuntimeService sensorRuntimeService;
    private final byte[] expectedToken;

    @Autowired
    public IntegrationController(
            RuntimeStateService runtimeStateService,
            RuntimeControlService runtimeControlService,
            SensorRuntimeService sensorRuntimeService,
            @Value("${app.integration.token}") String expectedToken
    ) {
        this.runtimeStateService = runtimeStateService;
        this.runtimeControlService = runtimeControlService;
        this.sensorRuntimeService = sensorRuntimeService;
        this.expectedToken = expectedToken.getBytes(StandardCharsets.UTF_8);
    }

    /** Keeps lightweight standalone controller tests source-compatible. */
    public IntegrationController(
            RuntimeStateService runtimeStateService,
            RuntimeControlService runtimeControlService,
            String expectedToken
    ) {
        this(runtimeStateService, runtimeControlService, new SensorRuntimeService(), expectedToken);
    }

    @PostMapping("/san60/spectrum")
    public ApiResponse<Map<String, Boolean>> receiveSan60Spectrum(
            @RequestHeader(value = "X-Platform-Token", required = false) String token,
            @RequestBody JsonNode frame
    ) {
        verifyToken(token);
        if (!"spectrum_frame".equals(frame.path("message_type").asText())) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "Expected spectrum_frame");
        }
        JsonNode data = frame.path("data").isObject()
                ? frame.path("data")
                : frame.path("frame").path("data");
        if (!data.isObject() || !data.path("powers_dbm").isArray() || data.path("powers_dbm").isEmpty()) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "Spectrum data and powers_dbm are required");
        }
        sensorRuntimeService.observeSpectrumFrame(frame);
        return ApiResponse.success(Map.of("accepted", true));
    }

    @PostMapping("/heartbeat")
    public ApiResponse<Map<String, Boolean>> heartbeat(
            @RequestHeader(value = "X-Platform-Token", required = false) String token,
            @Valid @RequestBody IntegrationHeartbeatRequest request,
            HttpServletRequest servletRequest
    ) {
        verifyToken(token);
        if (!RuntimeStateService.UNITY_CODE.equals(request.componentCode())) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "Unsupported component code");
        }

        runtimeStateService.observeUnityHeartbeat(request, servletRequest.getRemoteAddr());
        return ApiResponse.success(Map.of("accepted", true));
    }

    @PostMapping("/commands/{commandKey}/ack")
    public ApiResponse<RuntimeCommandResponse> acknowledgeCommand(
            @RequestHeader(value = "X-Platform-Token", required = false) String token,
            @PathVariable String commandKey,
            @Valid @RequestBody RuntimeCommandAckRequest request
    ) {
        verifyToken(token);
        return ApiResponse.success(runtimeControlService.acknowledgeCommand(commandKey, request));
    }

    private void verifyToken(String token) {
        if (token == null || !MessageDigest.isEqual(expectedToken, token.getBytes(StandardCharsets.UTF_8))) {
            throw new BusinessException(ErrorCode.UNAUTHORIZED, "Invalid integration token");
        }
    }
}
