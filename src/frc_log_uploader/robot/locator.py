"""Robot address discovery and location."""

import logging
import socket
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RobotAddress:
    """Resolved robot address with metadata."""

    ip: str
    team: int | None = None
    source: str = "unknown"  # "override", "team_ip", "mdns", "usb"
    hostname: str | None = None

    def __str__(self) -> str:
        source_info = f" (via {self.source})"
        if self.hostname and self.hostname != self.ip:
            return f"{self.hostname} -> {self.ip}{source_info}"
        return f"{self.ip}{source_info}"


class RobotLocator:
    """Locates the roboRIO using various discovery methods."""

    def __init__(self, config):
        self.config = config
        self._resolved_address: RobotAddress | None = None

    def find(self) -> RobotAddress:
        """Find the roboRIO using the configured discovery order."""
        # 1. Explicit address override
        if self.config.address:
            ip = self._resolve_address(self.config.address)
            if ip and self._test_connection(ip):
                self._resolved_address = RobotAddress(
                    ip=ip,
                    team=self.config.team,
                    source="override",
                    hostname=self.config.address,
                )
                logger.info(f"Using explicit address override: {self._resolved_address}")
                return self._resolved_address
            elif ip:
                logger.warning(
                    f"Explicit address {self.config.address} resolved to {ip} but connection failed"
                )

        # 2. Team number IP derivation
        if self.config.team:
            team_ip = self._team_to_ip(self.config.team)
            if self._test_connection(team_ip):
                self._resolved_address = RobotAddress(
                    ip=team_ip,
                    team=self.config.team,
                    source="team_ip",
                )
                logger.info(f"Found robot via team IP: {self._resolved_address}")
                return self._resolved_address

            # 3. mDNS fallback for team
            mdns_name = f"roborio-{self.config.team}-frc.local"
            mdns_ip = self._resolve_address(mdns_name)
            if mdns_ip and self._test_connection(mdns_ip):
                self._resolved_address = RobotAddress(
                    ip=mdns_ip,
                    team=self.config.team,
                    source="mdns",
                    hostname=mdns_name,
                )
                logger.info(f"Found robot via mDNS: {self._resolved_address}")
                return self._resolved_address

        # 4. USB fallback
        usb_ip = "172.22.11.2"
        if self._test_connection(usb_ip):
            self._resolved_address = RobotAddress(
                ip=usb_ip,
                team=self.config.team,
                source="usb",
            )
            logger.info(f"Found robot via USB: {self._resolved_address}")
            return self._resolved_address

        # 5. Last resort: try any configured address without team
        if self.config.address:
            ip = self._resolve_address(self.config.address)
            if ip and self._test_connection(ip):
                self._resolved_address = RobotAddress(
                    ip=ip,
                    team=self.config.team,
                    source="override",
                    hostname=self.config.address,
                )
                logger.info(f"Found robot via configured address: {self._resolved_address}")
                return self._resolved_address

        raise RobotNotFoundError(
            f"Could not find roboRIO (team={self.config.team}, address={self.config.address})"
        )

    def _team_to_ip(self, team: int) -> str:
        """Convert team number to standard FRC roboRIO IP address.

        Format: 10.<first digits>.<last two digits>.2
        Examples:
            254   -> 10.2.54.2
            6328  -> 10.63.28.2
            10079 -> 10.100.79.2
        """
        team_str = str(team)
        if len(team_str) <= 2:
            # 1-2 digit teams: 10.0.<team>.2
            first = "0"
            last = team_str.zfill(2)
        else:
            # 3+ digit teams: 10.<first>.<last two>.2
            first = team_str[:-2]
            last = team_str[-2:]
        return f"10.{first}.{last}.2"

    def _resolve_address(self, address: str) -> str | None:
        """Resolve a hostname to an IP address."""
        # Already an IP?
        try:
            socket.inet_aton(address)
            return address
        except OSError:
            pass

        # Try mDNS/DNS resolution
        try:
            ip = socket.gethostbyname(address)
            logger.debug(f"Resolved {address} to {ip}")
            return ip
        except socket.gaierror as e:
            logger.debug(f"Could not resolve {address}: {e}")
            return None

    def _test_connection(self, ip: str, port: int | None = None, timeout: float = 3.0) -> bool:
        """Test if we can connect to the robot's SFTP port."""
        if port is None:
            port = self.config.port
        try:
            sock = socket.create_connection((ip, port), timeout=timeout)
            sock.close()
            return True
        except (TimeoutError, ConnectionRefusedError, OSError) as e:
            logger.debug(f"Connection test failed for {ip}:{port}: {e}")
            return False

    def get_resolved_address(self) -> RobotAddress | None:
        """Get the last successfully resolved address."""
        return self._resolved_address


class RobotNotFoundError(Exception):
    """Raised when robot cannot be located."""

    pass
