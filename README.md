# FRC Log Uploader

Automated utility for downloading log files from an FRC roboRIO and uploading them to Google Drive.

## Overview

This tool runs on a pit laptop and automatically:
1. Detects when a roboRIO is connected
2. Discovers the most recent log files via FTP
3. Excludes logs that are still being actively written
4. Downloads the newest N stable logs
5. Uploads them to Google Drive
6. Cleans up local temporary files

## Installation

### Prerequisites

- Python 3.13+
- `uv` (recommended) or `pip`

### Install with uv

```bash
uv pip install -e .
```

Or with pip:

```bash
pip install -e .
```

## Configuration

Create a configuration file:

```bash
frc-log-uploader init-config
```

This creates `~/.config/frc-log-uploader/config.toml`. Edit it with your settings:

```toml
[robot]
team = 10079                          # Your FRC team number (required for auto-discovery)
address = ""                          # Optional: explicit address override (IP or hostname)
log_directory = "/home/lvuser/logs"   # Default roboRIO log directory
username = "lvuser"                   # Default roboRIO username
password = ""                         # Default roboRIO password (empty)
port = 22                             # SSH/SFTP port (FTP uses 21 — do not use for SFTP)
use_ftp = false                       # Use SFTP (correct protocol per WPILib)

[logs]
count = 5                             # Number of logs to upload
stability_seconds = 2                 # Seconds to wait for file stability check

[cloud]
provider = "google_drive"
folder_id = "YOUR_GOOGLE_DRIVE_FOLDER_ID"
credentials_file = "credentials.json" # OAuth2 credentials from Google Cloud Console
token_file = "token.json"             # Auto-generated after first auth

[watch]
poll_interval = 30                    # Seconds between checks in watch mode
```

## Google Drive Setup

### Overview

The tool uses **OAuth 2.0** to authenticate with Google Drive on behalf of your team account. This requires two files:

- **`credentials.json`** - Your OAuth 2.0 client credentials (downloaded from Google Cloud Console)
- **`token.json`** - Auto-generated access/refresh tokens (created on first run)

### Step-by-Step Setup

1. **Create a Google Cloud Project**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one
   - Note the project name for your team

2. **Enable Google Drive API**
   - Navigate to **APIs & Services → Library**
   - Search for "Google Drive API" and click **Enable**

3. **Configure OAuth Consent Screen**
   - Go to **APIs & Services → OAuth consent screen**
   - Choose **External** user type (unless you have Google Workspace)
   - Fill in required fields (App name, User support email, Developer contact)
   - Add scopes: `https://www.googleapis.com/auth/drive.file`
   - Add your team's email addresses as **Test users** (since the app is in testing mode)

4. **Create OAuth 2.0 Credentials**
   - Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth client ID**
   - Application type: **Desktop application**
   - Name: "FRC Log Uploader" (or similar)
   - Click **Create**
   - Download the JSON file → rename to `credentials.json`
   - Place it in the config directory: `~/.config/frc-log-uploader/credentials.json`

5. **Get Google Drive Folder ID**
   - Open Google Drive in browser
   - Navigate to the folder where logs should be uploaded
   - Copy the folder ID from the URL:
     `https://drive.google.com/drive/folders/<FOLDER_ID_HERE>`
   - Add it to your `config.toml`:
     ```toml
     [cloud]
     folder_id = "YOUR_FOLDER_ID_HERE"
     ```

6. **First Run - Authorization**
   - Run the tool: `frc-log-uploader scan` (or `run`/`watch`)
   - A browser window will open asking you to sign in and grant permission
   - After approval, `token.json` is created automatically in the config directory
   - Subsequent runs use the saved token (auto-refreshed when expired)

### File Locations

| File | Location | Purpose |
|------|----------|---------|
| `credentials.json` | `~/.config/frc-log-uploader/credentials.json` | OAuth client ID/secret (you provide) |
| `token.json` | `~/.config/frc-log-uploader/token.json` | Access/refresh tokens (auto-generated) |
| `config.toml` | `~/.config/frc-log-uploader/config.toml` | Your configuration |

### Troubleshooting

- **"Invalid client" error**: Verify `credentials.json` is in the correct location and matches your Cloud Console project
- **"Access blocked"**: Ensure your email is added as a test user on the OAuth consent screen
- **Token expired**: Delete `token.json` and re-run to re-authorize
- **Permission denied**: Ensure the folder ID is correct and your account has edit access to that folder

## Usage

### Scan for logs (one-time)

```bash
frc-log-uploader scan
```

