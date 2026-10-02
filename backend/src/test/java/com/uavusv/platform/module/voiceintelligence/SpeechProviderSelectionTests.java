package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

class SpeechProviderSelectionTests {
    private final ApplicationContextRunner contexts = new ApplicationContextRunner()
            .withBean(AsrSettings.class, AsrSettings::new)
            .withBean(ObjectMapper.class, ObjectMapper::new)
            .withUserConfiguration(LocalAsrProvider.class, AliyunAsrProvider.class);

    @Test
    void defaultsToOnlyTheLocalProvider() {
        contexts.run(context -> {
            assertEquals(1, context.getBeanNamesForType(SpeechProvider.class).length);
            assertInstanceOf(LocalAsrProvider.class, context.getBean(SpeechProvider.class));
        });
    }

    @Test
    void aliyunSelectsOnlyTheCloudProvider() {
        contexts.withPropertyValues("app.voiceintelligence.provider=aliyun")
                .run(context -> {
                    assertEquals(1, context.getBeanNamesForType(SpeechProvider.class).length);
                    assertInstanceOf(AliyunAsrProvider.class, context.getBean(SpeechProvider.class));
                });
    }
}
