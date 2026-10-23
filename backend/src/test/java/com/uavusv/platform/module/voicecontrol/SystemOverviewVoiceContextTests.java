package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.uavusv.platform.module.device.entity.DeviceType;
import com.uavusv.platform.module.mission.dto.response.MissionDetailResponse;
import com.uavusv.platform.module.mission.dto.response.MissionDeviceResponse;
import com.uavusv.platform.module.mission.dto.response.MissionResponse;
import com.uavusv.platform.module.mission.entity.MissionStatus;
import com.uavusv.platform.module.mission.service.MissionService;

import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.List;
import java.util.UUID;

class SystemOverviewVoiceContextTests {
    private VoiceJson json;
    private VoiceControlTests.TestTime time;
    private RuntimeContextRegistry registry;
    private SystemOverviewVoiceContextService service;
    private MissionService missions;

    @BeforeEach
    void setup() {
        var dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        new ResourceDatabasePopulator(
                new ClassPathResource("db/migration/V19__create_voice_control_p0.sql"))
                .execute(dataSource);
        var jdbc = new JdbcTemplate(dataSource);
        jdbc.execute("CREATE TABLE app_user(id BIGINT PRIMARY KEY,username VARCHAR(64),role VARCHAR(32),enabled BOOLEAN)");
        jdbc.update("INSERT INTO app_user VALUES(1,'alice','ADMIN',true)");

        json = new VoiceJson();
        time = new VoiceControlTests.TestTime();
        var store = new VoiceStore(jdbc, new DataSourceTransactionManager(dataSource), json);
        registry = new RuntimeContextRegistry(
                store, json, time, new VoiceAccess(jdbc), new VoiceSettings(true));
        missions = mock(MissionService.class);
        service = new SystemOverviewVoiceContextService(registry, missions);
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(
                        "alice", "", AuthorityUtils.createAuthorityList("ROLE_ADMIN")));
    }

    @AfterEach
    void clearSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void unitySceneStateDoesNotChangeOrBlockSystemOverviewStart() {
        MissionDetailResponse missionDetail = detail(MissionStatus.READY);
        when(missions.getMission(42L)).thenReturn(missionDetail);

        var ready = service.sync(request(true));
        assertEquals("SYSTEM_OVERVIEW", ready.path("runtimeScope").asText());
        assertEquals("REAL_MISSION", ready.path("runtimeKind").asText());
        assertEquals("ROS_GATEWAY", ready.path("executionBackend").asText());
        assertFalse(ready.path("sceneReady").asBoolean());
        assertEquals("[\"UAV-001\",\"USV-001\"]",
                registry.require(ready.path("runtimeRef").asText(), 1).path("_members").toString());

        long frozenVersion = ready.path("contextVersion").asLong();
        time.advance(1_000);
        var withoutUnityScene = service.sync(request(false));
        assertFalse(withoutUnityScene.path("sceneReady").asBoolean());
        assertEquals(frozenVersion, withoutUnityScene.path("contextVersion").asLong());
        assertDoesNotThrow(() -> registry.check(
                registry.require(withoutUnityScene.path("runtimeRef").asText(), 1), "START", true));
    }

    @Test
    void publicHeartbeatKeepsContextLiveWithoutAlgorithmChannel() {
        MissionDetailResponse missionDetail = detail(MissionStatus.PAUSED);
        when(missions.getMission(42L)).thenReturn(missionDetail);
        var context = service.sync(request(true));
        assertDoesNotThrow(() -> registry.check(
                registry.require(context.path("runtimeRef").asText(), 1), "RESUME", true));

        time.advance(5_001);
        var failure = assertThrows(VoiceFailure.class,
                () -> registry.check(
                        registry.require(context.path("runtimeRef").asText(), 1), "RESUME", true));
        assertEquals("HEARTBEAT_STALE", failure.code);
    }

    private com.fasterxml.jackson.databind.node.ObjectNode request(boolean sceneReady) {
        return json.object()
                .put("missionId", 42)
                .put("runtimeInstanceId", "overview-unity-01")
                .put("sceneReady", sceneReady);
    }

    private MissionDetailResponse detail(MissionStatus status) {
        MissionResponse mission = mock(MissionResponse.class);
        when(mission.status()).thenReturn(status);
        when(mission.algorithmCode()).thenReturn("GB_SFLA_CS");
        MissionDeviceResponse uav = device("uav_001", DeviceType.UAV);
        MissionDeviceResponse usv = device("USV-001", DeviceType.USV);
        return new MissionDetailResponse(mission, List.of(usv, uav), List.of(), List.of(), null, List.of());
    }

    private MissionDeviceResponse device(String code, DeviceType type) {
        MissionDeviceResponse device = mock(MissionDeviceResponse.class);
        when(device.required()).thenReturn(true);
        when(device.code()).thenReturn(code);
        when(device.type()).thenReturn(type);
        return device;
    }
}
