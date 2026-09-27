# P1 D1 ASR、D2 意图候选与 D3 本地模型联调

D1 本地ASR的 D01–D12 已全部验收。当前进入 D2 意图候选阶段，详见 [D2接口与测试设计](P1-D2-意图解析接口与测试设计-v1.0.md)。本地识别和解析仍只放在算法仿真页面右侧“语音控制”面板，不新增页面。

D2 第一轮由 Java 本地受限规则解析器实现，用于完成真实HTTP、安全、幂等、上下文和 P0 提案来源关联；它不是大模型验收。语音录音路径在本地 ASR 与本地离线 LLM 都成功、LLM 给出单一受支持整队动作且当前实例通过运行状态、心跳、权限和 Unity 场景校验后，自动创建并提交同一 P0 冻结提案。提案和确认仍经幂等写请求与服务端校验；文字输入不自动下发。未识别、否定、多动作、单设备目标、非本地模型结果或仿真条件不符时，保留识别文本供操作员编辑重试。自动语音仅适用于 Python 仿真运行，不经 ROS 或真实设备通道。

自动化保留后端 proposal → confirm 事务、Idempotency-Key、ASR 结果与 interpretationId 关联以及 Python 仿真状态门禁；只省去用户对可验证语音候选的第二次鼠标确认。模型输出动作标签，不提供可靠概率分数，所以客户端不会把缺失 confidence 当成概率阈值，而是要求 ASR provider 精确为 `local-asr`、意图 provider 精确为 `local-llm`、分类为单一白名单 `CANDIDATE`，并由服务端复查同一 interpretation、当前代际和冻结计划。

配置见application-d1-asr.yml，启动见start-d1-java.ps1（默认只检查，需先打包）；需环境变量P0_DB_PASSWORD、P0_ADMIN_PASSWORD、P0_INTEGRATION_TOKEN、P0_PYTHON、P0_RUNNER，以及D1_ASR_TOKEN和D1_ASR_MODEL_REVISION。ASR仅127.0.0.1:18082。网页统一localhost，登录后进入`/?workspace=simulation`，校验现有Cookie及CSRF配置。

在常驻 `local` 后端（例如 8083）接入 ASR 时，`app.voiceintelligence` 还必须有可用的 `base-url`、`token` 和 `model-revision`。可通过进程环境 `APP_VOICEINTELLIGENCE_BASE_URL`（默认 `http://127.0.0.1:18082`）、`APP_VOICEINTELLIGENCE_TOKEN` 和 `APP_VOICEINTELLIGENCE_MODEL_REVISION` 提供；仅 ASR `/health/ready` 返回就绪不代表 Java 到 ASR 的内部鉴权成功。D1 已验收模型修订号为 `536b0662742c02347bc0e980a01041f333bce120`。令牌只放本机后端进程环境，不写入仓库或聊天。

D4 整链路集成复测已完成：`真实录音 → 本地 ASR → 人工核对文字 → 本地 LLM 候选 → 冻结提案 → 人工确认 → Runner / Unity`。文件输入与真实物理麦克风两条链路均已通过；动作白名单未扩大，模型不能直接执行，也未新增独立页面。详见 [D4 整链路验收](P1-D4-本地语音到控制整链路验收-v1.0.md)。

配置见application-d1-asr.yml，启动见start-d1-java.ps1（默认只检查，需先打包）；需环境变量P0_DB_PASSWORD、P0_ADMIN_PASSWORD、P0_INTEGRATION_TOKEN、P0_PYTHON、P0_RUNNER，以及D1_ASR_TOKEN和D1_ASR_MODEL_REVISION。ASR仅127.0.0.1:18082。网页统一localhost，登录后进入`/?workspace=simulation`，校验现有Cookie及CSRF配置。

