#!/usr/bin/env python3
"""Gemini adapter for drama-skills ``short-drama-produce``.

Two profiles, one Gemini API key:

* ``image`` -- Nano Banana (default ``gemini-nano-banana-2.1``) keyframes and
  reference sheets, up to 14 reference images.
* ``video`` -- Gemini Omni Flash (default ``gemini-omni-1.2-flash``) image-to-video
  with first frame, first+last frame, "hold" (first frame sent again as the last
  frame, a 'hold' shot) and up to 6 reference images.

The adapter follows drama-skills' adapter contract
(``skills/short-drama-produce/references/adapter-contract.md``): it reads one
confirmed job from stdin, writes the media into ``output_root`` and prints only
the contract JSON. It reuses the bundled adapter module for job validation,
the ordered reference contract, safe error reporting and the crash handle, so
those semantics stay identical to the built-in providers. The upstream clone is
imported, never modified.

Environment (credentials only from the environment, never from project files):

  GEMINI_API_KEY            required (or a macOS keychain item named GEMINI_API_KEY:
                            security add-generic-password -a "$USER" -s GEMINI_API_KEY -w)
  GEMINI_BASE_URL           default https://generativelanguage.googleapis.com/v1beta
  GEMINI_API_REVISION       default 2026-05-20 (sent as the Api-Revision header; "" to omit)
  GEMINI_IMAGE_MODEL        default gemini-nano-banana-2.1
  GEMINI_VIDEO_MODEL        default gemini-omni-1.2-flash
  GEMINI_VIDEO_BACKGROUND   auto (default) | on | off
  GEMINI_POLL_SECONDS       default 5
  GEMINI_POLL_TIMEOUT       default 1800

Usage:
  python3 gemini_adapter.py image < job.json
  python3 gemini_adapter.py video < job.json
  python3 gemini_adapter.py --selftest
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("gemini_adapter.py requires Python 3.9 or newer")

_DEFAULT_SCRIPTS = (Path(__file__).resolve().parents[2] / "skills" / "short-drama-produce" / "scripts")  # plugin: <root>/skills/short-drama-produce/scripts
sys.path.insert(0, os.environ.get("DRAMA_SKILLS_PRODUCE_SCRIPTS", str(_DEFAULT_SCRIPTS)))
import provider_adapters as pa  # noqa: E402  (drama-skills bundled adapter module)

AdapterFailure = pa.AdapterFailure

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
API_REVISION = "2026-05-20"
IMAGE_MODEL = "gemini-nano-banana-2.1"
VIDEO_MODEL = "gemini-omni-1.2-flash"

IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
VIDEO_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
IMAGE_SIZES = {"512", "1K", "2K", "4K"}
IMAGE_MAX_REFERENCES = 14
VIDEO_RATIOS = {"9:16", "16:9"}
VIDEO_RESOLUTIONS = {"360p", "720p", "1080p", "4k"}
VIDEO_TASKS = {"text_to_video", "image_to_video", "reference_to_video"}
VIDEO_MIN_SECONDS, VIDEO_MAX_SECONDS, VIDEO_MAX_INTERPOLATION_SECONDS = 3, 15, 10
VIDEO_MAX_REFERENCE_IMAGES, VIDEO_MAX_REFERENCE_VIDEOS = 6, 3
VIDEO_ROLES = {
    "first_frame": "image",
    "last_frame": "image",
    "reference_image": "image",
    "reference_video": "video",
}
DONE, FAILED = {"completed"}, {"failed", "cancelled", "canceled", "expired"}


# --------------------------------------------------------------------------- #
# Pure compilation (no I/O)
# --------------------------------------------------------------------------- #

def _ratio(parameters: dict[str, Any]) -> str | None:
    a, b = parameters.pop("aspect_ratio", None), parameters.pop("ratio", None)
    if a is not None and b is not None and a != b:
        raise ValueError("aspect_ratio and ratio disagree")
    value = a if a is not None else b
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"\d{1,2}:\d{1,2}", value)):
        raise ValueError("aspect ratio is invalid")
    return value


def compile_image_request(job: Mapping[str, Any], *, model: str = IMAGE_MODEL) -> dict[str, Any]:
    """Interaction body for a Nano Banana image job, media left as ordered slots."""
    prompt, parameters = pa._require_job(job, "image")
    parameters = pa._take(parameters, {"aspect_ratio", "ratio", "image_size", "resolution", "prompt_language"})
    language = pa._pop_prompt_language(parameters)
    ratio = _ratio(parameters)
    size = parameters.pop("image_size", None)
    resolution = parameters.pop("resolution", None)
    if size is not None and resolution is not None and size != resolution:
        raise ValueError("image_size and resolution disagree")
    size = size if size is not None else resolution
    if size is not None:
        size = str(size).upper()
        if size not in IMAGE_SIZES:
            raise ValueError("image_size must be one of 512, 1K, 2K, 4K")
    suffix = Path(job["outputs"][0]).suffix.casefold()
    if suffix not in IMAGE_MIME:
        raise ValueError("image output must be png, jpg or webp")
    references = job.get("references", [])
    if not isinstance(references, list) or len(references) > IMAGE_MAX_REFERENCES:
        raise ValueError("Nano Banana accepts at most fourteen reference images")
    if any(not isinstance(r, str) or Path(r).suffix.casefold() not in IMAGE_MIME for r in references):
        raise ValueError("Nano Banana references must be png, jpg or webp images")
    tokens = [f"Image {index}" for index in range(1, len(references) + 1)]
    text = pa._prompt_with_reference_contract(
        prompt, job, prompt_language=language, reference_tokens=tokens if references else ()
    )
    response_format: dict[str, Any] = {"type": "image", "mime_type": IMAGE_MIME[suffix]}
    if ratio is not None:
        response_format["aspect_ratio"] = ratio
    if size is not None:
        response_format["image_size"] = size
    return {
        "model": model,
        "media": [{"reference_index": index, "kind": "image"} for index in range(len(references))],
        "text": text,
        "response_format": response_format,
    }


def compile_video_request(job: Mapping[str, Any], *, model: str = VIDEO_MODEL) -> dict[str, Any]:
    """Interaction body for an Omni video job, media left as ordered slots.

    Media are sent first-frame, last-frame, reference images, reference videos,
    and the reference contract names each binding by its sent position
    ("Image 1", "Video 1"), whatever order the bindings were written in.
    """
    prompt, parameters = pa._require_job(job, "video")
    parameters = pa._take(
        parameters,
        {"duration", "aspect_ratio", "ratio", "resolution", "task", "hold_last_frame", "prompt_language"},
    )
    language = pa._pop_prompt_language(parameters)
    ratio = _ratio(parameters) or "9:16"
    if ratio not in VIDEO_RATIOS:
        raise ValueError("Omni aspect ratio must be 9:16 or 16:9")
    resolution = str(parameters.pop("resolution", "720p")).lower()
    if resolution not in VIDEO_RESOLUTIONS:
        raise ValueError("Omni resolution must be 360p, 720p, 1080p or 4k")
    raw_duration = parameters.pop("duration", None)
    if raw_duration is None:
        # The API default is 10 s; billing is per second, so never default silently.
        raise ValueError("video jobs must state duration in seconds")
    if isinstance(raw_duration, str) and re.fullmatch(r"\d{1,2}s?", raw_duration.strip()):
        raw_duration = int(raw_duration.strip().rstrip("s"))
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, int):
        raise ValueError("duration must be whole seconds")
    if not VIDEO_MIN_SECONDS <= raw_duration <= VIDEO_MAX_SECONDS:
        raise ValueError("Omni duration must be 3-15 seconds")
    task = parameters.pop("task", None)
    if task is not None and task not in VIDEO_TASKS:
        raise ValueError("Omni task is invalid for new generation")
    hold = parameters.pop("hold_last_frame", False)
    if not isinstance(hold, bool):
        raise ValueError("hold_last_frame must be true or false")
    if Path(job["outputs"][0]).suffix.casefold() != ".mp4":
        raise ValueError("Omni output must be an .mp4 file")

    references = job.get("references", [])
    if not isinstance(references, list):
        raise ValueError("references must be a list")
    roles = (
        pa._binding_roles(job, allowed={k: k for k in VIDEO_ROLES}, provider="gemini-omni")
        if references else []
    )
    for path, role in zip(references, roles):
        mimes = IMAGE_MIME if VIDEO_ROLES[role] == "image" else VIDEO_MIME
        if not isinstance(path, str) or Path(path).suffix.casefold() not in mimes:
            raise ValueError(f"{role} reference has an unsupported file type")
    count = {role: roles.count(role) for role in VIDEO_ROLES}
    if count["first_frame"] > 1 or count["last_frame"] > 1:
        raise ValueError("at most one first_frame and one last_frame")
    if count["last_frame"] and not count["first_frame"]:
        raise ValueError("last_frame needs a first_frame")
    if hold and (not count["first_frame"] or count["last_frame"]):
        raise ValueError("hold_last_frame needs a first_frame and no last_frame")
    if count["reference_image"] > VIDEO_MAX_REFERENCE_IMAGES:
        raise ValueError("Omni accepts at most six reference images")
    if count["reference_video"] > VIDEO_MAX_REFERENCE_VIDEOS:
        raise ValueError("Omni accepts at most three reference videos")
    two_frames = bool(count["last_frame"]) or hold
    if two_frames and raw_duration > VIDEO_MAX_INTERPOLATION_SECONDS:
        raise ValueError("first+last frame videos are limited to 10 seconds")

    order = [i for r in ("first_frame", "last_frame", "reference_image", "reference_video")
             for i, role in enumerate(roles) if role == r]
    media: list[dict[str, Any]] = []
    tokens = [""] * len(references)
    image_no = video_no = 0
    for index in order:
        kind = VIDEO_ROLES[roles[index]]
        media.append({"reference_index": index, "kind": kind})
        if kind == "image":
            image_no += 1
            tokens[index] = f"Image {image_no}"
        else:
            video_no += 1
            tokens[index] = f"Video {video_no}"
        if hold and roles[index] == "first_frame":
            media.append({"reference_index": index, "kind": "image"})
            image_no += 1
            tokens[index] += f" (sent again as Image {image_no})"
    text = pa._prompt_with_reference_contract(
        prompt, job, prompt_language=language, reference_tokens=tokens if references else ()
    )
    if two_frames:
        text += (
            "\n\nFrames: Image 1 is the exact first frame and Image 2 is the exact last frame of this one shot."
            if not hold else
            "\n\nFrames: Image 1 is the exact first frame. Image 2 is the same picture: the shot ends on exactly this frame."
        )
    body: dict[str, Any] = {
        "model": model,
        "media": media,
        "text": text,
        "response_format": {
            "type": "video",
            "aspect_ratio": ratio,
            "resolution": resolution,
            "duration": f"{raw_duration}s",
            "delivery": "uri",
        },
    }
    if task is not None:
        body["generation_config"] = {"video_config": {"task": task}}
    return body


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _base() -> str:
    value = os.environ.get("GEMINI_BASE_URL", BASE_URL).rstrip("/")
    parsed = urllib.parse.urlparse(value)
    local_test = (
        os.environ.get("GEMINI_ADAPTER_ALLOW_LOCAL_HTTP") == "1"
        and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    )
    if (parsed.scheme != "https" and not local_test) or not parsed.netloc or parsed.username or parsed.password:
        raise AdapterFailure("Gemini base URL is invalid", category="configuration", code="invalid_base_url")
    return value


def _api_key() -> str:
    """GEMINI_API_KEY from the environment, else the macOS keychain item of that name."""
    value = os.environ.get("GEMINI_API_KEY")
    if value:
        return value
    if sys.platform == "darwin":
        import subprocess
        try:
            found = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", "GEMINI_API_KEY", "-w"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            found = None
        if found is not None and found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    raise AdapterFailure("provider credential is not configured", category="configuration",
                         code="missing_credential")


def _request(provider: str, method: str, url: str, body: Mapping[str, Any] | None = None,
             *, timeout: float = 180) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("x-goog-api-key", _api_key())
    revision = os.environ.get("GEMINI_API_REVISION", API_REVISION)
    if revision:
        request.add_header("Api-Revision", revision)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(pa.MAX_JSON_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        raise pa._http_failure(provider, exc) from exc
    except TimeoutError as exc:
        raise AdapterFailure(f"{provider} request timed out", category="timeout",
                             code="request_timeout", retryable=True) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise AdapterFailure(f"{provider} request failed", category="network",
                             code="network_error", retryable=True) from exc
    if len(raw) > pa.MAX_JSON_RESPONSE:
        raise AdapterFailure(f"{provider} response is too large", code="response_too_large")
    try:
        document = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterFailure(f"{provider} returned invalid JSON", code="invalid_json") from exc
    if not isinstance(document, dict):
        raise AdapterFailure(f"{provider} returned an invalid response", code="invalid_response")
    return document


def _interaction_input(job: Mapping[str, Any], compiled: Mapping[str, Any]) -> list[dict[str, Any]]:
    paths = pa._reference_paths(job)
    items: list[dict[str, Any]] = []
    for slot in compiled["media"]:
        path = paths[slot["reference_index"]]
        table = IMAGE_MIME if slot["kind"] == "image" else VIDEO_MIME
        content = pa._read_reference(path)
        if slot["kind"] == "image":
            try:
                pa._validate_media_content(path.name, content)
            except AdapterFailure as exc:
                raise AdapterFailure("a reference image's bytes do not match its extension",
                                     category="configuration", code="reference_type_mismatch") from exc
        items.append({"type": slot["kind"], "mime_type": table[path.suffix.casefold()],
                      "data": base64.b64encode(content).decode("ascii")})
    items.append({"type": "text", "text": compiled["text"]})
    return items


def _find_media(document: Mapping[str, Any], kind: str) -> tuple[str, str] | None:
    """("data", base64) or ("uri", "files/..."): the last generated item of ``kind``."""
    candidates: list[Mapping[str, Any]] = []
    direct = document.get(f"output_{kind}")
    if isinstance(direct, Mapping):
        candidates.append(direct)
    for step in reversed(document.get("steps") or []):
        if isinstance(step, Mapping) and step.get("type") == "model_output":
            for item in reversed(step.get("content") or []):
                if isinstance(item, Mapping) and item.get("type") == kind:
                    candidates.append(item)
    for item in candidates:
        if isinstance(item.get("uri"), str) and item["uri"]:
            return "uri", item["uri"]
        if isinstance(item.get("data"), str) and item["data"]:
            return "data", item["data"]
    return None


def _failure_from(document: Mapping[str, Any], provider: str) -> AdapterFailure:
    return AdapterFailure(
        f"{provider} generation did not complete",
        category="provider_response",
        code=pa._provider_code(document) or f"status_{pa._safe_token(document.get('status')) or 'unknown'}",
        request_id=pa._safe_token(document.get("id")),
    )


def _poll_interaction(provider: str, interaction_id: str) -> dict[str, Any]:
    interval = float(os.environ.get("GEMINI_POLL_SECONDS", "5"))
    deadline = time.monotonic() + float(os.environ.get("GEMINI_POLL_TIMEOUT", "1800"))
    url = f"{_base()}/interactions/{urllib.parse.quote(interaction_id, safe='')}"
    while True:
        document = _request(provider, "GET", url)
        status = document.get("status")
        if status in DONE:
            return document
        if status in FAILED:
            raise _failure_from(document, provider)
        if time.monotonic() > deadline:
            raise AdapterFailure(f"{provider} is still running; collect it later", category="timeout",
                                 code="poll_timeout", request_id=pa._safe_token(interaction_id),
                                 retryable=True)
        time.sleep(interval)


def _save(provider: str, job: Mapping[str, Any], found: tuple[str, str], target: str) -> Path:
    how, value = found
    if how == "data":
        try:
            content = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise AdapterFailure(f"{provider} returned invalid media data", code="invalid_media_data") from exc
        try:
            return pa._temporary_output(job, target, content)
        except AdapterFailure as exc:
            raise AdapterFailure(f"{provider} returned an unexpected media type",
                                 code="unexpected_media_type") from exc
    name = value.split("/files/", 1)[-1] if "/files/" in value else value.split("files/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", name):
        raise AdapterFailure(f"{provider} returned an invalid file URI", code="invalid_file_uri")
    interval = float(os.environ.get("GEMINI_POLL_SECONDS", "5"))
    deadline = time.monotonic() + float(os.environ.get("GEMINI_POLL_TIMEOUT", "1800"))
    while True:
        meta = _request(provider, "GET", f"{_base()}/files/{name}")
        state = meta.get("state")
        state = state.get("name") if isinstance(state, Mapping) else state
        if state == "ACTIVE":
            break
        if state == "FAILED":
            raise AdapterFailure(f"{provider} output file failed", code="file_failed")
        if time.monotonic() > deadline:
            raise AdapterFailure(f"{provider} output file is not ready", category="timeout",
                                 code="file_poll_timeout", retryable=True)
        time.sleep(interval)
    request = urllib.request.Request(f"{_base()}/files/{name}:download?alt=media", method="GET")
    request.add_header("x-goog-api-key", _api_key())
    path = pa._output_root(job) / ("result" + Path(target).suffix.casefold())
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=600) as response, path.open("xb") as handle:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > pa.MAX_OUTPUT_BYTES:
                    raise AdapterFailure("provider output is too large")
                handle.write(chunk)
    except AdapterFailure:
        path.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        path.unlink(missing_ok=True)
        raise pa._http_failure(provider, exc) from exc
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        path.unlink(missing_ok=True)
        raise AdapterFailure(f"{provider} download failed", category="network",
                             code="download_failed", retryable=True) from exc
    with path.open("rb") as handle:
        try:
            pa._validate_media_content(target, handle.read(16))
        except AdapterFailure as exc:
            raise AdapterFailure(f"{provider} downloaded an unexpected media type",
                                 code="unexpected_media_type") from exc
    if size == 0:
        raise AdapterFailure("provider output is empty")
    return path


# --------------------------------------------------------------------------- #
# Runners
# --------------------------------------------------------------------------- #

def run_image(job: Mapping[str, Any]) -> tuple[Path, str | None]:
    provider = "gemini-image"
    target = job["outputs"][0]
    collect = pa._collect_target(job)
    if collect:
        document = _poll_interaction(provider, collect)
    else:
        compiled = compile_image_request(job, model=os.environ.get("GEMINI_IMAGE_MODEL", IMAGE_MODEL))
        body = {"model": compiled["model"], "input": _interaction_input(job, compiled),
                "response_format": compiled["response_format"]}
        document = _request(provider, "POST", f"{_base()}/interactions", body, timeout=600)
        if document.get("status") in FAILED:
            raise _failure_from(document, provider)
    found = _find_media(document, "image")
    if found is None:
        raise AdapterFailure("Gemini did not return an image", code="missing_image",
                             request_id=pa._safe_token(document.get("id")))
    return _save(provider, job, found, target), pa._safe_token(document.get("id"))


def run_video(job: Mapping[str, Any]) -> tuple[Path, str | None]:
    provider = "gemini-omni"
    target = job["outputs"][0]
    collect = pa._collect_target(job)
    if collect:
        document = _poll_interaction(provider, collect)
        interaction_id: str | None = collect
    else:
        compiled = compile_video_request(job, model=os.environ.get("GEMINI_VIDEO_MODEL", VIDEO_MODEL))
        body: dict[str, Any] = {"model": compiled["model"], "input": _interaction_input(job, compiled),
                                "response_format": compiled["response_format"]}
        if "generation_config" in compiled:
            body["generation_config"] = compiled["generation_config"]
        mode = os.environ.get("GEMINI_VIDEO_BACKGROUND", "auto").lower()
        document = None
        if mode in {"auto", "on"}:
            try:
                document = _request(provider, "POST", f"{_base()}/interactions",
                                    {**body, "background": True}, timeout=180)
            except AdapterFailure as exc:
                # Omni is not in the documented background-model list. A 400 is a
                # rejected request (not billed), so fall back to one synchronous call.
                if mode == "on" or exc.http_status != 400:
                    raise
        if document is None:
            document = _request(provider, "POST", f"{_base()}/interactions", body, timeout=1800)
        interaction_id = pa._safe_token(document.get("id"))
        if interaction_id:
            pa._record_handle(job, interaction_id)
        status = document.get("status")
        if status in FAILED:
            raise _failure_from(document, provider)
        if status not in DONE and _find_media(document, "video") is None:
            if not interaction_id:
                raise AdapterFailure("Gemini returned no interaction id", code="missing_interaction_id")
            document = _poll_interaction(provider, interaction_id)
    found = _find_media(document, "video")
    if found is None:
        raise AdapterFailure("Gemini did not return a video", code="missing_video",
                             request_id=interaction_id)
    return _save(provider, job, found, target), interaction_id


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gemini adapter for short-drama-produce")
    parser.add_argument("profile", nargs="?", choices=("image", "video"))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        from gemini_adapter_selftest import selftest  # local, offline
        selftest()
        return 0
    if args.profile is None:
        parser.error("profile is required unless --selftest is used")
    provider = "gemini-image" if args.profile == "image" else "gemini-omni"
    try:
        job = json.load(sys.stdin.buffer)
        if not isinstance(job, Mapping):
            raise ValueError("adapter input must be an object")
        path, provider_job_id = (run_image if args.profile == "image" else run_video)(job)
        response: dict[str, Any] = {"outputs": [{"target": job["outputs"][0], "source": str(path)}]}
        if provider_job_id:
            response["provider_job_id"] = provider_job_id
        json.dump(response, sys.stdout, ensure_ascii=True)
        return 0
    except AdapterFailure as exc:
        json.dump({"error": exc.public(provider)}, sys.stdout, ensure_ascii=True)
        print("gemini adapter failed safely", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        json.dump({"error": {"provider": provider, "category": "invalid_request",
                             "code": "invalid_job", "retryable": False}}, sys.stdout, ensure_ascii=True)
        # The validation reason names no prompt, path or credential, so it is safe for stderr.
        print(f"gemini adapter rejected the job: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
