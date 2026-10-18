"""Log discovery scanner using SFTP (paramiko), matching WPILib DataLogTool."""

import logging
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

import paramiko

from ..config import RobotConfig
from .models import DiscoveryResult, LogFile

logger = logging.getLogger(__name__)


class FTPError(Exception):
    """Base exception for SFTP-related errors."""

    def __init__(self, message: str, operation: str, original: Exception | None = None):
        super().__init__(message)
        self.operation = operation
        self.original = original


class FTPConnectionError(FTPError):
    """SFTP connection/TCP-level error."""

    pass


class FTPAuthError(FTPError):
    """SFTP authentication error."""

    pass


class FTPProtocolError(FTPError):
    """SFTP protocol-level error (command rejected, etc.)."""

    pass


class FTPDataConnectionError(FTPError):
    """SFTP data connection error (LIST, RETR, etc.)."""

    pass


class FTPConnection:
    """Wrapper around paramiko SFTP for easier usage with improved error handling."""

    def __init__(self, config: RobotConfig, debug: bool = False):
        self.config = config
        self.debug = debug
        self._ssh: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._connected = False

    def connect(self) -> None:
        """Establish SFTP connection to the roboRIO."""
        logger.info(f"Connecting to roboRIO at {self.config.address}:{self.config.port}")

        if self._is_url(self.config.address):
            raise FTPConnectionError(
                f"Invalid address format: '{self.config.address}'. "
                "Provide hostname or IP address only (no scheme or port suffix).",
                "connect",
            )

        self._ssh = paramiko.SSHClient()
        # Match WPILib DataLogTool: do not strictly verify host keys.
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if self.debug:
            paramiko.util.log_to_file("sftp_debug.log")
            logger.debug("SFTP debug logging enabled")

        try:
            self._ssh.connect(
                hostname=self.config.address,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                timeout=self.config.connection_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            self._connected = True

            # Open SFTP subsystem session
            self._sftp = self._ssh.open_sftp()
            # Apply transfer timeout to the underlying socket
            if self._sftp.sock:
                self._sftp.sock.settimeout(self.config.transfer_timeout)

            logger.info("Connected successfully")

        except paramiko.AuthenticationException as e:
            self._safe_disconnect()
            raise FTPAuthError(f"SFTP authentication failed: {e}", "login", e)
        except paramiko.SSHException as e:
            self._safe_disconnect()
            raise FTPConnectionError(f"SFTP connection failed: {e}", "connect", e)
        except (TimeoutError, ConnectionRefusedError, OSError) as e:
            self._safe_disconnect()
            raise FTPConnectionError(
                f"TCP connection failed to {self.config.address}:{self.config.port}: {e}",
                "connect",
                e,
            )
        except Exception as e:
            self._safe_disconnect()
            raise FTPConnectionError(f"Unexpected error during SFTP connection: {e}", "connect", e)

    def _is_url(self, address: str) -> bool:
        """Check if address contains a URL scheme."""
        return address.startswith(("ftp://", "ftps://", "http://", "https://", "sftp://"))

    def _safe_disconnect(self) -> None:
        """Safely disconnect, suppressing errors."""
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None
        self._connected = False

    def disconnect(self) -> None:
        """Close SFTP connection."""
        self._safe_disconnect()

    def __enter__(self) -> "FTPConnection":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def _require_connection(self) -> paramiko.SFTPClient:
        """Ensure we have an active connection and return the SFTP object."""
        if not self._sftp or not self._connected:
            raise FTPConnectionError("Not connected to SFTP server", "operation")
        return self._sftp

    def _sanitize_remote_path(self, path: str) -> str:
        """Validate a remote path to prevent traversal and command injection.

        Protects the local machine by ensuring the requested remote path:
        * contains no newline/CR characters (command injection)
        * contains no ".." components (parent directory traversal)
        * stays within the configured log_directory
        """
        if "\n" in path or "\r" in path:
            raise FTPProtocolError(f"Remote path contains newline characters: {path!r}", "sanitize")

        normalized = path.replace("\\", "/")
        parts = normalized.split("/")
        if ".." in parts:
            raise FTPProtocolError(
                f"Remote path contains parent traversal '..': {path!r}", "sanitize"
            )

        # Confine to the configured log directory
        log_dir = self.config.log_directory.rstrip("/")
        if log_dir and log_dir != "/":
            if normalized != log_dir and not normalized.startswith(log_dir + "/"):
                raise FTPProtocolError(
                    f"Remote path '{path}' is outside log directory '{log_dir}'",
                    "sanitize",
                )
        return normalized

    def list_files(self, path: str) -> list[tuple[str, dict]]:
        """List files in a directory using SFTP listdir_attr."""
        sftp = self._require_connection()
        path = self._sanitize_remote_path(path)

        files: list[tuple[str, dict]] = []
        try:
            for attr in sftp.listdir_attr(path):
                name = attr.filename
                if name in (".", ".."):
                    continue

                st_mode = attr.st_mode
                is_dir = stat.S_ISDIR(st_mode) if st_mode is not None else False
                facts: dict = {"type": "dir" if is_dir else "file"}

                if attr.st_mtime:
                    facts["modify"] = datetime.fromtimestamp(attr.st_mtime, tz=UTC).strftime(
                        "%Y%m%d%H%M%S"
                    )

                if not is_dir:
                    st_size = attr.st_size
                    facts["size"] = str(st_size) if st_size is not None else "0"

                files.append((name, facts))
            return files
        except FileNotFoundError as e:
            raise FTPProtocolError(f"Directory not found: '{path}': {e}", "list", e)
        except PermissionError as e:
            raise FTPProtocolError(f"Permission denied listing '{path}': {e}", "list", e)
        except (OSError, paramiko.SFTPError) as e:
            raise FTPDataConnectionError(
                f"SFTP data connection failed during listing of '{path}': {e}",
                "list",
                e,
            )

    def get_file_size(self, path: str) -> int:
        """Get the size of a file. Raises if the file cannot be stat'd."""
        sftp = self._require_connection()
        path = self._sanitize_remote_path(path)
        try:
            attr = sftp.stat(path)
            st_size = attr.st_size
            return st_size if st_size is not None else 0
        except FileNotFoundError as e:
            raise FTPDataConnectionError(f"File not found: '{path}': {e}", "size", e)
        except (OSError, paramiko.SFTPError) as e:
            raise FTPDataConnectionError(f"SFTP stat failed for '{path}': {e}", "size", e)

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Download a file from the robot with integrity verification."""
        sftp = self._require_connection()
        remote_path = self._sanitize_remote_path(remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Get expected size for integrity verification
        try:
            expected_size = sftp.stat(remote_path).st_size
        except FileNotFoundError as e:
            if local_path.exists():
                local_path.unlink(missing_ok=True)
            raise FTPProtocolError(
                f"SFTP server rejected download of '{remote_path}': file not found",
                "download",
                e,
            )
        except (OSError, paramiko.SFTPError) as e:
            if local_path.exists():
                local_path.unlink(missing_ok=True)
            raise FTPDataConnectionError(
                f"SFTP stat failed for '{remote_path}': {e}", "download", e
            )

        try:
            with sftp.open(remote_path, "rb") as remote_file:
                with open(local_path, "wb") as local_file:
                    total = 0
                    chunk_size = 32 * 1024
                    while True:
                        data = remote_file.read(chunk_size)
                        if not data:
                            break
                        local_file.write(data)
                        total += len(data)

            # Integrity verification: confirm all expected bytes were received
            if total != expected_size:
                local_path.unlink(missing_ok=True)
                raise FTPDataConnectionError(
                    f"Download integrity check failed for '{remote_path}': "
                    f"expected {expected_size} bytes, received {total}",
                    "download",
                )

            logger.debug(f"Downloaded {remote_path} ({total} bytes) to {local_path}")
        except FTPError:
            # Re-raise our custom errors as-is (don't double-wrap)
            raise
        except (OSError, paramiko.SFTPError, paramiko.SSHException) as e:
            if local_path.exists():
                local_path.unlink(missing_ok=True)
            raise FTPDataConnectionError(
                f"SFTP data connection failed during download of '{remote_path}': {e}",
                "download",
                e,
            )
        except Exception as e:
            if local_path.exists():
                local_path.unlink(missing_ok=True)
            raise FTPError(f"Unexpected error downloading '{remote_path}': {e}", "download", e)


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
        logs: list[LogFile] = []
        try:
            items = conn.list_files(path)
        except FTPError as e:
            logger.error(f"Failed to list directory {path}: {e}")
            return logs
        except Exception as e:
            logger.error(f"Unexpected error listing directory {path}: {e}")
            return logs

        for name, facts in items:
            if name in (".", ".."):
                continue

            full_path = f"{path.rstrip('/')}/{name}"
            is_dir = facts.get("type") == "dir"

            # Parse modification time
            modify_str = facts.get("modify", "")
            modified_time = self._parse_ftp_time(modify_str)

            # Get size from listing facts (avoids an extra stat round-trip per file)
            if is_dir:
                size = 0
            else:
                size_str = facts.get("size")
                size = int(size_str) if size_str is not None else 0

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
        """Parse SFTP modification time (YYYYMMDDHHMMSS)."""
        if not time_str or len(time_str) < 14:
            return datetime.now()
        try:
            return datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        except ValueError:
            return datetime.now()

    def check_file_stable(
        self, remote_path: str, interval: float, conn: FTPConnection | None = None
    ) -> bool:
        """Check if a file is stable (not being written to).

        If a connection is supplied it is reused; otherwise a short-lived
        connection is opened for the check.
        """
        if conn is not None:
            try:
                size1 = conn.get_file_size(remote_path)
            except FTPDataConnectionError:
                return False
            time.sleep(interval)
            try:
                size2 = conn.get_file_size(remote_path)
            except FTPDataConnectionError:
                return False
            return size1 == size2 and size1 > 0

        with FTPConnection(self.config) as c:
            try:
                size1 = c.get_file_size(remote_path)
            except FTPDataConnectionError:
                return False
            time.sleep(interval)
            try:
                size2 = c.get_file_size(remote_path)
            except FTPDataConnectionError:
                return False
            return size1 == size2 and size1 > 0
