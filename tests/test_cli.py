"""Tests for the CLI module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from frc_log_uploader.cli import app


class TestCLI:
    """Test CLI commands."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_scan_command(self):
        """Test the scan command."""
        with patch("frc_log_uploader.cli.LogUploaderService") as mock_service_class:
            mock_service = MagicMock()
            mock_service_class.return_value = mock_service

            mock_discovery = MagicMock()
            mock_discovery.robot_address = "10.100.79.2 (via team_ip)"
            mock_discovery.log_directory = "/home/lvuser/logs"
            mock_discovery.logs = []
            mock_discovery.sorted_by_time.return_value = []
            mock_service.scan.return_value = mock_discovery

            result = self.runner.invoke(app, ["scan"])

        assert result.exit_code == 0
        assert "Scanning" in result.output

    def test_scan_command_with_config(self):
        """Test the scan command with a config file."""
        with patch("frc_log_uploader.cli.LogUploaderService") as mock_service_class:
            mock_service = MagicMock()
            mock_service_class.return_value = mock_service

            mock_discovery = MagicMock()
            mock_discovery.robot_address = "10.100.79.2"
            mock_discovery.log_directory = "/home/lvuser/logs"
            mock_discovery.logs = []
            mock_discovery.sorted_by_time.return_value = []
            mock_service.scan.return_value = mock_discovery

            result = self.runner.invoke(app, ["scan", "--config", "/tmp/test_config.toml"])

        assert result.exit_code == 0

    def test_run_command_no_logs(self):
        """Test the run command when no logs are available."""
        with patch("frc_log_uploader.cli.LogUploaderService") as mock_service_class:
            mock_service = MagicMock()
            mock_service_class.return_value = mock_service
            mock_service.run_once.return_value = False

            result = self.runner.invoke(app, ["run"])

        assert result.exit_code == 1
        assert "No logs uploaded" in result.output

    def test_run_command_success(self):
        """Test the run command when upload succeeds."""
        with patch("frc_log_uploader.cli.LogUploaderService") as mock_service_class:
            mock_service = MagicMock()
            mock_service_class.return_value = mock_service
            mock_service.run_once.return_value = True

            result = self.runner.invoke(app, ["run"])

        assert result.exit_code == 0
        assert "Upload cycle completed successfully" in result.output

    def test_init_config_command(self):
        """Test the init-config command."""
        with patch("frc_log_uploader.cli.Config.default_path") as mock_default_path:
            mock_path = Path("/tmp/test_config.toml")
            mock_default_path.return_value = mock_path

            with patch("frc_log_uploader.cli.Config.save") as mock_save:
                result = self.runner.invoke(app, ["init-config"])

        assert result.exit_code == 0
        mock_save.assert_called_once_with(mock_path)

    def test_help_command(self):
        """Test the help command."""
        result = self.runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan" in result.output
        assert "run" in result.output
        assert "watch" in result.output
        assert "init-config" in result.output
