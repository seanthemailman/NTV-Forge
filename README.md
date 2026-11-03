# NTV Forge — v1.3.2c Beta

**Link Forge • Channel Forge • Archive Forge** — a retro-styled local companion for NostalgiaTV.

**Getting started:** See [GITHUB_RELEASE.md](GITHUB_RELEASE.md) for current setup and release packaging, [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for beta limitations, and [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) before sharing diagnostics or exposing the server to other machines.

> The historical user guide below predates Archive Forge. Use the release notes above for current version details.

---

# NTV Forge 1.0 — Windows User Guide

NTV Forge is a local companion for **NostalgiaTV**, combining **Link Forge** (M3U streaming channels) and **Channel Forge** (editing `channels.csv` using Plex/Jellyfin/Emby libraries). The two workspaces use different data formats; an M3U playlist is not a CSV file.

## Quick start: choose a launcher

**Option A — No console, system tray (recommended for Python installs):**
1. Install **Python 3 for Windows** with the `python` command on PATH (recommended), or the `py` launcher.
2. Run `Install Tray Dependencies.bat` once to install `pystray` and `Pillow`.
3. Double-click `Launch NTV Forge.vbs`. The app opens in your browser at `http://localhost:8765`, with **no command window**.
4. Closing the browser does **not** stop the streaming server. Find **NTV Forge** in the Windows notification area (possibly under the ^ arrow). Right-click its icon for **Open NTV Forge**, **Copy M3U Playlist URL**, or **Exit NTV Forge**. **Exit** stops the server.

**Option B — Diagnostic console:** Double-click `Launch NTV Forge.bat` to see server errors. Keep the window open; press **Ctrl+C** to stop. Do not run both launchers at once.

**Option C — Windows executable (build on Windows):** On a **Windows** computer, run `Build Windows EXE.bat` (requires Python and internet access for PyInstaller). Launch `dist\NTV Forge\NTV Forge.exe`. Distribute the **entire `dist\NTV Forge` folder**, not only the `.exe`. This is a build script, **not a precompiled EXE**; the ZIP contains source code and launchers. Keep the EXE folder writable for `project.json`. The executable bundles the Python runtime and tray dependencies, but it is not a standalone single file. The Windows EXE still needs FFmpeg/yt-dlp/Streamlink installed for relevant streaming sources.

> **Streaming prerequisites:** FFmpeg, yt-dlp, and Streamlink must be installed and accessible via PATH for applicable sources. Check with `ffmpeg -version`, `yt-dlp --version`, `streamlink --version`. `python -m pip install -U yt-dlp streamlink` installs the latter two. FFmpeg is installed separately. Intel Quick Sync is used when supported; software fallback may consume more CPU.

## Link Forge — create and publish streaming channels

1. Open **01 / LINK FORGE**. Under **Source**, name a source and paste a supported YouTube video/playlist, Twitch channel, or direct HLS/MP4/TS URL. Save the source. Source status is checked automatically; **Offline** can mean the broadcaster isn't live; **Unverified** means the check was inconclusive.
2. Under **Channel**, create a channel name and assign one of the saved sources. Repeat for each channel you want to publish.
3. In **Review**, confirm your channel/source assignments and exported-channel count.
4. In **Connect to NostalgiaTV**, choose the host PC's reachable **LAN IP** and copy the **M3U playlist URL** (`http://YOUR-LAN-IP:8765/channels.m3u`). Add it in NostalgiaTV under its M3U/HDHR source configuration. Use the LAN IP, not `localhost`, when NTV runs on another machine or in Docker. Optional XMLTV includes channel definitions, not programme schedules.
5. Leave NTV Forge running whenever NTV needs these streams. **Test Local Server** tests playlist availability, not end-to-end playback.
6. Use **Download Project Backup** to export a JSON backup, and **Restore JSON Backup** to import it. The live Link Forge project is also saved in `project.json` beside the application.

**Resolution:** Link Forge no longer forces 720p or 30 fps. It attempts to preserve source resolution and frame rate (1080p and 4K have been user-tested). Playback can still involve HLS conversion/transcoding and may demand more CPU/GPU resources. Supported resolution depends on your hardware and the source.

**Logos:** Logo URL and local logo uploads were intentionally removed because logo display was not confirmed in NostalgiaTV. M3U entries do not include logo URLs.

## Channel Forge — edit NostalgiaTV channel CSVs

1. Open **02 / CHANNEL FORGE**. Click **Browse CSV** and load your existing NostalgiaTV `channels.csv`.
2. Choose **Plex**, **Jellyfin**, or **Emby**. Enter your media server's full URL (including protocol and port) and API token/key. Click **Test Connection**, then **Scan Libraries**. A green illuminated result means the operation succeeded; red indicates failure; amber indicates work in progress.
3. In **Choose an Existing Channel**, search for and select the channel you want to edit.
4. In **Current Channel Contents**, review titles already assigned. Unmatched titles remain visible; they are not automatically deleted. Use **Remove** or clear a selected channel deliberately.
5. In **Find and Assign Titles**, search scanned library titles and use **Add Selected Titles** to append selections. **Replace All Channel Content** replaces the current assignments; use with care.
6. In **Review & Export**, export a **new timestamped CSV** and import it into NostalgiaTV using its CSV import workflow. The original input CSV is not overwritten. Export before closing your session. Save a project JSON backup when desired.

**Connection privacy:** API tokens are masked. Browser **Remember Connection** is optional and stores credentials locally in that browser without encryption. **Clear Saved Connection** removes NTV Forge's saved browser connection; your browser's own autofill may be independent. Do not share exported backups containing secrets. Fresh Chrome/Firefox profiles should not inherit each other's browser-saved tokens.

## Appearance and controls

The **cassette-futurist** interface uses amber workspace tabs, ivory illuminated action keys, dark CRT-inspired bezels, and functional readouts. Buttons indicate **amber while processing, green on success, and red on failure** when the operation reports a result. Indicators are not simulated metrics. Effects are lightweight CSS rather than animated videos.

## Troubleshooting

- **Port 8765 already in use:** Exit an older NTV Forge/Link Forge process from its tray icon or console; only run one server at a time.
- **Tray launcher does nothing:** Run `python tray_launcher.py` from a Command Prompt opened in the extracted folder to reveal missing dependencies. Run `Install Tray Dependencies.bat` again if needed. The installer now uses the same `python` interpreter preferred by the launcher.
- **Tray icon hidden:** Expand the Windows notification area (^). If nothing launches, run the diagnostic BAT and inspect errors or `ntv-forge.log`.
- **Stream won't play:** Verify FFmpeg/yt-dlp/Streamlink installation, the source's availability, and Windows Firewall/LAN connectivity. Direct links may expire. NTV Forge must remain running.
- **NTV can't load M3U:** Open the LAN playlist URL from a second device; ensure NTV uses the PC LAN IP rather than `localhost`.
- **High CPU at 4K:** Higher resolutions can require substantial transcoding resources; hardware acceleration availability varies.
- **Media library won't connect:** Check server URL, port, API token, firewall, and server accessibility. Test connection before scanning.
- **Missing changes in Channel Forge:** Export the CSV before closing; its editing session is not the same as Link Forge's server-saved `project.json`.
- **Browser shows old layout:** Force refresh (Ctrl+F5) after changing versions.

## Safety and sharing

Run on trusted local networks only. The server binds to all network interfaces on port 8765 and has **no authentication**. Do not forward this port to the public internet. Remove personal tokens and backups before sharing a customized installation folder. This release ZIP ships with an empty `project.json`.

## What's included

`tray_launcher.py` (Windows tray controller), `Launch NTV Forge.vbs` (silent launch), `Launch NTV Forge.bat` (diagnostics), `Build Windows EXE.bat` (optional Windows-only build), `Install Tray Dependencies.bat`, `server.py`, `index.html`, `suite.html`, `channel_forge/`, `project.json`, `README.md`, and `RELEASE_NOTES.md`.

## Verified release testing and packaging limitations

The NTV Forge browser UI, Link Forge and Channel Forge, tray launch/exit, and 1080p/4K streaming were tested by the project tester on Windows. The improved dependency installer and PyInstaller build script in this package are **new** and need Windows testing. **No precompiled Windows EXE is included**; run `Build Windows EXE.bat` on a Windows PC to create one. For the EXE distribution, test the generated executable before sharing the `dist\NTV Forge` folder.

**Moving to a new release:** Back up your Link Forge project JSON and export your Channel Forge CSV before replacing the app folder. The included `project.json` is empty; do not overwrite your existing saved project with it.
