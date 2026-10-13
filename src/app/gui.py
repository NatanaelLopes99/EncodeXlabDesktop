from __future__ import annotations

import shutil
import subprocess
import sys
import time
import tkinter as tk
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from threading import Event, Thread
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
from src.utils.fs import move_output_file

console = Console()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Video Compressor Desktop (EPUB)")
        self.geometry("720x480")
        self.minsize(640, 420)

        self.processing = False
        self.cancel_event = Event()
        self.worker_thread: Optional[Thread] = None

        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Pronto.")
        self.image_quality_var = tk.IntVar(value=DEFAULT_IMAGE_QUALITY)
        self.video_crf_var = tk.IntVar(value=DEFAULT_VIDEO_CRF)
        self.video_crf_fallback_var = tk.StringVar()
        self.workers_var = tk.IntVar(value=1)
        self.sound_path_var = tk.StringVar()

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

        fallback_row = ttk.Frame(options_frame)
        fallback_row.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(fallback_row, text="CRFs alternativos (ex: 22,24):").pack(side=tk.LEFT)
        ttk.Entry(
            fallback_row,
            textvariable=self.video_crf_fallback_var,
            width=16,
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

        sound_row = ttk.Frame(options_frame)
        sound_row.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(sound_row, text="Som ao finalizar:").pack(side=tk.LEFT)
        ttk.Entry(sound_row, textvariable=self.sound_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        ttk.Button(sound_row, text="Escolher...", command=self.pick_sound_file).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(sound_row, text="Limpar", command=self.clear_sound_file).pack(side=tk.LEFT, padx=(6, 0))

        controls = ttk.Frame(container)
        controls.pack(fill=tk.X, pady=(0, 8))

        self.process_button = ttk.Button(controls, text="Processar obras", command=self.start_processing)
        self.process_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(
            controls, text="Parar", command=self.stop_processing, state=tk.DISABLED
        )
        self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))

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

    def pick_sound_file(self) -> None:
        current = self.sound_path_var.get().strip()
        initial_dir = None
        if current:
            current_path = Path(current).expanduser()
            if current_path.exists():
                initial_dir = str(current_path.parent)
        dialog_options = {
            "title": "Selecionar som de conclusao",
            "filetypes": (
                ("Arquivos de audio", "*.wav *.mp3 *.ogg *.flac *.m4a *.aac"),
                ("Todos os arquivos", "*.*"),
            ),
        }
        if initial_dir:
            dialog_options["initialdir"] = initial_dir
        file_path = filedialog.askopenfilename(**dialog_options)
        if file_path:
            self.sound_path_var.set(file_path)

    def clear_sound_file(self) -> None:
        self.sound_path_var.set("")

    def append_log(self, text: str) -> None:
        self._update_log("append", text)

    def set_status(self, text: str) -> None:
        def apply() -> None:
            self.status_var.set(text)

        self.after(0, apply)

    def stop_processing(self) -> None:
        if not self.processing or self.cancel_event.is_set():
            return
        self.cancel_event.set()
        self.cancel_button.configure(state=tk.DISABLED)
        self.append_log("Cancelamento solicitado. Aguardando tarefas em andamento...")
        self.set_status("Cancelando processamento...")

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

        fallback_raw = self.video_crf_fallback_var.get().strip()
        fallback_values: List[int] = []
        if fallback_raw:
            sanitized = fallback_raw.replace(";", ",")
            for chunk in sanitized.split(","):
                part = chunk.strip()
                if not part:
                    continue
                try:
                    value = int(part)
                except ValueError:
                    messagebox.showerror("Valor invalido", f"CRF alternativo invalido: {part}")
                    return
                value = max(0, min(value, 51))
                if value >= video_crf:
                    messagebox.showerror(
                        "Valor invalido",
                        f"CRFs alternativos precisam ser menores que o CRF principal ({video_crf}).",
                    )
                    return
                if value not in fallback_values:
                    fallback_values.append(value)
            self.video_crf_fallback_var.set(",".join(str(v) for v in fallback_values))
        fallback_tuple = tuple(fallback_values)

        try:
            workers = int(self.workers_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Valor invalido", "Informe um numero maior ou igual a 1 para processos paralelos.")
            return
        if workers < 1:
            messagebox.showerror("Valor invalido", "Informe um numero maior ou igual a 1 para processos paralelos.")
            return

        self.cancel_event.clear()
        self.processing = True
        self.process_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.set_status("Processando obras...")
        self.append_log("Iniciando processamento...")
        fallback_text = f"; fallback CRFs={','.join(str(v) for v in fallback_values)}" if fallback_values else ""
        self.append_log(
            f"Parametros: imagem qualidade={image_quality}; video CRF={video_crf}{fallback_text}; processos={workers}"
        )

        start_time = time.perf_counter()

        worker = Thread(
            target=self._process_worker,
            args=(input_path, output_path, image_quality, video_crf, fallback_tuple, workers, start_time),
            daemon=True,
        )
        worker.start()
        self.worker_thread = worker

    def _process_worker(
        self,
        input_path: Path,
        output_path: Path | None,
        image_quality: int,
        video_crf: int,
        fallback_crfs: Tuple[int, ...],
        workers: int,
        start_time: float,
    ) -> None:
        cancelled = False
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
                    cancelled=False,
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
                            if self.cancel_event.is_set():
                                cancelled = True
                                self.append_log(
                                    "Cancelamento solicitado. Interrompendo atribuicao de novas tarefas paralelas."
                                )
                                break
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
                                fallback_crfs=fallback_crfs,
                            )
                            future_to_meta[future] = file_path

                        if cancelled:
                            for future in future_to_meta:
                                future.cancel()
                        for future in as_completed(future_to_meta):
                            if self.cancel_event.is_set() and not cancelled:
                                cancelled = True
                                self.append_log(
                                    "Cancelamento solicitado. Aguardando tarefas paralelas em andamento finalizarem."
                                )
                                for pending in future_to_meta:
                                    pending.cancel()
                            if future.cancelled():
                                continue
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

            cancelled = cancelled or self.cancel_event.is_set()

            if not processed_in_parallel and not cancelled:
                completed_count = len(successes)
                remaining_files = [fp for fp in files if fp not in processed_inputs]
                for idx, file_path in enumerate(remaining_files, start=1):
                    if self.cancel_event.is_set():
                        cancelled = True
                        self.append_log("Cancelamento solicitado. Interrompendo processamento sequencial.")
                        break
                    next_index = completed_count + 1
                    self.append_log(f"[{next_index}/{total}] Processando: {file_path}")
                    try:
                        result = process_epub_zip(
                            file_path,
                            image_quality=image_quality,
                            video_crf=video_crf,
                            fallback_crfs=fallback_crfs,
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
                cancelled=cancelled,
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
        cancelled: bool,
    ) -> None:
        def notify() -> None:
            formatted_duration = self._format_duration(duration)
            if cancelled:
                self.set_status("Cancelado.")
                if successes or failures:
                    self.append_log(f"Processamento cancelado apos {formatted_duration} ({duration:.2f}s).")
                else:
                    self.append_log("Processamento cancelado pelo usuario.")

                summary_lines = []
                if successes:
                    summary_lines.append(f"Processados antes do cancelamento: {len(successes)}")
                    for item in successes:
                        summary_lines.append(f"- {item.output_path}")
                        if item.log_path:
                            summary_lines.append(f"  Log: {item.log_path}")
                if failures:
                    summary_lines.append("")
                    summary_lines.append(f"Falhas registradas: {len(failures)}")
                    for path, error in failures:
                        summary_lines.append(f"- {path} -> {error}")
                if successes or failures:
                    summary_lines.append("")
                    summary_lines.append(f"Tempo ate o cancelamento: {formatted_duration} ({duration:.2f}s)")
                if summary_path is not None:
                    summary_lines.append(f"Resumo geral: {summary_path}")
                if not summary_lines:
                    summary_lines.append("Nenhum arquivo processado antes do cancelamento.")

                messagebox.showinfo("Processamento cancelado", "\n".join(summary_lines))
                return

            self.set_status("Pronto.")

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
            self._play_completion_sound()
            messagebox.showinfo("Processamento finalizado", summary)

        self.after(0, notify)

    def _play_completion_sound(self) -> None:
        path_text = self.sound_path_var.get().strip()
        if not path_text:
            return

        sound_path = Path(path_text).expanduser()
        if not sound_path.exists():
            self.append_log(f"Som de conclusao nao encontrado: {sound_path}")
            return

        def _run() -> None:
            if sys.platform.startswith("win"):
                try:
                    import winsound  # type: ignore

                    winsound.PlaySound(str(sound_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return
                except Exception:
                    pass

            ffplay = shutil.which("ffplay")
            if ffplay:
                try:
                    subprocess.Popen(
                        [ffplay, "-nodisp", "-autoexit", str(sound_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except Exception:
                    pass

            if sys.platform == "darwin":
                afplay = shutil.which("afplay")
                if afplay:
                    try:
                        subprocess.Popen(
                            [afplay, str(sound_path)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return
                    except Exception:
                        pass

            if sys.platform.startswith("linux"):
                player = shutil.which("paplay") or shutil.which("aplay") or shutil.which("cvlc")
                if player:
                    try:
                        args = [player, str(sound_path)]
                        if Path(player).name.lower() == "cvlc":
                            args.insert(1, "--play-and-exit")
                        subprocess.Popen(
                            args,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return
                    except Exception:
                        pass

            self.append_log("Nao foi possivel reproduzir o som de conclusao.")

        Thread(target=_run, daemon=True).start()

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _reset_ui_state(self) -> None:
        self.processing = False
        self.process_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.cancel_event.clear()
        self.worker_thread = None

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
        destination = destination_dir / result.output_path.name
        result.output_path = move_output_file(result.output_path, destination, result.input_path)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    main()
