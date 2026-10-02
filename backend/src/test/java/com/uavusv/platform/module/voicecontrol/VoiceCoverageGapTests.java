package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.Arguments;
import java.util.Set;
import java.util.stream.Stream;

/** Named coverage gaps. H2/service fixtures; these do not constitute Unity browser evidence. */
class VoiceCoverageGapTests extends VoiceControlTests {
    static Stream<Arguments> actionStates() {
        return Stream.of("START","PAUSE","RESUME","STOP").flatMap(action ->
            Stream.of("PREPARED","PREVIEW","RUNNING","PAUSED","STOPPED","COMPLETED","CANCELLED","FAILED","LOST")
                .map(state -> Arguments.of(action,state)));
    }
    @ParameterizedTest @MethodSource("actionStates")
    void actionStateMatrix(String action,String state) {
        s.locked(() -> {var ctx=s.get("voice_runtime_context",ref());ctx.put("state",state);s.save("voice_runtime_context",ctx);return null;});
        boolean allowed=switch(action){
            case "START" -> Set.of("PREPARED","PREVIEW").contains(state);
            case "PAUSE" -> state.equals("RUNNING");
            case "RESUME" -> state.equals("PAUSED");
            default -> Set.of("PREPARED","PREVIEW","RUNNING","PAUSED").contains(state);
        };
        if(allowed) assertNotNull(proposal(action));
        else error("INVALID_STATE",()->proposal(action));
        assertEquals(0,sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"VIEWER","OPERATOR","DISABLED"})
    void revocationRejectsCachedConfirmationAndNewWrites(String mode) {
        var p=proposal("PAUSE");String key=VoiceJson.uuid();
        var first=app.confirm(p.path("proposalId").asText(),key,confirmation(p));
        if(mode.equals("DISABLED")) s.jdbc.update("UPDATE app_user SET enabled=false WHERE id=1");
        else s.jdbc.update("UPDATE app_user SET role=? WHERE id=1",mode);
        error("FORBIDDEN",()->app.confirm(p.path("proposalId").asText(),key,confirmation(p)));
        error("FORBIDDEN",()->app.createProposal(VoiceJson.uuid(),request("STOP")));
        error("FORBIDDEN",()->app.cancel(p.path("proposalId").asText(),VoiceJson.uuid(),confirmation(p)));
        worker.tick();
        assertEquals("INVALIDATED",s.get("voice_execution",execution(first.data()).path("executionId").asText()).path("state").asText());
        assertEquals(0,sent.size());assertEquals(1,count("voice_execution"));
    }
    @ParameterizedTest @ValueSource(strings={"QUEUED","DISPATCHED","ACCEPTED","SUCCEEDED"})
    void restartRecoversEachDispatchStageWithoutReplay(String stage) {
        var p=proposal("PAUSE");var e=execution(confirm(p));
        if(!stage.equals("QUEUED")) worker.tick();
        if(stage.equals("ACCEPTED")) result(e,"ACCEPTED",1);
        if(stage.equals("SUCCEEDED")) result(e,"SUCCEEDED",2);
        var pending=proposal("STOP");int writes=sent.size();
        r.channels.clear();worker.recover();worker.tick();worker.recover();worker.tick();
        assertEquals("LOST",app.context(ref()).path("state").asText());
        assertEquals("INVALIDATED",s.get("voice_proposal",pending.path("proposalId").asText()).path("status").asText());
        var actual=app.execution(e.path("executionId").asText());
        assertEquals(stage.equals("QUEUED")?"INVALIDATED":stage.equals("SUCCEEDED")?"SUCCEEDED":"TIMED_OUT",actual.path("state").asText());
        if(stage.equals("DISPATCHED")||stage.equals("ACCEPTED"))assertEquals("UNKNOWN",actual.path("outcome").asText());
        assertEquals(writes,sent.size());assertEquals(1,count("voice_execution"));
    }
    @Test void oldBindingAndGenerationReportsCannotRefreshScene() {
        var bind=presentation.bind(ref(),VoiceJson.uuid(),j.object().put("runtimeGeneration",gen()).putNull("expectedBindingId"));
        var body=j.object().put("runtimeGeneration",gen()).put("bindingId",bind.path("bindingId").asText()).put("kind","SCENE_READY").putNull("executionId");
        var q=presentation.challenge(ref(),VoiceJson.uuid(),body);
        var report=body.deepCopy();report.set("requestId",q.path("requestId"));report.set("sequence",q.path("sequence"));report.put("frameSequence",1).put("applied",true);
        var replacement=presentation.bind(ref(),VoiceJson.uuid(),j.object().put("runtimeGeneration",gen()).put("expectedBindingId",bind.path("bindingId").asText()));
        error("CONTEXT_CHANGED",()->presentation.report(ref(),VoiceJson.uuid(),report));
        report.put("bindingId",replacement.path("bindingId").asText()).put("runtimeGeneration",VoiceJson.uuid());
        error("GENERATION_MISMATCH",()->presentation.report(ref(),VoiceJson.uuid(),report));
        assertFalse(app.context(ref()).path("sceneReady").asBoolean());
        assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
    }
    @Test void lateAcceptedNeverRollsBackSuccess() {
        var e=execution(confirm(proposal("PAUSE")));worker.tick();result(e,"SUCCEEDED",2);result(e,"ACCEPTED",1);
        assertEquals("SUCCEEDED",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("PAUSED",app.context(ref()).path("state").asText());assertEquals(1,sent.size());
    }

    @ParameterizedTest @ValueSource(strings={"ownerUserId","runId","executionBackend","requiresConfirmation"})
    void dangerousUnknownProposalFieldsAreRejected(String field) {
        var body=request("PAUSE");body.put(field,false);
        error("INVALID_REQUEST",()->app.createProposal(VoiceJson.uuid(),body));
        assertEquals(0,count("voice_proposal"));assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"version","hash"})
    void wrongPlanNeverCreatesExecution(String field) {
        var p=proposal("PAUSE");var body=confirmation(p);
        if(field.equals("version"))body.put("expectedPlanVersion",2);else body.put("expectedPlanHash","0".repeat(64));
        var before=s.get("voice_proposal",p.path("proposalId").asText()).deepCopy();
        var failure=assertThrows(VoiceFailure.class,()->app.confirm(p.path("proposalId").asText(),VoiceJson.uuid(),body));
        assertEquals(field.equals("version")?400:409,failure.status);
        assertEquals(field.equals("version")?"INVALID_REQUEST":"PLAN_MISMATCH",failure.code);
        assertEquals(before,s.get("voice_proposal",p.path("proposalId").asText()));
        assertEquals(0,count("voice_execution"));assertEquals(0,count("voice_outbox"));assertEquals(0,sent.size());
    }
    @ParameterizedTest @ValueSource(longs={29999,30000,30001})
    void proposalExpiryAtEachMillisecondBoundary(long age) {
        var p=proposal("PAUSE");t.advance(age);heartbeat("RUNNING",3,2);
        if(age<30000)assertNotNull(confirm(p));else {
            error("PROPOSAL_EXPIRED",()->confirm(p));
            assertEquals("EXPIRED",app.proposal(p.path("proposalId").asText()).path("status").asText());
            assertEquals(0,count("voice_execution"));
        }
        assertEquals(0,sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"legacy","subset"})
    void unsupportedProtocolOrActionNeverWrites(String mode) {
        s.locked(()->{var ctx=s.get("voice_runtime_context",ref());
            if(mode.equals("legacy"))ctx.put("protocolVersion","legacy");else ctx.putArray("capabilities").add("STOP");
            s.save("voice_runtime_context",ctx);return null;});
        error(mode.equals("legacy")?"PROTOCOL_UNSUPPORTED":"UNSUPPORTED_CAPABILITY",()->proposal("PAUSE"));
        assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"START","PAUSE","RESUME","STOP"})
    void staleSceneGatesNewStartButAllowsLocalSimulationResume(String action) {
        String state=action.equals("START")?"PREPARED":action.equals("RESUME")?"PAUSED":"RUNNING";
        heartbeat(state,4,2);t.advance(10001);heartbeat(state,4,3);
        if(action.equals("START"))error("SCENE_NOT_READY",()->proposal(action));
        else assertNotNull(proposal(action));
    }
    @Test void duplicateCancelAndConfirmedCancelNeverWrite() {
        var p=proposal("PAUSE");var id=p.path("proposalId").asText();var key=VoiceJson.uuid();
        app.cancel(id,key,confirmation(p));app.cancel(id,key,confirmation(p));app.cancel(id,VoiceJson.uuid(),confirmation(p));
        assertEquals("CANCELLED",app.proposal(id).path("status").asText());assertEquals(0,count("voice_execution"));
        var another=proposal("STOP");confirm(another);
        error("ALREADY_CONFIRMED",()->app.cancel(another.path("proposalId").asText(),VoiceJson.uuid(),confirmation(another)));
        assertEquals(0,sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"before_write","after_write"})
    void committedSendingFailureDoesNotRetry(String window) {
        r.attach(ref(),gen(),n -> {
            if(n.path("kind").asText().equals("STATUS_QUERY"))return;
            assertEquals("SENDING",s.jdbc.queryForObject("SELECT status FROM voice_outbox",String.class));
            if(window.equals("after_write"))sent.add(n);
            throw new IllegalStateException("injected "+window);
        },alive::get);
        r.channel(ref()).heartbeatNanos=t.nanos();
        var e=execution(confirm(proposal("PAUSE")));worker.tick();worker.tick();
        assertEquals("UNCERTAIN",s.jdbc.queryForObject("SELECT status FROM voice_outbox",String.class));
        assertEquals("TIMED_OUT",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("UNKNOWN",app.execution(e.path("executionId").asText()).path("outcome").asText());
        assertEquals(window.equals("after_write")?1:0,sent.stream().filter(n->n.path("kind").asText().equals("COMMAND")).count());
    }
    @Test void receiptPersistenceFailureIsBufferedAndReconciled() {
        var e=execution(confirm(proposal("PAUSE")));worker.tick();
        s.jdbc.execute("ALTER TABLE voice_command_event RENAME TO temporarily_unavailable_event");
        try {
            result(e,"SUCCEEDED",2);worker.tick();
            assertEquals("DISPATCHED",app.execution(e.path("executionId").asText()).path("state").asText());
            assertEquals(1,sent.size());
        } finally {s.jdbc.execute("ALTER TABLE temporarily_unavailable_event RENAME TO voice_command_event");}
        worker.tick();assertEquals("SUCCEEDED",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(1,count("voice_command_event"));assertEquals(1,sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"ACK","RESULT"})
    void exactCommandTimeoutBoundary(String phase) {
        var e=execution(confirm(proposal("PAUSE")));worker.tick();
        if(phase.equals("RESULT"))result(e,"ACCEPTED",1);
        t.advance(phase.equals("ACK")?4999:14999);worker.tick();
        assertNotEquals("TIMED_OUT",app.execution(e.path("executionId").asText()).path("state").asText());
        t.advance(1);worker.tick();assertEquals("TIMED_OUT",app.execution(e.path("executionId").asText()).path("state").asText());
        result(e,"SUCCEEDED",2);assertEquals("SUCCEEDED",app.execution(e.path("executionId").asText()).path("state").asText());
        assertFalse(app.execution(e.path("executionId").asText()).path("timedOutAt").isNull());
        assertEquals(1,sent.stream().filter(n->n.path("kind").asText().equals("COMMAND")).count());
    }
    @Test void twoWorkersOnlyClaimOneOutbox() throws Exception {
        confirm(proposal("PAUSE"));var second=new VoiceDispatcher(s,j,t,r);
        var gate=new java.util.concurrent.CountDownLatch(1);var pool=java.util.concurrent.Executors.newFixedThreadPool(2);
        try {
            var one=pool.submit(()->{gate.await();worker.tick();return true;});
            var two=pool.submit(()->{gate.await();second.tick();return true;});gate.countDown();
            one.get(10,java.util.concurrent.TimeUnit.SECONDS);two.get(10,java.util.concurrent.TimeUnit.SECONDS);
        } finally {pool.shutdownNow();}
        assertEquals(1,sent.size());assertEquals(1,count("voice_outbox"));
    }

    @org.junit.jupiter.api.RepeatedTest(100)
    void confirmConcurrencyHundredRounds() throws Exception {
        concurrentConfirmDifferentKeysHasOneExecutionAndOneWrite();
    }
    @org.junit.jupiter.api.RepeatedTest(100)
    void confirmCancelHundredRounds() throws Exception {
        concurrentConfirmCancelHasOneWinner();worker.tick();
        assertTrue(sent.size()<=1);assertEquals(count("voice_execution"),sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"TIMED_OUT","FAILED","REJECTED","INVALIDATED"})
    void unsuccessfulExecutionCannotAcquireFrameChallenge(String state) {
        var e=execution(confirm(proposal("PAUSE")));
        s.locked(()->{var stored=s.get("voice_execution",e.path("executionId").asText());stored.put("state",state).put("action","START");s.save("voice_execution",stored);return null;});
        var bind=presentation.bind(ref(),VoiceJson.uuid(),j.object().put("runtimeGeneration",gen()).putNull("expectedBindingId"));
        var body=j.object().put("runtimeGeneration",gen()).put("bindingId",bind.path("bindingId").asText()).put("kind","FRAME_APPLIED").put("executionId",e.path("executionId").asText());
        error("INVALID_STATE",()->presentation.challenge(ref(),VoiceJson.uuid(),body));
        assertEquals(state,app.execution(e.path("executionId").asText()).path("state").asText());assertEquals(0,sent.size());
    }
    @ParameterizedTest @ValueSource(longs={2,3,5,6})
    void presentationFrameBounds(long frame) {
        heartbeat("PREPARED",4,2);var e=execution(confirm(proposal("START")));worker.tick();
        r.frame(ref(),gen(),j.read("{\"sequence\":5,\"agents\":[{\"deviceCode\":\"UAV-001\"},{\"deviceCode\":\"USV-001\"}]}"));
        var result=event("COMMAND_RESULT");result.set("commandId",e.path("commandId"));
        result.put("eventSequence",2).put("status","SUCCEEDED").put("runtimeState","RUNNING").put("stateVersion",5).put("lastFrameSequence",3).putNull("errorCode");
        result.putArray("affectedDeviceCodes").add("UAV-001").add("USV-001");worker.receive(ref(),gen(),result);
        var bind=presentation.bind(ref(),VoiceJson.uuid(),j.object().put("runtimeGeneration",gen()).putNull("expectedBindingId"));
        var body=j.object().put("runtimeGeneration",gen()).put("bindingId",bind.path("bindingId").asText()).put("kind","FRAME_APPLIED").put("executionId",e.path("executionId").asText());
        var q=presentation.challenge(ref(),VoiceJson.uuid(),body);var report=body.deepCopy();report.set("requestId",q.path("requestId"));report.set("sequence",q.path("sequence"));report.put("frameSequence",frame).put("applied",true);
        if(frame<3)error("CONTEXT_CHANGED",()->presentation.report(ref(),VoiceJson.uuid(),report));
        else if(frame>5)error("INVALID_REQUEST",()->presentation.report(ref(),VoiceJson.uuid(),report));
        else presentation.report(ref(),VoiceJson.uuid(),report);
        var actual=app.execution(e.path("executionId").asText());assertEquals("SUCCEEDED",actual.path("state").asText());
        assertEquals(frame>=3&&frame<=5?"REPORTED_APPLIED":"PENDING",actual.path("presentationStatus").asText());
        assertEquals(1,sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"generation","members"})
    void dispatchRechecksChangedIdentityAndMembership(String change) {
        var e=execution(confirm(proposal("PAUSE")));
        if(change.equals("generation"))s.locked(()->{var ctx=s.get("voice_runtime_context",ref());ctx.put("runtimeGeneration",VoiceJson.uuid());s.save("voice_runtime_context",ctx);return null;});
        else r.frame(ref(),gen(),j.read("{\"sequence\":2,\"agents\":[{\"deviceCode\":\"OTHER\"}]}"));
        worker.tick();assertEquals("INVALIDATED",app.execution(e.path("executionId").asText()).path("state").asText());assertEquals(0,sent.size());
    }

    @ParameterizedTest @ValueSource(booleans={true,false})
    void capacityProtocolErrorProtectsChannelWithoutInventingSuccess(boolean correlated) {
        var e=execution(confirm(proposal("PAUSE")));worker.tick();
        var failure=event("PROTOCOL_ERROR");
        failure.put("relatedCommandId",correlated?e.path("commandId").asText():VoiceJson.uuid());
        failure.put("errorCode","CAPACITY_EXCEEDED").put("detail","command cache limit reached");
        worker.receive(ref(),gen(),failure);
        assertTrue(r.channel(ref()).faulted);
        assertEquals("RUNNING",app.context(ref()).path("state").asText());
        assertEquals(3,app.context(ref()).path("stateVersion").asInt());
        assertEquals("DISPATCHED",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(0,count("voice_command_event"));
        assertEquals(1,s.jdbc.queryForObject("SELECT COUNT(*) FROM voice_audit WHERE kind='PROTOCOL_ERROR' AND detail='CAPACITY_EXCEEDED'",Integer.class));
        error("RUNTIME_UNAVAILABLE",()->proposal("STOP"));
        t.advance(5001);worker.tick();
        assertEquals("TIMED_OUT",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("UNKNOWN",app.execution(e.path("executionId").asText()).path("outcome").asText());
        var query=sent.stream().filter(n->n.path("kind").asText().equals("STATUS_QUERY")).findFirst().orElseThrow();
        var reply=event("STATUS_REPLY");reply.set("queryId",query.path("queryId"));reply.set("commandId",e.path("commandId"));reply.put("known",false).putNull("result");
        worker.receive(ref(),gen(),reply);
        for(int i=0;i<5;i++){t.advance(5000);worker.tick();}
        assertEquals("TIMED_OUT",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(1,sent.stream().filter(n->n.path("kind").asText().equals("COMMAND")).count());
        assertEquals(1,count("voice_execution"));assertEquals(0,count("voice_command_event"));
    }
    @Test void committedReadyOutboxIsSentOnceAfterDeferredClaim() {
        var e=execution(confirm(proposal("PAUSE")));
        assertEquals("QUEUED",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("READY",s.jdbc.queryForObject("SELECT status FROM voice_outbox",String.class));
        assertEquals(1,count("voice_execution"));assertEquals(0,sent.size());
        var replacementWorker=new VoiceDispatcher(s,j,t,r);
        replacementWorker.tick();replacementWorker.tick();worker.tick();
        assertEquals("SENT",s.jdbc.queryForObject("SELECT status FROM voice_outbox",String.class));
        assertEquals("DISPATCHED",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals(1,sent.size());assertEquals(e.path("commandId"),sent.get(0).path("commandId"));
    }
    @Test void ownerlessContextCannotBeClaimedByCurrentAdmin() {
        var body=request("PAUSE");
        s.locked(()->{var stored=s.get("voice_runtime_context",ref());stored.remove("_owner");s.save("voice_runtime_context",stored);return null;});
        error("RESOURCE_NOT_FOUND",()->app.context(ref()));
        error("RESOURCE_NOT_FOUND",()->app.createProposal(VoiceJson.uuid(),body));
        assertFalse(s.get("voice_runtime_context",ref()).has("_owner"));
        assertEquals(0,count("voice_proposal"));assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
    }
    @Test void proposalCreationAndReplayPreserveExpiryWithoutExecution() {
        var key=VoiceJson.uuid();var body=request("PAUSE");
        var first=app.createProposal(key,body);
        assertEquals(201,first.status());
        assertEquals(t.now().plusSeconds(30).toString(),first.data().path("expiresAt").asText());
        t.advance(1000);
        var replay=app.createProposal(key,body);
        assertEquals(200,replay.status());assertEquals(first.data(),replay.data());
        assertEquals(1,count("voice_proposal"));assertEquals(0,count("voice_execution"));
        assertEquals(0,count("voice_outbox"));assertEquals(0,sent.size());
    }
    @Test void sameKeySeparatesResourceOperationsAndDeniesOtherOwner() {
        var key=VoiceJson.uuid();var first=app.createProposal(key,request("PAUSE")).data();
        var second=proposal("STOP");
        login("bob");
        error("RESOURCE_NOT_FOUND",()->app.cancel(first.path("proposalId").asText(),key,confirmation(first)));
        login("alice");
        assertEquals("AWAITING_CONFIRMATION",app.proposal(first.path("proposalId").asText()).path("status").asText());
        for(var p:java.util.List.of(first,second)) {
            var cancelled=app.cancel(p.path("proposalId").asText(),key,confirmation(p));
            assertEquals("CANCELLED",cancelled.data().path("status").asText());
            assertEquals(p.path("proposalId"),cancelled.data().path("proposalId"));
        }
        assertEquals(0,count("voice_execution"));assertEquals(0,count("voice_outbox"));assertEquals(0,sent.size());
    }
    @org.junit.jupiter.api.RepeatedTest(30)
    void confirmationAndManualActionCompeteForSingleExecution() throws Exception {
        var p=proposal("PAUSE");
        var gate=new java.util.concurrent.CountDownLatch(1);
        var pool=java.util.concurrent.Executors.newFixedThreadPool(2);
        try {
            java.util.concurrent.Callable<String> confirmTask=()->{login("alice");try {gate.await();confirm(p);return "OK";}catch(VoiceFailure e){return e.code;}finally{org.springframework.security.core.context.SecurityContextHolder.clearContext();}};
            java.util.concurrent.Callable<String> manualTask=()->{login("alice");try {gate.await();app.manual(ref(),"STOP");return "OK";}catch(VoiceFailure e){return e.code;}finally{org.springframework.security.core.context.SecurityContextHolder.clearContext();}};
            var one=pool.submit(confirmTask);var two=pool.submit(manualTask);gate.countDown();
            var outcomes=java.util.List.of(one.get(10,java.util.concurrent.TimeUnit.SECONDS),two.get(10,java.util.concurrent.TimeUnit.SECONDS));
            assertEquals(1,outcomes.stream().filter("OK"::equals).count());
            assertEquals(1,outcomes.stream().filter("EXECUTION_IN_PROGRESS"::equals).count());
            assertEquals(1,count("voice_execution"));assertEquals(1,count("voice_outbox"));
            worker.tick();worker.tick();assertEquals(1,sent.size());
        } finally {pool.shutdownNow();}
    }
    @Test void manualCompletionInvalidatesPreviouslyPreparedConfirmation() {
        var p=proposal("PAUSE");var e=execution(app.manual(ref(),"STOP"));
        worker.tick();result(e,"SUCCEEDED",2);
        error("CONTEXT_CHANGED",()->confirm(p));
        worker.tick();assertEquals(1,count("voice_execution"));assertEquals(1,sent.size());
        assertEquals("STOP",sent.get(0).path("action").asText());
        assertEquals("STOPPED",app.context(ref()).path("state").asText());
    }    @Test void uncertainExecutionRetainsDeduplicationAcrossAgingAndRecovery() {
        var p=proposal("PAUSE");var key=VoiceJson.uuid();var body=confirmation(p);
        var e=execution(app.confirm(p.path("proposalId").asText(),key,body).data());worker.tick();
        t.advance(5001);worker.tick();
        assertEquals("UNKNOWN",app.execution(e.path("executionId").asText()).path("outcome").asText());
        int idempotency=count("voice_idempotency");
        s.audit(ref(),"RETENTION_CHECK","uncertain execution",t.stamp());int audits=count("voice_audit");
        t.advance(7L*24*60*60*1000);worker.tick();worker.recover();
        var replay=execution(app.confirm(p.path("proposalId").asText(),key,body).data());
        assertEquals(e.path("executionId"),replay.path("executionId"));
        assertEquals(e.path("commandId"),replay.path("commandId"));
        assertEquals(1,count("voice_execution"));assertEquals(idempotency,count("voice_idempotency"));
        assertTrue(count("voice_audit")>=audits);
        assertEquals(1,sent.stream().filter(n->n.path("kind").asText().equals("COMMAND")).count());
    }
    @ParameterizedTest @ValueSource(strings={"runtimeScope","executionBackend","missionRunId"})
    void proposalCannotOverrideTrustedRuntimeDomain(String field) {
        var body=request("PAUSE");
        body.put(field,field.equals("runtimeScope")?"ROS":field.equals("executionBackend")?"ROS_GAZEBO":"7001");
        error("INVALID_REQUEST",()->app.createProposal(VoiceJson.uuid(),body));
        assertEquals(0,count("voice_proposal"));assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
        var ctx=app.context(ref());assertEquals("STANDALONE_ALGORITHM",ctx.path("runtimeKind").asText());
        assertEquals("PYTHON_SIMULATION",ctx.path("executionBackend").asText());assertTrue(ctx.path("missionRunId").isNull());
    }
    @ParameterizedTest @ValueSource(strings={"EXPIRED","INVALIDATED"})
    void cancelExpiredOrInvalidatedProposalNeverWrites(String state) {
        var p=proposal("PAUSE");var id=p.path("proposalId").asText();
        if(state.equals("EXPIRED"))t.advance(30000);
        else r.ended(ref(),gen());
        for(int attempt=0;attempt<2;attempt++) {
            var failure=assertThrows(VoiceFailure.class,()->app.cancel(id,VoiceJson.uuid(),confirmation(p)));
            assertEquals(409,failure.status);assertEquals("PROPOSAL_"+state,failure.code);
        }
        assertEquals(state,app.proposal(id).path("status").asText());
        worker.tick();assertEquals(0,count("voice_execution"));assertEquals(0,count("voice_outbox"));assertEquals(0,sent.size());
    }
    @Test void sameProposalKeyIsIndependentForTwoOwners() {
        var key=VoiceJson.uuid();var aliceBody=request("PAUSE");
        var aliceProposal=app.createProposal(key,aliceBody).data();
        alive.set(false);login("bob");c=r.register(7002,"bob-config");
        r.attach(ref(),gen(),sent::add,()->true);
        var ready=event("RUNTIME_READY");ready.put("adapterId","test").put("state","PREPARED").put("stateVersion",0);
        ready.putArray("capabilities").add("START").add("PAUSE").add("RESUME").add("STOP");worker.receive(ref(),gen(),ready);
        r.frame(ref(),gen(),j.read("{\"sequence\":1,\"agents\":[{\"deviceCode\":\"UAV-001\"}]}"));
        heartbeat("RUNNING",3,1);
        var bobBody=request("PAUSE");var bobReply=app.createProposal(key,bobBody);
        assertEquals(201,bobReply.status());var bobProposal=bobReply.data();
        assertNotEquals(aliceProposal.path("proposalId"),bobProposal.path("proposalId"));
        assertEquals(bobProposal,app.createProposal(key,bobBody).data());
        error("RESOURCE_NOT_FOUND",()->app.proposal(aliceProposal.path("proposalId").asText()));
        login("alice");assertEquals(aliceProposal,app.createProposal(key,aliceBody).data());
        error("RESOURCE_NOT_FOUND",()->app.proposal(bobProposal.path("proposalId").asText()));
        assertEquals(2,count("voice_proposal"));assertEquals(2,count("voice_idempotency"));
        assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
    }
    @ParameterizedTest @ValueSource(strings={"innerCommandId","innerRuntimeRef","innerGeneration","queryId","outerCommandId","valid"})
    void statusReplyRequiresAllCorrelationIdentities(String mismatch) {
        var e=execution(confirm(proposal("PAUSE")));worker.tick();t.advance(5000);worker.tick();
        var query=sent.stream().filter(n->n.path("kind").asText().equals("STATUS_QUERY")).findFirst().orElseThrow();
        var result=event("COMMAND_RESULT");result.set("commandId",e.path("commandId"));
        result.put("eventSequence",2).put("status","SUCCEEDED").put("runtimeState","PAUSED").put("stateVersion",4).put("lastFrameSequence",1).putNull("errorCode");
        result.putArray("affectedDeviceCodes").add("UAV-001").add("USV-001");
        var reply=event("STATUS_REPLY");reply.set("queryId",query.path("queryId"));reply.set("commandId",e.path("commandId"));reply.put("known",true);reply.set("result",result);
        switch(mismatch) {
            case "innerCommandId" -> result.put("commandId",VoiceJson.uuid());
            case "innerRuntimeRef" -> result.put("runtimeRef",VoiceJson.uuid());
            case "innerGeneration" -> result.put("runtimeGeneration",VoiceJson.uuid());
            case "queryId" -> reply.put("queryId",VoiceJson.uuid());
            case "outerCommandId" -> reply.put("commandId",VoiceJson.uuid());
        }
        worker.receive(ref(),gen(),reply);
        var actual=app.execution(e.path("executionId").asText());
        if(mismatch.equals("valid")) {
            assertEquals("SUCCEEDED",actual.path("state").asText());assertEquals("SUCCESS",actual.path("outcome").asText());assertEquals(1,count("voice_command_event"));assertFalse(r.channel(ref()).faulted);
        } else {
            assertEquals("TIMED_OUT",actual.path("state").asText());assertEquals("UNKNOWN",actual.path("outcome").asText());
            assertEquals("RUNNING",app.context(ref()).path("state").asText());assertEquals(3,app.context(ref()).path("stateVersion").asInt());
            assertEquals(0,count("voice_command_event"));assertTrue(r.channel(ref()).faulted);
            assertTrue(count("voice_audit")>0);
        }
        assertEquals(1,sent.stream().filter(n->n.path("kind").asText().equals("COMMAND")).count());
    }
    @Test void reorderedMembersProduceIdenticalFrozenPlanHash() {
        var first=proposal("PAUSE");
        r.frame(ref(),gen(),j.read("{\"sequence\":2,\"agents\":[{\"deviceCode\":\"UAV-001\"},{\"deviceCode\":\"USV-001\"}]}"));
        var second=proposal("PAUSE");
        assertNotEquals(first.path("proposalId"),second.path("proposalId"));
        assertEquals(first.path("plan"),second.path("plan"));assertEquals(first.path("planHash"),second.path("planHash"));
        assertEquals(j.read("{\"members\":[\"UAV-001\",\"USV-001\"]}").path("members"),second.path("plan").path("explicitDeviceCodes"));
        assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
    }
    @Test void disabledFeatureRejectsAllProposalWritesButReconcilesInFlight() {
        var p=proposal("PAUSE");var other=proposal("STOP");var e=execution(confirm(p));worker.tick();
        settings.setEnabled(false);
        error("VOICE_CONTROL_DISABLED",()->proposal("STOP"));
        error("VOICE_CONTROL_DISABLED",()->confirm(other));
        error("VOICE_CONTROL_DISABLED",()->app.cancel(other.path("proposalId").asText(),VoiceJson.uuid(),confirmation(other)));
        result(e,"SUCCEEDED",2);
        assertEquals("SUCCEEDED",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("AWAITING_CONFIRMATION",app.proposal(other.path("proposalId").asText()).path("status").asText());
        assertEquals(1,count("voice_execution"));assertEquals(1,sent.size());
        var manual=execution(app.manual(ref(),"STOP"));worker.tick();
        var stopped=event("COMMAND_RESULT");stopped.set("commandId",manual.path("commandId"));
        stopped.put("eventSequence",2).put("status","SUCCEEDED").put("runtimeState","STOPPED").put("stateVersion",5).put("lastFrameSequence",1).putNull("errorCode");
        stopped.putArray("affectedDeviceCodes").add("UAV-001").add("USV-001");worker.receive(ref(),gen(),stopped);
        assertEquals("SUCCEEDED",app.execution(manual.path("executionId").asText()).path("state").asText());
        assertEquals(2,count("voice_execution"));assertEquals(2,sent.size());
    }
    @Test void otherOwnerCannotReadOrWriteAnyVoiceResource() {
        var p=proposal("PAUSE");var e=execution(confirm(p));var create=request("STOP");
        var binding=presentation.bind(ref(),VoiceJson.uuid(),j.object().put("runtimeGeneration",gen()).putNull("expectedBindingId"));
        var challengeBody=j.object().put("runtimeGeneration",gen()).put("bindingId",binding.path("bindingId").asText()).put("kind","SCENE_READY").putNull("executionId");
        var q=presentation.challenge(ref(),VoiceJson.uuid(),challengeBody);
        var report=challengeBody.deepCopy();report.set("requestId",q.path("requestId"));report.set("sequence",q.path("sequence"));report.put("frameSequence",1).put("applied",true);
        int keys=count("voice_idempotency");login("bob");assertTrue(app.contexts().isEmpty());
        error("RESOURCE_NOT_FOUND",()->app.context(ref()));error("RESOURCE_NOT_FOUND",()->app.proposal(p.path("proposalId").asText()));error("RESOURCE_NOT_FOUND",()->app.execution(e.path("executionId").asText()));
        error("RESOURCE_NOT_FOUND",()->app.createProposal(VoiceJson.uuid(),create));error("RESOURCE_NOT_FOUND",()->confirm(p));error("RESOURCE_NOT_FOUND",()->app.cancel(p.path("proposalId").asText(),VoiceJson.uuid(),confirmation(p)));
        error("RESOURCE_NOT_FOUND",()->presentation.binding(ref()));error("RESOURCE_NOT_FOUND",()->presentation.bind(ref(),VoiceJson.uuid(),j.object().put("runtimeGeneration",gen()).putNull("expectedBindingId")));
        error("RESOURCE_NOT_FOUND",()->presentation.challenge(ref(),VoiceJson.uuid(),challengeBody));error("RESOURCE_NOT_FOUND",()->presentation.report(ref(),VoiceJson.uuid(),report));
        assertEquals(keys,count("voice_idempotency"));assertEquals(1,count("voice_execution"));assertEquals(1,count("voice_proposal"));assertEquals(0,sent.size());
    }
    @Test void receiptStorageFailureBlocksAnotherQueuedRuntimeUntilReconciliation() {
        var firstContext=c;var first=execution(confirm(proposal("PAUSE")));worker.tick();
        c=r.register(7020,"second-runtime");r.attach(ref(),gen(),sent::add,()->true);
        var ready=event("RUNTIME_READY");ready.put("adapterId","test").put("state","PREPARED").put("stateVersion",0);ready.putArray("capabilities").add("PAUSE");worker.receive(ref(),gen(),ready);
        r.frame(ref(),gen(),j.read("{\"sequence\":1,\"agents\":[{\"deviceCode\":\"UAV-001\"}]}"));heartbeat("RUNNING",3,1);
        var second=execution(confirm(proposal("PAUSE")));c=firstContext;
        s.jdbc.execute("ALTER TABLE voice_command_event RENAME TO temporarily_unavailable_event");
        try {
            result(first,"SUCCEEDED",2);worker.tick();
            assertEquals("DISPATCHED",app.execution(first.path("executionId").asText()).path("state").asText());
            assertEquals("QUEUED",app.execution(second.path("executionId").asText()).path("state").asText());assertEquals(1,sent.size());
        } finally {s.jdbc.execute("ALTER TABLE temporarily_unavailable_event RENAME TO voice_command_event");}
        worker.tick();assertEquals("SUCCEEDED",app.execution(first.path("executionId").asText()).path("state").asText());
        assertEquals("DISPATCHED",app.execution(second.path("executionId").asText()).path("state").asText());
        assertEquals(2,sent.size());assertEquals(1,count("voice_command_event"));
        assertNotEquals(sent.get(0).path("commandId"),sent.get(1).path("commandId"));
    }
    @ParameterizedTest @ValueSource(strings={"runtimeRef","generation","sourceProcess","unknownCommand"})
    void resultIdentityBoundaryCannotChangeRuntimeOrExecution(String variant) {
        var e=execution(confirm(proposal("PAUSE")));worker.tick();
        var n=event("COMMAND_RESULT");n.set("commandId",e.path("commandId"));n.put("eventSequence",2).put("status","SUCCEEDED").put("runtimeState","PAUSED").put("stateVersion",4).put("lastFrameSequence",1).putNull("errorCode");n.putArray("affectedDeviceCodes").add("UAV-001").add("USV-001");
        if(variant.equals("runtimeRef"))n.put("runtimeRef",VoiceJson.uuid());
        if(variant.equals("generation"))n.put("runtimeGeneration",VoiceJson.uuid());
        if(variant.equals("unknownCommand"))n.put("commandId",VoiceJson.uuid());
        worker.receive(ref(),variant.equals("sourceProcess")?VoiceJson.uuid():gen(),n);
        assertEquals("DISPATCHED",app.execution(e.path("executionId").asText()).path("state").asText());
        assertEquals("RUNNING",app.context(ref()).path("state").asText());assertEquals(3,app.context(ref()).path("stateVersion").asInt());assertEquals(0,count("voice_command_event"));assertEquals(1,sent.size());
    }
    @Test void awaitingProposalWithChangedGenerationFailsGenerationCheck() {
        var p=proposal("PAUSE");
        s.locked(()->{var changed=s.get("voice_runtime_context",ref());changed.put("runtimeGeneration",VoiceJson.uuid());s.save("voice_runtime_context",changed);return null;});
        error("GENERATION_MISMATCH",()->confirm(p));
        assertEquals("INVALIDATED",app.proposal(p.path("proposalId").asText()).path("status").asText());
        assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
    }
    @Test void businessSoftDeleteCannotRemoveUncertainVoiceAuditOrDedupe() {
        var p=proposal("PAUSE");var e=execution(confirm(p));worker.tick();t.advance(16000);worker.tick();
        assertEquals("UNKNOWN",app.execution(e.path("executionId").asText()).path("outcome").asText());
        int executions=count("voice_execution"),events=count("voice_command_event"),keys=count("voice_idempotency"),outboxes=count("voice_outbox");
        var tasks=mock(com.uavusv.platform.module.mission.repository.MissionTaskRepository.class);
        var taskDevices=mock(com.uavusv.platform.module.mission.repository.MissionTaskDeviceRepository.class);
        var taskParams=mock(com.uavusv.platform.module.mission.repository.MissionTaskParameterRepository.class);
        var missionEvents=mock(com.uavusv.platform.module.mission.repository.MissionEventRepository.class);
        var runs=mock(com.uavusv.platform.module.mission.repository.MissionRunRepository.class);
        var task=new com.uavusv.platform.module.mission.entity.MissionTask("p0-cleanup-fixture");
        org.springframework.test.util.ReflectionTestUtils.setField(task,"id",991001L);
        org.springframework.test.util.ReflectionTestUtils.setField(task,"status",com.uavusv.platform.module.mission.entity.MissionStatus.DRAFT);
        when(tasks.findByIdAndDeletedFalse(991001L)).thenReturn(java.util.Optional.of(task));
        var service=new com.uavusv.platform.module.mission.service.impl.MissionServiceImpl(tasks,taskDevices,taskParams,missionEvents,runs,
            mock(com.uavusv.platform.module.device.repository.DeviceRepository.class),
            mock(com.uavusv.platform.module.runtimecontrol.repository.SimulationSessionRepository.class),
            mock(com.uavusv.platform.module.runtimecontrol.service.RuntimeControlService.class),
            mock(com.uavusv.platform.module.monitoring.repository.RuntimeDeviceStatusRepository.class),
            mock(com.uavusv.platform.module.monitoring.service.RuntimeStateService.class));
        service.deleteMission(991001L);assertTrue(task.isDeleted());verifyNoInteractions(taskDevices,taskParams,runs);
        assertEquals(executions,count("voice_execution"));assertEquals(events,count("voice_command_event"));assertEquals(keys,count("voice_idempotency"));assertEquals(outboxes,count("voice_outbox"));
        assertEquals(e.path("executionId"),execution(confirm(p)).path("executionId"));
        assertEquals(1,sent.stream().filter(n->n.path("kind").asText().equals("COMMAND")).count());
    }
    @ParameterizedTest @ValueSource(strings={"members","capabilities","state","policy"})
    void confirmationRevalidatesEveryFrozenPlanDimension(String dimension) {
        var p=proposal("PAUSE");
        switch(dimension) {
            case "members" -> r.frame(ref(),gen(),j.read("{\"sequence\":2,\"agents\":[{\"deviceCode\":\"NEW-DEVICE\"}]}"));
            case "state" -> heartbeat("PAUSED",4,2);
            case "capabilities" -> s.locked(()->{var ctx=s.get("voice_runtime_context",ref());ctx.putArray("capabilities").add("STOP");RuntimeContextRegistry.bump(ctx);s.save("voice_runtime_context",ctx);return null;});
            case "policy" -> s.locked(()->{var stored=s.get("voice_proposal",p.path("proposalId").asText());((com.fasterxml.jackson.databind.node.ObjectNode)stored.path("plan")).put("policyVersion","voice-p0.v0");stored.put("planHash",j.hash(stored.path("plan")));p.put("planHash",stored.path("planHash").asText());s.save("voice_proposal",stored);return null;});
        }
        error("CONTEXT_CHANGED",()->confirm(p));assertEquals("INVALIDATED",app.proposal(p.path("proposalId").asText()).path("status").asText());assertEquals(0,count("voice_execution"));assertEquals(0,sent.size());
    }
    @Test void dispatcherRejectsQueuedExecutionFromOlderPolicy() {
        var e=execution(confirm(proposal("PAUSE")));
        s.locked(()->{var stored=s.get("voice_execution",e.path("executionId").asText());((com.fasterxml.jackson.databind.node.ObjectNode)stored.path("_plan")).put("policyVersion","voice-p0.v0");s.save("voice_execution",stored);return null;});
        worker.tick();assertEquals("INVALIDATED",app.execution(e.path("executionId").asText()).path("state").asText());assertEquals(0,sent.size());
    }
}
