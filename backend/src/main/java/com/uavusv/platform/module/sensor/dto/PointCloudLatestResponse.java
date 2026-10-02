package com.uavusv.platform.module.sensor.dto;

import com.fasterxml.jackson.databind.JsonNode;

public record PointCloudLatestResponse(
        String streamId,
        Long sequence,
        long receivedAtMs,
        long ageMs,
        JsonNode frame
) {
}
