package com.uavusv.platform.module.monitoring;

import com.uavusv.platform.common.exception.GlobalExceptionHandler;
import com.uavusv.platform.module.monitoring.integration.IntegrationController;
import com.uavusv.platform.module.monitoring.service.RuntimeStateService;
import com.uavusv.platform.module.runtimecontrol.service.RuntimeControlService;
import com.uavusv.platform.module.sensor.service.SensorRuntimeService;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class IntegrationHeartbeatErrorTests {
    @Test
    void wrongTokenReturns401WithoutUpdatingHeartbeat() throws Exception {
        var runtime = mock(RuntimeStateService.class);
        var controller = new IntegrationController(runtime, mock(RuntimeControlService.class), "test-only");
        var mvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new GlobalExceptionHandler()).build();
        mvc.perform(post("/api/integration/heartbeat").contentType("application/json")
                .header("X-Platform-Token", "wrong")
                .content("{\"componentCode\":\"unity-client-01\",\"instanceId\":\"overview-test\",\"state\":\"ONLINE\",\"detail\":\"test\",\"rosConnectionStatus\":\"OFFLINE\"}"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(runtime);
    }

    @Test
    void acceptsSan60SpectrumFramesWithIntegrationToken() throws Exception {
        var runtime = mock(RuntimeStateService.class);
        var sensor = mock(SensorRuntimeService.class);
        var controller = new IntegrationController(runtime, mock(RuntimeControlService.class), sensor, "test-only");
        var mvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new GlobalExceptionHandler()).build();

        mvc.perform(post("/api/integration/san60/spectrum")
                        .contentType("application/json")
                        .header("X-Platform-Token", "test-only")
                        .content("{\"message_type\":\"spectrum_frame\",\"data\":{\"vehicle_id\":\"uav_01\",\"stream_id\":\"offline_san60\",\"powers_dbm\":[-80.0],\"spatial\":{\"track\":[],\"cells\":[],\"field\":[]}}}"))
                .andExpect(status().isOk());
        verify(sensor).observeSpectrumFrame(any());
    }
}
