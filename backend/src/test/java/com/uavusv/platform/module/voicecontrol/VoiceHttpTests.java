package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.config.SecurityConfig;

import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.*;
import org.springframework.context.annotation.*;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.mock.web.MockServletContext;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.*;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.support.AnnotationConfigWebApplicationContext;
import org.springframework.web.servlet.config.annotation.EnableWebMvc;

import javax.sql.DataSource;

class VoiceHttpTests {
    @Configuration
    @EnableWebMvc
    @EnableWebSecurity
    @Import({
        SecurityConfig.class,
        VoiceJson.class,
        VoiceTime.class,
        VoiceAccess.class,
        VoiceStore.class,
        RuntimeContextRegistry.class,
        VoiceCommandApplicationService.class,
        VoicePresentationService.class,
        VoiceController.class,
        VoiceExceptionHandler.class,
        VoiceMvcErrors.class
    })
    static class Config {
        @Bean
        ObjectMapper mapper() {
            return new ObjectMapper().findAndRegisterModules();
        }

        @Bean
        VoiceSettings settings() {
            return new VoiceSettings(true);
        }

        @Bean
        SystemOverviewVoiceContextService systemOverviewVoiceContextService() {
            return org.mockito.Mockito.mock(SystemOverviewVoiceContextService.class);
        }

        @Bean
        DataSource ds() {
            var ds = new JdbcDataSource();
            ds.setURL("jdbc:h2:mem:" + VoiceJson.uuid() + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
            new ResourceDatabasePopulator(
                            new ClassPathResource("db/migration/V19__create_voice_control_p0.sql"))
                    .execute(ds);
            var j = new JdbcTemplate(ds);
            j.execute(
                    "CREATE TABLE app_user(id BIGINT PRIMARY KEY,username VARCHAR(64),role"
                        + " VARCHAR(32),enabled BOOLEAN)");
            j.update(
                    "INSERT INTO app_user VALUES"
                        + " (1,'alice','ADMIN',true),(2,'viewer','VIEWER',true)");
            return ds;
        }

        @Bean
        JdbcTemplate jdbc(DataSource ds) {
            return new JdbcTemplate(ds);
        }

        @Bean
        DataSourceTransactionManager tm(DataSource ds) {
            return new DataSourceTransactionManager(ds);
        }

        @Bean
        UserDetailsService users() {
            return new InMemoryUserDetailsManager(
                    User.withUsername("alice").password("unused").roles("ADMIN").build());
        }
    }

    AnnotationConfigWebApplicationContext context;
    MockMvc mvc;

    @BeforeEach
    void setup() {
        context = new AnnotationConfigWebApplicationContext();
        context.setServletContext(new MockServletContext());
        context.register(Config.class);
        context.refresh();
        mvc =
                MockMvcBuilders.webAppContextSetup(context)
                        .apply(springSecurity())
                        .addFilters(
                                new VoiceHttpFilter(
                                        context.getBean(VoiceJson.class),
                                        context.getBean(VoiceTime.class)))
                        .build();
    }

    @AfterEach
    void close() {
        SecurityContextHolder.clearContext();
        context.close();
    }

    @Test
    void unauthenticatedGetAndPostAre401() throws Exception {
        mvc.perform(get("/api/voice/contexts")).andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.message").value("请先登录或登录状态已失效"));
        mvc.perform(
                        post("/api/voice/commands/proposals")
                                .servletPath("/api/voice/commands/proposals")
                                .contentType("application/json")
                                .content("{}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"))
                .andExpect(jsonPath("$.message").value("请先登录或登录状态已失效"));
    }

    @Test
    void authenticatedPostRequiresCsrf() throws Exception {
        mvc.perform(
                        post("/api/voice/commands/proposals")
                                .with(user("alice").roles("ADMIN"))
                                .contentType("application/json")
                                .header("Idempotency-Key", VoiceJson.uuid())
                                .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("CSRF_INVALID"));
    }

    @Test
    void readHasNoStoreAndNoInstancesIsEmpty() throws Exception {
        mvc.perform(get("/api/voice/contexts").with(user("alice").roles("ADMIN")))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.data").isEmpty());
    }

