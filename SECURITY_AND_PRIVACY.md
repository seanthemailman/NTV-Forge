# Security and privacy

Forge is intended for trusted local networks. Do not expose port 8765 directly to the public Internet. File browsing and archive writes operate on paths accessible to the Forge host account. Restrict network access to trusted clients and do not treat the Konami-code Archive unlock as an authentication mechanism.

Do not commit `project.json`, media-server tokens, browser cookies, private NAS paths, diagnostic archives, or full request URLs that contain credentials. Review logs before sharing them with maintainers. The repository includes only `project.example.json` as a safe starter.

For issue reports: provide version, Windows version, source type, steps to reproduce, expected/actual results, and a redacted diagnostic ZIP. Remove IP addresses, user names, paths, tokens, and signed URLs if present.
