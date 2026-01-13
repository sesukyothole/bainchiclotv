import requests
import sys
from pathlib import Path
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

TIMEOUT = 10
MIN_SEGMENT_SIZE = 20000  # 20 KB
MAX_THREADS = 20  # adjust based on CPU/network

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# Domains to automatically reject
BLOCKED_DOMAINS = [
    "amagi.tv",
    "ssai2-ads.api.leiniao.com"
]


def is_stream_playable(url, headers=None):
    """Check if a stream is playable (relaxed): blocked domains + HLS/segments."""
    for blocked in BLOCKED_DOMAINS:
        if blocked in url:
            return False

    headers = {**DEFAULT_HEADERS, **(headers or {})}

    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        if r.status_code >= 400:
            return False
    except requests.RequestException:
        return False

    content_type = r.headers.get("Content-Type", "").lower()

    # HLS playlist
    if ".m3u8" in url or "mpegurl" in content_type:
        text = r.text
        if not text.lstrip().startswith("#EXTM3U"):
            return False

        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # Master playlist → check first variant recursively
        if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
            for i, l in enumerate(lines):
                if l.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
                    variant = lines[i + 1]
                    if not variant.startswith("#"):
                        return is_stream_playable(urljoin(url, variant), headers)
            return False

        # Media playlist → check first segment size
        segments = [l for l in lines if not l.startswith("#")]
        if not segments:
            return False

        seg_url = urljoin(url, segments[0])
        try:
            seg = requests.get(seg_url, headers=headers, timeout=TIMEOUT, stream=True)
            if seg.status_code >= 400:
                return False

            data = b""
            for chunk in seg.iter_content(8192):
                if not chunk:
                    break
                data += chunk
                if len(data) >= 65536:
                    break

            if len(data) < MIN_SEGMENT_SIZE:
                return False

            return True
        except requests.RequestException:
            return False

    return True


def check_stream(entry):
    """Worker function for multithreading. Returns (playable, extinf, vlcopts, url, title)."""
    extinf, vlcopts, url = entry
    headers = {}
    for opt in vlcopts:
        key, _, value = opt[len("#EXTVLCOPT:"):].partition("=")
        key = key.lower()
        if key == "http-referrer":
            headers["Referer"] = value
        elif key == "http-origin":
            headers["Origin"] = value
        elif key == "http-user-agent":
            headers["User-Agent"] = value

    playable = is_stream_playable(url, headers)

    # Extract title from EXTINF line
    title = ""
    if extinf:
        parts = extinf[0].split(",", 1)
        if len(parts) == 2:
            title = parts[1].strip()

    return playable, extinf, vlcopts, url, title


def filter_m3u_playlist(input_path, output_path):
    """Reads EXTINF playlist, filters playable streams, adds group-title, sorts alphabetically."""
    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.rstrip() for line in f]

    entries = []
    extinf, vlcopts = [], []

    for line in lines:
        if line.startswith("#EXTINF"):
            extinf = [line]
        elif line.startswith("#EXTVLCOPT"):
            vlcopts.append(line)
        elif line.strip().startswith(("http://", "https://")):
            url = line.strip()
            entries.append((extinf.copy(), vlcopts.copy(), url))
            extinf, vlcopts = [], []

    playable_entries = []

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        future_to_entry = {executor.submit(check_stream, e): e for e in entries}
        for future in as_completed(future_to_entry):
            playable, extinf, vlcopts, url, title = future.result()
            if playable:
                print(f"✓ Playable: {title} ({url})")
                # Add group-title="TCL+" to EXTINF line
                if extinf:
                    parts = extinf[0].split(",", 1)
                    if len(parts) == 2:
                        extinf[0] = f'{parts[0]} group-title="TCL+",{parts[1]}'
                    else:
                        extinf[0] = f'{parts[0]} group-title="TCL+"'
                playable_entries.append((title, extinf, vlcopts, url))
            else:
                print(f"✗ Rejected (blocked domain / tiny segment / unreachable): {url}")

    # Sort alphabetically by title
    playable_entries.sort(key=lambda x: x[0].lower())

    output = ["#EXTM3U"]
    for title, extinf, vlcopts, url in playable_entries:
        output.extend(extinf)
        output.extend(vlcopts)
        output.append(url)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output) + "\n")

    print(f"\nSaved filtered and sorted playlist to: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python filter_m3u_playlist.py input.m3u output.m3u")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    if not Path(input_file).exists():
        print("Input file does not exist.")
        sys.exit(1)

    filter_m3u_playlist(input_file, output_file)
