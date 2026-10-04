# 阿里云一句话识别接入与验收

本阶段保持浏览器现有“录完一句再识别”的交互。音频只发往平台后端，后端将其转换成 16 kHz、单声道、16 bit PCM WAV，再调用阿里云智能语音交互（ISI）一句话识别。识别文字仍走现有意图解析、运行状态校验和人工确认链路。阿里云不会直接控制设备。

## 本机配置

1. 将 `backend/src/main/resources/application-local.example.yml` 复制成同目录下被 Git 忽略的 `application-local.yml`，按现有方式配置数据库和登录信息。
2. 在本机后端配置 `APP_VOICEINTELLIGENCE_ENABLED=true` 和 `APP_VOICEINTELLIGENCE_PROVIDER=aliyun`。填写 `APP_VOICEINTELLIGENCE_ALIYUN_APP_KEY`，并选择一种鉴权方式：临时调试可填写 `APP_VOICEINTELLIGENCE_ALIYUN_TOKEN`；长期运行应把 RAM 用户的凭证放入后端环境变量 `ALIYUN_AK_ID` 与 `ALIYUN_AK_SECRET`。AppKey 是项目标识，Token 和 AccessKey 都是敏感凭证；不要把它们写进前端、Git、聊天记录或测试日志。
3. 按项目所在地域设置 `APP_VOICEINTELLIGENCE_ALIYUN_REGION`，可选 `cn-shanghai`、`cn-beijing`、`cn-shenzhen`，默认上海。可通过 `APP_VOICEINTELLIGENCE_ALIYUN_MODEL_ALIAS` 设置展示名称。
4. 浏览器麦克风通常产生 WebM/Opus。要让后端真正转码，请安装可执行的 FFmpeg，并使其在后端进程 `PATH` 中，或通过 `APP_VOICEINTELLIGENCE_ALIYUN_FFMPEG_PATH` 指向其绝对路径。已符合要求的 PCM WAV 可直接使用；改 MIME 名称不能代替转码。
5. 前端按现有流程启用真实后端语音入口：`VITE_VOICE_P1_PREPARATION=true`、`VITE_VOICE_P1_BACKEND=true`、`VITE_VOICE_P1_MOCK=false`。仅验收转文字时设 `VITE_VOICE_ASR_ONLY=true`；联调控制时改为 `false` 并重启前端。

切回本地识别时，把 `APP_VOICEINTELLIGENCE_PROVIDER` 设为 `local`，保留原本本地 ASR 的 `base-url`、`token` 和 `model-revision` 配置。云端模式同时兼容手工 Token 和自动 Token：当 `APP_VOICEINTELLIGENCE_ALIYUN_TOKEN` 非空时优先使用它；该项为空且 AccessKey 两项齐全时，后端通过阿里云 `CreateToken` OpenAPI 获取 Token，按照响应中的 `ExpireTime` 缓存，并默认提前 300 秒刷新。可用 `APP_VOICEINTELLIGENCE_ALIYUN_TOKEN_REFRESH_SKEW_SECONDS` 调整提前量（实现会限制在 60—3600 秒）。RAM 用户需具有 `nls:CreateToken` 权限，例如授予 `AliyunNLSSpeechServiceAccess`。生产环境建议使用最小权限 RAM 用户，不要使用主账号 AccessKey。

## 真人录音验收顺序

1. 先在阿里云控制台用真人语音测试“开始任务”“暂停任务”“第一架无人机悬停”“第一架无人机返航”，确认项目、地域、模型、16 kHz 配置正确。
2. 平台先开 **仅转文字** 模式，通过浏览器实体麦克风逐条录制以上四句。确认回执中 `provider` 为 `aliyun-asr`，文字正确，录音与文字输入可继续使用；此阶段不执行任务控制。也可上传真人录制的音频文件复核。
3. 切到完整语音控制页面，在仿真中先启动任务。识别结果会进入原有意图解析与安全校验；明确且允许的口令可能自动提交，其他候选需要人工核对，所以先完成第 2 步再开启此模式。逐条观察整队命令回执。单设备悬停、返航仅在任务 `RUNNING` 时测试，并核对目标设备与执行回执。
4. 再测试无语音、Token 过期、网络超时和限流等失败情况。明确无语音应允许重新录制；未知结果按原有幂等与恢复机制处理，不重复执行控制命令。

合成音频、单元测试或控制台的成功提示都不能代替第 2–3 步的真人麦克风验收。测试过程中请留意阿里云免费试用额度。

官方依据：[一句话识别 REST API](https://help.aliyun.com/zh/isi/user-guide/restful-api-2)、[获取 Token](https://help.aliyun.com/zh/isi/user-guide/obtain-an-access-token)、[控制台临时 Token](https://help.aliyun.com/zh/isi/getting-started/obtain-an-access-token-in-the-console)。