Output:
```
Connected to roborio-10079-frc.local
Log directory: /home/lvuser/logs
Found 17 items

┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Time               ┃ Type ┃ Size      ┃ Name                      ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 2026-10-08 12:50   │ FILE │ 184.0 MB  │ match_006.wpilog          │
│ 2026-10-08 12:40   │ FILE │ 192.0 MB  │ match_005.wpilog          │
│ 2026-10-08 12:30   │ FILE │ 188.0 MB  │ match_004.wpilog          │
└────────────────────┴──────┴───────────┴───────────────────────────┘
```

### Run one upload cycle

```bash
frc-log-uploader run
```

### Watch mode (continuous)

```bash
frc-log-uploader watch
```

This continuously polls for the robot and automatically uploads logs when connected. Press `Ctrl+C` to stop.

**Behavior when robot is disconnected:**
- In `watch` mode, the tool logs "Robot not available, waiting for next poll..." and retries after the configured `poll_interval`
- In `run` mode, the tool exits with an error if no robot is found
- The tool does not treat temporary connection failures as fatal errors in `watch` mode

## How It Works

### Robot Discovery

The tool uses a multi-step discovery process to find the roboRIO:

1. **Explicit address override** - If `address` is set in config, use it directly
2. **Team-derived IP** - Derives `10.xx.yy.2` from team number (e.g., team 10079 → `10.100.79.2`)
3. **mDNS fallback** - Tries `roborio-<team>-frc.local` (e.g., `roborio-10079-frc.local`)
4. **USB fallback** - Tries `172.22.11.2` (USB tethering address)

This mirrors the discovery behavior used by WPILib tools and AdvantageScope 2026.

### Log Discovery

- Connects to roboRIO via SFTP (port 22)
- Recursively scans the configured log directory
- Collects file/directory names, sizes, and modification times
- Supports subdirectories (used by CTRE Signal Logger)

### Active Log Detection

The newest log file may still be recording. The tool checks stability by:
1. Getting the file size
2. Waiting `stability_seconds` (default 2s)
3. Getting the file size again
4. If sizes match, the file is considered stable

### Log Selection

1. Sort all logs by modification time (newest first)
2. Remove unstable (actively writing) logs
3. Select the newest `count` stable logs

Example with `count=5`:
```
12:50 match_006  unstable (excluded)
12:40 match_005  stable  ← selected
12:30 match_004  stable  ← selected
12:20 match_003  stable  ← selected
12:10 match_002  stable  ← selected
12:00 match_001  stable  ← selected
11:50 match_000  stable  (not selected, count=5)
```

### Download

- Downloads files to a temporary staging directory
- Preserves directory structure for subdirectories
- Cleans up partial downloads on failure

### Upload

- Uploads to Google Drive using resumable uploads
- Only deletes local file after successful upload
- Failed uploads keep local files for retry

## Supported Log Formats

- WPILOG (.wpilog) - WPILib DataLog, AdvantageKit
- Hoot (.hoot) - CTRE Phoenix 6 Signal Logger
- REVLOG (.revlog) - REV Robotics StatusLogger
- DS Logs (.dslog, .dsevents) - Driver Station
- RLOG (.rlog) - AdvantageKit legacy
- Custom (.log) - Road Runner, others

## AdvantageScope Research Notes

This implementation is based on research into AdvantageScope's 2026 release:

### Key Findings

1. **Protocol**: SFTP is the correct protocol per WPILib (FTP on port 21 is legacy)
   - SFTP: runs over SSH on port 22
   - FTP: runs on port 21 (legacy, not recommended)

2. **Default Log Directory**: `/home/lvuser/logs` (standard WPILib location)

3. **Recursive Discovery**: Supports subdirectories for CTRE Signal Logger format

4. **Connection**: Uses mDNS address (`roborio-XXXX-frc.local`) or USB fallback (`172.22.11.2`)

5. **Authentication**: Default `lvuser` user with empty password (standard roboRIO SSH config)

6. **File Listing**: Uses SFTP `listdir_attr` for detailed metadata (size, modification time, type)

### Implementation Decisions

- **SFTP over FTP**: Following WPILib's recommendation — SFTP is the correct protocol
- **Port 22**: SSH/SFTP runs on port 22; port 21 is FTP only and will not work with paramiko
- **No resumability**: SFTP doesn't natively support resume; files are typically <200MB
- **Stability check via size polling**: Simple and effective for detecting active writes
- **Google Drive API**: Official API with OAuth2, not service account (simpler for team use)

## Error Handling

The tool handles gracefully:
- Robot unavailable (waits and retries in watch mode)
- Network disconnection during transfer (cleans up partial files)
- Google Drive upload failures (keeps local files for retry)
- No stable logs (reports and waits)

## Development

### Run tests

```bash
python -m pytest tests/ -v
```

### Lint

```bash
python -m ruff check src/
python -m ruff format src/
```

## License

MIT License - see LICENSE file for details.