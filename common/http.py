"""Tiny stdlib HTTP helper (no third-party deps)."""
import json
import ssl
import time
import urllib.error
import urllib.request

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CONTEXT = None


class HttpError(Exception):
    def __init__(self, status, body=""):
        super().__init__(f"HTTP {status}: {str(body)[:300]}")
        self.status = status
        self.body = body


def request_json(method, url, body=None, headers=None, timeout=30, retries=0, backoff=1.5):
    data = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            last = HttpError(e.code, raw)
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries:
                try:
                    return e.code, json.loads(raw)
                except Exception:
                    raise last
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if attempt == retries:
                raise
        time.sleep(backoff ** attempt)
    raise last
