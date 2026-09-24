# P1 当前接口实施入口：D1 ASR + D2 意图候选

D1 本地语音识别与 D2 规则解析已验收结束。D3 在不改变公共接口的前提下，将 `POST /api/voice/intelligence/interpretations` 的内部解析器切换为本地 LLM；详细范围和测试见 [P1-D3 本地 LLM 意图解析接口与测试设计 v1.0](P1-D3-本地LLM意图解析接口与测试设计-v1.0.md)。历史供应商无关草案见 [v1.1 历史契约](interface-contract-v1.1-history.md)。

当前两条公共接口为：

- `POST /api/voice/intelligence/transcriptions`：录音或 MP3 转文字，沿用 D1 已验收实现。
- `POST /api/voice/intelligence/interpretations`：文字转为候选意图，只允许 `START`、`PAUSE`、`RESUME`、`STOP`。

D2 第一轮使用后端本地受限规则解析器 `local-rules/rules-v1`，用于冻结 HTTP、安全和 P0 来源关联边界。它不是大模型验收结果。以后替换成本地 LLM 时必须保持本文件定义的请求、响应、错误和人工确认边界，不允许模型直接访问 Runner 或 Unity。

两个接口都要求当前启用的 ADMIN、动态 CSRF、相同的小写 UUID `X-Request-ID`/`Idempotency-Key`/正文 `requestId`，响应使用平台统一包装并返回 `Cache-Control: no-store`。解析成功只产生候选；创建 P0 提案时携带 `X-Voice-Interpretation-ID`，后端校验用户、动作和运行代次后仍进入“冻结提案 → 用户确认 → 执行”。

D1 的 Python ASR 内部接口和部署方式保持不变。D2 本地规则解析在 Java 内执行，不修改算法 Runner、Python ASR 或 Unity。
