#!/usr/bin/env python3
"""
VAD Performance Monitor
Monitors recording behavior with per-channel VAD settings
"""

import json
import os
import time
from datetime import datetime, timezone
import subprocess

def load_channels():
    """Load channel configuration"""
    with open('radio_channels.json', 'r') as f:
        data = json.load(f)
    return data['channels']

def get_recent_recordings(minutes=30):
    """Get recordings from the last N minutes"""
    current_time = time.time()
    cutoff_time = current_time - minutes * 60
    
    recordings = {}
    
    for channel_dir in os.listdir('audio_files'):
        if not os.path.isdir(f'audio_files/{channel_dir}'):
            continue
            
        channel_recordings = []
        channel_path = f'audio_files/{channel_dir}'
        
        for file in os.listdir(channel_path):
            if not file.endswith('.flac'):
                continue
                
            # File times are absolute; legacy filenames may use server-local time.
            try:
                file_path = os.path.join(channel_path, file)
                modified_time = os.path.getmtime(file_path)
                if cutoff_time <= modified_time <= current_time:
                    timestamp_part = datetime.fromtimestamp(modified_time, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
                    
                    # Get file duration
                    try:
                        result = subprocess.run([
                            'ffprobe', '-v', 'quiet', '-show_entries', 
                            'format=duration', '-of', 'csv=p=0', file_path
                        ], capture_output=True, text=True)
                        
                        duration = float(result.stdout.strip()) if result.stdout.strip() else 0
                        
                        channel_recordings.append({
                            'file': file,
                            'duration': duration,
                            'timestamp': timestamp_part
                        })
                    except:
                        pass
            except:
                pass
        
        if channel_recordings:
            recordings[channel_dir] = channel_recordings
    
    return recordings

def analyze_vad_performance():
    """Analyze VAD performance across channels"""
    channels = load_channels()
    recent_recordings = get_recent_recordings(30)  # Last 30 minutes
    
    print("=== VAD Performance Analysis (Last 30 Minutes) ===\n")
    
    # Create channel mapping
    channel_map = {}
    for channel in channels:
        name = channel['name']
        # Create directory name from channel name
        dir_name = name.replace(' ', '_').replace('-', '_')
        channel_map[dir_name] = channel
    
    vad_channels = []
    default_channels = []
    
    for channel in channels:
        if 'vad' in channel:
            vad_channels.append(channel)
        else:
            default_channels.append(channel)
    
    print(f"Channels with Custom VAD ({len(vad_channels)}):")
    print("=" * 50)
    
    for channel in vad_channels:
        name = channel['name']
        vad = channel['vad']
        
        # Find recordings for this channel
        dir_name = None
        recordings = []
        
        for dir_key, recording_list in recent_recordings.items():
            if name.replace(' ', '_').replace('-', '_') in dir_key or \
               name.replace(' - ', '_') in dir_key.replace('_-_', '_'):
                recordings = recording_list
                dir_name = dir_key
                break
        
        print(f"\n📊 {name}")
        print(f"   VAD Settings:")
        print(f"      Energy Threshold: {vad['energy_threshold']:.2e}")
        print(f"      ZCR Range: {vad['zcr_min']:.2f} - {vad['zcr_max']:.2f}")
        print(f"      Min Transmission: {vad['min_transmission_ms']}ms")
        print(f"      Speech Frames: {vad['speech_frames_to_start']}")
        
        if recordings:
            count = len(recordings)
            avg_duration = sum(r['duration'] for r in recordings) / count
            short_files = sum(1 for r in recordings if r['duration'] < 2.0)
            long_files = sum(1 for r in recordings if r['duration'] > 10.0)
            
            print(f"   Recent Activity:")
            print(f"      Total recordings: {count}")
            print(f"      Average duration: {avg_duration:.1f}s")
            print(f"      Short files (<2s): {short_files}")
            print(f"      Long files (>10s): {long_files}")
            
            # Quality score based on desired characteristics
            quality_score = 100
            if short_files > count * 0.2:  # More than 20% short files
                quality_score -= 30
            if avg_duration < 3.0:  # Very short average
                quality_score -= 20
            if count == 0:  # No activity
                quality_score -= 50
            elif count > 20:  # Too much activity
                quality_score -= 15
                
            print(f"      Quality Score: {max(0, quality_score)}/100")
        else:
            print(f"   Recent Activity: No recordings in last 30 minutes")
    
    print(f"\n\nChannels with Default VAD ({len(default_channels)}):")
    print("=" * 50)
    
    # Show a few examples of default channels
    for channel in default_channels[:5]:  # Show first 5
        name = channel['name']
        
        # Find recordings for this channel
        recordings = []
        for dir_key, recording_list in recent_recordings.items():
            if name.replace(' ', '_').replace('-', '_') in dir_key or \
               name.replace(' - ', '_') in dir_key.replace('_-_', '_'):
                recordings = recording_list
                break
        
        print(f"\n📊 {name}")
        print(f"   VAD Settings: Default (energy: 8e-6, zcr: 0.08-0.32, min: 2000ms)")
        
        if recordings:
            count = len(recordings)
            avg_duration = sum(r['duration'] for r in recordings) / count
            short_files = sum(1 for r in recordings if r['duration'] < 2.0)
            
            print(f"   Recent Activity:")
            print(f"      Total recordings: {count}")
            print(f"      Average duration: {avg_duration:.1f}s")
            print(f"      Short files (<2s): {short_files}")
        else:
            print(f"   Recent Activity: No recordings")
    
    if len(default_channels) > 5:
        print(f"\n   ... and {len(default_channels) - 5} more channels using defaults")
    
    # Overall statistics
    total_recordings = sum(len(recordings) for recordings in recent_recordings.values())
    all_durations = []
    for recordings in recent_recordings.values():
        all_durations.extend([r['duration'] for r in recordings])
    
    if all_durations:
        avg_duration = sum(all_durations) / len(all_durations)
        short_count = sum(1 for d in all_durations if d < 2.0)
        print(f"\n=== Overall Statistics ===")
        print(f"Total recordings (30 min): {total_recordings}")
        print(f"Average duration: {avg_duration:.1f}s")
        print(f"Short files (<2s): {short_count} ({short_count/len(all_durations)*100:.1f}%)")
        print(f"Active channels: {len(recent_recordings)}")

if __name__ == '__main__':
    analyze_vad_performance()
