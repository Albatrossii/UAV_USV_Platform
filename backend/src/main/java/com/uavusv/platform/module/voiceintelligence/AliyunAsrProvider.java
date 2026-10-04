package com.uavusv.platform.module.voiceintelligence;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/** One-sentence recognition. Audio stays in memory; the temporary Token never reaches the browser or logs. */
@Component
@ConditionalOnProperty(name = "app.voiceintelligence.provider", havingValue = "aliyun")
public class AliyunAsrProvider implements SpeechProvider {
    private static final int SAMPLE_RATE = 16000;
    private static final int MAX_PCM = SAMPLE_RATE * 2 * 60;
    private static final int MAX_RESPONSE = 16384;
    private final AsrSettings settings;
    private final ObjectMapper json;
    private final URI testEndpoint;
    private final AliyunTokenManager tokens;
    private final HttpClient client =
            HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(3))
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .build();

    @Autowired
    public AliyunAsrProvider(AsrSettings settings, ObjectMapper json) {
        this(settings, json, null, new AliyunTokenManager(settings, json));
    }

    // Only tests in this package may substitute a loopback HTTP server. Production uses fixed HTTPS hosts.
    AliyunAsrProvider(AsrSettings settings, ObjectMapper json, URI testEndpoint) {
        this(settings, json, testEndpoint, new AliyunTokenManager(settings, json));
    }

    AliyunAsrProvider(
            AsrSettings settings,
            ObjectMapper json,
            URI testEndpoint,
            AliyunTokenManager tokens) {
        if (testEndpoint != null
                && !("http".equals(testEndpoint.getScheme())
                        && "127.0.0.1".equals(testEndpoint.getHost()))) {
            throw new IllegalArgumentException("test endpoint must be loopback HTTP");
        }
        this.settings = settings;
        this.json = json;
        this.testEndpoint = testEndpoint;
        this.tokens = tokens;
    }

    @Override
    public Transcript transcribe(Audio audio, long deadlineNanos) {
        URI endpoint = endpoint();
        String appKey = settings.getAliyunAppKey();
        if (appKey == null || !appKey.matches("[A-Za-z0-9]{1,128}")
                || settings.getAliyunModelAlias() == null
                || settings.getAliyunModelAlias().isBlank()
                || settings.getAliyunModelAlias().length() > 96) {
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        }
        Wav wav = wav(audio, deadlineNanos);
        String token = tokens.token(deadlineNanos);
        long remaining = deadlineNanos - System.nanoTime();
        if (remaining <= 0) throw timeout(false);
        String query = "appkey=" + URLEncoder.encode(appKey, StandardCharsets.UTF_8)
                + "&format=wav&sample_rate=16000&enable_punctuation_prediction=true";
        URI uri = URI.create(endpoint.toString() + "?" + query);
        HttpRequest request;
        try {
            request = HttpRequest.newBuilder(uri)
                    .timeout(Duration.ofNanos(remaining))
                    .header("X-NLS-Token", token)
                    .header("Content-Type", "application/octet-stream")
                    .POST(HttpRequest.BodyPublishers.ofByteArray(wav.bytes()))
                    .build();
        } catch (RuntimeException e) {
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        }
        CompletableFuture<HttpResponse<byte[]>> future;
        try {
            future = client.sendAsync(request, response -> new LimitedBody());
        } catch (RuntimeException e) {
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        }
        HttpResponse<byte[]> response;
        try {
            remaining = deadlineNanos - System.nanoTime();
            if (remaining <= 0) throw new TimeoutException();
            response = future.get(remaining, TimeUnit.NANOSECONDS);
        } catch (InterruptedException e) {
            future.cancel(true);
            Thread.currentThread().interrupt();
            throw timeout(true);
        } catch (TimeoutException e) {
            future.cancel(true);
            throw timeout(true);
        } catch (ExecutionException e) {
            future.cancel(true);
            Throwable cause = e.getCause();
            if (cause instanceof HttpTimeoutException) throw timeout(true);
            if (cause instanceof BodyLimit)
                throw new AsrFailure(502, "VOICE_PROVIDER_INVALID_RESPONSE", null, true);
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE", null, true);
        }
        JsonNode body;
        try {
            body = json.reader()
                    .with(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
                    .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                    .readTree(response.body());
        } catch (Exception e) {
            if (response.statusCode() == 429)
                throw new AsrFailure(429, "VOICE_RATE_LIMITED", 2, false);
            if (response.statusCode() != 200)
                throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
            throw new AsrFailure(502, "VOICE_PROVIDER_INVALID_RESPONSE", null, true);
        }
        if (body == null || !body.isObject() || !body.path("status").isIntegralNumber()) {
            if (response.statusCode() == 429)
                throw new AsrFailure(429, "VOICE_RATE_LIMITED", 2, false);
            if (response.statusCode() != 200)
                throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
            throw new AsrFailure(502, "VOICE_PROVIDER_INVALID_RESPONSE", null, true);
        }
        int status = body.path("status").intValue();
        if (response.statusCode() == 429 || status == 40000005)
            throw new AsrFailure(429, "VOICE_RATE_LIMITED", 2, false);
        if (status == 40000001)
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        if (status == 40000004) throw timeout(false);
        if (status == 41010100 || status == 41010101)
            throw new AsrFailure(415, "VOICE_AUDIO_FORMAT_UNSUPPORTED");
        if (response.statusCode() != 200 || status != 20000000)
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        JsonNode result = body.get("result");
        if (result == null || !result.isTextual())
            throw new AsrFailure(502, "VOICE_PROVIDER_INVALID_RESPONSE", null, true);
        String text = result.textValue();
        if (text.isBlank()) throw new AsrFailure(422, "VOICE_NO_SPEECH");
        if (text.codePointCount(0, text.length()) > 500)
            throw new AsrFailure(422, "VOICE_TRANSCRIPT_TOO_LONG");
        return new Transcript(
                audio.requestId(), text, wav.durationMs(), "aliyun-isi-rest-v1",
                "aliyun-asr", settings.getAliyunModelAlias());
    }

    private URI endpoint() {
        if (testEndpoint != null) return testEndpoint;
        if (settings.getAliyunRegion() == null)
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        String host = switch (settings.getAliyunRegion()) {
            case "cn-shanghai" -> "nls-gateway-cn-shanghai.aliyuncs.com";
            case "cn-beijing" -> "nls-gateway-cn-beijing.aliyuncs.com";
            case "cn-shenzhen" -> "nls-gateway-cn-shenzhen.aliyuncs.com";
            default -> null;
        };
        if (host == null) throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        return URI.create("https://" + host + "/stream/v1/asr");
    }

    private record Wav(byte[] bytes, int durationMs) {}

    private Wav wav(Audio audio, long deadlineNanos) {
        if (audio.bytes() == null || audio.bytes().length == 0)
            throw new AsrFailure(400, "VOICE_AUDIO_EMPTY");
        if (audio.bytes().length > AudioMultipart.MAX_AUDIO)
            throw new AsrFailure(413, "VOICE_AUDIO_TOO_LARGE");
        if (!Set.of("audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "audio/mpeg")
                .contains(audio.mime()))
            throw new AsrFailure(415, "VOICE_AUDIO_FORMAT_UNSUPPORTED");
        if ("audio/wav".equals(audio.mime())) {
            Wav direct = qualifiedWav(audio.bytes());
            if (direct != null) return direct;
        }
        byte[] pcm = convert(audio.bytes(), deadlineNanos);
        if (pcm.length == 0) throw new AsrFailure(422, "VOICE_NO_SPEECH");
        if ((pcm.length & 1) != 0)
            throw new AsrFailure(415, "VOICE_AUDIO_FORMAT_UNSUPPORTED");
        int durationMs = (int) ((long) pcm.length * 1000 / (SAMPLE_RATE * 2));
        if (durationMs < 1) throw new AsrFailure(422, "VOICE_NO_SPEECH");
        if (durationMs > 60000) throw new AsrFailure(413, "VOICE_AUDIO_TOO_LONG");
        ByteBuffer wav = ByteBuffer.allocate(44 + pcm.length).order(ByteOrder.LITTLE_ENDIAN);
        wav.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(36 + pcm.length);
        wav.put("WAVEfmt ".getBytes(StandardCharsets.US_ASCII)).putInt(16);
        wav.putShort((short) 1).putShort((short) 1).putInt(SAMPLE_RATE);
        wav.putInt(SAMPLE_RATE * 2).putShort((short) 2).putShort((short) 16);
        wav.put("data".getBytes(StandardCharsets.US_ASCII)).putInt(pcm.length).put(pcm);
        return new Wav(wav.array(), durationMs);
    }

    private static Wav qualifiedWav(byte[] bytes) {
        if (bytes.length < 44
                || !ascii(bytes, 0, "RIFF")
                || !ascii(bytes, 8, "WAVE")
                || Integer.toUnsignedLong(le32(bytes, 4)) + 8 != bytes.length) return null;
        int at = 12, dataLength = -1;
        boolean exactPcm = false;
        while (at + 8 <= bytes.length) {
            long size = Integer.toUnsignedLong(le32(bytes, at + 4));
            long next = at + 8L + size + (size & 1);
            if (next > bytes.length) return null;
            if (ascii(bytes, at, "fmt ") && size >= 16) {
                int f = at + 8;
                exactPcm = le16(bytes, f) == 1 && le16(bytes, f + 2) == 1
                        && le32(bytes, f + 4) == SAMPLE_RATE
                        && le32(bytes, f + 8) == SAMPLE_RATE * 2
                        && le16(bytes, f + 12) == 2 && le16(bytes, f + 14) == 16;
            }
            if (ascii(bytes, at, "data")) {
                if (dataLength >= 0) return null;
                dataLength = (int) size;
            }
            at = (int) next;
        }
        if (!exactPcm || dataLength < 0 || at != bytes.length) return null;
        if (dataLength == 0) throw new AsrFailure(400, "VOICE_AUDIO_EMPTY");
        if (dataLength > MAX_PCM) throw new AsrFailure(413, "VOICE_AUDIO_TOO_LONG");
        if ((dataLength & 1) != 0) return null;
        int durationMs = (int) ((long) dataLength * 1000 / (SAMPLE_RATE * 2));
        if (durationMs < 1) throw new AsrFailure(422, "VOICE_NO_SPEECH");
        return new Wav(bytes, durationMs);
    }

    private static boolean ascii(byte[] bytes, int at, String value) {
        return at + value.length() <= bytes.length
                && new String(bytes, at, value.length(), StandardCharsets.US_ASCII).equals(value);
    }

    private static int le16(byte[] bytes, int at) {
        return (bytes[at] & 255) | ((bytes[at + 1] & 255) << 8);
    }

    private static int le32(byte[] bytes, int at) {
        return (bytes[at] & 255) | ((bytes[at + 1] & 255) << 8)
                | ((bytes[at + 2] & 255) << 16) | ((bytes[at + 3] & 255) << 24);
    }

    private byte[] convert(byte[] input, long deadlineNanos) {
        String executable = settings.getAliyunFfmpegPath();
        if (executable == null || executable.isBlank())
            throw new AsrFailure(503, "VOICE_AUDIO_CONVERTER_UNAVAILABLE");
        Process process;
        try {
            process = new ProcessBuilder(
                    executable, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-threads", "1", "-protocol_whitelist", "pipe", "-i", "pipe:0",
                    "-map", "0:a:0", "-vn", "-sn", "-dn",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1")
                    .start();
        } catch (IOException e) {
            throw new AsrFailure(503, "VOICE_AUDIO_CONVERTER_UNAVAILABLE");
        }
        CompletableFuture<Void> writer = CompletableFuture.runAsync(() -> {
            try (OutputStream out = process.getOutputStream()) {
                out.write(input);
            } catch (IOException ignored) {
                // ffmpeg may close stdin immediately for an unsupported file.
            }
        });
        CompletableFuture<Void> stderr = CompletableFuture.runAsync(() -> {
            try (InputStream stream = process.getErrorStream()) {
                stream.transferTo(OutputStream.nullOutputStream());
            } catch (IOException ignored) {
            }
        });
        CompletableFuture<byte[]> stdout = CompletableFuture.supplyAsync(() -> {
            try (InputStream stream = process.getInputStream();
                    ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                byte[] block = new byte[8192];
                int count;
                while ((count = stream.read(block)) != -1) {
                    if (out.size() + count > MAX_PCM) {
                        process.destroyForcibly();
                        throw new AudioTooLong();
                    }
                    out.write(block, 0, count);
                }
                return out.toByteArray();
            } catch (IOException e) {
                throw new CompletionException(e);
            }
        });
        try {
            long remaining = Math.min(TimeUnit.SECONDS.toNanos(30), deadlineNanos - System.nanoTime());
            if (remaining <= 0 || !process.waitFor(remaining, TimeUnit.NANOSECONDS))
                throw timeout(false);
            remaining = deadlineNanos - System.nanoTime();
            if (remaining <= 0) throw timeout(false);
            byte[] pcm = stdout.get(remaining, TimeUnit.NANOSECONDS);
            if (process.exitValue() != 0)
                throw new AsrFailure(415, "VOICE_AUDIO_FORMAT_UNSUPPORTED");
            return pcm;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw timeout(false);
        } catch (TimeoutException e) {
            throw timeout(false);
        } catch (ExecutionException e) {
            if (e.getCause() instanceof AudioTooLong)
                throw new AsrFailure(413, "VOICE_AUDIO_TOO_LONG");
            throw new AsrFailure(415, "VOICE_AUDIO_FORMAT_UNSUPPORTED");
        } finally {
            if (process.isAlive()) process.destroyForcibly();
            writer.cancel(true);
            stderr.cancel(true);
            stdout.cancel(true);
        }
    }

    private static AsrFailure timeout(boolean uncertain) {
        return new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT", null, uncertain);
    }

    private static final class AudioTooLong extends RuntimeException {}
    private static final class BodyLimit extends RuntimeException {}

    private static final class LimitedBody implements HttpResponse.BodySubscriber<byte[]> {
        private final CompletableFuture<byte[]> result = new CompletableFuture<>();
        private final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        private Flow.Subscription subscription;

        public CompletionStage<byte[]> getBody() {
            return result;
        }

        public void onSubscribe(Flow.Subscription s) {
            subscription = s;
            s.request(1);
        }

        public void onNext(List<ByteBuffer> buffers) {
            for (ByteBuffer buffer : buffers) {
                if (bytes.size() + buffer.remaining() > MAX_RESPONSE) {
                    subscription.cancel();
                    result.completeExceptionally(new BodyLimit());
                    return;
                }
                byte[] next = new byte[buffer.remaining()];
                buffer.get(next);
                bytes.writeBytes(next);
            }
            subscription.request(1);
        }

        public void onError(Throwable error) {
            result.completeExceptionally(error);
        }

        public void onComplete() {
            result.complete(bytes.toByteArray());
        }
    }
}
