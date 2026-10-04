package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.autoconfigure.context.ConfigurationPropertiesAutoConfiguration;
import org.springframework.boot.test.context.ConfigDataApplicationContextInitializer;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

class AliyunCredentialBindingTests {
    @Test
    void standardAliyunEnvironmentNamesReachAsrSettings() {
        new ApplicationContextRunner()
                .withInitializer(new ConfigDataApplicationContextInitializer())
                .withConfiguration(AutoConfigurations.of(ConfigurationPropertiesAutoConfiguration.class))
                .withUserConfiguration(AsrSettings.class)
                .withPropertyValues(
                        "ALIYUN_AK_ID=test-access-key-id",
                        "ALIYUN_AK_SECRET=test-access-key-secret")
                .run(context -> {
                    AsrSettings settings = context.getBean(AsrSettings.class);
                    assertEquals("test-access-key-id", settings.getAliyunAccessKeyId());
                    assertEquals("test-access-key-secret", settings.getAliyunAccessKeySecret());
                });
    }
}
