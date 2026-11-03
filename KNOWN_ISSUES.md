# Known issues — v1.3.2c beta

- YouTube may request verification and trigger a temporary 30-minute pause; this is a source/platform restriction, not necessarily a local network fault.
- Enabling subtitles partway through a YouTube video may produce 10–20 seconds of delay; captions may disappear on loops. This release retains the 1.2.4c subtitle baseline.
- Twitch playback reliability varies with the stream and environment. Please attach sanitized diagnostic reports to reproducible bug reports.
- Archive download progress is based on completed video count, not byte-level transfer percentage.
- Some NAS mapped drives are accessible only under the Windows account that mapped them; UNC paths may work better for other accounts.
- Windows packaged build and all external-provider scenarios require testing on the target machine.
