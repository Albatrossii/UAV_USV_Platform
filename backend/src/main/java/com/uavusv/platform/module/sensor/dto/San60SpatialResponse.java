package com.uavusv.platform.module.sensor.dto;

import com.fasterxml.jackson.databind.JsonNode;

public record San60SpatialResponse(
        boolean connected,
        long updatedAtMs,
        Long sequence,
        String vehicleId,
        String streamId,
        JsonNode spatial
) {
}
