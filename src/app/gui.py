from __future__ import annotations

import shutil
import time
import tkinter as tk
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from threading import Thread
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

from rich.console import Console

from src.constants import ERROR_OUTPUT_DIR_NAME
from src.core.epub_processor import (
    ArchiveProcessResult,
    DEFAULT_IMAGE_QUALITY,
    DEFAULT_VIDEO_CRF,
    process_epub_zip,
    write_result_log,
    write_batch_summary_log,
)
from src.utils.cache import clear_temp_cache

console = Console()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Video Compressor Desktop (EPUB)")
        self.geometry("720x480")
        self.minsize(640, 420)

        self.processing = False

        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Pronto.")
        self.image_quality_var = tk.IntVar(value=DEFAULT_IMAGE_QUALITY)
        self.video_crf_var = tk.IntVar(value=DEFAULT_VIDEO_CRF)
        self.workers_var = tk.IntVar(value=1)

        self._build_layout()

    def _build_layout(self) -> None:
        container = ttk.Frame(self, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        input_label = ttk.Label(container, text="Pasta ou arquivo de entrada (.zip / .epub.zip):")
        input_label.pack(anchor="w")

        input_row = ttk.Frame(container)
        input_row.pack(fill=tk.X, pady=(2, 8))

        input_entry = ttk.Entry(input_row, textvariable=self.input_path_var)
        input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(input_row, text="Pasta...", command=self.pick_input_directory).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(input_row, text="Arquivo...", command=self.pick_input_file).pack(side=tk.LEFT, padx=(6, 0))

        output_label = ttk.Label(container, text="Pasta de saida (opcional):")
        output_label.pack(anchor="w")

        output_row = ttk.Frame(container)
        output_row.pack(fill=tk.X, pady=(2, 8))

        output_entry = ttk.Entry(output_row, textvariable=self.output_path_var)
        output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(output_row, text="Selecionar...", command=self.pick_output_directory).pack(side=tk.LEFT, padx=(6, 0))

        options_frame = ttk.LabelFrame(container, text="Parametros de compressao")
        options_frame.pack(fill=tk.X, pady=(0, 8))

        quality_row = ttk.Frame(options_frame)
        quality_row.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(quality_row, text="Qualidade imagem (1-100):").pack(side=tk.LEFT)
        ttk.Spinbox(
            quality_row,
            from_=1,
            to=100,
            textvariable=self.image_quality_var,
            width=5,
        ).pack(side=tk.LEFT, padx=(6, 0))

        video_row = ttk.Frame(options_frame)
        video_row.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(video_row, text="Video CRF (0-51):").pack(side=tk.LEFT)
        ttk.Spinbox(
            video_row,
            from_=0,
            to=51,
            textvariable=self.video_crf_var,
            width=5,
        ).pack(side=tk.LEFT, padx=(6, 0))

        workers_row = ttk.Frame(options_frame)
        workers_row.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(workers_row, text="Processos paralelos:").pack(side=tk.LEFT)
        ttk.Spinbox(
            workers_row,
            from_=1,
            to=32,
            textvariable=self.workers_var,
            width=5,
        ).pack(side=tk.LEFT, padx=(6, 0))

        controls = ttk.Frame(container)
        controls.pack(fill=tk.X, pady=(0, 8))

        self.process_button = ttk.Button(controls, text="Processar obras", command=self.start_processing)
        self.process_button.pack(side=tk.LEFT)

        ttk.Button(controls, text="Limpar log", command=self.clear_log).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(controls, text="Limpar cache", command=self.clear_cache).pack(side=tk.LEFT, padx=(8, 0))

        log_frame = ttk.LabelFrame(container, text="Log do processamento")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=14, state=tk.DISABLED)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        status = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(10, 4))
        status.pack(fill=tk.X, side=tk.BOTTOM)

    def pick_input_directory(self) -> None:
        chosen = filedialog.askdirectory(title="Selecione a pasta de entrada")
        if chosen:
            self.input_path_var.set(chosen)

    def pick_input_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Selecione um arquivo .zip ou .epub.zip",
            filetypes=[("EPUB ZIP", "*.epub.zip"), ("ZIP", "*.zip"), ("Todos", "*.*")],
        )
        if chosen:
            self.input_path_var.set(chosen)

    def pick_output_directory(self) -> None:
        chosen = filedialog.askdirectory(title="Selecione a pasta de saida")
        if chosen:
            self.output_path_var.set(chosen)

    def clear_log(self) -> None:
        self._update_log("clear")

    def clear_cache(self) -> None:
        if self.processing:
            messagebox.showwarning(
                "Processamento em andamento",
                "Finalize o processamento antes de limpar o cache.",
            )
            return

        removed, failures = clear_temp_cache()
        removed_count = len(removed)
        failure_count = len(failures)

        if removed_count:
            self.append_log(f"Cache limpo: {removed_count} item(s) removido(s).")
        else:
            self.append_log("Cache limpo: nenhum item para remover.")

        if failure_count:
            for path, error in failures[:3]:
                self.append_log(f"Falha ao remover {path}: {error}")
            if failure_count > 3:
                self.append_log(f"... {failure_count - 3} falha(s) adicional(is).")

        self.set_status("Cache limpo." if failure_count == 0 else "Cache limpo com avisos.")

        message_lines = [
            f"Itens removidos: {removed_count}",
            f"Falhas: {failure_count}",
        ]

        if failure_count:
            message_lines.append("")
            for path, error in failures[:3]:
                message_lines.append(f"- {path.name}: {error}")
            if failure_count > 3:
                message_lines.append("- ...")
            message = "\n".join(message_lines)
            messagebox.showwarning("Limpeza de cache", message)
        else:
            message = "\n".join(message_lines)
            messagebox.showinfo("Limpeza de cache", message)

    def append_log(self, text: str) -> None:
        self._update_log("append", text)

    def set_status(self, text: str) -> None:
        def apply() -> None:
            self.status_var.set(text)

        self.after(0, apply)

    def _update_log(self, action: str, text: str | None = None) -> None:
        def apply() -> None:
            self.log_text.configure(state=tk.NORMAL)
            if action == "clear":
                self.log_text.delete("1.0", tk.END)
            elif action == "append" and text is not None:
                self.log_text.insert(tk.END, text + "\n")
                self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        self.after(0, apply)

    def start_processing(self) -> None:
        if self.processing:
            return

        input_path_text = self.input_path_var.get().strip()
        if not input_path_text:
            messagebox.showerror("Entrada invalida", "Informe uma pasta ou arquivo .zip/.epub.zip.")
            return

        input_path = Path(input_path_text).expanduser()
        if not input_path.exists():
            messagebox.showerror("Entrada invalida", f"O caminho informado nao existe:\n{input_path}")
            return

        output_path_text = self.output_path_var.get().strip()
        output_path = Path(output_path_text).expanduser() if output_path_text else None

        try:
            image_quality = int(self.image_quality_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Valor invalido", "Informe um numero entre 1 e 100 para a qualidade de imagem.")
            return
        if not 1 <= image_quality <= 100:
            messagebox.showerror("Valor invalido", "Informe um numero entre 1 e 100 para a qualidade de imagem.")
            return

        try:
            video_crf = int(self.video_crf_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Valor invalido", "Informe um numero entre 0 e 51 para o CRF do video.")
            return
        if not 0 <= video_crf <= 51:
            messagebox.showerror("Valor invalido", "Informe um numero entre 0 e 51 para o CRF do video.")
            return

        try:
            workers = int(self.workers_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Valor invalido", "Informe um numero maior ou igual a 1 para processos paralelos.")
            return
        if workers < 1:
            messagebox.showerror("Valor invalido", "Informe um numero maior ou igual a 1 para processos paralelos.")
            return

        self.processing = True
        self.process_button.configure(state=tk.DISABLED)
        self.set_status("Processando obras...")
        self.append_log("Iniciando processamento...")
        self.append_log(
            f"Parametros: imagem qualidade={image_quality}; video CRF={video_crf}; processos={workers}"
        )

        start_time = time.perf_counter()

        worker = Thread(
            target=self._process_worker,
            args=(input_path, output_path, image_quality, video_crf, workers, start_time),
            daemon=True,
        )
        worker.start()

    def _process_worker(
        self,
        input_path: Path,
        output_path: Path | None,
        image_quality: int,
        video_crf: int,
        workers: int,
        start_time: float,
    ) -> None:
        try:
            files = self._collect_zip_files(input_path)
            if not files:
                self.append_log("Nenhum arquivo .zip encontrado para processar.")
                duration = time.perf_counter() - start_time
                self._finish_processing(
                    successes=[],
                    failures=[],
                    summary_path=None,
                    duration=duration,
                )
                return

            total = len(files)
            successes: List[ArchiveProcessResult] = []
            failures: List[tuple[Path, Exception]] = []
            summary_path: Optional[Path] = None

            if output_path is not None:
                output_path.mkdir(parents=True, exist_ok=True)

            processed_inputs: set[Path] = set()
            completed_count = 0

            def finalize_result(result: ArchiveProcessResult) -> None:
                nonlocal completed_count
                if output_path is not None:
                    try:
                        self._move_result(result, output_path)
                    except Exception as exc:
                        failures.append((result.output_path, exc))
                        self.append_log(f"!- Erro ao mover {result.output_path.name}: {exc}")
                        return
                try:
                    self._ensure_error_destination(result)
                except Exception as exc:
                    failures.append((result.output_path, exc))
                    self.append_log(f"!- Erro ao organizar obra com erros ({result.output_path.name}): {exc}")
                    return

                successes.append(result)
                processed_inputs.add(result.input_path.resolve())
                completed_count += 1
                display_name = result.input_path.name if result.input_path else result.output_path.name
                self.append_log(f"[{completed_count}/{total}] Finalizado: {display_name}")
                self.set_status(f"Processando ({completed_count}/{total})...")
                self.append_log(f"-> Arquivo final: {result.output_path}")
                if result.error_results:
                    location = result.output_path.parent
                    if location.name.lower() == ERROR_OUTPUT_DIR_NAME.lower():
                        self.append_log(
                            f"   Aviso: {len(result.error_results)} arquivo(s) ignorado(s). Obra movida para '{location}'."
                        )
                    else:
                        self.append_log(
                            f"   Aviso: {len(result.error_results)} arquivo(s) ignorado(s) durante a compressao."
                        )
                try:
                    log_path = write_result_log(result)
                except Exception as exc:
                    result.log_path = None
                    self.append_log(f"!- Erro ao gerar log ({result.output_path.name}): {exc}")
                else:
                    self.append_log(f"-> Log: {log_path}")

            use_parallel = workers > 1 and total > 1
            if total == 1:
                self.append_log("Encontrado 1 arquivo para processar.")
            else:
                self.append_log(f"Encontrados {total} arquivos para processar.")
            if use_parallel:
                self.append_log(f"Processando com ate {workers} processo(s) em paralelo...")
            else:
                self.append_log("Processando em modo sequencial.")
            self.set_status(f"Processando (0/{total})...")

            processed_in_parallel = False

            if use_parallel:
                try:
                    with ProcessPoolExecutor(max_workers=workers) as executor:
                        future_to_meta: dict = {}
                        preview_limit = min(total, workers)
                        queued_notice_shown = False

                        for idx, file_path in enumerate(files, start=1):
                            if idx <= preview_limit:
                                self.append_log(f"[Fila] Iniciando: {file_path}")
                            elif not queued_notice_shown:
                                remaining = total - (idx - 1)
                                if remaining > 0:
                                    self.append_log(f"... + {remaining} arquivo(s) aguardando na fila.")
                                queued_notice_shown = True
                            future = executor.submit(
                                process_epub_zip,
                                file_path,
                                image_quality=image_quality,
                                video_crf=video_crf,
                            )
                            future_to_meta[future] = file_path

                        for future in as_completed(future_to_meta):
                            file_path = future_to_meta[future]
                            try:
                                result = future.result()
                            except Exception as exc:
                                failures.append((file_path, exc))
                                self.append_log(f"!- Erro em {file_path.name}: {exc}")
                            else:
                                finalize_result(result)
                        processed_in_parallel = True
                except Exception as exc:
                    self.append_log(f"Processamento paralelo falhou ({exc}). Alternando para modo sequencial.")
                    processed_in_parallel = False

            if not processed_in_parallel:
                completed_count = len(successes)
                remaining_files = [fp for fp in files if fp not in processed_inputs]
                for idx, file_path in enumerate(remaining_files, start=1):
                    next_index = completed_count + 1
                    self.append_log(f"[{next_index}/{total}] Processando: {file_path}")
                    try:
                        result = process_epub_zip(
                            file_path,
                            image_quality=image_quality,
                            video_crf=video_crf,
                        )
                        finalize_result(result)
                    except Exception as exc:
                        failures.append((file_path, exc))
                        self.append_log(f"!- Erro em {file_path.name}: {exc}")

            duration = time.perf_counter() - start_time
            if successes:
                log_dir = output_path if output_path is not None else None
                try:
                    summary_path = write_batch_summary_log(
                        successes,
                        duration_seconds=duration,
                        log_dir=log_dir,
                        failures=failures,
                    )
                except Exception as exc:
                    self.append_log(f"!- Erro ao gerar resumo geral: {exc}")
                    summary_path = None

            self._finish_processing(
                successes=successes,
                failures=failures,
                summary_path=summary_path,
                duration=duration,
            )
        finally:
            self.after(0, self._reset_ui_state)

    def _finish_processing(
        self,
        *,
        successes: List[ArchiveProcessResult],
        failures: List[tuple[Path, Exception]],
        summary_path: Optional[Path],
        duration: float,
    ) -> None:
        def notify() -> None:
            self.set_status("Pronto.")

            formatted_duration = self._format_duration(duration)
            if successes:
                self.append_log(f"Processamento concluido com {len(successes)} arquivo(s) gerado(s).")
            if failures:
                self.append_log(f"{len(failures)} arquivo(s) falharam.")
            if successes or failures:
                self.append_log(f"Tempo total: {formatted_duration} ({duration:.2f}s)")
            if summary_path is not None:
                self.append_log(f"Resumo geral: {summary_path}")

            summary_lines = []
            if successes:
                summary_lines.append(f"Sucesso: {len(successes)}")
                for item in successes:
                    summary_lines.append(f"- {item.output_path}")
                    if item.log_path:
                        summary_lines.append(f"  Log: {item.log_path}")
            if failures:
                summary_lines.append("")
                summary_lines.append(f"Falhas: {len(failures)}")
                for path, error in failures:
                    summary_lines.append(f"- {path} -> {error}")
            if summary_path is not None:
                summary_lines.append("")
                summary_lines.append(f"Resumo geral: {summary_path}")
            if successes or failures:
                summary_lines.append(f"Tempo total: {formatted_duration} ({duration:.2f}s)")

            summary = "\n".join(summary_lines) if summary_lines else "Nenhum arquivo processado."
            messagebox.showinfo("Processamento finalizado", summary)

        self.after(0, notify)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _reset_ui_state(self) -> None:
        self.processing = False
        self.process_button.configure(state=tk.NORMAL)

    def _collect_zip_files(self, path: Path) -> List[Path]:
        files: List[Path] = []
        seen: set[Path] = set()

        def add_candidate(candidate: Path) -> None:
            resolved = candidate.resolve()
            if resolved in seen:
                return
            if ".compressed" in resolved.name.lower():
                self.append_log(f"Ignorando arquivo ja comprimido: {resolved}")
                return
            files.append(resolved)
            seen.add(resolved)

        if path.is_dir():
            found = sorted(p for p in path.rglob("*.zip") if p.is_file())
            for candidate in found:
                add_candidate(candidate)
        elif path.is_file():
            if path.suffix.lower() != ".zip":
                self.append_log(f"Ignorando arquivo (nao eh .zip): {path}")
            else:
                add_candidate(path)
        else:
            self.append_log(f"Caminho ignorado: {path}")

        return files

    def _ensure_error_destination(self, result: ArchiveProcessResult) -> bool:
        if not result.error_results:
            return False
        current_parent = result.output_path.parent
        if current_parent.name.lower() == ERROR_OUTPUT_DIR_NAME.lower():
            return False
        error_dir = current_parent / ERROR_OUTPUT_DIR_NAME
        self._move_result(result, error_dir)
        return True

    def _move_result(self, result: ArchiveProcessResult, destination_dir: Path) -> None:
        destination_dir = destination_dir.expanduser().resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)

        source = result.output_path
        destination = destination_dir / source.name
        if source == destination:
            result.output_path = destination
            return
        if destination.exists():
            destination.unlink()
        try:
            shutil.move(str(source), destination)
        except Exception:
            shutil.copy2(str(source), destination)
        if source.exists():
            try:
                source.unlink()
            except OSError:
                self.append_log(f"Aviso: nao foi possivel remover '{source}' apos mover.")
        result.output_path = destination


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
