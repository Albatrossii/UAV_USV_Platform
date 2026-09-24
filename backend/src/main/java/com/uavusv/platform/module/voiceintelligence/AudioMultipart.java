package com.uavusv.platform.module.voiceintelligence;

import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Pattern;

/** Bounded in-memory parser: never calls Servlet getParts (which may spool audio). */
public final class AudioMultipart {
    public static final int MAX_AUDIO = 5 * 1024 * 1024, MAX_BODY = 6 * 1024 * 1024;
    public static final Pattern UUID =
            Pattern.compile("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");

    public static SpeechProvider.Audio parse(String contentType, byte[] body) {
        if (body.length > MAX_BODY) throw new AsrFailure(413, "VOICE_AUDIO_TOO_LARGE");
        if (contentType == null
                || !contentType.toLowerCase(Locale.ROOT).startsWith("multipart/form-data;"))
            throw new AsrFailure(415, "VOICE_AUDIO_FORMAT_UNSUPPORTED");
        var bm =
                Pattern.compile(
                                "(?:^|;)\s*boundary=(?:\"([^\"]+)\"|([^;\s]+))",
                                Pattern.CASE_INSENSITIVE)
                        .matcher(contentType);
        if (!bm.find()) throw AsrFailure.invalid();
        String boundary = bm.group(1) != null ? bm.group(1) : bm.group(2);
        if (boundary.length() > 70 || !boundary.matches("[A-Za-z0-9'()+_,./:=?-]+") || bm.find())
            throw AsrFailure.invalid();
        String raw = new String(body, StandardCharsets.ISO_8859_1), marker = "--" + boundary;
        if (!raw.startsWith(marker + "\r\n")) throw AsrFailure.invalid();
        int pos = marker.length() + 2;
        Map<String, byte[]> fields = new HashMap<>();
        String mime = null;
        while (true) {
            int end = raw.indexOf("\r\n\r\n", pos);
            if (end < 0 || end - pos > 2048) throw AsrFailure.invalid();
            Map<String, String> headers = new HashMap<>();
            for (String h : raw.substring(pos, end).split("\r\n")) {
                int colon = h.indexOf(':');
                if (colon < 1) throw AsrFailure.invalid();
                String key = h.substring(0, colon).toLowerCase(Locale.ROOT);
                if (headers.put(key, h.substring(colon + 1).trim()) != null)
                    throw AsrFailure.invalid();
            }
            if (!Set.of("content-disposition", "content-type").containsAll(headers.keySet()))
                throw AsrFailure.invalid();
            String disposition = headers.getOrDefault("content-disposition", "");
            if (!disposition.startsWith("form-data;")) throw AsrFailure.invalid();
            var nm = Pattern.compile("(?:^|;)\s*name=\"([^\"]+)\"").matcher(disposition);
            if (!nm.find()) throw AsrFailure.invalid();
            String name = nm.group(1);
            if (nm.find()
                    || !Set.of("audio", "requestId", "locale").contains(name)
                    || fields.containsKey(name)) throw AsrFailure.invalid();
            int next = raw.indexOf("\r\n" + marker, end + 4);
            if (next < 0) throw AsrFailure.invalid();
            byte[] value = Arrays.copyOfRange(body, end + 4, next);
            if (!name.equals("audio") && value.length > 128) throw AsrFailure.invalid();
            fields.put(name, value);
            if (name.equals("audio"))
                mime =
                        headers.getOrDefault("content-type", "")
                                .split(";", 2)[0]
                                .trim()
                                .toLowerCase(Locale.ROOT);
            pos = next + 2 + marker.length();
            if (raw.startsWith("--", pos)) {
                String tail = raw.substring(pos + 2);
                if (!tail.isEmpty() && !tail.equals("\r\n")) throw AsrFailure.invalid();
                break;
            }
            if (!raw.startsWith("\r\n", pos)) throw AsrFailure.invalid();
            pos += 2;
        }
        if (fields.size() != 3) throw AsrFailure.invalid();
        String id = new String(fields.get("requestId"), StandardCharsets.UTF_8),
                locale = new String(fields.get("locale"), StandardCharsets.UTF_8);
        if (!UUID.matcher(id).matches() || !locale.equals("zh-CN")) throw AsrFailure.invalid();
        byte[] audio = fields.get("audio");
        if (audio.length == 0) throw new AsrFailure(400, "VOICE_AUDIO_EMPTY");
        if (audio.length > MAX_AUDIO) throw new AsrFailure(413, "VOICE_AUDIO_TOO_LARGE");
        if (!Set.of("audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "audio/mpeg")
                .contains(mime))
            throw new AsrFailure(415, "VOICE_AUDIO_FORMAT_UNSUPPORTED");
        return new SpeechProvider.Audio(id, locale, mime, audio);
    }
}
