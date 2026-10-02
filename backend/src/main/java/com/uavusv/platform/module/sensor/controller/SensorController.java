package com.uavusv.platform.module.sensor.controller;

import com.uavusv.platform.common.api.ApiResponse;
import com.uavusv.platform.module.sensor.dto.RadarOverviewResponse;
import com.uavusv.platform.module.sensor.dto.PointCloudLatestResponse;
import com.uavusv.platform.module.sensor.service.SensorRuntimeService;
import java.time.Clock;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/sensors")
public class SensorController {

    private final SensorRuntimeService sensorRuntimeService;
    private final Clock clock = Clock.systemUTC();

    public SensorController(SensorRuntimeService sensorRuntimeService) {
        this.sensorRuntimeService = sensorRuntimeService;
    }

    @GetMapping("/radar")
    public ApiResponse<RadarOverviewResponse> radar() {
        return ApiResponse.success(sensorRuntimeService.radarOverview());
    }

    @GetMapping("/pointcloud/latest")
    public ApiResponse<PointCloudLatestResponse> latestPointCloud(
            @RequestParam(defaultValue = "usv_01_mid360") String streamId
    ) {
        return ApiResponse.success(sensorRuntimeService.latestPointCloudFrame(streamId)
                .map(frame -> new PointCloudLatestResponse(
                        frame.streamId(),
                        frame.sequence(),
                        frame.receivedAtMs(),
                        Math.max(0L, clock.millis() - frame.receivedAtMs()),
                        frame.frame()
                ))
                .orElse(null));
    }
}
