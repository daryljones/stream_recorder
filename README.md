# Radio Stream Recorder

A comprehensive Python application for recording and managing radio transmissions from multiple channels simultaneously with advanced audio processing and web interface.

## Features

- **Multi-Channel Recording**: Record from 25+ radio channels simultaneously
- **High-Quality Audio**: FLAC format with advanced audio processing
- **Intelligent Voice Detection**: Smart transmission detection with configurable silence gaps
- **Web Interface**: Modern web UI for monitoring channels and managing recordings
- **Modal Recording Browser**: Detailed modal windows with recording lists for each channel
- **Batch Operations**: 
  - Select and play multiple recordings in sequence
  - Concatenate and download multiple recordings as single FLAC file
  - Chronological sorting (oldest to newest)
- **Refresh Functionality**: Real-time refresh of recording lists in modal windows
- **Time-based Filtering**: Filter recordings by date and time ranges with accurate timezone display
- **Automatic Cleanup**: Built-in temp file cleanup and configurable retention policies
- **Per-Channel Configuration**: Customize volume sensitivity for each channel
- **Cross-Browser Compatibility**: Reliable audio playback across modern browsers
- **Real-time Monitoring**: Live channel status and recording activity
- **Production Ready**: Deployment-agnostic with absolute path handling

## Quick Start

1. **Install Dependencies**:
   ```bash
   uv sync
   ```

2. **Start Recording**:
   ```bash
   ./start_recording.py
   ```

3. **Start Web Interface**:
   ```bash
   uv run python main.py
   ```

4. **Access Web Interface**:
   Open http://localhost:8000 in your browser

## Recent Improvements

### Latest Updates (November 2025)
**Performance and usability improvements**

- The site now loads faster thanks to background caching and quicker file reads.  
- Recording lists update instantly instead of waiting for long calculations.  
- Added a setting (`STATS_REFRESH_SEC`, default 30 s) to control how often cached data refreshes.  
- You can now secure specific streams using optional `username` and `password` fields in `radio_channels.json`.  
- Streams without login details continue to work as before.  
- Added sorting options for group, channel, and total minutes.  
- Recording times now automatically display in your local timezone (files are still stored in UTC).  
- Cleaned up and simplified code for easier maintenance.

> 💡 For detailed technical changes, see the full [CHANGELOG.md](CHANGELOG.md).

## Project Structure

```
stream_recorder/
├── main.py                 # Main Flask web application
├── audio_recorder.py       # Core recording engine with audio processing
├── radio_channels.json     # Channel configuration and settings
├── start_recording.py      # Recording daemon control
├── stop_recording.py       # Stop recording processes
├── templates/              # Web interface templates
├── static/                 # Static web assets (CSS, JS)
├── scripts/                # Utility and cleanup scripts
├── docs/                   # Complete documentation
├── dev/                    # Development and testing tools
├── tests/                  # Unit tests
├── archive/                # Historical files and legacy code
└── audio_files/            # Recorded audio storage (organized by channel)
```

## Core Components

### Main Application (`main.py`)
Flask web application providing:
- REST API endpoints for channel management
- Recording playback and download
- Cleanup operations and status monitoring
- Web interface serving

### Audio Recording Engine (`audio_recorder.py`)
Handles:
- Multi-channel audio streaming with concurrent processing
- Voice activity detection and transmission filtering
- Audio file processing and metadata creation
- Automatic cleanup and maintenance
- Per-channel volume sensitivity configuration
- Configurable silence gaps (currently 4 seconds)

### Configuration (`radio_channels.json`)
Defines radio channels with:
- Channel names and stream URLs
- Enable/disable flags for selective recording
- Channel grouping for organization
- Volume sensitivity settings per channel

## Usage

## Usage

### Web Interface
- **Channel Overview**: Browse channels in the main table with real-time status
- **Modal Recording Browser**: Click channel names to open detailed recording lists
- **Date/Time Filtering**: Use filters to find recordings within specific time ranges
- **Batch Operations**: 
  - Select multiple recordings with checkboxes
  - **Batch Playback**: Play selected recordings in chronological sequence
  - **Batch Download**: Concatenate and download multiple recordings as single FLAC file
- **Real-time Updates**: Use refresh button in modal windows to update recording lists
- **FLAC Audio Playback**: High-quality lossless audio streaming across all browsers
- **Accurate Timestamps**: Recording labels and filters use your browser's local timezone, including daylight saving time
- **Progress Feedback**: Visual indicators for processing operations

Recording lists show the approximate file save time in your local timezone (for example, `09/28/2026, 09:37:45 PM PDT`), with the original filename below it. Filtering and batch ordering use that same file time. The browser sends filter boundaries in UTC; the entire selected end minute is included.

The backend uses UTC regardless of the server's configured timezone. API dates without a timezone are interpreted as UTC. The browser also sends its local midnight boundaries for the "Today" count, so each viewer sees their own calendar day, including daylight saving changes. Statistics remain cached; API clients that omit day boundaries get the UTC day's count.

