"""HTTP access for research modules.

This file holds the contract that other modules build on: HttpResponse and the HttpClient
protocol. Modules take an HttpClient so tests can pass a fake and never touch the network.

UrllibHttpClient is the real implementation on top of urllib. It is built to fetch public
company websites only, so it refuses anything that could reach the internal network (SSRF
protection, see is_public_host and validate_public_url).
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol
from urllib.parse import quote, urljoin, urlsplit

from src.automation.errors import AutomationError, ErrorType

# Statuses handed back to the caller. Every other status of 400 or above raises AutomationError.
PASSTHROUGH_STATUSES = frozenset({404, 410})


@dataclass(frozen=True)
class HttpResponse:
    url: str  # final URL after redirects
    status: int
    body: str
    elapsed_ms: int
    size_bytes: int
    content_type: str | None = None


class HttpClient(Protocol):
    def get(self, url: str, *, timeout: float = 10.0, max_bytes: int = 2_000_000) -> HttpResponse:
        """Fetch a URL. Returns 2xx, 404 and 410 responses, raises AutomationError otherwise."""
        ...


# ---------------------------------------------------------------------------------------------
# Implementation. HttpResponse, PASSTHROUGH_STATUSES and HttpClient above are the contract and
# must not change.
# ---------------------------------------------------------------------------------------------

ALLOWED_SCHEMES = frozenset({"http", "https"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Same shape as socket.getaddrinfo(host, port). Tests pass a fake.
Resolver = Callable[..., list]

# The timeout argument limits each socket operation. The whole call, redirects and body reading
# included, must also finish within this many timeouts, so a server that drips one byte at a
# time cannot hold a worker for long.
_DEADLINE_FACTOR = 3
_READ_CHUNK = 64 * 1024

_NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+")
_HOST_LABEL = re.compile(r"[a-z0-9_-]{1,63}")
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_PATH_SAFE = "/%:@!$&'()*+,;=~-._"
_QUERY_SAFE = _PATH_SAFE + "?"


class _HostCheck(Enum):
    PUBLIC = "public"
    BLOCKED = "blocked"
    UNRESOLVABLE = "unresolvable"


@dataclass(frozen=True)
class _Host:
    name: str  # canonical form: lowercase ASCII name, dotted quad or compressed IPv6
    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None  # set for IP literals


def _parse_ipv4_number(part: str) -> int | None:
    """One part of an old style IPv4 literal: decimal, octal (leading 0) or hex (0x)."""
    if part.startswith("0x"):
        digits, base = part[2:], 16
    elif len(part) > 1 and part.startswith("0"):
        digits, base = part[1:], 8
    else:
        digits, base = part, 10
    try:
        return int(digits, base)
    except ValueError:
        return None


def _parse_legacy_ipv4(labels: list[str]) -> ipaddress.IPv4Address | None:
    """Parse the spellings inet_aton accepts, such as 2130706433, 0x7f000001, 0177.0.0.1 and 127.1.

    The C resolver reads these as IPv4, so the check has to read them the same way.
    """
    if not 1 <= len(labels) <= 4:
        return None
    numbers = [_parse_ipv4_number(label) for label in labels]
    if any(number is None for number in numbers):
        return None
    *head, last = numbers
    if any(number > 255 for number in head) or last >= 256 ** (5 - len(labels)):
        return None
    value = last
    for index, number in enumerate(head):
        value += number << (8 * (3 - index))
    return ipaddress.IPv4Address(value)


def _parse_ipv6(text: str) -> _Host | None:
    if "%" in text:  # zone ids have no use for a public website
        return None
    try:
        address = ipaddress.IPv6Address(text)
    except ValueError:
        return None
    return _Host(address.compressed, address)


def _parse_host(raw: object) -> _Host | None:
    """Turn a host string into its canonical form, or None if it is not an acceptable host.

    Handles uppercase, one trailing dot, IDN (including full width digits and dots), IPv6 in
    brackets and the odd IPv4 spellings. The canonical form is what gets resolved and requested.
    """
    if not isinstance(raw, str) or not raw:
        return None
    if raw.startswith("[") and raw.endswith("]"):
        return _parse_ipv6(raw[1:-1])
    if ":" in raw:
        return _parse_ipv6(raw)
    try:
        host = raw.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        return None
    labels = host.split(".")
    if not all(_HOST_LABEL.fullmatch(label) for label in labels):
        return None
    if _NUMERIC_LABEL.fullmatch(labels[-1]):
        # A numeric last label is never a real top level domain. Read it as IPv4 or refuse it.
        address = _parse_legacy_ipv4(labels)
        return None if address is None else _Host(str(address), address)
    return _Host(host, None)


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 address carries (mapped, 6to4 or NAT64), if any."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            return _is_public_address(embedded)
        if address.is_site_local:
            return False
    # is_global alone is not enough: it is True for multicast and site-local IPv6 on some versions.
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or not address.is_global
    )


def _check_host(host: _Host, resolver: Resolver) -> _HostCheck:
    if host.address is not None:
        return _HostCheck.PUBLIC if _is_public_address(host.address) else _HostCheck.BLOCKED
    if host.name == "localhost" or host.name.endswith(".localhost"):
        return _HostCheck.BLOCKED
    try:
        infos = list(resolver(host.name, None))
    except (OSError, UnicodeError):
        return _HostCheck.UNRESOLVABLE
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except (ValueError, IndexError, TypeError):
            return _HostCheck.BLOCKED  # an answer we cannot read is not an answer we trust
    if not addresses:
        return _HostCheck.UNRESOLVABLE
    # One private address among public ones is enough to refuse: the connection may pick it.
    return _HostCheck.PUBLIC if all(_is_public_address(a) for a in addresses) else _HostCheck.BLOCKED


def is_public_host(host: str, resolver: Resolver = socket.getaddrinfo) -> bool:
    """True only if host is a name or IP literal that points to public addresses only.

    False for private, loopback, link-local, multicast, reserved and unspecified addresses
    (also as IPv4-mapped IPv6 and in decimal, hex or octal spelling), for localhost, for
    malformed hosts and for hosts that do not resolve. Every resolved address must be public.
    An IPv6 address that carries an IPv4 address (mapped, 6to4, NAT64) is judged by the IPv4
    address inside it.
    """
    parsed = _parse_host(host)
    return parsed is not None and _check_host(parsed, resolver) is _HostCheck.PUBLIC


def _shorten(text: str, limit: int = 80) -> str:
    return repr(text if len(text) <= limit else text[:limit] + "...")


def _check_url(url: object, resolver: Resolver, label: str) -> str:
    """Validate a URL and return the canonical URL to request. Raises AutomationError."""

    def refuse(reason: str, error_type: ErrorType = ErrorType.VALIDATION_ERROR) -> AutomationError:
        return AutomationError(error_type, f"{label} {reason}")

    if not isinstance(url, str):
        raise refuse("must be a string")
    text = url.strip()
    if not text:
        raise refuse("is empty")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise refuse("contains control characters")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise refuse("is not a valid URL or has an invalid port") from None
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise refuse("scheme is not allowed, only http and https")
    if "@" in parts.netloc:
        raise refuse("must not contain credentials")
    if not parts.hostname:
        raise refuse("has no host")
    host = _parse_host(parts.hostname)
    if host is None:
        raise refuse(f"has an invalid host {_shorten(parts.hostname)}")
    verdict = _check_host(host, resolver)
    if verdict is _HostCheck.BLOCKED:
        raise refuse(f"host is not a public address: {_shorten(host.name)}")
    if verdict is _HostCheck.UNRESOLVABLE:
        raise refuse("host could not be resolved", ErrorType.TEMPORARY_ERROR)

    # Rebuild the URL from the validated parts so the opener cannot read it differently.
    host_text = f"[{host.name}]" if isinstance(host.address, ipaddress.IPv6Address) else host.name
    netloc = host_text if port is None else f"{host_text}:{port}"
    path = quote(parts.path, safe=_PATH_SAFE) or "/"
    query = quote(parts.query, safe=_QUERY_SAFE)
    return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def validate_public_url(url: str, resolver: Resolver = socket.getaddrinfo) -> str:
    """Check that url may be fetched and return its canonical form.

    Only http and https, no credentials in the URL, a non-empty host and public addresses only.
    Raises AutomationError(VALIDATION_ERROR) for a URL that is refused, and TEMPORARY_ERROR if
    the host does not resolve. Nothing is requested.
    """
    return _check_url(url, resolver, "URL")


def _build_default_opener() -> urllib.request.OpenerDirector:
    """Opener for http and https only: no redirect, file, ftp, data or cookie handlers.

    Without a redirect handler a 3xx arrives as an HTTPError, so the client follows redirects
    itself and checks every hop. Certificates and host names are verified. Proxy settings from
    the environment are honoured, which means a configured proxy makes the connection itself.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def _network_error(exc: BaseException) -> AutomationError:
    """Classify a low level failure. The message holds the exception class only, never any
    text the server sent and never the request, so nothing sensitive can leak through it."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    name = type(reason).__name__
    if isinstance(reason, TimeoutError):
        return AutomationError(ErrorType.TEMPORARY_ERROR, "timeout while talking to the server")
    if isinstance(reason, ssl.SSLError):
        return AutomationError(
            ErrorType.EXTERNAL_SERVICE_ERROR, f"tls or certificate error ({name})"
        )
    if isinstance(reason, (OSError, http.client.IncompleteRead)):
        return AutomationError(ErrorType.TEMPORARY_ERROR, f"network error ({name})")
    if isinstance(reason, http.client.HTTPException):
        return AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, f"malformed HTTP response ({name})")
    return AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, f"request failed ({name})")


def _check_status(status: int) -> None:
    """Raise for every status the caller should not see. 2xx, 404 and 410 pass."""
    if 200 <= status < 300 or status in PASSTHROUGH_STATUSES:
        return
    if status in (401, 403):
        raise AutomationError(ErrorType.AUTHENTICATION_ERROR, f"access refused (HTTP {status})")
    if status == 429:
        raise AutomationError(ErrorType.RATE_LIMIT, "too many requests (HTTP 429)")
    if status == 408:
        raise AutomationError(ErrorType.TEMPORARY_ERROR, "request timeout (HTTP 408)")
    if 400 <= status < 500:
        raise AutomationError(ErrorType.VALIDATION_ERROR, f"request rejected (HTTP {status})")
    if 500 <= status < 600:
        raise AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, f"server error (HTTP {status})")
    raise AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, f"unexpected HTTP status {status}")


def _status_of(response) -> int:
    status = getattr(response, "status", None)
    return status if isinstance(status, int) else response.getcode()


def _header(response, name: str) -> str | None:
    value = response.headers.get(name) if getattr(response, "headers", None) is not None else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _close(response) -> None:
    try:
        response.close()
    except OSError:
        pass


def _decode(data: bytes, content_type: str | None) -> str:
    """Decode with the charset from Content-Type. Default utf-8, bad bytes become U+FFFD."""
    charset = "utf-8"
    for param in (content_type or "").split(";")[1:]:
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset" and value.strip().strip("\"'"):
            charset = value.strip().strip("\"'")
            break
    try:
        return data.decode(charset, errors="replace")
    except LookupError:  # unknown charset name, or a codec that is not a text encoding
        return data.decode("utf-8", errors="replace")


def _resolve_redirect(base: str, location: str) -> str:
    """Join a Location header onto the current URL. Control characters are refused up front,
    because urljoin would silently drop tabs and newlines."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in location):
        raise AutomationError(ErrorType.VALIDATION_ERROR, "redirect target contains control characters")
    try:
        return urljoin(base, location)
    except ValueError:
        raise AutomationError(ErrorType.VALIDATION_ERROR, "redirect target is not a valid URL") from None


