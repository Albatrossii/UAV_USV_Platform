package com.uavusv.platform.module.runtimecontrol;

import com.uavusv.platform.module.device.entity.Device;
import com.uavusv.platform.module.device.entity.DeviceType;
import com.uavusv.platform.module.device.repository.DeviceRepository;
import com.uavusv.platform.module.mission.repository.MissionRunRepository;
import com.uavusv.platform.module.mission.service.AlgorithmRuntimeManager;
import com.uavusv.platform.module.monitoring.service.RuntimeStateService;
import com.uavusv.platform.module.runtimecontrol.dispatch.RuntimeCommandDispatcher;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandRequest;
import com.uavusv.platform.module.runtimecontrol.dto.RuntimeCommandResponse;
import com.uavusv.platform.module.runtimecontrol.entity.CommandStatus;
import com.uavusv.platform.module.runtimecontrol.entity.CommandType;
import com.uavusv.platform.module.runtimecontrol.entity.ControlCommand;
import com.uavusv.platform.module.runtimecontrol.entity.RuntimeScope;
import com.uavusv.platform.module.runtimecontrol.repository.ControlCommandRepository;
import com.uavusv.platform.module.runtimecontrol.repository.SimulationSessionRepository;
import com.uavusv.platform.module.runtimecontrol.service.RuntimeControlService;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.context.ApplicationEventPublisher;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RuntimeControlServiceStandaloneDeviceTests {

    @Test
    void mapsUnitySimulationCodeToRegisteredDeviceOnlyForStandaloneRuntime() {
        DeviceRepository devices = mock(DeviceRepository.class);
        Device device = mock(Device.class);
        when(device.getId()).thenReturn(21L);
        when(device.getType()).thenReturn(DeviceType.UAV);
        when(device.isDeleted()).thenReturn(false);
        when(devices.findByCode("UAV-001")).thenReturn(Optional.empty());
        when(devices.findByCode("uav-01")).thenReturn(Optional.of(device));

        ControlCommandRepository commands = mock(ControlCommandRepository.class);
        when(commands.save(any(ControlCommand.class))).thenAnswer(invocation -> invocation.getArgument(0));
        AlgorithmRuntimeManager algorithmRuntime = mock(AlgorithmRuntimeManager.class);
        when(algorithmRuntime.isStandaloneSingleDeviceRun(42L)).thenReturn(true);

        RuntimeControlService service = new RuntimeControlService(
                mock(RuntimeStateService.class),
                mock(SimulationSessionRepository.class),
                commands,
                devices,
                mock(MissionRunRepository.class),
                mock(RuntimeCommandDispatcher.class),
                mock(ApplicationEventPublisher.class),
                "Ubuntu", ".", ".", ".", "ws://localhost", "token", "browser-unity", 15, 75
        );
        ReflectionTestUtils.setField(service, "algorithmRuntimeManager", algorithmRuntime);

        RuntimeCommandRequest request = new RuntimeCommandRequest(
                CommandType.UAV_HOVER,
                42L,
                "UAV-001",
                null,
                null,
                RuntimeScope.MISSION_CENTER,
                "ALGORITHM_RUN:42"
        );
        RuntimeCommandResponse response = service.issueCommand(request, "admin");

        assertEquals(CommandStatus.SUCCEEDED, response.status());
        verify(algorithmRuntime).controlDevice(42L, "UAV-001", "UAV_HOVER");
        verify(devices).findByCode("uav-01");
    }
}
