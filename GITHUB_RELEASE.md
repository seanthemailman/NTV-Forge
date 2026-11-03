# NTV Forge v1.3.2c Beta

**Link Forge · Channel Forge · Archive Forge**

A Windows-first local companion to NostalgiaTV for generating M3U streams, editing channel CSV files, and archiving YouTube playlists with metadata and thumbnails.

## What is included
- Python source, HTML/JS interface, Windows launchers, PyInstaller build script, icon assets.
- Hidden Archive Forge workspace, unlocked by the Konami sequence in the expanded system monitor. The achievement is stored in the project JSON backup.
- Archive downloads to accessible local or NAS destinations; resume/skip existing items.
- Downloadable diagnostics for troubleshooting Twitch and other sources.

## Install from source (Windows)
1. Extract the repository ZIP.
2. Install Python 3 and run `Install Tray Dependencies.bat`.
3. Run `Download Portable Tools.bat` if you need the portable streaming dependencies, or install FFmpeg, yt-dlp and Streamlink yourself.
4. Run `Launch NTV Forge.vbs` or `Launch NTV Forge.bat` for console diagnostics.
5. Open http://localhost:8765.

## Build a Windows release
1. On Windows, run `Build Windows EXE.bat`.
2. Test `dist\NTV Forge\NTV Forge.exe`.
3. ZIP the **whole** `dist\NTV Forge` folder, including `_internal` and any required external tools. Do not publish the EXE by itself.
4. Before uploading, remove any personal `project.json`, logs, cookies, or credentials from the distribution.

## Known limitations
See [KNOWN_ISSUES.md](KNOWN_ISSUES.md). This is a beta release and is not affiliated with NostalgiaTV or any streaming provider.

## Safety
See [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md). Keep the service on a trusted LAN; do not port-forward it to the public Internet.
