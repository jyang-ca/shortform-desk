"""Offline self-test for codex_image_adapter.py with a fake `codex` executable."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

def _png() -> bytes:
    import struct, zlib
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x80\x80\x80")) + chunk(b"IEND", b""))


PNG = _png()
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_CODEX_LOG"]
with open(log, "a") as f:
    f.write(json.dumps(args) + "\n")
mode = os.environ.get("FAKE_CODEX_MODE", "save")
cd = args[args.index("-C") + 1]
png = bytes.fromhex(os.environ["FAKE_PNG_HEX"])
if mode == "save":
    open(os.path.join(cd, "result.png"), "wb").write(png)
elif mode == "generated":
    d = os.path.join(os.environ["CODEX_GENERATED_DIR"], "sess")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "ig_x.png"), "wb").write(png)
elif mode == "blocked":
    print("error: moderation_blocked secret prompt echo")
    sys.exit(1)
elif mode == "limit":
    print("You've hit your usage limit. secret")
    sys.exit(1)
'''


def _job(tmp: Path, refs: list[tuple[str, bytes]], output: str, **parameters) -> dict:
    project = tmp / "project"
    out = tmp / f"out-{len(list(tmp.iterdir()))}"
    out.mkdir()
    paths, bindings = [], []
    for i, (name, content) in enumerate(refs, 1):
        p = project / "设定集" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        rel = str(p.relative_to(project))
        paths.append(rel)
        bindings.append({"slot_id": f"REF-{i}", "order": i, "path": rel, "label": f"参考{i}",
                         "role": "reference_image", "may_control": ["identity"], "must_not_control": ["lighting"]})
    project.mkdir(exist_ok=True)
    return {"job_id": "T", "modality": "image", "adapter": "codex-image", "prompt": "A candle on a table.",
            "reference_bindings": bindings, "references": paths, "outputs": [output],
            "parameters": {"prompt_language": "en", **parameters},
            "project_root": str(project), "output_root": str(out)}


def selftest() -> None:
    adapter = Path(__file__).with_name("codex_image_adapter.py")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        fake = tmp / "codex"
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        log = tmp / "log.jsonl"
        gen = tmp / "generated"
        gen.mkdir()
        base_env = {**os.environ, "FAKE_PNG_HEX": PNG.hex(), "CODEX_BIN": str(fake), "FAKE_CODEX_LOG": str(log), "CODEX_GENERATED_DIR": str(gen)}

        def call(job: dict, mode: str) -> tuple[int, dict, bytes]:
            r = subprocess.run([sys.executable, str(adapter)], input=json.dumps(job).encode(),
                               capture_output=True, env={**base_env, "FAKE_CODEX_MODE": mode})
            return r.returncode, json.loads(r.stdout), r.stdout + r.stderr

        # 1. saved in output_root, two references attached in order, ratio sentence, reference contract
        job = _job(tmp, [("c1.png", PNG), ("plate.png", PNG)], "剧集/EP003/制作成果/image/a.png", ratio="9:16")
        code, out, _ = call(job, "save")
        assert code == 0, out
        assert Path(out["outputs"][0]["source"]).read_bytes() == PNG
        args = json.loads(log.read_text().splitlines()[-1])
        assert args[:2] == ["exec", "--skip-git-repo-check"] and "workspace-write" in args
        images = [a for a in args if a.startswith("--image=")]
        assert len(images) == 2 and images[0].endswith("c1.png") and images[1].endswith("plate.png")
        prompt = args[-1]
        assert "built-in image generation tool" in prompt and "vertical 9:16 portrait" in prompt
        assert "Reference attached image 2 (参考2)" in prompt
        assert sorted(p.name for p in Path(job["output_root"]).iterdir()) == ["final.png"]

        # 2. fallback: image only in ~/.codex/generated_images
        code, out, _ = call(_job(tmp, [], "剧集/EP003/制作成果/image/b.png"), "generated")
        assert code == 0 and Path(out["outputs"][0]["source"]).read_bytes() == PNG

        # 3. png → jpg target converted (sips, macOS)
        if sys.platform == "darwin":
            code, out, _ = call(_job(tmp, [], "剧集/EP003/制作成果/image/c.jpg"), "save")
            assert code == 0 and Path(out["outputs"][0]["source"]).read_bytes()[:3] == b"\xff\xd8\xff", out

        # 4. moderation block / usage limit → whitelisted errors, no echo
        for mode, cat in (("blocked", "provider_response"), ("limit", "rate_limit")):
            code, out, raw_out = call(_job(tmp, [], "x.png"), mode)
            assert code == 1 and out["error"]["category"] == cat, out
            assert b"secret" not in raw_out

        # 5. validation: bad ratio, gif output, mislabeled reference
        code, out, _ = call(_job(tmp, [], "x.png", ratio="5:7"), "save")
        assert code == 1 and out["error"]["code"] == "invalid_job"
        code, out, _ = call(_job(tmp, [], "x.gif"), "save")
        assert code == 1 and out["error"]["code"] == "invalid_job"
        code, out, _ = call(_job(tmp, [("bad.png", b"GIF89a" + b"0" * 20)], "x.png"), "save")
        assert code == 1 and out["error"]["code"] == "reference_type_mismatch", out
    print("codex image adapter selftest: 5 scenarios passed")
