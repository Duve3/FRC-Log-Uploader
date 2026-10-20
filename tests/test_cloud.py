"""Tests for the cloud module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from frc_log_uploader.cloud import create_provider
from frc_log_uploader.cloud.google_drive import GoogleDriveProvider


class TestGoogleDriveProvider:
    """Test Google Drive provider."""

    def test_init(self):
        """Test provider initialization."""
        provider = GoogleDriveProvider(
            credentials_file=Path("/tmp/credentials.json"),
            token_file=Path("/tmp/token.json"),
            folder_id="test_folder",
        )
        assert provider.credentials_file == Path("/tmp/credentials.json")
        assert provider.token_file == Path("/tmp/token.json")
        assert provider.folder_id == "test_folder"
        assert provider._service is None
        assert provider._creds is None

    def test_authenticate_no_token_no_credentials(self):
        """Test authentication fails when no token or credentials exist."""
        provider = GoogleDriveProvider(
            credentials_file=Path("/tmp/nonexistent_creds.json"),
            token_file=Path("/tmp/nonexistent_token.json"),
        )
        result = provider.authenticate()
        assert result is False

    def test_authenticate_with_valid_token(self):
        """Test authentication with existing valid token."""
        provider = GoogleDriveProvider(
            credentials_file=Path("/tmp/credentials.json"),
            token_file=Path("/tmp/token.json"),
        )

        mock_creds = MagicMock()
        mock_creds.valid = True

        with patch.object(Path, "exists", return_value=True):
            with patch(
                "frc_log_uploader.cloud.google_drive.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ):
                with patch("frc_log_uploader.cloud.google_drive.build") as mock_build:
                    mock_service = MagicMock()
                    mock_build.return_value = mock_service
                    result = provider.authenticate()

        assert result is True
        assert provider._service == mock_service

    def test_upload_without_service(self):
        """Test upload fails when not authenticated."""
        provider = GoogleDriveProvider(
            credentials_file=Path("/tmp/credentials.json"),
            token_file=Path("/tmp/token.json"),
        )

        with patch.object(provider, "authenticate", return_value=False):
            with pytest.raises(RuntimeError, match="Failed to authenticate"):
                provider.upload(Path("/tmp/test.wpilog"))

    def test_upload_success(self):
        """Test successful upload."""
        provider = GoogleDriveProvider(
            credentials_file=Path("/tmp/credentials.json"),
            token_file=Path("/tmp/token.json"),
            folder_id="test_folder",
        )

        mock_service = MagicMock()
        mock_file_result = MagicMock()
        mock_file_result.execute.return_value = {"id": "file_123"}
        mock_service.files().create().execute.return_value = {"id": "file_123"}
        provider._service = mock_service

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wpilog", delete=False) as f:
            f.write(b"test data")
            path = Path(f.name)

        try:
            file_id = provider.upload(path, "test.wpilog")
            assert file_id == "file_123"
        finally:
            path.unlink(missing_ok=True)

    def test_list_files(self):
        """Test listing files."""
        provider = GoogleDriveProvider(
            credentials_file=Path("/tmp/credentials.json"),
            token_file=Path("/tmp/token.json"),
        )

        mock_service = MagicMock()
        mock_service.files().list().execute.return_value = {
            "files": [{"id": "1", "name": "test.wpilog"}]
        }
        provider._service = mock_service

        files = provider.list_files("folder_id")
        assert len(files) == 1
        assert files[0]["name"] == "test.wpilog"


class TestCreateProvider:
    """Test provider factory function."""

    def test_create_google_drive_provider(self):
        """Test creating a Google Drive provider."""
        from frc_log_uploader.config import CloudConfig

        config = CloudConfig(
            provider="google_drive",
            folder_id="folder_id",
            credentials_file="credentials.json",
            token_file="token.json",
        )

        provider = create_provider(config)
        assert isinstance(provider, GoogleDriveProvider)
        assert provider.folder_id == "folder_id"

    def test_create_unknown_provider(self):
        """Test creating an unknown provider raises ValueError."""
        from frc_log_uploader.config import CloudConfig

        config = CloudConfig(provider="unknown")

        with pytest.raises(ValueError, match="Unknown cloud provider"):
            create_provider(config)
