package com.uavusv.platform.module.gimbalcamera.controller;
import com.uavusv.platform.common.api.ApiResponse;
import com.uavusv.platform.module.gimbalcamera.dto.*;
import com.uavusv.platform.module.gimbalcamera.service.GimbalCameraService;
import com.uavusv.platform.module.gimbalcamera.service.GimbalCameraFrameService;
import jakarta.validation.Valid;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/api/gimbal-camera")
public class GimbalCameraController {
 private final GimbalCameraService service;
 private final GimbalCameraFrameService frames;
 public GimbalCameraController(GimbalCameraService service,GimbalCameraFrameService frames){this.service=service;this.frames=frames;}
 @GetMapping("/status") public ApiResponse<GimbalCameraStatusResponse> status(){return ApiResponse.success(service.status());}
 @PostMapping("/commands") public ApiResponse<GimbalCameraCommandResponse> command(@Valid @RequestBody GimbalCameraCommandRequest request){return ApiResponse.success(service.send(request));}
 @GetMapping(value="/frame", produces=MediaType.IMAGE_JPEG_VALUE)
 public ResponseEntity<byte[]> frame(@RequestParam(defaultValue="0") long since){
  byte[] jpeg=frames.latestFrame(since);
  if(jpeg==null)return ResponseEntity.status(HttpStatus.NO_CONTENT).cacheControl(CacheControl.noStore()).build();
  return ResponseEntity.ok().cacheControl(CacheControl.noStore()).header(HttpHeaders.PRAGMA,"no-cache")
   .header("X-Frame-Timestamp",Long.toString(frames.receivedAtMillis())).body(jpeg);
 }
}
