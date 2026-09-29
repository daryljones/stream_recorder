"""
Radio Stream Recorder - Main Flask Application
Simplified audio recording system with RMS-based detection and FLAC output
"""
__version__ = "1.1.1"

import os
import time
import json
import threading
from bisect import bisect_left
from typing import Any, Dict
from flask import Flask, render_template, jsonify, request, send_file

from audio_recorder import AudioRecorder, parse_utc_datetime
# Get the directory where this script is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Initialize audio recorder with proper paths
audio_recorder = AudioRecorder(
    config_file=os.path.join(BASE_DIR, "radio_channels.json"),
    output_dir=os.path.join(BASE_DIR, "audio_files"),
)

_STATS_CACHE: Dict[str, Any] = {}
_CHANMINS_CACHE: Dict[str, Any] = {}
_REFRESH_SEC = int(os.environ.get("STATS_REFRESH_SEC", "30"))

def _generate_stats(recorder: AudioRecorder, base_dir: str) -> Dict[str, Any]:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    stats: Dict[str, Any] = {}
    total_recordings = 0
    total_size = 0
    today_recordings = 0
    enabled_channels = {cid: meta for cid, meta in recorder.channels.items() if meta.get("enabled", True)}
    for channel_id, meta in enabled_channels.items():
        channel_dir = os.path.join(base_dir, "audio_files", channel_id)
        channel_count = 0
        channel_size = 0
        channel_today = 0
        recording_times = []
        if os.path.exists(channel_dir):
            try:
                with os.scandir(channel_dir) as it:
                    for entry in it:
                        if not entry.is_file():
                            continue
                        name = entry.name
                        if not (name.endswith(".mp3") or name.endswith(".flac")):
                            continue
                        channel_count += 1
                        try:
                            stat = entry.stat()
                            channel_size += stat.st_size
                        except FileNotFoundError:
                            continue
                        # Cache file times so client-local day counts need no directory rescan.
                        recording_times.append(stat.st_mtime)
                        if datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).date() == today:
                            channel_today += 1
            except FileNotFoundError:
                pass
        stats[channel_id] = {
            "name": meta.get("name", channel_id),
            "recordings": channel_count,
            "total_size": channel_size,
            "today": channel_today,
            "_recording_times": sorted(recording_times),
        }
        total_recordings += channel_count
        total_size += channel_size
        today_recordings += channel_today
    stats["total"] = {
        "recordings": total_recordings,
        "total_size": total_size,
        "channels": len(enabled_channels),
        "today": today_recordings,
    }
    return stats

def _generate_channel_minutes(recorder: AudioRecorder, base_dir: str) -> Dict[str, float]:
    minutes_data: Dict[str, float] = {}
    enabled_channels = {cid: meta for cid, meta in recorder.channels.items() if meta.get("enabled", True)}
    for channel_id in enabled_channels:
        channel_dir = os.path.join(base_dir, "audio_files", channel_id)
        total_ms = 0
        if os.path.exists(channel_dir):
            try:
                with os.scandir(channel_dir) as it:
                    for entry in it:
                        if not entry.is_file():
                            continue
                        if not entry.name.endswith("_metadata.json"):
                            continue
                        try:
                            with open(entry.path, "r") as f:
                                meta = json.load(f)
                            total_ms += int(meta.get("duration_ms", 0))
                        except (json.JSONDecodeError, FileNotFoundError, ValueError):
                            continue
            except FileNotFoundError:
                pass
        minutes_data[channel_id] = round(total_ms / 60000.0, 1)
    return minutes_data

