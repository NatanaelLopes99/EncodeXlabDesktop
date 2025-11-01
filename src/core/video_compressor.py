from __future__ import annotations

import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

from rich.console import Console

from .compression_result import CompressionResult

console = Console()

SUPPORTED_VIDEO_EXTENSIONS: Tuple[str, ...] = (".mp4", ".m4v", ".mov")
CPU_ENCODER = "libx264"
GPU_ENCODER_PRIORITY: Tuple[str, ...] = ("h264_nvenc",)
KNOWN_ENCODERS = set(GPU_ENCODER_PRIORITY + (CPU_ENCODER,))
NVENC_HWACCEL_PRIORITY: Tuple[str, ...] = ("cuda", "dxva2", "d3d11va")

NVENC_PRESET_MAP = {
    "ultrafast": "p1",
    "superfast": "p2",
    "veryfast": "p3",
    "faster": "p3",
    "fast": "p4",
    "medium": "p5",
    "slow": "p6",
    "slower": "p6",
    "veryslow": "p7",
}

DEFAULT_AUDIO_BITRATE = "128k"
FFMPEG_ERROR_TAIL = 600


def _ffmpeg_bin() -> str:
    return "ffmpeg"


def _ffprobe_bin() -> str:
    return "ffprobe"


def has_ffmpeg() -> bool:
    return shutil.which(_ffmpeg_bin()) is not None and shutil.which(_ffprobe_bin()) is not None


