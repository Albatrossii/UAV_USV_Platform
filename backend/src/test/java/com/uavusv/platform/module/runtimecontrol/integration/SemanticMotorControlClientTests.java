package com.uavusv.platform.module.runtimecontrol.integration;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandRequest;
import com.uavusv.platform.module.runtimecontrol.entity.CommandType;
import com.uavusv.platform.module.runtimecontrol.entity.RuntimeScope;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class SemanticMotorControlClientTests {

    private final SemanticMotorControlClient client = new SemanticMotorControlClient(
            true, "http://192.168.10.32:18781", "test-token", new ObjectMapper());

    @Test
    void supportsOnlyPhysicalUavTakeoffFromSystemOverview() {
        RuntimeCommandRequest request = request(
                CommandType.UAV_TAKEOFF, "uav-01", RuntimeScope.SYSTEM_OVERVIEW, "overview-unity-01");

        assertThat(client.supports(request)).isTrue();
        assertThat(client.supports(request(CommandType.UAV_LAND, "uav-01",
                RuntimeScope.SYSTEM_OVERVIEW, "overview-unity-01"))).isFalse();
        assertThat(client.supports(request(CommandType.UAV_TAKEOFF, "uav-02",
                RuntimeScope.SYSTEM_OVERVIEW, "overview-unity-01"))).isFalse();
        assertThat(client.supports(request(CommandType.UAV_TAKEOFF, "uav-01",
                RuntimeScope.MISSION_CENTER, "overview-unity-01"))).isFalse();
        assertThat(client.supports(request(CommandType.UAV_TAKEOFF, "uav-01",
                RuntimeScope.SYSTEM_OVERVIEW, "another-instance"))).isFalse();
    }

    @Test
    void disabledMotorGatewayStillClaimsPhysicalScopeAndRejectsDispatch() throws Exception {
        SemanticMotorControlClient disabled = new SemanticMotorControlClient(
                false, "", "", new ObjectMapper());
        RuntimeCommandRequest request = request(
                CommandType.UAV_TAKEOFF, "uav-01", RuntimeScope.SYSTEM_OVERVIEW, "overview-unity-01");

        assertThat(disabled.supports(request)).isTrue();
        var result = disabled.dispatch("test-command", request);
        assertThat(result.accepted()).isFalse();
        assertThat(result.errorCode()).isEqualTo("MOTOR_CONTROL_DISABLED");
    }

    private RuntimeCommandRequest request(
            CommandType type, String deviceCode, RuntimeScope scope, String instanceId) {
        return new RuntimeCommandRequest(type, null, deviceCode, null, null, scope, instanceId);
    }
}
