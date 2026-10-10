"""Tests for service layer logic."""

from datetime import datetime, timedelta

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


def test_select_logs_excludes_unstable():
    """Test that unstable (newest) logs are excluded."""
    config = Config(
        robot=RobotConfig(address="test"),
        logs=LogsConfig(count=3, stability_seconds=2.0),
    )
    service = LogUploaderService(config)

    # Create logs where the newest is unstable
    logs = [
        create_log("stable1", 10),
        create_log("stable2", 20),
        create_log("stable3", 30),
        create_log("unstable_newest", 1),  # This will be checked for stability
    ]
    discovery = DiscoveryResult(
        logs=logs,
        robot_address="test",
        log_directory="/home/lvuser/logs",
        discovery_time=datetime.now(),
    )

    # Mock the stability check - unstable_newest is unstable, others stable
    def mock_is_stable(log):
        return log.name != "unstable_newest"

    service._is_stable = mock_is_stable

    selected = service.select_logs(discovery)

    assert len(selected) == 3
    assert selected[0].name == "stable1"
    assert selected[1].name == "stable2"
    assert selected[2].name == "stable3"


def test_select_logs_fewer_than_count():
    """Test that all stable logs are returned when fewer than count."""
    config = Config(
        robot=RobotConfig(address="test"),
        logs=LogsConfig(count=5, stability_seconds=2.0),
    )
    service = LogUploaderService(config)

    logs = [
        create_log("stable1", 10),
        create_log("stable2", 20),
    ]
    discovery = DiscoveryResult(
        logs=logs,
        robot_address="test",
        log_directory="/home/lvuser/logs",
        discovery_time=datetime.now(),
    )

    service._is_stable = lambda log: True

    selected = service.select_logs(discovery)

    assert len(selected) == 2
    assert selected[0].name == "stable1"
    assert selected[1].name == "stable2"


def test_select_logs_no_stable():
    """Test that empty list is returned when no stable logs."""
    config = Config(
        robot=RobotConfig(address="test"),
        logs=LogsConfig(count=3, stability_seconds=2.0),
    )
    service = LogUploaderService(config)

    logs = [
        create_log("unstable1", 1),
        create_log("unstable2", 2),
    ]
    discovery = DiscoveryResult(
        logs=logs,
        robot_address="test",
        log_directory="/home/lvuser/logs",
        discovery_time=datetime.now(),
    )

    service._is_stable = lambda log: False

    selected = service.select_logs(discovery)

    assert len(selected) == 0


def test_select_logs_respects_count():
    """Test that only 'count' logs are selected."""
    config = Config(
        robot=RobotConfig(address="test"),
        logs=LogsConfig(count=2, stability_seconds=2.0),
    )
    service = LogUploaderService(config)

    logs = [
        create_log("stable1", 10),
        create_log("stable2", 20),
        create_log("stable3", 30),
        create_log("stable4", 40),
    ]
    discovery = DiscoveryResult(
        logs=logs,
        robot_address="test",
        log_directory="/home/lvuser/logs",
        discovery_time=datetime.now(),
    )

    service._is_stable = lambda log: True

    selected = service.select_logs(discovery)

    assert len(selected) == 2
    assert selected[0].name == "stable1"
    assert selected[1].name == "stable2"


def test_log_file_full_remote_path():
    """Test LogFile.full_remote_path property."""
    log = LogFile(
        remote_path="/home/lvuser/logs",
        name="test.wpilog",
        size=100,
        modified_time=datetime.now(),
    )

    assert log.full_remote_path == "/home/lvuser/logs/test.wpilog"

    # Test with trailing slash
    log2 = LogFile(
        remote_path="/home/lvuser/logs/",
        name="test.wpilog",
        size=100,
        modified_time=datetime.now(),
    )

    assert log2.full_remote_path == "/home/lvuser/logs/test.wpilog"
