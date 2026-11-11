package com.uavusv.platform.module.sensor.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.uavusv.platform.common.api.ApiResponse;
import com.uavusv.platform.module.sensor.service.San60Service;
import java.util.concurrent.TimeUnit;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/san60")
public class San60Controller {
    private final San60Service service;
    public San60Controller(San60Service service) { this.service = service; }

    @GetMapping("/state")
    public ResponseEntity<ApiResponse<JsonNode>> state() {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(ApiResponse.success(service.status()));
    }

    @PostMapping("/control")
    public ApiResponse<JsonNode> control(@RequestBody JsonNode command) { return ApiResponse.success(service.control(command)); }

    @PostMapping("/ingest")
    public ApiResponse<JsonNode> ingest(@RequestBody JsonNode frames) { return ApiResponse.success(service.ingest(frames)); }

    @GetMapping(value = "/plotly.min.js", produces = "application/javascript")
    public ResponseEntity<byte[]> plotly() {
        return ResponseEntity.ok().cacheControl(CacheControl.maxAge(1, TimeUnit.DAYS).cachePrivate()).body(service.plotly());
    }

    @ExceptionHandler(San60Service.San60Exception.class)
    public ResponseEntity<ApiResponse<Void>> error(San60Service.San60Exception exception) {
        return ResponseEntity.status(exception.status()).cacheControl(CacheControl.noStore())
                .body(ApiResponse.failure("SAN60_" + exception.status(), exception.getMessage()));
    }
}
