package com.uavusv.platform.module.visualsensor;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.module.visualsensor.service.VisualSensorService;
import org.junit.jupiter.api.Test;

import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;

class VisualSensorServiceTests {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void exposesSixWaitingChannelsWithoutFabricatingOnlineFrames() {
        VisualSensorService service = new VisualSensorService();

        var overview = service.overview();

        assertThat(overview.totalCount()).isEqualTo(6);
        assertThat(overview.onlineCount()).isZero();
        assertThat(overview.sensors())
                .extracting(sensor -> sensor.cameraId())
                .containsExactly("uav_01", "uav_02", "uav_03", "usv_01", "usv_02", "usv_03");
        assertThat(overview.sensors()).allMatch(sensor -> "WAITING".equals(sensor.status()));
    }

    @Test
    void storesRealJpegAndMarksOnlyThatChannelOnline() {
        VisualSensorService service = new VisualSensorService();
        byte[] jpeg = {(byte) 0xff, (byte) 0xd8, 1, 2, (byte) 0xff, (byte) 0xd9};

        boolean accepted = service.observeJpegFrame(
                "uav_01",
                Base64.getEncoder().encodeToString(jpeg),
                640,
                360,
                System.currentTimeMillis(),
                0.02
        );

        var overview = service.overview();
        assertThat(accepted).isTrue();
        assertThat(overview.onlineCount()).isEqualTo(1);
        assertThat(overview.sensors().get(0).status()).isEqualTo("ONLINE");
        assertThat(overview.sensors().get(0).width()).isEqualTo(640);
        assertThat(service.latestFrame("uav_01")).hasValueSatisfying(
                stored -> assertThat(stored).isEqualTo(jpeg)
        );
    }

    @Test
    void normalizesGatewayCameraIdsBeforeCaching() {
        VisualSensorService service = new VisualSensorService();
        byte[] jpeg = {(byte) 0xff, (byte) 0xd8, 3, 4, (byte) 0xff, (byte) 0xd9};

        boolean accepted = service.observeJpegFrame(
                "usv_01_front",
                Base64.getEncoder().encodeToString(jpeg),
                240,
                135,
                System.currentTimeMillis(),
                0.02
        );

        var overview = service.overview();
        assertThat(accepted).isTrue();
        assertThat(overview.onlineCount()).isEqualTo(1);
        assertThat(overview.sensors().get(3).cameraId()).isEqualTo("usv_01");
        assertThat(overview.sensors().get(3).status()).isEqualTo("ONLINE");
        assertThat(service.latestFrame("usv_01")).hasValueSatisfying(
                stored -> assertThat(stored).isEqualTo(jpeg)
        );
        assertThat(service.latestFrame("usv_01_front")).isEmpty();
    }

    @Test
    void acceptsCurrentGatewayCameraFrameEnvelope() throws Exception {
        VisualSensorService service = new VisualSensorService();
        byte[] jpeg = {(byte) 0xff, (byte) 0xd8, 5, 6, (byte) 0xff, (byte) 0xd9};
        String encoded = Base64.getEncoder().encodeToString(jpeg);
        var frame = objectMapper.readTree("""
                {
                  "message_type": "camera_frame",
                  "timestamp": 1790409897.6063564,
                  "source": "ros_gateway",
                  "data": {
                    "stream_id": "uav_03_down",
                    "vehicle_id": "uav_03",
                    "encoding": "image/jpeg",
                    "width": 720,
                    "height": 540,
                    "data_base64": "%s"
                  }
                }
                """.formatted(encoded));

        service.observeFrame(frame);

        var overview = service.overview();
        assertThat(overview.onlineCount()).isEqualTo(1);
        assertThat(overview.sensors().get(2).status()).isEqualTo("ONLINE");
        assertThat(overview.sensors().get(2).width()).isEqualTo(720);
        assertThat(overview.sensors().get(2).height()).isEqualTo(540);
        assertThat(service.latestFrame("uav_03")).hasValueSatisfying(
                stored -> assertThat(stored).isEqualTo(jpeg)
        );
    }

    @Test
    void rejectsUnknownCameraId() {
        VisualSensorService service = new VisualSensorService();
        byte[] jpeg = {(byte) 0xff, (byte) 0xd8, 1, 2, (byte) 0xff, (byte) 0xd9};

        boolean accepted = service.observeJpegFrame(
                "unknown_camera",
                Base64.getEncoder().encodeToString(jpeg),
                640,
                360,
                System.currentTimeMillis(),
                0.02
        );

        assertThat(accepted).isFalse();
        assertThat(service.overview().onlineCount()).isZero();
    }

    @Test
    void rejectsEmptyJpeg() {
        VisualSensorService service = new VisualSensorService();

        boolean accepted = service.observeJpegFrame(
                "uav_01",
                "",
                640,
                360,
                System.currentTimeMillis(),
                0.02
        );

        assertThat(accepted).isFalse();
        assertThat(service.latestFrame("uav_01")).isEmpty();
    }

    @Test
    void rejectsInvalidBase64Jpeg() {
        VisualSensorService service = new VisualSensorService();

        boolean accepted = service.observeJpegFrame(
                "uav_01",
                "not-base64",
                640,
                360,
                System.currentTimeMillis(),
                0.02
        );

        assertThat(accepted).isFalse();
        assertThat(service.latestFrame("uav_01")).isEmpty();
    }

    @Test
    void rejectsTooShortJpeg() {
        VisualSensorService service = new VisualSensorService();
        byte[] tooShort = {(byte) 0xff, (byte) 0xd8, (byte) 0xff};

        boolean accepted = service.observeJpegFrame(
                "uav_01",
                Base64.getEncoder().encodeToString(tooShort),
                640,
                360,
                System.currentTimeMillis(),
                0.02
        );

        assertThat(accepted).isFalse();
        assertThat(service.latestFrame("uav_01")).isEmpty();
    }
}
