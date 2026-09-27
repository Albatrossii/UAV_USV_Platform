package com.uavusv.platform.module.voiceintelligence;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.*;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

@Component
public class AsrAcceptanceStore {
    static final String ENDPOINT = "transcriptions";
    static final long RESULT_SECONDS = 24L * 60 * 60;
    static final long RETENTION_SECONDS = 7L * 24 * 60 * 60;
    static final long AUDIT_SECONDS = 30L * 24 * 60 * 60;

    enum Reservation { NEW, MATCH, CONFLICT }
    enum StoredState { RESTORED, EXPIRED, UNKNOWN }
    record Stored(StoredState state, AsrResponses.Outcome outcome) {
        static Stored unknown() { return new Stored(StoredState.UNKNOWN, null); }
    }

    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final SecretKeySpec key;
    private final SecureRandom random = new SecureRandom();

    @Autowired
    public AsrAcceptanceStore(JdbcTemplate jdbc, AsrSettings settings, ObjectMapper json) {
        this(jdbc, settings.getResultEncryptionKey(), json);
    }

    AsrAcceptanceStore(JdbcTemplate jdbc, String encodedKey, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
        if (encodedKey == null || encodedKey.isBlank()) key = null;
        else {
            byte[] decoded;
            try { decoded = Base64.getDecoder().decode(encodedKey); }
            catch (IllegalArgumentException e) { throw new IllegalStateException("Invalid ASR result encryption key"); }
            if (decoded.length != 32) throw new IllegalStateException("ASR result encryption key must be 32 bytes");
            key = new SecretKeySpec(decoded, "AES");
        }
    }

    public Reservation reserve(long user, String requestId, String hash, Instant now) {
        String instant = now.toString();
        jdbc.update("DELETE FROM voice_asr_acceptance WHERE user_id=? AND endpoint_key=? AND request_id=? AND expires_at<=?", user, ENDPOINT, requestId, instant);
        try {
            jdbc.update("INSERT INTO voice_asr_acceptance(user_id,endpoint_key,request_id,content_hash,accepted_at,expires_at) VALUES (?,?,?,?,?,?)",
                    user, ENDPOINT, requestId, hash, instant, now.plusSeconds(RETENTION_SECONDS).toString());
            return Reservation.NEW;
        } catch (DuplicateKeyException e) {
            var hashes = jdbc.queryForList("SELECT content_hash FROM voice_asr_acceptance WHERE user_id=? AND endpoint_key=? AND request_id=?", String.class, user, ENDPOINT, requestId);
            if (hashes.isEmpty()) throw e;
            return hash.equals(hashes.get(0)) ? Reservation.MATCH : Reservation.CONFLICT;
        }
    }

    public void saveOutcome(long user, String requestId, String hash, AsrResponses.Outcome outcome, Instant now) {
        if (key == null) return;
        try {
            byte[] iv = new byte[12]; random.nextBytes(iv);
            byte[] plain = json.writeValueAsBytes(Map.of("status", outcome.status(), "body", outcome.body(), "retryAfter", outcome.retryAfter() == null ? -1 : outcome.retryAfter()));
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(128, iv));
            cipher.updateAAD(aad(user, requestId, hash));
            byte[] encrypted = cipher.doFinal(plain);
            int changed = jdbc.update("UPDATE voice_asr_acceptance SET outcome_status=?,outcome_cipher=?,outcome_iv=?,completed_at=?,result_expires_at=? WHERE user_id=? AND endpoint_key=? AND request_id=? AND content_hash=?",
                    outcome.status(), encrypted, iv, now.toString(), now.plusSeconds(RESULT_SECONDS).toString(), user, ENDPOINT, requestId, hash);
            if (changed != 1) throw new IllegalStateException("ASR acceptance disappeared before outcome persistence");
            jdbc.update("INSERT INTO voice_asr_audit(id,user_id,request_id,outcome_status,created_at,expires_at) VALUES (?,?,?,?,?,?)",
                    UUID.randomUUID().toString(), user, requestId, outcome.status(), now.toString(), now.plusSeconds(AUDIT_SECONDS).toString());
        } catch (Exception e) { throw new IllegalStateException("Unable to persist ASR outcome", e); }
    }

    public Stored restore(long user, String requestId, String hash, Instant now) {
        if (key == null) return Stored.unknown();
        var rows = jdbc.queryForList("SELECT outcome_cipher,outcome_iv,completed_at,result_expires_at FROM voice_asr_acceptance WHERE user_id=? AND endpoint_key=? AND request_id=? AND content_hash=?",
                user, ENDPOINT, requestId, hash);
        if (rows.isEmpty() || rows.get(0).get("completed_at") == null) return Stored.unknown();
        var row = rows.get(0);
        String expires = Objects.toString(row.get("result_expires_at"), "");
        byte[] encrypted = (byte[]) row.get("outcome_cipher");
        byte[] iv = (byte[]) row.get("outcome_iv");
        if (expires.isBlank() || !Instant.parse(expires).isAfter(now) || encrypted == null || iv == null) {
            clearResult(user, requestId);
            return new Stored(StoredState.EXPIRED, null);
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, iv));
            cipher.updateAAD(aad(user, requestId, hash));
            Map<String, Object> value = json.readValue(cipher.doFinal(encrypted), new TypeReference<>() {});
            int status = ((Number) value.get("status")).intValue();
            @SuppressWarnings("unchecked") Map<String, Object> body = (Map<String, Object>) value.get("body");
            int retry = ((Number) value.get("retryAfter")).intValue();
            return new Stored(StoredState.RESTORED, new AsrResponses.Outcome(status, body, retry < 0 ? null : retry));
        } catch (Exception e) { throw new IllegalStateException("Unable to restore ASR outcome", e); }
    }

    private void clearResult(long user, String requestId) {
        jdbc.update("UPDATE voice_asr_acceptance SET outcome_cipher=NULL,outcome_iv=NULL WHERE user_id=? AND endpoint_key=? AND request_id=?", user, ENDPOINT, requestId);
    }

    public void cleanup(Instant now) {
        jdbc.update("UPDATE voice_asr_acceptance SET outcome_cipher=NULL,outcome_iv=NULL WHERE result_expires_at IS NOT NULL AND result_expires_at<=?", now.toString());
        jdbc.update("DELETE FROM voice_asr_acceptance WHERE expires_at<=?", now.toString());
        jdbc.update("DELETE FROM voice_asr_audit WHERE expires_at<=?", now.toString());
    }

    private static byte[] aad(long user, String requestId, String hash) {
        return (user + ":" + ENDPOINT + ":" + requestId + ":" + hash).getBytes(StandardCharsets.UTF_8);
    }
}
