package com.uavusv.platform.module.monitoring.controller;

import com.uavusv.platform.common.api.ApiResponse;
import com.uavusv.platform.common.exception.BusinessException;
import com.uavusv.platform.common.exception.ErrorCode;
import com.uavusv.platform.module.monitoring.dto.request.IntegrationHeartbeatRequest;
import com.uavusv.platform.module.monitoring.service.RuntimeStateService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

/** Browser heartbeats use the authenticated session and normal CSRF validation. */
@RestController
@RequestMapping("/api/monitoring/unity-heartbeat")
public class BrowserUnityHeartbeatController {
    private final RuntimeStateService runtimeStateService;

    public BrowserUnityHeartbeatController(RuntimeStateService runtimeStateService) {
        this.runtimeStateService = runtimeStateService;
    }

    @PostMapping
    public ApiResponse<Map<String, Boolean>> heartbeat(
            @Valid @RequestBody IntegrationHeartbeatRequest request,
            HttpServletRequest servletRequest) {
        if (!RuntimeStateService.UNITY_CODE.equals(request.componentCode())) {
            throw new BusinessException(ErrorCode.BAD_REQUEST, "Unsupported component code");
        }
        runtimeStateService.observeUnityHeartbeat(request, servletRequest.getRemoteAddr());
        return ApiResponse.success(Map.of("accepted", true));
    }
}
