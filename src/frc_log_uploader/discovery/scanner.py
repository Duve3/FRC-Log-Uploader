"""Log discovery scanner using FTP."""

import ftplib
import logging
import socket
import time
from datetime import datetime
from pathlib import Path

from ..config import RobotConfig
from .models import DiscoveryResult, LogFile

logger = logging.getLogger(__name__)


class FTPError(Exception):
    """Base exception for FTP-related errors."""

    def __init__(self, message: str, operation: str, original: Exception | None = None):
        super().__init__(message)
        self.operation = operation
        self.original = original


class FTPConnectionError(FTPError):
    """FTP connection/TCP-level error."""

    pass


class FTPAuthError(FTPError):
    """FTP authentication error."""

    pass


class FTPProtocolError(FTPError):
    """FTP protocol-level error (command rejected, etc.)."""

    pass


class FTPDataConnectionError(FTPError):
    """FTP data connection error (LIST, RETR, etc.)."""

    pass


class FTPConnection:
    """Wrapper around ftplib.FTP for easier usage with improved error handling."""

    def __init__(self, config: RobotConfig, debug: bool = False):
        self.config = config
        self.debug = debug
        self.ftp: ftplib.FTP | None = None
        self._connected = False

    def connect(self) -> None:
        """Establish FTP connection to the roboRIO."""
        logger.info(f"Connecting to roboRIO at {self.config.address}:{self.config.port}")

        # Validate address format - must be hostname or IP, not a URL
        if self._is_url(self.config.address):
            raise FTPConnectionError(
                f"Invalid address format: '{self.config.address}'. "
                "Provide hostname or IP address only (no ftp:// scheme or port suffix).",
                "connect",
            )

        self.ftp = ftplib.FTP()
        if self.debug:
            self.ftp.set_debuglevel(2)
            logger.debug("FTP debug logging enabled")

        try:
            # Set connection timeout
            self.ftp.connect(
                self.config.address, self.config.port, timeout=self.config.connection_timeout
            )
            self._connected = True

            # Read server greeting
            welcome = self.ftp.getwelcome()
            logger.debug(f"Server greeting: {welcome}")

            # Login with credentials
            try:
                # Try login - for FRC roboRIO, credentials are typically lvuser with empty password
                # or anonymous access might work
                if self.config.username and self.config.password:
                    self.ftp.login(self.config.username, self.config.password)
                elif self.config.username and not self.config.password:
                    # Try with empty password
                    self.ftp.login(self.config.username, "")
                else:
                    # Try anonymous login if no credentials specified
                    try:
                        self.ftp.login("anonymous", "")
                    except Exception:
                        self.ftp.login("lvuser", "")
            except ftplib.error_perm as e:
                # Check for specific vsftpd error
                error_str = str(e)
                if "500 OOPS: bad state in vsf_one_process_login" in error_str:
                    # This can happen if we've already sent login commands or server state is bad
                    # Try to re-establish connection cleanly
                    logger.warning("vsftpd 'bad state' error detected, attempting to reconnect")
                    self._safe_disconnect()
                    # Retry once with a fresh connection
                    self.ftp = ftplib.FTP()
                    self.ftp.connect(
                        self.config.address,
                        self.config.port,
                        timeout=self.config.connection_timeout,
                    )
                    self._connected = True
                    if self.debug:
                        self.ftp.set_debuglevel(2)
                    # Try anonymous login as fallback for roboRIO
                    try:
                        self.ftp.login("anonymous", "")
                    except Exception:
                        try:
                            self.ftp.login("lvuser", "")
                        except Exception:
                            raise FTPAuthError(
                                "Server rejected login: vsftpd 'bad state' error even after reconnect. "
                                "This may indicate a server-side issue or invalid credentials. "
                                f"Server response: {e}",
                                "login",
                                e,
                            )
                else:
                    raise FTPAuthError(
                        f"FTP authentication failed: {e}",
                        "login",
                        e,
                    )
            except (socket.gaierror, UnicodeError) as e:
                raise FTPConnectionError(
                    f"Hostname resolution failed for '{self.config.address}': {e}",
                    "connect",
                    e,
                )

            # Enable passive mode (default for ftplib, but explicit for clarity)
            self.ftp.set_pasv(True)

            # Set transfer timeout on the control socket
            if self.ftp.sock:
                self.ftp.sock.settimeout(self.config.transfer_timeout)

            logger.info("Connected successfully")

        except FTPError:
            # Re-raise our custom errors
            self._safe_disconnect()
            raise
        except (TimeoutError, ConnectionRefusedError) as e:
            self._safe_disconnect()
            raise FTPConnectionError(
                f"TCP connection failed to {self.config.address}:{self.config.port}: {e}",
                "connect",
                e,
            )
        except socket.gaierror as e:
            self._safe_disconnect()
            raise FTPConnectionError(
                f"Hostname resolution failed for '{self.config.address}': {e}",
                "connect",
                e,
            )
        except UnicodeError as e:
            self._safe_disconnect()
            raise FTPConnectionError(
                f"Invalid hostname encoding for '{self.config.address}': {e}",
                "connect",
                e,
            )
        except OSError as e:
            self._safe_disconnect()
            raise FTPConnectionError(
                f"Network error connecting to {self.config.address}:{self.config.port}: {e}",
                "connect",
                e,
            )
        except Exception as e:
            self._safe_disconnect()
            raise FTPConnectionError(
                f"Unexpected error during FTP connection: {e}",
                "connect",
                e,
            )

    def _is_url(self, address: str) -> bool:
        """Check if address contains a URL scheme."""
        return address.startswith(("ftp://", "ftps://", "http://", "https://"))

    def _safe_disconnect(self) -> None:
        """Safely disconnect, suppressing errors."""
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            finally:
                self.ftp = None
                self._connected = False

    def disconnect(self) -> None:
        """Close FTP connection."""
        if self.ftp:
            try:
                self.ftp.quit()
                logger.debug("FTP connection closed gracefully")
            except ftplib.error_proto as e:
                logger.warning(f"FTP protocol error during quit: {e}")
                try:
                    self.ftp.close()
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Error closing FTP connection: {e}")
            finally:
                self.ftp = None
                self._connected = False

    def __enter__(self) -> "FTPConnection":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def _require_connection(self) -> ftplib.FTP:
        """Ensure we have an active connection and return the FTP object."""
        if not self.ftp or not self._connected:
            raise FTPConnectionError("Not connected to FTP server", "operation")
        return self.ftp

    def list_files(self, path: str) -> list[tuple[str, dict]]:
        """List files in a directory using MLSD for detailed info."""
        ftp = self._require_connection()

        files = []
        try:
            # Change to the target directory
            try:
                ftp.cwd(path)
            except ftplib.error_perm as e:
                raise FTPProtocolError(
                    f"Failed to change to directory '{path}': {e}",
                    "cwd",
                    e,
                )

            # Try MLSD first (machine-readable listing)
            try:
                for name, facts in ftp.mlsd():
                    files.append((name, facts))
                return files
            except ftplib.error_perm as e:
                logger.warning(f"MLSD failed for {path}: {e}, trying NLST")
                # Fallback to NLST
                try:
                    names = ftp.nlst(path)
                    for name in names:
                        if name not in (".", ".."):
                            files.append((name, {"type": "file"}))
                    return files
                except ftplib.error_perm as e2:
                    raise FTPProtocolError(
                        f"Directory listing failed for '{path}': {e2}",
                        "list",
                        e2,
                    )

        except FTPError:
            raise
        except (TimeoutError, OSError) as e:
            raise FTPDataConnectionError(
                f"Data connection failed during listing of '{path}': {e}",
                "list",
                e,
            )

    def get_file_size(self, path: str) -> int:
        """Get the size of a file."""
        ftp = self._require_connection()
        try:
            size = ftp.size(path)
            return size if size is not None else 0
        except ftplib.error_perm as e:
            logger.debug(f"Could not get size for {path}: {e}")
            return 0
        except (TimeoutError, OSError) as e:
            raise FTPDataConnectionError(
                f"Data connection failed during size check for '{path}': {e}",
                "size",
                e,
            )

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Download a file from the robot."""
        ftp = self._require_connection()
        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(local_path, "wb") as f:
                ftp.retrbinary(f"RETR {remote_path}", f.write)
            logger.debug(f"Downloaded {remote_path} to {local_path}")
        except ftplib.error_perm as e:
            # Clean up partial download
            if local_path.exists():
                local_path.unlink()
            raise FTPProtocolError(
                f"FTP server rejected download of '{remote_path}': {e}",
                "download",
                e,
            )
        except (TimeoutError, OSError) as e:
            # Clean up partial download
            if local_path.exists():
                local_path.unlink()
            raise FTPDataConnectionError(
                f"Data connection failed during download of '{remote_path}': {e}",
                "download",
                e,
            )
        except Exception as e:
            # Clean up partial download
            if local_path.exists():
                local_path.unlink()
            raise FTPError(
                f"Unexpected error downloading '{remote_path}': {e}",
                "download",
                e,
            )


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
        with FTPConnection(self.config) as conn:
            size1 = conn.get_file_size(remote_path)
            time.sleep(interval)
            size2 = conn.get_file_size(remote_path)

        return size1 == size2 and size1 > 0
