package com.uavusv.platform.module.voicecontrol;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class AlgorithmPresetParsingTests {
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
