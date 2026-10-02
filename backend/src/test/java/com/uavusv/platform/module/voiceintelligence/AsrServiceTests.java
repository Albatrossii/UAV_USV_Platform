package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.uavusv.platform.module.voicecontrol.*;

import org.junit.jupiter.api.*;

import java.time.*;
import java.util.*;
import java.util.concurrent.*;

class AsrServiceTests {
    static class Time extends Clock {
        Instant now = Instant.parse("2026-09-23T00:00:00Z");

        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        public Clock withZone(ZoneId z) {
            return this;
        }

        public Instant instant() {
            return now;
        }
    }

    VoiceAccess access;
    AsrSettings settings;
    SpeechProvider provider;
    AsrAcceptanceStore acceptances;
    Time clock;
    AsrService service;

    @BeforeEach
    void setup() {
        access = mock(VoiceAccess.class);
        settings = new AsrSettings();
        settings.setEnabled(true);
        provider = mock(SpeechProvider.class);
        acceptances = mock(AsrAcceptanceStore.class);
        when(acceptances.reserve(anyLong(), anyString(), anyString(), any()))
                .thenReturn(AsrAcceptanceStore.Reservation.NEW);
        when(acceptances.restore(anyLong(), anyString(), anyString(), any()))
                .thenReturn(AsrAcceptanceStore.Stored.unknown());
        clock = new Time();
        service = new AsrService(access, settings, provider, acceptances, clock);
        when(provider.transcribe(any(), anyLong()))
                .thenAnswer(
                        i -> {
                            var a = (SpeechProvider.Audio) i.getArgument(0);
                            if (a == null) return null;
                            return new SpeechProvider.Transcript(a.requestId(), "暂停任务", 2000, "r1");
                        });
    }

    SpeechProvider.Audio audio(String id) {
        return new SpeechProvider.Audio(id, "zh-CN", "audio/mpeg", new byte[] {1, 2, 3});
    }

    AsrResponses.Outcome call(long user, String id) {
        return service.transcribe(
                user, audio(id), System.nanoTime() + TimeUnit.SECONDS.toNanos(120));
    }

    @Test
    void cloudTranscriptMetadataIsReturnedWithoutChangingThePublicEnvelope() {
        when(provider.transcribe(any(), anyLong()))
                .thenAnswer(i -> {
                    var a = (SpeechProvider.Audio) i.getArgument(0);
                    return new SpeechProvider.Transcript(
                            a.requestId(), "开始任务", 1000, "aliyun-isi-rest-v1",
                            "aliyun-asr", "aliyun-shiyinshi-v1");
                });
        var outcome = call(1, UUID.randomUUID().toString());
        assertEquals(200, outcome.status());
        @SuppressWarnings("unchecked")
        var data = (Map<String, Object>) outcome.body().get("data");
        assertEquals("aliyun-asr", data.get("provider"));
        assertEquals("aliyun-shiyinshi-v1", data.get("model"));
    }

    @Test
    void replayPreservesResultAndTimestamp() {
        var id = UUID.randomUUID().toString();
        var first = call(1, id);
        assertSame(first, call(1, id));
        verify(provider, times(1)).transcribe(any(), anyLong());
    }

    @Test
    void sameKeyDifferentAudioConflicts() {
        var id = UUID.randomUUID().toString();
        call(1, id);
        var e =
                assertThrows(
                        AsrFailure.class,
                        () ->
                                service.transcribe(
                                        1,
                                        new SpeechProvider.Audio(
                                                id, "zh-CN", "audio/mpeg", new byte[] {4}),
                                        Long.MAX_VALUE));
        assertEquals("IDEMPOTENCY_CONFLICT", e.code);
    }

    @Test
    void sameIdDifferentUsersIndependent() {
        String id = UUID.randomUUID().toString();
        call(1, id);
        call(2, id);
        verify(provider, times(2)).transcribe(any(), anyLong());
    }

    @Test
    void revokedUserCannotReadCache() {
        String id = UUID.randomUUID().toString();
        call(1, id);
        doThrow(new VoiceFailure(403, "FORBIDDEN")).when(access).require(1, true);
        assertThrows(VoiceFailure.class, () -> call(1, id));
    }

