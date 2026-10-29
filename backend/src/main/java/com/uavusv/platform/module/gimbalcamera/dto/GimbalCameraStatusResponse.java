package com.uavusv.platform.module.gimbalcamera.dto;

import java.util.List;

public record GimbalCameraStatusResponse(
        boolean configured,
        boolean connected,
        String deviceName,
        String controlProtocol,
        String streamProtocol,
        String playbackUrl,
        Double yawDegrees,
        Double pitchDegrees,
        Double rollDegrees,
        Double opticalZoom,
        Double infraredZoom,
        String imageMode,
        String detail,
        boolean controlEnabled,
        boolean motionActive,
        double watchdogTimeoutSec,
        String safetyFault,
        List<String> supportedCommands,
        boolean zoomControlEnabled) {}
