package com.uavusv.platform.module.runtimecontrol;

import com.uavusv.platform.common.exception.BusinessException;
import com.uavusv.platform.module.device.entity.Device;
import com.uavusv.platform.module.device.entity.DeviceType;
import com.uavusv.platform.module.device.repository.DeviceRepository;
import com.uavusv.platform.module.mission.repository.MissionRunRepository;
import com.uavusv.platform.module.mission.service.AlgorithmRuntimeManager;
import com.uavusv.platform.module.monitoring.service.RuntimeStateService;
import com.uavusv.platform.module.runtimecontrol.dispatch.CommandDispatchResult;
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
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RuntimeControlServiceSafetyStopTests {
    private ControlCommandRepository commandRepository;
    private RuntimeCommandDispatcher commandDispatcher;
    private MissionRunRepository missionRunRepository;
    private RuntimeControlService service;

    @BeforeEach
    void setUp() {
        commandRepository = mock(ControlCommandRepository.class);
        commandDispatcher = mock(RuntimeCommandDispatcher.class);
        missionRunRepository = mock(MissionRunRepository.class);
        DeviceRepository deviceRepository = mock(DeviceRepository.class);
        Device device = mock(Device.class);
        when(device.getId()).thenReturn(21L);
        when(device.getType()).thenReturn(DeviceType.USV);
        when(deviceRepository.findByCode("usv-01")).thenReturn(Optional.of(device));
        when(commandRepository.save(any(ControlCommand.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(commandDispatcher.dispatch(any(), any())).thenReturn(CommandDispatchResult.dispatched("sent"));
        service = new RuntimeControlService(
                mock(RuntimeStateService.class),
                mock(SimulationSessionRepository.class),
                commandRepository,
                deviceRepository,
                missionRunRepository,
                commandDispatcher,
                mock(ApplicationEventPublisher.class),
                "Ubuntu", ".", ".", ".", "ws://localhost", "token", "ros-gateway-v1", 15, 75
        );
    }

    @Test
    void systemOverviewStopIsDispatchedImmediately() {
        RuntimeCommandResponse response = service.issueCommand(request(CommandType.USV_STOP, null), "test");

        assertEquals(CommandStatus.DISPATCHED, response.status());
        assertNotNull(response.commandKey());
        assertDispatched(CommandType.USV_STOP);
    }

    @Test
    void missionCenterStopBypassesPendingDepartWithoutChangingIt() {
        when(missionRunRepository.existsById(31L)).thenReturn(true);
        when(commandRepository.existsByRunIdAndDeviceIdAndStatusIn(eq(31L), eq(21L), any())).thenReturn(true);
        ControlCommand depart = executingDepart();

        RuntimeCommandResponse response = service.issueCommand(request(CommandType.USV_STOP, 31L), "test");

        assertEquals(CommandStatus.DISPATCHED, response.status());
        assertNotEquals(depart.getCommandKey(), response.commandKey());
        assertEquals(CommandStatus.EXECUTING, depart.getStatus());
        verify(commandRepository, never()).existsByRunIdAndDeviceIdAndStatusIn(eq(31L), eq(21L), any());
        assertDispatched(CommandType.USV_STOP);
    }

    @Test
    void missionCenterEmergencyStopBypassesPendingDepart() {
        when(missionRunRepository.existsById(31L)).thenReturn(true);

        RuntimeCommandResponse response = service.issueCommand(
                request(CommandType.USV_EMERGENCY_STOP, 31L), "test");

        assertEquals(CommandStatus.DISPATCHED, response.status());
        verify(commandRepository, never()).existsByRunIdAndDeviceIdAndStatusIn(eq(31L), eq(21L), any());
        assertDispatched(CommandType.USV_EMERGENCY_STOP);
    }

    @Test
    void ordinaryMissionCommandStillUsesPendingGuard() {
        when(missionRunRepository.existsById(31L)).thenReturn(true);
        when(commandRepository.existsByRunIdAndDeviceIdAndStatusIn(eq(31L), eq(21L), any())).thenReturn(true);

        assertThrows(BusinessException.class,
                () -> service.issueCommand(request(CommandType.USV_RETURN, 31L), "test"));

        verify(commandDispatcher, never()).dispatch(any(), any());
    }

    @Test
    void standaloneSingleDeviceCommandIsRoutedToAlgorithmRuntime() {
        AlgorithmRuntimeManager algorithmRuntimeManager = mock(AlgorithmRuntimeManager.class);
        when(algorithmRuntimeManager.isStandaloneSingleDeviceRun(31L)).thenReturn(true);
        ReflectionTestUtils.setField(service, "algorithmRuntimeManager", algorithmRuntimeManager);

        RuntimeCommandResponse response = service.issueCommand(
                request(CommandType.USV_RETURN, 31L), "test");

        assertEquals(CommandStatus.SUCCEEDED, response.status());
        verify(algorithmRuntimeManager).controlDevice(31L, "usv-01", "USV_RETURN");
        verify(commandDispatcher, never()).dispatch(any(), any());
    }

    private void assertDispatched(CommandType commandType) {
        ArgumentCaptor<RuntimeCommandRequest> request = ArgumentCaptor.forClass(RuntimeCommandRequest.class);
        verify(commandDispatcher).dispatch(any(), request.capture());
        assertEquals(commandType, request.getValue().commandType());
    }

    private ControlCommand executingDepart() {
        ControlCommand command = new ControlCommand(
                1L, 31L, 21L, CommandType.USV_DEPART, "{}", "test",
                RuntimeScope.MISSION_CENTER, "instance");
        command.execute("executing");
        return command;
    }

    private RuntimeCommandRequest request(CommandType commandType, Long runId) {
        return new RuntimeCommandRequest(
                commandType,
                runId,
                "usv-01",
                "{}",
                commandType.name(),
                runId == null ? RuntimeScope.SYSTEM_OVERVIEW : RuntimeScope.MISSION_CENTER,
                "instance"
        );
    }
}
