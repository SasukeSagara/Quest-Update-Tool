import os
import threading
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Dict, Optional

import requests


class DownloadStatus(Enum):
    QUEUED = auto()
    DOWNLOADING = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    ERROR = auto()


@dataclass
class DownloadTask:
    id: int
    url: str
    filename: str
    path: str
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: int = 0
    error: Optional[str] = None


class DownloadManager:
    """
    Простой менеджер параллельных загрузок.

    Все события пробрасываются во внешний мир через коллбек on_event,
    чтобы Tkinter-UI мог обрабатывать их в своём потоке.
    """

    def __init__(self, target_dir: str, on_event: Callable[[str, dict], None]):
        self.target_dir = target_dir
        os.makedirs(self.target_dir, exist_ok=True)

        self._on_event = on_event
        self._tasks: Dict[int, DownloadTask] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    # ----- Внешний API -----

    def add_download(self, url: str) -> DownloadTask:
        with self._lock:
            task_id = self._next_id
            self._next_id += 1

        filename = os.path.basename(url.split("?")[0]) or "firmware.zip"
        path = os.path.join(self.target_dir, filename)
        task = DownloadTask(id=task_id, url=url, filename=filename, path=path)

        with self._lock:
            self._tasks[task.id] = task

        self._fire("task_created", {"task": task})

        thread = threading.Thread(target=self._run_download, args=(task.id,), daemon=True)
        thread.start()

        return task

    def cancel(self, task_id: int) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.status in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.ERROR):
                return
            task.status = DownloadStatus.CANCELLED

        # сам поток проверит статус и завершится
        self._fire("task_cancelled", {"task_id": task_id})

    def get_task(self, task_id: int) -> Optional[DownloadTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def all_tasks(self) -> Dict[int, DownloadTask]:
        with self._lock:
            return dict(self._tasks)

    # ----- Внутренняя логика -----

    def _run_download(self, task_id: int) -> None:
        task = self.get_task(task_id)
        if not task:
            return

        # Если пользователь успел отменить до старта — ничего не делаем
        if task.status == DownloadStatus.CANCELLED:
            return

        task.status = DownloadStatus.DOWNLOADING
        self._fire("task_started", {"task": task})

        try:
            with requests.get(task.url, stream=True, timeout=30) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", "0") or "0")
                downloaded = 0

                with open(task.path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        # проверяем отмену
                        if self._is_cancelled(task_id):
                            raise _CancelledDownload()

                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total:
                            progress = int(downloaded / total * 100)
                            self._update_progress(task_id, progress)

            # если дошли сюда без исключений и без отмены — завершили успешно
            self._set_status(task_id, DownloadStatus.COMPLETED)
            self._fire("task_completed", {"task": self.get_task(task_id)})

        except _CancelledDownload:
            # удаляем недокачанный файл
            try:
                if os.path.exists(task.path):
                    os.remove(task.path)
            except OSError:
                pass
            self._set_status(task_id, DownloadStatus.CANCELLED)
            self._fire("task_cancelled_finished", {"task": self.get_task(task_id)})

        except Exception as e:  # noqa: BLE001
            self._set_status(task_id, DownloadStatus.ERROR, error=str(e))
            self._fire("task_error", {"task": self.get_task(task_id)})

    def _is_cancelled(self, task_id: int) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.status == DownloadStatus.CANCELLED)

    def _update_progress(self, task_id: int, progress: int) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.progress = max(0, min(100, progress))
        self._fire("task_progress", {"task": self.get_task(task_id)})

    def _set_status(self, task_id: int, status: DownloadStatus, error: Optional[str] = None) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.status = status
            task.error = error
        self._fire("task_status", {"task": self.get_task(task_id)})

    def _fire(self, event: str, payload: dict) -> None:
        try:
            self._on_event(event, payload)
        except Exception:
            # UI-слой не должен ломать менеджер
            pass


class _CancelledDownload(Exception):
    """Внутреннее исключение для управления потоком при отмене."""

    pass

