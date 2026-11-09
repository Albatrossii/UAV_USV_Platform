package com.uavusv.platform.module.voiceintelligence;

/** Routes exactly once. A local error never submits audio to the cloud. */
final class RoutingSpeechProvider implements SpeechProvider {
    private final AsrSettings settings;
    private final SpeechProvider local;
    private final SpeechProvider aliyun;

    RoutingSpeechProvider(AsrSettings settings, SpeechProvider local, SpeechProvider aliyun) {
        this.settings = settings;
        this.local = local;
        this.aliyun = aliyun;
    }

    static String selection(String requested, AsrSettings settings) {
        String selected = requested == null ? settings.getProvider() : requested;
        if (!"local".equals(selected) && !"aliyun".equals(selected)) throw AsrFailure.invalid();
        return selected;
    }

    @Override
    public Transcript transcribe(Audio audio, long deadline) {
        return switch (selection(audio.provider(), settings)) {
            case "local" -> local.transcribe(audio, deadline);
            case "aliyun" -> aliyun.transcribe(audio, deadline);
            default -> throw AsrFailure.invalid();
        };
    }
}
