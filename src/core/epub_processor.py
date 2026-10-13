from __future__ import annotations

import tempfile
import shutil
from datetime import datetime
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Dict, List, Optional, Tuple
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from rich.console import Console

from src.utils.fs import iter_files, open_unique_output
from src.utils.system_info import collect_system_info

from .compression_result import CompressionResult
from .image_compressor import SUPPORTED_IMAGE_EXTENSIONS, compress_image_file
from .video_compressor import SUPPORTED_VIDEO_EXTENSIONS, compress_video_file

console = Console()

DEFAULT_IMAGE_QUALITY = 85
DEFAULT_VIDEO_CRF = 23

IMAGE_EXTS = {ext.lower() for ext in SUPPORTED_IMAGE_EXTENSIONS}
VIDEO_EXTS = {ext.lower() for ext in SUPPORTED_VIDEO_EXTENSIONS}


def _normalize_video_fallback_crfs(
    candidates: Optional[Tuple[int, ...]],
    requested_crf: int,
) -> Tuple[int, ...]:
    normalized: list[int] = []
    if not candidates:
        return tuple()
    for raw in candidates:
        if raw is None:
            continue
        value = int(max(0, min(int(raw), 51)))
        if value >= requested_crf:
            continue
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


@dataclass(slots=True)
class ArchiveProcessResult:
    input_path: Path
    output_path: Path
    image_results: List[CompressionResult]
    video_results: List[CompressionResult]
    error_results: List[CompressionResult]
    image_quality: int
    video_crf: int
    video_crf_fallbacks: Tuple[int, ...]
    prefer_gpu: bool
    requested_encoder: Optional[str]
    log_path: Optional[Path] = None
    original_archive_size: int = 0
    final_archive_size: int = 0
    processing_seconds: float = 0.0
    media_saved_bytes: int = 0

    @property
    def compressed_images(self) -> List[CompressionResult]:
        return [r for r in self.image_results if r.changed]

    @property
    def kept_images(self) -> List[CompressionResult]:
        return [r for r in self.image_results if not r.changed and r.reason == "no-gain"]

    @property
    def ignored_images(self) -> List[CompressionResult]:
        return [r for r in self.image_results if not r.changed and r.reason not in (None, "no-gain")]

    @property
    def compressed_videos(self) -> List[CompressionResult]:
        return [r for r in self.video_results if r.changed]

    @property
    def total_saved_bytes(self) -> int:
        return sum(r.saved_bytes for r in self.compressed_images + self.compressed_videos)


