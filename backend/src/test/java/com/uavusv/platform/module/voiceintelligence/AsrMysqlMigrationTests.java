package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.time.Instant;
import java.util.UUID;

@EnabledIfEnvironmentVariable(named = "VOICE_TEST_MYSQL_URL", matches = ".+")
class AsrMysqlMigrationTests {
    String database;
    JdbcTemplate admin;
    boolean created;

    @Test
    void flywayV20AndPersistentAcceptanceWorkOnMysql() {
        String root = System.getenv("VOICE_TEST_MYSQL_URL");
        String user = System.getenv("VOICE_TEST_MYSQL_USER");
        String password = System.getenv("VOICE_TEST_MYSQL_PASSWORD");
        if (!root.matches("jdbc:mysql://[^/]+/\\?.*"))
            throw new IllegalArgumentException("Test URL must target server root, not a database");
        database = "uav_usv_asr_test_" + UUID.randomUUID().toString().replace("-", "");
        admin = new JdbcTemplate(new DriverManagerDataSource(root, user, password));
        admin.execute("CREATE DATABASE " + database + " CHARACTER SET utf8mb4");
        created = true;
        var ds =
                new DriverManagerDataSource(
                        root.replace("/?", "/" + database + "?"), user, password);
        var migration = Flyway.configure().dataSource(ds).load().migrate();
        assertEquals(21, migration.migrationsExecuted);
        var store = new AsrAcceptanceStore(new JdbcTemplate(ds), java.util.Base64.getEncoder().encodeToString(new byte[32]), new com.fasterxml.jackson.databind.ObjectMapper());
        Instant now = Instant.parse("2026-09-23T00:00:00Z");
        assertEquals(AsrAcceptanceStore.Reservation.NEW, store.reserve(1, "id", "hash", now));
        assertEquals(AsrAcceptanceStore.Reservation.MATCH, store.reserve(1, "id", "hash", now));
        var outcome = new AsrResponses.Outcome(200, java.util.Map.of("code", "SUCCESS", "data", java.util.Map.of("text", "暂停任务")), null);
        store.saveOutcome(1, "id", "hash", outcome, now);
        assertEquals(outcome.body(), store.restore(1, "id", "hash", now.plusSeconds(1)).outcome().body());
    }

    @AfterEach
    void removeOnlyOwnedDatabase() {
        if (created && database.matches("uav_usv_asr_test_[0-9a-f]{32}"))
            admin.execute("DROP DATABASE " + database);
    }
}
