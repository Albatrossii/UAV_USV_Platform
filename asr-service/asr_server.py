"""D1 loopback-only, memory-only ASR. No P0/Runner/Unity imports or cloud fallback."""
from __future__ import annotations

import concurrent.futures
from email import policy
from email.parser import BytesParser
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import threading
import time

MAX_AUDIO = 5 * 1024 * 1024
MAX_BODY = 6 * 1024 * 1024
MAX_SAMPLES = 960000
MODEL_REVISION = '536b0662742c02347bc0e980a01041f333bce120'
ZIPFORMER_REVISION = 'sherpa-onnx-1.13.8+zipformer-zh-14m-204ad334e2e683fd295359930cc16fc0432a23ac-int8-greedy-v1'
ZIPFORMER_FILES = {
    'tokens.txt': '8b294db9045d6e5f94647f4c1eec1af4da143a75053c399611444b378ff966ac',
    'encoder-epoch-99-avg-1.int8.onnx': '1c556ea57cec304e55ec4b72e52c1cc098bb01476ed7d90f3de939fe126487b1',
    'decoder-epoch-99-avg-1.int8.onnx': '22f123bb8cba9b38974b3df18a3f45e7081f4985ebb2e075d9f21f618c468bbf',
    'joiner-epoch-99-avg-1.int8.onnx': 'a7cf9d82757bdcf786059454495a9ca95e4bd7347f72473fc08d794475c36169',
}
ASR_ENGINE = os.environ.get('ASR_ENGINE', 'whisper-small')
if ASR_ENGINE not in ('whisper-small', 'zipformer-streaming', 'command-fastpath'):
    raise RuntimeError('ASR_ENGINE must be whisper-small, zipformer-streaming, or command-fastpath')
BEAM_SIZE = int(os.environ.get('ASR_BEAM_SIZE', '2'))
if BEAM_SIZE not in (1, 2, 3, 5):
    raise RuntimeError('ASR_BEAM_SIZE must be one of 1, 2, 3, or 5')
DECODING_PROFILE_REVISION = ('uav-usv-domain-prompt-v1' if BEAM_SIZE == 5
                             else f'uav-usv-domain-prompt-v1-beam-{BEAM_SIZE}')
REVISION = f'{MODEL_REVISION}+{DECODING_PROFILE_REVISION}'
MODEL_SHA256 = '3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671'
MODEL_FILES = {
    'model.bin': MODEL_SHA256,
    'config.json': 'b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828',
    'tokenizer.json': 'fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab',
    'vocabulary.txt': '34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913',
}
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
DOMAIN_INITIAL_PROMPT = (
    '无人机，无人艇，协同搜索，协同航迹，改进蛙跳算法，粒子群优化，全局最优解，'
    '运行时引用，运行代次，展示绑定，编队控制，动态避障，航向角速度，经纬度，遥测，'
    '场景就绪，帧应用回执，幂等请求，命令序列，WebGL，实时因子，峰值内存，九十五分位延迟，'
    '第一架无人机悬停，第一架无人机返航，第一架无人机归队，第一艘无人艇驻留，第一艘无人艇返航'
)
PRODUCTION_TRANSCRIBE_OPTIONS = {
    'language': 'zh',
    'beam_size': BEAM_SIZE,
    'vad_filter': True,
    'condition_on_previous_text': False,
    'initial_prompt': DOMAIN_INITIAL_PROMPT,
}


