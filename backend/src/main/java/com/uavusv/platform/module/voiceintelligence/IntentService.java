package com.uavusv.platform.module.voiceintelligence;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.uavusv.platform.module.voicecontrol.RuntimeContextRegistry;
import com.uavusv.platform.module.voicecontrol.VoiceAccess;
import com.uavusv.platform.module.voicecontrol.VoiceJson;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.*;
import java.util.*;
import java.util.regex.Pattern;

/**
 * D2 local intent parser. It deliberately produces candidates only; execution remains behind the
 * existing P0 proposal and confirmation flow.
 */
@Service
public class IntentService {
    private static final Set<String> ACTIONS = Set.of("START", "PAUSE", "RESUME", "STOP");
    private static final Map<String, Pattern> PATTERNS =
            Map.of(
                    "START", Pattern.compile("开始|启动|执行任务"),
                    "PAUSE", Pattern.compile("暂停|先停一下|暂停任务"),
                    "RESUME", Pattern.compile("继续|恢复(?:任务|执行|运行)?"),
                    "STOP", Pattern.compile("停止|终止|结束任务"));
    // Exact, unambiguous commands can skip the local LLM round trip. The result still goes
    // through runtime capability checks and is stored as a normal interpretation candidate.
    private static final Map<String, String> EXACT_COMMANDS =
            Map.of("开始任务", "START", "暂停任务", "PAUSE", "继续任务", "RESUME", "停止任务", "STOP");
    private static final Pattern NEGATED =
            Pattern.compile("(不要|别|无需|不用|禁止).{0,6}(开始|启动|暂停|继续|恢复|停止|终止|结束)");
    private static final Pattern UNSUPPORTED = Pattern.compile("攻击|打击|开火|围捕|包围|撤退|返航");
    private static final Pattern TARGETED =
            Pattern.compile(
                    "(UAV|USV)[-_]?\\d+|(?:第?[一二三四五六七八九十\\d]+|某(?:一|个))号?(?:架|艘)?(?:无人机|无人艇)|(?:无人机|无人艇)[-_]?\\d+",
                    Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    private record Key(long user, String requestId) {}

    private static final class Entry {
        final String hash;
        final AsrResponses.Outcome outcome;
        final String action;
        final String runtimeRef;
        final String generation;
        final Long contextVersion;
        final Instant completed;

        Entry(
                String hash,
                AsrResponses.Outcome outcome,
                String action,
                String runtimeRef,
                String generation,
                Long contextVersion,
                Instant completed) {
            this.hash = hash;
            this.outcome = outcome;
            this.action = action;
            this.runtimeRef = runtimeRef;
            this.generation = generation;
            this.contextVersion = contextVersion;
            this.completed = completed;
        }
    }

    private final Map<Key, Entry> entries = new HashMap<>();
    private final VoiceAccess access;
    private final AsrSettings settings;
    private final RuntimeContextRegistry runtimes;
    private final VoiceJson json;
    private final LocalLlmIntentProvider llm;
    private final Clock clock;

    @org.springframework.beans.factory.annotation.Autowired
    public IntentService(
            VoiceAccess access,
            AsrSettings settings,
            RuntimeContextRegistry runtimes,
            VoiceJson json,
            org.springframework.beans.factory.ObjectProvider<LocalLlmIntentProvider> provider) {
        this(access, settings, runtimes, json, provider.getIfAvailable(), Clock.systemUTC());
    }

    IntentService(
            VoiceAccess access,
            AsrSettings settings,
            RuntimeContextRegistry runtimes,
            VoiceJson json) {
        this(access, settings, runtimes, json, null, Clock.systemUTC());
    }

    IntentService(
            VoiceAccess access,
            AsrSettings settings,
            RuntimeContextRegistry runtimes,
            VoiceJson json,
            Clock clock) {
        this(access, settings, runtimes, json, null, clock);
    }

    IntentService(
            VoiceAccess access,
            AsrSettings settings,
            RuntimeContextRegistry runtimes,
            VoiceJson json,
            LocalLlmIntentProvider llm,
            Clock clock) {
        this.access = access;
        this.settings = settings;
        this.runtimes = runtimes;
        this.json = json;
        this.llm = llm;
        this.clock = clock;
    }

    public AsrResponses.Outcome interpret(long user, String requestId, JsonNode body) {
        access.require(user, true);
        if (!settings.isEnabled()) throw new AsrFailure(503, "VOICE_INTELLIGENCE_DISABLED");
        validate(requestId, body);
        String hash = json.hash(body);
        Key key = new Key(user, requestId);
        synchronized (this) {
            cleanup();
            Entry existing = entries.get(key);
            if (existing != null) {
                if (!existing.hash.equals(hash))
                    throw new AsrFailure(409, "IDEMPOTENCY_CONFLICT");
                access.require(user, true);
                return existing.outcome;
            }
            if (entries.size() >= 1000)
                throw new AsrFailure(429, "VOICE_RATE_LIMITED", 2, false);
        }

        ObjectNode runtime = requireContext(user, body.path("runtimeContext"));
        String text = normalize(body.path("text").asText());
        boolean exactCommand = EXACT_COMMANDS.containsKey(text);
        IntentClassification parsed = parse(text, body.path("allowedActions"), runtime);
        boolean localLlm = "local-llm".equals(settings.getIntentProvider()) && !exactCommand;
        ObjectNode data =
                parsed.data(
                        requestId,
                        text,
                        localLlm ? "local-llm" : "local-rules",
                        localLlm ? settings.getLlmModel() : "rules-v1",
                        json);
        var outcome =
                new AsrResponses.Outcome(
                        200, AsrResponses.body("SUCCESS", "操作成功", data), null);

        JsonNode hint = body.path("runtimeContext");
        Entry entry =
                new Entry(
                        hash,
                        outcome,
                        parsed.action(),
                        hint.isNull() ? null : hint.path("runtimeRef").asText(),
                        hint.isNull() ? null : hint.path("runtimeGeneration").asText(),
                        hint.isNull() ? null : hint.path("contextVersion").asLong(),
                        clock.instant());
        synchronized (this) {
            Entry raced = entries.putIfAbsent(key, entry);
            if (raced != null) {
                if (!raced.hash.equals(hash))
                    throw new AsrFailure(409, "IDEMPOTENCY_CONFLICT");
                return raced.outcome;
            }
            // Distinct requests can pass the earlier bound check together. Recheck when
            // committing so concurrent model calls cannot grow the cache past its limit.
            if (entries.size() > 1000) {
                entries.remove(key);
                throw new AsrFailure(429, "VOICE_RATE_LIMITED", 2, false);
            }
        }
        access.require(user, true);
        return outcome;
    }

    /** Validates that a P0 proposal really came from this user's live D2 candidate. */
    public void requireCandidate(
            long user, String interpretationId, String action, ObjectNode runtime) {
        if (interpretationId == null) return;
        if (!AudioMultipart.UUID.matcher(interpretationId).matches())
            throw new AsrFailure(409, "VOICE_INTERPRETATION_INVALID");
        Entry entry;
        synchronized (this) {
            cleanup();
            entry = entries.get(new Key(user, interpretationId));
        }
        if (entry == null
                || entry.action == null
                || !entry.action.equals(action)
                || (entry.runtimeRef != null
                        && (!entry.runtimeRef.equals(runtime.path("runtimeRef").asText())
                                || !entry.generation.equals(
                                        runtime.path("runtimeGeneration").asText())
                                || entry.contextVersion.longValue()
                                        != runtime.path("contextVersion").asLong())))
            throw new AsrFailure(409, "VOICE_INTERPRETATION_INVALID");
    }

    private ObjectNode requireContext(long user, JsonNode hint) {
        if (hint.isNull()) return null;
        ObjectNode current;
        try {
            current = runtimes.require(hint.path("runtimeRef").asText(), user);
        } catch (RuntimeException e) {
            throw new AsrFailure(409, "VOICE_CONTEXT_CHANGED");
        }
        if (!current.path("runtimeGeneration").equals(hint.path("runtimeGeneration"))
                || current.path("contextVersion").asLong()
                        != hint.path("contextVersion").asLong())
            throw new AsrFailure(409, "VOICE_CONTEXT_CHANGED");
        return current;
    }

    private IntentClassification parse(String text, JsonNode allowed, ObjectNode runtime) {
        if (UNSUPPORTED.matcher(text).find())
            return new IntentClassification(
                    "UNSUPPORTED",
                    "UNSUPPORTED_CAPABILITY",
                    "该动作尚未接入算法能力，不能生成执行提案。",
                    null);
        if (TARGETED.matcher(text).find())
            return new IntentClassification(
                    "UNSUPPORTED",
                    "UNSUPPORTED_TARGETING",
                    "当前仅支持整队任务控制，暂不支持指定单台设备。",
                    null);
        if (NEGATED.matcher(text).find())
            return new IntentClassification(
                    "NOT_ACTIONABLE",
                    "NEGATED_ACTION",
                    "检测到否定表达，为避免误执行，请重新明确指令。",
                    null);
        List<String> matched =
                ACTIONS.stream().filter(a -> PATTERNS.get(a).matcher(text).find()).sorted().toList();
        if (matched.size() > 1)
            return new IntentClassification(
                    "NEEDS_CLARIFICATION",
                    "AMBIGUOUS_ACTION",
                    "一句话中包含多个动作，请一次只说明一个任务动作。",
                    null);
        IntentClassification parsed;
        String exactAction = EXACT_COMMANDS.get(text);
        if (exactAction != null) {
            parsed = new IntentClassification("CANDIDATE", null, null, exactAction);
        } else if ("local-llm".equals(settings.getIntentProvider())) {
            if (llm == null) throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
            parsed = llm.classify(text);
        } else if ("rules".equals(settings.getIntentProvider())) {
            parsed =
                    matched.isEmpty()
                            ? new IntentClassification(
                                    "NEEDS_CLARIFICATION",
                                    "NO_SUPPORTED_ACTION",
                                    "未识别到开始、暂停、继续或停止，请重新表述。",
                                    null)
                            : new IntentClassification(
                                    "CANDIDATE", null, null, matched.get(0));
        } else {
            throw new AsrFailure(503, "VOICE_PROVIDER_UNAVAILABLE");
        }
        if (parsed.action() == null) return parsed;
        String action = parsed.action();
        boolean hinted = false;
        for (JsonNode n : allowed) hinted |= action.equals(n.asText());
        boolean authoritative = runtime == null;
        if (runtime != null)
            for (JsonNode n : runtime.path("capabilities"))
                authoritative |= action.equals(n.asText());
        if (!hinted || !authoritative)
            return new IntentClassification(
                    "UNSUPPORTED",
                    "UNSUPPORTED_CAPABILITY",
                    "当前运行上下文不支持该动作。",
                    null);
        return parsed;
    }

    private void validate(String requestId, JsonNode body) {
        if (!AudioMultipart.UUID.matcher(requestId).matches()
                || !body.isObject()
                || body.size() != 6
                || !requestId.equals(body.path("requestId").asText())
                || !"zh-CN".equals(body.path("locale").asText())
                || !body.path("text").isTextual()
                || body.path("text").asText().isBlank()
                || body.path("text").asText().codePointCount(0, body.path("text").asText().length())
                        > 200
                || !body.path("allowedActions").isArray()
                || body.path("allowedActions").size() > 4
                || !body.path("availableDeviceCodes").isArray()
                || body.path("availableDeviceCodes").size() > 200
                || !(body.path("runtimeContext").isNull()
                        || validRuntimeHint(body.path("runtimeContext"))))
            throw AsrFailure.invalid();
        if (!Set.of(
                        "requestId",
                        "text",
                        "locale",
                        "allowedActions",
                        "availableDeviceCodes",
                        "runtimeContext")
                .equals(fieldNames(body))) throw AsrFailure.invalid();
        var actions = new HashSet<String>();
        for (JsonNode n : body.path("allowedActions"))
            if (!n.isTextual() || !ACTIONS.contains(n.asText()) || !actions.add(n.asText()))
                throw AsrFailure.invalid();
        var devices = new HashSet<String>();
        for (JsonNode n : body.path("availableDeviceCodes"))
            if (!n.isTextual()
                    || !n.asText().matches("[A-Za-z0-9_.:-]{1,96}")
                    || !devices.add(n.asText())) throw AsrFailure.invalid();
    }

    private boolean validRuntimeHint(JsonNode n) {
        return n.isObject()
                && n.size() == 3
                && fieldNames(n)
                        .equals(Set.of("runtimeRef", "runtimeGeneration", "contextVersion"))
                && AudioMultipart.UUID.matcher(n.path("runtimeRef").asText()).matches()
                && AudioMultipart.UUID.matcher(n.path("runtimeGeneration").asText()).matches()
                && n.path("contextVersion").isIntegralNumber()
                && n.path("contextVersion").canConvertToLong()
                && n.path("contextVersion").asLong() >= 0;
    }

    private static Set<String> fieldNames(JsonNode n) {
        var names = new HashSet<String>();
        n.fieldNames().forEachRemaining(names::add);
        return names;
    }

    private static String normalize(String text) {
        return text.trim().replaceAll("\\s+", "").replaceAll("[。！？!?，,]+$", "");
    }

    @Scheduled(fixedDelay = 60000)
    public synchronized void cleanup() {
        Instant cutoff = clock.instant().minusSeconds(1800);
        entries.values().removeIf(e -> !e.completed.isAfter(cutoff));
    }
}
