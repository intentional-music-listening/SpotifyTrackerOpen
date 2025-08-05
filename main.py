import sys
import requests
import smtplib
import subprocess
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import shutil
import numpy as np
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import time
import json
import csv
import pandas as pd
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from IPython.display import Audio, display
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive

# --- EMAIL CONFIG ---

# Projects Gmail Account
EMAIL_ADDRESS = "-"
EMAIL_PASSWORD = "-"


# --- Tracker State ---
last_status = "nothing"
last_track_id = None
last_track_name = None
last_progress_ms = None
last_track_duration = None
current_session = None
session_log = []
total_time_per_track = {}
spotify_opened_announced = False
initial_checked = False
pause_start_time = None
session_start_time = None



# --- Helper functions ---
def now():
    return datetime.now()

def seconds_between(start, end):
    return (end - start).total_seconds()

def format_time(seconds):
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    millis = int((seconds - int(seconds)) * 1000)
    return f"{minutes}:{secs:02d}.{millis:03d}"

def format_pretty_date(dt_str):
    dt = datetime.fromisoformat(dt_str)
    return dt.strftime("%H:%M - %m/%d/%Y")

def send_email(subject, body):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = EMAIL_ADDRESS
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    server.send_message(msg)
    server.quit()

def check_patient_id(patient_id, json_path):
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            patients = json.load(f)
        
        if patient_id in patients:
            return patients[patient_id]
        else:
            print(f"❌ Patient ID '{patient_id}' not found.")
            return None
    
    except FileNotFoundError:
        print(f"❌ JSON file not found: {json_path}")
    except json.JSONDecodeError:
        print("❌ Invalid JSON format.")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    
    return None
        
def finalize_session(patient_id, output_folder, session_start_time, reason):
    global current_session
    if current_session:
        current_session["end"] = now().isoformat()
        current_session["track_listening_duration"] += seconds_between(current_session["start_time"], now())

        tid = current_session["track_id"]
        if tid not in total_time_per_track:
            total_time_per_track[tid] = 0.0
        total_time_per_track[tid] += current_session["track_listening_duration"]
        current_session["total_track_listening_duration"] = total_time_per_track[tid]

        current_session["end_reason"] = reason
        current_session["session_start_time"] = session_start_time
        session_log.append(current_session)
        
        current_session = None  

    # Always save if pause or spotify_closed
    if reason in ["long_pause", "spotify_closed", "paused_and_closed"]:
        if session_log and session_log[-1]["end_reason"] == "paused":
            session_log[-1]["end_reason"] = reason
        save(patient_id, output_folder, session_start_time)
    
def save(patient_id, output_folder, session_start_time):
    dir_name_date = now().strftime("%m-%d")
    dir = f"{output_folder}/{patient_id}"
    os.makedirs(dir, exist_ok=True)  # Make folder if it doesn't exist

    # --- Save JSON ---
    json_friendly_log = [
        {k: v for k, v in entry.items() if k != "start_time"} for entry in session_log
    ]

    # --- Save CSV ---
    file_path = f"{dir}/session_log_{dir_name_date}_{session_start_time}.csv"
    
    with open(file_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "patient_id", "session_start_time", "artist_name", "album_name", "track_name", 
            "total_track_duration", "start_position_seconds", "start_date", "end_date",
            "track_listening_duration", "total_track_listening_duration",
            "end_reason", "device_name"
        ])
        writer.writeheader()
        for entry in json_friendly_log:
            writer.writerow({
                "patient_id": patient_id,
                "session_start_time": entry.get("session_start_time", session_start_time),
                "artist_name": entry["artist_name"],
                "album_name": entry["album_name"],
                "track_name": entry["track_name"],
                "total_track_duration": format_time(entry["track_duration"]),
                "start_position_seconds": format_time(entry["start_position_seconds"]),
                "start_date": format_pretty_date(entry["start"]),
                "end_date": format_pretty_date(entry["end"]),
                "track_listening_duration": format_time(entry["track_listening_duration"]),
                "total_track_listening_duration": format_time(entry["total_track_listening_duration"]),
                "end_reason": entry["end_reason"],
                "device_name": entry["device_name"]
            })

    print(f"✅ Logs saved to {file_path}")
    upload_to_drive(file_path, patient_id)

    # --- Reset after saving ---
    session_log.clear()
    total_time_per_track.clear()
    global current_session
    current_session = None

def upload_to_drive(file_path, patient_id):
    gauth = GoogleAuth()
    gauth.LoadCredentialsFile("mycreds.txt")

    if gauth.credentials is None:
        print("❌ No credentials found. Authenticate on your local machine first.")
        return
    elif gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()

    gauth.SaveCredentialsFile("mycreds.txt")
    drive = GoogleDrive(gauth)

    file_name = os.path.basename(file_path)

    # Build folder path in Drive: logs → patient_id
    root_folder_id = get_or_create_drive_folder(drive, "logs")
    patient_folder_id = get_or_create_drive_folder(drive, patient_id, parent_id=root_folder_id)

    # Upload directly into patient folder
    gfile = drive.CreateFile({
        'title': file_name,
        'parents': [{'id': patient_folder_id}]
    })
    gfile.SetContentFile(file_path)
    gfile.Upload()
    print(f"☁️ Uploaded to Google Drive: {file_name} → logs/{patient_id}\n")

