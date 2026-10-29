package com.uavusv.platform.module.gimbalcamera.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.module.gimbalcamera.dto.GimbalCameraCommandRequest;
import com.uavusv.platform.module.gimbalcamera.dto.GimbalCameraCommandResponse;
import com.uavusv.platform.module.gimbalcamera.dto.GimbalCameraStatusResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class GimbalCameraService {
    private static final Set<String> ALLOWED = Set.of(
            "PTZ_MOVE", "PTZ_STOP", "PTZ_HOME", "PTZ_GOTO", "PTZ_DOWN", "PTZ_FPV",
            "COPY_COORDINATES", "IMAGE_MODE", "EO_ZOOM", "IR_ZOOM", "IR_DZOOM",
            "EO_FOCUS", "IR_FOCUS", "PIP", "GRID", "COMPASS", "KLV",
            "SNAPSHOT", "RECORD_START", "RECORD_STOP");
    private static final String TOKEN_HEADER = "X-Adapter-Token";

    private final ObjectMapper mapper;
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
    private final boolean enabled;
    private final String gateway;
    private final String gatewayToken;

    public GimbalCameraService(
            ObjectMapper mapper,
            @Value("${app.gimbal-camera.enabled:false}") boolean enabled,
            @Value("${app.gimbal-camera.gateway-url:}") String gateway,
            @Value("${app.gimbal-camera.gateway-token:}") String gatewayToken) {
        this.mapper = mapper;
        this.enabled = enabled;
        this.gateway = gateway == null ? "" : gateway.replaceAll("/+$", "");
        this.gatewayToken = gatewayToken == null ? "" : gatewayToken;
    }

    public GimbalCameraStatusResponse status() {
        if (gateway.isBlank()) {
            return unavailableStatus(false, false, "云台协议网关未配置；请配置网关地址");
        }
        try {
            var adapterStatus = fetchAdapterStatus();
            var adapterEnabled = adapterStatus.path("enabled").asBoolean(false);
            var tcpReachable = adapterStatus.path("tcpReachable").asBoolean(false);
            var controlEnabled = adapterStatus.path("controlEnabled").asBoolean(false);
            var motionActive = adapterStatus.path("motionActive").asBoolean(false);
            var watchdogTimeoutSec = adapterStatus.path("watchdogTimeoutSec").asDouble(0.0);
            var safetyFaultNode = adapterStatus.get("safetyFault");
            var safetyFault = safetyFaultNode == null || safetyFaultNode.isNull()
                    ? null : safetyFaultNode.asText();
            var supportedCommands = new ArrayList<String>();
            var supportedNode = adapterStatus.path("supportedCommands");
            if (supportedNode.isArray()) {
                supportedNode.forEach(command -> supportedCommands.add(command.asText()));
            }
            var detail = adapterStatus.path("reachabilityDetail").asText("TCP 状态未知");
            detail += adapterStatus.path("tcpSessionOpen").asBoolean(false)
                    ? "；TCP 会话已建立" : "；当前为连通性探测，尚无持久 TCP 会话";
            if (!adapterEnabled || !controlEnabled) {
                detail += "；适配器控制开关未启用";
            }
            if (!enabled) {
                detail += "；平台控制开关未启用";
            }
            if (safetyFault != null && !safetyFault.isBlank()) {
                detail += "；安全故障：" + safetyFault;
            }
            return new GimbalCameraStatusResponse(
                    enabled && !gateway.isBlank() && adapterEnabled,
                    tcpReachable,
                    "gimbal_01",
                    adapterStatus.path("protocol").asText("A10TR TCP"),
                    "Gateway WebSocket · image/jpeg",
                    null, null, null, null, null, null, null, detail,
                    controlEnabled, motionActive, watchdogTimeoutSec, safetyFault,
                    List.copyOf(supportedCommands), adapterStatus.path("zoomControlEnabled").asBoolean(false));
        } catch (Exception e) {
            return unavailableStatus(false, false, "云台协议适配器状态读取失败");
        }
    }

    public GimbalCameraCommandResponse send(GimbalCameraCommandRequest command) {
        if (command == null || command.command() == null || !ALLOWED.contains(command.command())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "不支持的云台相机指令");
        }
        if (!enabled || gateway.isBlank()) {
            throw new ResponseStatusException(
                    HttpStatus.SERVICE_UNAVAILABLE, "云台相机控制尚未启用，未发送设备指令");
        }
        try {
            var adapterStatus = fetchAdapterStatus();
            var safetyFaultNode = adapterStatus.get("safetyFault");
            var hasSafetyFault = safetyFaultNode != null && !safetyFaultNode.isNull()
                    && !safetyFaultNode.asText().isBlank();
            if (!adapterStatus.path("enabled").asBoolean(false)
                    || !adapterStatus.path("controlEnabled").asBoolean(false)
                    || !adapterStatus.path("tcpReachable").asBoolean(false)
                    || (hasSafetyFault && !"PTZ_STOP".equals(command.command()))) {
                throw new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE, hasSafetyFault && !"PTZ_STOP".equals(command.command())
                                ? "云台适配器存在安全故障，未发送设备指令"
                                : "云台适配器控制未启用或设备 TCP 不可达，未发送设备指令");
            }
            var supportedCommands = adapterStatus.path("supportedCommands");
            var commandSupported = false;
            if (supportedCommands.isArray()) {
                for (var supportedCommand : supportedCommands) {
                    if (command.command().equals(supportedCommand.asText())) {
                        commandSupported = true;
                        break;
                    }
                }
            }
            if (!commandSupported) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_REQUEST, "云台适配器未声明支持该指令，未发送设备指令");
            }
            var body = mapper.writeValueAsBytes(Map.of(
                    "commandId", UUID.randomUUID().toString(),
                    "command", command.command(),
                    "parameters", command.parameters() == null ? Map.of() : command.parameters()));
            var request = gatewayRequest("/commands")
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                    .build();
            var response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() / 100 != 2) {
                var status = switch (response.statusCode()) {
                    case 400 -> HttpStatus.BAD_REQUEST;
                    case 503 -> HttpStatus.SERVICE_UNAVAILABLE;
                    default -> HttpStatus.BAD_GATEWAY;
                };
                var detail = switch (status) {
                    case BAD_REQUEST -> "云台适配器拒绝指令参数";
                    case SERVICE_UNAVAILABLE -> "云台适配器控制未启用，未发送设备指令";
                    default -> "云台适配器拒绝指令或认证失败";
                };
                throw new ResponseStatusException(status, detail);
            }
            return mapper.readValue(response.body(), GimbalCameraCommandResponse.class);
        } catch (ResponseStatusException e) {
            throw e;
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "云台适配器通信失败", e);
        }
    }

    private HttpRequest.Builder gatewayRequest(String path) {
        var request = HttpRequest.newBuilder(URI.create(gateway + path)).timeout(Duration.ofSeconds(3));
        if (!gatewayToken.isBlank()) {
            request.header(TOKEN_HEADER, gatewayToken);
        }
        return request;
    }

    private com.fasterxml.jackson.databind.JsonNode fetchAdapterStatus() throws Exception {
        var request = gatewayRequest("/status").GET().build();
        var response = client.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() / 100 != 2) {
            throw new IllegalStateException("云台状态接口返回非成功状态");
        }
        return mapper.readTree(response.body());
    }

    private static GimbalCameraStatusResponse unavailableStatus(
            boolean configured, boolean connected, String detail) {
        return new GimbalCameraStatusResponse(
                configured, connected, "云台相机", "未连接", "未连接", null,
                null, null, null, null, null, null, detail,
                false, false, 0.0, null, List.of(), false);
    }
}
