package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.*;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContextHolder;

import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

class VoiceControlTests {
    static Path repositoryFile(String relativePath) {
        Path current = Path.of("").toAbsolutePath().normalize();
        for (int depth = 0; current != null && depth < 5; depth++, current = current.getParent()) {
            Path candidate = current.resolve(relativePath).normalize();
            if (Files.isRegularFile(candidate)) return candidate;
        }
        throw new IllegalStateException("Could not locate repository file '" + relativePath
                + "' from working directory " + Path.of("").toAbsolutePath());
    }

    static class TestTime extends VoiceTime {
        Instant now = Instant.parse("2026-09-19T08:00:00Z");
        long ns = 1;

        @Override
        public Instant now() {
            return now;
        }

        @Override
        public long nanos() {
            return ns;
        }

        void advance(long millis) {
            now = now.plusMillis(millis);
            ns += millis * 1_000_000;
        }
    }

    VoiceJson j;
    VoiceStore s;
    VoiceAccess a;
    VoiceSettings settings;
    RuntimeContextRegistry r;
    VoiceCommandApplicationService app;
    VoiceDispatcher worker;
    VoicePresentationService presentation;
    TestTime t;
    final List<JsonNode> sent = new CopyOnWriteArrayList<>();
    final AtomicBoolean alive = new AtomicBoolean(true);
    ObjectNode c;

    javax.sql.DataSource dataSource() {
        var ds = new JdbcDataSource();
        ds.setURL("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        return ds;
    }

    @BeforeEach
    void setup() {
        var ds = dataSource();
        new ResourceDatabasePopulator(
                        new ClassPathResource("db/migration/V19__create_voice_control_p0.sql"))
                .execute(ds);
        var jdbc = new JdbcTemplate(ds);
        jdbc.execute(
                "CREATE TABLE app_user(id BIGINT PRIMARY KEY,username VARCHAR(64),role"
                    + " VARCHAR(32),enabled BOOLEAN)");
        jdbc.update(
                "INSERT INTO app_user"
                    + " VALUES(1,'alice','ADMIN',true),(2,'bob','ADMIN',true),(3,'viewer','VIEWER',true)");
        j = new VoiceJson();
        t = new TestTime();
        settings = new VoiceSettings(true);
        s = new VoiceStore(jdbc, new DataSourceTransactionManager(ds), j);
        a = new VoiceAccess(jdbc);
        r = new RuntimeContextRegistry(s, j, t, a, settings);
        app = new VoiceCommandApplicationService(s, j, t, a, r, settings);
        worker = new VoiceDispatcher(s, j, t, r);
        presentation = new VoicePresentationService(s, j, t, a, r);
        login("alice");
        c = r.register(7001, "fixture");
        r.attach(ref(), gen(), sent::add, alive::get);
        var ready = event("RUNTIME_READY");
        ready.put("adapterId", "test").put("state", "PREPARED").put("stateVersion", 0);
        ready.putArray("capabilities").add("START").add("PAUSE").add("RESUME").add("STOP");
        worker.receive(ref(), gen(), ready);
        var frame =
                j.read(
                        "{\"sequence\":1,\"agents\":[{\"deviceCode\":\"USV-001\"},{\"deviceCode\":\"UAV-001\"}]}");
        r.frame(ref(), gen(), frame);
        heartbeat("RUNNING", 3, 1);
        r.channel(ref()).sceneNanos = t.nanos();
    }

    @AfterEach
    void clear() {
        SecurityContextHolder.clearContext();
    }

    static void login(String name) {
        SecurityContextHolder.getContext()
                .setAuthentication(
                        new UsernamePasswordAuthenticationToken(
                                name,
                                "",
                                AuthorityUtils.createAuthorityList(
                                        "ROLE_" + (name.equals("viewer") ? "VIEWER" : "ADMIN"))));
    }

    String ref() {
        return c.path("runtimeRef").asText();
    }

    String gen() {
        return c.path("runtimeGeneration").asText();
    }

    ObjectNode event(String kind) {
        return j.object()
                .put("protocolVersion", RuntimeContextRegistry.PROTOCOL)
                .put("kind", kind)
                .put("runtimeRef", ref())
                .put("runtimeGeneration", gen());
    }

    void heartbeat(String state, long version, long sequence) {
        var e = event("HEARTBEAT");
        e.put("runtimeState", state)
                .put("stateVersion", version)
                .put("heartbeatSequence", sequence)
                .put("lastFrameSequence", 1);
        worker.receive(ref(), gen(), e);
    }

    ObjectNode request(String action) {
        return j.object()
                .put("runtimeRef", ref())
                .put("runtimeGeneration", gen())
                .put("expectedContextVersion", app.context(ref()).path("contextVersion").asLong())
                .put("intent", "MISSION_" + action);
    }

    ObjectNode proposal(String action) {
        return app.createProposal(VoiceJson.uuid(), request(action)).data();
    }

    ObjectNode confirmation(ObjectNode p) {
        return j.object()
                .put("expectedPlanVersion", 1)
                .put("expectedPlanHash", p.path("planHash").asText());
    }

    ObjectNode confirm(ObjectNode p) {
        return app.confirm(p.path("proposalId").asText(), VoiceJson.uuid(), confirmation(p)).data();
    }

    ObjectNode execution(ObjectNode combined) {
        return (ObjectNode) combined.path("execution");
    }

    void result(ObjectNode e, String status, long sequence) {
        var n = event("COMMAND_RESULT");
        n.set("commandId", e.path("commandId"));
        n.put("eventSequence", sequence).put("status", status);
        boolean success = status.equals("SUCCEEDED");
        String target = e.path("action").asText().equals("STOP") ? "STOPPED" : "PAUSED";
        n.put("runtimeState", success ? target : "RUNNING")
                .put("stateVersion", success ? 4 : 3)
                .put("lastFrameSequence", 1)
                .putNull("errorCode");
        n.putArray("affectedDeviceCodes").add("UAV-001").add("USV-001");
        worker.receive(ref(), gen(), n);
    }

    int count(String table) {
        return s.jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class);
    }

