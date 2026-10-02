package com.uavusv.platform.module.voiceintelligence;

import org.springframework.http.ResponseEntity;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

public final class AsrResponses {
    public record Outcome(int status, Map<String, Object> body, Integer retryAfter) {}

    public static Outcome error(AsrFailure e) {
        return new Outcome(e.status, body(e.code, message(e.code), null), e.retryAfter);
    }

    public static Map<String, Object> body(String code, String message, Object data) {
        var b = new LinkedHashMap<String, Object>();
        b.put("code", code);
        b.put("message", message);
        b.put("data", data);
        b.put("timestamp", Instant.now().toString());
        return b;
    }

    public static ResponseEntity<Map<String, Object>> response(Outcome o) {
        return response(o, null);
    }

    public static ResponseEntity<Map<String, Object>> response(Outcome o, String requestId) {
        var r = ResponseEntity.status(o.status()).header("Cache-Control", "no-store");
        if (requestId != null) r.header("X-Request-ID", requestId);
        if (o.retryAfter() != null) r.header("Retry-After", o.retryAfter().toString());
        return r.body(o.body());
    }

    private static String message(String c) {
        return switch (c) {
            case "VOICE_NO_SPEECH" -> "未识别到有效语音，请重新录制";
            case "VOICE_REQUEST_IN_PROGRESS" -> "原请求仍在处理中";
            case "VOICE_REQUEST_OUTCOME_UNKNOWN" -> "原请求结果无法安全恢复，请重新录制后提交";
            case "VOICE_REQUEST_EXPIRED" -> "原请求结果已过期，请重新录制后提交";
            case "VOICE_TRANSCRIPTION_TIMEOUT" -> "识别等待超时，请联系管理员检查服务";
            case "VOICE_INTERPRETATION_TIMEOUT" -> "意图解析超时，请稍后重试";
            case "VOICE_PROVIDER_UNAVAILABLE" -> "语音识别服务暂不可用，请检查服务配置";
            case "VOICE_PROVIDER_INVALID_RESPONSE" -> "语音识别服务返回了无效结果";
            case "VOICE_AUDIO_CONVERTER_UNAVAILABLE" -> "音频转换工具未就绪，请联系管理员配置 ffmpeg";
            case "VOICE_AUDIO_TOO_LONG" -> "录音超过60秒，请缩短后重录";
            case "VOICE_INTELLIGENCE_DISABLED" -> "语音识别尚未启用";
            case "VOICE_CONTEXT_CHANGED" -> "运行上下文已变化，请刷新后重新解析";
            case "VOICE_INTERPRETATION_INVALID" -> "意图候选无效或已过期，请重新解析";
            case "VOICE_RATE_LIMITED" -> "识别服务忙，请稍后再试";
            default -> "语音请求未完成，请检查输入或联系管理员";
        };
    }
}
