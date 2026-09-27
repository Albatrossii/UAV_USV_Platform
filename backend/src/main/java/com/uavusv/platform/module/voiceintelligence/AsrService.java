package com.uavusv.platform.module.voiceintelligence;

import com.uavusv.platform.module.voicecontrol.VoiceAccess;
import com.uavusv.platform.module.voicecontrol.VoiceFailure;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.security.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.TimeUnit;

@Service
public class AsrService {
    private static final org.slf4j.Logger LOG = org.slf4j.LoggerFactory.getLogger(AsrService.class);

    private record Key(long user, String id) {}

    private static final class Entry {
        final String hash;
        Instant completed;
        AsrResponses.Outcome outcome;

        Entry(String h) {
            hash = h;
        }
    }

    private final Map<Key, Entry> entries = new HashMap<>();
    private final Map<Long, ArrayDeque<Instant>> rates = new HashMap<>();
    private final VoiceAccess access;
    private final AsrSettings settings;
    private final SpeechProvider provider;
    private final AsrAcceptanceStore acceptances;
    private final Clock clock;
    private boolean occupied = false, quarantined = false;

    @org.springframework.beans.factory.annotation.Autowired
    public AsrService(VoiceAccess a, AsrSettings s, SpeechProvider p, AsrAcceptanceStore store) {
        this(a, s, p, store, Clock.systemUTC());
    }

    AsrService(VoiceAccess a, AsrSettings s, SpeechProvider p, AsrAcceptanceStore store, Clock c) {
        access = a;
        settings = s;
        provider = p;
        acceptances = store;
        clock = c;
    }

    public long authorize() {
        long u = access.user(true);
        if (!settings.isEnabled()) throw new AsrFailure(503, "VOICE_INTELLIGENCE_DISABLED");
        return u;
    }

    public AsrResponses.Outcome transcribe(long user, SpeechProvider.Audio audio, long deadline) {
        access.require(user, true);
        if (!settings.isEnabled()) throw new AsrFailure(503, "VOICE_INTELLIGENCE_DISABLED");
        long started = System.nanoTime();
        String hash = fingerprint(audio);
        Key key = new Key(user, audio.requestId());
        Entry entry;
        synchronized (this) {
            cleanup();
            entry = entries.get(key);
            if (entry != null) {
                if (!entry.hash.equals(hash)) throw new AsrFailure(409, "IDEMPOTENCY_CONFLICT");
                if (entry.outcome == null)
                    throw new AsrFailure(409, "VOICE_REQUEST_IN_PROGRESS", 2, false);
                access.require(user, true);
                return entry.outcome;
            }
            if (entries.size() >= 1000 || occupied)
                throw new AsrFailure(429, "VOICE_RATE_LIMITED", 2, false);
            var q = rates.computeIfAbsent(user, k -> new ArrayDeque<>());
            Instant now = clock.instant();
            while (!q.isEmpty() && !q.peekFirst().isAfter(now.minusSeconds(60))) q.removeFirst();
            if (q.size() >= 10)
                throw new AsrFailure(
                        429,
                        "VOICE_RATE_LIMITED",
                        Math.max(
                                1,
                                (int)
                                                Duration.between(now, q.peekFirst().plusSeconds(60))
                                                        .toSeconds()
                                        + 1),
                        false);
            AsrAcceptanceStore.Reservation reservation;
            try {
                reservation = acceptances.reserve(user, audio.requestId(), hash, now);
            } catch (RuntimeException e) {
                throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
            }
            if (reservation == AsrAcceptanceStore.Reservation.CONFLICT)
                throw new AsrFailure(409, "IDEMPOTENCY_CONFLICT");
            if (reservation == AsrAcceptanceStore.Reservation.MATCH) {
                AsrAcceptanceStore.Stored stored;
                try {
                    stored = acceptances.restore(user, audio.requestId(), hash, now);
                } catch (RuntimeException e) {
                    throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
                }
                if (stored.state() == AsrAcceptanceStore.StoredState.RESTORED) {
                    access.require(user, true);
                    return stored.outcome();
                }
                if (stored.state() == AsrAcceptanceStore.StoredState.EXPIRED)
                    throw new AsrFailure(409, "VOICE_REQUEST_EXPIRED");
                throw new AsrFailure(409, "VOICE_REQUEST_OUTCOME_UNKNOWN");
            }
            q.add(now);
            entry = new Entry(hash);
            entries.put(key, entry);
            occupied = true;
        }
        AsrResponses.Outcome outcome;
        boolean uncertain = false;
        try {
            if (System.nanoTime() >= deadline)
                throw new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT");
            var t = provider.transcribe(audio, deadline);
            if (System.nanoTime() >= deadline)
                throw new AsrFailure(504, "VOICE_TRANSCRIPTION_TIMEOUT", null, true);
            access.require(user, true);
            var data =
                    Map.of(
                            "requestId",
                            t.requestId(),
                            "text",
                            t.text(),
                            "durationMs",
                            t.durationMs(),
                            "locale",
                            "zh-CN",
                            "provider",
                            "local-asr",
                            "model",
                            settings.getModelAlias());
            outcome =
                    new AsrResponses.Outcome(200, AsrResponses.body("SUCCESS", "操作成功", data), null);
        } catch (AsrFailure e) {
            outcome = AsrResponses.error(e);
            uncertain = e.uncertain;
        } catch (VoiceFailure e) {
            outcome = AsrResponses.error(new AsrFailure(e.status, e.code));
        } catch (Exception e) {
            outcome = AsrResponses.error(new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE"));
            uncertain = true;
        }
        synchronized (this) {
            entry.outcome = outcome;
            entry.completed = clock.instant();
            quarantined |= uncertain;
            occupied = quarantined;
        }
        try {
            acceptances.saveOutcome(user, audio.requestId(), hash, outcome, entry.completed);
        } catch (RuntimeException e) {
            LOG.warn("ASR outcome persistence deferred user={} requestId={}", user, audio.requestId());
        }
        LOG.info(
                "ASR completed user={} requestId={} status={} elapsedMs={} quarantined={}",
                user,
                audio.requestId(),
                outcome.status(),
                TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started),
                uncertain);
        access.require(user, true);
        return outcome;
    }

    @Scheduled(fixedDelay = 60000)
    public synchronized void cleanup() {
        Instant now = clock.instant();
        entries.values()
                .removeIf(e -> e.completed != null && !e.completed.plusSeconds(1800).isAfter(now));
        rates.values().removeIf(q -> q.isEmpty() || !q.peekLast().isAfter(now.minusSeconds(60)));
        try {
            acceptances.cleanup(now);
        } catch (RuntimeException e) {
            LOG.warn("ASR acceptance cleanup deferred because persistence is unavailable");
        }
    }

    static String fingerprint(SpeechProvider.Audio a) {
        try {
            var md = MessageDigest.getInstance("SHA-256");
            String digest = HexFormat.of().formatHex(md.digest(a.bytes()));
            return digest + ":" + a.locale() + ":" + a.mime();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    synchronized int size() {
        return entries.size();
    }
}
