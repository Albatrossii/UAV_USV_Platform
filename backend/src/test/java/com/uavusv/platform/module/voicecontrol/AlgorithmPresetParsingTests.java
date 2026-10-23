package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class AlgorithmPresetParsingTests {
    @ParameterizedTest
    @ValueSource(strings = {"的船", "的穿", "的喘", "地船", "底川", "敌穿", "滴船", "d穿", "D船", "di喘", "di chuan", "ＤＩＣＨＵＡＮ", "敌chuan", "机船"})
    void phoneticTargetSlotPreservesAllEighteenDeviceAndGroupMappings(String noun) {
        for (int id=1; id<=18; id++) {
            String text = AlgorithmPresets.label(id).replace("敌船", noun) + "。";
            assertEquals(AlgorithmPresets.label(id) + "。", AlgorithmPresets.normalizeAlgorithmSpeech(text));
            assertEquals(id, AlgorithmPresets.parse(text), text);
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"不要一号无人机追击一号d穿", "一号无人机追击一号d穿吗？", "如果一号无人机追击一号d穿",
        "一号无人机追击一号d穿然后停止", "一号无人机追击一号d穿或者二号敌船",
        "一号无人机追击三号d穿", "四号无人机追击一号d穿", "一号无人机追击d穿",
        "一号无人机追击一号客船", "一号无人机追击一号货船", "一号无人机追击一号护航船",
        "一号无人机追击一号地图", "一号无人机追击一号d", "一号无人鸡追击一号d穿",
        "将一二三号无人艇分配给三号d穿", "不要将一二三号无人艇分配给二号d穿"})
    void phoneticTargetDoesNotGuessUnresolvedMeaning(String text) {
        assertEquals(java.text.Normalizer.normalize(text, java.text.Normalizer.Form.NFKC), AlgorithmPresets.normalizeAlgorithmSpeech(text));
        assertNull(AlgorithmPresets.parse(text));
    }

    @Test
    void confirmedOneAircraftPhraseDoesNotGeneralizeCountsToIds() {
        assertEquals(1, AlgorithmPresets.parse("让一架无人机追击一号敌船。"));
        assertEquals(1, AlgorithmPresets.parse("请让一架无人机追击一号敌船！"));
        for (String text : new String[]{"任选一架无人机追击一号敌船", "让两架无人机追击一号敌船",
                "不要让一架无人机追击一号敌船", "让一架无人机追击一号敌船吗？",
                "如果让一架无人机追击一号敌船", "让一架无人机追击一号敌船然后停止"}) {
            assertNull(AlgorithmPresets.parse(text));
            assertEquals(java.text.Normalizer.normalize(text, java.text.Normalizer.Form.NFKC), AlgorithmPresets.normalizeAlgorithmSpeech(text));
        }
    }
    @Test
    void bindingRepairsKeepTheExactGroupAndTarget() {
        for (int id=13; id<=18; id++) for (String noun : new String[]{"机船", "的船", "的穿", "的喘"}) {
            assertEquals(id, AlgorithmPresets.parse(AlgorithmPresets.label(id).replace("敌船", noun)+"。"));
        }
        for (String text : new String[]{"不要将一二三号无人艇分配给二号的穿", "将一二三号无人艇分配给二号的穿吗？",
                "将一二三号无人艇分配给三号的穿", "将一二三号无人艇分配给二号的穿然后停止"}) {
            assertNull(AlgorithmPresets.parse(text));
        }
    }
    @Test
    void guardHomophoneOnlyRepairsTheCompleteCommand() {
        for (String text : new String[]{"恢复首位。", "请恢复首位！", "恢复守卫"}) {
            assertEquals(19, AlgorithmPresets.parse(text));
        }
        for (String text : new String[]{"不要恢复首位", "恢复首位吗？", "如果恢复首位", "恢复首位然后停止", "恢复首位或者围捕"}) {
            assertNull(AlgorithmPresets.parse(text));
            assertEquals(java.text.Normalizer.normalize(text, java.text.Normalizer.Form.NFKC), AlgorithmPresets.normalizeAlgorithmSpeech(text));
        }
    }
    @Test
    void chaseHomophonesKeepEveryDeviceAndTargetNumber() {
        int[][] air = {{1,7},{9,3},{5,11}};
        int[][] sea = {{2,8},{10,4},{12,6}};
        for (int unit=1; unit<=3; unit++) for (int target=1; target<=2; target++) {
            for (String noun : new String[]{"的船", "的穿", "的喘", "机船"}) {
                assertEquals(air[unit-1][target-1], AlgorithmPresets.parse(unit+"号无人机追击"+target+"号"+noun+"。"));
                assertEquals(sea[unit-1][target-1], AlgorithmPresets.parse(unit+"号无人艇追击"+target+"号"+noun+"。"));
            }
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {
        "执行围捕观察全过程。", "执行围捕，观察全过程。", "请开始围捕，查看整个过程",
        "启动围捕；然后观看全程", "围捕并观察一下全过程"
    })
    void captureAllowsAnExplicitViewingDescription(String text) {
        assertEquals(20, AlgorithmPresets.parse(text));
    }

    @ParameterizedTest
    @ValueSource(strings = {
        "观察围捕全过程", "不要执行围捕，观察全过程", "如果执行围捕，观察全过程",
        "执行围捕，观察全过程吗？", "执行围捕，停止任务", "执行围捕，观察全过程后停止",
        "执行围捕，观察全过程，不要开始", "执行围捕观察不明目标"
    })
    void viewingDescriptionCannotHideOtherInstructions(String text) {
        assertNull(AlgorithmPresets.parse(text));
    }

    @ParameterizedTest
    @ValueSource(strings = {
        "执行第13条指令", "执行第十三条指令", "第十三号指令", "第十三个指令",
        "执行十三条指令", "13", "十三", "XIII", "xiii", "ⅩⅢ", "ⅹⅲ",
        "执行第XIII条指令", "执行第ⅩⅢ号命令", "执行第１３条指令",
        "指令十三", "指令13", "指令XIII", "指令编号为13", "编号为十三的指令",
        "请执行第十三条指令吧", "麻烦帮我执行第十三个指令一下",
        "请现在执行第十三号命令", "帮我执行第十三条命令", "开始执行第十三号指令",
        "执行第壹拾叁条指令", "执行第拾叁条指令", "执行第一十三条指令",
        "  请 执行 第 13 条 指令 。"
    })
    void equivalentThirteenPhrases(String text) {
        assertEquals(13, AlgorithmPresets.parse(text), text);
    }

    @Test
    void allNumbersAndClassifiersHaveIdenticalMeanings() {
        String[] chinese = {"零","一","二","三","四","五","六","七","八","九","十","十一",
                "十二","十三","十四","十五","十六","十七","十八","十九","二十"};
        String[] roman = {"0","I","II","III","IV","V","VI","VII","VIII","IX","X","XI",
                "XII","XIII","XIV","XV","XVI","XVII","XVIII","XIX","XX"};
        for (int id = 0; id <= 20; id++) {
            for (String number : new String[]{String.valueOf(id), chinese[id], roman[id]}) {
                for (String unit : new String[]{"号", "条", "个"}) {
                    String phrase = "请执行第" + number + unit + "指令";
                    assertEquals(id, AlgorithmPresets.parse(phrase), phrase);
                }
            }
        }
        assertEquals(0, AlgorithmPresets.parse("全体撤退，恢复护航"));
        assertEquals(0, AlgorithmPresets.parse("〇"));
        assertEquals(2, AlgorithmPresets.parse("第兩条指令"));
        assertEquals(20, AlgorithmPresets.parse("第贰拾条指令"));
    }

    @ParameterizedTest
    @ValueSource(strings = {
        "执行第二十一条指令", "指令XXI", "第IIII条指令", "指令IIV", "13.5", "-13",
        "执行第十十条指令", "执行一个指令", "执行两个指令", "执行十三个指令",
        "不要第十三条指令", "别执行XIII", "第13条指令是什么", "执行13号指令吗？",
        "如果需要执行第十三条指令", "执行13或14号指令", "执行13，14号指令",
        "执行第十三条指令然后执行第十四条指令", "UAV-013", "第十三号无人机",
        "第999999999999999999999号指令"
    })
    void doesNotGuessAmbiguousOrMalformedCommands(String text) {
        assertNull(AlgorithmPresets.parse(text), text);
    }
}
