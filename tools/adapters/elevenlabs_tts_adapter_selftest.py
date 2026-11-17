"""Offline self-test for elevenlabs_tts_adapter.py against a local mock of the TTS endpoint."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64


def selftest() -> None:
    adapter = Path(__file__).with_name("elevenlabs_tts_adapter.py")
    seen: list[dict] = []
    replies: list[tuple[int, bytes, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append({"path": self.path, "key": self.headers.get("xi-api-key"),
                         "body": json.loads(self.rfile.read(n))})
            status, body, ctype = replies.pop(0)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("request-id", "req_123")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "ELEVENLABS_API_KEY": "test-key", "ELEVENLABS_ADAPTER_ALLOW_LOCAL_HTTP": "1",
           "ELEVENLABS_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1"}
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        def job(output: str, **parameters) -> dict:
            out = tmp / f"out-{len(list(tmp.iterdir()))}"
            out.mkdir()
            return {"job_id": "T", "modality": "tts", "adapter": "elevenlabs-tts", "prompt": "전쟁 십일 일째.",
                    "outputs": [output], "parameters": parameters, "project_root": str(tmp), "output_root": str(out)}

        def call(j: dict) -> tuple[int, dict, bytes]:
            r = subprocess.run([sys.executable, str(adapter)], input=json.dumps(j).encode(), capture_output=True, env=env)
            return r.returncode, json.loads(r.stdout), r.stdout + r.stderr

        # 1. success: verbatim text, defaults, query, key header
        replies.append((200, MP3, "audio/mpeg"))
        code, out, _ = call(job("剧集/EP003/制作成果/tts/A01.mp3", voice_id="TESTVOICE000000000000"))
        assert code == 0, out
        req = seen[-1]
        assert req["key"] == "test-key"
        assert req["path"] == "/v1/text-to-speech/TESTVOICE000000000000?output_format=mp3_44100_128"
        assert req["body"] == {"text": "전쟁 십일 일째.", "model_id": "eleven_v4",
                               "voice_settings": {"stability": 0.6, "similarity_boost": 0.75}}
        assert Path(out["outputs"][0]["source"]).read_bytes() == MP3 and out["provider_job_id"] == "req_123"

        # 2. overrides
        replies.append((200, MP3, "audio/mpeg"))
        code, out, _ = call(job("a.mp3", voice_id="abc123", stability=0.5, speed=1.1, seed=7))
        assert code == 0 and seen[-1]["body"]["voice_settings"]["stability"] == 0.5
        assert seen[-1]["body"]["voice_settings"]["speed"] == 1.1 and seen[-1]["body"]["seed"] == 7

        # 3. voice not found → configuration error, body not echoed
        replies.append((404, json.dumps({"detail": {"code": "voice_not_found", "message": "secret text"}}).encode(),
                        "application/json"))
        code, out, raw_out = call(job("a.mp3", voice_id="missing1"))
        assert code == 1 and out["error"]["category"] == "configuration" and out["error"]["code"] == "voice_not_found", out
        assert b"secret" not in raw_out

        # 4. validation: no voice, bad format, non-audio body
        code, out, _ = call(job("a.mp3"))
        assert code == 1 and out["error"]["code"] == "invalid_job"
        code, out, _ = call(job("a.ogg", voice_id="abc"))
        assert code == 1 and out["error"]["code"] == "invalid_job"
        replies.append((200, b"<html>not audio</html>", "text/html"))
        code, out, _ = call(job("a.mp3", voice_id="abc"))
        assert code == 1 and out["error"]["category"] == "provider_response", out
    server.shutdown()
    print("elevenlabs tts adapter selftest: 4 scenarios passed")
