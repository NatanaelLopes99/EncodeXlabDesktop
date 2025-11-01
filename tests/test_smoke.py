from __future__ import annotations

def test_imports():
    import src  
    from src.app.gui import App  
    from src.core.epub_processor import process_epub_zip  
    from src.core.image_compressor import compress_image_file  
    from src.core.video_compressor import compress_video_file  
    from src.utils.fs import iter_files 
