"""Tests for SFTP connection and download functionality."""

import logging
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from frc_log_uploader.config import RobotConfig
from frc_log_uploader.discovery.scanner import (
    FTPAuthError,
    FTPConnection,
    FTPConnectionError,
    FTPDataConnectionError,
    FTPError,
    FTPProtocolError,
    LogScanner,
)


def make_sftp_attr(filename, st_mode, st_size, st_mtime=None):
    """Create a mock SFTPAttributes-like object."""
    attr = MagicMock()
    attr.filename = filename
    attr.st_mode = st_mode
    attr.st_size = st_size
    attr.st_mtime = st_mtime
    return attr


class TestSFTPConnectionErrors:
    """Test SFTP connection error handling."""

    def test_sftp_connection_refused(self):
        """Test SFTP connection to refused port raises FTPConnectionError."""
        config = RobotConfig(
            address="127.0.0.1",
            port=1,
            username="lvuser",
            password="",
            connection_timeout=0.1,
            transfer_timeout=10.0,
        )

        with pytest.raises(FTPConnectionError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "connect"
        assert (
            "connection" in str(exc_info.value).lower() or "refused" in str(exc_info.value).lower()
        )

    def test_sftp_invalid_hostname(self):
        """Test SFTP connection with invalid hostname raises FTPConnectionError."""
        config = RobotConfig(
            address="invalid-hostname-that-does-not-exist-12345.local",
            port=22,
            username="lvuser",
            password="",
            connection_timeout=0.1,
            transfer_timeout=10.0,
        )

        with pytest.raises(FTPConnectionError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "connect"
        assert (
            "resolution" in str(exc_info.value).lower() or "failed" in str(exc_info.value).lower()
        )

    def test_sftp_url_address_rejected(self):
        """Test that URL-style addresses are rejected."""
        config = RobotConfig(
            address="sftp://127.0.0.1:22",
            port=22,
            username="lvuser",
            password="",
        )

        with pytest.raises(FTPConnectionError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "connect"
        assert "invalid address format" in str(exc_info.value).lower()

    def test_sftp_connection_unicode_error(self):
        """Test SFTP connection with invalid Unicode in hostname."""
        config = RobotConfig(
            address="\udcff",  # Invalid surrogate
            port=22,
            username="lvuser",
            password="",
            connection_timeout=0.1,
            transfer_timeout=10.0,
        )

        with pytest.raises(FTPConnectionError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "connect"


class TestSFTPConnectionWithMock:
    """Test SFTP connection using mocked paramiko."""

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_connection_success(self, mock_ssh_class):
        """Test successful SFTP connection and authentication."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            connection_timeout=10.0,
            transfer_timeout=30.0,
        )

        with FTPConnection(config) as conn:
            assert conn._connected
            assert conn._sftp is mock_sftp

        mock_ssh.connect.assert_called_once_with(
            hostname="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            timeout=10.0,
            allow_agent=False,
            look_for_keys=False,
        )
        mock_ssh.open_sftp.assert_called_once()
        mock_ssh.close.assert_called_once()

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_connection_wrong_password(self, mock_ssh_class):
        """Test SFTP connection with wrong password raises FTPAuthError."""
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = paramiko.AuthenticationException("530 Login incorrect")
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="wrong",
        )

        with pytest.raises(FTPAuthError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "login"
        assert "authentication failed" in str(exc_info.value).lower()

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_list_files(self, mock_ssh_class):
        """Test directory listing via SFTP listdir_attr."""
        import stat

        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_sftp.listdir_attr.return_value = [
            make_sftp_attr("test1.wpilog", stat.S_IFREG | 0o644, 1024, 1704110400),
            make_sftp_attr("test2.wpilog", stat.S_IFREG | 0o644, 2048, 1704114000),
            make_sftp_attr("subdir", stat.S_IFDIR | 0o755, 0, 1704106800),
        ]
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        with FTPConnection(config) as conn:
            files = conn.list_files("/home/lvuser/logs")

        names = [name for name, _ in files]
        assert "test1.wpilog" in names
        assert "test2.wpilog" in names
        assert "subdir" in names

        # Check type info
        type_map = {name: facts.get("type") for name, facts in files}
        assert type_map["subdir"] == "dir"
        assert type_map["test1.wpilog"] == "file"

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_list_files_failure(self, mock_ssh_class):
        """Test directory listing failure raises FTPProtocolError."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_sftp.listdir_attr.side_effect = FileNotFoundError("No such directory")
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        with FTPConnection(config) as conn:
            with pytest.raises(FTPProtocolError) as exc_info:
                conn.list_files("/home/lvuser/logs")

        assert exc_info.value.operation == "list"

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_get_file_size(self, mock_ssh_class):
        """Test getting file size via SFTP stat."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_attr = MagicMock()
        mock_attr.st_size = 2048
        mock_sftp.stat.return_value = mock_attr
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        with FTPConnection(config) as conn:
            size = conn.get_file_size("/home/lvuser/logs/test.wpilog")

        assert size == 2048

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_get_file_size_nonexistent(self, mock_ssh_class):
        """Test getting size of non-existent file raises FTPDataConnectionError."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_sftp.stat.side_effect = FileNotFoundError("No such file")
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        with FTPConnection(config) as conn:
            with pytest.raises(FTPDataConnectionError) as exc_info:
                conn.get_file_size("/home/lvuser/logs/nonexistent.wpilog")

        assert exc_info.value.operation == "size"

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_download_file(self, mock_ssh_class, tmp_path):
        """Test file download with integrity verification."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        # stat returns expected size
        mock_attr = MagicMock()
        mock_attr.st_size = 3000
        mock_sftp.stat.return_value = mock_attr

        # open returns a file-like object that yields chunks then empty
        mock_remote_file = MagicMock()
        mock_remote_file.__enter__.return_value = mock_remote_file
        mock_remote_file.read.side_effect = [b"a" * 1000, b"b" * 1000, b"c" * 1000, b""]
        mock_sftp.open.return_value = mock_remote_file

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )
        local_path = tmp_path / "downloaded.wpilog"

        with FTPConnection(config) as conn:
            conn.download_file("/home/lvuser/logs/test.wpilog", local_path)

        mock_sftp.open.assert_called_once_with("/home/lvuser/logs/test.wpilog", "rb")
        # 3 data chunks + up to 3 empty reads (robustness against transient empty reads)
        assert mock_remote_file.read.call_count >= 4
        assert local_path.exists()
        assert local_path.read_bytes() == b"a" * 1000 + b"b" * 1000 + b"c" * 1000

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_download_file_integrity_mismatch(self, mock_ssh_class, tmp_path):
        """Test download integrity check fails when byte count doesn't match."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        # stat says 5000 bytes but we only deliver 1000
        mock_attr = MagicMock()
        mock_attr.st_size = 5000
        mock_sftp.stat.return_value = mock_attr

        mock_remote_file = MagicMock()
        mock_remote_file.__enter__.return_value = mock_remote_file
        mock_remote_file.read.side_effect = [b"a" * 1000, b""]
        mock_sftp.open.return_value = mock_remote_file

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )
        local_path = tmp_path / "downloaded.wpilog"

        with FTPConnection(config) as conn:
            with pytest.raises(FTPDataConnectionError) as exc_info:
                conn.download_file("/home/lvuser/logs/test.wpilog", local_path)

        assert exc_info.value.operation == "download"
        assert "integrity" in str(exc_info.value).lower()
        assert not local_path.exists()

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_download_file_not_found(self, mock_ssh_class, tmp_path):
        """Test download of non-existent file raises FTPProtocolError and cleans up."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        mock_sftp.stat.side_effect = FileNotFoundError("No such file")

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )
        local_path = tmp_path / "downloaded.wpilog"
        local_path.write_text("partial")

        with FTPConnection(config) as conn:
            with pytest.raises(FTPProtocolError) as exc_info:
                conn.download_file("/home/lvuser/logs/nonexistent.wpilog", local_path)

        assert exc_info.value.operation == "download"
        assert not local_path.exists()

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_connection_cleanup_on_error(self, mock_ssh_class):
        """Test that connection is properly cleaned up on error."""
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = paramiko.AuthenticationException("530 Login incorrect")
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="wrong",
        )

        with pytest.raises(FTPAuthError):
            with FTPConnection(config):
                pass

        # Verify cleanup was called (close)
        mock_ssh.close.assert_called()

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sftp_context_manager_cleanup_on_exception(self, mock_ssh_class):
        """Test that context manager properly cleans up on exception."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(address="10.100.79.2", port=22, username="lvuser", password="")

        try:
            with FTPConnection(config) as conn:
                assert conn._connected
                raise RuntimeError("Simulated error")
        except RuntimeError:
            pass

        mock_ssh.close.assert_called()

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    @patch("frc_log_uploader.discovery.scanner.paramiko.util.log_to_file")
    def test_sftp_debug_logging(self, mock_log_to_file, mock_ssh_class, caplog):
        """Test that debug logging can be enabled."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(address="10.100.79.2", port=22, username="lvuser", password="")

        with caplog.at_level(logging.DEBUG):
            with FTPConnection(config, debug=True):
                pass

        # Check that paramiko debug logging was enabled
        mock_log_to_file.assert_called_once_with("sftp_debug.log")


class TestPathSanitization:
    """Test remote path sanitization against traversal and injection."""

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sanitize_blocks_newlines(self, mock_ssh_class):
        """Newlines in remote path are rejected (command injection)."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(address="10.100.79.2", port=22, username="lvuser", password="")
        with FTPConnection(config) as conn:
            with pytest.raises(FTPProtocolError) as exc_info:
                conn._sanitize_remote_path("/home/lvuser/logs\nQUIT")
            assert exc_info.value.operation == "sanitize"

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sanitize_blocks_parent_traversal(self, mock_ssh_class):
        """``..`` components in remote path are rejected."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )
        with FTPConnection(config) as conn:
            with pytest.raises(FTPProtocolError) as exc_info:
                conn._sanitize_remote_path("/home/lvuser/logs/../../etc/passwd")
            assert exc_info.value.operation == "sanitize"

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sanitize_confines_to_log_directory(self, mock_ssh_class):
        """Paths outside the configured log directory are rejected."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )
        with FTPConnection(config) as conn:
            with pytest.raises(FTPProtocolError):
                conn._sanitize_remote_path("/home/lvuser/other/wpilog")

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_sanitize_allows_within_log_directory(self, mock_ssh_class):
        """Paths within the log directory pass validation."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )
        with FTPConnection(config) as conn:
            result = conn._sanitize_remote_path("/home/lvuser/logs/match1.wpilog")
            assert result == "/home/lvuser/logs/match1.wpilog"


class TestLogScanner:
    """Test LogScanner functionality with mocked SFTP."""

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_log_scanner_discover(self, mock_ssh_class):
        """Test LogScanner discover functionality."""
        import stat

        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        # Track cwd-equivalent path calls
        listed_paths = []

        def mock_listdir_attr(path):
            listed_paths.append(path)
            if path == "/home/lvuser/logs":
                return [
                    make_sftp_attr("match1.wpilog", stat.S_IFREG | 0o644, 1024, 1704110400),
                    make_sftp_attr("match2.wpilog", stat.S_IFREG | 0o644, 2048, 1704114000),
                    make_sftp_attr("subdir", stat.S_IFDIR | 0o755, 0, 1704106800),
                ]
            return []

        mock_sftp.listdir_attr.side_effect = mock_listdir_attr

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        scanner = LogScanner(config)
        discovery = scanner.discover()

        assert len(discovery.logs) >= 3
        log_names = [log.name for log in discovery.logs]
        assert "match1.wpilog" in log_names
        assert "match2.wpilog" in log_names
        assert "subdir" in log_names

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_log_scanner_check_file_stable(self, mock_ssh_class):
        """Test file stability check."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        mock_attr1 = MagicMock()
        mock_attr1.st_size = 1024
        mock_attr2 = MagicMock()
        mock_attr2.st_size = 1024
        mock_sftp.stat.side_effect = [mock_attr1, mock_attr2]

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        scanner = LogScanner(config)
        is_stable = scanner.check_file_stable("/home/lvuser/logs/stable.wpilog", 0.01)

        assert is_stable
        assert mock_sftp.stat.call_count == 2

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_log_scanner_check_file_unstable(self, mock_ssh_class):
        """Test file stability check for growing file."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        mock_attr1 = MagicMock()
        mock_attr1.st_size = 1024
        mock_attr2 = MagicMock()
        mock_attr2.st_size = 2048
        mock_sftp.stat.side_effect = [mock_attr1, mock_attr2]

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        scanner = LogScanner(config)
        is_stable = scanner.check_file_stable("/home/lvuser/logs/growing.wpilog", 0.01)

        assert not is_stable

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_log_scanner_check_file_nonexistent(self, mock_ssh_class):
        """Test file stability check for non-existent file returns False."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        mock_sftp.stat.side_effect = FileNotFoundError("No such file")

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        scanner = LogScanner(config)
        is_stable = scanner.check_file_stable("/home/lvuser/logs/nonexistent.wpilog", 0.01)

        assert not is_stable

    @patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient")
    def test_log_scanner_check_file_stable_reuses_connection(self, mock_ssh_class):
        """Test that check_file_stable reuses a supplied connection."""
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        mock_attr1 = MagicMock()
        mock_attr1.st_size = 1024
        mock_attr2 = MagicMock()
        mock_attr2.st_size = 1024
        mock_sftp.stat.side_effect = [mock_attr1, mock_attr2]

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        scanner = LogScanner(config)
        with FTPConnection(config) as conn:
            is_stable = scanner.check_file_stable("/home/lvuser/logs/stable.wpilog", 0.01, conn)

        assert is_stable
        # Only one SSH connection should be opened (not two)
        mock_ssh.connect.assert_called_once()


class TestFTPErrorHierarchy:
    """Test FTP error hierarchy and attributes."""

    def test_ftp_error_base(self):
        """Test base FTPError has operation and original."""
        try:
            raise FTPError("test message", "test_op", ValueError("original"))
        except FTPError as e:
            assert e.operation == "test_op"
            assert isinstance(e.original, ValueError)

    def test_ftp_error_subclasses(self):
        """Test all FTP error subclasses can be instantiated."""
        for error_class in [
            FTPConnectionError,
            FTPAuthError,
            FTPProtocolError,
            FTPDataConnectionError,
        ]:
            try:
                raise error_class("test", "op", ValueError("orig"))
            except error_class as e:
                assert isinstance(e, FTPError)
                assert e.operation == "op"

    def test_ftp_error_without_original(self):
        """Test FTPError can be created without original exception."""
        try:
            raise FTPError("test message", "test_op")
        except FTPError as e:
            assert e.original is None
