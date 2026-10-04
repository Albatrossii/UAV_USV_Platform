package com.uavusv.platform.module.voiceintelligence;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.Map;
import java.util.StringJoiner;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Resolves a manually configured NLS Token or securely creates and caches one via Aliyun POP. */
final class AliyunTokenManager {
    private static final URI ENDPOINT = URI.create("https://nls-meta.cn-shanghai.aliyuncs.com/");
    private static final int MAX_RESPONSE = 16384;

    private final AsrSettings settings;
    private final ObjectMapper json;
    private final URI endpoint;
    private final Clock clock;
    private final Supplier<String> nonce;
    private final HttpClient client;
    private volatile CachedToken cached;

    AliyunTokenManager(AsrSettings settings, ObjectMapper json) {
        this(settings, json, ENDPOINT, Clock.systemUTC(), () -> UUID.randomUUID().toString());
    }

    AliyunTokenManager(
            AsrSettings settings,
            ObjectMapper json,
            URI endpoint,
            Clock clock,
            Supplier<String> nonce) {
        if (endpoint == null
                || !("https".equals(endpoint.getScheme())
                        && "nls-meta.cn-shanghai.aliyuncs.com".equals(endpoint.getHost()))
                && !("http".equals(endpoint.getScheme())
                        && "127.0.0.1".equals(endpoint.getHost()))) {
            throw new IllegalArgumentException("token endpoint must be Aliyun HTTPS or loopback HTTP");
        }
        this.settings = settings;
        this.json = json;
        this.endpoint = endpoint;
        this.clock = clock;
        this.nonce = nonce;
        this.client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    String token(long deadlineNanos) {
        String manual = settings.getAliyunToken();
        if (validToken(manual)) return manual;
        if (manual != null && !manual.isBlank()) throw unavailable();

        long now = clock.instant().getEpochSecond();
        CachedToken current = cached;
        if (current != null && now < current.refreshAtEpochSecond()) return current.value();
        synchronized (this) {
            now = clock.instant().getEpochSecond();
            current = cached;
            if (current != null && now < current.refreshAtEpochSecond()) return current.value();
            cached = fetch(deadlineNanos, now);
            return cached.value();
        }
    }

    private CachedToken fetch(long deadlineNanos, long nowEpochSecond) {
        String accessKeyId = settings.getAliyunAccessKeyId();
        String accessKeySecret = settings.getAliyunAccessKeySecret();
        if (accessKeyId == null || !accessKeyId.matches("[A-Za-z0-9._-]{8,128}")
                || accessKeySecret == null || accessKeySecret.length() < 8
                || accessKeySecret.length() > 256
                || accessKeySecret.chars().anyMatch(c -> c < 0x21 || c > 0x7e)) {
            throw unavailable();
        }

        Map<String, String> parameters = new TreeMap<>();
        parameters.put("AccessKeyId", accessKeyId);
        parameters.put("Action", "CreateToken");
        parameters.put("Format", "JSON");
        parameters.put("RegionId", "cn-shanghai");
        parameters.put("SignatureMethod", "HMAC-SHA1");
        parameters.put("SignatureNonce", nonce.get());
        parameters.put("SignatureVersion", "1.0");
        parameters.put("Timestamp", clock.instant().truncatedTo(ChronoUnit.SECONDS).toString());
        parameters.put("Version", "2019-02-28");

        StringJoiner query = new StringJoiner("&");
        parameters.forEach((key, value) -> query.add(encode(key) + "=" + encode(value)));
        String canonical = query.toString();
        String signature;
        try {
            Mac mac = Mac.getInstance("HmacSHA1");
            mac.init(new SecretKeySpec(
                    (accessKeySecret + "&").getBytes(StandardCharsets.UTF_8), "HmacSHA1"));
            signature = Base64.getEncoder().encodeToString(mac.doFinal(
                    ("GET&" + encode("/") + "&" + encode(canonical))
                            .getBytes(StandardCharsets.UTF_8)));
        } catch (GeneralSecurityException e) {
            throw unavailable();
        }

        long remaining = deadlineNanos - System.nanoTime();
        if (remaining <= 0) throw timeout();
        URI requestUri = URI.create(endpoint + "?" + canonical + "&Signature=" + encode(signature));
        HttpRequest request = HttpRequest.newBuilder(requestUri)
                .timeout(Duration.ofNanos(remaining))
                .header("Accept", "application/json")
                .GET()
                .build();
        HttpResponse<byte[]> response;
        try {
            response = client.send(request, HttpResponse.BodyHandlers.ofByteArray());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw timeout();
        } catch (Exception e) {
            throw unavailable();
        }
        if (response.statusCode() != 200 || response.body().length > MAX_RESPONSE)
            throw unavailable();

        JsonNode body;
        try {
            body = json.reader()
                    .with(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
                    .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                    .readTree(response.body());
        } catch (Exception e) {
            throw unavailable();
        }
        JsonNode token = body == null ? null : body.get("Token");
        if (token == null || !token.isObject()
                || !token.path("Id").isTextual()
                || !token.path("ExpireTime").canConvertToLong()) {
            throw unavailable();
        }
        String value = token.path("Id").textValue();
        long expires = token.path("ExpireTime").longValue();
        if (!validToken(value) || expires <= nowEpochSecond) throw unavailable();
        int configuredSkew = settings.getAliyunTokenRefreshSkewSeconds();
        long skew = Math.min(3600, Math.max(60, configuredSkew));
        long refreshAt = Math.max(nowEpochSecond + 1, expires - skew);
        return new CachedToken(value, refreshAt);
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8)
                .replace("+", "%20")
                .replace("*", "%2A")
                .replace("%7E", "~");
    }

    private static boolean validToken(String value) {
        return value != null && !value.isBlank() && value.length() <= 4096
                && value.chars().allMatch(c -> c >= 0x21 && c <= 0x7e);
    }

    private static AsrFailure unavailable() {
        return new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
    }

    private static AsrFailure timeout() {
        return new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT", null, true);
    }

    private record CachedToken(String value, long refreshAtEpochSecond) {}
}
