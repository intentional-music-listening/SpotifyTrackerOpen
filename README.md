# IML SpotifyTrackerOpen 🎧

A Python script to log Spotify playback activity for research purposes.  
This script was developed as part of the Intentional Music Listening project to analyze participants’ music listening behavior.

## Features

- Detects when the Spotify app is opened or closed
- Logs:
  - Track name, artist, and album
  - Start and end times
  - Track listening duration and total session duration
  - Pauses, skips, and seeks
- Automatically saves a session log as a CSV file when:
  - The Spotify app closes
  - Playback is paused for more than 5 minutes

## Example Workflow

1. Start the script for a specific participant:
   ```bash
   python3 main.py <patient_id> <input_file.json> <output_folder>
   ```

2. While Spotify is active:
   - The script prints playback activity to the console
   - Logs playback continuously
   - Pauses, skips, and seeks are recorded automatically

3. When the session ends:
   - A CSV log named `session_log_[month]-[day]_[hour]-[minutes].csv` is saved under `logs/[patient_id]`.
     
4. Example Output:
   - See `SAMPLE_session_log_07-16_12h-34m.csv` for an example of the log format.

## Repository Contents

- `main.py` — Main script to track Spotify playback  
- `patient_ids.json` — Example patient ID mapping 
- `SAMPLE_session_log_*.csv` — Example session log output
- `README.md` — Project description and usage

## Notes

- This repository is provided as a reference
- API credentials, cache files, and HPC batch scripts are excluded for security
- The script requires Spotify API credentials and setup to run
