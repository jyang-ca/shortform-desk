#!/usr/bin/env python3
"""Codex CLI image adapter for drama-skills ``short-drama-produce``.

Generates one image with Codex's built-in image generation tool (ChatGPT
subscription; no per-image API billing), a route proven in production
for EP1-EP3 keyframes (`codex exec --image=<ref> ... -- "Use your built-in image
generation tool ..."`).

Contract: drama-skills ``references/adapter-contract.md``. Reads one confirmed
image job from stdin, writes the image into ``output_root`` and prints only the
contract JSON. Codex output and the prompt are never echoed (only whitelisted
error metadata). Collect is not supported: Codex runs synchronously and keeps no
remote task id.

Environment
  CODEX_BIN              default: `codex` on PATH, else ~/.local/bin/codex
  CODEX_IMAGE_TIMEOUT    seconds, default 900
  CODEX_GENERATED_DIR    default ~/.codex/generated_images (fallback search)

Usage
  python3 codex_image_adapter.py < job.json
  python3 codex_image_adapter.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("codex_image_adapter.py requires Python 3.9 or newer")

_DEFAULT_SCRIPTS = (Path(__file__).resolve().parents[2] / "skills" / "short-drama-produce" / "scripts")  # plugin: <root>/skills/short-drama-produce/scripts
sys.path.insert(0, os.environ.get("DRAMA_SKILLS_PRODUCE_SCRIPTS", str(_DEFAULT_SCRIPTS)))
import provider_adapters as pa  # noqa: E402

AdapterFailure = pa.AdapterFailure
PROVIDER = "codex-image"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_REFERENCES = 10
SAVE = ("Use your built-in image generation tool (not code, not SVG, not a script) to create exactly one "
        "image, then save it in the current directory as {name}. Do nothing else.")
RATIO_WORDS = {"9:16": "vertical 9:16 portrait", "16:9": "horizontal 16:9 landscape", "1:1": "square 1:1",
               "3:4": "vertical 3:4", "4:3": "horizontal 4:3", "2:3": "vertical 2:3", "3:2": "horizontal 3:2"}


def compile_codex_prompt(job: Mapping[str, Any]) -> tuple[str, list[str], str]:
    """(full prompt, reference paths in attach order, output file name). Pure."""
    prompt, parameters = pa._require_job(job, "image")
    parameters = pa._take(parameters, {"ratio", "aspect_ratio", "prompt_language"})
    language = pa._pop_prompt_language(parameters)
    ratio = parameters.pop("aspect_ratio", None) or parameters.pop("ratio", None)
    if ratio is not None and ratio not in RATIO_WORDS:
        raise ValueError("ratio must be one of " + ", ".join(sorted(RATIO_WORDS)))
    suffix = Path(job["outputs"][0]).suffix.casefold()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("image output must be png, jpg or webp")
    references = job.get("references", [])
    if not isinstance(references, list) or len(references) > MAX_REFERENCES:
        raise ValueError("Codex image jobs accept at most ten reference images")
    if any(not isinstance(r, str) or Path(r).suffix.casefold() not in IMAGE_SUFFIXES for r in references):
        raise ValueError("references must be png, jpg or webp images")
    tokens = [f"attached image {i}" for i in range(1, len(references) + 1)]
    body = pa._prompt_with_reference_contract(prompt, job, prompt_language=language,
                                              reference_tokens=tokens if references else ())
    name = "result.png"
    lines = [SAVE.format(name=name), body]
    if ratio is not None:
        lines.append(f"Image format: {RATIO_WORDS[ratio]}.")
    return "\n\n".join(lines), list(references), name


def _codex_bin() -> str:
    explicit = os.environ.get("CODEX_BIN")
    if explicit:
        return explicit
    found = shutil.which("codex")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "codex"
    if fallback.exists():
        return str(fallback)
    raise AdapterFailure("codex CLI is not installed", category="configuration", code="codex_missing")


def _generated_root() -> Path:
    return Path(os.environ.get("CODEX_GENERATED_DIR", str(Path.home() / ".codex" / "generated_images")))


def _generated_snapshot() -> set[Path]:
    root = _generated_root()
    if not root.is_dir():
        return set()
    return {p for p in root.rglob("*") if p.is_file() and p.suffix.casefold() in IMAGE_SUFFIXES}


def _single_new_generated(before: set[Path]) -> Path | None:
    """The one image Codex added during this run; None if zero or several (another session may be running)."""
    new = sorted(_generated_snapshot() - before)
    return new[0] if len(new) == 1 else None


def _classify(text: str) -> AdapterFailure:
    low = text.lower()
    if "moderation_blocked" in low or "safety" in low and "block" in low:
        # desk rule: never resend a blocked prompt unchanged; change the composition first.
        return AdapterFailure("codex image was blocked by the content filter", category="provider_response",
                              code="moderation_blocked")
    if "usage limit" in low or "rate limit" in low or "too many requests" in low:
        return AdapterFailure("codex usage limit reached", category="rate_limit", code="usage_limit",
                              retryable=True)
    if "not logged in" in low or "login" in low and "required" in low:
        return AdapterFailure("codex is not logged in", category="authentication", code="codex_login")
    return AdapterFailure("codex did not produce an image", code="no_image")


def run(job: Mapping[str, Any]) -> Path:
    if pa._collect_target(job):
        raise AdapterFailure("codex image jobs cannot be collected; rerun with a new confirmation",
                             category="configuration", code="collect_unsupported")
    prompt, _, name = compile_codex_prompt(job)
    refs = pa._reference_paths(job)
    for path in refs:  # bytes must match the extension before we attach them
        try:
            pa._validate_media_content(path.name, pa._read_reference(path))
        except AdapterFailure as exc:
            raise AdapterFailure("a reference image's bytes do not match its extension",
                                 category="configuration", code="reference_type_mismatch") from exc
    out_dir = pa._output_root(job)
    cmd = [_codex_bin(), "exec", "--skip-git-repo-check", "-C", str(out_dir), "-s", "workspace-write"]
    cmd += [f"--image={p}" for p in refs] + ["--", prompt]
    before = _generated_snapshot()
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=float(os.environ.get("CODEX_IMAGE_TIMEOUT", "900")), check=False)
    except subprocess.TimeoutExpired as exc:
        raise AdapterFailure("codex image generation timed out", category="timeout",
                             code="codex_timeout", retryable=True) from exc
    except OSError as exc:
        raise AdapterFailure("codex could not be started", category="configuration",
                             code="codex_start_failed") from exc
    produced = out_dir / name
    if not produced.is_file():
        stray = [p for p in out_dir.iterdir() if p.is_file() and p.suffix.casefold() in IMAGE_SUFFIXES]
        produced = stray[0] if len(stray) == 1 else None
    if produced is None:
        found = _single_new_generated(before)
        if found is None:
            raise _classify((done.stdout or "") + (done.stderr or ""))
        produced = out_dir / ("generated" + found.suffix.casefold())
        shutil.copyfile(found, produced)
    target = job["outputs"][0]
    want = Path(target).suffix.casefold()
    have = produced.suffix.casefold()
    final = out_dir / ("final" + want)
    if want == have or {want, have} <= {".jpg", ".jpeg"}:
        shutil.copyfile(produced, final)
    else:
        fmt = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": None}[want]
        if fmt is None or not shutil.which("sips"):
            raise AdapterFailure("codex returned a different image format", code="unexpected_media_type")
        conv = subprocess.run(["sips", "-s", "format", fmt, str(produced), "--out", str(final)],
                              capture_output=True, text=True, check=False)
        if conv.returncode != 0 or not final.is_file():
            raise AdapterFailure("image format conversion failed", code="conversion_failed")
    with final.open("rb") as handle:
        try:
            pa._validate_media_content(target, handle.read(16))
        except AdapterFailure as exc:
            raise AdapterFailure("codex returned an unexpected media type", code="unexpected_media_type") from exc
    for leftover in out_dir.iterdir():  # contract: only our result stays in output_root
        if leftover != final and leftover.is_file():
            leftover.unlink()
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex CLI image adapter for short-drama-produce")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        from codex_image_adapter_selftest import selftest
        selftest()
        return 0
    try:
        job = json.load(sys.stdin.buffer)
        if not isinstance(job, Mapping):
            raise ValueError("adapter input must be an object")
        path = run(job)
        json.dump({"outputs": [{"target": job["outputs"][0], "source": str(path)}]}, sys.stdout, ensure_ascii=True)
        return 0
    except AdapterFailure as exc:
        json.dump({"error": exc.public(PROVIDER)}, sys.stdout, ensure_ascii=True)
        print("codex image adapter failed safely", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        json.dump({"error": {"provider": PROVIDER, "category": "invalid_request", "code": "invalid_job",
                             "retryable": False}}, sys.stdout, ensure_ascii=True)
        print(f"codex image adapter rejected the job: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
