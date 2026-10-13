from __future__ import annotations

import argparse
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Iterable, List, Tuple

from rich.console import Console

from src.constants import ERROR_OUTPUT_DIR_NAME
from src.utils.fs import move_output_file
from src.core.epub_processor import (
    DEFAULT_IMAGE_QUALITY,
    DEFAULT_VIDEO_CRF,
    ArchiveProcessResult,
    process_epub_zip,
    write_result_log,
    write_batch_summary_log,
)

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compressao de midias (imagens e videos) contidas em EPUBs dentro de arquivos ZIP."
    )
    parser.add_argument(
        "--input",
        "-i",
        action="append",
        required=True,
        help="Arquivo(s) ou diretorio(s) .zip/.epub.zip para processar (repita a opcao para varios).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Arquivo de saida (quando houver um input) ou diretorio de destino para multiplos.",
    )
    parser.add_argument(
        "--image-quality",
        type=int,
        default=DEFAULT_IMAGE_QUALITY,
        help="Qualidade para JPEG/WEBP (0-100)",
    )
    parser.add_argument(
        "--video-crf",
        type=int,
        default=DEFAULT_VIDEO_CRF,
        help="CRF do video (libx264) recomendado 18-28",
    )
    parser.add_argument(
        "--video-crf-fallback",
        type=int,
        action="append",
        dest="video_crf_fallback",
        help="CRF alternativo a testar quando o principal falhar (repita a opcao para adicionar outros).",
    )
    parser.add_argument(
        "--encoder",
        choices=["auto", "libx264", "h264_nvenc"],
        default="auto",
        help="Encoder de video preferido (auto tenta NVENC quando disponivel).",
    )
    parser.add_argument(
        "--cpu-only",
        action="store_true",
        help="Forca processamento de video com CPU (libx264), mesmo se houver GPU.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Numero maximo de processos paralelos para comprimir arquivos (padrao: 1).",
    )
    return parser.parse_args()


def collect_input_files(inputs: Iterable[str]) -> List[Path]:
    files: List[Path] = []
    seen: set[Path] = set()

    for raw in inputs:
        path = Path(raw).expanduser()
        if not path.exists():
            console.print(f"[yellow]Ignorando (nao encontrado):[/] {path}")
            continue

        path = path.resolve()
        if path.is_dir():
            candidates = sorted(
                p for p in path.rglob("*.zip") if p.is_file() and ".compressed" not in p.name.lower()
            )
            if not candidates:
                console.print(f"[yellow]Nenhum .zip encontrado em:[/] {path}")
            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                files.append(resolved)
                seen.add(resolved)
        else:
            if path.suffix.lower() != ".zip":
                console.print(f"[yellow]Ignorando (nao e .zip):[/] {path}")
                continue
            if ".compressed" in path.name.lower():
                console.print(f"[yellow]Ignorando arquivo ja comprimido:[/] {path}")
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            files.append(resolved)
            seen.add(resolved)

    return files


def relocate_results(results: List[ArchiveProcessResult], target: Path) -> None:
    if not results:
        return

    target = target.expanduser()

    if len(results) == 1:
        destination: Path
        if target.exists() and target.is_dir():
            destination = target / results[0].output_path.name
        elif not target.exists() and target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            destination = target / results[0].output_path.name
        else:
            destination = target
            destination.parent.mkdir(parents=True, exist_ok=True)
        _move_result(results[0], destination)
        moved_to_error = _ensure_error_destination(results[0])
        if moved_to_error:
            console.print(f"[yellow]Arquivo com erros movido para: {results[0].output_path}")
        else:
            console.print(f"Arquivo movido para: {results[0].output_path}")
        return

    if target.exists() and not target.is_dir():
        console.print(f"[yellow]Destino nao e diretorio. Ignorando --output: {target}")
        return

    target.mkdir(parents=True, exist_ok=True)
    for result in results:
        destination = target / result.output_path.name
        _move_result(result, destination)
        moved_to_error = _ensure_error_destination(result)
        if moved_to_error:
            console.print(f"[yellow]Arquivo com erros movido para: {result.output_path}")
        else:
            console.print(f"Arquivo movido para: {result.output_path}")


def _move_result(result: ArchiveProcessResult, destination: Path) -> None:
    result.output_path = move_output_file(result.output_path, destination, result.input_path)


def _ensure_error_destination(result: ArchiveProcessResult) -> bool:
    if not result.error_results:
        return False
    current_parent = result.output_path.parent
    if current_parent.name.lower() == ERROR_OUTPUT_DIR_NAME.lower():
        return False
    error_dir = current_parent / ERROR_OUTPUT_DIR_NAME
    destination = error_dir / result.output_path.name
    _move_result(result, destination)
    return True


