# NTV Forge 1.0 — Release Notes

This is the first public-facing release candidate combining Link Forge and Channel Forge in one local web application.

## Streaming and Link Forge
- Source support: YouTube videos/playlists, Twitch, and supported direct streams.
- Automatic availability checks and source status indicators.
- M3U playlist generation over LAN, optional basic XMLTV, JSON backups.
- Removed forced 720p/30 fps processing; preserves source resolution/frame rate when supported. User-tested with 1080p and 4K streams.
- Removed unverified channel-logo URL/upload functionality.
- Quick Sync hardware encoding and software fallback retained.

## Channel Forge
- Plex/Jellyfin/Emby connections and library scanning.
- Existing channel CSV import, editing, title matching, and export.
- Connection tokens masked; optional browser-based persistence and clear control.

## Interface
- Cassette-futurist design, amber workspace navigation, ivory keys, recessed CRT-style panels.
- Button progress/success/failure lighting where operations return results.
- Better button wrapping, accessible Remove actions, and visible local server-test feedback.

## Windows launch and distribution
- New optional **silent Python launcher** with a Windows notification-area tray menu to open the app, copy the playlist URL, and stop the server.
- Diagnostic BAT retained.
- Windows-only PyInstaller build script supplied; **this archive does not include a prebuilt Windows EXE**.
- Expanded README is a complete end-user guide.

## Known limitations
- Windows EXE must be built on Windows and tested there before redistribution.
- Streaming utilities are separate prerequisites; higher-resolution encoding may increase CPU/GPU load.
- The server is for trusted LAN use, not direct internet exposure.

### Final Windows packaging updates
- Fixed the tray dependency installer to prefer `python -m pip` (matching the working Windows launcher) with a `py -3` fallback, and show a clear success/failure message.
- Corrected PyInstaller runtime asset paths for the packaged web interface and Channel Forge assets.
- Updated the Windows EXE build script to bundle application code/UI while leaving the writable `project.json` alongside the executable.
- Clarified that Windows EXE builds must be compiled and tested on Windows and that streaming tools remain separate prerequisites.

### Windows packaging maintenance update
- Fixed PyInstaller `--windowed` HTTP request logging, which could produce empty HTTP replies without a console.
- Added optional portable `tools` folder lookup and build-time copying for ffmpeg, yt-dlp, and standalone Streamlink.
- Added file logging to `ntv-forge.log` for diagnosing background failures.
- Source package does not include third-party streaming executables.

## Portable-tool packaging update
- Added one-click download of Windows portable FFmpeg, yt-dlp and Streamlink.
- Preserve complete Streamlink portable runtime, not just its EXE.
- Builder copies the complete tool directory; checks required tools before success.
- Source ZIP does not include downloaded third-party binaries.
