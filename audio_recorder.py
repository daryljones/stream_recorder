"""
Simplified Audio Recorder Module
Handles streaming audio capture with simple PCM audio level detection
"""

import os
import json
import time
import threading
import requests
import numpy as np
import queue
from datetime import datetime
from datetime import timezone
from pydub import AudioSegment
import logging
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_utc_datetime(value):
    """Parse API dates without depending on the server's local timezone."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AudioRecorder:
    def __init__(self, config_file="radio_channels.json", output_dir="audio_files"):
        self.config_file = config_file
        self.output_dir = output_dir
        self.channels = {}
        self.streams = {}
        self.is_recording = {}
        self.processing_queue = queue.Queue()
        self.channel_failures = {}
        self.channel_last_success = {}
        self.channel_retry_delays = {}
        self.max_consecutive_failures = 5

        # Buffer management
        self.audio_buffers = {}
        self.buffer_locks = {}
        self.worker_threads = {}
        self.recording_threads = {}

        # Simple audio processing settings
        self.chunk_duration = 45  # seconds - buffer size for processing
        self.min_transmission_length = 500  # ms (minimum 0.5 seconds)
        self.max_transmission_length = 45000  # ms (maximum 45 seconds)
        self.silence_gap = 4000  # ms between transmissions (4 seconds of silence before stopping)
        self.audio_padding = 400  # ms - extra padding at end of transmissions to capture fade-outs

        self.load_channels()
        self.ensure_output_directory()

        # Clean up old temporary files on startup
        self.cleanup_temp_files(max_age_hours=1)

        # Start background cleanup scheduler
        self.start_cleanup_scheduler()

    def load_channels(self):
        """Load radio channels from JSON configuration"""
        try:
            with open(self.config_file, "r") as f:
                config = json.load(f)

            for channel in config.get("channels", []):
                channel_name = channel.get("name", "")
                channel_id = self.sanitize_name(channel_name)
                if channel_id and channel.get("url"):
                    # store auth creds if present
                    self.channels[channel_id] = {
                        "name": channel_name,
                        "url": channel.get("url"),
                        "group": channel.get("group", "Unknown"),
                        "enabled": channel.get("enabled", True),
                        "volume_sensitivity": channel.get("volume_sensitivity", 0.01),
                        "username": channel.get("username"),
                        "password": channel.get("password"),
                    }

            logger.info(f"Loaded {len(self.channels)} channels from configuration")

        except Exception as e:
            logger.error(f"Error loading channels: {e}")
            self.channels = {}

    def calculate_rms_level(self, audio_segment):
        """Calculate RMS (Root Mean Square) level of audio in PCM"""
        try:
            # Convert to numpy array
            samples = np.array(audio_segment.get_array_of_samples(), dtype=np.float32)

            # Normalize to [-1, 1] range
            if audio_segment.sample_width == 2:  # 16-bit
                samples = samples / 32768.0
            elif audio_segment.sample_width == 4:  # 32-bit
                samples = samples / 2147483648.0

            # Calculate RMS
            rms = np.sqrt(np.mean(samples ** 2))
            return rms

        except Exception as e:
            logger.error(f"Error calculating RMS level: {e}")
            return 0.0

    def detect_audio_activity(self, audio_segment, volume_sensitivity):
        """Simple audio activity detection based on RMS level"""
        try:
            rms_level = self.calculate_rms_level(audio_segment)

            # Check if RMS level exceeds sensitivity threshold
            has_audio = rms_level > volume_sensitivity

            if has_audio:
                logger.debug(f"Audio detected: RMS={rms_level:.4f}, threshold={volume_sensitivity}")
                return True, f"Audio level detected (RMS: {rms_level:.4f})"
            else:
                return False, f"Audio too quiet (RMS: {rms_level:.4f}, threshold: {volume_sensitivity})"

        except Exception as e:
            logger.error(f"Error in audio activity detection: {e}")
            return False, "Detection error"

    def find_audio_segments(self, audio_segment, volume_sensitivity):
        """Find segments with audio activity using simple level detection"""
        try:
            # Split audio into small chunks for analysis (100ms chunks)
            chunk_size_ms = 100
            segments = []
            duration_ms = len(audio_segment)

            current_segment_start = None

            for start_ms in range(0, duration_ms, chunk_size_ms):
                end_ms = min(start_ms + chunk_size_ms, duration_ms)
                chunk = audio_segment[start_ms:end_ms]

                has_audio, _ = self.detect_audio_activity(chunk, volume_sensitivity)

                if has_audio:
                    if current_segment_start is None:
                        current_segment_start = start_ms
                else:
                    if current_segment_start is not None:
                        # End of current segment - add padding to capture fade-outs
                        original_end = start_ms
                        padded_end = min(start_ms + self.audio_padding, duration_ms)
                        segments.append((current_segment_start, padded_end))

                        # Log padding application
                        padding_applied = padded_end - original_end
                        if padding_applied > 0:
                            logger.debug(f"Applied {padding_applied}ms padding to segment ending at {original_end}ms")

                        current_segment_start = None

            # Handle segment that goes to the end
            if current_segment_start is not None:
                segments.append((current_segment_start, duration_ms))

            # Merge segments that are close together (within silence_gap)
            merged_segments = []
            for start_ms, end_ms in segments:
                if merged_segments and (start_ms - merged_segments[-1][1]) < self.silence_gap:
                    # Merge with previous segment
                    merged_segments[-1] = (merged_segments[-1][0], end_ms)
                else:
                    merged_segments.append((start_ms, end_ms))

            return merged_segments

        except Exception as e:
            logger.error(f"Error finding audio segments: {e}")
            return []

    def sanitize_name(self, name):
        """Convert channel name to safe filename"""
        import re
        # Replace spaces and special characters with underscores
        sanitized = re.sub(r'[^\w\-_.]', '_', name)
        # Remove multiple consecutive underscores
        sanitized = re.sub(r'_+', '_', sanitized)
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_')
        return sanitized

    def ensure_output_directory(self):
        """Create output directory structure"""
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            for channel_id in self.channels:
                channel_dir = os.path.join(self.output_dir, channel_id)
                os.makedirs(channel_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating directories: {e}")

    def get_timestamp(self):
        """Generate UTC timestamp for filenames"""
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]

    def save_transmission_ffmpeg(self, source_file, start_ms, end_ms, channel_id, timestamp):
        """Save transmission using ffmpeg for precise extraction"""
        try:
            channel_dir = os.path.join(self.output_dir, channel_id)
            output_file = os.path.join(channel_dir, f"{timestamp}_{channel_id}.flac")

            # Convert milliseconds to seconds for ffmpeg
            start_sec = start_ms / 1000.0
            duration_sec = (end_ms - start_ms) / 1000.0

            logger.info(f"Extracting transmission with ffmpeg: {start_ms}-{end_ms}ms ({duration_sec:.2f}s)")

            # FFmpeg command to extract segment with FLAC encoding
            cmd = [
                'ffmpeg', '-y', '-i', source_file,
                '-ss', str(start_sec),
                '-t', str(duration_sec),
                '-c:a', 'flac',  # Use FLAC encoding
                output_file
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                # Create metadata file
                metadata = {
                    "channel": self.channels[channel_id]["name"],
                    "timestamp": timestamp,
                    "duration_ms": end_ms - start_ms,
                    "start_time": datetime.now(timezone.utc).isoformat(),
                    "source_file": os.path.basename(source_file),
                    "extraction_method": "ffmpeg_flac"
                }

                metadata_file = output_file.replace('.flac', '_metadata.json')
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)

                logger.info(f"Successfully extracted transmission: {output_file}")
                logger.info(f"Created metadata: {metadata_file}")

                return output_file
            else:
                logger.error(f"ffmpeg extraction failed: {result.stderr}")
                return None

        except Exception as e:
            logger.error(f"Error saving transmission with ffmpeg: {e}")
            return None

    def process_audio_segment(self, filepath, channel_id):
        """Process audio segment with simple level-based detection"""
        try:
            logger.info(f"Processing complete segment for {channel_id}: {filepath}")

            # Load audio file
            audio_segment = AudioSegment.from_file(filepath)
            logger.info(f"File size: {os.path.getsize(filepath)} bytes")
            logger.info(f"Loaded audio segment: {len(audio_segment)}ms, {audio_segment.frame_rate}Hz, {audio_segment.channels} channels")

            # Get channel-specific volume sensitivity
            volume_sensitivity = self.channels[channel_id].get("volume_sensitivity", 0.01)

            # Calculate overall audio levels for logging
            rms_level = self.calculate_rms_level(audio_segment)
            max_db = audio_segment.max_dBFS if len(audio_segment) > 0 else -float('inf')

            logger.info(f"Audio levels: max_dBFS={max_db:.1f}, RMS={rms_level:.4f}")
            logger.info(f"Volume sensitivity threshold: {volume_sensitivity}")
            logger.info(f"Audio padding enabled: {self.audio_padding}ms")

            # Find audio segments
            audio_segments = self.find_audio_segments(audio_segment, volume_sensitivity)

            logger.info(f"Found {len(audio_segments)} audio segments")

            saved_count = 0
            filtered_count = 0

            # Process each detected audio segment
            for start_ms, end_ms in audio_segments:
                transmission_length = end_ms - start_ms
                logger.info(f"Audio segment detected: {transmission_length}ms (range: {start_ms}-{end_ms})")

                # Filter by transmission length
                if self.min_transmission_length <= transmission_length <= self.max_transmission_length:
                    # Save the transmission
                    timestamp = self.get_timestamp()
                    saved_file = self.save_transmission_ffmpeg(filepath, start_ms, end_ms, channel_id, timestamp)

                    if saved_file:
                        logger.info(f"Saved transmission: {saved_file} (RMS: {rms_level:.4f})")
                        saved_count += 1
                    else:
                        logger.warning("Failed to save transmission with ffmpeg")
                        filtered_count += 1
                else:
                    logger.info(f"Transmission filtered out: {transmission_length}ms (too short/long)")
                    filtered_count += 1

            logger.info(f"Segment processing complete: {saved_count} transmissions saved, {filtered_count} filtered out")
            logger.info(f"Successfully processed segment for {channel_id}")

        except Exception as e:
            logger.error(f"Error processing audio segment: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # Ensure temp file is always cleaned up, even if processing fails
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    logger.info(f"Cleaned up temp file: {filepath}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {filepath}: {e}")

    def cleanup_temp_files(self, max_age_hours=1):
        """Clean up old temporary files

        Args:
            max_age_hours: Maximum age in hours. Set to 0 to remove all temp files regardless of age.

        Returns:
            int: Number of files removed
        """
        try:
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            removed_count = 0

            for channel_id in self.channels:
                channel_dir = os.path.join(self.output_dir, channel_id)
                if os.path.exists(channel_dir):
                    for filename in os.listdir(channel_dir):
                        if filename.startswith('temp_'):
                            filepath = os.path.join(channel_dir, filename)
                            try:
                                if max_age_hours == 0:
                                    # Force cleanup all temp files
                                    os.remove(filepath)
                                    logger.info(f"Removed temp file: {filepath}")
                                    removed_count += 1
                                else:
                                    # Check age before removal
                                    file_age = current_time - os.path.getmtime(filepath)
                                    if file_age > max_age_seconds:
                                        os.remove(filepath)
                                        logger.info(f"Removed old temp file: {filepath}")
                                        removed_count += 1
                            except Exception as e:
                                logger.warning(f"Failed to remove temp file {filepath}: {e}")

            if removed_count > 0:
                logger.info(f"Cleanup complete: removed {removed_count} temporary files")
            else:
                logger.info("No temporary files found to remove")

            return removed_count

        except Exception as e:
            logger.error(f"Error during temp file cleanup: {e}")
            return 0

    def start_cleanup_scheduler(self):
        """Start background cleanup scheduler"""
        def cleanup_worker():
            while True:
                time.sleep(3600)  # Run every hour
                self.cleanup_temp_files()

        cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
        cleanup_thread.start()
        logger.info("Background temp file cleanup scheduler started")

    def start_recording(self, channel_ids):
        """Start recording for specified channels"""
        for channel_id in channel_ids:
            if channel_id not in self.channels:
                logger.error(f"Channel {channel_id} not found in configuration")
                continue

            if self.is_recording.get(channel_id, False):
                logger.warning(f"Channel {channel_id} is already recording")
                continue

            channel_info = self.channels[channel_id]
            if not channel_info.get('enabled', True):
                logger.info(f"Channel {channel_id} is disabled, skipping")
                continue

            logger.info(f"Starting stream for channel: {channel_info['name']} ({channel_info['url']})")

            # Start recording thread
            self.is_recording[channel_id] = True
            thread = threading.Thread(target=self._record_channel, args=(channel_id,))
            thread.daemon = True
            thread.start()
            self.recording_threads[channel_id] = thread

            logger.info(f"Started recording thread for: {channel_id}")

    def stop_recording(self, channel_ids=None):
        """Stop recording for specified channels or all if None"""
        if channel_ids is None:
            channel_ids = list(self.is_recording.keys())

        for channel_id in channel_ids:
            if channel_id in self.is_recording:
                logger.info(f"Stopping recording for: {channel_id}")
                self.is_recording[channel_id] = False

                # Wait for thread to finish
                if channel_id in self.recording_threads:
                    self.recording_threads[channel_id].join(timeout=10)
                    del self.recording_threads[channel_id]

                logger.info(f"Stopped recording for channel: {self.channels[channel_id]['name']}")

    def get_status(self):
        """Get current recording status for all channels"""
        status = {}
        for channel_id, channel_info in self.channels.items():
            status[channel_id] = {
                'name': channel_info['name'],
                'group': channel_info['group'],
                'enabled': channel_info.get('enabled', True),
                'recording': self.is_recording.get(channel_id, False),
                'volume_sensitivity': channel_info.get('volume_sensitivity', 0.01)
            }
        return status

    def _record_channel(self, channel_id):
        """Record audio from a single channel"""
        channel_info = self.channels[channel_id]
        url = channel_info['url']

        # Build auth if this channel has creds
        auth = None
        if channel_info.get("username") and channel_info.get("password"):
            auth = requests.auth.HTTPBasicAuth(
                channel_info["username"],
                channel_info["password"]
            )

        while self.is_recording.get(channel_id, False):
            try:
                # Create temp file for this segment
                timestamp = self.get_timestamp()
                channel_dir = os.path.join(self.output_dir, channel_id)
                temp_file = os.path.join(channel_dir, f"temp_{timestamp}_{channel_id}.mp3")

                # Stream audio and save to temp file
                response = requests.get(
                    url,
                    stream=True,
                    timeout=30,
                    auth=auth
                )

                if response.status_code == 200:
                    content_type = response.headers.get('content-type', '')
                    logger.info(f"Stream content type for {channel_id}: {content_type}")
                    logger.info(f"Creating temp file: {temp_file}")

                    bytes_written = 0
                    start_time = time.time()

                    with open(temp_file, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if not self.is_recording.get(channel_id, False):
                                break

                            if chunk:
                                f.write(chunk)
                                bytes_written += len(chunk)

                            # Check if we've reached our chunk duration
                            if time.time() - start_time >= self.chunk_duration:
                                break

                    elapsed_time = time.time() - start_time
                    logger.info(f"Finished recording segment for {channel_id}: {bytes_written} bytes written in {elapsed_time:.1f}s")

                    # Process the recorded segment
                    if bytes_written > 0:
                        self.process_audio_segment(temp_file, channel_id)
                    else:
                        # Remove empty temp file
                        try:
                            os.remove(temp_file)
                        except Exception:
                            pass
                else:
                    logger.error(f"Failed to connect to {channel_id}: HTTP {response.status_code}")
                    time.sleep(5)  # Wait before retrying

            except Exception as e:
                logger.error(f"Error recording {channel_id}: {e}")
                time.sleep(5)  # Wait before retrying

    def get_recordings(self, channel_id=None, limit=50):
        """Get list of recordings"""
        recordings = []

        channels_to_check = [channel_id] if channel_id else self.channels.keys()

        for ch_id in channels_to_check:
            channel_dir = os.path.join(self.output_dir, ch_id)
            if os.path.exists(channel_dir):
                files = os.listdir(channel_dir)
                flac_files = [f for f in files if f.endswith('.flac') and not f.startswith('temp_')]
                # Legacy filenames use server-local time; sort by the actual file time.
                flac_files.sort(key=lambda name: os.path.getmtime(os.path.join(channel_dir, name)), reverse=True)

                for filename in flac_files[:limit]:
                    filepath = os.path.join(channel_dir, filename)
                    stat = os.stat(filepath)

                    recording_info = {
                        'filename': filename,
                        'channel_id': ch_id,
                        'channel_name': self.channels[ch_id]['name'],
                        'file_size': stat.st_size,
                        'created_time': datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                        'modified_time': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        'file_path': filepath
                    }

                    # Load metadata if available
                    metadata_file = filepath.replace('.flac', '_metadata.json')
                    if os.path.exists(metadata_file):
                        try:
                            with open(metadata_file, 'r') as f:
                                metadata = json.load(f)
                            recording_info.update(metadata)
                        except Exception as e:
                            logger.warning(f"Failed to load metadata from {metadata_file}: {e}")

                    recordings.append(recording_info)

        return sorted(recordings, key=lambda x: x['modified_time'], reverse=True)[:limit]

    def get_channel_recordings(self, channel_id, limit=50, offset=0, start_date=None, end_date=None):
        """Get recordings for a specific channel with filtering options"""
        recordings = []

        channel_dir = os.path.join(self.output_dir, channel_id)
        if not os.path.exists(channel_dir):
            return recordings

        files = os.listdir(channel_dir)
        flac_files = [f for f in files if f.endswith('.flac') and not f.startswith('temp_')]
        # Normalize filters to UTC; timezone-free API inputs are also treated as UTC.
        start_dt = None
        end_dt = None
        if start_date:
            try:
                start_dt = parse_utc_datetime(start_date)
            except Exception:
                pass
        if end_date:
            try:
                end_dt = parse_utc_datetime(end_date)
            except Exception:
                pass

        for filename in flac_files:
            filepath = os.path.join(channel_dir, filename)
            stat = os.stat(filepath)
            modified_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

            # Apply date filtering
            if start_dt and modified_time < start_dt:
                continue
            if end_dt and modified_time > end_dt:
                continue

            recording_info = {
                'filename': filename,
                'channel_id': channel_id,
                'channel_name': self.channels.get(channel_id, {}).get('name', channel_id),
                'file_size': stat.st_size,
                'created_time': datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                'modified_time': modified_time.isoformat(),
                'file_path': filepath
            }

            # Load metadata if available
            metadata_file = filepath.replace('.flac', '_metadata.json')
            if os.path.exists(metadata_file):
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    recording_info.update(metadata)
                except Exception as e:
                    logger.warning(f"Failed to load metadata from {metadata_file}: {e}")

            recordings.append(recording_info)

        # Sort before pagination so legacy and UTC filenames stay in chronological order.
        recordings.sort(key=lambda recording: recording['modified_time'], reverse=True)
        start_idx = offset
        end_idx = offset + limit
        return recordings[start_idx:end_idx]
