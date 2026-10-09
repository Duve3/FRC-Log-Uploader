"""Log discovery scanner using FTP."""

import ftplib
import logging
from datetime import datetime
from pathlib import Path

from ..config import RobotConfig
from .models import DiscoveryResult, LogFile

logger = logging.getLogger(__name__)


class FTPConnection:
    """Wrapper around ftplib.FTP for easier usage."""

    def __init__(self, config: RobotConfig):
        self.config = config
        self.ftp: ftplib.FTP | None = None

    def connect(self) -> None:
        """Establish FTP connection to the roboRIO."""
        logger.info(f"Connecting to roboRIO at {self.config.address}:{self.config.port}")
        self.ftp = ftplib.FTP()
        self.ftp.connect(self.config.address, self.config.port, timeout=10)
        self.ftp.login(self.config.username, self.config.password)
        self.ftp.set_pasv(True)
        logger.info("Connected successfully")

    def disconnect(self) -> None:
        """Close FTP connection."""
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                pass
            self.ftp = None

    def __enter__(self) -> "FTPConnection":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def list_files(self, path: str) -> list[tuple[str, dict]]:
        """List files in a directory using MLSD for detailed info."""
        if not self.ftp:
            raise RuntimeError("Not connected")

        files = []
        try:
            self.ftp.cwd(path)
            for name, facts in self.ftp.mlsd():
                files.append((name, facts))
        except ftplib.error_perm as e:
            logger.warning(f"MLSD failed for {path}: {e}, trying NLST")
            # Fallback to NLST
            try:
                names = self.ftp.nlst(path)
                for name in names:
                    if name not in (".", ".."):
                        files.append((name, {"type": "file"}))
            except ftplib.error_perm:
                pass
        return files

    def get_file_size(self, path: str) -> int:
        """Get the size of a file."""
        if not self.ftp:
            raise RuntimeError("Not connected")
        try:
            size = self.ftp.size(path)
            return size if size is not None else 0
        except ftplib.error_perm:
            return 0

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Download a file from the robot."""
        if not self.ftp:
            raise RuntimeError("Not connected")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as f:
            self.ftp.retrbinary(f"RETR {remote_path}", f.write)


class LogScanner:
    """Scans the roboRIO for log files."""

    def __init__(self, config: RobotConfig):
        self.config = config

    def discover(self) -> DiscoveryResult:
        """Discover all log files on the roboRIO."""
        with FTPConnection(self.config) as conn:
            logs = self._scan_directory(conn, self.config.log_directory)

        discovery_time = datetime.now()
        logger.info(f"Discovered {len(logs)} items on {self.config.address}")

        return DiscoveryResult(
            logs=logs,
            robot_address=self.config.address,
            log_directory=self.config.log_directory,
            discovery_time=discovery_time,
        )

    def _scan_directory(self, conn: FTPConnection, path: str) -> list[LogFile]:
        """Recursively scan a directory for log files."""
        logs = []
        try:
            items = conn.list_files(path)
        except Exception as e:
            logger.error(f"Failed to list directory {path}: {e}")
            return logs

        for name, facts in items:
            if name in (".", ".."):
                continue

            full_path = f"{path.rstrip('/')}/{name}"
            is_dir = facts.get("type") == "dir"

            # Parse modification time
            modify_str = facts.get("modify", "")
            modified_time = self._parse_ftp_time(modify_str)

            # Get size
            if is_dir:
                size = 0
            else:
                size = conn.get_file_size(full_path) or 0

            log_file = LogFile(
                remote_path=path,
                name=name,
                size=size,
                modified_time=modified_time,
                is_directory=is_dir,
            )
            logs.append(log_file)

            # Recurse into subdirectories
            if is_dir:
                sub_logs = self._scan_directory(conn, full_path)
                logs.extend(sub_logs)

        return logs

    def _parse_ftp_time(self, time_str: str) -> datetime:
        """Parse FTP MLSD modification time (YYYYMMDDHHMMSS)."""
        if not time_str or len(time_str) < 14:
            return datetime.now()
        try:
            return datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        except ValueError:
            return datetime.now()

    def check_file_stable(self, remote_path: str, interval: float) -> bool:
        """Check if a file is stable (not being written to)."""
        import time

        with FTPConnection(self.config) as conn:
            size1 = conn.get_file_size(remote_path)
            time.sleep(interval)
            size2 = conn.get_file_size(remote_path)

        return size1 == size2 and size1 > 0
