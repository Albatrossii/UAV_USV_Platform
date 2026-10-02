package com.uavusv.platform.module.voiceintelligence;

public interface SpeechProvider {
    record Audio(String requestId, String locale, String mime, byte[] bytes) {}

    record Transcript(
            String requestId,
            String text,
            int durationMs,
            String modelRevision,
            String provider,
            String model) {
        public Transcript(String requestId, String text, int durationMs, String modelRevision) {
            this(requestId, text, durationMs, modelRevision, "local-asr", null);
        }
    }

    Transcript transcribe(Audio audio, long deadlineNanos);
}
