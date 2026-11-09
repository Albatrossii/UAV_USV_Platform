package com.uavusv.platform.module.voiceintelligence;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uavusv.platform.config.SecurityConfig;
import com.uavusv.platform.module.voicecontrol.*;

import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.*;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockServletContext;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.*;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.test.web.servlet.*;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.support.AnnotationConfigWebApplicationContext;
import org.springframework.web.servlet.config.annotation.EnableWebMvc;

class AsrHttpTests {
    @Configuration
    @EnableWebMvc
    @EnableWebSecurity
    @Import({
        SecurityConfig.class,
        VoiceAccess.class,
        VoiceJson.class,
        VoiceTime.class,
        VoiceExceptionHandler.class,
        AsrAcceptanceStore.class,
        AsrService.class,
        AsrController.class,
        AsrAdvice.class
    })
    static class Config {
        @Bean
        ObjectMapper mapper() {
            return new ObjectMapper().findAndRegisterModules();
        }

        @Bean
        JdbcTemplate jdbc() {
            var ds = new JdbcDataSource();
            ds.setURL(
                    "jdbc:h2:mem:" + java.util.UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
            var j = new JdbcTemplate(ds);
            j.execute(
                    "CREATE TABLE app_user(id BIGINT PRIMARY KEY,username VARCHAR(64),role"
                            + " VARCHAR(32),enabled BOOLEAN)");
            j.execute(
                    "INSERT INTO app_user"
                            + " VALUES(1,'admin','ADMIN',true),(2,'viewer','VIEWER',true)");
            new org.springframework.jdbc.datasource.init.ResourceDatabasePopulator(
                            new org.springframework.core.io.ClassPathResource(
                                    "db/migration/V20__create_voice_asr_acceptance.sql"))
                    .execute(ds);
            return j;
        }

        @Bean
        AsrSettings settings() {
            var s = new AsrSettings();
            s.setEnabled(true);
            return s;
        }

        @Bean
        SpeechProvider provider() {
            var p = mock(SpeechProvider.class);
            when(p.transcribe(any(), anyLong()))
                    .thenAnswer(
                            i ->
                                    new SpeechProvider.Transcript(
                                            ((SpeechProvider.Audio) i.getArgument(0)).requestId(),
                                            "停止任务",
                                            1800,
                                            "r1"));
            return p;
        }

        @Bean
        UserDetailsService users() {
            return new InMemoryUserDetailsManager(
                    User.withUsername("admin")
                            .password(
                                    new org.springframework.security.crypto.bcrypt
                                                    .BCryptPasswordEncoder()
                                            .encode("test-only"))
                            .roles("ADMIN")
                            .build());
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
                        .addFilters(new AsrIngressFilter())
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

    org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder request() {
        return post(AsrIngressFilter.PATH)
                .servletPath(AsrIngressFilter.PATH)
                .contentType("multipart/form-data; boundary=b")
                .header("X-Request-ID", AudioMultipartTests.ID)
                .header("Idempotency-Key", AudioMultipartTests.ID)
                .requestAttr(
                        AsrUploadFilter.BODY,
                        AudioMultipartTests.body(
                                AudioMultipartTests.ID, "audio/mpeg", new byte[] {1, 2, 3}))
                .requestAttr(
                        AsrUploadFilter.DEADLINE,
                        System.nanoTime() + java.util.concurrent.TimeUnit.SECONDS.toNanos(120))
                .content(
                        AudioMultipartTests.body(
                                AudioMultipartTests.ID, "audio/mpeg", new byte[] {1, 2, 3}));
    }

    @Test
    void unauthenticated401() throws Exception {
        mvc.perform(request()).andExpect(status().isUnauthorized());
        verifyNoInteractions(context.getBean(SpeechProvider.class));
    }

    @Test
    void providerHeaderIsForwardedAndInvalidSelectionRejected() throws Exception {
        mvc.perform(request().with(user("admin").roles("ADMIN")).with(csrf().asHeader())
                        .header("X-ASR-Provider", "aliyun"))
                .andExpect(status().isOk());
        verify(context.getBean(SpeechProvider.class)).transcribe(
                argThat(a -> "aliyun".equals(a.provider())), anyLong());
        mvc.perform(request().with(user("admin").roles("ADMIN")).with(csrf().asHeader())
                        .header("X-ASR-Provider", "unknown"))
                .andExpect(status().isBadRequest());
        mvc.perform(request().with(user("admin").roles("ADMIN")).with(csrf().asHeader())
                        .header("X-ASR-Provider", "local", "aliyun"))
                .andExpect(status().isBadRequest());
        verify(context.getBean(SpeechProvider.class), times(1)).transcribe(any(), anyLong());
    }

    @Test
    void csrfRequired() throws Exception {
        mvc.perform(request().with(user("admin").roles("ADMIN")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("CSRF_INVALID"));
        verifyNoInteractions(context.getBean(SpeechProvider.class));
    }

    @Test
    void viewerForbidden() throws Exception {
        mvc.perform(request().with(user("viewer").roles("VIEWER")).with(csrf().asHeader()))
                .andExpect(status().isForbidden());
        verifyNoInteractions(context.getBean(SpeechProvider.class));
    }

    @Test
    void realMultipartAcceptedThroughP0GuardAndReplayed() throws Exception {
        String first =
                mvc.perform(request().with(user("admin").roles("ADMIN")).with(csrf().asHeader()))
                        .andExpect(status().isOk())
                        .andExpect(header().string("Cache-Control", "no-store"))
                        .andExpect(header().string("X-Request-ID", AudioMultipartTests.ID))
                        .andExpect(jsonPath("$.data.text").value("停止任务"))
                        .andReturn()
                        .getResponse()
                        .getContentAsString();
        String second =
                mvc.perform(request().with(user("admin").roles("ADMIN")).with(csrf().asHeader()))
                        .andExpect(status().isOk())
                        .andReturn()
                        .getResponse()
                        .getContentAsString();
        assertEquals(first, second);
        verify(context.getBean(SpeechProvider.class), times(1)).transcribe(any(), anyLong());
    }

    @Test
    void persistedAcceptanceAfterRestartReturnsUnknownWithoutInference() throws Exception {
        var audio =
                new SpeechProvider.Audio(
                        AudioMultipartTests.ID, "zh-CN", "audio/mpeg", new byte[] {1, 2, 3});
        context.getBean(AsrAcceptanceStore.class)
                .reserve(
                        1,
                        AudioMultipartTests.ID,
                        AsrService.fingerprint(audio),
                        java.time.Instant.now());
        mvc.perform(request().with(user("admin").roles("ADMIN")).with(csrf().asHeader()))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("VOICE_REQUEST_OUTCOME_UNKNOWN"));
        verifyNoInteractions(context.getBean(SpeechProvider.class));
    }

    @Test
    void requestIdMismatchNeverCallsProvider() throws Exception {
        mvc.perform(
                        request()
                                .header("X-Request-ID", "other")
                                .with(user("admin").roles("ADMIN"))
                                .with(csrf().asHeader()))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(context.getBean(SpeechProvider.class));
    }

    @Test
    void disabled503() throws Exception {
        context.getBean(AsrSettings.class).setEnabled(false);
        mvc.perform(request().with(user("admin").roles("ADMIN")).with(csrf().asHeader()))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("VOICE_INTELLIGENCE_DISABLED"));
    }

    @Test
    void interpretationsAlwaysDisabled() throws Exception {
        mvc.perform(
                        post("/api/voice/intelligence/interpretations")
                                .contentType("application/json")
                                .content("{}")
                                .with(user("admin").roles("ADMIN"))
                                .with(csrf().asHeader()))
                .andExpect(status().isServiceUnavailable());
        verifyNoInteractions(context.getBean(SpeechProvider.class));
    }

    @Test
    void revokedSessionNotAccepted() throws Exception {
        context.getBean(JdbcTemplate.class).update("UPDATE app_user SET enabled=false WHERE id=1");
        mvc.perform(request().with(user("admin").roles("ADMIN")).with(csrf().asHeader()))
                .andExpect(status().isForbidden());
        verifyNoInteractions(context.getBean(SpeechProvider.class));
    }
}