New recording filenames use UTC. Older versions used the server's local clock for filenames, even though file times were returned by the API in UTC. Existing files are not renamed, and their names are not used to determine the displayed time. These times describe when a file was saved, not the exact start of the transmission; copying files without preserving modification times will change the time shown and used for filtering.

### Audio Quality & Processing
- **MP3 format**: Optimized for web streaming and storage efficiency
- **Voice Activity Detection**: Smart detection of actual transmissions
- **Configurable silence gaps**: 4-second silence detection prevents false stops
- **Volume sensitivity**: Per-channel sensitivity tuning
- **Automatic filtering**: Removes background noise and short clips

### API Endpoints
- `GET /api/channels` - List all channels
- `GET /api/recordings/channel/{name}` - Get channel recordings with date/time filtering
- `GET /api/recording/{filename}` - Download individual recording file
- `POST /api/recordings/concatenate` - Concatenate multiple recordings into single FLAC file
- `GET /api/status` - System status and recording activity
- `GET /api/stats` - Recording statistics and storage information
- `POST /api/cleanup-temp` - Manual cleanup of temporary files
- `GET /api/cleanup-status` - Cleanup statistics and temporary file counts

### Command Line Scripts
- `./start_recording.py` - Start recording daemon for all enabled channels
- `./stop_recording.py` - Stop recording daemon and all processes
- `cleanup_old_recordings.sh` - Remove old recordings (configurable retention)
- `cleanup_temp.py` - Clean up temporary audio files

## Configuration

### Channel Setup
Edit `radio_channels.json` to add/modify channels:
```json
{
  "channels": [
    {
      "name": "Channel Name",
      "url": "https://stream.url/path",
      "enabled": true,
      "group": "Category",
      "volume_sensitivity": 0.01,
      "username": "",
      "password": ""
    }
  ]
}
```
The username and password fields are required for auth-protected streams; if your stream doesn’t require authentication, leave them blank.

### Audio Settings
Key parameters in `audio_recorder.py`:
- `silence_gap`: 4000ms (4 seconds of silence before stopping recording)
- `min_transmission_length`: 500ms (minimum recording duration)
- `max_transmission_length`: 45000ms (maximum recording duration)
- `volume_sensitivity`: Per-channel sensitivity (in radio_channels.json)

## Automated Maintenance

### Temp File Cleanup
- **Startup**: Removes old temp files on application start
- **Background**: Hourly cleanup of orphaned temp files
- **Post-processing**: Immediate cleanup after transmission extraction

### Recording Retention
Set up automated cleanup with cron:
```bash
# Add to crontab for daily cleanup at 2 AM
0 2 * * * /path/to/stream_recorder/scripts/cleanup_old_recordings.sh
```

## Development

### Running Tests
```bash
cd dev/
uv run python test_channels.py
uv run python test_voice_detection.py
```

### Debug Tools
- `dev/debug_recording.py` - Audio processing debugging
- `dev/test_*.py` - Backend component testing
- `dev/test_*.html` - Frontend component testing
- `dev/channel_health_monitor.py` - Channel monitoring utilities

## Requirements

- Python 3.11+
- uv (Python package manager)
- FFmpeg (for audio processing)
- Network access to radio stream URLs
- Sufficient storage for audio files (MP3 format)

## Dependencies

See `pyproject.toml` for complete dependency list. Key packages:
- Flask (web framework)
- pydub (audio processing)
- requests (HTTP client)
- threading (concurrent processing)
- numpy (audio analysis)

## Latest Features Summary

### Batch Download & Concatenation
Select multiple recordings in any channel's modal window and use the "Download Selected" button to:
- Automatically sort recordings chronologically (oldest to newest)
- Concatenate into a single FLAC file using FFmpeg
- Generate descriptive filenames with timestamp ranges
- Download seamlessly with progress feedback

### Enhanced Modal Interface
- **Refresh Button**: Update recording lists without closing the modal
- **Improved Layout**: Better organized controls and visual feedback
- **Accurate Timestamps**: Fixed timezone display issues (all UI timestamps use browser time)
- **Batch Operations**: Select multiple recordings for playback or download

### Production Ready
- **Deployment Flexible**: Works correctly regardless of installation directory
- **Container Compatible**: Ready for Docker deployment
- **Service Ready**: Compatible with systemd and other process managers
- **Path Independent**: Uses absolute paths for reliable file operations

See [CHANGELOG.md](CHANGELOG.md) for detailed version history.

## License

This project is for educational and personal use. Ensure compliance with radio stream terms of service and applicable laws.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes and test thoroughly
4. Submit a pull request

## Support

For issues and questions:
1. Check the documentation in `docs/`
2. Review development tools in `dev/`
3. Examine log files for error details
4. Test with individual components first
