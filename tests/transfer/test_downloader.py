"""Tests for cloud upload and file cleanup."""

from pathlib import Path
from unittest.mock import Mock, patch

from frc_log_uploader.transfer.downloader import DownloadResult
from frc_log_uploader.discovery.models import LogFile
from datetime import datetime


def test_download_result_repr():
    """Test DownloadResult representation."""
    log_file = LogFile(
        remote_path="/home/lvuser/logs",
        name="test.wpilog",
        size=100 * 1024 * 1024,
        modified_time=datetime.now(),
    )

    # Successful result
    result = DownloadResult(log_file, Path("/tmp/test.wpilog"), success=True)
    assert "OK" in repr(result)

    # Failed result
    result = DownloadResult(
        log_file, Path("/tmp/test.wpilog"), success=False, error="Connection failed"
    )
    assert "FAILED" in repr(result)
    assert "Connection failed" in repr(result)