def file_sha256(file_object):
    digest = hashlib.sha256()
    for chunk in iter(lambda: file_object.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


class AsrError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def invalid():
    # Frozen contract has no ASR_INVALID_REQUEST: caller/config failure, Java maps to 503.
    return AsrError(503, 'ASR_UNAVAILABLE', '内部请求参数无效')


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise AsrError(504, 'ASR_TIMEOUT', '本地识别处理超时')


def parse_multipart(content_type, body):
    if len(body) > MAX_BODY:
        raise AsrError(413, 'ASR_AUDIO_TOO_LARGE', '请求超过大小限制')
    if len(content_type) > 512 or '\r' in content_type or '\n' in content_type:
        raise invalid()
    message = BytesParser(policy=policy.default).parsebytes(
        b'Content-Type: ' + content_type.encode('ascii') + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
    if message.get_content_type() != 'multipart/form-data' or not message.is_multipart() or message.defects:
        raise invalid()
    fields = {}
    mime = None
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        if name not in ('requestId', 'locale', 'audio') or name in fields or part.is_multipart() or part.defects:
            raise invalid()
        if part.get_content_disposition() != 'form-data' or part.get('Content-Transfer-Encoding'):
            raise invalid()
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise invalid()
        if name == 'audio':
            if not payload:
                raise invalid()
            if len(payload) > MAX_AUDIO:
                raise AsrError(413, 'ASR_AUDIO_TOO_LARGE', '音频超过5 MiB')
            mime = part.get_content_type()
            if mime not in ('audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/mpeg'):
                raise AsrError(415, 'ASR_AUDIO_FORMAT_UNSUPPORTED', '音频格式不支持')
            codec = part.get_param('codecs')
            expected_codec = {
                'audio/webm': 'opus', 'audio/ogg': 'opus', 'audio/mp4': 'aac',
                'audio/wav': 'pcm', 'audio/mpeg': 'mp3',
            }[mime]
            if codec and codec.lower() != expected_codec:
                raise AsrError(415, 'ASR_AUDIO_FORMAT_UNSUPPORTED', '音频codec不支持')
            fields[name] = payload
        else:
            if len(payload) > 64 or part.get_filename() is not None:
                raise invalid()
            fields[name] = payload.decode('utf-8', errors='strict')
    if set(fields) != {'requestId', 'locale', 'audio'} or fields['locale'] != 'zh-CN' or not UUID.fullmatch(fields['requestId']):
        raise invalid()
    return fields['requestId'], fields['audio'], mime


def decode_audio(audio, mime, deadline):
    """Bound encoded bytes, streams, channels, rates and decoded samples before inference."""
    import av
    import numpy as np
    if not audio or len(audio) > MAX_AUDIO:
        raise AsrError(413, 'ASR_AUDIO_TOO_LARGE', '音频大小不合法')
    check_deadline(deadline)
    try:
        with av.open(io.BytesIO(audio), mode='r') as container:
            streams = list(container.streams)
            if len(streams) != 1 or streams[0].type != 'audio':
                raise ValueError('stream')
            stream = streams[0]
            fmt, codec = container.format.name, stream.codec_context.name
            valid_format = (
                (mime == 'audio/mpeg' and fmt == 'mp3' and codec in ('mp3', 'mp3float'))
                or (mime == 'audio/webm' and 'webm' in fmt and codec == 'opus'
                    and b'webm' in audio[:4096])
                or (mime == 'audio/ogg' and 'ogg' in fmt and codec == 'opus'
                    and audio.startswith(b'OggS'))
                or (mime == 'audio/mp4' and 'mp4' in fmt and codec == 'aac'
                    and b'ftyp' in audio[:64])
                or (mime == 'audio/wav' and fmt == 'wav' and codec.startswith('pcm_')
                    and audio.startswith(b'RIFF') and audio[8:12] == b'WAVE')
            )
            if not valid_format:
                raise ValueError('format')
            if not 1 <= stream.codec_context.channels <= 2 or not 1 <= stream.codec_context.sample_rate <= 48000:
                raise ValueError('specification')
            converter = av.AudioResampler(format='fltp', layout='mono', rate=16000)
            parts, count = [], 0
            for frame in container.decode(stream):
                check_deadline(deadline)
                if frame.sample_rate > 48000 or len(frame.layout.channels) > 2:
                    raise ValueError('frame specification')
                for converted in converter.resample(frame):
                    count += converted.samples
                    if count > MAX_SAMPLES:
                        raise AsrError(413, 'ASR_AUDIO_TOO_LONG', '音频实际时长超过60秒')
                    parts.append(converted.to_ndarray().reshape(-1))
            for converted in converter.resample(None):
                count += converted.samples
                if count > MAX_SAMPLES:
                    raise AsrError(413, 'ASR_AUDIO_TOO_LONG', '音频实际时长超过60秒')
                parts.append(converted.to_ndarray().reshape(-1))
            if not count:
                raise ValueError('empty')
            check_deadline(deadline)
            return np.concatenate(parts).astype(np.float32, copy=False), math.ceil(count * 1000 / 16000)
    except AsrError:
        raise
    except Exception:
        raise AsrError(415, 'ASR_AUDIO_FORMAT_UNSUPPORTED', '音频损坏或实际格式/规格不支持') from None


class LocalEngine:
    revision = REVISION

    def __init__(self, model_path, threads):
        # Only explicitly provisioned files: no Hub lookup/download at service runtime.
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        from faster_whisper import WhisperModel
        import numpy as np
        model_path = Path(model_path).resolve(strict=True)
        for name, expected in MODEL_FILES.items():
            with (model_path / name).open('rb') as model_file:
                if file_sha256(model_file) != expected:
                    raise ValueError('model checksum mismatch')
        self.model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=threads,
                                  num_workers=1, local_files_only=True)
        # Force the decoder to execute, not merely a VAD-only no-op.
        segments, _ = self.model.transcribe(np.zeros(16000, dtype=np.float32), language='zh',
                                            beam_size=1, vad_filter=False)
        list(segments)

    def transcribe(self, audio, mime, deadline):
        decode_started = time.monotonic()
        try:
            samples, duration = decode_audio(audio, mime, deadline)
        except Exception:
            print(json.dumps({'event': 'ASR_STAGE', 'stage': 'decode', 'status': 'failed',
                              'elapsedMs': round((time.monotonic() - decode_started) * 1000),
                              'pid': os.getpid()}), flush=True)
            raise
        print(json.dumps({'event': 'ASR_STAGE', 'stage': 'decode', 'status': 'ok',
                          'elapsedMs': round((time.monotonic() - decode_started) * 1000),
                          'audioDurationMs': duration, 'audioBytes': len(audio),
                          'pid': os.getpid()}), flush=True)
        check_deadline(deadline)
        inference_started = time.monotonic()
        try:
            segments, _ = self.model.transcribe(samples, **PRODUCTION_TRANSCRIBE_OPTIONS)
            pieces, length = [], 0
            for segment in segments:
                check_deadline(deadline)
                pieces.append(segment.text)
                length += len(segment.text)
                if length > 500:
                    raise AsrError(422, 'ASR_TRANSCRIPT_TOO_LONG', '识别文字超过500字符')
            check_deadline(deadline)
        except Exception:
            print(json.dumps({'event': 'ASR_STAGE', 'stage': 'inference', 'status': 'failed',
                              'elapsedMs': round((time.monotonic() - inference_started) * 1000),
                              'beamSize': BEAM_SIZE, 'pid': os.getpid()}), flush=True)
            raise
        print(json.dumps({'event': 'ASR_STAGE', 'stage': 'inference', 'status': 'ok',
                          'elapsedMs': round((time.monotonic() - inference_started) * 1000),
                          'beamSize': BEAM_SIZE, 'pid': os.getpid()}), flush=True)
        text = ''.join(pieces).strip()
        if not text:
            raise AsrError(422, 'ASR_NO_SPEECH', '未检测到可识别语音')
        return text, duration


class ZipformerStreamingEngine:
    revision = ZIPFORMER_REVISION

    def __init__(self, model_path, threads):
        import numpy as np
        import sherpa_onnx

        model_path = Path(model_path).resolve(strict=True)
        for name, expected in ZIPFORMER_FILES.items():
            with (model_path / name).open('rb') as model_file:
                if file_sha256(model_file) != expected:
                    raise ValueError('streaming model checksum mismatch')
        self.np = np
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(model_path / 'tokens.txt'),
            encoder=str(model_path / 'encoder-epoch-99-avg-1.int8.onnx'),
            decoder=str(model_path / 'decoder-epoch-99-avg-1.int8.onnx'),
            joiner=str(model_path / 'joiner-epoch-99-avg-1.int8.onnx'),
            num_threads=threads,
            sample_rate=16000,
            feature_dim=80,
            decoding_method='greedy_search',
            enable_endpoint_detection=False,
            provider='cpu',
        )

    def transcribe(self, audio, mime, deadline):
        decode_started = time.monotonic()
        try:
            samples, duration = decode_audio(audio, mime, deadline)
        except Exception:
            print(json.dumps({'event': 'ASR_STAGE', 'stage': 'decode', 'status': 'failed',
                              'elapsedMs': round((time.monotonic() - decode_started) * 1000),
                              'pid': os.getpid()}), flush=True)
            raise
        print(json.dumps({'event': 'ASR_STAGE', 'stage': 'decode', 'status': 'ok',
                          'elapsedMs': round((time.monotonic() - decode_started) * 1000),
                          'audioDurationMs': duration, 'audioBytes': len(audio),
                          'pid': os.getpid()}), flush=True)
        check_deadline(deadline)
        inference_started = time.monotonic()
        try:
            stream = self.recognizer.create_stream()
            stream.accept_waveform(16000, self.np.asarray(samples, dtype=self.np.float32))
            stream.accept_waveform(16000, self.np.zeros(8000, dtype=self.np.float32))
            stream.input_finished()
            while self.recognizer.is_ready(stream):
                check_deadline(deadline)
                self.recognizer.decode_stream(stream)
            check_deadline(deadline)
            text = self.recognizer.get_result_all(stream).text.strip()
            if len(text) > 500:
                raise AsrError(422, 'ASR_TRANSCRIPT_TOO_LONG', '识别文字超过500字符')
        except Exception:
            print(json.dumps({'event': 'ASR_STAGE', 'stage': 'inference', 'status': 'failed',
                              'elapsedMs': round((time.monotonic() - inference_started) * 1000),
                              'engine': self.revision, 'pid': os.getpid()}), flush=True)
            raise
        print(json.dumps({'event': 'ASR_STAGE', 'stage': 'inference', 'status': 'ok',
                          'elapsedMs': round((time.monotonic() - inference_started) * 1000),
                          'engine': self.revision, 'pid': os.getpid()}), flush=True)
        if not text:
            raise AsrError(422, 'ASR_NO_SPEECH', '未检测到可识别语音')
        return text, duration


