package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.*;

import java.nio.charset.StandardCharsets;
import java.util.*;

class AudioMultipartTests {
    static String ID = "11111111-1111-4111-8111-111111111111";

    static byte[] body(String id, String mime, byte[] audio) {
        var out = new java.io.ByteArrayOutputStream();
        out.writeBytes(
                ("--b\r\nContent-Disposition: form-data; name=\"requestId\"\r\n\r\n"
                                + id
                                + "\r\n"
                                + "--b\r\n"
                                + "Content-Disposition: form-data; name=\"locale\"\r\n\r\n"
                                + "zh-CN\r\n"
                                + "--b\r\n"
                                + "Content-Disposition: form-data; name=\"audio\";"
                                + " filename=\"test\"\r\n"
                                + "Content-Type: "
                                + mime
                                + "\r\n\r\n")
                        .getBytes(StandardCharsets.UTF_8));
        out.writeBytes(audio);
        out.writeBytes("\r\n--b--\r\n".getBytes(StandardCharsets.US_ASCII));
        return out.toByteArray();
    }

    SpeechProvider.Audio parse(byte[] b) {
        return AudioMultipart.parse("multipart/form-data; boundary=b", b);
    }

    @Test
    void binaryRoundTripAndCodec() {
        byte[] bytes = new byte[256];
        for (int i = 0; i < 256; i++) bytes[i] = (byte) i;
        var a = parse(body(ID, "audio/webm;codecs=opus", bytes));
        assertArrayEquals(bytes, a.bytes());
        assertEquals("audio/webm", a.mime());
    }

    @Test
    void oneByteAndFiveMiBAccepted() {
        assertEquals(1, parse(body(ID, "audio/mpeg", new byte[1])).bytes().length);
        assertEquals(
                AudioMultipart.MAX_AUDIO,
                parse(body(ID, "audio/mpeg", new byte[AudioMultipart.MAX_AUDIO])).bytes().length);
    }

    @Test
    void emptyRejected() {
        assertEquals(
                "VOICE_AUDIO_EMPTY",
                assertThrows(AsrFailure.class, () -> parse(body(ID, "audio/mpeg", new byte[0])))
                        .code);
    }

    @Test
    void tooLargeRejected() {
        assertEquals(
                413,
                assertThrows(
                                AsrFailure.class,
                                () ->
                                        parse(
                                                body(
                                                        ID,
                                                        "audio/mpeg",
                                                        new byte[AudioMultipart.MAX_AUDIO + 1])))
                        .status);
    }

    @Test
    void totalMultipartOverSixMiBRejected() {
        assertEquals(
                413,
                assertThrows(
                                AsrFailure.class,
                                () -> parse(new byte[AudioMultipart.MAX_BODY + 1]))
                        .status);
    }

    @Test
    void otherFormatRejected() {
        assertEquals(
                415,
                assertThrows(AsrFailure.class, () -> parse(body(ID, "audio/wav", new byte[1])))
                        .status);
    }

    @Test
    void invalidIdRejected() {
        assertEquals(
                400,
                assertThrows(
                                AsrFailure.class,
                                () -> parse(body("wrong", "audio/mpeg", new byte[1])))
                        .status);
    }

    @Test
    void missingClosingBoundaryRejected() {
        byte[] b = body(ID, "audio/mpeg", new byte[1]);
        assertThrows(AsrFailure.class, () -> parse(Arrays.copyOf(b, b.length - 8)));
    }

    @Test
    void unsupportedLocaleRejected() {
        String b =
                new String(body(ID, "audio/mpeg", new byte[1]), StandardCharsets.ISO_8859_1)
                        .replace("\r\nzh-CN\r\n", "\r\nen-US\r\n");
        assertEquals(
                400,
                assertThrows(
                                AsrFailure.class,
                                () -> parse(b.getBytes(StandardCharsets.ISO_8859_1)))
                        .status);
    }

    @Test
    void unknownAndDuplicateFieldsRejected() {
        String b = new String(body(ID, "audio/mpeg", new byte[1]), StandardCharsets.ISO_8859_1);
        for (String name : List.of("locale", "audio", "unknown")) {
            String extra =
                    "--b\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\nx\r\n";
            assertThrows(
                    AsrFailure.class,
                    () -> parse((extra + b).getBytes(StandardCharsets.ISO_8859_1)));
        }
    }
}
