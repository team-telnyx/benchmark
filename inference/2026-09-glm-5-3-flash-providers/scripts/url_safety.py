from urllib.parse import urlsplit


def require_https_url(url: str) -> str:
    """Accept only credential-free HTTPS URLs for benchmark network calls."""
    if not url or any(ord(character) <= 32 or ord(character) == 127 for character in url):
        raise ValueError("Benchmark URLs must not contain whitespace or control characters")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"Benchmark URL must use HTTPS and include a hostname: {url!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Benchmark URLs must not contain embedded credentials")
    return url
