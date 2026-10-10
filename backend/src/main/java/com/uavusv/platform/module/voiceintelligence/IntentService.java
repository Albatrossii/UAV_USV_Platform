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
import java.util.regex.Matcher;
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
    // Short, explicit commands can skip the local LLM round trip. These are whole-utterance
    // matches only; arbitrary sentences still use the configured parser and safety checks.
    private static final Map<String, String> EXACT_COMMANDS =
            Map.ofEntries(
                    Map.entry("开始任务", "START"),
                    Map.entry("开始执行任务", "START"),
                    Map.entry("启动任务", "START"),
                    Map.entry("执行任务", "START"),
                    Map.entry("请开始任务", "START"),
                    Map.entry("请开始执行任务", "START"),
                    Map.entry("现在开始任务", "START"),
                    Map.entry("开始当前任务", "START"),
                    Map.entry("启动当前任务", "START"),
                    Map.entry("暂停任务", "PAUSE"),
                    Map.entry("暂停一下", "PAUSE"),
                    Map.entry("暂停当前任务", "PAUSE"),
                    Map.entry("暂停一下当前任务", "PAUSE"),
                    Map.entry("请暂停任务", "PAUSE"),
                    Map.entry("继续任务", "RESUME"),
                    Map.entry("继续执行任务", "RESUME"),
                    Map.entry("恢复任务", "RESUME"),
                    Map.entry("恢复运行", "RESUME"),
                    Map.entry("恢复执行", "RESUME"),
                    Map.entry("请继续任务", "RESUME"),
                    Map.entry("停止任务", "STOP"),
                    Map.entry("停止执行任务", "STOP"),
                    Map.entry("停止当前任务", "STOP"),
                    Map.entry("停止当前运行任务", "STOP"),
                    Map.entry("结束任务", "STOP"),
                    Map.entry("结束当前任务", "STOP"),
                    Map.entry("终止任务", "STOP"),
                    Map.entry("终止当前任务", "STOP"),
                    Map.entry("请停止任务", "STOP"),
                    Map.entry("请停止当前任务", "STOP"),
                    Map.entry("立即停止任务", "STOP"),
                    Map.entry("马上停止任务", "STOP"),
                    Map.entry("请立即停止任务", "STOP"));
    private static final Pattern NEGATED =
            Pattern.compile("(不要|别|无需|不用|禁止).{0,24}(开始|启动|暂停|继续|恢复|停止|终止|结束|悬停|驻留|待命|返航|归队|降落|停船)");
    private static final Pattern UNSUPPORTED = Pattern.compile("攻击|打击|开火|围捕|包围|撤退|返航");
    private static final Pattern TARGETED =
            Pattern.compile(
                    "(UAV|USV)[-_]?\\d+|(?:第?[一二三四五六七八九十\\d]+|某(?:一|个))号?(?:架|艘)?(?:无人机|无人艇)|(?:无人机|无人艇)(?:[-_]?\\d+|[一二三四五六七八九十]+)",
                    Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern DEVICE_CUE = Pattern.compile("UAV|USV|无人机|无人艇", Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern CONTROLLED_SEQUENCE =
            Pattern.compile(
                    "^(?:请)?(?:先)?(开始(?:执行|当前)?任务|启动(?:当前)?任务|执行任务)[，,。；;]?(?:(?:之后|以后|后|然后|再|接着)[，,。；;]?)?(?:再)?(?:让|请)?(.+)$",
                    Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
    private static final Pattern TASK_CLAUSE =
            Pattern.compile("(?:开始|启动|执行|暂停|继续|恢复|停止|终止|结束)(?:执行|当前|运行)?任务");
    // Conservative ASR repairs for unmistakable device nouns. Keep aligned
    // with the browser's automatic-execution guard; do not add fuzzy matching.
    private static final Map<String, String> DEVICE_TERM_CORRECTIONS =
            Map.ofEntries(
                    Map.entry("艇无人", "无人艇"),
                    Map.entry("机无人", "无人机"),
                    Map.entry("无人庭", "无人艇"),
                    Map.entry("无人廷", "无人艇"),
                    Map.entry("无人停", "无人艇"),
                    Map.entry("无人鸡", "无人机"),
                    Map.entry("无人基", "无人机"));

    private record Key(long user, String requestId) {}

    private static final class Entry {
        final String hash;
        final AsrResponses.Outcome outcome;
        final String action;
        final String targetDeviceCode;
        final String deviceCommandType;
        final String runtimeRef;
        final String generation;
        final Long contextVersion;
        final Instant completed;

        Entry(
                String hash,
                AsrResponses.Outcome outcome,
                String action,
                String targetDeviceCode,
                String deviceCommandType,
                String runtimeRef,
                String generation,
                Long contextVersion,
                Instant completed) {
            this.hash = hash;
            this.outcome = outcome;
            this.action = action;
            this.targetDeviceCode = targetDeviceCode;
            this.deviceCommandType = deviceCommandType;
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
        boolean localLlm = "local-llm".equals(settings.getIntentProvider())
                && !exactCommand
                && !Set.of("DEVICE_COMMAND", "SEQUENCE").contains(parsed.action());
        boolean sequence = "SEQUENCE".equals(parsed.action());
        ObjectNode data =
                parsed.data(
                        requestId,
                        text,
                        localLlm ? "local-llm" : "local-rules",
                        localLlm ? settings.getLlmModel() : sequence ? "rules-sequence-v1" : "rules-v1",
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
                        parsed.targetDeviceCode(),
                        parsed.deviceCommandType(),
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

    public record TargetedCandidate(String deviceCode, String commandType) {}

    public com.fasterxml.jackson.databind.node.ArrayNode requireSequenceSteps(
            long user, String interpretationId, ObjectNode runtime) {
        requireCandidate(user, interpretationId, "SEQUENCE", runtime);
        Entry entry;
        synchronized (this) { entry = entries.get(new Key(user, interpretationId)); }
        if (entry == null || entry.runtimeRef == null)
            throw new AsrFailure(409, "VOICE_INTERPRETATION_INVALID");
        return ((com.fasterxml.jackson.databind.node.ArrayNode)
                ((ObjectNode) entry.outcome.body().get("data")).path("steps")).deepCopy();
    }

    public TargetedCandidate requireSequenceCandidate(
            long user, String interpretationId, ObjectNode runtime) {
        if (interpretationId == null || !AudioMultipart.UUID.matcher(interpretationId).matches())
            throw new AsrFailure(409, "VOICE_INTERPRETATION_INVALID");
        Entry entry;
        synchronized (this) {
            cleanup();
            entry = entries.get(new Key(user, interpretationId));
        }
        if (entry == null || !"SEQUENCE".equals(entry.action)
                || entry.targetDeviceCode == null || entry.deviceCommandType == null
                || entry.runtimeRef == null
                || !entry.runtimeRef.equals(runtime.path("runtimeRef").asText())
                || !entry.generation.equals(runtime.path("runtimeGeneration").asText())
                || entry.contextVersion == null
                || entry.contextVersion.longValue() != runtime.path("contextVersion").asLong())
            throw new AsrFailure(409, "VOICE_INTERPRETATION_INVALID");
        return new TargetedCandidate(entry.targetDeviceCode, entry.deviceCommandType);
    }

    public TargetedCandidate requireTargetedCandidate(
            long user, String interpretationId, ObjectNode runtime) {
        if (interpretationId == null || !AudioMultipart.UUID.matcher(interpretationId).matches())
            throw new AsrFailure(409, "VOICE_INTERPRETATION_INVALID");
        Entry entry;
        synchronized (this) {
            cleanup();
            entry = entries.get(new Key(user, interpretationId));
        }
        if (entry == null || !"DEVICE_COMMAND".equals(entry.action)
                || entry.targetDeviceCode == null || entry.deviceCommandType == null
                || entry.runtimeRef == null
                || !entry.runtimeRef.equals(runtime.path("runtimeRef").asText())
                || !entry.generation.equals(runtime.path("runtimeGeneration").asText())
                || entry.contextVersion == null
                || entry.contextVersion.longValue() != runtime.path("contextVersion").asLong())
            throw new AsrFailure(409, "VOICE_INTERPRETATION_INVALID");
        return new TargetedCandidate(entry.targetDeviceCode, entry.deviceCommandType);
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
        if (NEGATED.matcher(text).find())
            return new IntentClassification(
                    "NOT_ACTIONABLE",
                    "NEGATED_ACTION",
                    "检测到否定表达，为避免误执行，请重新明确指令。",
                    null);
        IntentClassification sequence = parseSequence(text, allowed, runtime);
        if (sequence != null) return sequence;
        if (TARGETED.matcher(text).find()
                || (DEVICE_CUE.matcher(text).find()
                        && Pattern.compile("悬停|驻留|待命|保持|返航|返回|归队|继续|停止|降落|停船").matcher(text).find()))
            return parseTargeted(text, runtime);
        if (text.contains("设备") && Pattern.compile("悬停|驻留|返航|归队|降落|停止").matcher(text).find())
            return new IntentClassification("NEEDS_CLARIFICATION", "AMBIGUOUS_TARGET", "请明确无人机或无人艇，以及设备编号。", null);
        if (UNSUPPORTED.matcher(text).find())
            return new IntentClassification(
                    "UNSUPPORTED",
                    "UNSUPPORTED_CAPABILITY",
                    "该动作尚未接入算法能力，不能生成执行提案。",
                    null);
        IntentClassification parsed;
        String exactAction = EXACT_COMMANDS.get(text);
        if (exactAction != null) {
            parsed = new IntentClassification("CANDIDATE", null, null, exactAction);
        } else {
            List<String> matched =
                    ACTIONS.stream()
                            .filter(a -> PATTERNS.get(a).matcher(text).find())
                            .sorted()
                            .toList();
            if (matched.size() > 1)
                return new IntentClassification(
                        "NEEDS_CLARIFICATION",
                        "AMBIGUOUS_ACTION",
                        "一句话中包含多个动作，请一次只说明一个任务动作。",
                        null);
            if ("local-llm".equals(settings.getIntentProvider())) {
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

    private IntentClassification parseSequence(String text, JsonNode allowed, ObjectNode runtime) {
        String separated = text;
        Matcher compact = CONTROLLED_SEQUENCE.matcher(text);
        if (compact.matches()) separated = compact.group(1) + "，" + compact.group(2);
        String[] clauses = separated.split("(?:[，,。；;]*(?:然后|接着|之后|以后|再)[，,。；;]*|[，,。；;]+)", -1);
        if (clauses.length == 1) return null;
        if (clauses.length < 2 || clauses.length > 4)
            return new IntentClassification("NEEDS_CLARIFICATION", "AMBIGUOUS_ACTION", "请一次说明 2–4 个顺序动作。", null);
        var steps = json.mapper.createArrayNode();
        String lastTarget = null;
        String lastCommand = null;
        for (String raw : clauses) {
            String clause = raw.replaceFirst("^(?:请)?(?:先)?(?:让)?", "");
            String action = EXACT_COMMANDS.get(clause);
            var step = steps.addObject().put("index", steps.size() - 1);
            if (action != null) {
                boolean capable = false;
                for (JsonNode a : allowed) capable |= action.equals(a.asText());
                if (!capable) return new IntentClassification("UNSUPPORTED", "UNSUPPORTED_CAPABILITY", "当前场景不支持序列中的任务动作。", null);
                step.put("action", action);
                lastTarget = null;
            } else {
                Matcher wait = Pattern.compile("^(?:等待|等)([一二三四五六七八九十]|\\d{1,2})秒$").matcher(clause);
                if (wait.matches()) {
                    int seconds = chineseNumber(wait.group(1));
                    if (steps.size() == 1 || seconds < 1 || seconds > 60)
                        return new IntentClassification("NEEDS_CLARIFICATION", "AMBIGUOUS_ACTION", "等待须放在动作之后，时长为 1–60 秒。", null);
                    step.put("action", "WAIT").put("waitSeconds", seconds);
                    continue;
                }
                if (lastTarget != null && clause.matches("(?:它)?(?:悬停|驻留|待命|返航|返回|归队|继续|停止|降落|停船)"))
                    clause = lastTarget + clause.replaceFirst("^它", "");
                if (!TARGETED.matcher(clause).find())
                    return new IntentClassification("NEEDS_CLARIFICATION", "AMBIGUOUS_ACTION", "请明确每一步的任务动作或设备编号与动作。", null);
                IntentClassification target = parseTargeted(clause, runtime);
                if (!"CANDIDATE".equals(target.status())) return target;
                lastTarget = target.targetDeviceCode();
                lastCommand = target.deviceCommandType();
                step.put("action", "DEVICE_COMMAND").put("targetDeviceCode", lastTarget).put("deviceCommandType", lastCommand);
            }
        }
        return new IntentClassification("CANDIDATE", null, null, "SEQUENCE", lastTarget, lastCommand, steps);
    }

    private IntentClassification parseTargeted(String text, ObjectNode runtime) {
        if (NEGATED.matcher(text).find())
            return new IntentClassification(
                    "NOT_ACTIONABLE", "NEGATED_ACTION", "检测到否定表达，请重新明确指令。", null);
        // Never discard a task-level action and execute only the device fragment.
        // Supported START + device sequences have already removed their first clause.
        if (TASK_CLAUSE.matcher(text).find())
            return new IntentClassification(
                    "NEEDS_CLARIFICATION", "AMBIGUOUS_ACTION",
                    "检测到任务动作和设备动作；当前支持“开始任务，让一号无人机悬停”或“开始任务，让一号无人艇驻留”。", null);
        if (runtime == null || !Set.of("ESCORT_GUARD", "GB_SFLA_CS", "ESCORT_GUARD_SINGLE_DEVICE", "GB_SFLA_CS_SINGLE_DEVICE")
                .contains(runtime.path("_algorithmCode").asText()))
            return new IntentClassification(
                    "UNSUPPORTED", "UNSUPPORTED_TARGETING", "当前运行实例未启用单设备控制。", null);

        List<String> members = new ArrayList<>();
        runtime.path("_members").forEach(n -> members.add(n.asText()));
        List<String> mentioned = new ArrayList<>();
        for (String code : members) {
            String compactCode = code.replaceAll("[^A-Za-z0-9]", "").toUpperCase(Locale.ROOT);
            String compactText = text.replaceAll("[^A-Za-z0-9]", "").toUpperCase(Locale.ROOT);
            if (!compactCode.isEmpty() && compactText.contains(compactCode)) mentioned.add(code);
        }
        Matcher matcher = TARGETED.matcher(text);
        Set<String> targetTokens = new LinkedHashSet<>();
        while (matcher.find()) targetTokens.add(matcher.group());
        for (String token : targetTokens) {
            String compactToken = token.replaceAll("[^A-Za-z0-9]", "").toUpperCase(Locale.ROOT);
            if (compactToken.matches("UAV\\d+") || compactToken.matches("USV\\d+")) {
                String type = compactToken.startsWith("UAV") ? "UAV" : "USV";
                String number = compactToken.substring(3);
                for (String code : members) {
                    String compact = code.replaceAll("[^A-Za-z0-9]", "").toUpperCase(Locale.ROOT);
                    if (compact.startsWith(type) && compact.substring(3).replaceFirst("^0+", "").equals(number.replaceFirst("^0+", ""))) mentioned.add(code);
                }
            } else {
                Matcher cn = Pattern.compile("(?:第?([一二三四五六七八九十]|\\d+)号?(?:架|艘)?(无人机|无人艇)|(无人机|无人艇)([一二三四五六七八九十]|\\d+))").matcher(token);
                if (cn.find()) {
                    String kind = cn.group(2) != null ? cn.group(2) : cn.group(3);
                    String ordinal = cn.group(1) != null ? cn.group(1).trim() : cn.group(4).trim();
                    int number = chineseNumber(ordinal);
                    String type = "无人机".equals(kind) ? "UAV" : "USV";
                    for (String code : members) {
                        String compact = code.replaceAll("[^A-Za-z0-9]", "").toUpperCase(Locale.ROOT);
                        String digits = compact.startsWith(type) ? compact.substring(3).replaceFirst("^0+", "") : "";
                        if (!digits.isEmpty() && digits.equals(Integer.toString(number))) mentioned.add(code);
                    }
                }
            }
        }
        mentioned = mentioned.stream().distinct().toList();
        if (mentioned.size() != 1)
            return new IntentClassification(
                    "NEEDS_CLARIFICATION", "AMBIGUOUS_TARGET", "请明确一个当前场景中存在的设备编号。", null);
        String target = mentioned.get(0);
        boolean uav = target.toUpperCase(Locale.ROOT).startsWith("UAV");
        boolean usv = target.toUpperCase(Locale.ROOT).startsWith("USV");
        if (!uav && !usv)
            return new IntentClassification("UNSUPPORTED", "UNSUPPORTED_TARGETING", "目标设备类型不支持语音控制。", null);

        Map<String, String> commands = uav
                ? Map.of("悬停", "UAV_HOVER", "暂停", "UAV_HOVER", "返航", "UAV_RETURN", "返回", "UAV_RETURN", "归队", "UAV_RESUME", "继续", "UAV_RESUME", "停止", "UAV_LAND", "降落", "UAV_LAND")
                : Map.of("驻留", "USV_HOLD", "待命", "USV_HOLD", "保持", "USV_HOLD", "暂停", "USV_HOLD", "返航", "USV_RETURN", "返回", "USV_RETURN", "归队", "USV_RESUME", "继续", "USV_RESUME", "停止", "USV_STOP", "停船", "USV_STOP");
        Set<String> matched = new LinkedHashSet<>();
        long actionWords = commands.keySet().stream().filter(text::contains).count();
        commands.forEach((word, command) -> { if (text.contains(word)) matched.add(command); });
        if (actionWords != 1 || matched.size() != 1)
            return new IntentClassification(
                    "NEEDS_CLARIFICATION", "AMBIGUOUS_ACTION", "请一次明确一个设备动作，例如“UAV-001 悬停”。", null);
        String command = matched.iterator().next();
        boolean deviceCommandCapable = false;
        for (JsonNode capability : runtime.path("capabilities"))
            deviceCommandCapable |= "DEVICE_COMMAND".equals(capability.asText());
        if (!deviceCommandCapable)
            return new IntentClassification("UNSUPPORTED", "UNSUPPORTED_CAPABILITY", "当前算法实例没有声明单设备控制能力。", null);
        return new IntentClassification("CANDIDATE", null, null, "DEVICE_COMMAND", target, command);
    }

    private static int chineseNumber(String text) {
        if (text.matches("\\d+")) return Integer.parseInt(text);
        return switch (text) {
            case "一" -> 1; case "二" -> 2; case "三" -> 3; case "四" -> 4; case "五" -> 5;
            case "六" -> 6; case "七" -> 7; case "八" -> 8; case "九" -> 9; case "十" -> 10;
            default -> -1;
        };
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
        String normalized = text.trim().replaceAll("\\s+", "").replaceAll("[。！？!?，,]+$", "");
        for (Map.Entry<String, String> correction : DEVICE_TERM_CORRECTIONS.entrySet())
            normalized = normalized.replace(correction.getKey(), correction.getValue());
        normalized = normalized.replaceAll("(第?[一二三四五六七八九十\\d]+)(?:号|架)(?:飞机|机)(?!无人)", "$1号无人机")
                .replaceAll("(第?[一二三四五六七八九十\\d]+)(?:号|艘)(?:船|艇)(?!无人)", "$1号无人艇");
        return normalized;
    }

    @Scheduled(fixedDelay = 60000)
    public synchronized void cleanup() {
        Instant cutoff = clock.instant().minusSeconds(1800);
        entries.values().removeIf(e -> !e.completed.isAfter(cutoff));
    }
}
