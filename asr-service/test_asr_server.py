import concurrent.futures
import http.client
import threading
import time
import unittest
import json
import io

from asr_server import (AsrError, CommandFastPathEngine, MAX_AUDIO, MAX_BODY, REVISION,
                        Runtime, Server, decode_audio, file_sha256, parse_multipart)

TOKEN = 'test-only-not-a-deployment-secret-12345678'
ID = '11111111-1111-4111-8111-111111111111'


def multipart(audio=b'fixture', mime='audio/mpeg', extra=b''):
    body = (b'--boundary\r\nContent-Disposition: form-data; name="requestId"\r\n\r\n' + ID.encode()
            + b'\r\n--boundary\r\nContent-Disposition: form-data; name="locale"\r\n\r\nzh-CN'
            + b'\r\n--boundary\r\nContent-Disposition: form-data; name="audio"; filename="sample"\r\nContent-Type: '
            + mime.encode() + b'\r\n\r\n' + audio + b'\r\n' + extra + b'--boundary--\r\n')
    return body


class FakeEngine:
    revision = REVISION
    def __init__(self):
        self.block = None
        self.error = None
    def transcribe(self, *args):
        if self.block:
            self.block.wait(2)
        if self.error:
            raise self.error
        return '停止任务', 1000


class HashTests(unittest.TestCase):
    def test_streaming_sha256_supports_the_deployment_interpreter(self):
        self.assertEqual(
            file_sha256(io.BytesIO(b'abc')),
            'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')


