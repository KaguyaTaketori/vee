import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, TypedDict


class DownloadStatus(Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


STATUS_EMOJI: dict[DownloadStatus, str] = {
    DownloadStatus.QUEUED:      "⏳",
    DownloadStatus.DOWNLOADING: "⬇️",
    DownloadStatus.PROCESSING:  "⚙️",
    DownloadStatus.UPLOADING:   "📤",
    DownloadStatus.COMPLETED:   "✅",
    DownloadStatus.FAILED:      "❌",
    DownloadStatus.CANCELLED:   "🚫",
}


@dataclass
class DownloadTask:
    task_id: str
    user_id: int
    url: str
    download_type: str
    format_id: Optional[str] = None
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    max_retries: int = 2
    retry_count: int = 0
    retry_delay: float = 5.0


class TaskContext(TypedDict):
    query: Any
    processing_msg: Any
    context: Any
