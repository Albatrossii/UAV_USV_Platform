# 阿里云一句话识别真人录音验收记录（2026-10-02）

## 范围与环境

- 基于 `codex/p1-integration-edit` 的 `1bf1f50`，接入代码位于本地提交 `7e696ff`。
- 使用独立工作树。阿里云 AppKey、临时 Token、数据库密码只存于被 Git 忽略的本机配置；本记录不含凭证值。
- 后端通过独立临时 MySQL 实例启动，前端以 `VITE_VOICE_ASR_ONLY=true` 启动。本轮没有提交设备控制。
- 以用户于 20:21–20:22 补充的四段录音为准。均为 48 kHz、双声道、16 位 PCM WAV；适配器经 FFmpeg 转成 16 kHz、单声道、16 位 PCM WAV 后调用阿里云一句话识别。

## 后端上传与识别结果

使用管理员会话与动态 CSRF 令牌，通过 `POST /api/voice/intelligence/transcriptions` 逐条上传。请求带有一致的 `requestId`、`X-Request-ID` 和 `Idempotency-Key`。四次均返回 HTTP 200、`code=SUCCESS`、`data.provider=aliyun-asr`。

| 文件 | 时长 | 用户确认的原话 | 阿里云返回文字 | 逐字结果 |
| --- | ---: | --- | --- | --- |
| `start.wav` | 1.52 秒 | 开始任务 | 任务。 | 未通过，缺“开始” |
| `pause.wav` | 2.24 秒 | 暂停任务 | 暂停任务。 | 通过，忽略标点 |
| `stop1.wav` | 2.92 秒 | 第一架无人机悬停 | 低价无人机悬停。 | 未通过，目标序号错误 |
| `return1.wav` | 2.52 秒 | 第一架无人机返航 | 低价无人机返航。 | 未通过，目标序号错误 |

结论：真实云端连接、音频转码、登录与 CSRF 保护、平台上传接口均已通过真人录音验证；**逐字识别正确率本轮为 1/4，尚未达到交接说明的文字正确要求**。不能把“低价”自动修正成“第一架”，以免错误定位设备。

将四条返回文字分别提交到现有 `/api/voice/intelligence/interpretations`，未提交任何控制命令：

| 文件 | 意图解析结果 |
| --- | --- |
| `start.wav` | HTTP 503 / `VOICE_PROVIDER_UNAVAILABLE`，没有动作候选 |
| `pause.wav` | HTTP 200 / `CANDIDATE` / `PAUSE` |
| `stop1.wav` | HTTP 200 / `UNSUPPORTED`，没有动作候选 |
| `return1.wav` | HTTP 200 / `UNSUPPORTED`，没有动作候选 |

以上意图解析辅助检查使用空 `runtimeContext`，不代表仿真运行时的完整设备命令能力。完整语音页面在满足权限和仿真条件时可能自动提交控制提案，因此当前保持 `VITE_VOICE_ASR_ONLY=true`，直到目标设备名称识别正确。

对三条误识别录音试过在本机生成“前后补静音、提高音量”的临时副本，阿里云仍分别返回“任务。”“低架无人机悬停。”“低价无人机返航。”，简单音量与留白处理未解决问题；这些副本未作为正式验收材料。

## 后续验收

- 为 `start.wav` 重新录制完整的“开始任务”，说话前后各留短暂空白，避免起始字被截掉。
- 为“第一架无人机”两句优化语音模型词汇或重新录制，并再次逐句核对目标序号。已准备同目录的 `aliyun-asr-hotwords-20261002.txt` 供在[阿里云控制台创建业务类热词](https://help.aliyun.com/zh/isi/user-guide/create-hotwords)并应用到该项目；该词表尚未应用，生效后仍须用原始真人录音重新验证。
- 本轮使用录音文件，没有测试浏览器麦克风采集，也没有在仿真中执行设备控制。控制台临时 Token 过期后需在本机替换；长期运行需要服务端自动获取与刷新 Token。