E1 加密结果恢复还要求 `D1_ASR_RESULT_ENCRYPTION_KEY`：值为恰好 32 个随机字节的 Base64。密钥不得写入仓库、普通日志或前端；同一环境跨重启必须保持稳定。更换或丢失密钥会使尚在 24 小时恢复期内的密文不可解，应先暂停提交并按运维流程处理，不能自动重试推理。

V20新增最小受理墓碑；V21在配置加密密钥后保存 AES-256-GCM 终态密文。结果可恢复24小时，之后清除密文并在7天墓碑期返回409 VOICE_REQUEST_EXPIRED；同键不同内容返回409 IDEMPOTENCY_CONFLICT。30天审计只保存请求标识、状态和时间，不保存音频或文字。没有配置V21密钥时保持D1兼容行为：重启后的旧键返回VOICE_REQUEST_OUTCOME_UNKNOWN，仍禁止再次调用Python。

启用ASR时Tomcat上传读超时10秒、maxSwallowSize=0；该连接器设置会作用于该Java进程其他上传入口，D1应使用隔离服务。音频入口采用Servlet非阻塞读取且有10秒总期限，防止multipart落盘；Servlet容器不解析该路径的multipart。其他路径保持原过滤。

运行后端测试：mvn.cmd -f backend/pom.xml -Dtest=AsrAcceptanceStoreTests,AsrServiceTests,AudioMultipartTests,LocalAsrProviderTests,AsrHttpTests,AsrEmbeddedTests,VoiceHttpTests test

真实MySQL迁移专项：配置仅指向服务器根地址的VOICE_TEST_MYSQL_URL以及测试账号后，运行mvn.cmd -f backend/pom.xml -Dtest=AsrMysqlMigrationTests test。测试只创建并删除自己随机命名的uav_usv_asr_test_<32位十六进制>数据库。

契约校验：python docs/voice-control-p1/validate_contracts.py

真实服务就绪后：设置D1_TEST_USERNAME/D1_TEST_PASSWORD，再执行python docs/voice-control-p1/verify-d1-http.py --audio <本机授权音频> --mime audio/mpeg --output <本机结果JSON>。Java重启后可增加--request-id <原UUID> --expect-code VOICE_REQUEST_OUTCOME_UNKNOWN验证墓碑。脚本不输出Cookie、密码、原音频和完整文字；仅HTTP及同键重放/重启保护冒烟，不代替浏览器D01–D12。

D2 及后续 D3 的分阶段记录见 [本地规则与离线模型联调记录](evidence/mxy-d2-local-rules-20260925/verification.md)。规则解析闭环与真实模型闭环分别留证，不能互相替代。

## D3 本地离线模型

当前 Java 适配器支持 `APP_VOICEINTELLIGENCE_INTENT_PROVIDER=local-llm`。配置示例见 `backend/src/main/resources/application-local.example.yml`：模型服务默认使用 `http://127.0.0.1:18083`，模型别名为 `qwen2.5-1.5b-instruct-q4_k_m`，认证通过本机环境变量 `APP_VOICEINTELLIGENCE_LLM_TOKEN` 提供。模型权重及认证值不进入仓库。

浏览器解析等待时间调整为 25 秒，覆盖默认 20 秒的 Java 本地模型调用期限及响应处理时间；这替代早期前端 12 秒等待基线，避免浏览器在后端仍正常推理时提前报错。若调整后端模型期限，应同步检查前端等待预算。结果的“本地离线模型 / 本地规则解析”来源说明可展开查看模型名称和请求编号，便于联调关联原请求。

需要通过语音候选进入仿真提案时，应在本地后端启用 `VOICE_CONTROL_ENABLED=true`，并重新生成 `algorithm.command.v1` 预览上下文。自动路径只替代已验证本地语音候选的界面点击，后端仍检查提案、幂等键、运行代际、当前状态和场景就绪。关闭时生成的上下文为旧协议，不能用于语音提案。后端重启后需恢复登录并重建内存中的 Runner；旧页面快照不代表运行仍存在。
