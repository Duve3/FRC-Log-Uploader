"""Integration tests that test the full SFTP download pipeline with real file I/O.

These tests use mocked paramiko at the SSHClient level but exercise the complete
download pipeline: list_files → stat → download → integrity verification → cleanup.
Files are real files on disk, so this tests actual file reading, byte counting,
partial download cleanup, and error handling.
"""

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from frc_log_uploader.config import RobotConfig
from frc_log_uploader.discovery import (
    FTPAuthError,
    FTPConnection,
    FTPConnectionError,
    FTPDataConnectionError,
    FTPProtocolError,
    LogScanner,
)
from frc_log_uploader.discovery.models import LogFile
from frc_log_uploader.transfer import LogDownloader


def make_sftp_attr(filename, st_mode, st_size, st_mtime=None):
    """Create a mock SFTPAttributes-like object."""
    attr = MagicMock()
    attr.filename = filename
    attr.st_mode = st_mode
    attr.st_size = st_size
    attr.st_mtime = st_mtime
    return attr


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create a temporary directory with realistic log files."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Real log files with known content
    (log_dir / "match_001.wpilog").write_bytes(b"\x00\x01\x02\x03" * 256)  # 1024 bytes
    (log_dir / "match_002.wpilog").write_bytes(b"\x04\x05\x06\x07" * 512)  # 2048 bytes
    (log_dir / "match_003.wpilog").write_bytes(b"\x08\x09\x0a\x0b" * 1024)  # 4096 bytes

    # Subdirectory (CTRE Signal Logger style)
    subdir = log_dir / "ctre_logs"
    subdir.mkdir()
    (subdir / "signal_001.hoot").write_bytes(b"\x0c\x0d\x0e\x0f" * 2048)  # 8192 bytes

    return log_dir


@pytest.fixture
def mock_sftp_client(temp_log_dir):
    """Create a mock SFTP client backed by real files on disk."""
    mock_sftp = MagicMock()

    def listdir_attr(path):
        path = str(path)
        if path.endswith("/"):
            path = path[:-1]
        full_path = str(temp_log_dir / path.lstrip("/")) if not path.startswith("/") else path

        if not os.path.isdir(full_path):
            raise FileNotFoundError(f"No such directory: {full_path}")

        entries = []
        for name in sorted(os.listdir(full_path)):
            full = os.path.join(full_path, name)
            st = os.stat(full)
            attr = make_sftp_attr(
                name,
                st.st_mode,
                st.st_size,
                int(st.st_mtime),
            )
            entries.append(attr)
        return entries

    def stat(path):
        path = str(path)
        full_path = (
            str(temp_log_dir / path.lstrip("/"))
            if not path.startswith("/") and not path.startswith(str(temp_log_dir))
            else path
        )
        # Handle paths relative to temp_log_dir
        if not os.path.exists(full_path):
            alt = str(temp_log_dir / path.lstrip("/"))
            if os.path.exists(alt):
                full_path = alt
            else:
                raise FileNotFoundError(f"No such file: {full_path}")

        st = os.stat(full_path)
        attr = MagicMock()
        attr.st_size = st.st_size
        attr.st_mtime = st.st_mtime
        attr.st_mode = st.st_mode
        return attr

    def open_sftp_file(path, mode):
        path = str(path)
        full_path = (
            str(temp_log_dir / path.lstrip("/"))
            if not path.startswith("/") and not path.startswith(str(temp_log_dir))
            else path
        )
        if not os.path.exists(full_path):
            alt = str(temp_log_dir / path.lstrip("/"))
            if os.path.exists(alt):
                full_path = alt
            else:
                raise FileNotFoundError(f"No such file: {full_path}")

        # Return a real file object wrapped in a mock context manager
        real_file = open(full_path, "rb")
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)

        # Read in chunks to simulate SFTP behavior
        chunks = []
        while True:
            data = real_file.read(32 * 1024)
            if not data:
                break
            chunks.append(data)
        real_file.close()
        chunks.append(b"")
        mock_file.read = MagicMock(side_effect=chunks)

        return mock_file

    mock_sftp.listdir_attr.side_effect = listdir_attr
    mock_sftp.stat.side_effect = stat
    mock_sftp.open.side_effect = open_sftp_file
    mock_sftp.sock = MagicMock()

    return mock_sftp


@pytest.fixture
def mock_ssh_client(mock_sftp_client):
    """Create a mock SSH client that returns the mock SFTP client."""
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp_client
    return mock_ssh


