import requests

TIMEOUT = 10
READ_BYTES = 4096

STREAM_MIME_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpeg",
    "video/mp2t",
    "video/mp4",
    "application/octet-stream",  # many IPTV servers
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StreamChecker/1.0)"
}


def is_stream_playable(url: str) -> bool:
    try:
        with requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            stream=True,
            allow_redirects=True,
        ) as response:

            if response.status_code >= 400:
                return False

            content_type = response.headers.get("Content-Type", "").lower()

            # Must look like a stream
            if not any(mime in content_type for mime in STREAM_MIME_TYPES):
                return False

            # Read initial bytes to ensure data flows
            chunk = next(response.iter_content(READ_BYTES), None)
            if not chunk:
                return False

            # Special handling for M3U8 playlists
            if ".m3u8" in url.lower():
                text = chunk.decode(errors="ignore")
                if "#EXTM3U" not in text:
                    return False

            return True

    except requests.RequestException:
        return False
