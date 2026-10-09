"""Service layer - orchestrates discovery, download, and upload."""

import logging
import time
from pathlib import Path

from frc_log_uploader.cloud import CloudProvider, create_provider
from frc_log_uploader.config import Config
from frc_log_uploader.discovery import DiscoveryResult, LogFile, LogScanner
from frc_log_uploader.robot import RobotAddress, RobotLocator, RobotNotFoundError
from frc_log_uploader.transfer import LogDownloader

logger = logging.getLogger(__name__)


class LogUploaderService:
    """Main service that coordinates the log upload process."""

    def __init__(self, config: Config):
        self.config = config
        self.locator = RobotLocator(config.robot)
        self.resolved_address: RobotAddress | None = None
        self.scanner = LogScanner(config.robot)
        self.downloader = LogDownloader(config.robot)
        self.cloud_provider: CloudProvider | None = None

    def _get_cloud_provider(self) -> CloudProvider:
        """Get or create the cloud provider."""
        if self.cloud_provider is None:
            self.cloud_provider = create_provider(self.config.cloud)
        return self.cloud_provider

    def _ensure_robot_found(self) -> RobotAddress:
        """Ensure robot is located and return resolved address."""
        if self.resolved_address is None:
            self.resolved_address = self.locator.find()
            # Update the scanner and downloader configs with the resolved IP
            self.config.robot.address = self.resolved_address.ip
        return self.resolved_address

    def scan(self) -> DiscoveryResult:
        """Scan for logs on the roboRIO."""
        self._ensure_robot_found()
        logger.info(f"Scanning {self.resolved_address} for logs...")
        return self.scanner.discover()

    def select_logs(self, discovery: DiscoveryResult) -> list[LogFile]:
        """Select the newest stable logs for upload."""
        logs = discovery.sorted_by_time(newest_first=True)

        if not logs:
            logger.info("No logs found")
            return []

        # Check stability of the newest log
        stable_logs = []
        for log in logs:
            if self._is_stable(log):
                stable_logs.append(log)
                if len(stable_logs) >= self.config.logs.count:
                    break
            else:
                logger.info(f"Skipping unstable log: {log.name}")

        if not stable_logs:
            logger.info("No stable logs available")
            return []

        logger.info(f"Selected {len(stable_logs)} stable logs for upload")
        return stable_logs

    def _is_stable(self, log: LogFile) -> bool:
        """Check if a log file is stable (not being written)."""
        if log.is_directory:
            # For directories, check if any file inside is changing
            # For simplicity, we'll consider directories stable
            return True

        return self.scanner.check_file_stable(
            log.full_remote_path, self.config.logs.stability_seconds
        )

    def run_once(self) -> bool:
        """Run one complete upload cycle.

        Returns:
            True if at least one log was uploaded successfully
        """
        try:
            # Discover logs
            discovery = self.scan()

            # Select logs to upload
            logs_to_upload = self.select_logs(discovery)
            if not logs_to_upload:
                return False

            # Download logs
            download_results = self.downloader.download_multiple(logs_to_upload)

            # Upload successful downloads
            provider = self._get_cloud_provider()
            uploaded_count = 0

            for result in download_results:
                if not result.success:
                    logger.error(f"Skipping upload for {result.log_file.name}: download failed")
                    continue

                try:
                    provider.upload(result.local_path, result.log_file.name)
                    uploaded_count += 1

                    # Clean up local file after successful upload
                    self._cleanup_local_file(result.local_path)

                except Exception as e:
                    logger.error(f"Upload failed for {result.log_file.name}: {e}")
                    # Keep local file for retry

            logger.info(f"Cycle complete: {uploaded_count}/{len(logs_to_upload)} logs uploaded")
            return uploaded_count > 0

        except RobotNotFoundError as e:
            logger.warning(f"Robot not found: {e}")
            return False
        except Exception as e:
            logger.error(f"Upload cycle failed: {e}")
            return False

    def _cleanup_local_file(self, path: Path) -> None:
        """Remove local file or directory after successful upload."""
        import shutil

        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            logger.debug(f"Cleaned up {path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup {path}: {e}")

    def watch(self) -> None:
        """Continuously watch for robot and upload logs."""
        logger.info("Starting watch mode...")
        logger.info(f"Poll interval: {self.config.watch.poll_interval}s")

        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("Watch mode stopped by user")
                break
            except RobotNotFoundError:
                # Robot not found is expected in watch mode, log at info level
                logger.info("Robot not available, waiting for next poll...")
            except Exception as e:
                logger.error(f"Watch cycle error: {e}")

            # Reset resolved address so we re-discover on next cycle
            self.resolved_address = None

            logger.info(f"Waiting {self.config.watch.poll_interval}s before next check...")
            time.sleep(self.config.watch.poll_interval)
