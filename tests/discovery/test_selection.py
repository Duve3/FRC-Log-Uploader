"""Tests for log selection logic."""

from datetime import datetime, timedelta

from frc_log_uploader.discovery.models import DiscoveryResult, LogFile


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


def test_sorting_newest_first():
    """Test that logs are sorted newest first."""
    logs = [
        create_log("log1", 30),
        create_log("log2", 20),
        create_log("log3", 10),
    ]
    discovery = DiscoveryResult(
        logs=logs,
        robot_address="test",
        log_directory="/home/lvuser/logs",
        discovery_time=datetime.now(),
    )

    sorted_logs = discovery.sorted_by_time(newest_first=True)
    assert sorted_logs[0].name == "log3"
    assert sorted_logs[1].name == "log2"
    assert sorted_logs[2].name == "log1"


def test_sorting_oldest_first():
    """Test that logs are sorted oldest first."""
    logs = [
        create_log("log1", 30),
        create_log("log2", 20),
        create_log("log3", 10),
    ]
    discovery = DiscoveryResult(
        logs=logs,
        robot_address="test",
        log_directory="/home/lvuser/logs",
        discovery_time=datetime.now(),
    )

    sorted_logs = discovery.sorted_by_time(newest_first=False)
    assert sorted_logs[0].name == "log1"
    assert sorted_logs[1].name == "log2"
    assert sorted_logs[2].name == "log3"


def test_log_files_property():
    """Test that log_files property returns only files."""
    logs = [
        create_log("file1", 10),
        create_log("dir1", 20, is_dir=True),
        create_log("file2", 30),
    ]
    discovery = DiscoveryResult(
        logs=logs,
        robot_address="test",
        log_directory="/home/lvuser/logs",
        discovery_time=datetime.now(),
    )

    assert len(discovery.log_files) == 2
    assert all(not log.is_directory for log in discovery.log_files)


def test_log_directories_property():
    """Test that log_directories property returns only directories."""
    logs = [
        create_log("file1", 10),
        create_log("dir1", 20, is_dir=True),
        create_log("dir2", 30, is_dir=True),
    ]
    discovery = DiscoveryResult(
        logs=logs,
        robot_address="test",
        log_directory="/home/lvuser/logs",
        discovery_time=datetime.now(),
    )

    assert len(discovery.log_directories) == 2
    assert all(log.is_directory for log in discovery.log_directories)