class TestIntegrationPipeline:
    """Test the full download pipeline with mocked SFTP and real files."""

    def test_list_files_integration(self, mock_ssh_client, temp_log_dir):
        """Test listing files via mocked SFTP with real directory contents."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            with FTPConnection(config) as conn:
                files = conn.list_files(str(temp_log_dir))

        names = {name for name, _ in files}
        assert "match_001.wpilog" in names
        assert "match_002.wpilog" in names
        assert "match_003.wpilog" in names
        assert "ctre_logs" in names

        # Check type info
        type_map = {name: facts.get("type") for name, facts in files}
        assert type_map["ctre_logs"] == "dir"
        assert type_map["match_001.wpilog"] == "file"

    def test_get_file_size_integration(self, mock_ssh_client, temp_log_dir):
        """Test getting file size via mocked SFTP with real file stats."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            with FTPConnection(config) as conn:
                size = conn.get_file_size(f"{temp_log_dir}/match_001.wpilog")

        assert size == 1024

    def test_download_file_integration(self, mock_ssh_client, temp_log_dir, tmp_path):
        """Test downloading a file with integrity verification (real bytes)."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        local_path = tmp_path / "downloaded.wpilog"
        remote_path = f"{temp_log_dir}/match_002.wpilog"
        expected_content = (temp_log_dir / "match_002.wpilog").read_bytes()

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            with FTPConnection(config) as conn:
                conn.download_file(remote_path, local_path)

        assert local_path.exists()
        assert local_path.read_bytes() == expected_content

    def test_download_integrity_mismatch_integration(
        self, mock_sftp_client, temp_log_dir, tmp_path
    ):
        """Test that download integrity check catches byte count mismatches."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        # Make stat return wrong size
        def wrong_stat(path):
            attr = MagicMock()
            attr.st_size = 999999  # Wrong size
            return attr

        mock_sftp_client.stat.side_effect = wrong_stat

        local_path = tmp_path / "downloaded.wpilog"
        remote_path = f"{temp_log_dir}/match_001.wpilog"

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value.open_sftp.return_value = mock_sftp_client

            with FTPConnection(config) as conn:
                with pytest.raises(FTPDataConnectionError) as exc_info:
                    conn.download_file(remote_path, local_path)

        assert exc_info.value.operation == "download"
        assert "integrity" in str(exc_info.value).lower()
        assert not local_path.exists()  # Partial download cleaned up

    def test_download_nonexistent_file_integration(self, mock_ssh_client, temp_log_dir, tmp_path):
        """Test downloading a nonexistent file cleans up partial downloads."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        local_path = tmp_path / "downloaded.wpilog"
        local_path.write_text("partial data")  # Pre-existing partial file

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            with FTPConnection(config) as conn:
                with pytest.raises(FTPProtocolError):
                    conn.download_file(f"{temp_log_dir}/nonexistent.wpilog", local_path)

        assert not local_path.exists()  # Partial file cleaned up

    def test_log_scanner_discover_integration(self, mock_ssh_client, temp_log_dir):
        """Test LogScanner.discover() with real directory structure."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            scanner = LogScanner(config)
            discovery = scanner.discover()

        assert len(discovery.logs) >= 4  # 3 files + 1 subdir
        log_names = [log.name for log in discovery.logs]
        assert "match_001.wpilog" in log_names
        assert "match_002.wpilog" in log_names
        assert "match_003.wpilog" in log_names
        assert "ctre_logs" in log_names

    def test_log_scanner_recursive_integration(self, mock_ssh_client, temp_log_dir):
        """Test recursive scanning into subdirectories."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            scanner = LogScanner(config)
            discovery = scanner.discover()

        # Should find files in subdirectory too
        ctre_files = [
            log
            for log in discovery.logs
            if "ctre_logs" in log.remote_path or log.name == "signal_001.hoot"
        ]
        assert len(ctre_files) > 0

    def test_check_file_stable_integration(self, mock_ssh_client, temp_log_dir):
        """Test file stability check with real file sizes."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            scanner = LogScanner(config)
            # File should be stable (same size before and after)
            assert scanner.check_file_stable(f"{temp_log_dir}/match_001.wpilog", 0.01)

    def test_log_downloader_download_integration(
        self, mock_ssh_client, mock_sftp_client, temp_log_dir, tmp_path
    ):
        """Test LogDownloader.download() end-to-end."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        log_file = LogFile(
            remote_path=str(temp_log_dir),
            name="match_001.wpilog",
            size=1024,
            modified_time=datetime.now(UTC),
            is_directory=False,
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value.open_sftp.return_value = mock_sftp_client

            downloader = LogDownloader(config, staging_dir=tmp_path)
            result = downloader.download(log_file)

        assert result.success
        assert result.local_path.exists()
        assert result.local_path.read_bytes() == (temp_log_dir / "match_001.wpilog").read_bytes()

    def test_log_downloader_download_directory_integration(
        self, mock_ssh_client, mock_sftp_client, temp_log_dir, tmp_path
    ):
        """Test downloading a directory tree."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        log_file = LogFile(
            remote_path=str(temp_log_dir),
            name="ctre_logs",
            size=0,
            modified_time=datetime.now(UTC),
            is_directory=True,
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value.open_sftp.return_value = mock_sftp_client

            downloader = LogDownloader(config, staging_dir=tmp_path)
            result = downloader.download(log_file)

        assert result.success
        assert result.local_path.exists()
        assert (result.local_path / "signal_001.hoot").exists()

    def test_log_downloader_download_multiple_integration(
        self, mock_ssh_client, mock_sftp_client, temp_log_dir, tmp_path
    ):
        """Test downloading multiple files in sequence."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory=str(temp_log_dir),
        )

        log_files = [
            LogFile(
                remote_path=str(temp_log_dir),
                name="match_001.wpilog",
                size=1024,
                modified_time=datetime.now(UTC),
                is_directory=False,
            ),
            LogFile(
                remote_path=str(temp_log_dir),
                name="match_002.wpilog",
                size=2048,
                modified_time=datetime.now(UTC),
                is_directory=False,
            ),
        ]

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value.open_sftp.return_value = mock_sftp_client

            downloader = LogDownloader(config, staging_dir=tmp_path)
            results = downloader.download_multiple(log_files)

        assert len(results) == 2
        assert all(r.success for r in results)
        assert (tmp_path / "match_001.wpilog").exists()
        assert (tmp_path / "match_002.wpilog").exists()

    def test_cleanup_staging_integration(self, tmp_path):
        """Test staging directory cleanup."""
        config = RobotConfig(address="10.100.79.2", port=22)

        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()
        (staging_dir / "old_file.wpilog").write_text("old")

        downloader = LogDownloader(config, staging_dir=staging_dir)
        downloader.cleanup_staging()

        assert staging_dir.exists()
        assert not (staging_dir / "old_file.wpilog").exists()


class TestSFTPErrorHandling:
    """Test SFTP error handling scenarios."""

    def test_connection_refused_error(self):
        """Test connection refused gives FTPConnectionError."""
        config = RobotConfig(
            address="127.0.0.1",
            port=1,  # Unlikely to be open
            username="lvuser",
            password="",
            connection_timeout=0.5,
            transfer_timeout=5.0,
        )

        with pytest.raises(FTPConnectionError) as exc_info:
            with FTPConnection(config):
                pass

        assert exc_info.value.operation == "connect"

    def test_authentication_error(self, mock_ssh_client):
        """Test authentication failure gives FTPAuthError."""
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = paramiko.AuthenticationException("530 Login incorrect")
        mock_ssh_class = MagicMock(return_value=mock_ssh)

        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="wrong",
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient", mock_ssh_class):
            with pytest.raises(FTPAuthError) as exc_info:
                with FTPConnection(config):
                    pass

        assert exc_info.value.operation == "login"

    def test_ssh_exception_banner_on_port_21(self):
        """Test clear error when connecting to FTP port 21 with SFTP."""
        config = RobotConfig(
            address="10.100.79.2",
            port=21,
            username="lvuser",
            password="",
            connection_timeout=5.0,
            transfer_timeout=5.0,
        )

        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = paramiko.SSHException("Error reading SSH protocol banner")
        mock_ssh_class = MagicMock(return_value=mock_ssh)

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient", mock_ssh_class):
            with pytest.raises(FTPConnectionError) as exc_info:
                with FTPConnection(config):
                    pass

        assert "FTP" in str(exc_info.value) and "SFTP" in str(exc_info.value)
        assert "port 22" in str(exc_info.value)

    def test_ssh_exception_banner_on_port_22(self):
        """Test clear error when SSH is not enabled on port 22."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            connection_timeout=5.0,
            transfer_timeout=5.0,
        )

        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = paramiko.SSHException("Error reading SSH protocol banner")
        mock_ssh_class = MagicMock(return_value=mock_ssh)

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient", mock_ssh_class):
            with pytest.raises(FTPConnectionError) as exc_info:
                with FTPConnection(config):
                    pass

        assert "SSH/SFTP" in str(exc_info.value) or "SSH" in str(exc_info.value)

    def test_path_sanitization_blocks_traversal(self, mock_ssh_client):
        """Test that path traversal attacks are blocked."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            with FTPConnection(config) as conn:
                with pytest.raises(FTPProtocolError):
                    conn._sanitize_remote_path("/home/lvuser/logs/../../etc/passwd")

    def test_path_sanitization_blocks_newlines(self, mock_ssh_client):
        """Test that newlines in paths are blocked."""
        config = RobotConfig(
            address="10.100.79.2",
            port=22,
            username="lvuser",
            password="",
            log_directory="/home/lvuser/logs",
        )

        with patch("frc_log_uploader.discovery.scanner.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh_class.return_value = mock_ssh_client

            with FTPConnection(config) as conn:
                with pytest.raises(FTPProtocolError):
                    conn._sanitize_remote_path("/home/lvuser/logs\nQUIT")
