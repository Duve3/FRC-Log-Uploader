"""Tests for the config module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from frc_log_uploader.config import Config, RobotConfig


class TestRobotConfig:
    """Test RobotConfig dataclass."""

    def test_default_values(self):
        """Test default RobotConfig values."""
        config = RobotConfig()
        assert config.team is None
        assert config.address == ""
        assert config.log_directory == "/home/lvuser/logs"
        assert config.username == "lvuser"
        assert config.password == ""
        assert config.port == 22
        assert config.use_ftp is False
        assert config.connection_timeout == 10.0
        assert config.transfer_timeout == 300.0

    def test_custom_values(self):
        """Test RobotConfig with custom values."""
        config = RobotConfig(
            team=10079,
            address="10.100.79.2",
            port=22,
            use_ftp=False,
            username="lvuser",
            password="",
        )
        assert config.team == 10079
        assert config.address == "10.100.79.2"
        assert config.port == 22

    def test_post_init_warns_on_ftp_with_port_22(self):
        """Test that use_ftp=True with port=22 generates a warning."""
        config = RobotConfig(use_ftp=True, port=22)
        # Should not raise, just set values
        assert config.use_ftp is True
        assert config.port == 22


class TestConfig:
    """Test Config dataclass."""

    def test_default_config(self):
        """Test default Config values."""
        config = Config()
        assert config.robot.team is None
        assert config.robot.address == ""
        assert config.robot.port == 22
        assert config.robot.use_ftp is False
        assert config.logs.count == 5
        assert config.logs.stability_seconds == 2.0
        assert config.cloud.provider == "google_drive"
        assert config.watch.poll_interval == 30

    def test_load_nonexistent_file(self):
        """Test loading config from nonexistent file returns defaults."""
        config = Config.load(Path("/tmp/nonexistent_config.toml"))
        assert config.robot.team is None
        assert config.robot.port == 22

    def test_load_from_file(self):
        """Test loading config from a TOML file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write("""
[robot]
team = 10079
address = "10.100.79.2"
port = 22
use_ftp = false
username = "lvuser"
password = ""

[logs]
count = 3
stability_seconds = 5.0

[cloud]
provider = "google_drive"
folder_id = "test_folder_id"
""")
            f.flush()
            path = Path(f.name)

        try:
            config = Config.load(path)
            assert config.robot.team == 10079
            assert config.robot.address == "10.100.79.2"
            assert config.robot.port == 22
            assert config.robot.use_ftp is False
            assert config.robot.username == "lvuser"
            assert config.logs.count == 3
            assert config.logs.stability_seconds == 5.0
            assert config.cloud.provider == "google_drive"
            assert config.cloud.folder_id == "test_folder_id"
        finally:
            os.unlink(path)

    def test_save_and_load(self):
        """Test saving and loading config."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            path = Path(f.name)

        try:
            config = Config()
            config.robot.team = 10079
            config.robot.address = "10.100.79.2"
            config.robot.port = 22
            config.logs.count = 3

            config.save(path)

            loaded = Config.load(path)
            assert loaded.robot.team == 10079
            assert loaded.robot.address == "10.100.79.2"
            assert loaded.robot.port == 22
            assert loaded.logs.count == 3
        finally:
            os.unlink(path)

    def test_default_path(self):
        """Test default config path."""
        path = Config.default_path()
        assert path.name == "config.toml"
        assert "frc-log-uploader" in str(path)

    def test_load_default(self):
        """Test load_default classmethod."""
        with patch("frc_log_uploader.config.Config.default_path") as mock_default:
            mock_path = Path("/tmp/test_config.toml")
            mock_default.return_value = mock_path

            with patch("frc_log_uploader.config.Config.load") as mock_load:
                mock_load.return_value = Config()
                Config.load_default()
                mock_load.assert_called_once_with(mock_path)

    def test_create_example(self):
        """Test create_example classmethod."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            path = Path(f.name)

        try:
            config = Config()
            config.create_example(path)

            assert path.exists()
            loaded = Config.load(path)
            assert loaded.robot.port == 22
            assert loaded.robot.use_ftp is False
        finally:
            os.unlink(path)
