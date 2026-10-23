package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.node.ObjectNode;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.*;
import java.util.function.Supplier;

/**
 * P0 has one standalone slot. This database mutex also serializes concurrent workers. No pipe I/O
 * in transactions.
 */
@Component
public class VoiceStore {
    final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    private final VoiceJson json;

    public VoiceStore(JdbcTemplate jdbc, PlatformTransactionManager tm, VoiceJson json) {
        this.jdbc = jdbc;
        this.tx = new TransactionTemplate(tm);
        this.json = json;
    }

    public <T> T locked(Supplier<T> work) {
        // Commit domain invalidations (expired plans etc.), then surface the business error.
        Object result =
                tx.execute(
                        s -> {
                            jdbc.queryForObject(
                                    "SELECT id FROM voice_control_lock WHERE id=1 FOR UPDATE",
                                    Integer.class);
                            try {
                                return work.get();
                            } catch (VoiceFailure e) {
                                return e;
                            }
                        });
        if (result instanceof VoiceFailure e) throw e;
        @SuppressWarnings("unchecked")
        T value = (T) result;
        return value;
    }

    public List<ObjectNode> query(String sql, Object... args) {
        return jdbc.query(sql, (rs, n) -> json.read(rs.getString("data_json")), args);
    }

    public ObjectNode get(String table, String id) {
        var rows = query("SELECT data_json FROM " + table + " WHERE id=?", id);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public void save(String table, ObjectNode n) {
        if ("voice_execution".equals(table))
            jdbc.update(
                    "UPDATE voice_execution SET data_json=?,state=? WHERE id=?",
                    n.toString(),
                    n.path("state").asText(),
                    n.path("_id").asText());
        else
            jdbc.update(
                    "UPDATE " + table + " SET data_json=? WHERE id=?",
                    n.toString(),
                    n.path("_id").asText());
    }

    public void context(ObjectNode n) {
        jdbc.update(
                "INSERT INTO"
                    + " voice_runtime_context(id,owner_id,generation,algorithm_run_id,data_json)"
                    + " VALUES (?,?,?,?,?)",
                n.path("_id").asText(),
                n.path("_owner").asLong(),
                n.path("runtimeGeneration").asText(),
                n.path("algorithmRunId").asText(),
                n.toString());
    }

    public void proposal(ObjectNode n) {
        jdbc.update(
                "INSERT INTO voice_proposal(id,owner_id,runtime_ref,data_json) VALUES (?,?,?,?)",
                n.path("_id").asText(),
                n.path("_owner").asLong(),
                n.path("plan").path("runtimeRef").asText(),
                n.toString());
    }

    public void execution(ObjectNode n) {
        execution(n, true);
    }

    public void execution(ObjectNode n, boolean createOutbox) {
        jdbc.update(
                "INSERT INTO"
                    + " voice_execution(id,proposal_id,command_id,runtime_ref,owner_id,state,data_json)"
                    + " VALUES (?,?,?,?,?,?,?)",
                n.path("_id").asText(),
                n.path("proposalId").asText(),
                n.path("commandId").asText(),
                n.path("runtimeRef").asText(),
                n.path("_owner").asLong(),
                n.path("state").asText(),
                n.toString());
        if (createOutbox)
            jdbc.update(
                    "INSERT INTO voice_outbox(execution_id,status,claimed_at) VALUES (?,'READY',NULL)",
                    n.path("_id").asText());
    }

    public String replay(long user, String op, String key, String hash) {
        var rows =
                jdbc.queryForList(
                        "SELECT body_hash,resource_id FROM voice_idempotency WHERE user_id=? AND"
                            + " operation_key=? AND idempotency_key=?",
                        user,
                        op,
                        key);
        if (rows.isEmpty()) return null;
        if (!hash.equals(rows.get(0).get("body_hash")))
            throw VoiceFailure.conflict("IDEMPOTENCY_CONFLICT");
        return (String) rows.get(0).get("resource_id");
    }

    public void remember(long user, String op, String key, String hash, String resource) {
        jdbc.update(
                "INSERT INTO"
                    + " voice_idempotency(user_id,operation_key,idempotency_key,body_hash,resource_id)"
                    + " VALUES (?,?,?,?,?)",
                user,
                op,
                key,
                hash,
                resource);
    }

    public boolean busy(String ref) {
        return query("SELECT data_json FROM voice_execution WHERE runtime_ref=?", ref).stream()
                .anyMatch(
                        n ->
                                Set.of("QUEUED", "DISPATCHED", "ACCEPTED", "EXECUTING", "TIMED_OUT")
                                        .contains(n.path("state").asText()));
    }

    public void audit(String ref, String kind, String detail, String at) {
        jdbc.update(
                "INSERT INTO voice_audit(id,runtime_ref,kind,detail,created_at) VALUES (?,?,?,?,?)",
                VoiceJson.uuid(),
                ref,
                kind,
                detail,
                at);
    }
}