    @Test
    void revokedDuringRecognitionCannotReceiveText() {
        when(provider.transcribe(any(), anyLong()))
                .thenAnswer(
                        i -> {
                            doThrow(new VoiceFailure(403, "FORBIDDEN"))
                                    .when(access)
                                    .require(1, true);
                            return new SpeechProvider.Transcript(
                                    ((SpeechProvider.Audio) i.getArgument(0)).requestId(),
                                    "secret",
                                    20,
                                    "r1");
                        });
        assertThrows(VoiceFailure.class, () -> call(1, UUID.randomUUID().toString()));
    }

    @Test
    void terminalFailuresCached() {
        when(provider.transcribe(any(), anyLong()))
                .thenThrow(new AsrFailure(422, "VOICE_NO_SPEECH"));
        String id = UUID.randomUUID().toString();
        assertEquals(422, call(1, id).status());
        assertEquals(422, call(1, id).status());
        verify(provider, times(1)).transcribe(any(), anyLong());
    }

    @Test
    void expiredMemoryResultBecomesUnknownAndDoesNotRepeatInference() {
        String id = UUID.randomUUID().toString();
        call(1, id);
        clock.now = clock.now.plusSeconds(1799);
        call(1, id);
        verify(provider, times(1)).transcribe(any(), anyLong());
        clock.now = clock.now.plusSeconds(1);
        service.cleanup();
        assertEquals(0, service.size());
        when(acceptances.reserve(eq(1L), eq(id), anyString(), any()))
                .thenReturn(AsrAcceptanceStore.Reservation.MATCH);
        var e = assertThrows(AsrFailure.class, () -> call(1, id));
        assertEquals("VOICE_REQUEST_OUTCOME_UNKNOWN", e.code);
        verify(provider, times(1)).transcribe(any(), anyLong());
    }

    @Test
    void capacityKeepsExistingKeys() {
        String first = UUID.randomUUID().toString();
        call(1, first);
        for (int i = 2; i <= 1000; i++) call(i, UUID.randomUUID().toString());
        assertEquals(1000, service.size());
        assertEquals(200, call(1, first).status());
        assertEquals(
                429,
                assertThrows(AsrFailure.class, () -> call(1001, UUID.randomUUID().toString()))
                        .status);
    }

    @Test
    void rateLimitDoesNotConsumeEntry() {
        for (int i = 0; i < 10; i++) call(1, UUID.randomUUID().toString());
        assertEquals(
                429,
                assertThrows(AsrFailure.class, () -> call(1, UUID.randomUUID().toString())).status);
        assertEquals(10, service.size());
        clock.now = clock.now.plusSeconds(60);
        assertEquals(200, call(1, UUID.randomUUID().toString()).status());
    }

