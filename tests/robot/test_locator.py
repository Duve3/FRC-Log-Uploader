"""Tests for robot discovery/locator."""

from unittest.mock import Mock, patch

from frc_log_uploader.robot.locator import RobotAddress, RobotLocator, RobotNotFoundError


class MockConfig:
    """Mock config for testing."""

    def __init__(self, team: int | None = None, address: str = ""):
        self.team = team
        self.address = address
        self.port = 22


def test_team_to_ip_generation():
    """Test team number to IP conversion."""
    config = MockConfig(team=10079)
    locator = RobotLocator(config)

    assert locator._team_to_ip(10079) == "10.100.79.2"
    assert locator._team_to_ip(6328) == "10.63.28.2"
    assert locator._team_to_ip(254) == "10.2.54.2"
    assert locator._team_to_ip(1) == "10.0.01.2"
    assert locator._team_to_ip(99) == "10.0.99.2"
    assert locator._team_to_ip(1234) == "10.12.34.2"
    assert locator._team_to_ip(9999) == "10.99.99.2"


@patch("frc_log_uploader.robot.locator.socket.create_connection")
def test_find_uses_explicit_address_override(mock_connect):
    """Test that explicit address override is used first."""
    mock_socket = Mock()
    mock_connect.return_value = mock_socket

    config = MockConfig(team=10079, address="192.168.1.50")
    locator = RobotLocator(config)

    address = locator.find()

    assert address.ip == "192.168.1.50"
    assert address.source == "override"
    assert address.hostname == "192.168.1.50"
    mock_connect.assert_called_once_with(("192.168.1.50", 22), timeout=3.0)
    mock_socket.close.assert_called_once()


@patch("frc_log_uploader.robot.locator.socket.create_connection")
@patch("frc_log_uploader.robot.locator.socket.gethostbyname")
def test_find_uses_team_ip(mock_gethostbyname, mock_connect):
    """Test that team-derived IP is tried second."""
    mock_socket = Mock()
    mock_connect.return_value = mock_socket

    config = MockConfig(team=10079, address="")
    locator = RobotLocator(config)

    address = locator.find()

    assert address.ip == "10.100.79.2"
    assert address.source == "team_ip"
    assert address.team == 10079
    mock_connect.assert_called_once_with(("10.100.79.2", 22), timeout=3.0)
    mock_gethostbyname.assert_not_called()
    mock_socket.close.assert_called_once()


@patch("frc_log_uploader.robot.locator.socket.create_connection")
def test_find_falls_back_to_mdns(mock_connect):
    """Test that mDNS is tried when team IP fails."""
    # First call (team IP) fails, second call (mDNS) succeeds
    mock_socket = Mock()
    mock_connect.side_effect = [
        ConnectionRefusedError(),
        mock_socket,
    ]

    with patch("frc_log_uploader.robot.locator.socket.gethostbyname", return_value="10.100.79.2"):
        config = MockConfig(team=10079, address="")
        locator = RobotLocator(config)

        address = locator.find()

        assert address.ip == "10.100.79.2"
        assert address.source == "mdns"
        assert address.hostname == "roborio-10079-frc.local"
        assert mock_connect.call_count == 2
        mock_socket.close.assert_called_once()


@patch("frc_log_uploader.robot.locator.socket.create_connection")
def test_find_falls_back_to_usb(mock_connect):
    """Test that USB fallback is tried when team IP and mDNS fail."""
    mock_socket = Mock()
    mock_connect.side_effect = [
        ConnectionRefusedError(),  # team IP
        ConnectionRefusedError(),  # mDNS
        mock_socket,  # USB
    ]

    with patch("frc_log_uploader.robot.locator.socket.gethostbyname", return_value="10.100.79.2"):
        config = MockConfig(team=10079, address="")
        locator = RobotLocator(config)

        address = locator.find()

        assert address.ip == "172.22.11.2"
        assert address.source == "usb"
        assert mock_connect.call_count == 3
        mock_socket.close.assert_called_once()


@patch("frc_log_uploader.robot.locator.socket.create_connection")
def test_find_raises_when_all_methods_fail(mock_connect):
    """Test that RobotNotFoundError is raised when all methods fail."""
    mock_connect.side_effect = [
        ConnectionRefusedError(),  # team IP
        ConnectionRefusedError(),  # mDNS
        ConnectionRefusedError(),  # USB
    ]

    with patch("frc_log_uploader.robot.locator.socket.gethostbyname", return_value="10.100.79.2"):
        config = MockConfig(team=10079, address="")
        locator = RobotLocator(config)

        try:
            locator.find()
            assert False, "Expected RobotNotFoundError"
        except RobotNotFoundError as e:
            assert "Could not find roboRIO" in str(e)


@patch("frc_log_uploader.robot.locator.socket.create_connection")
def test_find_with_no_team_no_address_raises(mock_connect):
    """Test that error is raised when no team and no address configured."""
    mock_connect.side_effect = ConnectionRefusedError()

    config = MockConfig(team=None, address="")
    locator = RobotLocator(config)

    try:
        locator.find()
        assert False, "Expected RobotNotFoundError"
    except RobotNotFoundError as e:
        assert "Could not find roboRIO" in str(e)


@patch("frc_log_uploader.robot.locator.socket.create_connection")
def test_find_with_address_no_team(mock_connect):
    """Test that explicit address works even without team number."""
    mock_socket = Mock()
    mock_connect.return_value = mock_socket

    config = MockConfig(team=None, address="192.168.1.100")
    locator = RobotLocator(config)

    address = locator.find()

    assert address.ip == "192.168.1.100"
    assert address.source == "override"
    assert address.team is None
    mock_connect.assert_called_once_with(("192.168.1.100", 22), timeout=3.0)
    mock_socket.close.assert_called_once()


def test_robot_address_str_representation():
    """Test RobotAddress string representation."""
    addr = RobotAddress(ip="10.100.79.2", team=10079, source="team_ip")
    assert str(addr) == "10.100.79.2 (via team_ip)"

    addr = RobotAddress(
        ip="10.100.79.2", team=10079, source="mdns", hostname="roborio-10079-frc.local"
    )
    assert "roborio-10079-frc.local -> 10.100.79.2" in str(addr)
