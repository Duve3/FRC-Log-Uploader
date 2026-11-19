"""Tests for the connection module."""

import socket
from unittest.mock import MagicMock, patch

from frc_log_uploader.robot.connection import (
    FTP_PORT,
    SSH_SFTP_PORT,
    get_usb_address,
    probe_ssh_port,
    resolve_robot_address,
)
from frc_log_uploader.robot.connection import (
    test_connection as tcp_test_connection,
)


class TestConnectionUtilities:
    """Test robot connection utility functions."""

    def test_resolve_robot_address_ip(self):
        """Test that IP addresses are returned as-is."""
        assert resolve_robot_address("10.100.79.2") == "10.100.79.2"

    def test_resolve_robot_address_hostname(self):
        """Test that hostnames are resolved to IPs."""
        with patch("frc_log_uploader.robot.connection.socket.gethostbyname") as mock_resolve:
            mock_resolve.return_value = "10.100.79.2"
            result = resolve_robot_address("roborio-10079-frc.local")
            assert result == "10.100.79.2"

    def test_resolve_robot_address_unresolvable(self):
        """Test that unresolvable hostnames are returned as-is."""
        with patch("frc_log_uploader.robot.connection.socket.gethostbyname") as mock_resolve:
            mock_resolve.side_effect = socket.gaierror("Name does not resolve")
            result = resolve_robot_address("nonexistent.local")
            assert result == "nonexistent.local"

    def test_test_connection_success(self):
        """Test successful TCP connection test."""
        with patch("frc_log_uploader.robot.connection.socket.create_connection") as mock_connect:
            mock_sock = MagicMock()
            mock_connect.return_value = mock_sock

            assert tcp_test_connection("10.100.79.2", 22, 5.0) is True
            mock_connect.assert_called_once_with(("10.100.79.2", 22), timeout=5.0)
            mock_sock.close.assert_called_once()

    def test_test_connection_refused(self):
        """Test connection refused returns False."""
        with patch("frc_log_uploader.robot.connection.socket.create_connection") as mock_connect:
            mock_connect.side_effect = ConnectionRefusedError()
            assert tcp_test_connection("10.100.79.2", 22, 5.0) is False

    def test_test_connection_timeout(self):
        """Test connection timeout returns False."""
        with patch("frc_log_uploader.robot.connection.socket.create_connection") as mock_connect:
            mock_connect.side_effect = TimeoutError()
            assert tcp_test_connection("10.100.79.2", 22, 5.0) is False

    def test_test_connection_defaults(self):
        """Test connection test with default timeout."""
        with patch("frc_log_uploader.robot.connection.socket.create_connection") as mock_connect:
            mock_sock = MagicMock()
            mock_connect.return_value = mock_sock
            tcp_test_connection("10.100.79.2")
            mock_connect.assert_called_once_with(("10.100.79.2", 22), timeout=5.0)

    def test_get_usb_address(self):
        """Test USB address constant."""
        assert get_usb_address() == "172.22.11.2"

    def test_constants(self):
        """Test port constants."""
        assert FTP_PORT == 21
        assert SSH_SFTP_PORT == 22

    def test_probe_ssh_port_ssh_open(self):
        """Test probe finds SSH port when only port 22 is open."""
        with patch("frc_log_uploader.robot.connection.test_connection") as mock_test:
            mock_test.side_effect = lambda addr, port, timeout: port == 22
            result = probe_ssh_port("10.100.79.2")
            assert result == 22

    def test_probe_ssh_port_ftp_only(self):
        """Test probe finds FTP port when only port 21 is open."""
        with patch("frc_log_uploader.robot.connection.test_connection") as mock_test:
            mock_test.side_effect = lambda addr, port, timeout: port == 21
            result = probe_ssh_port("10.100.79.2")
            assert result == 21

    def test_probe_ssh_port_none_open(self):
        """Test probe returns None when no ports are open."""
        with patch("frc_log_uploader.robot.connection.test_connection") as mock_test:
            mock_test.return_value = False
            result = probe_ssh_port("10.100.79.2")
            assert result is None
