package com.uavusv.platform.module.visualsensor.integration;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.module.sensor.service.SensorRuntimeService;
import com.uavusv.platform.module.visualsensor.service.VisualSensorService;
import com.uavusv.platform.module.gateway.v1.RealtimeHub;
import org.junit.jupiter.api.Test;
import static org.mockito.Mockito.*;

class VisualSensorGatewayEnvelopeTests {
    private final ObjectMapper mapper = new ObjectMapper();
    private final VisualSensorService vision = mock(VisualSensorService.class);
    private final SensorRuntimeService sensors = mock(SensorRuntimeService.class);
    private final RealtimeHub hub = mock(RealtimeHub.class);
    private final VisualSensorWebSocketClient client = new VisualSensorWebSocketClient(mapper, vision, sensors, hub, "ws://127.0.0.1:8765/ws");

    @Test void receivesGatewayWrappedJpeg() throws Exception {
        client.acceptMessage(mapper.readTree("""
            {"message_type":"camera_frame","data":{"stream_id":"usv_01_front","vehicle_id":"usv_01","encoding":"image/jpeg","width":240,"height":135,"data_base64":"/9j/2Q=="}}
            """));
        verify(vision).observeJpegFrame("usv_01", "/9j/2Q==", 240, 135, 0, 0);
    }

    @Test void preservesLegacyFrames() throws Exception {
        var frame = mapper.readTree("{\"type\":\"camera_frame\",\"camera_id\":\"uav_01\",\"jpeg_base64\":\"/9j/2Q==\"}");
        client.acceptMessage(frame);
        verify(vision).observeFrame(frame);
    }

    @Test void routesPointCloudPayload() throws Exception {
        var frame = mapper.readTree("{\"message_type\":\"pointcloud_frame\",\"data\":{\"stream_id\":\"usv_01_mid360\",\"xyz\":[1,2,3]}}");
        client.acceptMessage(frame);
        verify(sensors).observePointCloudFrame(frame);
    }

    @Test void doesNotTreatOtherEncodingsAsJpeg() throws Exception {
        client.acceptMessage(mapper.readTree("{\"message_type\":\"camera_frame\",\"data\":{\"encoding\":\"16UC1\",\"data_base64\":\"AAAA\"}}"));
        verifyNoInteractions(vision, sensors);
    }

    @Test void mapsObservationTargetsWithoutInventingCoordinates() throws Exception {
        client.acceptMessage(mapper.readTree("""
            {"message_type":"perception_targets","sequence":42,"data":{"frame_id":"map","targets":[
              {"track_id":"enemy_ship","position":{"x":1,"y":2,"z":0}},
              {"track_id":"unknown","position":{"x":1}}]}}
            """));
        verify(hub).publish(argThat(envelope -> envelope.runId() == null
                && envelope.sequence() == 42
                && envelope.payload().path("targets").get(0).path("coordinateValid").asBoolean()
                && !envelope.payload().path("targets").get(1).path("coordinateValid").asBoolean()));
    }
}
