#!/usr/bin/env python3
"""sume video adapter for drama-skills ``short-drama-produce`` (default model: Gemini Omni 1.2 Flash).

Flow (verified live 2026-11-16): upload each frame with ``POST /v1/files/upload`` (raw bytes →
``data.public_url``), ``POST /v1/video-router/generate`` (Bearer, Idempotency-Key = run id so a
retried run never pays twice), poll ``GET /v1/jobs/{id}``, download ``result.artifacts[type=video].url``.
A failed generation is refunded by sume.

Parameters (job ``parameters``)
  duration          required, whole seconds 3–15 (two frames: ≤10)
  resolution        360p | 720p | 1080p | 4K   (default 360p)
  ratio             9:16 | 16:9                 (default 9:16)
  hold_last_frame   true → the first frame is also sent as the end frame (hold shot)
  generate_audio    default true
Roles: first_frame (≤1), last_frame (≤1, needs first_frame), reference_image (≤6).

Credential: SUME_API_KEY env, else macOS keychain item SUME_API_KEY.
Env: SUME_BASE_URL (default https://api.sume.com/v1), SUME_VIDEO_MODEL (default gemini-omni-flash-1.2),
     SUME_POLL_SECONDS (8), SUME_POLL_TIMEOUT (1500).
Uploaded frames and results are stored by sume at public-by-link URLs.

Usage: python3 sume_video_adapter.py < job.json   |   --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("sume_video_adapter.py requires Python 3.9 or newer")

_DEFAULT_SCRIPTS = (Path(__file__).resolve().parents[2] / "skills" / "short-drama-produce" / "scripts")  # plugin: <root>/skills/short-drama-produce/scripts
sys.path.insert(0, os.environ.get("DRAMA_SKILLS_PRODUCE_SCRIPTS", str(_DEFAULT_SCRIPTS)))
import provider_adapters as pa  # noqa: E402

AdapterFailure = pa.AdapterFailure
PROVIDER = "sume-video"
BASE_URL = "https://api.sume.com/v1"
MODEL = "gemini-omni-flash-1.2"
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
RESOLUTIONS = {"360p": "360p", "720p": "720p", "1080p": "1080p", "4k": "4K"}
ROLES = {"first_frame", "last_frame", "reference_image"}
DONE, FAILED = {"completed", "succeeded"}, {"failed", "canceled", "cancelled", "expired"}


def compile_request(job: Mapping[str, Any]) -> dict[str, Any]:
    """Body without media URLs + the reference order to upload. Pure."""
    prompt, parameters = pa._require_job(job, "video")
    parameters = pa._take(parameters, {"duration", "resolution", "ratio", "aspect_ratio", "hold_last_frame",
                                       "generate_audio", "prompt_language"})
    parameters.pop("prompt_language", None)
    duration = parameters.pop("duration", None)
    if isinstance(duration, str) and re.fullmatch(r"\d{1,2}s?", duration.strip()):
        duration = int(duration.strip().rstrip("s"))
    if isinstance(duration, bool) or not isinstance(duration, int) or not 3 <= duration <= 15:
        raise ValueError("duration must be whole seconds between 3 and 15")
    resolution = RESOLUTIONS.get(str(parameters.pop("resolution", "360p")).lower())
    if resolution is None:
        raise ValueError("resolution must be 360p, 720p, 1080p or 4K")
    ratio = parameters.pop("aspect_ratio", None) or parameters.pop("ratio", "9:16")
    if ratio not in {"9:16", "16:9"}:
        raise ValueError("ratio must be 9:16 or 16:9")
    hold = parameters.pop("hold_last_frame", False)
    audio = parameters.pop("generate_audio", True)
    if not isinstance(hold, bool) or not isinstance(audio, bool):
        raise ValueError("hold_last_frame and generate_audio must be true or false")
    if Path(job["outputs"][0]).suffix.casefold() != ".mp4":
        raise ValueError("output must be an .mp4 file")
    references = job.get("references", [])
    roles = pa._binding_roles(job, allowed={r: r for r in ROLES}, provider=PROVIDER) if references else []
    for path in references:
        if Path(path).suffix.casefold() not in IMAGE_MIME:
            raise ValueError("references must be png, jpg or webp images")
    count = {r: roles.count(r) for r in ROLES}
    if count["first_frame"] > 1 or count["last_frame"] > 1 or count["reference_image"] > 6:
        raise ValueError("at most one first_frame, one last_frame and six reference images")
    if count["last_frame"] and not count["first_frame"]:
        raise ValueError("last_frame needs a first_frame")
    if hold and (not count["first_frame"] or count["last_frame"]):
        raise ValueError("hold_last_frame needs a first_frame and no last_frame")
    if (count["last_frame"] or hold) and duration > 10:
        raise ValueError("first+last frame clips are limited to 10 seconds")
    text = prompt
    if count["reference_image"]:
        tokens, n = [], 0
        for role in roles:
            if role == "reference_image":
                tokens.append(f"<IMAGE_REF_{n}>"); n += 1
            else:
                tokens.append("the start frame" if role == "first_frame" else "the end frame")
        text = pa._prompt_with_reference_contract(prompt, job, prompt_language="en", reference_tokens=tokens)
    return {"model": os.environ.get("SUME_VIDEO_MODEL", MODEL), "prompt": text, "duration": duration,
            "resolution": resolution, "aspect_ratio": ratio, "generate_audio": audio, "mode": "async",
            "_roles": roles, "_hold": hold}


def _key() -> str:
    if os.environ.get("SUME_API_KEY"):
        return os.environ["SUME_API_KEY"]
    if sys.platform == "darwin":
        found = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "SUME_API_KEY", "-w"],
                               capture_output=True, text=True, timeout=10, check=False)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    raise AdapterFailure("provider credential is not configured", category="configuration", code="missing_credential")


def _base() -> str:
    value = os.environ.get("SUME_BASE_URL", BASE_URL).rstrip("/")
    p = urllib.parse.urlparse(value)
    local = os.environ.get("SUME_ADAPTER_ALLOW_LOCAL_HTTP") == "1" and p.scheme == "http" and p.hostname == "127.0.0.1"
    if (p.scheme != "https" and not local) or not p.netloc or p.username or p.password:
        raise AdapterFailure("sume base URL is invalid", category="configuration", code="invalid_base_url")
    return value


def _failure(status: int | None, document: object, fallback: str) -> AdapterFailure:
    err = document.get("error") if isinstance(document, Mapping) else None
    err = err if isinstance(err, Mapping) else {}
    code = pa._safe_token(err.get("code")) or fallback
    category = {"auth": "authentication", "rate_limit": "rate_limit", "validation": "invalid_request",
                "generation_rejected": "provider_response"}.get(str(err.get("category")), "provider_response")
    if code == "content_policy_rejected":
        category = "provider_response"
    return AdapterFailure(f"{PROVIDER} request failed", category=category, code=code, http_status=status,
                          request_id=pa._safe_token(err.get("request_id")),
                          retryable=bool(err.get("retryable")) or status == 429 or (status or 0) >= 500)


def _call(method: str, url: str, data: bytes | None = None, headers: Mapping[str, str] | None = None,
          timeout: float = 300) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {_key()}")
    for k, v in (headers or {}).items():
        request.add_header(k, v)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(pa.MAX_JSON_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        try:
            doc = json.loads(exc.read(pa.MAX_ERROR_BODY_BYTES + 1) or b"{}")
        except (ValueError, OSError):
            doc = {}
        raise _failure(exc.code, doc, f"http_{exc.code}") from exc
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        raise AdapterFailure(f"{PROVIDER} request failed", category="network", code="network_error",
                             retryable=True) from exc
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise AdapterFailure(f"{PROVIDER} returned invalid JSON", code="invalid_json") from exc
    if not isinstance(doc, dict):
        raise AdapterFailure(f"{PROVIDER} returned an invalid response", code="invalid_response")
    return doc


def _upload(path: Path, run_id: str) -> str:
    content = pa._read_reference(path)
    try:
        pa._validate_media_content(path.name, content)
    except AdapterFailure as exc:
        raise AdapterFailure("a frame's bytes do not match its extension", category="configuration",
                             code="reference_type_mismatch") from exc
    name = urllib.parse.quote(path.name)
    doc = _call("POST", f"{_base()}/files/upload?filename={name}", content,
                {"Content-Type": IMAGE_MIME[path.suffix.casefold()],
                 "Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{path.name}:{len(content)}"))})
    url = (doc.get("data") or {}).get("public_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise AdapterFailure("sume upload returned no public URL", code="missing_upload_url")
    return url


def _job_doc(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    data = doc.get("data", {})
    return data.get("job", data) if isinstance(data, Mapping) else {}


def _poll(job_id: str) -> Mapping[str, Any]:
    interval = float(os.environ.get("SUME_POLL_SECONDS", "8"))
    deadline = time.monotonic() + float(os.environ.get("SUME_POLL_TIMEOUT", "1500"))
    while True:
        job = _job_doc(_call("GET", f"{_base()}/jobs/{urllib.parse.quote(job_id, safe='')}"))
        status = job.get("status")
        if status in DONE:
            return job
        if status in FAILED:
            raise _failure(None, {"error": job.get("error") or {}}, f"status_{pa._safe_token(status) or 'failed'}")
        if time.monotonic() > deadline:
            raise AdapterFailure("sume job is still running; collect it later", category="timeout",
                                 code="poll_timeout", request_id=pa._safe_token(job_id), retryable=True)
        time.sleep(interval)


def _download(job: Mapping[str, Any], done: Mapping[str, Any]) -> Path:
    artifacts = (done.get("result") or {}).get("artifacts") or []
    video = next((a for a in artifacts if isinstance(a, Mapping) and a.get("type") == "video"), None)
    url = video.get("url") if video else None
    if not isinstance(url, str):
        raise AdapterFailure("sume job finished without a video", code="missing_video")
    parsed = urllib.parse.urlparse(url)
    local = os.environ.get("SUME_ADAPTER_ALLOW_LOCAL_HTTP") == "1" and parsed.hostname == "127.0.0.1"
    if parsed.scheme != "https" and not local:
        raise AdapterFailure("sume video URL is invalid", code="invalid_video_url")
    target = job["outputs"][0]
    path = pa._output_root(job) / "result.mp4"
    try:
        with urllib.request.urlopen(url, timeout=600) as response, path.open("xb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        path.unlink(missing_ok=True)
        raise AdapterFailure("sume video download failed", category="network", code="download_failed",
                             retryable=True) from exc
    with path.open("rb") as handle:
        pa._validate_media_content(target, handle.read(16))
    return path


def run(job: Mapping[str, Any]) -> tuple[Path, str]:
    collect = pa._collect_target(job)
    if collect:
        return _download(job, _poll(collect)), collect
    body = compile_request(job)
    roles, hold = body.pop("_roles"), body.pop("_hold")
    run_id = str(job.get("run_id") or uuid.uuid4())
    paths = pa._reference_paths(job)
    refs = []
    for path, role in zip(paths, roles):
        url = _upload(path, run_id)
        if role == "first_frame":
            body["image_url"] = url
            if hold:
                body["end_image_url"] = url
        elif role == "last_frame":
            body["end_image_url"] = url
        else:
            refs.append(url)
    if refs:
        body["reference_image_urls"] = refs
    body["metadata"] = {"job_id": str(job.get("job_id", ""))[:120]}
    doc = _call("POST", f"{_base()}/video-router/generate", json.dumps(body).encode(),
                {"Content-Type": "application/json", "Idempotency-Key": f"{run_id}-generate"})
    job_id = pa._safe_token(_job_doc(doc).get("id"))
    if not job_id:
        raise AdapterFailure("sume returned no job id", code="missing_job_id")
    pa._record_handle(job, job_id)
    return _download(job, _poll(job_id)), job_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="sume video adapter for short-drama-produce")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        from sume_video_adapter_selftest import selftest
        selftest()
        return 0
    try:
        job = json.load(sys.stdin.buffer)
        if not isinstance(job, Mapping):
            raise ValueError("adapter input must be an object")
        path, job_id = run(job)
        json.dump({"outputs": [{"target": job["outputs"][0], "source": str(path)}], "provider_job_id": job_id},
                  sys.stdout, ensure_ascii=True)
        return 0
    except AdapterFailure as exc:
        json.dump({"error": exc.public(PROVIDER)}, sys.stdout, ensure_ascii=True)
        print("sume video adapter failed safely", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        json.dump({"error": {"provider": PROVIDER, "category": "invalid_request", "code": "invalid_job",
                             "retryable": False}}, sys.stdout, ensure_ascii=True)
        print(f"sume video adapter rejected the job: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
