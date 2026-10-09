"""Robot connection utilities."""

import logging
import socket

logger = logging.getLogger(__name__)


def resolve_robot_address(address: str) -> str:
    """Resolve robot address, handling mDNS and USB fallback."""
    # If it's already an IP address, return as-is
    try:
        socket.inet_aton(address)
        return address
    except OSError:
        pass

    # Try to resolve mDNS name
    try:
        ip = socket.gethostbyname(address)
        logger.info(f"Resolved {address} to {ip}")
        return ip
    except socket.gaierror:
        logger.warning(f"Could not resolve {address}")
        return address


def test_connection(address: str, port: int = 21, timeout: float = 5.0) -> bool:
    """Test if we can connect to the robot."""
    try:
        sock = socket.create_connection((address, port), timeout=timeout)
        sock.close()
        return True
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        logger.debug(f"Connection test failed: {e}")
        return False


def get_usb_address() -> str:
    """Get the USB roboRIO address (172.22.11.2)."""
    return "172.22.11.2"
