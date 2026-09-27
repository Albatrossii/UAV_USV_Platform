package com.uavusv.platform.module.sensor.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class SensorRuntimeServiceTests {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void summarizesLatestRadarFrameWithoutPersistence() throws Exception {
        SensorRuntimeService service = new SensorRuntimeService();
        service.observeRadarFrame(objectMapper.readTree("""
                {
                  "type": "radar_frame",
                  "device_id": "usv_01",
                  "timestamp_ms": 2000,
                  "obstacles": [
                    {"id": "obs-1", "range": 12.6, "bearing": -15.3}
                  ],
                  "detections": [
                    {"id": "target-1", "range": 20.0, "confidence": 0.91}
                  ]
                }
                """));

        var overview = service.radarOverview();

        assertThat(overview.connected()).isTrue();
        assertThat(overview.onlineCount()).isEqualTo(1);
        assertThat(overview.obstacleCount()).isEqualTo(1);
        assertThat(overview.detectionCount()).isEqualTo(1);
        assertThat(overview.nearestObstacleRange()).isEqualTo(12.6);
        assertThat(overview.latestTargetId()).isEqualTo("target-1");
    }

    @Test
    void summarizesPointCloudFrameAsRadarPoints() throws Exception {
        SensorRuntimeService service = new SensorRuntimeService();
        service.observePointCloudFrame(objectMapper.readTree("""
                {
                  "schema_version": "1.0",
                  "message_type": "pointcloud_frame",
                  "timestamp": 1784692800.5,
                  "sequence": 1,
                  "source": "uav_usv_fleet_gateway",
                  "data": {
                    "stream_id": "usv_01_mid360",
                    "vehicle_id": "usv_01",
                    "frame_id": "usv_01/mid360_link",
                    "timestamp": 1784692800.25,
                    "point_count": 4,
                    "xyz": [
                      4.125, -1.25, 0.45,
                      4.375, -1.0, 0.475,
                      4.625, -0.75, 0.5,
                      4.875, -0.5, 0.525
                    ]
                  }
                }
                """));

        var overview = service.radarOverview();

        assertThat(overview.connected()).isTrue();
        assertThat(overview.onlineCount()).isEqualTo(1);
        assertThat(overview.detectionCount()).isEqualTo(4);
        assertThat(overview.items()).hasSize(4);
        assertThat(overview.items().get(0).kind()).isEqualTo("POINTCLOUD");
        assertThat(overview.items().get(0).deviceId()).isEqualTo("usv_01");
        assertThat(overview.items().get(0).x()).isEqualTo(4.125);
        assertThat(overview.items().get(0).y()).isEqualTo(-1.25);
        assertThat(overview.items().get(0).z()).isEqualTo(0.45);
        assertThat(overview.items().get(0).timestampMs()).isEqualTo(1784692800250L);
    }

    @Test
    void decodesBase64LittleEndianLidarFrame() throws Exception {
        SensorRuntimeService service = new SensorRuntimeService();
        service.observePointCloudFrame(objectMapper.readTree("""
                {
                  "type": "lidar_frame",
                  "sensor_id": "usv_01",
                  "encoding": "xyz_f32_le_base64",
                  "point_stride_bytes": 12,
                  "point_count": 2,
                  "timestamp_ms": 1785830414555,
                  "data_base64": "AACgPwAAIMAAAEA/AACQQAAAqEAAAIC/"
                }
                """));

        var overview = service.radarOverview();

        assertThat(overview.detectionCount()).isEqualTo(2);
        assertThat(overview.items().get(0).deviceId()).isEqualTo("usv_01");
        assertThat(overview.items().get(0).x()).isEqualTo(1.25);
        assertThat(overview.items().get(0).y()).isEqualTo(-2.5);
        assertThat(overview.items().get(0).z()).isEqualTo(0.75);
        assertThat(overview.items().get(1).x()).isEqualTo(4.5);
    }

    @Test
    void projectsRadarScanToPointCloudStylePoints() {
        SensorRuntimeService service = new SensorRuntimeService();

        service.observeRadarScan(new RadarScanInput(
                "base_radar",
                1785830414555L,
                -Math.PI,
                Math.PI / 2,
                3.0,
                700.0,
                List.of(10.0, Double.NaN, 20.0, 800.0),
                List.of()
        ));

        var overview = service.radarOverview();

        assertThat(overview.connected()).isTrue();
        assertThat(overview.onlineCount()).isEqualTo(1);
        assertThat(overview.detectionCount()).isEqualTo(2);
        assertThat(overview.items()).hasSize(2);
        assertThat(overview.items()).allMatch(item -> "POINTCLOUD".equals(item.kind()));

        var first = overview.items().get(0);
        assertThat(first.id()).isEqualTo("base_radar-scan-1");
        assertThat(first.deviceId()).isEqualTo("base_radar");
        assertThat(first.range()).isEqualTo(10.0);
        assertThat(first.bearing()).isCloseTo(-180.0, org.assertj.core.data.Offset.offset(0.0001));
        assertThat(first.x()).isCloseTo(-10.0, org.assertj.core.data.Offset.offset(0.0001));
        assertThat(first.y()).isCloseTo(0.0, org.assertj.core.data.Offset.offset(0.0001));
        assertThat(first.timestampMs()).isEqualTo(1785830414555L);

        var second = overview.items().get(1);
        assertThat(second.id()).isEqualTo("base_radar-scan-3");
        assertThat(second.range()).isEqualTo(20.0);
        assertThat(second.bearing()).isCloseTo(0.0, org.assertj.core.data.Offset.offset(0.0001));
        assertThat(second.x()).isCloseTo(20.0, org.assertj.core.data.Offset.offset(0.0001));
        assertThat(second.y()).isCloseTo(0.0, org.assertj.core.data.Offset.offset(0.0001));
    }

    @Test
    void keepsRadarScanRangesOnInclusiveBoundsOnlyWhenFinite() {
        SensorRuntimeService service = new SensorRuntimeService();

        service.observeRadarScan(new RadarScanInput(
                "base_radar",
                1L,
                0.0,
                0.1,
                3.0,
                700.0,
                List.of(
                        Double.NEGATIVE_INFINITY,
                        2.99,
                        3.0,
                        700.0,
                        700.01,
                        Double.POSITIVE_INFINITY
                ),
                List.of()
        ));

        var items = service.radarOverview().items();

        assertThat(items).hasSize(2);
        assertThat(items).extracting("range").containsExactly(3.0, 700.0);
    }

    @Test
    void exposesGatewaySpectrumFrameAsElectronicDetectorData() throws Exception {
        SensorRuntimeService service = new SensorRuntimeService();

        service.observeSpectrumFrame(objectMapper.readTree("""
                {
                  "message_type": "spectrum_frame",
                  "timestamp": 1790411201.7340817,
                  "sequence": 12345,
                  "data": {
                    "vehicle_id": "uav_01",
                    "sensor_id": "san60",
                    "stream_id": "uav_01_san60",
                    "captured_at": 1790411201.6340817,
                    "start_hz": 2399948790.93684,
                    "stop_hz": 2500046445.56842,
                    "bin_hz": 61035.1552631579,
                    "rbw_hz": 100000,
                    "ref_level_dbm": 0,
                    "peak_hz": 2462326719.61579,
                    "peak_dbm": -68.4945,
                    "temperature_c": 42.32,
                    "powers_dbm": [-95.7, -91.3, -68.49, -92.5],
                    "sequence": 38423
                  }
                }
                """));

        var overview = service.radarOverview();
        assertThat(overview.connected()).isTrue();
        assertThat(overview.spectrumConnected()).isTrue();
        assertThat(overview.spectrumVehicleId()).isEqualTo("uav_01");
        assertThat(overview.spectrumSensorId()).isEqualTo("san60");
        assertThat(overview.spectrumStreamId()).isEqualTo("uav_01_san60");
        assertThat(overview.spectrumGatewaySequence()).isEqualTo(12345L);
        assertThat(overview.spectrumSequence()).isEqualTo(38423L);
        assertThat(overview.spectrumRbwHz()).isEqualTo(100000.0);
        assertThat(overview.spectrumPeakHz()).isEqualTo(2462326719.61579);
        assertThat(overview.spectrumPeakDbm()).isEqualTo(-68.4945);
        assertThat(overview.spectrumPowersDbm()).containsExactly(-95.7, -91.3, -68.49, -92.5);
        assertThat(overview.updatedAt()).isEqualTo(1790411201634L);
    }
}