@lru_cache(maxsize=1)
def _available_ffmpeg_encoders() -> set[str]:
    if not has_ffmpeg():
        return set()

    cmd = [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-encoders"]
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return set()

    encoders: set[str] = set()
    output = (process.stdout or "") + (process.stderr or "")
    for encoder in KNOWN_ENCODERS:
        if encoder in output:
            encoders.add(encoder)
    
    encoders.add(CPU_ENCODER)
    return encoders


@lru_cache(maxsize=1)
def _available_ffmpeg_hwaccels() -> set[str]:
    if not has_ffmpeg():
        return set()

    cmd = [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-hwaccels"]
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return set()

    hwaccels: set[str] = set()
    output = (process.stdout or "") + (process.stderr or "")
    for raw_line in output.splitlines():
        line = raw_line.strip().lower()
        if not line or "hardware acceleration methods" in line:
            continue
        hwaccels.add(line)
    return hwaccels


def _select_encoder(requested: Optional[str], prefer_gpu: bool) -> Tuple[str, str]:
    available = _available_ffmpeg_encoders()
    normalized: Optional[str] = requested.lower() if requested else None

    if normalized and normalized not in KNOWN_ENCODERS and normalized != "auto":
        raise ValueError(f"Encoder desconhecido: {requested}")

    if normalized and normalized != "auto":
        if normalized in available:
            return normalized, "manual"
        console.print(f"[yellow]Encoder solicitado '{requested}' indisponivel. Usando CPU (libx264).[/]")
        return CPU_ENCODER, "fallback"

    if prefer_gpu:
        for candidate in GPU_ENCODER_PRIORITY:
            if candidate in available:
                return candidate, "auto-gpu"

    return CPU_ENCODER, "auto-cpu"


def _map_nvenc_preset(preset: str) -> str:
    return NVENC_PRESET_MAP.get(preset.lower(), "p5")


def _select_hwaccel(encoder: str) -> Optional[str]:
    if encoder != "h264_nvenc":
        return None

    available = _available_ffmpeg_hwaccels()
    for candidate in NVENC_HWACCEL_PRIORITY:
        if candidate in available:
            return candidate
    return None


def compress_video_file(
    path: Path,
    crf: int = 23,
    preset: str = "medium",
    audio_bitrate: Optional[str] = None,
    encoder: Optional[str] = None,
    prefer_gpu: bool = True,
) -> CompressionResult:
    """
    Re-encodes a supported video and keeps the new version only when it is smaller.

    When prefer_gpu is True, the function attempts to use a hardware encoder such as NVENC.
    Audio streams are copied whenever possible, recompressing to AAC only when necessary.
    """
    path = Path(path)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_VIDEO_EXTENSIONS:
        return CompressionResult(path, False, 0, 0, reason="unsupported-format")

    if not path.exists():
        return CompressionResult(path, False, 0, 0, reason="missing-file")

    if not has_ffmpeg():
        raise RuntimeError("FFmpeg/ffprobe not found on PATH. Please install FFmpeg and try again.")

    selected_encoder, _ = _select_encoder(encoder, prefer_gpu)
    hwaccel = _select_hwaccel(selected_encoder)

    crf = int(max(0, min(crf, 51)))
    original_size = path.stat().st_size
    audio_mode = "auto" if audio_bitrate is None else audio_bitrate.strip().lower()
    copy_audio_requested = audio_mode in ("auto", "copy", "")
    allow_copy_fallback = audio_mode in ("auto", "")
    target_audio_bitrate = (
        audio_bitrate.strip() if audio_bitrate and not copy_audio_requested else DEFAULT_AUDIO_BITRATE
    )

    with tempfile.TemporaryDirectory(prefix="vcdesktop-") as tmpdir:
        tmp_output = Path(tmpdir) / path.name

        def _build_cmd(copy_audio_flag: bool) -> list[str]:
            cmd = [
                _ffmpeg_bin(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
            ]
            if hwaccel:
                cmd.extend(["-hwaccel", hwaccel])
            cmd.extend(
                [
                    "-i",
                    str(path),
                    "-pix_fmt",
                    "yuv420p",
                ]
            )

            if selected_encoder == CPU_ENCODER:
                cmd.extend(
                    [
                        "-c:v",
                        CPU_ENCODER,
                        "-preset",
                        str(preset),
                        "-crf",
                        str(crf),
                    ]
                )
            elif selected_encoder == "h264_nvenc":
                nvenc_preset = _map_nvenc_preset(preset)
                cmd.extend(
                    [
                        "-c:v",
                        "h264_nvenc",
                        "-preset",
                        nvenc_preset,
                        "-b:v",
                        "0",
                        "-cq",
                        str(crf),
                    ]
                )
            else:
                raise ValueError(f"Encoder nao suportado: {selected_encoder}")

            if copy_audio_flag:
                cmd.extend(["-c:a", "copy"])
            else:
                cmd.extend(
                    [
                        "-acodec",
                        "aac",
                        "-b:a",
                        target_audio_bitrate,
                    ]
                )

            if ext in {".mp4", ".m4v"}:
                cmd.extend(["-movflags", "+faststart"])
            elif ext == ".mov":
                cmd.extend(["-f", "mov"])

            cmd.append(str(tmp_output))
            return cmd

        if copy_audio_requested:
            if allow_copy_fallback:
                attempts = (True, False)
            else:
                attempts = (True,)
        else:
            attempts = (False,)

        process: Optional[subprocess.CompletedProcess[str]] = None
        for copy_audio_flag in attempts:
            cmd = _build_cmd(copy_audio_flag)
            process = subprocess.run(cmd, capture_output=True, text=True)
            if process.returncode == 0:
                break
            if copy_audio_flag and allow_copy_fallback:
                console.print(
                    "[yellow]Nao foi possivel copiar o audio diretamente; recomprimindo para AAC.[/]"
                )
                continue
            break

        if process is None or process.returncode != 0:
            stderr_tail = (process.stderr if process else "") or ""
            stderr_tail = stderr_tail[-FFMPEG_ERROR_TAIL:]
            raise RuntimeError(
                f"FFmpeg exited with code {process.returncode if process else 'unknown'}: {stderr_tail}"
            )

        if not tmp_output.exists():
            return CompressionResult(path, False, original_size, original_size, reason="no-output")

        new_size = tmp_output.stat().st_size

        if new_size >= original_size or new_size == 0:
            return CompressionResult(path, False, original_size, original_size, reason="no-gain", encoder=selected_encoder)

        tmp_output.replace(path)
        return CompressionResult(path, True, original_size, new_size, encoder=selected_encoder)
