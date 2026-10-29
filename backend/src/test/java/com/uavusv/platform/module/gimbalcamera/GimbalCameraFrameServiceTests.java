package com.uavusv.platform.module.gimbalcamera;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.module.gimbalcamera.service.GimbalCameraFrameService;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class GimbalCameraFrameServiceTests {
    private final ObjectMapper mapper = new ObjectMapper();
    private final GimbalCameraFrameService frames = new GimbalCameraFrameService();
    private static final String JPEG = "/9j/2Q==";

    @Test
    void acceptsCompleteGatewayEnvelope() throws Exception {
        var envelope = mapper.readTree("""
            {"schema_version":"1.0","message_type":"camera_frame","data":{
              "stream_id":"gimbal_01_a10tr","vehicle_id":"gimbal_01",
              "encoding":"image/jpeg","width":1280,"height":720,"data_base64":"%s"}}
            """.formatted(JPEG));
        assertTrue(frames.accept(envelope));
        assertArrayEquals(new byte[]{(byte) 0xff, (byte) 0xd8, (byte) 0xff, (byte) 0xd9}, frames.latestFrame(0));
        assertEquals(1280, frames.width());
        assertEquals(720, frames.height());
        assertNull(frames.latestFrame(frames.receivedAtMillis()));
    }

    @Test
    void acceptsExtractedFlatFrame() throws Exception {
        var frame = mapper.readTree("""
            {"stream_id":"gimbal_01_a10tr","vehicle_id":"gimbal_01",
              "encoding":"image/jpeg","width":1280,"height":720,"data_base64":"%s"}
            """.formatted(JPEG));
        assertTrue(frames.accept(frame));
    }

    @Test
    void rejectsOtherStreamsAndMalformedJpeg() throws Exception {
        var otherCamera = mapper.readTree("""
            {"message_type":"camera_frame","data":{"stream_id":"uav_01",
              "vehicle_id":"uav_01","encoding":"image/jpeg","width":10,"height":10,"data_base64":"%s"}}
            """.formatted(JPEG));
        var invalidJpeg = mapper.readTree("""
            {"stream_id":"gimbal_01_a10tr","vehicle_id":"gimbal_01",
              "encoding":"image/jpeg","width":1280,"height":720,"data_base64":"QUJDRA=="}
            """ );
        assertFalse(frames.accept(otherCamera));
        assertFalse(frames.accept(invalidJpeg));
        assertNull(frames.latestFrame(0));
    }
}
