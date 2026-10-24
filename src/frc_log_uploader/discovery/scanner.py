"""Log discovery scanner using SFTP (paramiko), matching WPILib DataLogTool."""

import logging
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

import paramiko

from ..config import RobotConfig
from ..robot.connection import FTP_PORT, SSH_SFTP_PORT
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
        """Establish SFTP connection to the roboRIO on the configured port.

        Raises:
            FTPConnectionError: If the connection fails.
            FTPAuthError: If authentication fails.
        """
        port = self.config.port
        logger.info(f"Connecting to roboRIO at {self.config.address}:{port}")

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
                port=port,
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

            logger.info(f"Connected successfully on port {port}")

        except paramiko.AuthenticationException as e:
            self._safe_disconnect()
            raise FTPAuthError(f"SFTP authentication failed: {e}", "login", e)
        except paramiko.SSHException as e:
            self._safe_disconnect()
            error_str = str(e).lower()
            if "banner" in error_str and port == FTP_PORT:
                # Connected to FTP server on port 21, not an SSH server
                raise FTPConnectionError(
                    f"Connected to {self.config.address}:{port} but it is running FTP, not SSH/SFTP. "
                    f"roboRIO FTP runs on port {FTP_PORT}; SFTP requires SSH on port {SSH_SFTP_PORT}. "
                    f"Set port={SSH_SFTP_PORT} and use_ftp=false in your config, and ensure SSH is "
                    f"enabled on the roboRIO.",
                    "connect",
                    e,
                )
            if "banner" in error_str:
                # Connected to port 22 but no SSH banner — SSH may not be enabled
                raise FTPConnectionError(
                    f"Connected to {self.config.address}:{port} but did not receive an SSH protocol banner. "
                    f"SSH/SFTP may not be enabled on the roboRIO. Enable SSH via the roboRIO web "
                    f"dashboard or imaging tool, then try again.",
                    "connect",
                    e,
                )
            raise FTPConnectionError(f"SFTP connection failed: {e}", "connect", e)
        except (TimeoutError, ConnectionRefusedError, OSError) as e:
            self._safe_disconnect()
            raise FTPConnectionError(
                f"TCP connection failed to {self.config.address}:{port}: {e}",
                "connect",
                e,
            )
        except Exception as e:
            self._safe_disconnect()
            raise FTPConnectionError(f"Unexpected error during SFTP connection: {e}", "connect", e)

    def probe_connection(self) -> int | None:
        """Probe common roboRIO ports and return the first open SSH/SFTP port.

        Tries port 22 (SSH/SFTP) first, then port 21 (FTP) as a diagnostic.
        Returns None if no ports are open.

        This is used by the robot locator to discover the correct port before
        attempting an SFTP connection.
        """
        from ..robot.connection import probe_ssh_port

        return probe_ssh_port(self.config.address)

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
            stat_attr = sftp.stat(remote_path)
            expected_size = stat_attr.st_size
            if expected_size is None:
                expected_size = 0
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

        expected_size_mb = expected_size / (1024 * 1024)
        logger.info(
            f"Starting download of '{remote_path}' ({expected_size_mb:.1f} MB) to {local_path}"
        )

        try:
            with sftp.open(remote_path, "rb") as remote_file:
                with open(local_path, "wb") as local_file:
                    total = 0
                    chunk_size = 256 * 1024
                    consecutive_empty = 0
                    last_logged = 0
                    while True:
                        try:
                            data = remote_file.read(chunk_size)
                        except StopIteration:
                            # Defensive: some mock SFTP implementations raise StopIteration
                            # instead of returning b"" at EOF. Real paramiko returns b"".
                            data = b""
                        if not data:
                            consecutive_empty += 1
                            # Allow a few empty reads before treating as EOF
                            # (SFTP servers can return empty on transient conditions)
                            if consecutive_empty >= 3:
                                break
                            continue
                        consecutive_empty = 0
                        local_file.write(data)
                        total += len(data)

                        # Log progress every ~10MB or when complete
                        if total - last_logged >= 10 * 1024 * 1024 or total == expected_size:
                            pct = (total / expected_size * 100) if expected_size > 0 else 0
                            logger.debug(
                                f"Downloaded {total / (1024 * 1024):.1f}/{expected_size_mb:.1f} MB ({pct:.0f}%) of '{remote_path}'"
                            )
                            last_logged = total

            # Integrity verification: confirm all expected bytes were received
            if total != expected_size:
                local_path.unlink(missing_ok=True)
                raise FTPDataConnectionError(
                    f"Download integrity check failed for '{remote_path}': "
                    f"expected {expected_size} bytes ({expected_size_mb:.1f} MB), "
                    f"received {total} bytes ({total / (1024 * 1024):.1f} MB)",
                    "download",
                )

            logger.info(
                f"Downloaded '{remote_path}' ({total / (1024 * 1024):.1f} MB) to {local_path}"
            )
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
