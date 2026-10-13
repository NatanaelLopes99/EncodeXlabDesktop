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
NVENC_HWACCEL_PRIORITY: Tuple[str, ...] = ("cuda",)

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


class FFmpegCompressionError(RuntimeError):
    def __init__(self, message: str, encoder: str, stderr: Optional[str] = None):
        super().__init__(message)
        self.encoder = encoder
        self.stderr = stderr


def _is_auto_encoder(requested: Optional[str]) -> bool:
    if requested is None:
        return True
    normalized = requested.strip().lower()
    return normalized in ("", "auto")


def _is_nvenc_invalid_argument_error(error: FFmpegCompressionError) -> bool:
    combined = f"{error}\n{error.stderr or ''}"
    triggers = (
        "Invalid argument",
        "Could not open encoder",
        "Nothing was written into output file",
    )
    return any(trigger in combined for trigger in triggers)


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
    fallback_crfs: Optional[Tuple[int, ...]] = None,
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
    try:
        return _compress_video_file_once(
            path=path,
            crf=crf,
            fallback_crfs=fallback_crfs,
            preset=preset,
            audio_bitrate=audio_bitrate,
            encoder=encoder,
            prefer_gpu=prefer_gpu,
        )
    except FFmpegCompressionError as error:
        fallback_allowed = prefer_gpu and _is_auto_encoder(encoder)
        if (
            fallback_allowed
            and error.encoder == "h264_nvenc"
            and _is_nvenc_invalid_argument_error(error)
        ):
            console.print(
                "[yellow]NVENC falhou com 'Invalid argument'; tentando novamente com libx264.[/]"
            )
            return _compress_video_file_once(
                path=path,
                crf=crf,
                fallback_crfs=fallback_crfs,
                preset=preset,
                audio_bitrate=audio_bitrate,
                encoder=encoder,
                prefer_gpu=False,
            )
        raise


def _compress_video_file_once(
    path: Path,
    crf: int = 23,
    fallback_crfs: Optional[Tuple[int, ...]] = None,
    preset: str = "medium",
    audio_bitrate: Optional[str] = None,
    encoder: Optional[str] = None,
    prefer_gpu: bool = True,
) -> CompressionResult:
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

    requested_crf = int(max(0, min(int(crf), 51)))
    normalized_fallbacks_list: list[int] = []
    if fallback_crfs:
        for raw_value in fallback_crfs:
            if raw_value is None:
                continue
            normalized_value = int(max(0, min(int(raw_value), 51)))
            if normalized_value >= requested_crf:
                continue
            if normalized_value not in normalized_fallbacks_list:
                normalized_fallbacks_list.append(normalized_value)
    normalized_fallbacks = tuple(normalized_fallbacks_list)

    crf_sequence: list[int] = []
    for candidate in (requested_crf,) + normalized_fallbacks:
        if candidate not in crf_sequence:
            crf_sequence.append(candidate)
    if not crf_sequence:
        crf_sequence.append(requested_crf)

    original_size = path.stat().st_size
    audio_mode = "auto" if audio_bitrate is None else audio_bitrate.strip().lower()
    copy_audio_requested = audio_mode in ("auto", "copy", "")
    allow_copy_fallback = audio_mode in ("auto", "")
    target_audio_bitrate = (
        audio_bitrate.strip() if audio_bitrate and not copy_audio_requested else DEFAULT_AUDIO_BITRATE
    )

    with tempfile.TemporaryDirectory(prefix="vcdesktop-") as tmpdir:
        tmp_output = Path(tmpdir) / path.name

        def _build_cmd(copy_audio_flag: bool, current_crf: int) -> list[str]:
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
                        str(current_crf),
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
                        str(current_crf),
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
        last_process: Optional[subprocess.CompletedProcess[str]] = None
        used_crf: Optional[int] = None
        fallback_used = False
        stderr_reports: list[str] = []

        for index, candidate_crf in enumerate(crf_sequence):
            candidate_process: Optional[subprocess.CompletedProcess[str]] = None
            for copy_audio_flag in attempts:
                cmd = _build_cmd(copy_audio_flag, candidate_crf)
                candidate_process = subprocess.run(cmd, capture_output=True, text=True)
                if candidate_process.returncode == 0:
                    process = candidate_process
                    used_crf = candidate_crf
                    fallback_used = index > 0
                    break
                if copy_audio_flag and allow_copy_fallback:
                    console.print(
                        "[yellow]Nao foi possivel copiar o audio diretamente; recomprimindo para AAC.[/]"
                    )
                    continue
                break

            last_process = candidate_process or last_process

            if process and process.returncode == 0:
                break

            if candidate_process is not None and candidate_process.returncode != 0:
                stderr_tail = (candidate_process.stderr or "")[-FFMPEG_ERROR_TAIL:]
                stderr_reports.append(f"CRF {candidate_crf}: {stderr_tail}")
                next_index = index + 1
                if next_index < len(crf_sequence):
                    next_crf = crf_sequence[next_index]
                    console.print(
                        f"[yellow]Nao foi possivel comprimir {path.name} com CRF {candidate_crf}; tentando CRF {next_crf}.[/]"
                    )

        if process is None or process.returncode != 0 or used_crf is None:
            last_code = last_process.returncode if last_process else "unknown"
            stderr_tail = (last_process.stderr if last_process else "") or ""
            stderr_tail = stderr_tail[-FFMPEG_ERROR_TAIL:]
            attempts_list = ", ".join(str(value) for value in crf_sequence)
            details = stderr_tail
            if stderr_reports:
                details = " | ".join(stderr_reports[-3:])
            raise FFmpegCompressionError(
                f"FFmpeg falhou (codigo {last_code}) apos tentar CRFs [{attempts_list}]: {details}",
                encoder=selected_encoder,
                stderr=stderr_tail,
            )

        if not tmp_output.exists():
            return CompressionResult(
                path,
                False,
                original_size,
                original_size,
                reason="no-output",
                encoder=selected_encoder,
                video_crf_requested=requested_crf,
                video_crf_used=used_crf,
                used_fallback_params=fallback_used,
            )

        new_size = tmp_output.stat().st_size

        if new_size >= original_size or new_size == 0:
            return CompressionResult(
                path,
                False,
                original_size,
                original_size,
                reason="no-gain",
                encoder=selected_encoder,
                video_crf_requested=requested_crf,
                video_crf_used=used_crf,
                used_fallback_params=fallback_used,
                notes="crf-fallback"
                if fallback_used
                else None,
            )

        tmp_output.replace(path)
        note_text = None
        if fallback_used and used_crf is not None:
            note_text = f"crf-fallback:{requested_crf}->{used_crf}"
        return CompressionResult(
            path,
            True,
            original_size,
            new_size,
            encoder=selected_encoder,
            video_crf_requested=requested_crf,
            video_crf_used=used_crf,
            used_fallback_params=fallback_used,
            notes=note_text,
        )
