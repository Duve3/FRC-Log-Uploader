"""Tests for the service layer with expanded coverage."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from frc_log_uploader.config import Config, LogsConfig, RobotConfig
from frc_log_uploader.discovery.models import DiscoveryResult, LogFile
from frc_log_uploader.service import LogUploaderService


def create_log(
    name: str, minutes_ago: int, size: int = 100 * 1024 * 1024, is_dir: bool = False
) -> LogFile:
    """Create a test log file."""
    return LogFile(
        remote_path="/home/lvuser/logs",
        name=name,
        size=size,
        modified_time=datetime.now() - timedelta(minutes=minutes_ago),
        is_directory=is_dir,
    )


class TestServiceScan:
    """Test service scan functionality."""

    def test_scan(self):
        """Test scan method calls locator and scanner."""
        config = Config(robot=RobotConfig(address="10.100.79.2"))
        service = LogUploaderService(config)

        mock_address = MagicMock()
        mock_address.ip = "10.100.79.2"
        mock_address.port = 22

        mock_discovery = MagicMock()

        with patch.object(service.locator, "find", return_value=mock_address):
            with patch.object(service.scanner, "discover", return_value=mock_discovery):
                result = service.scan()

        assert result == mock_discovery
        assert config.robot.address == "10.100.79.2"
        assert config.robot.port == 22


class TestServiceSelectLogs:
    """Test service log selection functionality."""

    def test_select_logs_stable_only(self):
        """Test that only stable logs are selected."""
        config = Config(
            robot=RobotConfig(address="test"),
            logs=LogsConfig(count=3, stability_seconds=2.0),
        )
        service = LogUploaderService(config)

        logs = [
            create_log("stable1", 10),
            create_log("stable2", 20),
            create_log("stable3", 30),
        ]
        discovery = DiscoveryResult(
            logs=logs,
            robot_address="test",
            log_directory="/home/lvuser/logs",
            discovery_time=datetime.now(),
        )

        service._is_stable = lambda log: True

        selected = service.select_logs(discovery)
        assert len(selected) == 3

    def test_select_logs_excludes_unstable(self):
        """Test that unstable logs are excluded."""
        config = Config(
            robot=RobotConfig(address="test"),
            logs=LogsConfig(count=3, stability_seconds=2.0),
        )
        service = LogUploaderService(config)

        logs = [
            create_log("stable1", 10),
            create_log("stable2", 20),
            create_log("unstable_newest", 1),
        ]
        discovery = DiscoveryResult(
            logs=logs,
            robot_address="test",
            log_directory="/home/lvuser/logs",
            discovery_time=datetime.now(),
        )

        service._is_stable = lambda log: log.name != "unstable_newest"

        selected = service.select_logs(discovery)
        assert len(selected) == 2
        assert all(log.name != "unstable_newest" for log in selected)

    def test_select_logs_empty(self):
        """Test selection with no logs."""
        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        discovery = DiscoveryResult(
            logs=[],
            robot_address="test",
            log_directory="/home/lvuser/logs",
            discovery_time=datetime.now(),
        )

        selected = service.select_logs(discovery)
        assert selected == []

    def test_select_logs_all_unstable(self):
        """Test selection when all logs are unstable."""
        config = Config(
            robot=RobotConfig(address="test"),
            logs=LogsConfig(count=3, stability_seconds=2.0),
        )
        service = LogUploaderService(config)

        logs = [create_log("unstable1", 1), create_log("unstable2", 2)]
        discovery = DiscoveryResult(
            logs=logs,
            robot_address="test",
            log_directory="/home/lvuser/logs",
            discovery_time=datetime.now(),
        )

        service._is_stable = lambda log: False

        selected = service.select_logs(discovery)
        assert selected == []


class TestServiceIsStable:
    """Test service stability check."""

    def test_is_stable_file(self):
        """Test stability check for a regular file."""
        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        log_file = LogFile(
            remote_path="/home/lvuser/logs",
            name="test.wpilog",
            size=1024,
            modified_time=datetime.now(),
            is_directory=False,
        )

        with patch.object(service.scanner, "check_file_stable", return_value=True) as mock_stable:
            result = service._is_stable(log_file)
            assert result is True
            mock_stable.assert_called_once()

    def test_is_stable_directory(self):
        """Test stability check for a directory (always stable)."""
        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        log_file = LogFile(
            remote_path="/home/lvuser/logs",
            name="ctre_logs",
            size=0,
            modified_time=datetime.now(),
            is_directory=True,
        )

        result = service._is_stable(log_file)
        assert result is True


class TestServiceRunOnce:
    """Test service run_once method."""

    def test_run_once_success(self):
        """Test a complete successful upload cycle."""
        config = Config(robot=RobotConfig(address="10.100.79.2"))
        service = LogUploaderService(config)

        mock_discovery = MagicMock()
        mock_log_file = MagicMock()
        mock_log_file.name = "test.wpilog"

        mock_download_result = MagicMock()
        mock_download_result.success = True
        mock_download_result.log_file = mock_log_file
        mock_download_result.local_path = Path("/tmp/test.wpilog")

        with patch.object(service, "scan", return_value=mock_discovery):
            with patch.object(service, "select_logs", return_value=[mock_log_file]):
                with patch.object(
                    service.downloader, "download_multiple", return_value=[mock_download_result]
                ):
                    with patch.object(service, "_get_cloud_provider") as mock_provider:
                        mock_provider.return_value.upload.return_value = "file_id"
                        with patch.object(service, "_cleanup_local_file"):
                            result = service.run_once()

        assert result is True

    def test_run_once_no_logs(self):
        """Test run_once when no logs are available."""
        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        with patch.object(service, "scan") as mock_scan:
            mock_discovery = MagicMock()
            mock_discovery.sorted_by_time.return_value = []
            mock_scan.return_value = mock_discovery
            with patch.object(service, "select_logs", return_value=[]):
                result = service.run_once()

        assert result is False

    def test_run_once_robot_not_found(self):
        """Test run_once when robot is not found."""
        from frc_log_uploader.robot import RobotNotFoundError

        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        with patch.object(service, "scan", side_effect=RobotNotFoundError("Not found")):
            result = service.run_once()

        assert result is False

    def test_run_once_download_failure(self):
        """Test run_once when download fails."""
        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        mock_log_file = MagicMock()
        mock_log_file.name = "test.wpilog"

        mock_download_result = MagicMock()
        mock_download_result.success = False
        mock_download_result.log_file = mock_log_file
        mock_download_result.error = "Connection failed"

        with patch.object(service, "scan") as mock_scan:
            mock_discovery = MagicMock()
            mock_scan.return_value = mock_discovery
            with patch.object(service, "select_logs", return_value=[mock_log_file]):
                with patch.object(
                    service.downloader, "download_multiple", return_value=[mock_download_result]
                ):
                    result = service.run_once()

        assert result is False

    def test_run_once_upload_failure(self):
        """Test run_once when upload fails but download succeeds."""
        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        mock_log_file = MagicMock()
        mock_log_file.name = "test.wpilog"

        mock_download_result = MagicMock()
        mock_download_result.success = True
        mock_download_result.log_file = mock_log_file
        mock_download_result.local_path = Path("/tmp/test.wpilog")

        with patch.object(service, "scan") as mock_scan:
            mock_discovery = MagicMock()
            mock_scan.return_value = mock_discovery
            with patch.object(service, "select_logs", return_value=[mock_log_file]):
                with patch.object(
                    service.downloader, "download_multiple", return_value=[mock_download_result]
                ):
                    with patch.object(service, "_get_cloud_provider") as mock_provider:
                        mock_provider.return_value.upload.side_effect = Exception("Upload failed")
                        result = service.run_once()

        # Upload failure means no logs were successfully uploaded
        assert result is False


class TestServiceCleanup:
    """Test service cleanup functionality."""

    def test_cleanup_local_file(self):
        """Test cleanup of a local file."""
        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            path = Path(f.name)

        try:
            assert path.exists()
            service._cleanup_local_file(path)
            assert not path.exists()
        finally:
            if path.exists():
                path.unlink()

    def test_cleanup_local_directory(self):
        """Test cleanup of a local directory."""
        config = Config(robot=RobotConfig(address="test"))
        service = LogUploaderService(config)

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subdir"
            path.mkdir()
            (path / "file.txt").write_text("test")

            assert path.exists()
            service._cleanup_local_file(path)
            assert not path.exists()
