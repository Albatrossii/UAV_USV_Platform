package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

class SpeechProviderSelectionTests {
    private final ApplicationContextRunner contexts = new ApplicationContextRunner()
            .withBean(AsrSettings.class, AsrSettings::new)
            .withBean(ObjectMapper.class, ObjectMapper::new)
            .withUserConfiguration(SpeechProviderConfiguration.class);

    @Test
    void registersOneRequestScopedRouter() {
        contexts.run(context -> {
            assertEquals(1, context.getBeanNamesForType(SpeechProvider.class).length);
            assertInstanceOf(RoutingSpeechProvider.class, context.getBean(SpeechProvider.class));
        });
    }

    @Test
    void legacyAliyunConfigurationStillRegistersOneRouter() {
        contexts.withPropertyValues("app.voiceintelligence.provider=aliyun")
                .run(context -> {
                    assertEquals(1, context.getBeanNamesForType(SpeechProvider.class).length);
                    assertInstanceOf(RoutingSpeechProvider.class, context.getBean(SpeechProvider.class));
                });
    }

    @Test
    void routesExplicitSelectionWithoutChangingLegacyDefault() {
        var settings = new AsrSettings();
        settings.setProvider("aliyun");
        var local = mock(SpeechProvider.class);
        var cloud = mock(SpeechProvider.class);
        var router = new RoutingSpeechProvider(settings, local, cloud);
        var explicit = new SpeechProvider.Audio("id", "zh-CN", "audio/wav", new byte[]{1}, "local");
        router.transcribe(explicit, 42);
        verify(local).transcribe(explicit, 42);
        verifyNoInteractions(cloud);
        var legacy = new SpeechProvider.Audio("legacy", "zh-CN", "audio/wav", new byte[]{1});
        router.transcribe(legacy, 43);
        verify(cloud).transcribe(legacy, 43);
    }

    @Test
    void localFailureNeverFallsBackToCloud() {
        var local = mock(SpeechProvider.class);
        var cloud = mock(SpeechProvider.class);
        when(local.transcribe(any(), anyLong())).thenThrow(new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE"));
        var router = new RoutingSpeechProvider(new AsrSettings(), local, cloud);
        assertThrows(AsrFailure.class, () -> router.transcribe(
                new SpeechProvider.Audio("id", "zh-CN", "audio/wav", new byte[]{1}, "local"), 42));
        verifyNoInteractions(cloud);
        assertThrows(AsrFailure.class, () -> router.transcribe(
                new SpeechProvider.Audio("id", "zh-CN", "audio/wav", new byte[]{1}, "invalid"), 42));
        verifyNoInteractions(cloud);
    }
}
