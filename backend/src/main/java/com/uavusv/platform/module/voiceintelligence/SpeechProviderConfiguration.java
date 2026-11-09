package com.uavusv.platform.module.voiceintelligence;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
public class SpeechProviderConfiguration {
    @Bean
    @ConditionalOnMissingBean(SpeechProvider.class)
    SpeechProvider speechProvider(AsrSettings settings, ObjectMapper json) {
        return new RoutingSpeechProvider(settings,
                new LocalAsrProvider(settings, json), new AliyunAsrProvider(settings, json));
    }
}
