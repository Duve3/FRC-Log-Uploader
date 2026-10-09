"""Google Drive cloud provider implementation."""

import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .base import CloudProvider

logger = logging.getLogger(__name__)

# Google Drive API scopes
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class GoogleDriveProvider(CloudProvider):
    """Google Drive cloud provider implementation."""

    def __init__(
        self,
        credentials_file: Path,
        token_file: Path,
        folder_id: str = "",
    ):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.folder_id = folder_id
        self._service = None
        self._creds = None

    def authenticate(self) -> bool:
        """Authenticate with Google Drive using OAuth2."""
        try:
            # Load existing token
            if self.token_file.exists():
                self._creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)

            # Refresh or get new credentials
            if not self._creds or not self._creds.valid:
                if self._creds and self._creds.expired and self._creds.refresh_token:
                    logger.info("Refreshing expired Google Drive credentials")
                    self._creds.refresh(Request())
                else:
                    logger.info("Starting OAuth2 flow for Google Drive")
                    if not self.credentials_file.exists():
                        raise FileNotFoundError(
                            f"Credentials file not found: {self.credentials_file}"
                        )
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials_file), SCOPES
                    )
                    self._creds = flow.run_local_server(port=0)

                # Save credentials for next run
                self.token_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.token_file, "w") as token:
                    token.write(self._creds.to_json())

            # Build service
            self._service = build("drive", "v3", credentials=self._creds)
            logger.info("Google Drive authentication successful")
            return True

        except Exception as e:
            logger.error(f"Google Drive authentication failed: {e}")
            return False

    def upload(self, file_path: Path, remote_name: str | None = None) -> str:
        """Upload a file to Google Drive."""
        if not self._service:
            if not self.authenticate():
                raise RuntimeError("Failed to authenticate with Google Drive")

        name = remote_name or file_path.name
        logger.info(f"Uploading {name} to Google Drive...")

        try:
            file_metadata = {"name": name}
            if self.folder_id:
                file_metadata["parents"] = [self.folder_id]  # type: ignore[assignment]

            media = MediaFileUpload(
                str(file_path),
                mimetype="application/octet-stream",
                resumable=True,
            )

            file = (
                self._service.files()  # type: ignore[union-attr]
                .create(body=file_metadata, media_body=media, fields="id")
                .execute()
            )

            file_id = file.get("id")
            logger.info(f"Upload complete: {name} (ID: {file_id})")
            return file_id

        except Exception as e:
            logger.error(f"Upload failed for {name}: {e}")
            raise

    def list_files(self, folder_id: str | None = None) -> list[dict]:
        """List files in a Google Drive folder."""
        if not self._service:
            if not self.authenticate():
                raise RuntimeError("Failed to authenticate with Google Drive")

        query = f"'{folder_id or self.folder_id}' in parents and trashed=false"
        results = (
            self._service.files()  # type: ignore[union-attr]
            .list(q=query, fields="files(id, name, size, modifiedTime)")
            .execute()
        )
        return results.get("files", [])


def create_provider(config) -> CloudProvider:
    """Factory function to create cloud provider from config."""
    if config.provider == "google_drive":
        return GoogleDriveProvider(
            credentials_file=Path(config.credentials_file),
            token_file=Path(config.token_file),
            folder_id=config.folder_id,
        )
    raise ValueError(f"Unknown cloud provider: {config.provider}")
