# P1-ASR D1 — nly 本地识别服务

依据：规划 v1.3 最终稿、分工 v1.1 最终稿（2026-09-23）。nly负责前端/Python/Unity兼容；mxy负责Java、部署与测试。此目录不引用或修改算法Runner，不访问业务数据库，不生成任务命令。

## 部署约束

- 首轮目标：mxy i5-10500 / 约16GB，Java、Python、浏览器同机。
- Python：Windows x64 / 3.13.15，独立venv。small / CPU INT8 / 默认4线程；`requirements-lock-win-py313.txt` 为nly已验证环境，mxy需重新安装核验。
- 服务的模型指纹计算使用分块SHA256，可在mxy现有Python 3.9诊断环境运行；这不表示3.9依赖已锁定。正式部署仍以本目录Python 3.13锁文件为准。
- 模型：`Systran/faster-whisper-small` revision `536b0662742c02347bc0e980a01041f333bce120`。4个文件SHA256写在 `asr_server.py` 的 `MODEL_FILES`，启动逐一检查；没有模型时不下载，ready保持503。
- 只监听127.0.0.1；不提供CORS、查询、取消、意图或控制接口。浏览器只访问Java。
- 模型缓存允许落盘；在线音频只使用内存。无multipart临时文件，日志仅ID/状态/耗时/PID/是否提交worker，不记录音频和文字。
- ASR超时不会强行取消计算；单槽直到实际worker结束才释放。D1卡死可由mxy人工停止本服务进程；不要终止Runner。

## 安装（mxy按自己的仓库路径进入目录）

```powershell
Set-Location '<仓库绝对路径>\asr-service'
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-lock-win-py313.txt
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -m unittest -v test_asr_server
```

模型部署时单独下载（首次需要网络，运行服务不需要；不要提交模型到Git）：

```powershell
& .\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; print(snapshot_download('Systran/faster-whisper-small', revision='536b0662742c02347bc0e980a01041f333bce120', allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.txt'], local_dir='models/whisper-small'))"
```

支持将已核对的4个模型文件复制到目标目录，服务启动仍会校验SHA256。离线依赖安装需提前准备相同Windows/Python版本的wheel包，不等同于仅缓存模型。

## 启动

先检查 `Get-NetTCPConnection -LocalPort 18082 -State Listen -ErrorAction SilentlyContinue`，有占用时核对所有者，不盲目结束进程。

```powershell
$env:ASR_MODEL_PATH = (Resolve-Path '.\models\whisper-small').Path
$env:ASR_CPU_THREADS = '4'
$env:ASR_PORT = '18082'
# 从双方约定的私密配置取得同一随机凭据。请勿把值写入仓库、聊天或命令行参数。
$privateToken = Read-Host '输入Java和ASR共用的内部随机凭据（至少32个ASCII字符）' -AsSecureString
$env:ASR_SERVICE_TOKEN = [System.Net.NetworkCredential]::new('', $privateToken).Password
$env:PYTHONIOENCODING = 'utf-8'
& .\.venv\Scripts\python.exe .\asr_server.py
```

前台日志先显示 `ASR_STARTING` 与自己的PID，预热后 `ASR_READY`。配置/模型不正确时 `ASR_NOT_READY`，不输出秘密或内部异常路径。另一终端 `Invoke-RestMethod http://127.0.0.1:18082/health/ready`；就绪 `{"ready":true}`。Java环境变量名称由mxy实现决定，本目录不虚构后端配置键。

## 停止与人工恢复

1. mxy通知所有页面暂停提交。
2. 首选ASR前台终端Ctrl+C；等待该PID退出（仍在推理时不会假装已经停止）。
3. 若卡住，按启动日志PID核实：

```powershell
$asrProcessId = [int](Read-Host 'ASR_STARTING日志中的PID')
$candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $asrProcessId"
$candidate | Select-Object ProcessId,ExecutablePath,CommandLine
# 人工确认：命令行必须指向本目录asr_server.py，不能是algorithm-service/runner.py。
```

