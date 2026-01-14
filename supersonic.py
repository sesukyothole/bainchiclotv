import asyncio
import aiohttp
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

# ---------- CONFIG ----------
TIMEOUT = aiohttp.ClientTimeout(total=12)
MAX_CONCURRENCY = 80
MAX_HLS_DEPTH = 3
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}
BLOCKED_DOMAINS = {
    "amagi.tv",
    "ssai2-ads.api.leiniao.com",
}

# ---------- STREAM VALIDATION (IGNORE SPEED) ----------
async def is_stream_fast(session, url, headers, depth=0):
    """
    Accept all streams as 'fast', unless they are blocked or recursion is too deep.
    """
    if depth > MAX_HLS_DEPTH:
        return False

    for d in BLOCKED_DOMAINS:
        if d in url:
            return False

    # Non-HLS or HLS, always accept
    return True

# ---------- WORKER ----------
async def check_stream(semaphore, session, entry):
    extinf, vlcopts, url = entry
    headers = {}

    for opt in vlcopts:
        key, _, value = opt[len("#EXTVLCOPT:"):].partition("=")
        k = key.lower()
        if k == "http-referrer":
            headers["Referer"] = value
        elif k == "http-origin":
            headers["Origin"] = value
        elif k == "http-user-agent":
            headers["User-Agent"] = value

    async with semaphore:
        fast = await is_stream_fast(session, url, headers)

    title = ""
    if extinf:
        parts = extinf[0].split(",", 1)
        if len(parts) == 2:
            title = parts[1].strip()

    return fast, title, extinf, vlcopts, url

# ---------- MAIN ----------
async def filter_all_streams(input_path, output_path):
    lines = Path(input_path).read_text(encoding="utf-8", errors="ignore").splitlines()

    entries = []
    extinf, vlcopts = [], []

    for line in lines:
        if line.startswith("#EXTINF"):
            extinf = [line]
        elif line.startswith("#EXTVLCOPT"):
            vlcopts.append(line)
        elif line.startswith(("http://", "https://")):
            entries.append((extinf.copy(), vlcopts.copy(), line.strip()))
            extinf.clear()
            vlcopts.clear()

    connector = aiohttp.TCPConnector(limit_per_host=15, ssl=False)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with aiohttp.ClientSession(
        timeout=TIMEOUT,
        connector=connector,
        headers=DEFAULT_HEADERS,
    ) as session:

        tasks = [check_stream(semaphore, session, e) for e in entries]
        all_entries = []

        for coro in asyncio.as_completed(tasks):
            fast, title, extinf, vlcopts, url = await coro
            if fast:
                print(f"✓ ACCEPTED: {title or url}")
                if extinf:
                    parts = extinf[0].split(",", 1)
                    extinf[0] = (
                        f'{parts[0]} group-title="Fast",{parts[1]}'
                        if len(parts) == 2
                        else f'{parts[0]} group-title="Fast"'
                    )
                all_entries.append((title.lower(), extinf, vlcopts, url))
            else:
                print(f"✗ BLOCKED DOMAIN: {url}")

    all_entries.sort(key=lambda x: x[0])

    out = ["#EXTM3U"]
    for _, extinf, vlcopts, url in all_entries:
        out.extend(extinf)
        out.extend(vlcopts)
        out.append(url)

    Path(output_path).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\nSaved playlist to: {output_path}")

# ---------- CLI ----------
if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python fast_filter.py input.m3u output.m3u")
        sys.exit(1)

    if not Path(sys.argv[1]).exists():
        print("Input file does not exist.")
        sys.exit(1)

    asyncio.run(filter_all_streams(sys.argv[1], sys.argv[2]))
