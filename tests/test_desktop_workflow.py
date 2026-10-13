from concurrent.futures import Future
from io import BytesIO
import json
import shutil
import subprocess
from threading import Event
import time
from types import SimpleNamespace
from zipfile import ZipFile

from PIL import Image
import pytest

from src.app.gui import App
from src.core.epub_processor import process_epub_zip


def test_zip_image_processing_keeps_original_and_other_content(tmp_path):
    image = BytesIO()
    Image.effect_noise((256, 256), 80).convert("RGB").save(image, "JPEG", quality=100)
    original = tmp_path / "book.zip"
    with ZipFile(original, "w") as archive:
        archive.writestr("OPS/image.jpg", image.getvalue())
        archive.writestr("OPS/chapter.xhtml", "<html>chapter</html>")
    original_bytes = original.read_bytes()

    result = process_epub_zip(original, prefer_gpu=False)

    assert original.read_bytes() == original_bytes
    assert not result.error_results
    assert result.image_results[0].changed
    with ZipFile(result.output_path) as archive:
        assert archive.read("OPS/chapter.xhtml") == b"<html>chapter</html>"
        with Image.open(BytesIO(archive.read("OPS/image.jpg"))) as compressed:
            assert compressed.size == (256, 256)
        assert len(archive.read("OPS/image.jpg")) < len(image.getvalue())


def test_optimized_image_is_kept_byte_for_byte(tmp_path):
    image = BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, "PNG", optimize=True, compress_level=9)
    original = tmp_path / "optimized.zip"
    with ZipFile(original, "w") as archive:
        archive.writestr("OPS/image.png", image.getvalue())

    result = process_epub_zip(original, prefer_gpu=False)

    assert not result.error_results
    assert not result.image_results[0].changed
    assert result.image_results[0].reason == "no-gain"
    with ZipFile(result.output_path) as archive:
        assert archive.read("OPS/image.png") == image.getvalue()


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg required")
def test_zip_video_processing_with_real_ffmpeg(tmp_path):
    video = tmp_path / "video.mov"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=128x128:rate=10", "-t", "1", "-c:v", "libx264", "-crf", "0",
         str(video)],
        check=True, capture_output=True, timeout=30,
    )
    original = tmp_path / "book.zip"
    with ZipFile(original, "w") as archive:
        archive.write(video, "OPS/video.mov")
    original_bytes = original.read_bytes()

    result = process_epub_zip(original, prefer_gpu=False, encoder="libx264")

    assert original.read_bytes() == original_bytes
    assert not result.error_results
    assert result.video_results[0].encoder == "libx264"
    assert result.video_results[0].changed
    with ZipFile(result.output_path) as archive:
        output = tmp_path / "compressed.mov"
        output.write_bytes(archive.read("OPS/video.mov"))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (128, 128)
    assert int(stream["nb_frames"]) == 10


def test_parallel_cancellation_records_already_completed_tasks(tmp_path, monkeypatch):
    files = []
    results = []
    for index in range(3):
        archive_path = tmp_path / f"book{index}.zip"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr("chapter.txt", "chapter")
        files.append(archive_path)
        if index < 2:
            results.append(process_epub_zip(archive_path, prefer_gpu=False))
    cancel = Event()
    logs = []
    completed = {}

    class Executor:
        def __init__(self, **kwargs):
            self.submitted = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, *args, **kwargs):
            future = Future()
            future.set_result(results[self.submitted])
            self.submitted += 1
            if self.submitted == 2:
                cancel.set()
            return future

    monkeypatch.setattr("src.app.gui.ProcessPoolExecutor", Executor)
    app = SimpleNamespace(
        cancel_event=cancel,
        _collect_zip_files=lambda path: files,
        append_log=logs.append,
        set_status=lambda text: None,
        _finish_processing=lambda **kwargs: completed.update(kwargs),
        _reset_ui_state=lambda: None,
        after=lambda delay, callback: callback(),
    )
    app._move_result = lambda result, directory: App._move_result(app, result, directory)
    app._ensure_error_destination = lambda result: App._ensure_error_destination(app, result)
    App._process_worker(app, tmp_path, tmp_path / "output", 85, 23, (), 2, time.perf_counter())

    assert completed["cancelled"]
    assert len(completed["successes"]) == 2
    assert not completed["failures"]
    assert completed["summary_path"].exists()
    for result in completed["successes"]:
        assert result.output_path.parent == tmp_path / "output"
        assert result.log_path.exists()
