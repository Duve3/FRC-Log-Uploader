"""Robot connection utilities."""

import logging
import socket

logger = logging.getLogger(__name__)

# Standard roboRIO ports
FTP_PORT = 21
SSH_SFTP_PORT = 22


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


def test_connection(address: str, port: int = SSH_SFTP_PORT, timeout: float = 5.0) -> bool:
    """Test if we can connect to the robot on the given port."""
    try:
        sock = socket.create_connection((address, port), timeout=timeout)
        sock.close()
        return True
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        logger.debug(f"Connection test failed for {address}:{port}: {e}")
        return False


def probe_ssh_port(address: str, timeout: float = 3.0) -> int | None:
    """Probe common SSH/SFTP ports and return the first that accepts connections.

    Tries port 22 (SSH/SFTP) first, then the FTP port 21 as a fallback
    (to help diagnose misconfigured setups).
    """
    for port in (SSH_SFTP_PORT, FTP_PORT):
        if test_connection(address, port, timeout=timeout):
            logger.info(f"Robot port {port} is open on {address}")
            return port
    return None


def get_usb_address() -> str:
    """Get the USB roboRIO address (172.22.11.2)."""
    return "172.22.11.2"
