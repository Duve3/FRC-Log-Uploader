"""Data models for log discovery."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, order=True)
class LogFile:
    """Represents a log file or directory on the roboRIO."""

    remote_path: str
    name: str
    size: int
    modified_time: datetime
    is_directory: bool = False

    @property
    def full_remote_path(self) -> str:
        """Get the full remote path including the name."""
        if self.remote_path.endswith("/"):
            return f"{self.remote_path}{self.name}"
        return f"{self.remote_path}/{self.name}"

    def __str__(self) -> str:
        size_mb = self.size / (1024 * 1024)
        if size_mb < 0.1:
            size_str = "<0.1 MB"
        else:
            size_str = f"{size_mb:.1f} MB"
        type_str = "DIR" if self.is_directory else "FILE"
        return f"{self.modified_time.strftime('%Y-%m-%d %H:%M')}  {type_str}  {size_str:>10}  {self.name}"


@dataclass
class DiscoveryResult:
    """Result of a log discovery operation."""

    logs: list[LogFile]
    robot_address: str
    log_directory: str
    discovery_time: datetime

    @property
    def log_files(self) -> list[LogFile]:
        """Get only files (not directories)."""
        return [log for log in self.logs if not log.is_directory]

    @property
    def log_directories(self) -> list[LogFile]:
        """Get only directories."""
        return [log for log in self.logs if log.is_directory]

    def sorted_by_time(self, newest_first: bool = True) -> list[LogFile]:
        """Return logs sorted by modification time."""
        return sorted(self.log_files, key=lambda log: log.modified_time, reverse=newest_first)
