package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;

import java.time.Instant;
import java.util.Base64;
import java.util.Map;
import java.util.UUID;

class AsrAcceptanceStoreTests {
    AsrAcceptanceStore store;
    JdbcTemplate jdbc;

    @BeforeEach
    void setup() {
        var ds = new JdbcDataSource();
        ds.setURL("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
                new ResourceDatabasePopulator(
                        new ClassPathResource("db/migration/V20__create_voice_asr_acceptance.sql"),
                        new ClassPathResource("db/migration/V21__persist_encrypted_asr_outcomes.sql"))
                .execute(ds);
        jdbc = new JdbcTemplate(ds);
        store = new AsrAcceptanceStore(jdbc, Base64.getEncoder().encodeToString(new byte[32]), new ObjectMapper());
    }

    @Test
    void survivesStoreRecreationAndSeparatesUsers() {
        Instant now = Instant.parse("2026-09-23T00:00:00Z");
        assertEquals(AsrAcceptanceStore.Reservation.NEW, store.reserve(1, "id", "hash", now));
        assertEquals(AsrAcceptanceStore.Reservation.MATCH, store.reserve(1, "id", "hash", now));
        assertEquals(AsrAcceptanceStore.Reservation.NEW, store.reserve(2, "id", "hash", now));
    }

    @Test
    void changedContentConflicts() {
        Instant now = Instant.parse("2026-09-23T00:00:00Z");
        store.reserve(1, "id", "hash-a", now);
        assertEquals(
                AsrAcceptanceStore.Reservation.CONFLICT,
                store.reserve(1, "id", "hash-b", now.plusSeconds(1)));
    }

    @Test
    void sevenDayExpiryOpensNewAcceptanceWindow() {
        Instant now = Instant.parse("2026-09-23T00:00:00Z");
        store.reserve(1, "id", "hash", now);
        assertEquals(
                AsrAcceptanceStore.Reservation.MATCH,
                store.reserve(1, "id", "hash", now.plusSeconds(604799)));
        assertEquals(
                AsrAcceptanceStore.Reservation.NEW,
                store.reserve(1, "id", "hash", now.plusSeconds(604800)));
    }

    @Test
    void cleanupRemovesOnlyExpiredRows() {
        Instant now = Instant.parse("2026-09-23T00:00:00Z");
        store.reserve(1, "expired", "a", now);
        store.reserve(1, "active", "b", now.plusSeconds(1));
        store.cleanup(now.plusSeconds(604800));
        assertEquals(
                AsrAcceptanceStore.Reservation.NEW,
                store.reserve(1, "expired", "a", now.plusSeconds(604800)));
        assertEquals(
                AsrAcceptanceStore.Reservation.MATCH,
                store.reserve(1, "active", "b", now.plusSeconds(604800)));
    }

    @Test
    void encryptedOutcomeRestoresFor24HoursThenLeavesSevenDayTombstone() {
        Instant now = Instant.parse("2026-09-23T00:00:00Z");
        store.reserve(1, "id", "hash", now);
        var outcome = new AsrResponses.Outcome(200, Map.of("code", "SUCCESS", "data", Map.of("text", "暂停任务")), null);
        store.saveOutcome(1, "id", "hash", outcome, now);

        byte[] cipher = jdbc.queryForObject("SELECT outcome_cipher FROM voice_asr_acceptance WHERE request_id='id'", byte[].class);
        org.junit.jupiter.api.Assertions.assertFalse(new String(cipher, java.nio.charset.StandardCharsets.UTF_8).contains("暂停任务"));
        var restored = store.restore(1, "id", "hash", now.plusSeconds(86399));
        assertEquals(AsrAcceptanceStore.StoredState.RESTORED, restored.state());
        assertEquals(outcome.body(), restored.outcome().body());

        store.cleanup(now.plusSeconds(86400));
        assertEquals(AsrAcceptanceStore.StoredState.EXPIRED, store.restore(1, "id", "hash", now.plusSeconds(86400)).state());
        assertEquals(AsrAcceptanceStore.Reservation.MATCH, store.reserve(1, "id", "hash", now.plusSeconds(604799)));
        assertEquals(AsrAcceptanceStore.Reservation.NEW, store.reserve(1, "id", "hash", now.plusSeconds(604800)));
    }

    @Test
    void auditContainsNoTranscriptAndExpiresAfterThirtyDays() {
        Instant now = Instant.parse("2026-09-23T00:00:00Z");
        store.reserve(1, "id", "hash", now);
        store.saveOutcome(1, "id", "hash", new AsrResponses.Outcome(422, Map.of("code", "VOICE_NO_SPEECH"), null), now);
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM voice_asr_audit", Integer.class));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM voice_asr_audit WHERE CONCAT(id,request_id,outcome_status,created_at,expires_at) LIKE '%暂停任务%'", Integer.class));
        store.cleanup(now.plusSeconds(30L * 24 * 60 * 60));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM voice_asr_audit", Integer.class));
    }
}
