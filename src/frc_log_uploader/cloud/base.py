"""Cloud provider base interface."""

from abc import ABC, abstractmethod
from pathlib import Path


class CloudProvider(ABC):
    """Abstract base class for cloud providers."""

    @abstractmethod
    def upload(self, file_path: Path, remote_name: str | None = None) -> str:
        """Upload a file to cloud storage.

        Args:
            file_path: Local path to the file to upload
            remote_name: Optional name for the remote file

        Returns:
            Remote file ID or URL
        """
        pass

    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with the cloud provider.

        Returns:
            True if authentication successful
        """
        pass
