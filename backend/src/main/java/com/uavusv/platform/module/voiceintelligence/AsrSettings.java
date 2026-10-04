package com.uavusv.platform.module.voiceintelligence;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "app.voiceintelligence")
public class AsrSettings {
    private boolean enabled = false;
    private String provider = "local",
            baseUrl = "http://127.0.0.1:18082",
            token = "",
            modelRevision = "",
            modelAlias = "whisper-small-cpu-int8-r1",
            aliyunAppKey = "",
            aliyunToken = "",
            aliyunAccessKeyId = "",
            aliyunAccessKeySecret = "",
            aliyunRegion = "cn-shanghai",
            aliyunFfmpegPath = "ffmpeg",
            aliyunModelAlias = "aliyun-shiyinshi-v1",
            intentProvider = "rules",
            llmBaseUrl = "http://127.0.0.1:18083",
            llmToken = "",
            llmModel = "qwen2.5-1.5b-instruct-q4_k_m",
            resultEncryptionKey = "";
    private int llmTimeoutMs = 20000;
    private int aliyunTokenRefreshSkewSeconds = 300;

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean v) {
        enabled = v;
    }

    public String getProvider() {
        return provider;
    }

    public void setProvider(String v) {
        provider = v;
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

    public String getAliyunAppKey() {
        return aliyunAppKey;
    }

    public void setAliyunAppKey(String v) {
        aliyunAppKey = v;
    }

    public String getAliyunToken() {
        return aliyunToken;
    }

    public void setAliyunToken(String v) {
        aliyunToken = v;
    }

    public String getAliyunAccessKeyId() {
        return aliyunAccessKeyId;
    }

    public void setAliyunAccessKeyId(String v) {
        aliyunAccessKeyId = v;
    }

    public String getAliyunAccessKeySecret() {
        return aliyunAccessKeySecret;
    }

    public void setAliyunAccessKeySecret(String v) {
        aliyunAccessKeySecret = v;
    }

    public int getAliyunTokenRefreshSkewSeconds() {
        return aliyunTokenRefreshSkewSeconds;
    }

    public void setAliyunTokenRefreshSkewSeconds(int v) {
        aliyunTokenRefreshSkewSeconds = v;
    }

    public String getAliyunRegion() {
        return aliyunRegion;
    }

    public void setAliyunRegion(String v) {
        aliyunRegion = v;
    }

    public String getAliyunFfmpegPath() {
        return aliyunFfmpegPath;
    }

    public void setAliyunFfmpegPath(String v) {
        aliyunFfmpegPath = v;
    }

    public String getAliyunModelAlias() {
        return aliyunModelAlias;
    }

    public void setAliyunModelAlias(String v) {
        aliyunModelAlias = v;
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

    public String getResultEncryptionKey() {
        return resultEncryptionKey;
    }

    public void setResultEncryptionKey(String v) {
        resultEncryptionKey = v;
    }
}