def get_or_create_drive_folder(drive, folder_name, parent_id=None):
    query = f"title = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    file_list = drive.ListFile({'q': query}).GetList()

    if file_list:
        return file_list[0]['id']  # Folder already exists
    else:
        folder_metadata = {
            'title': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            folder_metadata['parents'] = [{'id': parent_id}]
        folder = drive.CreateFile(folder_metadata)
        folder.Upload()
        return folder['id']



def main(patient_id, input_json_file="patient_ids.json", output_folder="logs"):
    global session_start_time
    global last_status, last_track_id, last_track_name, last_progress_ms, last_track_duration
    global current_session, session_log, total_time_per_track
    global spotify_opened_announced, initial_checked, pause_start_time

    patient_info = check_patient_id(patient_id, input_json_file)
    if not patient_info:
        return 404

    # --- Spotify OAuth Setup ---
    os.environ['SPOTIPY_CLIENT_ID'] = patient_info["SPOTIPY_CLIENT_ID"]
    os.environ['SPOTIPY_CLIENT_SECRET'] = patient_info["SPOTIPY_CLIENT_SECRET"]
    os.environ['SPOTIPY_REDIRECT_URI'] = patient_info["SPOTIPY_REDIRECT_URI"]
    
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        scope="user-read-playback-state user-read-currently-playing",
        cache_path=f"/home/mk9649/iml/SpotifyTracker/.cache-{patient_id}"
    ))

    try:
        while True:
            
            playback = None
            try:
                playback = sp.current_playback()
            except Exception as e:
                print(f"⚠️ Spotify playback request failed: {e}")
                
            try:
                devices_info = sp.devices()
                active_devices = devices_info.get('devices', [])
                
                if active_devices:
                    if not spotify_opened_announced:
                        session_time_start = datetime.now()
                        session_start_time = now().strftime("%Hh-%Mm")
                        print("🟢 Spotify app opened.")
                        send_email("Spotify Opened", f"🎧 {patient_id} started a spotify session at {session_start_time} EDT. 🎧")
                        spotify_opened_announced = True
                    if playback and playback.get('is_playing') and session_start_time is None:
                        if spotify_opened_announced:
                            session_start_time = now().strftime("%Hh-%Mm")
                            print("🟢 Spotify app reopened after a long pause.")
                            send_email("Spotify Reopened", f"🎧 {patient_id} continues a spotify session at {session_start_time} EDT. 🎧")
                elif playback == None and not active_devices:
                    if spotify_opened_announced:
                        session_time_end = datetime.now()
                        session_end_time = now().strftime("%Hh-%Mm")
                        
                        # Calculate listening duration
                        listening_duration = session_time_end - session_time_start
                        minutes = int(listening_duration.total_seconds() // 60)
                        seconds = int(listening_duration.total_seconds() % 60)
                        
                        print("🔴 Spotify app closed.")
                        send_email("Spotify Closed", f"🛑 {patient_id} stopped the spotify session. Active listening time: {minutes} min {seconds} sec. 🛑")
                        spotify_opened_announced = False
            
            except Exception as e:
                print(f"⚠️ Could not check devices: {e}")
    
            if playback and playback['device']['is_active']:
                is_playing = playback['is_playing']
                repeat_mode = playback.get('repeat_state', 'off')
                device_name = playback['device']['name']
                track_info = playback.get('item')
                progress_ms = playback.get('progress_ms', 0)
                
                if track_info:
                    track_id = track_info['id']
                    track_name = track_info['name']
                    artist_name = track_info['artists'][0]['name']
                    album_name = track_info['album']['name']
                    duration_ms = track_info['duration_ms']
                    
                else:
                    track_id = None
                    track_name = None
                    artist_name = None
                    album_name = None
                    duration_ms = None
    
                previous_track_name = last_track_name
                previous_track_duration = last_track_duration
    
                if (last_progress_ms is not None and abs(progress_ms - last_progress_ms) > 5000 and track_id == last_track_id):
    
                    from_time = format_time(last_progress_ms / 1000)
                    to_time = format_time(progress_ms / 1000)
    
                    if progress_ms < 5000 and last_progress_ms > duration_ms - 5000:
                        if repeat_mode == "track":
                            finalize_session(patient_id, output_folder, session_start_time, reason="repeat")
                            print(f"\U0001F501 Track repeated: {track_name}")
                            print(f"\U0001F3B5 Now playing: {track_name} by {artist_name}")
                        else:
                            finalize_session(patient_id, output_folder, session_start_time, reason="track_end")
                            print(f"⏹️ Track ended: {previous_track_name}")
                    else:
                        if progress_ms > last_progress_ms:
                            print(f"⏩ Seeked forward to {to_time} from {from_time}")
                            reason = "seeked_forward"
                        else:
                            print(f"⏪ Seeked backward to {to_time} from {from_time}")
                            reason = "seeked_backward"
                        finalize_session(patient_id, output_folder, session_start_time, reason)
    
                    current_session = {
                        "track_id": track_id,
                        "track_name": track_name,
                        "artist_name": artist_name,
                        "album_name": album_name,
                        "device_name": device_name,
                        "track_duration": round(duration_ms / 1000.0, 2),
                        "start_position_seconds": round(progress_ms / 1000.0, 2),
                        "start_time": now(),
                        "start": now().isoformat(),
                        "end": None,
                        "track_listening_duration": 0.0,
                        "total_track_listening_duration": None,
                        "end_reason": None
                    }
    
                if last_track_id is not None and track_id != last_track_id:
                    if last_progress_ms is not None and previous_track_duration is not None:
                        played_time = last_progress_ms / 1000.0
                        margin = 2.0 if repeat_mode != "context" else 5.0
    
                        if abs(previous_track_duration - played_time) <= margin:
                            finalize_session(patient_id, output_folder, session_start_time, reason="track_end")
                            print(f"⏹️ Track ended: {previous_track_name}")
                        else:
                            finalize_session(patient_id, output_folder, session_start_time, reason="skipped")
                            print(f"⏭️ Track skipped: {previous_track_name}")
                    else:
                        finalize_session(patient_id, output_folder, session_start_time, reason="skipped")
                        print(f"⏭️ Track skipped (unknown timing): {previous_track_name}")
                
                if is_playing and (last_status != "playing" or track_id != last_track_id):
                    
                    print(f"\U0001F3B5 Now playing: {track_name} by {artist_name}")
                    
                    current_session = {
                        "track_id": track_id,
                        "track_name": track_name,
                        "artist_name": artist_name,
                        "album_name": album_name,
                        "device_name": device_name,
                        "track_duration": round(duration_ms / 1000.0, 2),
                        "start_position_seconds": round(progress_ms / 1000.0, 2),
                        "start_time": now(),
                        "start": now().isoformat(),
                        "end": None,
                        "track_listening_duration": 0.0,
                        "total_track_listening_duration": None,
                        "end_reason": None
                    }
                    last_status = "playing"
                    last_track_id = track_id
                    last_track_name = track_name
                    last_track_duration = duration_ms / 1000.0
                    pause_start_time = None
    
                elif not is_playing and last_status == "playing":
                    print("⏸️ Track paused.")
                    finalize_session(patient_id, output_folder, session_start_time, reason="paused")
                    pause_start_time = now()
                    last_status = "paused"
                    last_track_id = track_id
                    last_track_name = track_name
                    last_track_duration = duration_ms / 1000.0
    
                last_progress_ms = progress_ms
    
            else:
                # App might be closed
                if playback is None and last_status != "nothing":
                    if last_status == "paused":
                        finalize_session(patient_id, output_folder, session_start_time, reason="paused_and_closed")
                    else:
                        finalize_session(patient_id, output_folder, session_start_time, reason="spotify_closed")
                
                    last_status = "nothing"
                    last_track_id = None
                    last_track_name = None
                    last_progress_ms = None
                    last_track_duration = None
                    pause_start_time = None
            
            if last_status == "paused" and pause_start_time:
                pause_limit_time = 300
                pause_limit_time_min = pause_limit_time // 60
                if seconds_between(pause_start_time, now()) >= pause_limit_time:
                    # Finalize session
                    print(f"⏳ Paused for {pause_limit_time_min} minute, session finalized.")
                    
                    finalize_session(patient_id, output_folder, session_start_time, reason="long_pause")
                    
                    session_time_end = datetime.now()
                    session_end_time = now().strftime("%Hh_%Mm")
                    
                    # Calculate listening duration
                    listening_duration = session_time_end - session_time_start
                    minutes = int(listening_duration.total_seconds() // 60)
                    seconds = int(listening_duration.total_seconds() % 60)
                    send_email("Spotify Closed", f"🛑 {patient_id} Spotify app paused for more than {pause_limit_time_min} minutes, Spotify session stopped and log saved. Active listening time: {minutes} min {seconds} sec. 🛑")
                    
                    # Reset status
                    last_status = "nothing"
                    last_track_id = None
                    last_track_name = None
                    last_progress_ms = None
                    last_track_duration = None
                    pause_start_time = None
                    current_session = None
                    session_start_time = None 
                    
            time.sleep(2)
    
    except KeyboardInterrupt:
        print("\n✅ Tracking ended successfully. ✅\n")
        finalize_session(patient_id, output_folder, session_start_time, reason="keyboard_interrupt")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 main.py <patient_id> [input_json_file] [output_folder]")
        sys.exit(1)
    pid = sys.argv[1]
    input_json = sys.argv[2] if len(sys.argv) > 2 else "patient_ids.json" 
    out_folder = sys.argv[3] if len(sys.argv) > 3 else "logs"
    main(pid, input_json, out_folder)
