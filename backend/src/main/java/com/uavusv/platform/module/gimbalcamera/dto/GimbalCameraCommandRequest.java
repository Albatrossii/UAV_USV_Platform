package com.uavusv.platform.module.gimbalcamera.dto;
import jakarta.validation.constraints.NotBlank;
import java.util.Map;
public record GimbalCameraCommandRequest(@NotBlank String command, Map<String, Object> parameters) {}