    void error(String code, Runnable action) {
        assertEquals(code, assertThrows(VoiceFailure.class, action::run).code);
    }

    @Test
    void frozenGoldenHashAndAllSchemaFixtures() throws Exception {
        var fixtures =
                j.mapper.readTree(
                        Files.readString(repositoryFile("docs/voice-control-p0/fixtures.json")));
        assertEquals(
                fixtures.path("goldenPlan").path("sha256").asText(),
                j.hash(fixtures.path("goldenPlan").path("data")));
        for (var item : fixtures.path("cases")) {
            if (item.path("valid").asBoolean())
                assertDoesNotThrow(
                        () -> j.validate(item.path("definition").asText(), item.path("data")),
                        item.path("id").asText());
            else
                assertThrows(
                        VoiceFailure.class,
                        () -> j.validate(item.path("definition").asText(), item.path("data")),
                        item.path("id").asText());
        }
        assertEquals(
                "{\"text\":\"\\u4e2d\\u6587\\ud83d\\ude00\"}",
                j.canonical(j.object().put("text", "中文😀")));
        assertArrayEquals(
                Files.readAllBytes(repositoryFile("docs/voice-control-p0/contracts.schema.json")),
                getClass()
                        .getResourceAsStream("/voicecontrol/contracts.schema.json")
                        .readAllBytes());
    }

