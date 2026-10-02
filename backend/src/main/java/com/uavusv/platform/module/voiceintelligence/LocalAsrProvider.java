package com.uavusv.platform.module.voiceintelligence;

import com.fasterxml.jackson.databind.*;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.Flow;

@Component
@ConditionalOnProperty(name = "app.voiceintelligence.provider", havingValue = "local", matchIfMissing = true)
public class LocalAsrProvider implements SpeechProvider {
    private final AsrSettings settings;
    private final ObjectMapper json;
    private final HttpClient client =
            HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(3))
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .proxy(
                            new ProxySelector() {
                                public List<Proxy> select(URI u) {
                                    return List.of(Proxy.NO_PROXY);
                                }

                                public void connectFailed(URI u, SocketAddress s, IOException e) {}
                            })
                    .build();

    public LocalAsrProvider(AsrSettings s, ObjectMapper j) {
        settings = s;
        json = j;
    }

    public Transcript transcribe(Audio a, long deadline) {
        URI base;
        try {
            base = URI.create(settings.getBaseUrl());
            if (!"http".equals(base.getScheme())
                    || !"127.0.0.1".equals(base.getHost())
                    || base.getPort() < 1
                    || base.getUserInfo() != null
                    || base.getQuery() != null
                    || base.getFragment() != null
                    || !(base.getPath().isEmpty() || base.getPath().equals("/"))
                    || settings.getToken().isBlank()
                    || settings.getModelRevision().isBlank()) throw new IllegalArgumentException();
        } catch (Exception e) {
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        }
        String boundary = "asr-" + UUID.randomUUID();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try {
            part(out, boundary, "requestId", a.requestId());
            part(out, boundary, "locale", a.locale());
            out.write(
                    ("--"
                                    + boundary
                                    + "\r\n"
                                    + "Content-Disposition: form-data; name=\"audio\";"
                                    + " filename=\"audio\"\r\n"
                                    + "Content-Type: "
                                    + a.mime()
                                    + "\r\n\r\n")
                            .getBytes(StandardCharsets.US_ASCII));
            out.write(a.bytes());
            out.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.US_ASCII));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        long ms = Math.min(120000, TimeUnit.NANOSECONDS.toMillis(deadline - System.nanoTime()));
        if (ms < 1) throw new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT");
        CompletableFuture<HttpResponse<byte[]>> future;
        try {
            var req =
                    HttpRequest.newBuilder(base.resolve("/internal/asr/transcriptions"))
                            .timeout(Duration.ofMillis(ms))
                            .header("Authorization", "Bearer " + settings.getToken())
                            .header("X-ASR-Timeout-Ms", Long.toString(ms))
                            .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                            .POST(HttpRequest.BodyPublishers.ofByteArray(out.toByteArray()))
                            .build();
            future = client.sendAsync(req, r -> new LimitedBody());
        } catch (Exception e) {
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        }
        HttpResponse<byte[]> response;
        try {
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) throw new TimeoutException();
            response = future.get(remaining, TimeUnit.NANOSECONDS);
        } catch (InterruptedException e) {
            future.cancel(true);
            Thread.currentThread().interrupt();
            throw new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT", null, true);
        } catch (TimeoutException e) {
            future.cancel(true);
            throw new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT", null, true);
        } catch (ExecutionException e) {
            future.cancel(true);
            Throwable cause = e.getCause();
            if (cause instanceof HttpTimeoutException)
                throw new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT", null, true);
            if (cause instanceof BodyLimit)
                throw new AsrFailure(502, "VOICE_PROVIDER_INVALID_RESPONSE", null, true);
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE", null, true);
        }
        try {
            JsonNode n =
                    json.reader()
                            .with(
                                    com.fasterxml.jackson.core.JsonParser.Feature
                                            .STRICT_DUPLICATE_DETECTION)
                            .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                            .readTree(response.body());
            if (n == null || !n.isObject()) throw invalid();
            if (response.statusCode() == 200) {
                exact(n, Set.of("requestId", "text", "durationMs", "modelRevision"));
                if (!a.requestId().equals(n.path("requestId").asText())
                        || !n.path("text").isTextual()
                        || !n.path("modelRevision").isTextual()
                        || !settings.getModelRevision().equals(n.path("modelRevision").asText())
                        || !n.path("durationMs").isIntegralNumber()
                        || !n.path("durationMs").canConvertToInt()) throw invalid();
                String text = n.path("text").textValue();
                int length = text.codePointCount(0, text.length()),
                        duration = n.path("durationMs").intValue();
                if (text.isBlank() || length > 500 || duration < 1 || duration > 60000)
                    throw invalid();
                return new Transcript(
                        a.requestId(), text, duration, n.path("modelRevision").asText());
            }
            exact(n, Set.of("requestId", "code", "message"));
            if (!n.get("requestId").isNull()
                    && (!n.get("requestId").isTextual()
                            || !a.requestId().equals(n.get("requestId").asText()))) throw invalid();
            if (!n.get("code").isTextual()
                    || !n.get("message").isTextual()
                    || n.get("message").asText().isBlank()
                    || n.get("message").asText().length() > 256) throw invalid();
            if (response.statusCode() == 401 || response.statusCode() == 403) {
                if (!n.get("code").asText().equals("ASR_UNAVAILABLE")) throw invalid();
                throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
            }
            AsrFailure e =
                    switch (n.get("code").asText()) {
                        case "ASR_BUSY" -> new AsrFailure(429, "VOICE_RATE_LIMITED", 2, false);
                        case "ASR_UNAVAILABLE" -> new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
                        case "ASR_TIMEOUT" ->
                                new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT", null, true);
                        case "ASR_NO_SPEECH" -> new AsrFailure(422, "VOICE_NO_SPEECH");
                        case "ASR_AUDIO_TOO_LARGE" -> new AsrFailure(413, "VOICE_AUDIO_TOO_LARGE");
                        case "ASR_AUDIO_TOO_LONG" -> new AsrFailure(413, "VOICE_AUDIO_TOO_LONG");
                        case "ASR_AUDIO_FORMAT_UNSUPPORTED" ->
                                new AsrFailure(415, "VOICE_AUDIO_FORMAT_UNSUPPORTED");
                        case "ASR_TRANSCRIPT_TOO_LONG" ->
                                new AsrFailure(422, "VOICE_TRANSCRIPT_TOO_LONG");
                        default -> invalid();
                    };
            if (e.status != response.statusCode()) throw invalid();
            throw e;
        } catch (AsrFailure e) {
            throw e;
        } catch (Exception e) {
            throw invalid();
        }
    }

    private static AsrFailure invalid() {
        return new AsrFailure(502, "VOICE_PROVIDER_INVALID_RESPONSE");
    }

    private static void exact(JsonNode n, Set<String> expected) {
        var actual = new HashSet<String>();
        n.fieldNames().forEachRemaining(actual::add);
        if (!actual.equals(expected)) throw invalid();
    }

    private static void part(ByteArrayOutputStream o, String b, String n, String v)
            throws IOException {
        o.write(
                ("--"
                                + b
                                + "\r\nContent-Disposition: form-data; name=\""
                                + n
                                + "\"\r\n\r\n"
                                + v
                                + "\r\n")
                        .getBytes(StandardCharsets.UTF_8));
    }

    private static final class BodyLimit extends RuntimeException {}

    private static final class LimitedBody implements HttpResponse.BodySubscriber<byte[]> {
        final CompletableFuture<byte[]> result = new CompletableFuture<>();
        final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        Flow.Subscription subscription;

        public CompletionStage<byte[]> getBody() {
            return result;
        }

        public void onSubscribe(Flow.Subscription s) {
            subscription = s;
            s.request(1);
        }

        public void onNext(List<ByteBuffer> list) {
            for (var b : list) {
                if (bytes.size() + b.remaining() > 16384) {
                    subscription.cancel();
                    result.completeExceptionally(new BodyLimit());
                    return;
                }
                byte[] a = new byte[b.remaining()];
                b.get(a);
                bytes.writeBytes(a);
            }
            subscription.request(1);
        }

        public void onError(Throwable t) {
            result.completeExceptionally(t);
        }

        public void onComplete() {
            result.complete(bytes.toByteArray());
        }
    }
}
