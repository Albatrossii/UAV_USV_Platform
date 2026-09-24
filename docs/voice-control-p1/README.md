# P1 本地语音控制阶段索引

D1 本地 ASR、D2 规则解析候选和 D3 本地 LLM 解析均已完成本机验收。本地识别和解析仍只放在算法仿真页面右侧“语音控制”面板，不新增页面。D3 详细范围和结果见 [D3 接口与测试设计](P1-D3-本地LLM意图解析接口与测试设计-v1.0.md) 与 [D3 验收记录](evidence/D3-LOCAL-LLM-ACCEPTANCE-20260924.md)。

D2 第一轮由 Java 本地受限规则解析器实现，用于冻结真实 HTTP、安全、幂等、上下文和 P0 提案来源关联；D3 已在不改变公共接口和人工确认门禁的前提下切换为本地 `Qwen2.5-1.5B-Instruct Q4_K_M`。解析只生成候选，不能直接执行动作。算法 Runner、Python ASR 和 Unity 未因 D3 修改。

D4 整链路集成复测已完成：`真实录音 → 本地 ASR → 人工核对文字 → 本地 LLM 候选 → 冻结提案 → 人工确认 → Runner / Unity`。文件输入与真实物理麦克风两条链路均已通过；动作白名单未扩大，模型不能直接执行，也未新增独立页面。详见 [D4 整链路验收](P1-D4-本地语音到控制整链路验收-v1.0.md)。

配置见application-d1-asr.yml，启动见start-d1-java.ps1（默认只检查，需先打包）；需环境变量P0_DB_PASSWORD、P0_ADMIN_PASSWORD、P0_INTEGRATION_TOKEN、P0_PYTHON、P0_RUNNER，以及D1_ASR_TOKEN和D1_ASR_MODEL_REVISION。ASR仅127.0.0.1:18082。网页统一localhost，登录后进入`/?workspace=simulation`，校验现有Cookie及CSRF配置。

V20新增最小受理墓碑，只保存userId、endpoint、requestId、音频指纹和受理/过期时间，不保存音频或文字。内存结果仍按全局1000条、终态30分钟和每分钟清理；墓碑保留7天。Java重启后同键同内容返回409 VOICE_REQUEST_OUTCOME_UNKNOWN，同键不同内容返回409 IDEMPOTENCY_CONFLICT，均禁止再次调用Python；7天到期后才允许作为新请求受理。

启用ASR时Tomcat上传读超时10秒、maxSwallowSize=0；该连接器设置会作用于该Java进程其他上传入口，D1应使用隔离服务。音频入口采用Servlet非阻塞读取且有10秒总期限，防止multipart落盘；Servlet容器不解析该路径的multipart。其他路径保持原过滤。

运行后端测试：mvn.cmd -f backend/pom.xml -Dtest=AsrAcceptanceStoreTests,AsrServiceTests,AudioMultipartTests,LocalAsrProviderTests,AsrHttpTests,AsrEmbeddedTests,VoiceHttpTests test

真实MySQL迁移专项：配置仅指向服务器根地址的VOICE_TEST_MYSQL_URL以及测试账号后，运行mvn.cmd -f backend/pom.xml -Dtest=AsrMysqlMigrationTests test。测试只创建并删除自己随机命名的uav_usv_asr_test_<32位十六进制>数据库。

契约校验：python docs/voice-control-p1/validate_contracts.py

真实服务就绪后：设置D1_TEST_USERNAME/D1_TEST_PASSWORD，再执行python docs/voice-control-p1/verify-d1-http.py --audio <本机授权音频> --mime audio/mpeg --output <本机结果JSON>。Java重启后可增加--request-id <原UUID> --expect-code VOICE_REQUEST_OUTCOME_UNKNOWN验证墓碑。脚本不输出Cookie、密码、原音频和完整文字；仅HTTP及同键重放/重启保护冒烟，不代替浏览器D01–D12。

E1 已建立逐项覆盖基线：31/46 条已有真实或自动化证据，10 条部分覆盖，2 条能力尚未实现，3 条尚未执行；该结果不表示 E1 完成。详见 [E1 覆盖基线](P1-E1-覆盖基线-20260924.md) 与 [E1 验收清单](P1-ASR-验收清单-v1.2.csv)。
