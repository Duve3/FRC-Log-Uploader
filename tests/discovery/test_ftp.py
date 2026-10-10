"""Tests for FTP connection and download functionality."""

import logging
from unittest.mock import MagicMock, patch

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


class TestFTPConnectionErrors:
    """Test FTP connection error handling."""

    def test_ftp_connection_refused(self):
        """Test FTP connection to refused port raises FTPConnectionError."""
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

    def test_ftp_invalid_hostname(self):
        """Test FTP connection with invalid hostname raises FTPConnectionError."""
        config = RobotConfig(
            address="invalid-hostname-that-does-not-exist-12345.local",
            port=21,
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

    def test_ftp_url_address_rejected(self):
        """Test that URL-style addresses are rejected."""
        config = RobotConfig(
            address="ftp://127.0.0.1:21",
            port=21,
            username="lvuser",
            password="",
        )

        with pytest.raises(FTPConnectionError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "connect"
        assert "invalid address format" in str(exc_info.value).lower()

    def test_ftp_connection_unicode_error(self):
        """Test FTP connection with invalid Unicode in hostname."""
        config = RobotConfig(
            address="\udcff",  # Invalid surrogate
            port=21,
            username="lvuser",
            password="",
            connection_timeout=0.1,
            transfer_timeout=10.0,
        )

        with pytest.raises(FTPConnectionError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "connect"


class TestFTPConnectionWithMock:
    """Test FTP connection using mocked ftplib."""

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_connection_success(self, mock_ftp_class):
        """Test successful FTP connection and login."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(
            address="10.100.79.2",
            port=21,
            username="lvuser",
            password="",
            connection_timeout=10.0,
            transfer_timeout=30.0,
        )

        with FTPConnection(config) as conn:
            assert conn._connected
            assert conn.ftp is mock_ftp

        mock_ftp.connect.assert_called_once_with("10.100.79.2", 21, timeout=10.0)
        mock_ftp.login.assert_called_once_with("lvuser", "")
        mock_ftp.set_pasv.assert_called_once_with(True)
        mock_ftp.quit.assert_called_once()

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_connection_wrong_password(self, mock_ftp_class):
        """Test FTP connection with wrong password raises FTPAuthError."""
        import ftplib

        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.login.side_effect = ftplib.error_perm("530 Login incorrect")
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(
            address="10.100.79.2",
            port=21,
            username="lvuser",
            password="wrong",
        )

        with pytest.raises(FTPAuthError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "login"
        assert "authentication failed" in str(exc_info.value).lower()

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_connection_vsftpd_oops_error(self, mock_ftp_class):
        """Test FTP connection handles vsftpd 'bad state' error."""
        import ftplib

        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.login.side_effect = ftplib.error_perm(
            "500 OOPS: bad state in vsf_one_process_login"
        )
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(
            address="10.100.79.2",
            port=21,
            username="lvuser",
            password="",
        )

        with pytest.raises(FTPAuthError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "login"
        assert "vsf_one_process_login" in str(exc_info.value)

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_list_files(self, mock_ftp_class):
        """Test directory listing."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.mlsd.return_value = [
            ("test1.wpilog", {"type": "file", "size": "1024"}),
            ("test2.wpilog", {"type": "file", "size": "2048"}),
            ("subdir", {"type": "dir"}),
        ]
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        with FTPConnection(config) as conn:
            files = conn.list_files(".")

        names = [name for name, _ in files]
        assert "test1.wpilog" in names
        assert "test2.wpilog" in names
        assert "subdir" in names

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_list_files_mlsd_fallback(self, mock_ftp_class):
        """Test directory listing falls back to NLST when MLSD fails."""
        import ftplib

        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.mlsd.side_effect = ftplib.error_perm("500 Command not supported")
        mock_ftp.nlst.return_value = ["test1.wpilog", "test2.wpilog"]
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        with FTPConnection(config) as conn:
            files = conn.list_files(".")

        names = [name for name, _ in files]
        assert "test1.wpilog" in names
        assert "test2.wpilog" in names
        # NLST fallback doesn't provide type info
        for _, facts in files:
            assert facts == {"type": "file"}

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_list_files_failure(self, mock_ftp_class):
        """Test directory listing failure raises FTPProtocolError."""
        import ftplib

        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.mlsd.side_effect = ftplib.error_perm("500 Error")
        mock_ftp.nlst.side_effect = ftplib.error_perm("500 Error")
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        with FTPConnection(config) as conn:
            with pytest.raises(FTPProtocolError) as exc_info:
                conn.list_files(".")

        assert exc_info.value.operation == "list"

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_get_file_size(self, mock_ftp_class):
        """Test getting file size."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.size.return_value = 2048
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        with FTPConnection(config) as conn:
            size = conn.get_file_size("test.wpilog")

        assert size == 2048

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_get_file_size_nonexistent(self, mock_ftp_class):
        """Test getting size of non-existent file returns 0."""
        import ftplib

        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.size.side_effect = ftplib.error_perm("550 File not found")
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        with FTPConnection(config) as conn:
            size = conn.get_file_size("nonexistent.wpilog")

        assert size == 0

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_download_file(self, mock_ftp_class, tmp_path):
        """Test file download."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")
        local_path = tmp_path / "downloaded.wpilog"

        with FTPConnection(config) as conn:
            conn.download_file("test.wpilog", local_path)

        mock_ftp.retrbinary.assert_called_once()
        args, kwargs = mock_ftp.retrbinary.call_args
        assert args[0] == "RETR test.wpilog"
        assert callable(args[1])

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_download_file_protocol_error(self, mock_ftp_class, tmp_path):
        """Test download protocol error cleans up partial file."""
        import ftplib

        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.retrbinary.side_effect = ftplib.error_perm("550 File not found")
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")
        local_path = tmp_path / "downloaded.wpilog"

        with FTPConnection(config) as conn:
            with pytest.raises(FTPProtocolError) as exc_info:
                conn.download_file("nonexistent.wpilog", local_path)

        assert exc_info.value.operation == "download"
        assert not local_path.exists()

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_connection_cleanup_on_error(self, mock_ftp_class):
        """Test that connection is properly cleaned up on error."""
        import ftplib

        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.login.side_effect = ftplib.error_perm("530 Login incorrect")
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(
            address="10.100.79.2",
            port=21,
            username="lvuser",
            password="wrong",
        )

        # First attempt with wrong password
        with pytest.raises(FTPAuthError):
            with FTPConnection(config):
                pass

        # Verify cleanup was called (quit or close)
        assert mock_ftp.quit.called or mock_ftp.close.called

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_context_manager_cleanup_on_exception(self, mock_ftp_class):
        """Test that context manager properly cleans up on exception."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        try:
            with FTPConnection(config) as conn:
                assert conn._connected
                raise RuntimeError("Simulated error")
        except RuntimeError:
            pass

        # Verify cleanup was called
        mock_ftp.quit.assert_called()

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_ftp_debug_logging(self, mock_ftp_class, caplog):
        """Test that debug logging can be enabled."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        with caplog.at_level(logging.DEBUG):
            with FTPConnection(config, debug=True):
                pass

        # Check that debug level was set on ftplib
        mock_ftp.set_debuglevel.assert_called_once_with(2)
        # Check that our debug message was logged
        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("debug logging enabled" in msg.lower() for msg in debug_messages)


class TestLogScanner:
    """Test LogScanner functionality with mocked FTP."""

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_log_scanner_discover(self, mock_ftp_class):
        """Test LogScanner discover functionality."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"

        # Track cwd calls to return different data for subdirectory
        cwd_calls = []

        def mock_cwd(path):
            cwd_calls.append(path)
            return None

        def mock_mlsd():
            # If we're in a subdirectory, return empty
            if cwd_calls and cwd_calls[-1] != "/home/lvuser/logs":
                return []
            # Root directory listing
            return [
                ("match1.wpilog", {"type": "file", "size": "1024", "modify": "20240101120000"}),
                ("match2.wpilog", {"type": "file", "size": "2048", "modify": "20240101130000"}),
                ("subdir", {"type": "dir", "modify": "20240101110000"}),
            ]

        mock_ftp.cwd.side_effect = mock_cwd
        mock_ftp.mlsd.side_effect = mock_mlsd
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(
            address="10.100.79.2",
            port=21,
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

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_log_scanner_check_file_stable(self, mock_ftp_class):
        """Test file stability check."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.size.side_effect = [1024, 1024]  # Same size = stable
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        scanner = LogScanner(config)
        is_stable = scanner.check_file_stable("stable.wpilog", 0.01)

        assert is_stable
        assert mock_ftp.size.call_count == 2

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_log_scanner_check_file_unstable(self, mock_ftp_class):
        """Test file stability check for growing file."""
        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.size.side_effect = [1024, 2048]  # Different size = unstable
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        scanner = LogScanner(config)
        is_stable = scanner.check_file_stable("growing.wpilog", 0.01)

        assert not is_stable

    @patch("frc_log_uploader.discovery.scanner.ftplib.FTP")
    def test_log_scanner_check_file_nonexistent(self, mock_ftp_class):
        """Test file stability check for non-existent file."""
        import ftplib

        mock_ftp = MagicMock()
        mock_ftp.getwelcome.return_value = "220 Welcome"
        mock_ftp.size.side_effect = ftplib.error_perm("550 File not found")
        mock_ftp_class.return_value = mock_ftp

        config = RobotConfig(address="10.100.79.2", port=21, username="lvuser", password="")

        scanner = LogScanner(config)
        is_stable = scanner.check_file_stable("nonexistent.wpilog", 0.01)

        assert not is_stable


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