def process_epub_zip(
    input_zip: Path,
    image_quality: int = DEFAULT_IMAGE_QUALITY,
    video_crf: int = DEFAULT_VIDEO_CRF,
    fallback_crfs: Optional[Tuple[int, ...]] = None,
    prefer_gpu: bool = True,
    encoder: Optional[str] = None,
) -> ArchiveProcessResult:
    """
    Processes an `.epub.zip` (or plain `.zip`) file by compressing the media contained inside.
    """
    start_time = time.perf_counter()
    input_zip = Path(input_zip)
    if not input_zip.exists():
        raise FileNotFoundError(f"Input file not found: {input_zip}")

    if input_zip.suffix.lower() != ".zip":
        console.print(f"[yellow]Warning:[/] {input_zip.name} does not end with .zip, continuing anyway.")

    original_archive_size = input_zip.stat().st_size if input_zip.exists() else 0

    with tempfile.TemporaryDirectory(prefix="vcdesktop-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        extract_dir = tmpdir_path / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        console.print(f"Extracting archive: {input_zip}")
        extract_errors: List[CompressionResult] = []
        with ZipFile(input_zip, "r") as zf:
            extract_errors = _safe_extractall(zf, extract_dir, input_zip)

        effective_fallbacks = _normalize_video_fallback_crfs(fallback_crfs, video_crf)

        summary = _process_extracted_content(
            extract_dir,
            image_quality=image_quality,
            video_crf=video_crf,
            fallback_crfs=effective_fallbacks,
            prefer_gpu=prefer_gpu,
            encoder=encoder,
        )
        if extract_errors:
            summary.setdefault("errors", []).extend(extract_errors)

        final_zip = _build_output_path(input_zip)
        console.print(f"Writing final archive: {final_zip}")
        final_zip = make_zip_from_dir(extract_dir, final_zip)

    processing_seconds = time.perf_counter() - start_time
    final_archive_size = final_zip.stat().st_size if final_zip.exists() else 0

    result = ArchiveProcessResult(
        input_path=input_zip,
        output_path=final_zip,
        image_results=summary.get("images", []),
        video_results=summary.get("videos", []),
        error_results=summary.get("errors", []),
        image_quality=image_quality,
        video_crf=video_crf,
        video_crf_fallbacks=effective_fallbacks,
        prefer_gpu=prefer_gpu,
        requested_encoder=encoder,
        original_archive_size=original_archive_size,
        final_archive_size=final_archive_size,
        processing_seconds=processing_seconds,
    )
    result.media_saved_bytes = result.total_saved_bytes
    _print_summary(result)
    return result


def _process_extracted_content(
    root: Path,
    image_quality: int,
    video_crf: int,
    fallback_crfs: Optional[Tuple[int, ...]],
    prefer_gpu: bool,
    encoder: Optional[str],
) -> Dict[str, List[CompressionResult]]:
    results: Dict[str, List[CompressionResult]] = {"images": [], "videos": [], "errors": []}

    epub_files = sorted(root.rglob("*.epub"))

    if epub_files:
        for epub_file in epub_files:
            console.print(f"Processing EPUB: {epub_file.relative_to(root)}")
            with tempfile.TemporaryDirectory(prefix="vcdesktop-") as epub_tmp:
                epub_tmp_path = Path(epub_tmp) / "content"
                epub_tmp_path.mkdir(parents=True, exist_ok=True)

                with ZipFile(epub_file, "r") as epub_zf:
                    epub_errors = _safe_extractall(epub_zf, epub_tmp_path, epub_file)
                if epub_errors:
                    # Keep the original EPUB rather than replace it with incomplete content.
                    results["errors"].extend(epub_errors)
                    continue

                dir_results = _compress_media_in_dir(
                    epub_tmp_path, image_quality, video_crf, fallback_crfs, prefer_gpu, encoder
                )
                _merge_results(results, dir_results)

                rebuilt_epub = Path(epub_tmp) / epub_file.name
                make_epub_archive(epub_tmp_path, rebuilt_epub)
                rebuilt_epub.replace(epub_file)
    else:
        console.print("No .epub file found inside the archive. Compressing extracted content directly.")
        dir_results = _compress_media_in_dir(
            root, image_quality, video_crf, fallback_crfs, prefer_gpu, encoder
        )
        _merge_results(results, dir_results)

    return results


def _compress_media_in_dir(
    root: Path,
    image_quality: int,
    video_crf: int,
    fallback_crfs: Optional[Tuple[int, ...]],
    prefer_gpu: bool,
    encoder: Optional[str],
) -> Dict[str, List[CompressionResult]]:
    results: Dict[str, List[CompressionResult]] = {"images": [], "videos": [], "errors": []}

    for file in iter_files(root):
        ext = file.suffix.lower()
        relative = file.relative_to(root)
        try:
            if ext in IMAGE_EXTS:
                console.print(f"Comprimindo imagem: {relative}")
                result = compress_image_file(file, quality=image_quality)
                result.relative_path = relative
                result.temp_path = file
                if not result.changed and result.reason == "no-gain":
                    console.print(f"[yellow]Imagem mantida (sem ganho):[/] {relative}")
                results["images"].append(result)
            elif ext in VIDEO_EXTS:
                console.print(f"Comprimindo video: {relative}")
                result = compress_video_file(
                    file,
                    crf=video_crf,
                    fallback_crfs=fallback_crfs,
                    encoder=encoder,
                    prefer_gpu=prefer_gpu,
                )
                result.relative_path = relative
                result.temp_path = file
                if result.encoder and result.encoder != "libx264":
                    console.print(f"[blue]Encoder utilizado: {result.encoder}[/]")
                results["videos"].append(result)
        except Exception as exc:  # pragma: no cover - safety log
            console.print(f"[red]Failed to compress {file}:[/] {exc}")
            results["errors"].append(
                CompressionResult(file, False, 0, 0, reason=str(exc), relative_path=relative)
            )

    return results


def _merge_results(
    accumulator: Dict[str, List[CompressionResult]],
    new_results: Dict[str, List[CompressionResult]],
) -> None:
    for key in ("images", "videos", "errors"):
        accumulator.setdefault(key, []).extend(new_results.get(key, []))


def _safe_extractall(zf: ZipFile, destination: Path, archive_path: Path) -> List[CompressionResult]:
    errors: List[CompressionResult] = []
    destination = destination.resolve()
    for info in zf.infolist():
        member_path = None
        created = False
        try:
            name = info.filename.replace("\\", "/")
            relative = Path(name)
            if (PureWindowsPath(name).drive or relative.is_absolute()
                    or ".." in relative.parts or any(":" in part for part in relative.parts)):
                raise ValueError("caminho fora da pasta de extracao")
            member_path = (destination / relative).resolve()
            if not member_path.is_relative_to(destination) or member_path == destination:
                raise ValueError("caminho fora da pasta de extracao")
            if info.is_dir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with member_path.open("xb") as output:
                created = True
                with zf.open(info) as original:
                    shutil.copyfileobj(original, output)
        except Exception as exc:
            if created and member_path is not None:
                try:
                    member_path.unlink()
                except OSError:
                    pass
            console.print(f"[yellow]Falha ao extrair '{info.filename}':[/] {exc}")
            errors.append(
                CompressionResult(
                    source=archive_path,
                    changed=False,
                    original_size=getattr(info, "file_size", 0),
                    new_size=getattr(info, "file_size", 0),
                    reason=f"falha ao extrair '{info.filename}': {exc}",
                    relative_path=Path(info.filename),
                )
            )
    return errors


def _build_output_path(input_zip: Path) -> Path:
    name = input_zip.name
    parent = input_zip.parent

    if name.endswith(".epub.zip"):
        base = name[: -len(".epub.zip")]
        return parent / f"{base}.compressed.epub.zip"
    if name.endswith(".zip"):
        base = name[: -len(".zip")]
        return parent / f"{base}.compressed.zip"
    return parent / f"{name}.compressed.zip"


def make_epub_archive(folder: Path, out_epub: Path) -> None:
    folder = Path(folder)
    out_epub = Path(out_epub)

    with ZipFile(out_epub, "w") as zf:
        mimetype_path = folder / "mimetype"
        if mimetype_path.exists():
            zf.write(mimetype_path, "mimetype", compress_type=ZIP_STORED)

        for file in folder.rglob("*"):
            if not file.is_file():
                continue
            if file == mimetype_path:
                continue
            arcname = file.relative_to(folder)
            zf.write(file, arcname, compress_type=ZIP_DEFLATED)


def make_zip_from_dir(folder: Path, out_zip: Path) -> Path:
    folder = Path(folder)
    out_zip = Path(out_zip)
    final_zip, output = open_unique_output(out_zip)
    try:
        with output, ZipFile(output, "w", compression=ZIP_DEFLATED) as zf:
            for file in folder.rglob("*"):
                if not file.is_file():
                    continue
                arcname = file.relative_to(folder)
                zf.write(file, arcname)
    except Exception:
        final_zip.unlink(missing_ok=True)
        raise
    return final_zip


def _print_summary(result: ArchiveProcessResult) -> None:
    console.print(
        f"[green]Done.[/] Saved {format_bytes(result.total_saved_bytes)} across "
        f"{len(result.compressed_images)} image(s) and {len(result.compressed_videos)} video(s)."
    )
    if result.video_results:
        encoders_used = sorted({(r.encoder or "libx264") for r in result.video_results})
        console.print(f"Encoders de video utilizados: {', '.join(encoders_used)}")
    if result.error_results:
        console.print(f"[yellow]Skipped {len(result.error_results)} file(s) due to errors.[/]")
    console.print(f"Output: {result.output_path}")


def write_result_log(result: ArchiveProcessResult, log_path: Optional[Path] = None) -> Path:
    log_path = Path(log_path) if log_path else result.output_path.with_suffix(".log.txt")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _format_percent(saved: int, base: int) -> str:
        if base <= 0 or saved <= 0:
            return "0.0%"
        percent = (saved / base) * 100
        return f"{percent:.1f}%"

    original_archive_size = result.original_archive_size
    if original_archive_size <= 0 and result.input_path.exists():
        try:
            original_archive_size = result.input_path.stat().st_size
        except OSError:
            original_archive_size = 0
        else:
            result.original_archive_size = original_archive_size

    final_archive_size = result.final_archive_size
    if final_archive_size <= 0 and result.output_path.exists():
        try:
            final_archive_size = result.output_path.stat().st_size
        except OSError:
            final_archive_size = 0
        else:
            result.final_archive_size = final_archive_size

    archive_saved = max(0, original_archive_size - final_archive_size)

    image_saved = sum(item.saved_bytes for item in result.compressed_images)
    image_original = sum(item.original_size for item in result.compressed_images)

    video_saved = sum(item.saved_bytes for item in result.compressed_videos)
    video_original = sum(item.original_size for item in result.compressed_videos)

    media_original_total = image_original + video_original

    encoders_used = sorted({(r.encoder or "libx264") for r in result.video_results if r.encoder or r.changed})
    requested_encoder = result.requested_encoder or "auto"
    prefer_gpu = "sim" if result.prefer_gpu else "nao"

    lines: List[str] = []
    lines.append("Resumo do processamento:")
    lines.append(f"- Input: {result.input_path}")
    lines.append(f"- Output: {result.output_path}")
    lines.append(f"- Tamanho original do arquivo: {format_bytes(original_archive_size)}")
    lines.append(f"- Tamanho final do arquivo: {format_bytes(final_archive_size)}")
    lines.append(
        f"- Reducao do arquivo final: {format_bytes(archive_saved)} "
        f"({_format_percent(archive_saved, original_archive_size)})"
    )
    lines.append(
        f"- Economias em midias recomprimidas: {format_bytes(result.total_saved_bytes)} "
        f"({_format_percent(result.total_saved_bytes, media_original_total)})"
    )
    lines.append(
        f"- Parametros de compressao: imagens qualidade={result.image_quality}; "
        f"videos CRF={result.video_crf}"
        + (
            f" (alternativas: {', '.join(str(value) for value in result.video_crf_fallbacks)})"
            if result.video_crf_fallbacks
            else ""
        )
        + f"; encoder requisitado={requested_encoder}; preferencia GPU={prefer_gpu}"
    )
    if encoders_used:
        lines.append(f"- Encoders de video efetivos: {', '.join(encoders_used)}")
    lines.append(
        f"- Parametros e reducao (imagens): qualidade alvo {result.image_quality} "
        f"com reducao media {_format_percent(image_saved, image_original)}"
    )
    lines.append(
        f"- Parametros e reducao (videos): CRF {result.video_crf} "
        f"com reducao media {_format_percent(video_saved, video_original)}"
    )
    lines.append("")

    lines.append(f"Total imagens: {len(result.image_results)}")
    lines.append(f"Total videos: {len(result.video_results)}")
    lines.append(f"Total imagens comprimidas: {len(result.compressed_images)}")
    lines.append(f"Total imagens mantidas (sem ganho): {len(result.kept_images)}")
    lines.append(f"Economia total: {format_bytes(result.total_saved_bytes)}")
    lines.append("")

    lines.append("Imagens comprimidas:")
    if result.compressed_images:
        for item in result.compressed_images:
            rel = item.relative_path or item.source
            lines.append(
                f"- {rel} | {format_bytes(item.original_size)} -> {format_bytes(item.new_size)} "
                f"(economia {format_bytes(item.saved_bytes)}) "
                f"| reducao {_format_percent(item.saved_bytes, item.original_size)}"
            )
    else:
        lines.append("- Nenhuma")

    lines.append("")
    lines.append("Imagens mantidas (sem ganho):")
    if result.kept_images:
        for item in result.kept_images:
            rel = item.relative_path or item.source
            lines.append(f"- {rel} | motivo: {item.reason or 'sem ganho'}")
    else:
        lines.append("- Nenhuma")

    if result.ignored_images:
        lines.append("")
        lines.append("Imagens ignoradas:")
        for item in result.ignored_images:
            rel = item.relative_path or item.source
            lines.append(f"- {rel} | motivo: {item.reason or 'desconhecido'}")

    lines.append("")
    lines.append("Videos comprimidos:")
    if result.compressed_videos:
        for item in result.compressed_videos:
            rel = item.relative_path or item.source
            encoder_name = item.encoder or "libx264"
            requested_crf = item.video_crf_requested
            used_crf = item.video_crf_used or requested_crf or result.video_crf
            if item.used_fallback_params and requested_crf is not None:
                crf_label = f"{used_crf} (fallback de {requested_crf})"
            else:
                crf_label = str(used_crf) if used_crf is not None else "desconhecido"
            lines.append(
                f"- {rel} | {format_bytes(item.original_size)} -> {format_bytes(item.new_size)} "
                f"(economia {format_bytes(item.saved_bytes)}) | encoder: {encoder_name} "
                f"| crf: {crf_label} "
                f"| reducao {_format_percent(item.saved_bytes, item.original_size)}"
            )
    else:
        lines.append("- Nenhum")

    fallback_videos = [
        item
        for item in result.video_results
        if item.used_fallback_params and item.video_crf_used is not None
    ]
    if fallback_videos:
        lines.append("")
        lines.append("Videos com CRF alternativo:")
        for item in fallback_videos:
            rel = item.relative_path or item.source
            requested_crf = item.video_crf_requested or "desconhecido"
            used_crf = item.video_crf_used or "desconhecido"
            lines.append(f"- {rel} | solicitado {requested_crf} -> usado {used_crf}")

    if result.error_results:
        lines.append("")
        lines.append("Erros:")
        for item in result.error_results:
            rel = item.relative_path or item.source
            lines.append(f"- {rel} | motivo: {item.reason}")

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result.log_path = log_path
    return log_path


def write_batch_summary_log(
    results: List[ArchiveProcessResult],
    duration_seconds: float,
    log_dir: Optional[Path] = None,
    failures: Optional[List[Tuple[Path, Exception]]] = None,
) -> Path:
    if not results:
        raise ValueError("Nenhum resultado fornecido para gerar resumo.")

    base_dir = Path(log_dir) if log_dir else results[0].output_path.parent
    base_dir = base_dir.expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary_path = base_dir / f"batch_summary_{timestamp}.log.txt"

    total_original = 0
    total_final = 0
    total_media_saved = 0
    total_processing_seconds = 0.0
    for result in results:
        original_size = result.original_archive_size
        if original_size <= 0 and result.input_path.exists():
            try:
                original_size = result.input_path.stat().st_size
            except OSError:
                original_size = 0
            else:
                result.original_archive_size = original_size
        total_original += original_size

        final_size = result.final_archive_size
        if final_size <= 0 and result.output_path.exists():
            try:
                final_size = result.output_path.stat().st_size
            except OSError:
                final_size = 0
            else:
                result.final_archive_size = final_size
        total_final += final_size

        media_saved = result.media_saved_bytes or result.total_saved_bytes
        result.media_saved_bytes = media_saved
        total_media_saved += media_saved

        total_processing_seconds += result.processing_seconds

    total_saved = max(0, total_original - total_final)

    archives_with_errors = [result for result in results if result.error_results]
    total_error_files = sum(len(result.error_results) for result in archives_with_errors)
    fallback_entries: List[Tuple[ArchiveProcessResult, CompressionResult]] = []
    for aggregate in results:
        for item in aggregate.video_results:
            if item.used_fallback_params and item.video_crf_used is not None:
                fallback_entries.append((aggregate, item))

    def _format_duration(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    system_info = collect_system_info()
    memory_bytes = system_info.get("memory_bytes")
    if isinstance(memory_bytes, int):
        memory_line = format_bytes(memory_bytes)
    else:
        memory_line = "desconhecida (instale psutil para detalhar)"

    lines: List[str] = []
    lines.append("Resumo geral do processamento:")
    lines.append(f"- Arquivos processados: {len(results)}")
    failure_entries = failures or []
    lines.append(f"- Arquivos com erro parcial: {len(archives_with_errors)}")
    lines.append(f"- Falhas totais (nao processados): {len(failure_entries)}")
    lines.append(f"- Tamanho original total: {format_bytes(total_original)}")
    lines.append(f"- Tamanho final total: {format_bytes(total_final)}")
    lines.append(f"- Reducao total: {format_bytes(total_saved)}")
    lines.append(f"- Economias em midias recomprimidas: {format_bytes(total_media_saved)}")
    lines.append(
        f"- Duracao total: {_format_duration(duration_seconds)} ({duration_seconds:.2f}s)"
    )
    if total_processing_seconds:
        lines.append(
            f"- Soma dos tempos individuais: {_format_duration(total_processing_seconds)} "
            f"({total_processing_seconds:.2f}s)"
        )
    if total_error_files:
        lines.append(f"- Arquivos com erro durante a compressao: {total_error_files}")
    lines.append("")
    lines.append("Configuracao do sistema:")
    lines.append(f"- Sistema: {system_info.get('os', 'desconhecido')}")
    lines.append(f"- Maquina: {system_info.get('machine', 'desconhecida')}")
    lines.append(f"- Processador: {system_info.get('processor', 'desconhecido')}")
    lines.append(f"- CPUs logicas: {system_info.get('cpu_count', 'desconhecido')}")
    lines.append(f"- Memoria RAM: {memory_line}")
    lines.append(f"- Python: {system_info.get('python', 'desconhecido')}")

    if failure_entries:
        lines.append("")
        lines.append("Arquivos que falharam totalmente:")
        for path, error in failure_entries:
            lines.append(f"- {path} -> {error}")

    if archives_with_errors:
        lines.append("")
        lines.append("Arquivos com erros parciais:")
        for result in archives_with_errors:
            lines.append(f"- {result.output_path} ({len(result.error_results)} erro(s))")

    if fallback_entries:
        lines.append("")
        lines.append("Videos que usaram CRF alternativo:")
        for result, item in fallback_entries:
            rel = item.relative_path or item.source
            requested = item.video_crf_requested or "desconhecido"
            used = item.video_crf_used or "desconhecido"
            lines.append(f"- {result.output_path.name}: {rel} | solicitado {requested} -> usado {used}")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def format_bytes(num_bytes: int) -> str:
    step = 1024.0
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(num_bytes)
    for unit in units:
        if size < step:
            return f"{size:.1f}{unit}"
        size /= step
    return f"{size:.1f}PiB"