    @Test
    void nonAdminDeniedAtProxiedServiceAndHttp() throws Exception {
        mvc.perform(
                        post("/api/voice/commands/proposals")
                                .with(user("viewer").roles("VIEWER"))
                                .with(csrf())
                                .header("Idempotency-Key", VoiceJson.uuid())
                                .contentType("application/json")
                                .content("{}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("FORBIDDEN"));
        VoiceControlTests.login("viewer");
        assertThrows(
                org.springframework.security.access.AccessDeniedException.class,
                () ->
                        context.getBean(VoiceCommandApplicationService.class)
                                .createProposal(
                                        VoiceJson.uuid(),
                                        context.getBean(VoiceJson.class).object()));
    }

    @Test
    void malformedAndUnknownFieldsReturn400() throws Exception {
        for (String body : new String[] {"{", "null", "{\"ownerUserId\":1}", "{\"x\":1,\"x\":2}"})
            mvc.perform(
                            post("/api/voice/commands/proposals")
                                    .with(user("alice").roles("ADMIN"))
                                    .with(csrf())
                                    .contentType("application/json")
                                    .header("Idempotency-Key", VoiceJson.uuid())
                                    .content(body))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("INVALID_REQUEST"));
    }

    @Test
    void rejectsOversizedStreamAndWrongMediaType() throws Exception {
        mvc.perform(
                        post("/api/voice/commands/proposals")
                                .servletPath("/api/voice/commands/proposals")
                                .with(user("alice").roles("ADMIN"))
                                .with(csrf())
                                .contentType("application/json")
                                .header("Idempotency-Key", VoiceJson.uuid())
                                .content("x".repeat(16385)))
                .andExpect(status().isPayloadTooLarge())
                .andExpect(jsonPath("$.code").value("PAYLOAD_TOO_LARGE"));
        mvc.perform(
                        post("/api/voice/commands/proposals")
                                .servletPath("/api/voice/commands/proposals")
                                .with(user("alice").roles("ADMIN"))
                                .with(csrf())
                                .contentType("text/plain")
                                .header("Idempotency-Key", VoiceJson.uuid())
                                .content("{}"))
                .andExpect(status().isUnsupportedMediaType());
    }

    @Test void allEndpointsDenyAnonymousAndAllPostsRequireCsrf() throws Exception {
        String id=VoiceJson.uuid();
        String[] reads={"/contexts","/contexts/"+id,"/commands/"+id,"/executions/"+id,"/contexts/"+id+"/presentation/binding"};
        String[] writes={"/commands/proposals","/commands/"+id+"/confirm","/commands/"+id+"/cancel","/contexts/system-overview/sync","/contexts/"+id+"/presentation/bindings","/contexts/"+id+"/presentation/challenges","/contexts/"+id+"/presentation/reports"};
        for(String suffix:reads)mvc.perform(get("/api/voice"+suffix)).andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("UNAUTHORIZED"));
        for(String suffix:writes){
            String path="/api/voice"+suffix;
            mvc.perform(post(path).servletPath(path).contentType("application/json").content("{}")).andExpect(status().isUnauthorized());
            for(boolean invalid:new boolean[]{false,true}){
                var request=post(path).servletPath(path).with(user("alice").roles("ADMIN")).header("Idempotency-Key",VoiceJson.uuid()).contentType("application/json").content("{}");
                if(invalid)request.with(csrf().useInvalidToken());
                mvc.perform(request).andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("CSRF_INVALID"));
            }
        }
        var jdbc=context.getBean(JdbcTemplate.class);
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM voice_proposal",Integer.class));
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM voice_execution",Integer.class));
    }
    @Test void bothNonAdminRolesDenyEveryCommandWrite() throws Exception {
        var jdbc=context.getBean(JdbcTemplate.class);
        for(String role:new String[]{"VIEWER","OPERATOR"}) {
            jdbc.update("UPDATE app_user SET role=? WHERE username='viewer'",role);
            for(String suffix:new String[]{"/proposals","/"+VoiceJson.uuid()+"/confirm","/"+VoiceJson.uuid()+"/cancel"}){
                mvc.perform(post("/api/voice/commands"+suffix).with(user("viewer").roles(role)).with(csrf()).header("Idempotency-Key",VoiceJson.uuid()).contentType("application/json").content("{}"))
                    .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("FORBIDDEN"));
            }
        }
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM voice_execution",Integer.class));
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.CsvSource({"I05-a,confirm", "I05-b,confirm", "I05-a,cancel", "I05-b,cancel"})
    void i05SchemaAndPlanErrorsHaveNoSideEffects(String caseId, String operation) throws Exception {
        var json=context.getBean(VoiceJson.class);
        var store=context.getBean(VoiceStore.class);
        var registry=context.getBean(RuntimeContextRegistry.class);
        var service=context.getBean(VoiceCommandApplicationService.class);
        var time=context.getBean(VoiceTime.class);
        var writes=new java.util.ArrayList<com.fasterxml.jackson.databind.JsonNode>();
        VoiceControlTests.login("alice");
        var runtime=registry.register(7001,"i05-fixture");
        String ref=runtime.path("runtimeRef").asText(), gen=runtime.path("runtimeGeneration").asText();
        runtime.put("state","RUNNING");runtime.putArray("capabilities").add("PAUSE");runtime.putArray("_members").add("UAV-001");
        store.locked(()->{store.save("voice_runtime_context",runtime);return null;});
        registry.attach(ref,gen,writes::add,()->true);registry.channel(ref).heartbeatNanos=time.nanos();
        var proposal=service.createProposal(VoiceJson.uuid(),json.object().put("runtimeRef",ref).put("runtimeGeneration",gen).put("expectedContextVersion",1).put("intent","MISSION_PAUSE")).data();
        String id=proposal.path("proposalId").asText();
        var before=store.get("voice_proposal",id).deepCopy();
        var fixtures=json.mapper.readTree(java.nio.file.Files.readString(VoiceControlTests.repositoryFile("docs/voice-control-p0/i05-http-fixtures.json")));
        com.fasterxml.jackson.databind.JsonNode fixture=null;
        for(var candidate:fixtures.path("cases"))if(candidate.path("id").asText().equals(caseId))fixture=candidate;
        assertNotNull(fixture);
        assertNotEquals(proposal.path("planHash"),fixture.path("request").path("expectedPlanHash"));
        String path="/api/voice/commands/"+id+"/"+operation;
        mvc.perform(post(path).servletPath(path).with(user("alice").roles("ADMIN")).with(csrf())
                .header("Idempotency-Key",VoiceJson.uuid()).contentType("application/json").content(fixture.path("request").toString()))
            .andExpect(status().is(fixture.path("expectedStatus").asInt()))
            .andExpect(jsonPath("$.code").value(fixture.path("expectedCode").asText()));
        assertEquals(before,store.get("voice_proposal",id));
        assertEquals(0,store.jdbc.queryForObject("SELECT COUNT(*) FROM voice_execution",Integer.class));
        assertEquals(0,store.jdbc.queryForObject("SELECT COUNT(*) FROM voice_outbox",Integer.class));
        assertTrue(writes.isEmpty());
    }
    @Test void staleAdminSessionCannotBypassProxiedServiceAfterRevocation() {
        VoiceControlTests.login("alice");
        var service=context.getBean(VoiceCommandApplicationService.class);
        assertTrue(org.springframework.aop.support.AopUtils.isAopProxy(service));
        assertDoesNotThrow(service::contexts);
        var jdbc=context.getBean(JdbcTemplate.class);var json=context.getBean(VoiceJson.class);
        for(String role:java.util.List.of("VIEWER","OPERATOR")) {
            jdbc.update("UPDATE app_user SET role=? WHERE username='alice'",role);
            var failure=assertThrows(VoiceFailure.class,()->service.createProposal(VoiceJson.uuid(),json.object()));
            assertEquals(403,failure.status);assertEquals("FORBIDDEN",failure.code);
        }
        jdbc.update("UPDATE app_user SET role='ADMIN',enabled=false WHERE username='alice'");
        assertEquals(403,assertThrows(VoiceFailure.class,()->service.createProposal(VoiceJson.uuid(),json.object())).status);
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM voice_proposal",Integer.class));
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM voice_execution",Integer.class));
    }
    @Test void everyVoicePostRejectsOversizedAndWrongMediaWithoutWrites() throws Exception {
        String id=VoiceJson.uuid();
        for(String path:java.util.List.of("/api/voice/commands/proposals","/api/voice/commands/"+id+"/confirm","/api/voice/commands/"+id+"/cancel","/api/voice/contexts/system-overview/sync","/api/voice/contexts/"+id+"/presentation/bindings","/api/voice/contexts/"+id+"/presentation/challenges","/api/voice/contexts/"+id+"/presentation/reports")) {
            mvc.perform(post(path).servletPath(path).with(user("alice").roles("ADMIN")).with(csrf()).header("Idempotency-Key",VoiceJson.uuid()).contentType("application/json").content("x".repeat(16385))).andExpect(status().isPayloadTooLarge());
            mvc.perform(post(path).servletPath(path).with(user("alice").roles("ADMIN")).with(csrf()).header("Idempotency-Key",VoiceJson.uuid()).contentType("text/plain").content("{}")).andExpect(status().isUnsupportedMediaType());
        }
        var jdbc=context.getBean(JdbcTemplate.class);
        for(String table:java.util.List.of("voice_proposal","voice_execution","voice_outbox","voice_idempotency"))assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM "+table,Integer.class));
    }
}