class UrllibHttpClient:
    """HttpClient on top of urllib. Fetches public websites and refuses everything else.

    Per request: the URL and every redirect target is checked by validate_public_url before
    any connection is made. Redirects are followed by hand, at most max_redirects of them.
    Only a User-Agent, Accept and Accept-Encoding header is sent, on every hop, and there are
    no cookies and no credentials. At most max_bytes of the body are read, the rest is dropped
    without notice. The body is decoded with the charset from Content-Type, default utf-8.

    Statuses: 2xx, 404 and 410 are returned. 401 and 403 raise AUTHENTICATION_ERROR, 429
    RATE_LIMIT, 408 TEMPORARY_ERROR, other 4xx VALIDATION_ERROR and 5xx EXTERNAL_SERVICE_ERROR.
    Timeouts, resets and DNS failures raise TEMPORARY_ERROR. TLS and certificate problems raise
    EXTERNAL_SERVICE_ERROR with "tls" in the message. Too many redirects, a redirect loop and a
    redirect without Location raise EXTERNAL_SERVICE_ERROR. A blocked URL raises
    VALIDATION_ERROR. Messages never hold response bodies, request headers or full URLs.

    Known limit: the host is resolved once for the check and again by urllib when it connects.
    An attacker who controls the DNS for a host can answer the first lookup with a public
    address and the second with a private one. This design does not close that race. It
    would take connecting to the checked address directly, or egress rules in the network.
    Behind a proxy (HTTP_PROXY, HTTPS_PROXY) the proxy resolves and connects, which widens it.

    The opener needs open(request, timeout=...) and returns an object with status (or
    getcode()), headers.get(name), read(amount) and close(). An HTTPError may be raised or
    returned. There is no switch to allow private addresses. Tests pass a fake opener and a
    fake resolver instead.
    """

    def __init__(
        self,
        user_agent: str = "MerediaResearch/1.0",
        resolver: Resolver = socket.getaddrinfo,
        opener=None,
        max_redirects: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not user_agent or any(ord(ch) < 32 or ord(ch) == 127 for ch in user_agent):
            raise AutomationError(ErrorType.VALIDATION_ERROR, "user_agent must be text without control characters")
        if max_redirects < 0:
            raise AutomationError(ErrorType.VALIDATION_ERROR, "max_redirects cannot be negative")
        self.user_agent = user_agent
        self.max_redirects = max_redirects
        self._resolver = resolver
        self._opener = opener if opener is not None else _build_default_opener()
        self._clock = clock

    def get(self, url: str, *, timeout: float = 10.0, max_bytes: int = 2_000_000) -> HttpResponse:
        """Fetch a URL. Returns 2xx, 404 and 410 responses, raises AutomationError otherwise."""
        if timeout <= 0:
            raise AutomationError(ErrorType.VALIDATION_ERROR, "timeout must be positive")
        if max_bytes <= 0:
            raise AutomationError(ErrorType.VALIDATION_ERROR, "max_bytes must be positive")
        started = self._clock()
        deadline = started + timeout * _DEADLINE_FACTOR

        current = _check_url(url, self._resolver, "URL")
        seen = {current}
        for hop in range(self.max_redirects + 1):
            self._check_deadline(deadline)
            response = self._open(current, timeout)
            try:
                status = _status_of(response)
                if status in REDIRECT_STATUSES:
                    location = _header(response, "Location")
                else:
                    _check_status(status)
                    data = self._read_body(response, max_bytes, deadline)
                    content_type = _header(response, "Content-Type")
                    elapsed_ms = max(0, round((self._clock() - started) * 1000))
                    return HttpResponse(
                        url=current,
                        status=status,
                        body=_decode(data, content_type),
                        elapsed_ms=elapsed_ms,
                        size_bytes=len(data),
                        content_type=content_type,
                    )
            finally:
                _close(response)

            if hop == self.max_redirects:
                raise AutomationError(
                    ErrorType.EXTERNAL_SERVICE_ERROR, f"too many redirects (limit {self.max_redirects})"
                )
            if location is None:
                raise AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, "redirect without Location header")
            current = _check_url(_resolve_redirect(current, location), self._resolver, "redirect target")
            if current in seen:
                raise AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, "redirect loop")
            seen.add(current)
        raise AssertionError("unreachable")  # the loop always returns or raises

    def _check_deadline(self, deadline: float) -> None:
        if self._clock() > deadline:
            raise AutomationError(ErrorType.TEMPORARY_ERROR, "timeout: the request took too long in total")

    def _open(self, url: str, timeout: float):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        try:
            return self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            return exc  # carries status, headers and body like a response
        except (OSError, http.client.HTTPException) as exc:
            raise _network_error(exc) from None

    def _read_body(self, response, max_bytes: int, deadline: float) -> bytes:
        chunks: list[bytes] = []
        total = 0
        try:
            while total < max_bytes:
                chunk = response.read(min(_READ_CHUNK, max_bytes - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                self._check_deadline(deadline)
        except (OSError, http.client.HTTPException) as exc:
            raise _network_error(exc) from None
        return b"".join(chunks)[:max_bytes]
