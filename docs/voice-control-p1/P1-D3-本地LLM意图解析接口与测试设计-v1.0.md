# UAV-USV 平台 P1-D3 本地 LLM 意图解析接口与测试设计 v1.0

日期：2026-09-24  
负责人：mxy（后端与测试）  
本地分支：`mxy/p1-llm-20260924`  
前置基线：D1 本地 ASR 与 D2 受限规则解析均已通过

## 1. 目标与边界

D3 用本机免费模型替换 D2 内部规则解析器，同时保持公共接口、候选状态和 P0 人工确认流程不变。LLM 只把中文文字归类为整队 `START`、`PAUSE`、`RESUME`、`STOP` 或拒绝原因，不访问数据库，不调用 Runner，不生成 execution，不接触 Unity。

否定句、显式多动作、危险能力、单设备目标、动作白名单、运行上下文和提案来源仍由 Java 校验。模型输出只能成为候选，用户必须显式生成提案并人工确认。

## 2. 固定运行组件

| 组件 | 固定版本/配置 |
|---|---|
| 推理程序 | llama.cpp Windows x64 CPU `b11147` |
| 推理程序压缩包 SHA256 | `b6776a3bfd46ac027d177782e80260c196ed069698e9a80c5d2c4bf62eb7a219` |
| 模型 | `Qwen/Qwen2.5-1.5B-Instruct-GGUF`，`Q4_K_M` |
| 模型文件 SHA256 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` |
| 监听地址 | `127.0.0.1:18083` |
| CPU 配置 | 4 推理线程、4 批处理线程、2048 上下文、单并发 |
| Java 总等待 | 20 秒，无自动重试 |

0.5B Q4 候选在本机可以运行，但核心中文分类出现明显误判，因此不作为 D3 验收模型。1.5B Q4 在当前 7 个核心模型样例中全部通过。模型、llama.cpp 二进制和凭据只放本机 `.local-tools/d3-llm`，不进入 Git。

## 3. 公共接口保持不变

继续使用：

`POST /api/voice/intelligence/interpretations`

登录、ADMIN 权限、CSRF、三个一致的小写 UUID、正文结构、幂等、运行上下文和响应字段全部沿用 D2。成功候选仍返回 `CANDIDATE`，前端仍需“生成提案 → 人工确认”；解析成功不得自动调用 P0、Runner 或 Unity。

D3 成功响应中的实现标识为：

```json
{
  "provider": "local-llm",
  "model": "qwen2.5-1.5b-instruct-q4_k_m"
}
```

## 4. Java—本地 LLM 内部契约

Java 只允许访问 `http://127.0.0.1:<port>`，拒绝远程主机、用户信息、查询参数、片段和空凭据。调用 OpenAI 兼容的 `POST /v1/chat/completions`，使用本机 Bearer 凭据、`temperature=0`、`stream=false`、最多 20 个输出 token，不自动重试。

内部输出不是公共响应，而是受 JSON Schema 约束的单标签对象：

```json
{"label":"START"}
```

标签仅允许：

- `START`、`PAUSE`、`RESUME`、`STOP`
- `AMBIGUOUS_ACTION`
- `NO_SUPPORTED_ACTION`
- `UNSUPPORTED_CAPABILITY`

Java 使用严格 JSON 解析，拒绝重复字段、尾随内容、未知字段、多个 choice、超长响应和未知标签；随后映射为 D2 公共四状态。LLM 返回的说明文字不会透传给用户，界面文案由 Java 固定生成。

## 5. 错误映射

| 场景 | 公共响应 |
|---|---|
| 模型服务限流 | `429 VOICE_RATE_LIMITED`，`Retry-After: 2` |
| 20 秒超时或调用线程中断 | `504 VOICE_INTERPRETATION_TIMEOUT`，不重试 |
| 服务不可达、配置错误、非 200 | `503 VOICE_PROVIDER_UNAVAILABLE` |
| 响应结构、关联或标签非法 | `502 VOICE_PROVIDER_INVALID_RESPONSE` |

超时后结果视为不确定，但不会自动换 requestId 或再次推理。用户可以使用新的请求重新提交；旧键仍遵循已有幂等规则。

## 6. 测试清单

| ID | 场景 | 预期 |
|---|---|---|
| D3-01 | “请让整个编队进入工作状态” | `CANDIDATE/START` |
| D3-02 | “先暂停一下” | `CANDIDATE/PAUSE` |
| D3-03 | “恢复刚才的任务” | `CANDIDATE/RESUME` |
| D3-04 | “结束当前任务” | `CANDIDATE/STOP` |
| D3-05 | “暂停然后继续” | `NEEDS_CLARIFICATION/AMBIGUOUS_ACTION` |
| D3-06 | 无任务动作 | `NEEDS_CLARIFICATION/NO_SUPPORTED_ACTION` |
| D3-07 | 攻击等危险能力 | `UNSUPPORTED/UNSUPPORTED_CAPABILITY` |
| D3-08 | 单设备目标 | `UNSUPPORTED/UNSUPPORTED_TARGETING` |
| D3-09 | 否定表达 | `NOT_ACTIONABLE/NEGATED_ACTION` |
| D3-10 | 同键重放与内容冲突 | 原结果一致；变更内容返回 409 |
| D3-11 | 非回环地址、空凭据 | 请求前拒绝，503 |
| D3-12 | 模型 429、超时、非法 JSON | 固定错误映射且不重试 |
| D3-13 | 候选生成提案 | 仅 `AWAITING_CONFIRMATION`，无 execution |
| D3-14 | 人工确认 | 继续由 P0 权限、状态和场景门禁决定 |
| D3-15 | 模型停止后既有 P0 手工控制 | 不受影响 |

## 7. 完成判定

D3 后端完成需满足：模型及二进制指纹固定；D3-01 至 D3-12 自动验证通过；真实 HTTP 响应显示 `local-llm` 与固定模型别名；D2 契约、幂等和来源关联回归通过。D3-13 至 D3-15 需要登录浏览器进行一次现场确认，保留候选、proposalId、executionId（若确认执行）及页面结果。

本阶段可先本地提交，不上传 GitHub。需要其他同学联调或准备合并时，再推送该分支。