def main() -> None:
    args = parse_args()

    start_time = time.perf_counter()
    failures: List[Tuple[Path, Exception]] = []

    input_files = collect_input_files(args.input)
    if not input_files:
        console.print("[red]Nenhum arquivo .zip valido para processar.[/]")
        return

    chosen_encoder = None if args.encoder == "auto" else args.encoder
    prefer_gpu = not args.cpu_only
    sanitized_fallbacks: List[int] = []
    ignored_fallbacks: List[int] = []
    for raw_value in args.video_crf_fallback or []:
        if raw_value is None:
            continue
        normalized_value = int(max(0, min(int(raw_value), 51)))
        if normalized_value >= args.video_crf:
            ignored_fallbacks.append(normalized_value)
            continue
        if normalized_value not in sanitized_fallbacks:
            sanitized_fallbacks.append(normalized_value)
    if ignored_fallbacks:
        console.print(
            "[yellow]Ignorando CRF(s) alternativo(s) "
            f"{', '.join(str(v) for v in sorted(set(ignored_fallbacks)))} "
            f"por serem maiores ou iguais ao CRF principal ({args.video_crf}).[/]"
        )
    fallback_crfs: Tuple[int, ...] = tuple(sanitized_fallbacks)
    max_workers = max(1, args.workers or 1)

    def _process_sequential(paths: List[Path]) -> List[ArchiveProcessResult]:
        seq_results: List[ArchiveProcessResult] = []
        for file_path in paths:
            try:
                result = process_epub_zip(
                    file_path,
                    image_quality=args.image_quality,
                    video_crf=args.video_crf,
                    fallback_crfs=fallback_crfs,
                    prefer_gpu=prefer_gpu,
                    encoder=chosen_encoder,
                )
                seq_results.append(result)
            except Exception as exc:
                console.print(f"[red]Falha ao processar {file_path}: {exc}")
                failures.append((file_path, exc))
        return seq_results

    results: List[ArchiveProcessResult]
    if max_workers > 1 and len(input_files) > 1:
        index_map = {path: idx for idx, path in enumerate(input_files)}
        ordered_results: List[Tuple[int, ArchiveProcessResult]] = []
        try:
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                future_to_path = {
                    executor.submit(
                        process_epub_zip,
                        file_path,
                        image_quality=args.image_quality,
                        video_crf=args.video_crf,
                        fallback_crfs=fallback_crfs,
                        prefer_gpu=prefer_gpu,
                        encoder=chosen_encoder,
                    ): file_path
                    for file_path in input_files
                }
                for future in as_completed(future_to_path):
                    file_path = future_to_path[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # pragma: no cover - runtime guard
                        console.print(f"[red]Falha ao processar {file_path}: {exc}")
                        failures.append((file_path, exc))
                    else:
                        ordered_results.append((index_map[file_path], result))
        except Exception as exc:  # pragma: no cover - safety fallback
            console.print(f"[yellow]Processamento paralelo falhou ({exc}). Retornando ao modo sequencial.[/]")
            results = _process_sequential(input_files)
        else:
            ordered_results.sort(key=lambda item: item[0])
            results = [item[1] for item in ordered_results]
    else:
        results = _process_sequential(input_files)

    if not results:
        console.print("[red]Nenhum arquivo processado com sucesso.[/]")
        return

    if args.output:
        relocate_results(results, Path(args.output))

    for result in results:
        _ensure_error_destination(result)
        in_error_dir = result.output_path.parent.name.lower() == ERROR_OUTPUT_DIR_NAME.lower()
        try:
            log_path = write_result_log(result)
        except Exception as exc:
            console.print(f"[yellow]Falha ao gerar log para {result.output_path}: {exc}")
        else:
            console.print(f"Log gerado: {log_path}")
        console.print(f"[green]Arquivo final:[/] {result.output_path}")
        if result.error_results:
            error_msg = f"[yellow]{len(result.error_results)} arquivo(s) apresentaram erro durante a compressao."
            if in_error_dir:
                error_msg += f" Resultado armazenado em '{result.output_path.parent}'."
            console.print(error_msg)

    duration = time.perf_counter() - start_time
    try:
        summary_path = write_batch_summary_log(results, duration_seconds=duration, failures=failures)
    except Exception as exc:
        console.print(f"[yellow]Falha ao gerar resumo geral: {exc}")
    else:
        console.print(f"[cyan]Resumo geral gerado:[/] {summary_path}")


if __name__ == "__main__":
    try:
        import multiprocessing as _mp
    except ImportError:  # pragma: no cover - platform safeguard
        main()
    else:
        if hasattr(_mp, "freeze_support"):
            _mp.freeze_support()
        main()
