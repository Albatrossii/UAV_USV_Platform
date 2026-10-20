package com.uavusv.platform.module.voicecontrol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.text.Normalizer;
import java.util.*;
import java.util.regex.*;

/** Versioned public meanings of the algorithm owner's fixed presets. */
public final class AlgorithmPresets {
    public static final String VERSION = "escort-20261020";
    private static final String[] COMMANDS = {
        "cancel", "chase U1 T1", "chase S1 T1", "chase U2 T2", "chase S2 T2", "chase U3 T1", "chase S3 T2",
        "chase U1 T2", "chase S1 T2", "chase U2 T1", "chase S2 T1", "chase U3 T2", "chase S3 T1",
        "bind T1 U1 U2 U3", "bind T2 S1 S2 S3", "bind T1 S1 S2 S3", "bind T2 U1 U2 U3",
        "bind T1 U1 S1 U2", "bind T2 S2 U3 S3", "guard", "capture"
    };
    private static final String[] LABELS = {
        "全体撤退，恢复护航", "一号无人机追击一号敌船", "一号无人艇追击一号敌船",
        "二号无人机追击二号敌船", "二号无人艇追击二号敌船", "三号无人机追击一号敌船", "三号无人艇追击二号敌船",
        "一号无人机追击二号敌船", "一号无人艇追击二号敌船", "二号无人机追击一号敌船", "二号无人艇追击一号敌船",
        "三号无人机追击二号敌船", "三号无人艇追击一号敌船",
        "将一二三号无人机分配给一号敌船", "将一二三号无人艇分配给二号敌船",
        "将一二三号无人艇分配给一号敌船", "将一二三号无人机分配给二号敌船",
        "将一二号无人机和一号无人艇分配给一号敌船", "将二三号无人艇和三号无人机分配给二号敌船",
        "形成守卫队形", "开始围捕"
    };
    private static final String NUMERAL = "[零〇一二两兩三四五六七八九十壹贰貳叁參肆伍陆陸柒捌玖拾IVXLCDMivxlcdm0-9]+";
    // Match the whole request: changing the spelling of a number must not
    // discard a condition, negation, second command or device identifier.
    private static final Pattern NUMBER_FIRST = Pattern.compile(
            "^(?:第(" + NUMERAL + ")(?:号|条|个)?|(" + NUMERAL + ")(?:号|条)?)(?:指令|命令)?$");
    private static final Pattern COMMAND_FIRST = Pattern.compile(
            "^(?:指令|命令)(?:编号)?(?:为|是|:)?第?(" + NUMERAL + ")(?:号|条|个)?$");
    private static final Pattern NUMBERED_COMMAND = Pattern.compile(
            "^编号(?:为|是|:)?第?(" + NUMERAL + ")(?:号|条|个)?(?:的)?(?:指令|命令)$");
    private static final String[] ROMAN = {
        "", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
        "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"
    };
    private AlgorithmPresets() {}
    public static String label(int id) { return LABELS[id]; }
    public static ObjectNode parameters(VoiceJson json, int id) {
        return json.object().put("presetId", id).put("presetVersion", VERSION);
    }
    public static ObjectNode catalog(VoiceJson json) {
        var result = json.object().put("version", VERSION);
        var items = result.putArray("items");
        for (int i=0;i<COMMANDS.length;i++) items.addObject().put("id", i).put("command", COMMANDS[i])
                .put("label", LABELS[i]).put("effect", i >= 13 && i <= 18 ? "设置固定分组；执行20后启动围捕" : i == 20 ? "启动围捕，不代表已捕获" : i == 0 ? "撤回护航，不停止仿真、不返回泊位" : "切换算法任务");
        return result;
    }
    public static Integer parse(String raw) {
        if (raw == null) return null;
        String text = Normalizer.normalize(raw, Normalizer.Form.NFKC).replaceAll("\\s+", "")
                .replaceAll("[。!，,]+$", "").replaceFirst("^(?:请)?(?:让)?", "");
        for (int i=0;i<LABELS.length;i++) if (text.equals(LABELS[i].replace("，", ""))
                || text.equals(Normalizer.normalize(LABELS[i], Normalizer.Form.NFKC))) return i;
        if (Set.of("恢复护航", "全体撤退", "撤回护航").contains(text)) return 0;
        if (Set.of("恢复守卫", "自动守卫", "形成守卫阵型").contains(text)) return 19;
        if (Set.of("启动围捕", "执行围捕").contains(text)) return 20;
        String numbered = text.replaceFirst("^(?:麻烦|劳驾)?(?:请)?(?:帮我|帮忙)?(?:现在|立即|马上)?(?:开始执行|执行|运行|启动|下达)?", "")
                .replaceFirst("(?:一下)?(?:吧)?$", "");
        Matcher number = NUMBER_FIRST.matcher(numbered);
        String token = null;
        if (number.matches()) token = number.group(1) != null ? number.group(1) : number.group(2);
        else {
            number = COMMAND_FIRST.matcher(numbered);
            if (number.matches()) token = number.group(1);
            else {
                number = NUMBERED_COMMAND.matcher(numbered);
                if (number.matches()) token = number.group(1);
            }
        }
        if (token != null) {
            int id = number(token);
            return id >= 0 && id <= 20 ? id : null;
        }
        // Preserve type and target number; no fuzzy repairs for tactical actions.
        Matcher chase = Pattern.compile("^第?([一二三123])号(无人机|无人艇)追击第?([一二12])号(?:敌船|目标|敌方目标)$").matcher(text);
        if (chase.matches()) {
            String command = "chase " + (chase.group(2).equals("无人机") ? "U" : "S") + number(chase.group(1)) + " T" + number(chase.group(3));
            for (int i=1;i<=12;i++) if (COMMANDS[i].equals(command)) return i;
        }
        return null;
    }
    public static boolean looksNumbered(String text) {
        // Invalid bare numbers must not become START/RESUME via keyword fallback.
        return Pattern.compile("^(?:麻烦|劳驾)?(?:请)?(?:帮我|帮忙)?(?:现在|立即|马上)?"
                + "(?:开始执行|恢复执行|执行|运行|启动|下达)?(?:第|编号|[零〇一二两兩三四五六七八九十壹贰貳叁參肆伍陆陸柒捌玖拾IVXLCDMivxlcdm0-9+-])")
                .matcher(text).find();
    }
    private static int number(String value) {
        String from = "〇两兩壹贰貳叁參肆伍陆陸柒捌玖拾";
        String to =   "零二二一二二三三四五六六七八九十";
        for (int i = 0; i < from.length(); i++) value = value.replace(from.charAt(i), to.charAt(i));
        if (value.matches("\\d{1,3}")) return Integer.parseInt(value);
        for (int i = 1; i < ROMAN.length; i++) if (ROMAN[i].equalsIgnoreCase(value)) return i;
        if (value.equals("二十")) return 20;
        if (value.startsWith("一十")) value = value.substring(1);
        if (value.matches("十[一二三四五六七八九]?")) return 10 + (value.length() == 1 ? 0 : "零一二三四五六七八九".indexOf(value.substring(1)));
        return value.length() == 1 ? "零一二三四五六七八九".indexOf(value) : -1;
    }
    public static void validate(ObjectNode context, JsonNode parameters) {
        if (!parameters.path("presetId").isIntegralNumber() || !parameters.path("presetId").canConvertToInt()
                || !VERSION.equals(parameters.path("presetVersion").asText())) throw VoiceFailure.conflict("PRESET_INVALID");
        int id = parameters.path("presetId").asInt(-1);
        if (id < 0 || id > 20) throw VoiceFailure.conflict("PRESET_INVALID");
        boolean capable = false;
        for (JsonNode n : context.path("capabilities")) capable |= "ALGORITHM_PRESET".equals(n.asText());
        if (!capable || !VERSION.equals(context.path("_algorithmCommandProfile").asText())) throw VoiceFailure.conflict("UNSUPPORTED_CAPABILITY");
        Matcher targets = Pattern.compile("T([12])").matcher(COMMANDS[id]);
        while (targets.find()) if (!context.path("_algorithmTargets").has("THREAT-00"+targets.group(1)))
            throw VoiceFailure.conflict("PRESET_TARGET_MISSING");
    }
}
