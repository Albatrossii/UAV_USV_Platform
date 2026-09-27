package com.uavusv.platform.module.sensor.dto;

import java.util.List;

public record RadarOverviewResponse(
        boolean connected,
        int onlineCount,
        int totalCount,
        long updatedAt,
        int obstacleCount,
        int detectionCount,
        Double nearestObstacleRange,
        String latestTargetId,
        List<RadarItemResponse> items,
        boolean spectrumConnected,
        String spectrumVehicleId,
        String spectrumSensorId,
        String spectrumStreamId,
        Long spectrumGatewaySequence,
        Long spectrumSequence,
        Double spectrumCapturedAt,
        Double spectrumStartHz,
        Double spectrumStopHz,
        Double spectrumBinHz,
        Double spectrumRbwHz,
        Double spectrumRefLevelDbm,
        Double spectrumPeakHz,
        Double spectrumPeakDbm,
        Double spectrumTemperatureC,
        List<Double> spectrumPowersDbm
) {
}
