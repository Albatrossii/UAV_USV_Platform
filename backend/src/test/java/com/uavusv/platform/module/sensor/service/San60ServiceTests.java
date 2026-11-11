package com.uavusv.platform.module.sensor.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.http.MediaType;
import com.uavusv.platform.module.sensor.controller.San60Controller;
import static org.assertj.core.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class San60ServiceTests {
    final ObjectMapper mapper = new ObjectMapper();
    HttpServer upstream;
    San60Service service;
    MockMvc mvc;
    AtomicReference<String> state = new AtomicReference<>("{\"latest\":{\"sequence\":7},\"spatial\":{\"voxel_size\":2,\"field\":[[1,2,3,-70]]},\"capabilities\":{\"default_bag\":\"C:/data/测量.bag\"}}");
    AtomicReference<String> command = new AtomicReference<>();
    AtomicReference<String> origin = new AtomicReference<>();
    @BeforeEach void start() throws Exception {
        upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        upstream.createContext("/", exchange -> {
            origin.set(exchange.getRequestHeaders().getFirst("Origin"));
            String reply = state.get();
            int status = 200;
            if (exchange.getRequestMethod().equals("POST")) {
                command.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
                if (command.get().contains("\"voxel\":0.005")) { status = 400; reply = "{\"error\":\"体素边长无效\"}"; }
                else reply = "{\"ok\":true}";
            }
            byte[] bytes = reply.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length); exchange.getResponseBody().write(bytes); exchange.close();
        });
        upstream.start();
        service = new San60Service(mapper, "http://127.0.0.1:" + upstream.getAddress().getPort());
        mvc = MockMvcBuilders.standaloneSetup(new San60Controller(service)).build();
    }
    @AfterEach void stop() { upstream.stop(0); }
    @Test void forwardsFullStateAndSettingsChangesWithoutRequiringANewSpectrumSequence() throws Exception {
        assertThat(service.status().path("state").path("spatial").path("field").get(0).get(2).asInt()).isEqualTo(3);
        state.set(state.get().replace("\"voxel_size\":2", "\"voxel_size\":0.01"));
        mvc.perform(get("/api/san60/state")).andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.data.state.spatial.voxel_size").value(.01))
                .andExpect(jsonPath("$.data.state.latest.sequence").value(7));
    }
    @Test void resolvesDefaultBagAndDoesNotForwardBrowserOrigin() throws Exception {
        mvc.perform(post("/api/san60/control").header("Origin", "http://localhost:5174")
                .contentType(MediaType.APPLICATION_JSON).content("{\"action\":\"bag\",\"speed\":10}"))
                .andExpect(status().isOk());
        assertThat(mapper.readTree(command.get()).path("path").asText()).isEqualTo("C:/data/测量.bag");
        assertThat(origin.get()).isNull();
    }
    @Test void reportsEngineValidationErrorsAndRejectsInvalidSpeed() throws Exception {
        mvc.perform(post("/api/san60/control").contentType(MediaType.APPLICATION_JSON)
                .content("{\"action\":\"settings\",\"voxel\":0.005}"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.message").value("体素边长无效"));
        mvc.perform(post("/api/san60/control").contentType(MediaType.APPLICATION_JSON)
                .content("{\"action\":\"bag\",\"speed\":101}"))
                .andExpect(status().isBadRequest());
    }
    @Test void exposesDisconnectionAndRecoversOnNextRequest() {
        upstream.stop(0);
        assertThat(service.status().path("connected").asBoolean()).isFalse();
        assertThat(service.status().path("state").isNull()).isTrue();
    }
    @Test void refusesNonLoopbackServiceConfiguration() {
        assertThatThrownBy(() -> new San60Service(mapper, "http://example.com:8775")).isInstanceOf(IllegalArgumentException.class);
    }
}