    @Test
    void happyPathAndPublicShapes() {
        var p = proposal("PAUSE");
        j.validate("Proposal", p);
        var e = execution(confirm(p));
        j.validate("Execution", e);
        j.validate("RuntimeContext", app.context(ref()));
        assertEquals(0, sent.size());
        worker.tick();
        assertEquals(1, sent.size());
        j.validate("Command", sent.get(0));
        result(e, "ACCEPTED", 1);
        result(e, "SUCCEEDED", 2);
        result(e, "ACCEPTED", 1);
        result(e, "SUCCEEDED", 2);
        assertEquals(
                "SUCCEEDED", app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(2, count("voice_command_event"));
        assertEquals("PAUSED", app.context(ref()).path("state").asText());
    }

    @Test
    void sameKeyDifferentBodyRejectedBeforeAnyExecution() {
        var key = VoiceJson.uuid();
        var body = request("PAUSE");
        app.createProposal(key, body);
        error("IDEMPOTENCY_CONFLICT", () -> app.createProposal(key, request("STOP")));
        assertEquals(1, count("voice_proposal"));
        assertEquals(0, count("voice_execution"));
    }

    @Test
    void unknownFieldAndDuplicateJsonRejected() {
        var b = request("PAUSE");
        b.put("ownerUserId", 2);
        error("INVALID_REQUEST", () -> app.createProposal(VoiceJson.uuid(), b));
        error("INVALID_REQUEST", () -> j.read("{\"x\":1,\"x\":2}"));
        assertEquals(0, count("voice_proposal"));
    }

    @Test
    void crossOwnerNotFoundAndServiceRoleRechecked() {
        var p = proposal("PAUSE");
        login("bob");
        error("RESOURCE_NOT_FOUND", () -> confirm(p));
        assertTrue(app.contexts().isEmpty());
        login("alice");
        s.jdbc.update("UPDATE app_user SET role='VIEWER' WHERE id=1");
        error("FORBIDDEN", () -> confirm(p));
        assertEquals(0, count("voice_execution"));
    }

    @Test
    void concurrentConfirmDifferentKeysHasOneExecutionAndOneWrite() throws Exception {
        var p = proposal("PAUSE");
        var pool = Executors.newFixedThreadPool(12);
        var gate = new CountDownLatch(1);
        var tasks = new ArrayList<Future<ObjectNode>>();
        try {
            for (int i = 0; i < 12; i++)
                tasks.add(
                        pool.submit(
                                () -> {
                                    login("alice");
                                    gate.await();
                                    try {
                                        return confirm(p);
                                    } finally {
                                        SecurityContextHolder.clearContext();
                                    }
                                }));
            gate.countDown();
            for (var task : tasks) assertNotNull(task.get(10, TimeUnit.SECONDS));
        } finally {
            pool.shutdownNow();
        }
        assertEquals(1, count("voice_execution"));
        assertEquals(1, count("voice_outbox"));
        worker.tick();
        worker.tick();
        assertEquals(1, sent.size());
    }

    @Test
    void concurrentConfirmCancelHasOneWinner() throws Exception {
        var p = proposal("PAUSE");
        var pool = Executors.newFixedThreadPool(2);
        var gate = new CountDownLatch(1);
        try {
            var f1 =
                    pool.submit(
                            () -> {
                                login("alice");
                                gate.await();
                                try {
                                    confirm(p);
                                    return "confirm";
                                } catch (VoiceFailure ex) {
                                    return ex.code;
                                }
                            });
            var f2 =
                    pool.submit(
                            () -> {
                                login("alice");
                                gate.await();
                                try {
                                    app.cancel(
                                            p.path("proposalId").asText(),
                                            VoiceJson.uuid(),
                                            confirmation(p));
                                    return "cancel";
                                } catch (VoiceFailure ex) {
                                    return ex.code;
                                }
                            });
            gate.countDown();
            var outcomes = Set.of(f1.get(10, TimeUnit.SECONDS), f2.get(10, TimeUnit.SECONDS));
            assertTrue(
                    outcomes.equals(Set.of("confirm", "ALREADY_CONFIRMED"))
                            || outcomes.equals(Set.of("cancel", "PROPOSAL_CANCELLED")));
            assertTrue(count("voice_execution") <= 1);
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void expiresExactlyAtDeadlineAndPersistsExpiration() {
        var p = proposal("PAUSE");
        t.advance(30000);
        error("PROPOSAL_EXPIRED", () -> confirm(p));
        assertEquals(
                "EXPIRED", app.proposal(p.path("proposalId").asText()).path("status").asText());
        assertEquals(0, count("voice_execution"));
    }

    @Test
    void confirmedReplaySurvivesExpiry() {
        var p = proposal("PAUSE");
        var first = confirm(p);
        t.advance(31000);
        assertEquals(first, confirm(p));
        assertEquals(1, count("voice_execution"));
    }

    @Test
    void changedContextInvalidatesFrozenPlan() {
        var p = proposal("PAUSE");
        heartbeat("PAUSED", 4, 2);
        error("CONTEXT_CHANGED", () -> confirm(p));
        assertEquals(
                "INVALIDATED", app.proposal(p.path("proposalId").asText()).path("status").asText());
    }

    @Test
    void ordinaryFramesDoNotInvalidatePlan() {
        var p = proposal("PAUSE");
        r.frame(
                ref(),
                gen(),
                j.read(
                        "{\"sequence\":2,\"agents\":[{\"deviceCode\":\"UAV-001\"},{\"deviceCode\":\"USV-001\"}]}"));
        assertNotNull(confirm(p));
    }

    @Test
    void workerRechecksRevokedRoleBeforeWriting() {
        var e = execution(confirm(proposal("PAUSE")));
        s.jdbc.update("UPDATE app_user SET role='VIEWER' WHERE id=1");
        worker.tick();
        assertEquals(0, sent.size());
        assertEquals(
                "INVALIDATED",
                s.get("voice_execution", e.path("executionId").asText()).path("state").asText());
    }

    @Test
    void heartbeatBoundaryAndPauseDoesNotNeedScene() {
        t.advance(5000);
        r.channel(ref()).sceneNanos = null;
        assertNotNull(proposal("PAUSE"));
        t.advance(1);
        error("RUNTIME_UNAVAILABLE", () -> proposal("PAUSE"));
    }

    @Test
    void timeoutRemainsUnknownOccupiedAndLateSuccessReconciles() {
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        t.advance(5000);
        worker.tick();
        assertEquals(
                "TIMED_OUT", app.execution(e.path("executionId").asText()).path("state").asText());
        heartbeat("RUNNING", 3, 2);
        var another = proposal("STOP");
        error("EXECUTION_IN_PROGRESS", () -> confirm(another));
        result(e, "SUCCEEDED", 2);
        var actual = app.execution(e.path("executionId").asText());
        assertEquals("SUCCESS", actual.path("outcome").asText());
        assertFalse(actual.path("timedOutAt").isNull());
        assertEquals(
                1, sent.stream().filter(n -> n.path("kind").asText().equals("COMMAND")).count());
    }

    @Test
    void queryUnknownNeverResendsAndMaxThreeQueries() {
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        for (int i = 0; i < 6; i++) {
            t.advance(5000);
            worker.tick();
        }
        assertEquals(
                1, sent.stream().filter(n -> n.path("kind").asText().equals("COMMAND")).count());
        assertEquals(
                3,
                sent.stream().filter(n -> n.path("kind").asText().equals("STATUS_QUERY")).count());
    }

    @Test
    void badGenerationCannotSettleExecution() {
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        var n = event("HEARTBEAT");
        n.put("runtimeGeneration", VoiceJson.uuid())
                .put("heartbeatSequence", 8)
                .put("runtimeState", "STOPPED")
                .put("stateVersion", 8)
                .put("lastFrameSequence", 1);
        worker.receive(ref(), gen(), n);
        assertEquals(
                "DISPATCHED", app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("RUNNING", app.context(ref()).path("state").asText());
    }

    @Test
    void mismatchedMembersCannotSettleExecution() {
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        var n = event("COMMAND_RESULT");
        n.set("commandId", e.path("commandId"));
        n.put("eventSequence", 1)
                .put("status", "SUCCEEDED")
                .put("runtimeState", "PAUSED")
                .put("stateVersion", 4)
                .put("lastFrameSequence", 1)
                .putNull("errorCode");
        n.putArray("affectedDeviceCodes").add("UAV-001");
        worker.receive(ref(), gen(), n);
        assertEquals(
                "DISPATCHED", app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(0, count("voice_command_event"));
    }

    @Test
    void conflictingDuplicateDoesNotOverwriteSuccessfulResult() {
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        result(e, "SUCCEEDED", 2);
        result(e, "ACCEPTED", 2);
        assertEquals(
                "SUCCEEDED", app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(1, count("voice_command_event"));
        assertTrue(r.channel(ref()).faulted);
    }

    @Test
    void shutdownAndRecoveryNeverReplayCommand() {
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        r.channels.clear();
        worker.recover();
        worker.tick();
        assertEquals(1, sent.size());
        assertEquals(
                "TIMED_OUT", app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("LOST", app.context(ref()).path("state").asText());
    }

    @Test
    void disableStopsNewWritesButDrainsAcceptedExecution() {
        var e = execution(confirm(proposal("PAUSE")));
        settings.setEnabled(false);
        error("VOICE_CONTROL_DISABLED", () -> proposal("PAUSE"));
        worker.tick();
        result(e, "SUCCEEDED", 2);
        assertEquals(
                "SUCCEEDED", app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(1, sent.size());
    }

    @Test
    void ambiguousWriteIsNotRetried() {
        r.channel(ref()).faulted = false;
        r.attach(
                ref(),
                gen(),
                n -> {
                    sent.add(n);
                    throw new IllegalStateException("broken after write");
                },
                alive::get);
        r.channel(ref()).heartbeatNanos = t.nanos();
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        worker.tick();
        assertEquals(
                1, sent.stream().filter(n -> n.path("kind").asText().equals("COMMAND")).count());
        assertEquals(
                "UNKNOWN", app.execution(e.path("executionId").asText()).path("outcome").asText());
    }

    @Test
    void presentationBindingEvidenceExpiresAndReplayCannotRefresh() {
        var bind =
                presentation.bind(
                        ref(),
                        VoiceJson.uuid(),
                        j.object().put("runtimeGeneration", gen()).putNull("expectedBindingId"));
        var body =
                j.object()
                        .put("runtimeGeneration", gen())
                        .put("bindingId", bind.path("bindingId").asText())
                        .put("kind", "SCENE_READY")
                        .putNull("executionId");
        var challenge = presentation.challenge(ref(), VoiceJson.uuid(), body);
        var report = body.deepCopy();
        report.set("requestId", challenge.path("requestId"));
        report.set("sequence", challenge.path("sequence"));
        report.put("frameSequence", 1).put("applied", true);
        String key = VoiceJson.uuid();
        assertTrue(presentation.report(ref(), key, report).path("sceneReady").asBoolean());
        t.advance(10001);
        assertFalse(presentation.report(ref(), key, report).path("sceneReady").asBoolean());
        error("SCENE_NOT_READY", () -> presentation.report(ref(), VoiceJson.uuid(), report));
        presentation.bind(
                ref(),
                VoiceJson.uuid(),
                j.object()
                        .put("runtimeGeneration", gen())
                        .put("expectedBindingId", bind.path("bindingId").asText()));
        error("CONTEXT_CHANGED", () -> presentation.challenge(ref(), VoiceJson.uuid(), body));
    }

    @Test
    void boundedLineReaderDrainsOversizedLine() throws Exception {
        byte[] huge =
                ("x".repeat(270000) + "\n{}\n").getBytes(java.nio.charset.StandardCharsets.UTF_8);
        var in = new java.io.ByteArrayInputStream(huge);
        assertTrue(VoiceLines.read(in).contains("OVERSIZED_LINE"));
        assertEquals("{}", VoiceLines.read(in));
        assertNull(VoiceLines.read(in));
    }

    @Test
    void transactionFailureRollsBackProposalExecutionAndOutbox() {
        var p = proposal("PAUSE");
        assertThrows(
                org.springframework.dao.DataAccessException.class,
                () ->
                        s.locked(
                                () -> {
                                    confirm(p);
                                    s.jdbc.update("INSERT INTO voice_control_lock(id) VALUES(1)");
                                    return null;
                                }));
        assertEquals(0, count("voice_execution"));
        assertEquals(0, count("voice_outbox"));
        assertEquals(
                "AWAITING_CONFIRMATION",
                app.proposal(p.path("proposalId").asText()).path("status").asText());
    }

    @Test
    void unknownQueryReplyIsReadOnly() {
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        t.advance(5000);
        worker.tick();
        var query =
                sent.stream()
                        .filter(n -> n.path("kind").asText().equals("STATUS_QUERY"))
                        .findFirst()
                        .orElseThrow();
        var reply = event("STATUS_REPLY");
        reply.set("queryId", query.path("queryId"));
        reply.set("commandId", e.path("commandId"));
        reply.put("known", false).putNull("result");
        worker.receive(ref(), gen(), reply);
        assertEquals(
                "TIMED_OUT", app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(
                1, sent.stream().filter(n -> n.path("kind").asText().equals("COMMAND")).count());
    }

    @Test
    void stateChangedFrameAndHttpReceiptNeverProveSuccess() {
        var e = execution(confirm(proposal("PAUSE")));
        assertEquals("UNKNOWN", e.path("outcome").asText());
        worker.tick();
        r.frame(
                ref(),
                gen(),
                j.read(
                        "{\"sequence\":2,\"agents\":[{\"deviceCode\":\"UAV-001\"},{\"deviceCode\":\"USV-001\"}]}"));
        heartbeat("PAUSED", 4, 2);
        assertEquals(
                "DISPATCHED", app.execution(e.path("executionId").asText()).path("state").asText());
    }

    @Test
    void staleSceneBlocksStartButAllowsPausedLocalSimulationResumeAndStop() {
        heartbeat("PAUSED", 4, 2);
        r.channel(ref()).sceneNanos = null;
        assertNotNull(proposal("RESUME"));
        assertNotNull(proposal("STOP"));
    }

    @Test
    void queueExpiryDoesNotSendOrConsumeCommandSequence() {
        var e = execution(confirm(proposal("PAUSE")));
        t.advance(10000);
        worker.tick();
        assertEquals(0, sent.size());
        assertEquals(
                "DISPATCH_DEADLINE_EXCEEDED",
                app.execution(e.path("executionId").asText()).path("errorCode").asText());
        assertEquals(0, s.get("voice_runtime_context", ref()).path("_nextSequence").asLong());
    }
    @Test
    void threeInvalidEnvelopesMakeChannelReadOnlyWithoutChangingState() {
        worker.receive(ref(), gen(), j.object());
        worker.receive(ref(), gen(), j.object());
        assertFalse(r.channel(ref()).faulted);
        worker.receive(ref(), gen(), j.object());
        assertTrue(r.channel(ref()).faulted);
        assertEquals("RUNNING", app.context(ref()).path("state").asText());
        assertEquals(0, sent.size());
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {"START", "RESUME"})
    void presentationDeadlineSurvivesServiceRecreationAndAcceptsLateReport(String action) {
        heartbeat(action.equals("START") ? "PREPARED" : "PAUSED", 4, 2);
        var e = execution(confirm(proposal(action)));
        String id = e.path("executionId").asText();
        worker.tick();
        t.advance(31000);
        worker.tick();
        assertEquals("TIMED_OUT", app.execution(id).path("state").asText());
        assertEquals("PENDING", app.execution(id).path("presentationStatus").asText());
        var success = event("COMMAND_RESULT");
        success.set("commandId", e.path("commandId"));
        success.put("eventSequence", 2).put("status", "SUCCEEDED")
                .put("runtimeState", "RUNNING").put("stateVersion", 5)
                .put("lastFrameSequence", 1).putNull("errorCode");
        success.putArray("affectedDeviceCodes").add("UAV-001").add("USV-001");
        worker.receive(ref(), gen(), success);
        String deadline = s.locked(() -> s.get("voice_execution", id)).path("_presentationDeadlineAt").asText();
        assertEquals(t.now().plusSeconds(30).toString(), deadline);
        t.advance(29999);
        assertEquals("PENDING", app.execution(id).path("presentationStatus").asText());
        worker.receive(ref(), gen(), success);
        assertEquals(deadline, s.locked(() -> s.get("voice_execution", id)).path("_presentationDeadlineAt").asText());
        t.advance(1);
        app = new VoiceCommandApplicationService(s, j, t, a, r, settings);
        var expired = app.execution(id);
        assertEquals("STALE", expired.path("presentationStatus").asText());
        assertEquals("SUCCEEDED", expired.path("state").asText());
        assertEquals("SUCCESS", expired.path("outcome").asText());
        assertFalse(expired.path("timedOutAt").isNull());
        assertFalse(expired.has("_presentationDeadlineAt"));
        var bound = presentation.bind(ref(), VoiceJson.uuid(), j.object()
                .put("runtimeGeneration", gen()).putNull("expectedBindingId"));
        var body = j.object().put("runtimeGeneration", gen())
                .put("bindingId", bound.path("bindingId").asText())
                .put("kind", "FRAME_APPLIED").put("executionId", id);
        var report = presentation.challenge(ref(), VoiceJson.uuid(), body);
        report.remove("expiresAt");
        report.put("frameSequence", 1).put("applied", true);
        presentation.report(ref(), VoiceJson.uuid(), report);
        assertEquals("REPORTED_APPLIED", app.execution(id).path("presentationStatus").asText());
        t.advance(10001);
        assertEquals("STALE", app.execution(id).path("presentationStatus").asText());
        assertEquals("SUCCEEDED", app.execution(id).path("state").asText());
    }

    @Test
    void pauseDoesNotAcquirePresentationDeadline() {
        var e = execution(confirm(proposal("PAUSE")));
        worker.tick();
        result(e, "SUCCEEDED", 2);
        t.advance(60000);
        var stored = s.locked(() -> s.get("voice_execution", e.path("executionId").asText()));
        assertFalse(stored.has("_presentationDeadlineAt"));
        assertEquals("NOT_REQUIRED", app.execution(e.path("executionId").asText()).path("presentationStatus").asText());
    }

}