def create_app():
    app = Flask(__name__)
    # Simple configuration
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    app.config["DEBUG"] = os.environ.get("FLASK_DEBUG", "False").lower() == "true"

    def _refresher():
        global _STATS_CACHE, _CHANMINS_CACHE
        while True:
            try:
                _STATS_CACHE = _generate_stats(audio_recorder, BASE_DIR)
            except Exception as e:
                print("stats refresh failed:", e)
            try:
                _CHANMINS_CACHE = _generate_channel_minutes(audio_recorder, BASE_DIR)
            except Exception as e:
                print("channel-minutes refresh failed:", e)
            time.sleep(_REFRESH_SEC)

    threading.Thread(target=_refresher, daemon=True).start()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/robots.txt")
    def robots_txt():
        return send_file("static/robots.txt", mimetype="text/plain")

    @app.route("/test")
    def test():
        return send_file("test_page.html")

    @app.route("/test_api")
    def test_api():
        return send_file("test_api.html")

    @app.route("/test_audio")
    def test_audio():
        return send_file("test_audio.html")

    @app.route("/api/health")
    def health_check():
        return jsonify({"status": "healthy", "message": "Police Radio Recorder API is running"})

    @app.route("/api/channels")
    def get_channels():
        """Get all available radio channels"""
        # Only return channels that are enabled in configuration
        enabled_channels = {cid: meta for cid, meta in audio_recorder.channels.items() if meta.get("enabled", True)}
        return jsonify(enabled_channels)

    @app.route("/api/status")
    def get_recording_status():
        """Get current recording status for all channels"""
        status = audio_recorder.get_status()
        return jsonify(status)

    @app.route("/api/start", methods=["POST"])
    def start_recording():
        """Start recording for specified channels"""
        data = request.get_json() or {}
        channel_ids = data.get("channels")  # If None, starts all enabled channels
        try:
            audio_recorder.start_recording(channel_ids)
            return jsonify({"status": "success", "message": "Recording started", "channels": channel_ids or list(audio_recorder.channels.keys())})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/stop", methods=["POST"])
    def stop_recording():
        """Stop recording for specified channels"""
        data = request.get_json() or {}
        channel_ids = data.get("channels")  # If None, stops all channels
        try:
            audio_recorder.stop_recording(channel_ids)
            return jsonify({"status": "success", "message": "Recording stopped", "channels": channel_ids or list(audio_recorder.channels.keys())})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/recordings")
    def get_recordings():
        """Get list of recorded transmissions with optional filtering"""
        channel_id = request.args.get("channel")
        limit = int(request.args.get("limit", 100))
        start_date = request.args.get("start_date")  # YYYY-MM-DD format
        end_date = request.args.get("end_date")  # YYYY-MM-DD format
        search_text = request.args.get("search")  # Search in channel names
        recordings = audio_recorder.get_recordings(channel_id=channel_id, limit=limit, start_date=start_date, end_date=end_date, search_text=search_text)
        return jsonify(recordings)

    @app.route("/api/recordings/channel/<channel_id>")
    def get_channel_recordings(channel_id):
        """Get recordings for a specific channel with pagination"""
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        recordings = audio_recorder.get_channel_recordings(channel_id=channel_id, limit=limit, offset=offset, start_date=start_date, end_date=end_date)
        return jsonify(recordings)

    @app.route("/api/recording/<path:filename>")
    def download_recording(filename):
        """Download a specific recording file with proper headers for audio streaming"""
        try:
            # Check if this is a concatenated file (stored directly in audio_files)
            if filename.startswith("concatenated_"):
                file_path = os.path.join(BASE_DIR, "audio_files", filename)
            else:
                # Extract channel from filename pattern: YYYYMMDD_HHMMSS_MS_CHANNEL.ext
                parts = filename.split("_")
                if len(parts) >= 4:
                    # Channel name starts from the 4th part (index 3)
                    # Remove both .mp3 and .flac extensions
                    channel_id = "_".join(parts[3:]).replace(".mp3", "").replace(".flac", "")
                    file_path = os.path.join(BASE_DIR, "audio_files", channel_id, filename)
                else:
                    return jsonify({"error": "Invalid filename format"}), 400
            if os.path.exists(file_path):
                # Determine MIME type based on file extension
                if filename.lower().endswith(".flac"):
                    mimetype = "audio/flac"
                elif filename.lower().endswith(".mp3"):
                    mimetype = "audio/mpeg"
                else:
                    mimetype = "audio/mpeg"  # Default fallback
                # Add headers for better audio streaming support
                response = send_file(file_path, mimetype=mimetype, as_attachment=False, download_name=filename)
                response.headers["Accept-Ranges"] = "bytes"
                response.headers["Cache-Control"] = "public, max-age=3600"
                response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
                return response
            return jsonify({"error": "File not found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/concatenate", methods=["POST"])
    def concatenate_recordings():
        """Concatenate multiple recording files into a single file"""
        try:
            data = request.get_json()
            if data is None:
                return jsonify({"error": "No JSON data received"}), 400
            files = data.get("files", [])
            channel_name = data.get("channel_name", "mixed").replace(" ", "_")
            if not files:
                return jsonify({"error": "No files specified"}), 400
            # Import required modules
            import subprocess
            import tempfile
            from datetime import datetime, timezone
            import shutil

            # Create temporary directory for processing
            with tempfile.TemporaryDirectory() as temp_dir:
                # Collect full file paths and validate they exist
                input_files = []
                for filename in files:
                    # Check if filename includes directory path (like "25_-_San_Mateo/file.flac")
                    if "/" in filename:
                        # Use the path as provided
                        file_path = os.path.join(BASE_DIR, "audio_files", filename)
                    else:
                        # Extract channel from filename pattern for backwards compatibility
                        parts = filename.split("_")
                        if len(parts) >= 4:
                            channel_id = "_".join(parts[3:]).replace(".mp3", "").replace(".flac", "")
                            file_path = os.path.join(BASE_DIR, "audio_files", channel_id, filename)
                        else:
                            return jsonify({"error": f"Invalid filename format: {filename}"}), 400
                    if os.path.exists(file_path):
                        input_files.append(file_path)
                    else:
                        return jsonify({"error": f"File not found: {filename}"}), 404
                if not input_files:
                    return jsonify({"error": "No valid files found"}), 404
                # Generate output filename with timestamp
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                output_filename = f"concatenated_{timestamp}.flac"
                output_path = os.path.join(temp_dir, output_filename)
                # Create ffmpeg command for concatenation using concat filter
                # This approach is more reliable with FLAC files
                ffmpeg_command = ["ffmpeg"]
                # Add input files
                for file_path in input_files:
                    ffmpeg_command.extend(["-i", os.path.abspath(file_path)])
                # Build filter complex for concatenation
                num_files = len(input_files)
                filter_inputs = "".join(f"[{i}:0]" for i in range(num_files))
                filter_complex = f"{filter_inputs}concat=n={num_files}:v=0:a=1[out]"
                ffmpeg_command.extend(["-filter_complex", filter_complex, "-map", "[out]", "-y", output_path]) # Overwrite output file
                result = subprocess.run(ffmpeg_command, capture_output=True, text=True, timeout=300) # 5-minute timeout
                if result.returncode != 0:
                    return jsonify({"error": f"FFmpeg failed: {result.stderr}"}), 500
                # Check if output file was created
                if not os.path.exists(output_path):
                    return jsonify({"error": "Concatenation failed - output file not created"}), 500
                # Move the concatenated file to the audio_files directory
                final_output_path = os.path.join(BASE_DIR, "audio_files", output_filename)
                shutil.move(output_path, final_output_path)
                # Return JSON response with download URL
                download_url = f"/api/recording/{output_filename}"
                return jsonify({"success": True, "download_url": download_url, "filename": output_filename, "files_concatenated": len(input_files), "file_list": [os.path.basename(f) for f in input_files]})
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Concatenation timed out"}), 504
        except Exception as e:
            return jsonify({"error": f"Concatenation failed: {str(e)}"}), 500

    @app.route("/api/stats")
    def get_statistics():
        """Get recording statistics"""
        cached_stats = _STATS_CACHE
        if not cached_stats:
            return jsonify({"error": "Stats warming up"}), 503
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        start_time = None
        end_time = None
        if start_date or end_date:
            try:
                if not (start_date and end_date):
                    raise ValueError("Both day boundaries are required")
                start_time = parse_utc_datetime(start_date).timestamp()
                end_time = parse_utc_datetime(end_date).timestamp()
                if end_time <= start_time:
                    raise ValueError("End must be after start")
            except (ValueError, OverflowError):
                return jsonify({"error": "Provide valid start_date and end_date boundaries"}), 400

        # Copy public values so different browser timezones never alter the shared cache.
        stats = {}
        today_recordings = 0
        for channel_id, values in cached_stats.items():
            if channel_id == "total":
                continue
            channel_stats = {key: value for key, value in values.items() if key != "_recording_times"}
            if start_time is not None:
                times = values["_recording_times"]
                # The next local midnight is exclusive, including on 23/25-hour DST days.
                channel_stats["today"] = bisect_left(times, end_time) - bisect_left(times, start_time)
            today_recordings += channel_stats["today"]
            stats[channel_id] = channel_stats
        stats["total"] = {**cached_stats["total"], "today": today_recordings}
        return jsonify(stats)

    @app.route("/api/channel-minutes")
    def get_channel_minutes():
        """Get total recording minutes for each channel from metadata files"""
        if not _CHANMINS_CACHE:
            return jsonify({"error": "Channel minutes warming up"}), 503
        return jsonify(_CHANMINS_CACHE)

    @app.route("/api/cleanup-temp", methods=["POST"])
    def cleanup_temp_files():
        """Clean up temporary audio files"""
        try:
            data = request.get_json() or {}
            max_age_hours = data.get("max_age_hours", 24)
            force_all = data.get("force_all", False)
            if force_all:
                # Force cleanup all temp files regardless of age
                removed_count = audio_recorder.cleanup_temp_files(max_age_hours=0)
            else:
                removed_count = audio_recorder.cleanup_temp_files(max_age_hours)
            return jsonify({"status": "success", "removed_count": removed_count, "message": f"Removed {removed_count} temporary files"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/cleanup-status")
    def cleanup_status():
        """Get status of temp files"""
        try:
            import glob
            from datetime import datetime, timedelta, timezone
            # Count temp files by age (both mp3 and flac)
            temp_pattern_mp3 = os.path.join(BASE_DIR, "audio_files", "*", "temp_*.mp3")
            temp_pattern_flac = os.path.join(BASE_DIR, "audio_files", "*", "temp_*.flac")
            temp_files = glob.glob(temp_pattern_mp3) + glob.glob(temp_pattern_flac)
            now = datetime.now(timezone.utc)
            counts = {"total": len(temp_files), "less_than_1_hour": 0, "less_than_24_hours": 0, "older_than_24_hours": 0, "orphaned": 0}
            for temp_file in temp_files:
                try:
                    file_time = datetime.fromtimestamp(os.path.getmtime(temp_file), tz=timezone.utc)
                    age = now - file_time
                    if age < timedelta(hours=1):
                        counts["less_than_1_hour"] += 1
                    elif age < timedelta(hours=24):
                        counts["less_than_24_hours"] += 1
                    else:
                        counts["older_than_24_hours"] += 1
                    # Check if orphaned (final file exists)
                    filename = os.path.basename(temp_file)
                    if filename.startswith("temp_"):
                        final_filename = filename[5:]  # Remove 'temp_' prefix
                        channel_dir = os.path.dirname(temp_file)
                        final_file = os.path.join(channel_dir, final_filename)
                        if os.path.exists(final_file):
                            counts["orphaned"] += 1
                except Exception:
                    pass  # Skip files with errors
            return jsonify({"status": "success", "temp_file_counts": counts, "cleanup_recommendations": {"immediate_cleanup": counts["older_than_24_hours"], "orphaned_cleanup": counts["orphaned"]}})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/test-connectivity")
    def test_connectivity():
        """Test connectivity to channels"""
        try:
            channel_id = request.args.get("channel")
            results = audio_recorder.test_channel_connectivity(channel_id)
            return jsonify({"status": "success", "results": results})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(debug=True, host="0.0.0.0", port=port)