def _single_device_fast_commands():
    ordinals = ('一', '二', '三', '四', '五', '六', '七', '八', '九', '十')
    commands = set()
    for index, ordinal in enumerate(ordinals, start=1):
        uav_targets = (f'第{ordinal}架无人机', f'{ordinal}号无人机', f'第{index}架无人机', f'{index}号无人机')
        usv_targets = (f'第{ordinal}艘无人艇', f'{ordinal}号无人艇', f'第{index}艘无人艇', f'{index}号无人艇')
        for target in uav_targets:
            commands.update(f'{target}{action}' for action in ('悬停', '暂停', '返航', '返回', '归队', '继续', '停止', '降落'))
        for target in usv_targets:
            commands.update(f'{target}{action}' for action in ('驻留', '待命', '保持', '暂停', '返航', '返回', '归队', '继续', '停止', '停船'))
    return frozenset(commands)


class CommandFastPathEngine:
    """Use the small streaming model only for exact task phrases; defer all else to Whisper."""
    revision = f'{ZIPFORMER_REVISION}+exact-command-v1+whisper-small-{DECODING_PROFILE_REVISION}'
    fast_commands = frozenset({
        '开始任务', '开始执行任务', '执行任务', '请开始任务', '请开始执行任务',
        '现在开始任务', '开始当前任务', '启动任务', '启动当前任务',
        '暂停任务', '暂停一下', '暂停当前任务', '暂停一下当前任务', '请暂停任务',
        '继续任务', '继续执行任务', '恢复任务', '恢复运行', '恢复执行', '请继续任务',
        '停止任务', '停止执行任务', '结束任务', '停止当前任务', '停止当前运行任务',
        '结束当前任务', '终止任务', '终止当前任务', '请停止任务', '请停止当前任务',
        '立即停止任务', '马上停止任务', '请立即停止任务',
    }) | _single_device_fast_commands()

    def __init__(self, whisper_path, zipformer_path, threads):
        self.fast = ZipformerStreamingEngine(zipformer_path, threads)
        self.fallback = LocalEngine(whisper_path, threads)

    def transcribe(self, audio, mime, deadline):
        text, duration = self.fast.transcribe(audio, mime, deadline)
        normalized = re.sub(r'[，。！？；,.!?…]+$', '', text.strip())
        if normalized in self.fast_commands:
            print(json.dumps({'event': 'ASR_FAST_COMMAND_ACCEPTED', 'pid': os.getpid()}), flush=True)
            return text, duration
        print(json.dumps({'event': 'ASR_FAST_PATH_FALLBACK', 'reason': 'not-exact-command',
                          'pid': os.getpid()}), flush=True)
        return self.fallback.transcribe(audio, mime, deadline)


