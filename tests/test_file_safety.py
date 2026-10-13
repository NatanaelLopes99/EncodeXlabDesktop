from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

import pytest

from src.app.gui import App
from src.core.epub_processor import _safe_extractall, make_zip_from_dir, process_epub_zip
from src.main import _move_result
from src.utils.fs import move_output_file, open_unique_output


def test_existing_archive_is_preserved(tmp_path):
    content = tmp_path / "content"
    content.mkdir()
    (content / "chapter.txt").write_text("chapter", encoding="utf-8")
    requested = tmp_path / "book.compressed.zip"
    requested.write_bytes(b"previous result")

    actual = make_zip_from_dir(content, requested)

    assert requested.read_bytes() == b"previous result"
    assert actual != requested
    with ZipFile(actual) as archive:
        assert archive.read("chapter.txt") == b"chapter"


def test_failed_archive_write_keeps_previous_result(tmp_path, monkeypatch):
    content = tmp_path / "content"
    content.mkdir()
    (content / "chapter.txt").write_text("chapter")
    requested = tmp_path / "book.compressed.zip"
    requested.write_bytes(b"previous result")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ZipFile, "write", fail)
    with pytest.raises(OSError, match="disk full"):
        make_zip_from_dir(content, requested)
    assert requested.read_bytes() == b"previous result"
    assert list(tmp_path.glob("*.zip")) == [requested]


@pytest.mark.parametrize("move", ["cli", "gui"])
def test_move_never_replaces_original(tmp_path, move):
    original = tmp_path / "book.zip"
    original.write_bytes(b"original")
    compressed = tmp_path / "book.compressed.zip"
    compressed.write_bytes(b"compressed")
    result = SimpleNamespace(input_path=original, output_path=compressed)
    with pytest.raises(ValueError, match="entrada original"):
        if move == "cli":
            _move_result(result, original)
        else:
            # GUI names its destination after the output, so simulate a conflicting input.
            result.input_path = tmp_path / "destination" / compressed.name
            result.input_path.parent.mkdir()
            result.input_path.write_bytes(b"another original")
            App._move_result(None, result, result.input_path.parent)
    assert original.read_bytes() == b"original"
    assert compressed.read_bytes() == b"compressed"
    assert result.input_path.read_bytes() in (b"original", b"another original")


def test_same_name_moves_preserve_both_results(tmp_path):
    destination = tmp_path / "output" / "book.compressed.zip"
    for name, data in [("first", b"first"), ("second", b"second")]:
        source = tmp_path / name / destination.name
        source.parent.mkdir()
        source.write_bytes(data)
        move_output_file(source, destination, tmp_path / name / "book.zip")
        assert not source.exists()
    assert {p.read_bytes() for p in destination.parent.iterdir()} == {b"first", b"second"}


def test_copy_failure_preserves_source_and_existing_destination(tmp_path, monkeypatch):
    source = tmp_path / "source.zip"
    source.write_bytes(b"source")
    destination = tmp_path / "output.zip"
    destination.write_bytes(b"previous")

    def fail(original, output):
        output.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr("src.utils.fs.shutil.copyfileobj", fail)
    with pytest.raises(OSError, match="disk full"):
        move_output_file(source, destination, tmp_path / "input.zip")
    assert source.read_bytes() == b"source"
    assert destination.read_bytes() == b"previous"
    assert len(list(tmp_path.iterdir())) == 2


def test_parallel_output_reservations_are_unique(tmp_path):
    def reserve(index):
        path, stream = open_unique_output(tmp_path / "book.compressed.zip")
        with stream:
            stream.write(str(index).encode())
        return path

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(reserve, range(12)))
    assert len(set(paths)) == 12
    assert {p.read_bytes() for p in paths} == {str(i).encode() for i in range(12)}


@pytest.mark.parametrize("member", ["../outside/", "../outside.txt", "..\\outside.txt", "/outside.txt", "C:/outside.txt", "OPS/file.txt:stream"])
def test_unsafe_archive_paths_are_rejected(tmp_path, member):
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        archive.writestr(member, b"unsafe")
        archive.writestr("OPS/chapter.txt", b"chapter")
    destination = tmp_path / "extracted"
    destination.mkdir()
    with ZipFile(content) as archive:
        errors = _safe_extractall(archive, destination, tmp_path / "input.zip")
    assert len(errors) == 1
    assert (destination / "OPS/chapter.txt").read_bytes() == b"chapter"
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_repeated_processing_preserves_input_and_epub_structure(tmp_path):
    epub = BytesIO()
    with ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("OPS/chapter.xhtml", "<html>chapter</html>")
    original = tmp_path / "book.epub.zip"
    with ZipFile(original, "w") as archive:
        archive.writestr("book.epub", epub.getvalue())
        archive.writestr("extra.txt", "keep")
    original_bytes = original.read_bytes()

    first = process_epub_zip(original, prefer_gpu=False)
    second = process_epub_zip(original, prefer_gpu=False)

    assert original.read_bytes() == original_bytes
    assert first.output_path != second.output_path
    for result in (first, second):
        assert not result.error_results
        with ZipFile(result.output_path) as outer:
            assert outer.read("extra.txt") == b"keep"
            with ZipFile(BytesIO(outer.read("book.epub"))) as inner:
                assert inner.infolist()[0].filename == "mimetype"
                assert inner.infolist()[0].compress_type == ZIP_STORED
                assert inner.read("OPS/chapter.xhtml") == b"<html>chapter</html>"


def test_invalid_nested_epub_is_kept_whole(tmp_path):
    epub = BytesIO()
    with ZipFile(epub, "w") as archive:
        archive.writestr("../outside.txt", b"unsafe")
        archive.writestr("chapter.txt", b"chapter")
    original = tmp_path / "book.zip"
    with ZipFile(original, "w") as archive:
        archive.writestr("book.epub", epub.getvalue())
    result = process_epub_zip(original, prefer_gpu=False)
    assert result.error_results
    with ZipFile(result.output_path) as archive:
        assert archive.read("book.epub") == epub.getvalue()
