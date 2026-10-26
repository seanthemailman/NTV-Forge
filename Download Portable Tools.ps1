$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $root 'tools'
$stage = Join-Path $root '_download_temp'
New-Item -ItemType Directory -Force $tools, $stage | Out-Null
$ProgressPreference = 'SilentlyContinue'
function Download($url,$dest) {
  Write-Host "Downloading $url"
  Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -Headers @{'User-Agent'='NTV-Forge-Portable-Builder'}
}
try {
  if (!(Test-Path (Join-Path $tools 'ffmpeg.exe')) -or !(Test-Path (Join-Path $tools 'ffprobe.exe'))) {
  Download 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' (Join-Path $stage 'ffmpeg.zip')
  Expand-Archive (Join-Path $stage 'ffmpeg.zip') (Join-Path $stage 'ffmpeg') -Force
  $ff = Get-ChildItem (Join-Path $stage 'ffmpeg') -Filter 'ffmpeg.exe' -Recurse | Select-Object -First 1
  $fp = Get-ChildItem (Join-Path $stage 'ffmpeg') -Filter 'ffprobe.exe' -Recurse | Select-Object -First 1
  if (!$ff -or !$fp) {throw 'FFmpeg executables missing from archive'}
  Copy-Item $ff.FullName (Join-Path $tools 'ffmpeg.exe') -Force
  Copy-Item $fp.FullName (Join-Path $tools 'ffprobe.exe') -Force
  } else { Write-Host 'FFmpeg already downloaded; skipping.' }
  if (!(Test-Path (Join-Path $tools 'yt-dlp.exe'))) {
    Download 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' (Join-Path $tools 'yt-dlp.exe')
  } else { Write-Host 'yt-dlp already downloaded; skipping.' }
  # yt-dlp needs a JavaScript runtime for reliable YouTube extraction.
  # Deno is standalone on Windows; place deno.exe beside yt-dlp.exe.
  if (!(Test-Path (Join-Path $tools 'deno.exe'))) {
    $denoUrl = 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip'
    Download $denoUrl (Join-Path $stage 'deno.zip')
    $denoExtract = Join-Path $stage 'deno'
    Expand-Archive (Join-Path $stage 'deno.zip') $denoExtract -Force
    $deno = Get-ChildItem $denoExtract -Filter 'deno.exe' -Recurse | Select-Object -First 1
    if (!$deno) { throw 'deno.exe missing from Deno archive' }
    Copy-Item $deno.FullName (Join-Path $tools 'deno.exe') -Force
  } else { Write-Host 'Deno already downloaded; skipping.' }
  $release = Invoke-RestMethod 'https://api.github.com/repos/streamlink/windows-builds/releases/latest' -Headers @{'User-Agent'='NTV-Forge-Portable-Builder'}
  Write-Host ('Streamlink release assets: ' + (($release.assets | ForEach-Object { $_.name }) -join ', '))
  $asset = $release.assets | Where-Object { $_.name -match '(?i)\.zip$' -and $_.name -match '(?i)(x86_64|amd64|win64)' -and $_.name -notmatch '(?i)(arm64|aarch64|source)' } | Select-Object -First 1
  if (!$asset) {throw 'No x86_64 Streamlink ZIP asset found. See asset names printed above.'}
  Download $asset.browser_download_url (Join-Path $stage 'streamlink.zip')
  $extract = Join-Path $stage 'streamlink'
  Expand-Archive (Join-Path $stage 'streamlink.zip') $extract -Force
  $sl = Get-ChildItem $extract -Filter 'streamlink.exe' -Recurse | Where-Object { $_.Directory.Name -eq 'bin' } | Select-Object -First 1
  if (!$sl) {throw 'streamlink.exe was not found in a bin folder; archive layout may have changed'}
  $portableRoot = $sl.Directory.Parent.FullName
  $dest = Join-Path $tools 'streamlink-portable'
  if (Test-Path $dest) {Remove-Item $dest -Recurse -Force}
  Copy-Item $portableRoot $dest -Recurse -Force
  $notices = Join-Path $tools 'THIRD_PARTY'
  New-Item -ItemType Directory -Force $notices | Out-Null
  Get-ChildItem (Join-Path $stage 'ffmpeg') -Recurse -File | Where-Object {$_.Name -match '(?i)(license|readme|copying)'} | ForEach-Object { Copy-Item $_.FullName (Join-Path $notices ('ffmpeg-'+$_.Name)) -Force }
  Get-ChildItem $extract -Recurse -File | Where-Object {$_.Name -match '(?i)^(license|copying)(\..*)?$'} | Select-Object -First 20 | ForEach-Object { Copy-Item $_.FullName (Join-Path $notices ('streamlink-'+$_.Name)) -Force }
  Write-Host 'Portable tools ready. Next run Build Windows EXE.bat.' -ForegroundColor Green
} catch {
  Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
} finally {
  if (Test-Path $stage) {Remove-Item $stage -Recurse -Force}
}
