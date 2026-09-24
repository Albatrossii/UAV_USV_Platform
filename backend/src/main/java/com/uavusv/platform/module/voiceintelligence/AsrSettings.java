package com.uavusv.platform.module.voiceintelligence;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "app.voiceintelligence")
public class AsrSettings {
    private boolean enabled = false;
    private String baseUrl = "http://127.0.0.1:18082",
            token = "",
            modelRevision = "",
            modelAlias = "whisper-small-cpu-int8-r1",
            intentProvider = "rules",
            llmBaseUrl = "http://127.0.0.1:18083",
            llmToken = "",
            llmModel = "qwen2.5-1.5b-instruct-q4_k_m";
    private int llmTimeoutMs = 20000;

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean v) {
        enabled = v;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String v) {
        baseUrl = v;
    }

    public String getToken() {
        return token;
    }

    public void setToken(String v) {
        token = v;
    }

    public String getModelRevision() {
        return modelRevision;
    }

    public void setModelRevision(String v) {
        modelRevision = v;
    }

    public String getModelAlias() {
        return modelAlias;
    }

    public void setModelAlias(String v) {
        modelAlias = v;
    }

    public String getIntentProvider() {
        return intentProvider;
    }

    public void setIntentProvider(String v) {
        intentProvider = v;
    }

    public String getLlmBaseUrl() {
        return llmBaseUrl;
    }

    public void setLlmBaseUrl(String v) {
        llmBaseUrl = v;
    }

    public String getLlmToken() {
        return llmToken;
    }

    public void setLlmToken(String v) {
        llmToken = v;
    }

    public String getLlmModel() {
        return llmModel;
    }

    public void setLlmModel(String v) {
        llmModel = v;
    }

    public int getLlmTimeoutMs() {
        return llmTimeoutMs;
    }

    public void setLlmTimeoutMs(int v) {
        llmTimeoutMs = v;
    }
}
