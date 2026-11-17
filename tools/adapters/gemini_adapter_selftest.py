"""Offline self-test for gemini_adapter.py against a local mock of the Gemini Interactions API.

Run:  python3 <TOOLS>/gemini_adapter.py --selftest
No network, no key: a fake key and a 127.0.0.1 HTTP server stand in for Google.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class Mock:
    """Scripted responses keyed by (method, path prefix); records every request."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.script: dict[tuple[str, str], list[tuple[int, object]]] = {}

    def on(self, method: str, prefix: str, *responses: tuple[int, object]) -> None:
        self.script[(method, prefix)] = list(responses)

    def take(self, method: str, path: str) -> tuple[int, object]:
        for (m, prefix), queue in self.script.items():
            if m == method and path.startswith(prefix) and queue:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return 404, {"error": {"code": "not_mocked"}}


def _server(mock: Mock) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _handle(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            mock.requests.append({"method": method, "path": self.path, "body": body,
                                  "key": self.headers.get("x-goog-api-key"),
                                  "revision": self.headers.get("Api-Revision")})
            status, payload = mock.take(method, self.path)
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream" if isinstance(payload, bytes) else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _binding(order: int, path: str, role: str) -> dict:
    return {"slot_id": f"REF-{order}", "order": order, "path": path, "label": f"참조{order}",
            "role": role, "may_control": ["identity"], "must_not_control": ["camera"]}


def _job(tmp: Path, modality: str, refs: list[tuple[str, bytes, str]], output: str, **parameters) -> dict:
    project = tmp / "project"
    out = tmp / f"out-{len(list(tmp.iterdir()))}"
    out.mkdir()
    bindings, paths = [], []
    for index, (name, content, role) in enumerate(refs, 1):
        path = project / "设定集" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        rel = str(path.relative_to(project))
        paths.append(rel)
        bindings.append(_binding(index, rel, role))
    project.mkdir(exist_ok=True)
    return {"schema_version": "1.0", "job_id": "EP003-T", "modality": modality, "adapter": f"gemini-{modality}",
            "prompt": "A faceless matte-black figure lights a candle.", "reference_bindings": bindings,
            "references": paths, "outputs": [output],
            "parameters": {"prompt_language": "en", **parameters},
            "project_root": str(project), "output_root": str(out), "handle_path": str(tmp / "handle.json")}


def _expect_error(fn, *args, contains: str = "") -> None:
    try:
        fn(*args)
    except ValueError as exc:
        assert contains in str(exc), (contains, str(exc))
        return
    raise AssertionError("expected ValueError")


def selftest() -> None:
    import gemini_adapter as ga

    mock = Mock()
    server = _server(mock)
    os.environ.update({"GEMINI_API_KEY": "test-key", "GEMINI_ADAPTER_ALLOW_LOCAL_HTTP": "1",
                       "GEMINI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1beta",
                       "GEMINI_POLL_SECONDS": "0.01", "GEMINI_POLL_TIMEOUT": "5"})
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        # 1. image: two references, inline image result
        mock.on("POST", "/v1beta/interactions", (200, {"id": "v1_img1", "status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "image", "mime_type": "image/png",
                                                  "data": base64.b64encode(PNG).decode()}]}]}))
        job = _job(tmp, "image", [("c1.png", PNG, "reference_image"), ("set.jpg", JPG, "reference_image")],
                   "剧集/EP003/制作成果/images/SHOT-EP003-001.png", aspect_ratio="9:16", image_size="2k")
        path, pid = ga.run_image(job)
        sent = mock.requests[-1]
        assert sent["key"] == "test-key" and sent["revision"] == "2026-05-20"
        body = sent["body"]
        assert body["model"] == "gemini-nano-banana-2.1"
        assert [i["type"] for i in body["input"]] == ["image", "image", "text"]
        assert body["input"][1]["mime_type"] == "image/jpeg"
        assert "Reference Image 2 (참조2)" in body["input"][2]["text"]
        assert body["response_format"] == {"type": "image", "mime_type": "image/png",
                                           "aspect_ratio": "9:16", "image_size": "2K"}
        assert path.read_bytes() == PNG and pid == "v1_img1"

        # 2. image: model returned JPEG for a PNG target -> safe failure
        mock.on("POST", "/v1beta/interactions", (200, {"id": "v1_img2", "status": "completed",
                "output_image": {"data": base64.b64encode(JPG).decode()}}))
        job = _job(tmp, "image", [], "剧集/EP003/制作成果/images/x.png")
        try:
            ga.run_image(job)
            raise AssertionError("expected media-type failure")
        except ga.AdapterFailure as exc:
            assert exc.code == "unexpected_media_type"

        # 3. video: first+last frame, background accepted, poll, URI delivery, file poll, download
        mock.requests.clear()
        mock.on("POST", "/v1beta/interactions", (200, {"id": "v1_vid1", "status": "in_progress"}))
        mock.on("GET", "/v1beta/interactions/v1_vid1",
                (200, {"id": "v1_vid1", "status": "in_progress"}),
                (200, {"id": "v1_vid1", "status": "completed", "steps": [
                    {"type": "model_output", "content": [{"type": "video", "uri": "files/abc-1"}]}]}))
        mock.on("GET", "/v1beta/files/abc-1:download", (200, MP4))
        mock.on("GET", "/v1beta/files/abc-1", (200, {"state": "PROCESSING"}), (200, {"state": "ACTIVE"}))
        job = _job(tmp, "video", [("ref.png", PNG, "reference_image"), ("end.jpg", JPG, "last_frame"),
                                  ("start.png", PNG, "first_frame")],
                   "剧集/EP003/制作成果/video/SHOT-EP003-002.mp4", duration=6, resolution="360p")
        path, pid = ga.run_video(job)
        create = mock.requests[0]["body"]
        assert create["background"] is True and create["model"] == "gemini-omni-1.2-flash"
        assert create["response_format"] == {"type": "video", "aspect_ratio": "9:16", "resolution": "360p",
                                             "duration": "6s", "delivery": "uri"}
        kinds = [(i["type"], i.get("mime_type")) for i in create["input"]]
        assert kinds == [("image", "image/png"), ("image", "image/jpeg"), ("image", "image/png"), ("text", None)]
        text = create["input"][-1]["text"]
        assert "Reference Image 1 (참조3), role first_frame" in text
        assert "Reference Image 2 (참조2), role last_frame" in text
        assert "Reference Image 3 (참조1), role reference_image" in text
        assert "Image 1 is the exact first frame and Image 2 is the exact last frame" in text
        assert path.read_bytes() == MP4 and pid == "v1_vid1"
        assert json.loads((tmp / "handle.json").read_text())["provider_job_id"] == "v1_vid1"

        # 4. video: hold_last_frame (K3) sends the first frame twice; background 400 -> sync fallback, inline data
        mock.requests.clear()
        mock.on("POST", "/v1beta/interactions",
                (400, {"error": {"code": "INVALID_ARGUMENT", "message": "secret prompt echo"}}),
                (200, {"id": "v1_vid2", "status": "completed",
                       "output_video": {"data": base64.b64encode(MP4).decode()}}))
        job = _job(tmp, "video", [("k3.png", PNG, "first_frame")], "剧集/EP003/制作成果/video/k3.mp4",
                   duration="4s", hold_last_frame=True)
        path, pid = ga.run_video(job)
        assert "background" in mock.requests[0]["body"] and "background" not in mock.requests[1]["body"]
        images = [i for i in mock.requests[1]["body"]["input"] if i["type"] == "image"]
        assert len(images) == 2 and images[0]["data"] == images[1]["data"]
        assert "sent again as Image 2" in mock.requests[1]["body"]["input"][-1]["text"]
        assert path.read_bytes() == MP4 and pid == "v1_vid2"

        # 5. collect: re-poll an existing interaction without submitting
        mock.requests.clear()
        mock.on("GET", "/v1beta/interactions/v1_vid9", (200, {"id": "v1_vid9", "status": "completed",
                "output_video": {"data": base64.b64encode(MP4).decode()}}))
        job = _job(tmp, "video", [], "剧集/EP003/制作成果/video/c.mp4", duration=5)
        job["collect_provider_job_id"] = "v1_vid9"
        path, pid = ga.run_video(job)
        assert all(r["method"] == "GET" for r in mock.requests) and pid == "v1_vid9"

        # 6. validation guards (no request is sent)
        two = [("a.png", PNG, "first_frame"), ("b.png", PNG, "last_frame")]
        _expect_error(ga.compile_video_request, _job(tmp, "video", two, "v.mp4", duration=12), contains="10 seconds")
        _expect_error(ga.compile_video_request, _job(tmp, "video", [], "v.mp4"), contains="duration")
        _expect_error(ga.compile_video_request, _job(tmp, "video", [("b.png", PNG, "last_frame")], "v.mp4", duration=5),
                      contains="needs a first_frame")
        _expect_error(ga.compile_video_request, _job(tmp, "video", two, "v.mp4", duration=5, hold_last_frame=True),
                      contains="hold_last_frame")
        _expect_error(ga.compile_video_request, _job(tmp, "video", [], "v.mp4", duration=5, resolution="480p"),
                      contains="resolution")
        _expect_error(ga.compile_image_request, _job(tmp, "image", [], "x.gif"), contains="png")

        # 7. CLI contract: rate limit -> whitelisted error JSON, provider body never echoed
        mock.on("POST", "/v1beta/interactions", (429, {"error": {"code": "RESOURCE_EXHAUSTED",
                                                                 "message": "secret prompt echo"}}))
        job = _job(tmp, "image", [], "剧集/EP003/制作成果/images/r.png")
        result = subprocess.run([sys.executable, str(Path(ga.__file__)), "image"], input=json.dumps(job).encode(),
                                capture_output=True, env=os.environ.copy())
        out = json.loads(result.stdout)
        assert result.returncode == 1 and out["error"]["category"] == "rate_limit" and out["error"]["retryable"]
        assert b"secret" not in result.stdout + result.stderr

        # 8. CLI contract: success JSON
        mock.on("POST", "/v1beta/interactions", (200, {"id": "v1_img3", "status": "completed",
                "output_image": {"data": base64.b64encode(PNG).decode()}}))
        job = _job(tmp, "image", [], "剧集/EP003/制作成果/images/s.png")
        result = subprocess.run([sys.executable, str(Path(ga.__file__)), "image"], input=json.dumps(job).encode(),
                                capture_output=True, env=os.environ.copy())
        out = json.loads(result.stdout)
        assert result.returncode == 0 and out["outputs"][0]["target"].endswith("s.png")
        assert Path(out["outputs"][0]["source"]).read_bytes() == PNG and out["provider_job_id"] == "v1_img3"
    server.shutdown()
    print("gemini adapter selftest: 8 scenarios passed")