class Runtime:
    def __init__(self, token, engine=None, audit=False):
        self.token = token
        self.engine = engine
        self.slot = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='asr-only')
        self.calls = 0
        self.audit = audit

    def recognize(self, audio, mime, deadline):
        self.calls += 1
        return self.engine.transcribe(audio, mime, deadline)


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalASR'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass  # No request lines, credentials, filenames or transcription text in logs.

    def reply(self, status, data, retry_after=None):
        self.response_status = status
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.close_connection = True
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            if retry_after is not None:
                self.send_header('Retry-After', str(retry_after))
            self.end_headers()
            self.wfile.write(body)
        except (OSError, ConnectionError):
            pass

    def discard_request_body(self):
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,10}', lengths[0]):
            return
        remaining = min(int(lengths[0]), MAX_BODY + 1) - getattr(self, 'body_read', 0)
        if remaining <= 0:
            return
        try:
            self.connection.settimeout(.05)
            while remaining > 0:
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self.body_read += len(chunk)
        except (OSError, ConnectionError, TimeoutError, socket.timeout):
            pass

    def do_GET(self):
        if self.path != '/health/ready':
            self.reply(404, {'requestId': None, 'code': 'ASR_UNAVAILABLE', 'message': '接口不存在'})
            return
        ready = self.server.runtime.engine is not None
        self.reply(200 if ready else 503, {'ready': ready})

    def do_POST(self):
        received = time.monotonic()
        runtime = self.server.runtime
        request_id = None
        acquired = False
        future = None
        self.body_read = 0
        try:
            if self.path != '/internal/asr/transcriptions':
                raise AsrError(404, 'ASR_UNAVAILABLE', '接口不存在')
            auth = self.headers.get_all('Authorization', [])
            if len(auth) != 1 or not hmac.compare_digest(auth[0].encode(), ('Bearer ' + runtime.token).encode()):
                raise AsrError(401, 'ASR_UNAVAILABLE', '内部鉴权失败')
            timeout = self.headers.get_all('X-ASR-Timeout-Ms', [])
            lengths = self.headers.get_all('Content-Length', [])
            if (len(timeout) != 1 or not re.fullmatch(r'[0-9]{1,6}', timeout[0])
                    or not 1 <= int(timeout[0]) <= 120000 or len(lengths) != 1
                    or not re.fullmatch(r'[0-9]{1,10}', lengths[0]) or self.headers.get('Transfer-Encoding')
                    or len(self.headers.get_all('Content-Type', [])) != 1):
                raise invalid()
            deadline = received + int(timeout[0]) / 1000
            size = int(lengths[0])
            if size > MAX_BODY:
                raise AsrError(413, 'ASR_AUDIO_TOO_LARGE', '请求超过6 MiB')
            if size == 0:
                raise invalid()
            if runtime.engine is None:
                raise AsrError(503, 'ASR_UNAVAILABLE', '模型尚未就绪')
            acquired = runtime.slot.acquire(blocking=False)
            if not acquired:
                raise AsrError(429, 'ASR_BUSY', '本地识别忙，请稍后重试')
            body = bytearray()
            while len(body) < size:
                check_deadline(deadline)
                self.connection.settimeout(max(.001, deadline - time.monotonic()))
                chunk = self.rfile.read1(min(65536, size - len(body)))
                if not chunk:
                    raise invalid()
                body.extend(chunk)
                self.body_read += len(chunk)
            check_deadline(deadline)
            request_id, audio, mime = parse_multipart(self.headers['Content-Type'], body)
            del body
            check_deadline(deadline)
            future = runtime.pool.submit(runtime.recognize, audio, mime, deadline)
            # Transfer the slot to actual work completion, including cancellation/disconnect.
            future.add_done_callback(lambda _: runtime.slot.release())
            acquired = False
            try:
                text, duration = future.result(timeout=max(0, deadline - time.monotonic()))
            except concurrent.futures.TimeoutError:
                raise AsrError(504, 'ASR_TIMEOUT', '本地识别处理超时') from None
            check_deadline(deadline)
            self.reply(200, {'requestId': request_id, 'text': text, 'durationMs': duration,
                             'modelRevision': runtime.engine.revision})
        except AsrError as error:
            self.discard_request_body()
            self.reply(error.status, {'requestId': request_id, 'code': error.code, 'message': error.message},
                       2 if error.code == 'ASR_BUSY' else None)
        except (TimeoutError, socket.timeout):
            self.discard_request_body()
            self.reply(504, {'requestId': request_id, 'code': 'ASR_TIMEOUT', 'message': '读取音频超时'})
        except (ValueError, UnicodeError):
            self.discard_request_body()
            self.reply(503, {'requestId': request_id, 'code': 'ASR_UNAVAILABLE', 'message': '内部请求参数无效'})
        except Exception:
            self.discard_request_body()
            self.reply(503, {'requestId': request_id, 'code': 'ASR_UNAVAILABLE', 'message': '本地识别异常，请检查服务'})
        finally:
            if acquired:
                runtime.slot.release()
            if runtime.audit:
                print(json.dumps({'event': 'ASR_REQUEST', 'requestId': request_id,
                                  'status': getattr(self, 'response_status', 503),
                                  'submittedToWorker': future is not None,
                                  'elapsedMs': round((time.monotonic() - received) * 1000),
                                  'busy': runtime.slot.locked(), 'pid': os.getpid()}), flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8
    allow_reuse_address = False

    def __init__(self, port, runtime):
        self.runtime = runtime
        self.connections = threading.BoundedSemaphore(8)
        super().__init__(('127.0.0.1', port), Handler)

    def process_request(self, request, address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.connections.release()

    def handle_error(self, *_):
        pass


def main():
    token = os.environ.get('ASR_SERVICE_TOKEN', '')
    whisper_path = os.environ.get('ASR_MODEL_PATH', '')
    zipformer_path = os.environ.get('ASR_ZIPFORMER_MODEL_PATH', '')
    if (len(token) < 32 or not token.isascii()
            or (ASR_ENGINE == 'whisper-small' and not whisper_path)
            or (ASR_ENGINE == 'zipformer-streaming' and not zipformer_path)
            or (ASR_ENGINE == 'command-fastpath' and (not whisper_path or not zipformer_path))):
        raise SystemExit('Set the selected local model paths and ASR_SERVICE_TOKEN; no secrets are printed.')
    threads = int(os.environ.get('ASR_CPU_THREADS', '6'))
    if not 1 <= threads <= 16:
        raise SystemExit('ASR_CPU_THREADS must be 1..16')
    runtime = Runtime(token, audit=True)
    server = Server(int(os.environ.get('ASR_PORT', '18082')), runtime)
    def initialize():
        try:
            if ASR_ENGINE == 'zipformer-streaming':
                runtime.engine = ZipformerStreamingEngine(zipformer_path, threads)
            elif ASR_ENGINE == 'command-fastpath':
                runtime.engine = CommandFastPathEngine(whisper_path, zipformer_path, threads)
            else:
                runtime.engine = LocalEngine(whisper_path, threads)
            ready = {'event': 'ASR_READY', 'pid': os.getpid(), 'engine': ASR_ENGINE,
                     'modelRevision': runtime.engine.revision, 'threads': threads}
            if ASR_ENGINE == 'whisper-small':
                ready['decodingProfileRevision'] = DECODING_PROFILE_REVISION
                ready['beamSize'] = BEAM_SIZE
            elif ASR_ENGINE == 'command-fastpath':
                ready['fallbackBeamSize'] = BEAM_SIZE
            print(json.dumps(ready), flush=True)
        except Exception:
            print(json.dumps({'event': 'ASR_NOT_READY', 'pid': os.getpid()}), flush=True)
    threading.Thread(target=initialize, daemon=True).start()
    print(json.dumps({'event': 'ASR_STARTING', 'pid': os.getpid(), 'port': server.server_port,
                      'engine': ASR_ENGINE}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Do not pretend inference was cancelled; an unfinished worker keeps this PID alive.
        runtime.pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()
