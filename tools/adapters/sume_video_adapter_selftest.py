"""Offline self-test for sume_video_adapter.py against a local mock of the sume API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def selftest() -> None:
    adapter = Path(__file__).with_name("sume_video_adapter.py")
    state = {"uploads": 0, "polls": 0, "generate": [], "mode": "ok"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, payload, ctype="application/json"):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n)
            assert self.headers.get("Authorization") == "Bearer test-key"
            if self.path.startswith("/v1/files/upload"):
                state["uploads"] += 1
                return self._send(201, {"data": {"public_url": f"https://media.example/a{state['uploads']}.png"}})
            if self.path == "/v1/video-router/generate":
                state["generate"].append({"body": json.loads(body), "idem": self.headers.get("Idempotency-Key")})
                if state["mode"] == "bad":
                    return self._send(400, {"error": {"code": "invalid_request", "category": "validation", "message": "secret"}})
                return self._send(202, {"data": {"request_id": "job_1", "job": {"id": "job_1", "status": "queued"}}})
            self._send(404, {})

        def do_GET(self):
            if self.path.startswith("/v1/jobs/"):
                state["polls"] += 1
                if state["mode"] == "policy":
                    return self._send(200, {"data": {"job": {"id": "job_1", "status": "failed", "error": {
                        "code": "content_policy_rejected", "category": "generation_rejected", "message": "secret"}}}})
                if state["polls"] < 2:
                    return self._send(200, {"data": {"job": {"id": "job_1", "status": "processing"}}})
                port = self.server.server_port
                return self._send(200, {"data": {"job": {"id": "job_1", "status": "completed", "result": {"artifacts": [
                    {"type": "image", "url": "x"}, {"type": "video", "url": f"http://127.0.0.1:{port}/media/v.mp4"}]}}}})
            if self.path == "/media/v.mp4":
                return self._send(200, MP4, "video/mp4")
            self._send(404, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "SUME_API_KEY": "test-key", "SUME_ADAPTER_ALLOW_LOCAL_HTTP": "1",
           "SUME_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "SUME_POLL_SECONDS": "0.01"}
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        proj = tmp / "p"; (proj / "k").mkdir(parents=True)
        (proj / "k/start.png").write_bytes(PNG); (proj / "k/end.png").write_bytes(PNG)

        def job(refs, **params):
            out = tmp / f"o{len(list(tmp.iterdir()))}"; out.mkdir()
            b = [{"slot_id": f"REF-{i}", "order": i, "path": p, "label": "起始帧", "role": r,
                  "may_control": ["构图"], "must_not_control": ["动作"]} for i, (p, r) in enumerate(refs, 1)]
            return {"job_id": "EP003-MOTION001", "run_id": "run_abc", "modality": "video", "adapter": "sume-video",
                    "prompt": "Animate this start frame.", "reference_bindings": b, "references": [p for p, _ in refs],
                    "outputs": ["剧集/EP003/制作成果/video/MOTION-EP003-001_t01.mp4"], "parameters": {"prompt_language": "en", **params},
                    "project_root": str(proj), "output_root": str(out), "handle_path": str(out / "h.json")}

        def call(j):
            r = subprocess.run([sys.executable, str(adapter)], input=json.dumps(j).encode(), capture_output=True, env=env)
            return r.returncode, json.loads(r.stdout), r.stdout + r.stderr

        # 1. K3 hold: one upload, same url for both frames, defaults, idempotency from run id, download
        code, out, _ = call(job([("k/start.png", "first_frame")], duration=3, hold_last_frame=True))
        assert code == 0, out
        g = state["generate"][-1]
        assert g["body"]["image_url"] == g["body"]["end_image_url"] and state["uploads"] == 1
        assert g["body"]["model"] == "gemini-omni-flash-1.2" and g["body"]["resolution"] == "360p"
        assert g["body"]["aspect_ratio"] == "9:16" and g["body"]["generate_audio"] is True and g["idem"] == "run_abc-generate"
        assert Path(out["outputs"][0]["source"]).read_bytes() == MP4 and out["provider_job_id"] == "job_1"

        # 2. start + end frames
        state["polls"] = 0
        code, out, _ = call(job([("k/start.png", "first_frame"), ("k/end.png", "last_frame")], duration=6))
        g = state["generate"][-1]["body"]
        assert code == 0 and g["image_url"] != g["end_image_url"], out

        # 3. content policy → whitelisted error, no echo
        state["mode"] = "policy"
        code, out, raw_out = call(job([("k/start.png", "first_frame")], duration=3))
        assert code == 1 and out["error"]["code"] == "content_policy_rejected" and b"secret" not in raw_out, out

        # 4. validation (no request sent)
        state["mode"] = "ok"; before = len(state["generate"])
        for params in ({}, {"duration": 2}, {"duration": 12, "hold_last_frame": True}, {"duration": 4, "resolution": "480p"}):
            code, out, _ = call(job([("k/start.png", "first_frame")], **params))
            assert code == 1 and out["error"]["code"] == "invalid_job", (params, out)
        code, out, _ = call(job([("k/end.png", "last_frame")], duration=4))
        assert code == 1 and len(state["generate"]) == before

        # 5. provider 400 → invalid_request, body not echoed
        state["mode"] = "bad"
        code, out, raw_out = call(job([("k/start.png", "first_frame")], duration=3))
        assert code == 1 and out["error"]["category"] == "invalid_request" and b"secret" not in raw_out, out
    server.shutdown()
    print("sume video adapter selftest: 5 scenarios passed")