class CommandFastPathTests(unittest.TestCase):
    class Stub:
        def __init__(self, text):
            self.text = text
            self.calls = 0

        def transcribe(self, *_):
            self.calls += 1
            return self.text, 900

    def engine(self, fast_text):
        engine = CommandFastPathEngine.__new__(CommandFastPathEngine)
        engine.fast = self.Stub(fast_text)
        engine.fallback = self.Stub('fallback')
        return engine

    def test_explicit_start_variants_do_not_fall_back_to_whisper(self):
        for phrase in ('开始任务', '请开始任务', '启动当前任务'):
            engine = self.engine(phrase)
            self.assertEqual(engine.transcribe(b'audio', 'audio/webm', time.monotonic() + 1)[0], phrase)
            self.assertEqual(engine.fallback.calls, 0)

    def test_chinese_ordinal_single_device_commands_do_not_fall_back_to_whisper(self):
        for phrase in ('第一架无人机悬停', '第二架无人机返航', '第一艘无人艇驻留', '第二艘无人艇归队'):
            engine = self.engine(phrase)
            self.assertEqual(engine.transcribe(b'audio', 'audio/webm', time.monotonic() + 1)[0], phrase)
            self.assertEqual(engine.fallback.calls, 0)

    def test_non_exact_text_still_uses_whisper(self):
        engine = self.engine('不要开始任务')
        self.assertEqual(engine.transcribe(b'audio', 'audio/webm', time.monotonic() + 1)[0], 'fallback')
        self.assertEqual(engine.fallback.calls, 1)

    def test_empty_fast_result_uses_existing_whisper_fallback(self):
        engine = self.engine('')
        engine.fast = FakeEngine()
        engine.fast.error = AsrError(422, 'ASR_NO_SPEECH', 'no speech')
        self.assertEqual(engine.transcribe(b'audio', 'audio/webm', time.monotonic() + 1)[0], 'fallback')
        self.assertEqual(engine.fallback.calls, 1)

    def test_invalid_audio_does_not_retry_another_model(self):
        engine = self.engine('')
        engine.fast = FakeEngine()
        engine.fast.error = AsrError(415, 'ASR_AUDIO_FORMAT_UNSUPPORTED', 'invalid')
        with self.assertRaises(AsrError):
            engine.transcribe(b'audio', 'audio/webm', time.monotonic() + 1)
        self.assertEqual(engine.fallback.calls, 0)

    def test_both_models_can_still_reject_silence(self):
        engine = self.engine('')
        engine.fast = FakeEngine()
        engine.fallback = FakeEngine()
        engine.fast.error = engine.fallback.error = AsrError(422, 'ASR_NO_SPEECH', 'no speech')
        with self.assertRaises(AsrError) as caught:
            engine.transcribe(b'audio', 'audio/webm', time.monotonic() + 1)
        self.assertEqual(caught.exception.code, 'ASR_NO_SPEECH')


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.runtime = Runtime(TOKEN, self.engine)
        self.server = Server(0, self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if self.engine.block:
            self.engine.block.set()
        self.server.shutdown()
        self.server.server_close()
        self.runtime.pool.shutdown(wait=True)
        self.thread.join()

    def request(self, body=None, headers=None, method='POST', path='/internal/asr/transcriptions'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        merged = {'Authorization': 'Bearer ' + TOKEN, 'X-ASR-Timeout-Ms': '1000',
                  'Content-Type': 'multipart/form-data; boundary=boundary'}
        merged.update(headers or {})
        conn.request(method, path, body=multipart() if body is None and method == 'POST' else body, headers=merged)
        response = conn.getresponse()
        result = response.status, json.loads(response.read()), dict(response.getheaders())
        conn.close()
        return result

    def test_success_closed_shape(self):
        status, body, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {'requestId', 'text', 'durationMs', 'modelRevision'})
        self.assertEqual(body['requestId'], ID)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.runtime.calls, 1)

    def test_auth_rejected_before_model(self):
        status, body, _ = self.request(headers={'Authorization': 'Bearer wrong'})
        self.assertEqual((status, body['code'], self.runtime.calls), (401, 'ASR_UNAVAILABLE', 0))

    def test_not_ready_and_ready(self):
        self.runtime.engine = None
        self.assertEqual(self.request(method='GET', path='/health/ready')[0], 503)
        self.assertEqual(self.request()[0], 503)
        self.runtime.engine = self.engine
        self.assertEqual(self.request(method='GET', path='/health/ready')[1], {'ready': True})

    def test_invalid_deadline(self):
        for value in ('0', '120001', 'abc', '-1'):
            self.assertEqual(self.request(headers={'X-ASR-Timeout-Ms': value})[0], 503)
        self.assertEqual(self.runtime.calls, 0)

    def test_large_body_rejected_before_read(self):
        self.assertEqual(self.request(headers={'Content-Length': str(MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.runtime.calls, 0)

    def test_duplicate_and_unknown_parts(self):
        for name in ('audio', 'other'):
            extra = ('--boundary\r\nContent-Disposition: form-data; name="' + name + '"\r\n\r\nx\r\n').encode()
            self.assertEqual(self.request(multipart(extra=extra))[0], 503)
        self.assertEqual(self.runtime.calls, 0)

    def test_empty_wrong_locale_and_uuid(self):
        for body in (multipart(b''), multipart().replace(b'zh-CN', b'en-US'), multipart().replace(ID.encode(), b'bad')):
            self.assertEqual(self.request(body)[0], 503)

    def test_supported_and_unsupported_mime(self):
        for mime in ('audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/mpeg'):
            self.assertEqual(self.request(multipart(mime=mime))[0], 200)
        self.assertEqual(self.request(multipart(mime='audio/flac'))[0], 415)

    def test_expired_work_remains_busy_ready_stays_true(self):
        self.engine.block = threading.Event()
        status, body, _ = self.request(headers={'X-ASR-Timeout-Ms': '30'})
        self.assertEqual((status, body['code']), (504, 'ASR_TIMEOUT'))
        self.assertEqual(self.request(method='GET', path='/health/ready')[0], 200)
        status, body, headers = self.request()
        self.assertEqual((status, body['code']), (429, 'ASR_BUSY'))
        self.assertEqual(headers['Retry-After'], '2')
        self.assertEqual(self.runtime.calls, 1)
        self.engine.block.set()
        for _ in range(100):
            if not self.runtime.slot.locked():
                break
            time.sleep(.005)
        self.assertEqual(self.request()[0], 200)

    def test_busy_includes_slow_body_read(self):
        import socket
        sock = socket.create_connection(('127.0.0.1', self.server.server_port))
        sock.sendall((f'POST /internal/asr/transcriptions HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {TOKEN}\r\n'
                      'X-ASR-Timeout-Ms: 150\r\nContent-Type: multipart/form-data; boundary=boundary\r\nContent-Length: 200\r\n\r\nx').encode())
        for _ in range(100):
            if self.runtime.slot.locked():
                break
            time.sleep(.001)
        self.assertEqual(self.request()[0], 429)
        self.assertEqual(self.runtime.calls, 0)
        sock.close()

    def test_known_errors_closed_body(self):
        for status, code in ((422, 'ASR_NO_SPEECH'), (422, 'ASR_TRANSCRIPT_TOO_LONG'), (415, 'ASR_AUDIO_FORMAT_UNSUPPORTED')):
            self.engine.error = AsrError(status, code, '受控错误')
            result, body, _ = self.request()
            self.assertEqual((result, body['code']), (status, code))
            self.assertEqual(set(body), {'requestId', 'code', 'message'})

    def test_unexpected_error_does_not_leak(self):
        self.engine.error = RuntimeError('secret private transcript C:/private/path')
        status, body, _ = self.request()
        self.assertEqual(status, 503)
        self.assertNotIn('secret', json.dumps(body))


def encoded_silence(seconds, format='mp3', codec='libmp3lame', rate=48000, layout='mono'):
    import av
    import numpy as np
    output = io.BytesIO()
    with av.open(output, mode='w', format=format) as container:
        stream = container.add_stream(codec, rate=rate)
        stream.layout = layout
        for start in range(0, int(seconds * rate), 960):
            size = min(960, int(seconds * rate) - start)
            channels = len(av.AudioLayout(layout).channels)
            frame = av.AudioFrame.from_ndarray(
                np.zeros((channels, size), dtype=np.float32), format='fltp', layout=layout)
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


class DecodeTests(unittest.TestCase):
    def test_mp3_actual_duration(self):
        samples, duration = decode_audio(encoded_silence(1), 'audio/mpeg', time.monotonic() + 10)
        self.assertEqual(len(samples), 16000)
        self.assertEqual(duration, 1000)

    def test_webm_opus(self):
        samples, duration = decode_audio(encoded_silence(1, 'webm', 'libopus'), 'audio/webm', time.monotonic() + 10)
        self.assertEqual(duration, 1000)

    def test_e1_five_real_containers(self):
        cases = (
            ('webm', 'libopus', 'audio/webm'),
            ('ogg', 'libopus', 'audio/ogg'),
            ('mp4', 'aac', 'audio/mp4'),
            ('wav', 'pcm_s16le', 'audio/wav'),
            ('mp3', 'libmp3lame', 'audio/mpeg'),
        )
        for container, codec, mime in cases:
            with self.subTest(mime=mime):
                samples, duration = decode_audio(
                    encoded_silence(1, container, codec), mime, time.monotonic() + 10)
                # AAC may add one codec frame of padding; decoded duration remains bounded.
                self.assertGreaterEqual(duration, 1000)
                self.assertLessEqual(duration, 1050)
                self.assertEqual(len(samples), duration * 16)

    def test_abnormal_rate_and_channels_are_bounded_and_service_recovers(self):
        for data in (
            encoded_silence(1, 'wav', 'pcm_s16le', rate=96000),
            encoded_silence(1, 'wav', 'pcm_s16le', layout='2.1'),
        ):
            with self.assertRaises(AsrError) as context:
                decode_audio(data, 'audio/wav', time.monotonic() + 10)
            self.assertEqual(context.exception.code, 'ASR_AUDIO_FORMAT_UNSUPPORTED')
        self.assertEqual(
            decode_audio(encoded_silence(1, 'wav', 'pcm_s16le'), 'audio/wav', time.monotonic() + 10)[1],
            1000)

    def test_fake_mime_and_damage(self):
        for data, mime in ((b'not audio', 'audio/webm'), (encoded_silence(1), 'audio/webm')):
            with self.assertRaises(AsrError) as context:
                decode_audio(data, mime, time.monotonic() + 10)
            self.assertEqual(context.exception.status, 415)

    def test_60_second_boundary(self):
        self.assertEqual(decode_audio(encoded_silence(60), 'audio/mpeg', time.monotonic() + 10)[1], 60000)
        with self.assertRaises(AsrError) as context:
            decode_audio(encoded_silence(60.01), 'audio/mpeg', time.monotonic() + 10)
        self.assertEqual(context.exception.code, 'ASR_AUDIO_TOO_LONG')

    def test_decode_deadline(self):
        with self.assertRaises(AsrError) as context:
            decode_audio(b'x', 'audio/mpeg', time.monotonic() - 1)
        self.assertEqual(context.exception.code, 'ASR_TIMEOUT')

    def test_audio_limit(self):
        with self.assertRaises(AsrError) as context:
            parse_multipart('multipart/form-data; boundary=boundary', multipart(b'x' * (MAX_AUDIO + 1)))
        self.assertEqual(context.exception.code, 'ASR_AUDIO_TOO_LARGE')


if __name__ == '__main__':
    unittest.main(verbosity=2)