    @Test
    void timeoutQuarantinesNewTasksButReplaysOld() {
        when(provider.transcribe(any(), anyLong()))
                .thenThrow(new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT", null, true));
        String id = UUID.randomUUID().toString();
        assertEquals(504, call(1, id).status());
        assertEquals(504, call(1, id).status());
        assertEquals(
                429,
                assertThrows(AsrFailure.class, () -> call(1, UUID.randomUUID().toString())).status);
    }

    @Test
    void disabledDoesNotCallProvider() {
        settings.setEnabled(false);
        assertEquals(
                503,
                assertThrows(AsrFailure.class, () -> call(1, UUID.randomUUID().toString())).status);
        verifyNoInteractions(provider);
    }

    @Test
    void pendingSurvivesCleanupAndPreventsConcurrentInference() throws Exception {
        var entered = new CountDownLatch(1);
        var release = new CountDownLatch(1);
        String id = UUID.randomUUID().toString();
        when(provider.transcribe(any(), anyLong()))
                .thenAnswer(
                        i -> {
                            entered.countDown();
                            assertTrue(release.await(5, TimeUnit.SECONDS));
                            return new SpeechProvider.Transcript(id, "文本", 10, "r1");
                        });
        var pool = Executors.newSingleThreadExecutor();
        try {
            var future = pool.submit(() -> call(1, id));
            assertTrue(entered.await(2, TimeUnit.SECONDS));
            clock.now = clock.now.plusSeconds(3600);
            service.cleanup();
            assertEquals(1, service.size());
            var e = assertThrows(AsrFailure.class, () -> call(1, id));
            assertEquals("VOICE_REQUEST_IN_PROGRESS", e.code);
            assertEquals(2, e.retryAfter);
            assertEquals(
                    429,
                    assertThrows(AsrFailure.class, () -> call(2, UUID.randomUUID().toString()))
                            .status);
            release.countDown();
            assertEquals(200, future.get().status());
            verify(provider, times(1)).transcribe(any(), anyLong());
        } finally {
            release.countDown();
            pool.shutdownNow();
        }
    }

    @Test
    void distinctUsersShareOneGlobalInferenceSlot() throws Exception {
        var entered = new CountDownLatch(1);
        var release = new CountDownLatch(1);
        when(provider.transcribe(any(), anyLong()))
                .thenAnswer(
                        invocation -> {
                            entered.countDown();
                            assertTrue(release.await(5, TimeUnit.SECONDS));
                            var request = (SpeechProvider.Audio) invocation.getArgument(0);
                            return new SpeechProvider.Transcript(
                                    request.requestId(), "文本", 10, "r1");
                        });

        var pool = Executors.newSingleThreadExecutor();
        try {
            var first = pool.submit(() -> call(101, UUID.randomUUID().toString()));
            assertTrue(entered.await(2, TimeUnit.SECONDS));

            var busy =
                    assertThrows(
                            AsrFailure.class,
                            () -> call(202, UUID.randomUUID().toString()));
            assertEquals(429, busy.status);
            assertEquals("VOICE_RATE_LIMITED", busy.code);
            assertEquals(2, busy.retryAfter);

            release.countDown();
            assertEquals(200, first.get().status());
            verify(provider, times(1)).transcribe(any(), anyLong());
        } finally {
            release.countDown();
            pool.shutdownNow();
        }
    }

    @Test
    void restartWithAcceptedKeyReturnsUnknownWithoutInference() {
        String id = UUID.randomUUID().toString();
        call(1, id);
        var restarted = new AsrService(access, settings, provider, acceptances, clock);
        when(acceptances.reserve(eq(1L), eq(id), anyString(), any()))
                .thenReturn(AsrAcceptanceStore.Reservation.MATCH);
        var e =
                assertThrows(
                        AsrFailure.class,
                        () ->
                                restarted.transcribe(
                                        1,
                                        audio(id),
                                        System.nanoTime() + TimeUnit.SECONDS.toNanos(120)));
        assertEquals("VOICE_REQUEST_OUTCOME_UNKNOWN", e.code);
        verify(provider, times(1)).transcribe(any(), anyLong());
    }

    @Test
    void restartReturnsPersistedOutcomeWithoutInference() {
        String id = UUID.randomUUID().toString();
        var expected = new AsrResponses.Outcome(200, Map.of("code", "SUCCESS"), null);
        when(acceptances.reserve(eq(1L), eq(id), anyString(), any()))
                .thenReturn(AsrAcceptanceStore.Reservation.MATCH);
        when(acceptances.restore(eq(1L), eq(id), anyString(), any()))
                .thenReturn(new AsrAcceptanceStore.Stored(AsrAcceptanceStore.StoredState.RESTORED, expected));
        assertSame(expected, call(1, id));
        verifyNoInteractions(provider);
    }

    @Test
    void expiredPersistedOutcomeDoesNotRepeatInference() {
        String id = UUID.randomUUID().toString();
        when(acceptances.reserve(eq(1L), eq(id), anyString(), any()))
                .thenReturn(AsrAcceptanceStore.Reservation.MATCH);
        when(acceptances.restore(eq(1L), eq(id), anyString(), any()))
                .thenReturn(new AsrAcceptanceStore.Stored(AsrAcceptanceStore.StoredState.EXPIRED, null));
        var failure = assertThrows(AsrFailure.class, () -> call(1, id));
        assertEquals("VOICE_REQUEST_EXPIRED", failure.code);
        verifyNoInteractions(provider);
    }

    @Test
    void persistenceFailureRejectsBeforeInference() {
        when(acceptances.reserve(anyLong(), anyString(), anyString(), any()))
                .thenThrow(new IllegalStateException("database unavailable"));
        var e = assertThrows(AsrFailure.class, () -> call(1, UUID.randomUUID().toString()));
        assertEquals(503, e.status);
        verifyNoInteractions(provider);
    }

    @Test
    void cleanupFailureDoesNotBlockAnExistingMemoryReplay() {
        String id = UUID.randomUUID().toString();
        var first = call(1, id);
        doThrow(new IllegalStateException("database unavailable")).when(acceptances).cleanup(any());
        assertSame(first, call(1, id));
        verify(provider, times(1)).transcribe(any(), anyLong());
    }
}
