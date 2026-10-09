"""Configuration management using TOML."""

from dataclasses import dataclass, field
from pathlib import Path

import tomli
import tomli_w


@dataclass
class RobotConfig:
    """Robot connection configuration."""

    team: int | None = None
    address: str = ""
    log_directory: str = "/home/lvuser/logs"
    username: str = "lvuser"
    password: str = ""
    port: int = 21
    use_ftp: bool = True


@dataclass
class LogsConfig:
    """Log selection configuration."""

    count: int = 5
    stability_seconds: float = 2.0
    extensions: list[str] = field(
        default_factory=lambda: [
            ".wpilog",
            ".hoot",
            ".revlog",
            ".dslog",
            ".dsevents",
            ".rlog",
            ".log",
        ]
    )


@dataclass
class CloudConfig:
    """Cloud upload configuration."""

    provider: str = "google_drive"
    folder_id: str = ""
    credentials_file: str = "credentials.json"
    token_file: str = "token.json"


@dataclass
class WatchConfig:
    """Watch mode configuration."""

    poll_interval: int = 30
    max_retries: int = 3
    retry_delay: float = 5.0


@dataclass
class Config:
    """Main configuration."""

    robot: RobotConfig = field(default_factory=RobotConfig)
    logs: LogsConfig = field(default_factory=LogsConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load configuration from TOML file."""
        if not path.exists():
            return cls()

        with open(path, "rb") as f:
            data = tomli.load(f)

        config = cls()
        if "robot" in data:
            config.robot = RobotConfig(**data["robot"])
        if "logs" in data:
            config.logs = LogsConfig(**data["logs"])
        if "cloud" in data:
            config.cloud = CloudConfig(**data["cloud"])
        if "watch" in data:
            config.watch = WatchConfig(**data["watch"])
        return config

    def save(self, path: Path) -> None:
        """Save configuration to TOML file."""

        def filter_none(d: dict) -> dict:
            """Recursively remove None values from dict."""
            result = {}
            for k, v in d.items():
                if v is None:
                    continue
                if isinstance(v, dict):
                    filtered = filter_none(v)
                    if filtered:
                        result[k] = filtered
                else:
                    result[k] = v
            return result

        data = filter_none(
            {
                "robot": self.robot.__dict__,
                "logs": self.logs.__dict__,
                "cloud": self.cloud.__dict__,
                "watch": self.watch.__dict__,
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            tomli_w.dump(data, f)

    @classmethod
    def default_path(cls) -> Path:
        """Get the default configuration file path."""
        return Path.home() / ".config" / "frc-log-uploader" / "config.toml"

    @classmethod
    def load_default(cls) -> "Config":
        """Load configuration from default path."""
        return cls.load(cls.default_path())

    def create_example(self, path: Path) -> None:
        """Create an example configuration file."""
        example = Config()
        example.save(path)
