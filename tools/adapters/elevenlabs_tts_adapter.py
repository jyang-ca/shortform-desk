#!/usr/bin/env python3
"""ElevenLabs TTS adapter for drama-skills ``short-drama-produce`` (modality ``tts``).

One confirmed job = one spoken line (the ``prompt`` is spoken verbatim). Writes
one .mp3 (or .wav via ffmpeg) into ``output_root`` and prints only the adapter
contract JSON. Request bodies, the text and the key are never echoed.

Parameters (job ``parameters``)
  voice_id           required (must be in the account's My Voices; see 시리즈/설정.json production.voice_id)
  model_id           default eleven_v4
  stability          default 0.6     similarity_boost  default 0.75
  style, speed, use_speaker_boost, seed, language_code    optional
  previous_text, next_text  neighbouring lines for continuous prosody (not spoken)
  output_format      default mp3_44100_128 (mp3 targets only)

Credential: ELEVENLABS_API_KEY env, else the macOS keychain item ELEVENLABS_API_KEY.
Env: ELEVENLABS_BASE_URL (default https://api.elevenlabs.io/v1).

Usage:  python3 elevenlabs_tts_adapter.py < job.json   |   --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("elevenlabs_tts_adapter.py requires Python 3.9 or newer")

_DEFAULT_SCRIPTS = (Path(__file__).resolve().parents[2] / "skills" / "short-drama-produce" / "scripts")  # plugin: <root>/skills/short-drama-produce/scripts
sys.path.insert(0, os.environ.get("DRAMA_SKILLS_PRODUCE_SCRIPTS", str(_DEFAULT_SCRIPTS)))
import provider_adapters as pa  # noqa: E402

AdapterFailure = pa.AdapterFailure
PROVIDER = "elevenlabs-tts"
BASE_URL = "https://api.elevenlabs.io/v1"
TEXT_LIMIT = 5000
SETTING_KEYS = {"stability", "similarity_boost", "style", "speed", "use_speaker_boost"}


def compile_tts_request(job: Mapping[str, Any]) -> tuple[str, dict[str, Any], dict[str, str]]:
    """(voice_id, JSON body, query). Pure."""
    text, parameters = pa._require_job(job, "tts")
    parameters = pa._take(parameters, {"voice_id", "model_id", "output_format", "seed", "language_code",
                                       "prompt_language", "previous_text", "next_text"} | SETTING_KEYS)
    parameters.pop("prompt_language", None)
    voice = parameters.pop("voice_id", None)
    if not isinstance(voice, str) or not voice.strip() or not voice.isalnum():
        raise ValueError("voice_id is required")
    if len(text) > TEXT_LIMIT:
        raise ValueError("line exceeds the ElevenLabs text limit")
    suffix = Path(job["outputs"][0]).suffix.casefold()
    if suffix not in {".mp3", ".wav"}:
        raise ValueError("tts output must be .mp3 or .wav")
    output_format = parameters.pop("output_format", "mp3_44100_128")
    if not isinstance(output_format, str) or not output_format.startswith("mp3_"):
        raise ValueError("output_format must be an mp3_* format")
    settings = {"stability": 0.6, "similarity_boost": 0.75}
    for key in SETTING_KEYS:
        if key in parameters:
            value = parameters.pop(key)
            if key == "use_speaker_boost":
                if not isinstance(value, bool):
                    raise ValueError("use_speaker_boost must be true or false")
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
            settings[key] = value
    body: dict[str, Any] = {"text": text, "model_id": parameters.pop("model_id", "eleven_v4"),
                            "voice_settings": settings}
    for key in ("seed", "language_code", "previous_text", "next_text"):
        if key in parameters:
            body[key] = parameters.pop(key)
    return voice, body, {"output_format": output_format}


def _api_key() -> str:
    value = os.environ.get("ELEVENLABS_API_KEY")
    if value:
        return value
    if sys.platform == "darwin":
        found = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "ELEVENLABS_API_KEY", "-w"],
                               capture_output=True, text=True, timeout=10, check=False)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    raise AdapterFailure("provider credential is not configured", category="configuration",
                         code="missing_credential")


def _base() -> str:
    value = os.environ.get("ELEVENLABS_BASE_URL", BASE_URL).rstrip("/")
    parsed = urllib.parse.urlparse(value)
    local = (os.environ.get("ELEVENLABS_ADAPTER_ALLOW_LOCAL_HTTP") == "1"
             and parsed.scheme == "http" and parsed.hostname == "127.0.0.1")
    if (parsed.scheme != "https" and not local) or not parsed.netloc or parsed.username or parsed.password:
        raise AdapterFailure("ElevenLabs base URL is invalid", category="configuration", code="invalid_base_url")
    return value


def _http_failure(error: urllib.error.HTTPError) -> AdapterFailure:
    """ElevenLabs errors are {"detail": {"code"|"status": ...}}; only the safe code token is kept."""
    status = error.code
    code = None
    try:
        raw = error.read(pa.MAX_ERROR_BODY_BYTES + 1)
        detail = json.loads(raw).get("detail") if len(raw) <= pa.MAX_ERROR_BODY_BYTES else None
        if isinstance(detail, Mapping):
            code = pa._safe_token(detail.get("code")) or pa._safe_token(detail.get("status"))
    except (OSError, UnicodeError, ValueError, AttributeError):
        code = None
    if code in {"voice_not_found", "missing_permissions"} or status == 404:
        category = "configuration"
    elif status == 401:
        category = "authentication"
    elif status == 429:
        category = "rate_limit"
    elif 500 <= status <= 599:
        category = "server"
    elif 400 <= status <= 499:
        category = "invalid_request"
    else:
        category = "provider_response"
    request_id = None
    if error.headers is not None:
        request_id = pa._safe_token(error.headers.get("request-id") or error.headers.get("x-request-id"))
    return AdapterFailure(f"{PROVIDER} HTTP request failed", category=category, code=code or f"http_{status}",
                          http_status=status, request_id=request_id,
                          retryable=status == 429 or 500 <= status <= 599)


def run(job: Mapping[str, Any]) -> tuple[Path, str | None]:
    if pa._collect_target(job):
        raise AdapterFailure("tts jobs are synchronous and cannot be collected", category="configuration",
                             code="collect_unsupported")
    voice, body, query = compile_tts_request(job)
    url = f"{_base()}/text-to-speech/{voice}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST")
    request.add_header("xi-api-key", _api_key())
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "audio/mpeg")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            content = response.read(pa.MAX_OUTPUT_BYTES + 1)
            request_id = pa._safe_token(response.headers.get("request-id") or response.headers.get("x-request-id"))
    except urllib.error.HTTPError as exc:
        raise _http_failure(exc) from exc
    except TimeoutError as exc:
        raise AdapterFailure("ElevenLabs request timed out", category="timeout", code="request_timeout",
                             retryable=True) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise AdapterFailure("ElevenLabs request failed", category="network", code="network_error",
                             retryable=True) from exc
    target = job["outputs"][0]
    mp3 = pa._temporary_output({**job, "outputs": ["x.mp3"]}, "x.mp3", content) if Path(target).suffix.casefold() == ".wav" \
        else pa._temporary_output(job, target, content)
    if Path(target).suffix.casefold() == ".wav":
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise AdapterFailure("ffmpeg is required for .wav targets", category="configuration", code="ffmpeg_missing")
        wav = mp3.with_name("result.wav")
        done = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(mp3), str(wav)], capture_output=True, check=False)
        mp3.unlink(missing_ok=True)
        if done.returncode != 0 or not wav.is_file():
            raise AdapterFailure("wav conversion failed", code="conversion_failed")
        return wav, request_id
    return mp3, request_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ElevenLabs TTS adapter for short-drama-produce")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        from elevenlabs_tts_adapter_selftest import selftest
        selftest()
        return 0
    try:
        job = json.load(sys.stdin.buffer)
        if not isinstance(job, Mapping):
            raise ValueError("adapter input must be an object")
        path, request_id = run(job)
        response: dict[str, Any] = {"outputs": [{"target": job["outputs"][0], "source": str(path)}]}
        if request_id:
            response["provider_job_id"] = request_id
        json.dump(response, sys.stdout, ensure_ascii=True)
        return 0
    except AdapterFailure as exc:
        json.dump({"error": exc.public(PROVIDER)}, sys.stdout, ensure_ascii=True)
        print("elevenlabs adapter failed safely", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        json.dump({"error": {"provider": PROVIDER, "category": "invalid_request", "code": "invalid_job",
                             "retryable": False}}, sys.stdout, ensure_ascii=True)
        print(f"elevenlabs adapter rejected the job: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
