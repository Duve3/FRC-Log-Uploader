"""Log downloader - handles robot to laptop transfers."""

import logging
import tempfile
import time
from pathlib import Path

from ..config import RobotConfig
from ..discovery import LogFile, LogScanner

logger = logging.getLogger(__name__)


class DownloadResult:
    """Result of a download operation."""

    def __init__(
        self,
        log_file: LogFile,
        local_path: Path,
        success: bool,
        error: str | None = None,
    ):
        self.log_file = log_file
        self.local_path = local_path
        self.success = success
        self.error = error

    def __repr__(self) -> str:
        status = "OK" if self.success else f"FAILED: {self.error}"
        return f"DownloadResult({self.log_file.name}, {status})"


class LogDownloader:
    """Downloads log files from the roboRIO."""

    def __init__(self, config: RobotConfig, staging_dir: Path | None = None):
        self.config = config
        self.staging_dir = staging_dir or Path(tempfile.gettempdir()) / "frc-log-uploader"
        self.scanner = LogScanner(config)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def download(self, log_file: LogFile, conn=None) -> DownloadResult:
        """Download a single log file or directory.

        Args:
            log_file: The log file to download
            conn: Optional existing FTPConnection to reuse
        """
        logger.info(f"Downloading {log_file.name} ({log_file.size / 1024 / 1024:.1f} MB)")

        local_path = self.staging_dir / log_file.name

        try:
            if log_file.is_directory:
                self._download_directory(log_file, local_path, conn)
            else:
                self._download_file(log_file, local_path, conn)

            logger.info(f"Downloaded {log_file.name} to {local_path}")
            return DownloadResult(log_file, local_path, success=True)

        except Exception as e:
            logger.error(f"Failed to download {log_file.name}: {e}")
            # Clean up partial download
            if local_path.exists():
                if local_path.is_dir():
                    import shutil

                    shutil.rmtree(local_path)
                else:
                    local_path.unlink()
            return DownloadResult(log_file, local_path, success=False, error=str(e))

    def _download_file(self, log_file: LogFile, local_path: Path, conn=None) -> None:
        """Download a single file.

        Args:
            log_file: The log file to download
            local_path: Local destination path
            conn: Optional existing FTPConnection to reuse. If not provided, creates a new one.
        """
        from ..discovery import FTPConnection

        if conn is not None:
            conn.download_file(log_file.full_remote_path, local_path)
        else:
            with FTPConnection(self.config) as new_conn:
                new_conn.download_file(log_file.full_remote_path, local_path)

    def _download_directory(self, log_dir: LogFile, local_dir: Path, conn=None) -> None:
        """Download a directory recursively.

        Args:
            log_dir: The log directory to download
            local_dir: Local destination directory
            conn: Optional existing FTPConnection to reuse. If not provided, creates new ones.
        """
        from ..discovery import FTPConnection

        local_dir.mkdir(parents=True, exist_ok=True)

        if conn is not None:
            items = conn.list_files(log_dir.full_remote_path)
        else:
            with FTPConnection(self.config) as new_conn:
                items = new_conn.list_files(log_dir.full_remote_path)

        for name, facts in items:
            if name in (".", ".."):
                continue

            is_subdir = facts.get("type") == "dir"
            remote_path = f"{log_dir.full_remote_path}/{name}"
            local_path = local_dir / name

            if is_subdir:
                sub_log = LogFile(
                    remote_path=log_dir.full_remote_path,
                    name=name,
                    size=0,
                    modified_time=log_dir.modified_time,
                    is_directory=True,
                )
                self._download_directory(sub_log, local_path, conn)
            else:
                self._download_file(
                    log_file=LogFile(
                        remote_path=remote_path,
                        name=name,
                        size=0,
                        modified_time=log_dir.modified_time,
                        is_directory=False,
                    ),
                    local_path=local_path,
                    conn=conn,
                )

    def download_multiple(self, log_files: list[LogFile], conn=None) -> list[DownloadResult]:
        """Download multiple log files.

        Args:
            log_files: List of log files to download
            conn: Optional existing FTPConnection to reuse for all downloads
        """
        results = []
        for log_file in log_files:
            result = self.download(log_file, conn)
            results.append(result)
            if not result.success:
                logger.warning(f"Download failed for {log_file.name}: {result.error}")
        return results

    def cleanup_staging(self) -> None:
        """Remove all files in staging directory."""
        import shutil

        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
            self.staging_dir.mkdir(parents=True, exist_ok=True)

    def wait_for_stable(self, log_file: LogFile, interval: float, max_wait: float = 60.0) -> bool:
        """Wait for a log file to become stable."""
        logger.info(f"Waiting for {log_file.name} to stabilize...")
        start_time = time.time()

        while time.time() - start_time < max_wait:
            if self.scanner.check_file_stable(log_file.full_remote_path, interval):
                logger.info(f"{log_file.name} is stable")
                return True
            logger.debug(f"{log_file.name} still changing, waiting...")
            time.sleep(interval)

        logger.warning(f"Timeout waiting for {log_file.name} to stabilize")
        return False