确认后仅终止该PID：`Stop-Process -Id $asrProcessId`；再用 `Get-Process -Id $asrProcessId -ErrorAction SilentlyContinue` 验证退出。若PID信息不符立即停止，不使用批量杀Python或按端口杀进程脚本。

4. 重启需要的服务，检查ready；所有页面刷新重新登录、废弃旧请求后恢复。Java重启的跨代次幂等保证不属于D1内存方案。

## 内部接口落地

- `GET /health/ready`：200/503，`{"ready":true/false}`；busy时仍ready。
- `POST /internal/asr/transcriptions`：Bearer内部凭据；`X-ASR-Timeout-Ms` 1..120000；必须有Content-Length，不接受chunked上传；multipart requestId/locale/audio，未知或重复字段拒绝。
- Java内部客户端须发送确定长度请求体（最大6MiB），这不改变浏览器到Java的协议。
- 成功：requestId/text/durationMs/modelRevision四字段，模型revision固定上述版本。Java负责映射公开别名，例如whisper-small-cpu-int8-r1。
- 失败：requestId（尚未解析则null）/code/message。业务错误遵循最终稿。
- 已对齐mxy提交425a96c：非法内部参数HTTP503＋ASR_UNAVAILABLE，鉴权401＋ASR_UNAVAILABLE。Java均按调用配置错误映射503；用户表单错误仍由Java返回400，未新增公共错误。
- busy返回429 ASR_BUSY，Retry-After:2；该值是重试建议，不承诺任务两秒内结束。Java不得自动重试推理POST。
- 实际时长按完整解码并重采样的采样数向上取整；严格拒绝损坏包，不像离线冒烟脚本自动跳过坏包。E1 接受单音频轨、最多2声道、48kHz的 WebM/Opus、Ogg/Opus、MP4/AAC、WAV/PCM 与 MP3；容器、codec 和 MIME 必须一致。
- Python只做本机单槽保护，不持久化业务幂等；Java负责同键恢复、登录、权限、CSRF和缓存。
- 最多8个HTTP处理线程、读头10秒、请求体6MiB、解码960000采样点上限。D1无独立进程内存硬配额；原生解码卡死按人工恢复流程处理，不把线程timeout当成强制终止。

## 前端

仅在前端本地环境或部署环境设置 `VITE_VOICE_ASR_ONLY=true`，重启Vite/重新构建。默认false不变。

- 唯一入口位于算法仿真工作区（`/?workspace=simulation`）右侧“语音控制”面板，不增加独立页面或侧边栏导航。旧`/asr`书签仅重定向到该工作区。
- 进入算法仿真工作区即可录音，不要求生成场景、启动算法实例或等待Unity就绪；原P0手工按钮保持独立。ASR-only隐藏旧意图输入。
- ASR-only始终调用真实Java转写接口，不受旧P1 Mock开关影响、不自动降级Mock。若Java返回test-fixture，显著标明不可用于真实验收。
- 58秒软停止，等待尾帧；140秒总等待；手动原键/Blob恢复最多10分钟；账号变化、卸载、退出清理；不存localStorage/Pinia。
- Chromium录音需要真实麦克风权限及localhost/HTTPS。E1 文件上传支持五种冻结容器，但不能代替浏览器物理麦克风实录验收。

## 真实模型HTTP冒烟

```powershell
& .\.venv\Scripts\python.exe .\smoke_real.py '<授权MP3绝对路径>' --model-path $env:ASR_MODEL_PATH --output '<仓库外私有结果JSON路径>'
```

脚本临时启动自己的ASR子进程，生成随机内部凭据不打印，测试真实MP3、内存转码WebM和静音，结束仅停止自己的子进程。结果包含识别全文，默认不提交Git。

若已知源录音损坏，可明确增加 `--normalize-damaged-source`：原MP3必须返回415；额外验证在内存显式重编码的MP3/WebM。此为诊断测试，不能声称原损坏文件识别成功，也不修改原文件或放宽服务端校验。
