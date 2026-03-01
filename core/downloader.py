import os
import logging
import yt_dlp

logger = logging.getLogger(__name__)
from config import MAX_FILE_SIZE, COOKIE_FILE


def _get_base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_retries": 3,
        "fragment_retries": 3,
    }
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    
    return opts


def get_formats(url):
    ydl_opts = _get_base_opts()
    ydl_opts['logger'] = logging.getLogger('yt_dlp')
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get("formats") or []
        logger.error(f"URL: {url}, Format count: {len(formats)}, First 10: {[(f.get('format_id'), f.get('height'), f.get('ext')) for f in formats[:10]]}")
        return formats, info


def download_video(url, format_id, progress_hook=None):
    ydl_opts = _get_base_opts()
    ydl_opts.update({
        "format": "bestvideo+bestaudio/best" if format_id == "best" else f"{format_id}+bestaudio/best",
        "outtmpl": "/tmp/%(title)s.%(ext)s",
        "merge_output_format": "mp4",
        "progress_hooks": [progress_hook] if progress_hook else None,
    })
    
    if "twitter.com" in url or "x.com" in url:
        ydl_opts["extractor_args"] = {
            "twitter": {"lang": "en"}
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
    return filename, info


def download_audio(url, progress_hook=None):
    ydl_opts = _get_base_opts()
    ydl_opts.update({
        "format": "bestaudio/best",
        "outtmpl": "/tmp/%(title)s.%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
        }],
        "progress_hooks": [progress_hook] if progress_hook else None,
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        filename = os.path.splitext(filename)[0] + ".mp3"
    return filename, info


def get_thumbnail(url):
    ydl_opts = _get_base_opts()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        thumbnail_url = info.get("thumbnail")
    return thumbnail_url, info


def get_images(url):
    ydl_opts = _get_base_opts()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        images = info.get("thumbnail") or []
        if isinstance(images, str):
            images = [images]
        
        additional_thumbnails = info.get("additional_thumbnails") or []
        if additional_thumbnails:
            for t in additional_thumbnails:
                if t.get("url"):
                    images.append(t.get("url"))
        
        title = info.get("title", "Image")
    return images, title
