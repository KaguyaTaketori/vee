import os
import shutil
import asyncio
import logging
import re as _re
import tempfile

from config import TEMP_DIR, BOT_FILE_PREFIX

logger = logging.getLogger(__name__)

_SPROTDL_PROGRESS_RE = _re.compile(r"(\d+)%\|")
_SPORTDL_DONE_RE     = _re.compile(r'Downloaded\s+"(.+?)"')


async def download_spotify(url: str, progress_hook=None) -> tuple[str, dict]:
    tmp_dir = tempfile.mkdtemp(dir=TEMP_DIR, prefix=f"{BOT_FILE_PREFIX}spotify_")

    cmd = [
        "spotdl", url,
        "--output", tmp_dir,
        "--format", "mp3",
        "--bitrate", "320k",
        "--overwrite", "force",
        "--log-level", "INFO",
    ]

    logger.info(f"Running spotdl: {' '.join(cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=tmp_dir,
        )

        title = "Unknown"
        last_percent = -1

        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            logger.debug(f"[spotdl] {line}")

            m_done = _SPORTDL_DONE_RE.search(line)
            if m_done:
                title = m_done.group(1)
                if progress_hook:
                    progress_hook({"status": "finished", "title": title})
                continue

            m_pct = _SPROTDL_PROGRESS_RE.search(line)
            if m_pct and progress_hook:
                percent = int(m_pct.group(1))
                if percent != last_percent:
                    last_percent = percent
                    progress_hook({
                        "status": "downloading",
                        "percent": float(percent),
                        "title": title,
                    })

        await process.wait()

        if process.returncode != 0:
            raise RuntimeError(f"spotdl exited with code {process.returncode}")

        files = [
            os.path.join(tmp_dir, f)
            for f in os.listdir(tmp_dir)
            if f.endswith(".mp3")
        ]
        if not files:
            raise RuntimeError("spotdl produced no output files")

        src  = max(files, key=os.path.getmtime)
        dest = os.path.join(TEMP_DIR, f"{BOT_FILE_PREFIX}{os.path.basename(src)}")
        shutil.move(src, dest)

        title = title or os.path.splitext(os.path.basename(dest))[0]
        return dest, {"title": title, "url": url}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
