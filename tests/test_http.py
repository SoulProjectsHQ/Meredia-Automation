import email.message
import http.client
import inspect
import io
import socket
import ssl
import unittest
import urllib.error
import urllib.request
from dataclasses import fields

from src.automation.errors import AutomationError, ErrorType
from src.integrations.http import (
    PASSTHROUGH_STATUSES,
    HttpClient,
    HttpResponse,
    UrllibHttpClient,
    is_public_host,
    validate_public_url,
)

E = ErrorType
# A real global address. The documentation ranges (192.0.2.0/24, 203.0.113.0/24) count as private.
PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"
START = "https://www.example.no/"
SECRET_BODY = "SECRET-BODY-TEXT"
SECRET_AGENT = "MerediaTestAgent/9.9"
SECRET_QUERY = "token=abc123"

# Hosts that must never be fetched. Includes the odd spellings of loopback and metadata addresses.
BLOCKED_HOSTS = [
    "127.0.0.1",
    "127.255.255.254",
    "10.1.2.3",
    "10.255.255.255",
    "192.168.1.1",
    "172.16.0.1",
    "172.31.255.255",
    "169.254.169.254",
    "::1",
    "[::1]",
    "fe80::1",
    "::ffff:127.0.0.1",
    "::ffff:7f00:1",
    "0:0:0:0:0:ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "::ffff:10.0.0.1",
    "2002:a9fe:a9fe::",
    "64:ff9b::c0a8:1",
    "0.0.0.0",
    "::",
    "localhost",
    "LOCALHOST",
    "localhost.",
    "app.localhost",
    "127.0.0.1.",
    "2130706433",
    "0x7f000001",
    "0177.0.0.1",
    "017700000001",
    "127.1",
    "0x7f.0.0.1",
    "2852039166",
    "0xa9fea9fe",
    "\uff11\uff12\uff17.\uff10.\uff10.\uff11",  # full width digits
    "127\u30020\u30020\u30021",  # ideographic full stops
    "100.64.0.1",
    "192.0.2.1",
    "198.18.0.1",
    "224.0.0.1",
    "240.0.0.1",
    "255.255.255.255",
    "fc00::1",
    "fd12:3456:789a::1",
    "fec0::1",
    "ff02::1",
    "64:ff9b::7f00:1",
    "64:ff9b::a9fe:a9fe",
    "2002:7f00:1::",
    "2001::1",
]


def url_for(host: str) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}/"


def addrinfo(*addresses: str) -> list:
    """Build a socket.getaddrinfo style answer."""
    result = []
    for address in addresses:
        if ":" in address:
            result.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 0, 0, 0)))
        else:
            result.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)))
    return result


class FakeResolver:
    """getaddrinfo stand-in. Unknown hosts get the default answer, an exception value is raised."""

    def __init__(self, table=None, default=(PUBLIC_V4,)):
        self.table = dict(table or {})
        self.default = default
        self.calls: list[str] = []

    def __call__(self, host, port, *args, **kwargs):
        self.calls.append(host)
        answer = self.table.get(host, self.default)
        if isinstance(answer, BaseException):
            raise answer
        return addrinfo(*answer)


def no_dns(host, port, *args, **kwargs):
    raise AssertionError(f"DNS must not be used, but {host!r} was resolved")


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_headers(pairs: dict | None = None) -> email.message.Message:
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = value
    return message


class FakeResponse:
    """What an opener hands back: status, headers, read(amount) and close()."""

    def __init__(self, status=200, body=b"", headers=None, read_error=None, on_read=None):
        self.status = status
        self.headers = make_headers(headers)
        self._buffer = io.BytesIO(body)
        self._read_error = read_error
        self._on_read = on_read
        self.bytes_read = 0
        self.max_amount = 0
        self.closed = False

    def read(self, amount=-1):
        if self._on_read:
            self._on_read()
        if self._read_error:
            raise self._read_error
        data = self._buffer.read(amount)
        self.bytes_read += len(data)
        self.max_amount = max(self.max_amount, amount)
        return data

    def close(self):
        self.closed = True


class OverfullResponse(FakeResponse):
    """A badly behaved response that ignores the amount asked for and returns everything."""

    def read(self, amount=-1):
        return super().read(-1)


def http_error(url: str, status: int, body: bytes = b"", headers=None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, status, "message", make_headers(headers), io.BytesIO(body))


def status_outcome(form: str, url: str, status: int, body: bytes = b"", headers=None):
    """The same answer in the two shapes an opener can use: raised HTTPError or returned response."""
    if form == "raised":
        return http_error(url, status, body, headers)
    return FakeResponse(status, body, headers)


FORMS = ("raised", "returned")


class FakeOpener:
    """Plays back outcomes by URL. An outcome is a response, an exception (raised) or a list of them."""

    def __init__(self, routes, on_open=None):
        self.routes = routes
        self.on_open = on_open
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.on_open:
            self.on_open()
        outcome = self.routes[request.full_url]
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def urls(self) -> list[str]:
        return [r.full_url for r in self.requests]


def make_client(routes=None, resolver=None, clock=None, on_open=None, **kwargs):
    opener = FakeOpener(routes if routes is not None else {}, on_open=on_open)
    client = UrllibHttpClient(
        resolver=resolver if resolver is not None else FakeResolver(),
        opener=opener,
        clock=clock if clock is not None else FakeClock(),
        **kwargs,
    )
    return client, opener


def ok(body: bytes = b"<html>hei</html>", headers=None, status: int = 200) -> FakeResponse:
    return FakeResponse(status, body, headers or {"Content-Type": "text/html; charset=utf-8"})


def redirect(location: str, status: int = 302) -> FakeResponse:
    return FakeResponse(status, b"", {"Location": location})


class ContractTests(unittest.TestCase):
    def test_http_response_fields_are_unchanged(self):
        names = [f.name for f in fields(HttpResponse)]
        self.assertEqual(names, ["url", "status", "body", "elapsed_ms", "size_bytes", "content_type"])
        self.assertEqual(PASSTHROUGH_STATUSES, frozenset({404, 410}))

    def test_get_signature_matches_the_protocol(self):
        def shape(fn):
            return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]

        self.assertEqual(shape(UrllibHttpClient.get), shape(HttpClient.get))

    def test_constructor_defaults(self):
        params = inspect.signature(UrllibHttpClient.__init__).parameters
        self.assertEqual(params["user_agent"].default, "MerediaResearch/1.0")
        self.assertEqual(params["max_redirects"].default, 5)
        self.assertIs(params["resolver"].default, socket.getaddrinfo)
        self.assertIsNone(params["opener"].default)

    def test_default_opener_only_speaks_http_and_never_follows_redirects(self):
        handlers = UrllibHttpClient()._opener.handlers
        for forbidden in (
            urllib.request.HTTPRedirectHandler,
            urllib.request.FileHandler,
            urllib.request.FTPHandler,
            urllib.request.DataHandler,
            urllib.request.HTTPCookieProcessor,
        ):
            self.assertFalse(any(isinstance(h, forbidden) for h in handlers), forbidden.__name__)

    def test_default_opener_verifies_certificates(self):
        https = [h for h in UrllibHttpClient()._opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        self.assertEqual(len(https), 1)
        context = https[0]._context
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


class IsPublicHostTests(unittest.TestCase):
    def test_public_name_is_allowed(self):
        resolver = FakeResolver({"www.example.no": [PUBLIC_V4, PUBLIC_V6]})
        self.assertTrue(is_public_host("www.example.no", resolver))
        self.assertEqual(resolver.calls, ["www.example.no"])

    def test_public_ip_literals_are_allowed_without_dns(self):
        for host in (PUBLIC_V4, "8.8.8.8", PUBLIC_V6, f"[{PUBLIC_V6}]", "0x5db8d822"):
            with self.subTest(host=host):
                self.assertTrue(is_public_host(host, no_dns))

    def test_uppercase_and_trailing_dot_are_normalised_before_resolving(self):
        resolver = FakeResolver()
        self.assertTrue(is_public_host("WWW.Example.NO.", resolver))
        self.assertEqual(resolver.calls, ["www.example.no"])

    def test_international_names_are_resolved_as_punycode(self):
        resolver = FakeResolver()
        self.assertTrue(is_public_host("bl\u00e5b\u00e6r.no", resolver))
        self.assertEqual(resolver.calls, ["xn--blbr-roah.no"])

    def test_ipv6_forms_that_carry_ipv4_are_judged_by_the_ipv4_inside(self):
        # IPv4-mapped, 6to4 and NAT64. The IPv6 flags alone are not what decides.
        for host in ("::ffff:8.8.8.8", "2002:808:808::", "64:ff9b::808:808"):
            with self.subTest(host=host):
                self.assertTrue(is_public_host(host, no_dns))
        for host in ("::ffff:10.0.0.1", "::ffff:192.168.1.1", "2002:a00:1::", "64:ff9b::c0a8:1", "2002:a9fe:a9fe::"):
            with self.subTest(host=host):
                self.assertFalse(is_public_host(host, no_dns))

    def test_blocked_hosts_are_refused_without_dns(self):
        for host in BLOCKED_HOSTS:
            with self.subTest(host=host):
                self.assertFalse(is_public_host(host, no_dns))

    def test_localhost_is_refused_even_if_dns_would_say_public(self):
        resolver = FakeResolver(default=(PUBLIC_V4,))
        for host in ("localhost", "LocalHost.", "service.localhost"):
            with self.subTest(host=host):
                self.assertFalse(is_public_host(host, resolver))
        self.assertEqual(resolver.calls, [])

    def test_name_resolving_to_a_private_address_is_refused(self):
        table = {
            "internal.example.no": ["10.0.0.7"],
            "loopback.example.no": ["127.0.0.1"],
            "metadata.example.no": ["169.254.169.254"],
            "v6loop.example.no": ["::1"],
            "mapped.example.no": ["::ffff:127.0.0.1"],
            "ula.example.no": ["fd00::5"],
            "linklocal.example.no": ["fe80::1"],
            "zero.example.no": ["0.0.0.0"],
        }
        resolver = FakeResolver(table)
        for host in table:
            with self.subTest(host=host):
                self.assertFalse(is_public_host(host, resolver))

    def test_name_resolving_to_a_mix_is_refused_in_any_order(self):
        resolver = FakeResolver(
            {
                "mixed.example.no": [PUBLIC_V4, "10.0.0.1"],
                "mixed2.example.no": ["10.0.0.1", PUBLIC_V4],
                "mixed3.example.no": [PUBLIC_V4, PUBLIC_V6, "::1"],
            }
        )
        for host in ("mixed.example.no", "mixed2.example.no", "mixed3.example.no"):
            with self.subTest(host=host):
                self.assertFalse(is_public_host(host, resolver))

    def test_scoped_ipv6_answer_is_judged_without_the_scope(self):
        def resolver(host, port, *args, **kwargs):
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 0, 0, 2))]

        self.assertFalse(is_public_host("scoped.example.no", resolver))

    def test_unresolvable_names_are_not_public(self):
        for answer in (socket.gaierror(-2, "Name or service not known"), OSError("boom")):
            with self.subTest(answer=type(answer).__name__):
                self.assertFalse(is_public_host("gone.example.no", FakeResolver({"gone.example.no": answer})))
        self.assertFalse(is_public_host("empty.example.no", lambda host, port, *a, **k: []))

    def test_unreadable_answer_is_not_trusted(self):
        for answer in ([(socket.AF_INET, 1, 6, "", ("not-an-ip", 0))], [(socket.AF_INET,)], [None]):
            with self.subTest(answer=answer):
                self.assertFalse(is_public_host("odd.example.no", lambda host, port, *a, _r=answer, **k: _r))

    def test_malformed_hosts_are_refused(self):
        for host in (
            "",
            " ",
            None,
            42,
            "exa mple.no",
            "a..b.no",
            "www.example.no/path",
            "www.example.no:80",
            "1.2.3.4.5",
            "256.1.1.1",
            "0x",
            "08.0.0.1",
            "[127.0.0.1]",
            "fe80::1%eth0",
            "a" * 64 + ".no",
            "x" * 250 + ".no",
            "evil.no\\@127.0.0.1",
            "ex\x00ample.no",
        ):
            with self.subTest(host=host):
                self.assertFalse(is_public_host(host, no_dns))


class ValidatePublicUrlTests(unittest.TestCase):
    def check(self, url, resolver=None):
        return validate_public_url(url, resolver if resolver is not None else FakeResolver())

    def refused(self, url, error_type=E.VALIDATION_ERROR, resolver=None) -> AutomationError:
        with self.assertRaises(AutomationError) as ctx:
            self.check(url, resolver)
        self.assertEqual(ctx.exception.error_type, error_type, url)
        return ctx.exception

    def test_canonical_form(self):
        self.assertEqual(self.check("HTTPS://WWW.Example.NO:8443/a b?x=\u00e6#frag"), "https://www.example.no:8443/a%20b?x=%C3%A6")
        self.assertEqual(self.check("http://www.example.no"), "http://www.example.no/")
        self.assertEqual(self.check("  https://www.example.no/x  "), "https://www.example.no/x")
        self.assertEqual(self.check("https://bl\u00e5b\u00e6r.no/\u00e5"), "https://xn--blbr-roah.no/%C3%A5")

    def test_existing_percent_escapes_are_kept(self):
        self.assertEqual(self.check("https://www.example.no/a%20b?q=%C3%A6"), "https://www.example.no/a%20b?q=%C3%A6")

    def test_ip_spellings_are_rewritten_so_the_opener_sees_what_was_checked(self):
        self.assertEqual(self.check("http://0x5db8d822/x", no_dns), f"http://{PUBLIC_V4}/x")
        self.assertEqual(self.check(f"http://[{PUBLIC_V6}]:8080/x", no_dns), f"http://[{PUBLIC_V6}]:8080/x")

    def test_backslash_and_userinfo_tricks_do_not_reach_the_opener(self):
        self.refused("http://www.example.no\\@127.0.0.1/")
        self.refused("http://127.0.0.1\\.www.example.no/")
        self.refused("http://127.0.0.1#@www.example.no/")
        self.refused("http://127.0.0.1?@www.example.no/")
        # Everything after # is a fragment, so the host really is www.example.no.
        self.assertEqual(self.check("http://www.example.no#@127.0.0.1/"), "http://www.example.no/")

    def test_credentials_are_refused_and_not_echoed(self):
        for url in (
            "http://user:hunter2@www.example.no/",
            "http://user@www.example.no/",
            "https://@www.example.no/",
            "https://:hunter2@www.example.no/",
            "http://www.example.no@127.0.0.1/",
        ):
            with self.subTest(url=url):
                error = self.refused(url)
                self.assertNotIn("hunter2", str(error))
                self.assertNotIn("user", str(error))

    def test_only_http_and_https_are_allowed(self):
        for url in (
            "ftp://www.example.no/file",
            "file:///etc/passwd",
            "gopher://www.example.no/",
            "javascript:alert(1)",
            "data:text/html,hei",
            "www.example.no/path",
            "//www.example.no/path",
            "/path/only",
            "ws://www.example.no/",
        ):
            with self.subTest(url=url):
                self.refused(url)

    def test_scheme_case_is_ignored(self):
        self.assertEqual(self.check("HtTp://www.example.no/"), "http://www.example.no/")

    def test_empty_host_is_refused(self):
        for url in ("http://", "http:///path", "https://:443/", "http://./", "http:www.example.no", "https://?x=1"):
            with self.subTest(url=url):
                self.refused(url)

    def test_bad_ports_are_refused(self):
        for url in ("http://www.example.no:99999/", "http://www.example.no:abc/", "http://www.example.no:80:90/"):
            with self.subTest(url=url):
                self.refused(url)

    def test_unusable_input_is_refused(self):
        for url in ("", "   ", None, 42, b"http://www.example.no/"):
            with self.subTest(url=url):
                self.refused(url)

    def test_control_characters_are_refused(self):
        for url in (
            "http://www.example.no/\r\nHost: evil.no",
            "http://www.exa\tmple.no/",
            "http://www.example.no/\x00",
            "http://www.example.no/a\x7fb",
        ):
            with self.subTest(url=repr(url)):
                self.refused(url)

    def test_blocked_hosts_are_refused_as_urls(self):
        for host in BLOCKED_HOSTS:
            with self.subTest(host=host):
                self.refused(url_for(host), resolver=no_dns)

    def test_blocked_message_names_the_host_but_not_the_path(self):
        error = self.refused(f"http://10.0.0.5/private?{SECRET_QUERY}")
        self.assertIn("10.0.0.5", str(error))
        self.assertNotIn(SECRET_QUERY, str(error))

    def test_private_resolution_is_refused(self):
        resolver = FakeResolver({"internal.example.no": ["10.0.0.7"], "mixed.example.no": [PUBLIC_V4, "10.0.0.7"]})
        self.refused("http://internal.example.no/", resolver=resolver)
        self.refused("http://mixed.example.no/", resolver=resolver)

    def test_dns_failure_is_temporary_not_validation(self):
        resolver = FakeResolver({"gone.example.no": socket.gaierror(-2, "Name or service not known")})
        self.refused("http://gone.example.no/", E.TEMPORARY_ERROR, resolver)
        self.refused("http://empty.example.no/", E.TEMPORARY_ERROR, FakeResolver({"empty.example.no": []}))


class FetchTests(unittest.TestCase):
    def test_allowed_public_url_is_fetched(self):
        resolver = FakeResolver()
        client, opener = make_client({START: ok(b"<h1>Hei</h1>")}, resolver=resolver)
        result = client.get(START, timeout=7.5)
        self.assertEqual(result.url, START)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body, "<h1>Hei</h1>")
        self.assertEqual(result.size_bytes, len(b"<h1>Hei</h1>"))
        self.assertEqual(result.content_type, "text/html; charset=utf-8")
        self.assertEqual(opener.urls, [START])
        self.assertEqual(opener.timeouts, [7.5])
        self.assertEqual(resolver.calls, ["www.example.no"])

    def test_request_headers_and_method(self):
        client, opener = make_client({START: ok()})
        client.get(START)
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("User-agent"), "MerediaResearch/1.0")
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertFalse(request.has_header("Authorization"))
        self.assertFalse(request.has_header("Cookie"))

    def test_custom_user_agent(self):
        client, opener = make_client({START: ok()}, user_agent="TestBot/2")
        client.get(START)
        self.assertEqual(opener.requests[0].get_header("User-agent"), "TestBot/2")

    def test_non_ascii_path_is_encoded_before_the_request(self):
        client, opener = make_client({"https://www.example.no/bl%C3%A5b%C3%A6r?q=%C3%A6": ok()})
        result = client.get("https://www.example.no/bl\u00e5b\u00e6r?q=\u00e6#del")
        self.assertEqual(result.url, "https://www.example.no/bl%C3%A5b%C3%A6r?q=%C3%A6")

    def test_ip_literal_url_is_fetched_in_canonical_form(self):
        client, opener = make_client({f"http://{PUBLIC_V4}/": ok()}, resolver=no_dns)
        self.assertEqual(client.get("http://0x5db8d822").url, f"http://{PUBLIC_V4}/")

    def test_success_statuses_are_returned(self):
        for status in (200, 201, 204, 206):
            with self.subTest(status=status):
                client, _ = make_client({START: FakeResponse(status, b"", {})})
                result = client.get(START)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.content_type)
                self.assertEqual(result.body, "")

    def test_404_and_410_are_returned_with_their_body(self):
        for form in FORMS:
            for status in sorted(PASSTHROUGH_STATUSES):
                with self.subTest(form=form, status=status):
                    outcome = status_outcome(form, START, status, b"Fant ikke siden", {"Content-Type": "text/html"})
                    client, _ = make_client({START: outcome})
                    result = client.get(START)
                    self.assertEqual((result.status, result.body), (status, "Fant ikke siden"))

    def test_elapsed_ms_comes_from_the_injected_clock(self):
        clock = FakeClock()
        client, _ = make_client({START: ok()}, clock=clock, on_open=lambda: clock.advance(0.25))
        self.assertEqual(client.get(START).elapsed_ms, 250)

    def test_response_is_closed(self):
        response = ok()
        client, _ = make_client({START: response})
        client.get(START)
        self.assertTrue(response.closed)

    def test_responses_are_closed_when_the_status_raises(self):
        for form in FORMS:
            with self.subTest(form=form):
                outcome = status_outcome(form, START, 500, b"x")
                client, _ = make_client({START: outcome})
                with self.assertRaises(AutomationError):
                    client.get(START)
                if form == "returned":
                    self.assertTrue(outcome.closed)
                else:
                    self.assertTrue(outcome.fp is None or outcome.fp.closed)


class BlockedUrlTests(unittest.TestCase):
    def assert_blocked(self, url, resolver=None, error_type=E.VALIDATION_ERROR):
        client, opener = make_client({}, resolver=resolver)
        with self.assertRaises(AutomationError) as ctx:
            client.get(url)
        self.assertEqual(ctx.exception.error_type, error_type, url)
        self.assertEqual(opener.requests, [], f"a request was made for {url}")
        return ctx.exception

    def test_every_blocked_host_is_refused_before_any_request(self):
        for host in BLOCKED_HOSTS:
            with self.subTest(host=host):
                self.assert_blocked(url_for(host), resolver=no_dns)

    def test_blocked_host_with_port_and_path(self):
        self.assert_blocked("http://LOCALHOST:8080/admin", resolver=no_dns)
        self.assert_blocked("https://169.254.169.254/latest/meta-data/iam", resolver=no_dns)

    def test_name_with_a_private_address_among_public_ones(self):
        resolver = FakeResolver({"mixed.example.no": [PUBLIC_V4, "10.0.0.1"]})
        self.assert_blocked("https://mixed.example.no/", resolver=resolver)
        self.assertEqual(resolver.calls, ["mixed.example.no"])

    def test_credentials_ftp_and_file(self):
        self.assert_blocked("http://user:pass@www.example.no/")
        self.assert_blocked("ftp://www.example.no/file.txt")
        self.assert_blocked("file:///etc/passwd")

    def test_dns_failure_is_temporary_and_makes_no_request(self):
        resolver = FakeResolver({"www.example.no": socket.gaierror(-3, "Temporary failure in name resolution")})
        self.assert_blocked(START, resolver=resolver, error_type=E.TEMPORARY_ERROR)

    def test_bad_arguments_are_refused_before_any_request(self):
        client, opener = make_client({START: ok()})
        for kwargs in ({"timeout": 0}, {"timeout": -1.0}, {"max_bytes": 0}, {"max_bytes": -5}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(AutomationError) as ctx:
                    client.get(START, **kwargs)
                self.assertEqual(ctx.exception.error_type, E.VALIDATION_ERROR)
        self.assertEqual(opener.requests, [])

    def test_bad_constructor_arguments(self):
        for kwargs in ({"max_redirects": -1}, {"user_agent": ""}, {"user_agent": "Bot\r\nX-Evil: 1"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(AutomationError) as ctx:
                    UrllibHttpClient(opener=FakeOpener({}), **kwargs)
                self.assertEqual(ctx.exception.error_type, E.VALIDATION_ERROR)


class RedirectTests(unittest.TestCase):
    BLOCKED_TARGETS = [
        "http://10.0.0.5/admin",
        "http://127.0.0.1:8080/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://internal.example.no/",
        "ftp://www.example.no/file",
        "file:///etc/passwd",
        "http://user:pw@www.example.no/",
        "http://www.example.no@10.0.0.5/",
        "//10.0.0.5/x",
    ]

    def resolver(self):
        return FakeResolver({"internal.example.no": ["10.0.0.7"]})

    def test_redirect_to_a_blocked_target_is_refused_without_a_request(self):
        for form in FORMS:
            for target in self.BLOCKED_TARGETS:
                with self.subTest(form=form, target=target):
                    outcome = status_outcome(form, START, 302, headers={"Location": target})
                    client, opener = make_client({START: outcome}, resolver=self.resolver())
                    with self.assertRaises(AutomationError) as ctx:
                        client.get(START)
                    self.assertEqual(ctx.exception.error_type, E.VALIDATION_ERROR)
                    self.assertEqual(opener.urls, [START])

    def test_redirect_to_a_blocked_target_deep_in_a_chain(self):
        routes = {
            START: redirect("/a"),
            "https://www.example.no/a": redirect("http://169.254.169.254/latest/meta-data/"),
        }
        client, opener = make_client(routes)
        with self.assertRaises(AutomationError) as ctx:
            client.get(START)
        self.assertEqual(ctx.exception.error_type, E.VALIDATION_ERROR)
        self.assertEqual(opener.urls, [START, "https://www.example.no/a"])

    def test_redirect_message_has_the_host_but_not_the_secret_query(self):
        client, _ = make_client({START: redirect(f"http://10.0.0.5/x?{SECRET_QUERY}")})
        with self.assertRaises(AutomationError) as ctx:
            client.get(START)
        self.assertIn("redirect target", str(ctx.exception))
        self.assertNotIn(SECRET_QUERY, str(ctx.exception))

    def test_redirect_with_control_characters_is_refused(self):
        client, opener = make_client({START: redirect("http://www.exa\tmple.no/\r\nX: y")})
        with self.assertRaises(AutomationError) as ctx:
            client.get(START)
        self.assertEqual(ctx.exception.error_type, E.VALIDATION_ERROR)
        self.assertEqual(opener.urls, [START])

    def test_all_redirect_statuses_are_followed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                routes = {START: redirect("https://www.example.no/ny", status), "https://www.example.no/ny": ok()}
                client, opener = make_client(routes)
                result = client.get(START)
                self.assertEqual(result.url, "https://www.example.no/ny")
                self.assertEqual(opener.urls, [START, "https://www.example.no/ny"])

    def test_redirect_raised_as_http_error_is_followed(self):
        routes = {START: http_error(START, 301, headers={"Location": "/ny"}), "https://www.example.no/ny": ok()}
        client, _ = make_client(routes)
        self.assertEqual(client.get(START).url, "https://www.example.no/ny")

    def test_relative_and_cross_host_locations(self):
        routes = {
            START: redirect("sub/side"),
            "https://www.example.no/sub/side": redirect("../opp"),
            "https://www.example.no/opp": redirect("https://other.example.no/landing"),
            "https://other.example.no/landing": ok(b"endelig"),
        }
        resolver = FakeResolver()
        client, opener = make_client(routes, resolver=resolver)
        result = client.get(START)
        self.assertEqual((result.url, result.body), ("https://other.example.no/landing", "endelig"))
        self.assertEqual(resolver.calls, ["www.example.no"] * 3 + ["other.example.no"])

    def test_redirect_responses_are_closed(self):
        first = redirect("/ny")
        client, _ = make_client({START: first, "https://www.example.no/ny": ok()})
        client.get(START)
        self.assertTrue(first.closed)

    def chain(self, redirects: int) -> dict:
        """START redirects through /hop1 .. /hop{redirects}, which answers 200."""
        urls = [START] + [f"https://www.example.no/hop{n}" for n in range(1, redirects + 1)]
        routes = {url: redirect(nxt) for url, nxt in zip(urls, urls[1:])}
        routes[urls[-1]] = ok(b"framme")
        return routes

    def test_exactly_max_redirects_hops_are_allowed(self):
        client, opener = make_client(self.chain(5))
        self.assertEqual(client.get(START).body, "framme")
        self.assertEqual(len(opener.requests), 6)

    def test_one_hop_too_many_is_an_error(self):
        client, opener = make_client(self.chain(6))
        with self.assertRaises(AutomationError) as ctx:
            client.get(START)
        self.assertEqual(ctx.exception.error_type, E.EXTERNAL_SERVICE_ERROR)
        self.assertIn("too many redirects", str(ctx.exception))
        self.assertEqual(len(opener.requests), 6)
        self.assertNotIn("https://www.example.no/hop6", opener.urls)

    def test_max_redirects_is_configurable(self):
        for limit, redirects, allowed in ((0, 1, False), (0, 0, True), (2, 2, True), (2, 3, False)):
            with self.subTest(limit=limit, redirects=redirects):
                client, _ = make_client(self.chain(redirects), max_redirects=limit)
                if allowed:
                    self.assertEqual(client.get(START).status, 200)
                else:
                    with self.assertRaises(AutomationError) as ctx:
                        client.get(START)
                    self.assertEqual(ctx.exception.error_type, E.EXTERNAL_SERVICE_ERROR)

    def test_redirect_loop_is_detected(self):
        a, b = START, "https://www.example.no/b"
        client, opener = make_client({a: redirect(b), b: redirect(a)})
        with self.assertRaises(AutomationError) as ctx:
            client.get(a)
        self.assertEqual(ctx.exception.error_type, E.EXTERNAL_SERVICE_ERROR)
        self.assertIn("loop", str(ctx.exception))
        self.assertEqual(opener.urls, [a, b])

    def test_redirect_to_itself_is_a_loop(self):
        client, opener = make_client({START: redirect(START)})
        with self.assertRaises(AutomationError) as ctx:
            client.get(START)
        self.assertIn("loop", str(ctx.exception))
        self.assertEqual(len(opener.requests), 1)

    def test_loop_through_equivalent_spellings_is_detected(self):
        client, opener = make_client({START: redirect("HTTPS://WWW.EXAMPLE.NO./#x")})
        with self.assertRaises(AutomationError) as ctx:
            client.get(START)
        self.assertIn("loop", str(ctx.exception))

    def test_redirect_without_location_is_an_error(self):
        for response in (FakeResponse(302, b"", {}), FakeResponse(302, b"", {"Location": "  "})):
            with self.subTest(headers=dict(response.headers)):
                client, _ = make_client({START: response})
                with self.assertRaises(AutomationError) as ctx:
                    client.get(START)
                self.assertEqual(ctx.exception.error_type, E.EXTERNAL_SERVICE_ERROR)
                self.assertIn("Location", str(ctx.exception))


class BodyTests(unittest.TestCase):
    def test_body_is_cut_at_max_bytes_and_never_read_beyond(self):
        response = ok(b"a" * 1000)
        client, _ = make_client({START: response})
        result = client.get(START, max_bytes=100)
        self.assertEqual(result.body, "a" * 100)
        self.assertEqual(result.size_bytes, 100)
        self.assertEqual(response.bytes_read, 100)
        self.assertLessEqual(response.max_amount, 100)

    def test_a_response_that_ignores_the_read_size_still_cannot_exceed_max_bytes(self):
        client, _ = make_client({START: OverfullResponse(200, b"e" * 1000, {})})
        result = client.get(START, max_bytes=100)
        self.assertEqual((result.size_bytes, len(result.body)), (100, 100))

    def test_large_body_is_read_in_chunks_up_to_the_limit(self):
        response = ok(b"b" * 500_000)
        client, _ = make_client({START: response})
        result = client.get(START, max_bytes=150_000)
        self.assertEqual((result.size_bytes, len(result.body)), (150_000, 150_000))
        self.assertEqual(response.bytes_read, 150_000)

    def test_body_exactly_at_and_below_the_limit_is_complete(self):
        for size in (100, 99, 0):
            with self.subTest(size=size):
                client, _ = make_client({START: ok(b"c" * size)})
                result = client.get(START, max_bytes=100)
                self.assertEqual((result.size_bytes, len(result.body)), (size, size))

    def test_default_limit_is_two_megabytes(self):
        response = ok(b"d" * 2_100_000)
        client, _ = make_client({START: response})
        self.assertEqual(client.get(START).size_bytes, 2_000_000)

    def test_truncation_inside_a_multibyte_character_does_not_fail(self):
        client, _ = make_client({START: ok("\u00e6".encode() * 100)})
        result = client.get(START, max_bytes=101)
        self.assertEqual(result.size_bytes, 101)
        self.assertTrue(result.body.startswith("\u00e6" * 50))

    def test_charset_from_content_type(self):
        text = "bl\u00e5b\u00e6r og r\u00f8dgr\u00f8t"
        cases = [
            ("text/html; charset=iso-8859-1", text.encode("latin-1")),
            ('text/html; charset="ISO-8859-1"', text.encode("latin-1")),
            ("text/html;CHARSET=utf-8;x=y", text.encode("utf-8")),
            ("text/html; charset=utf-16", text.encode("utf-16")),
            ("text/html; boundary=x; charset=windows-1252", text.encode("cp1252")),
        ]
        for content_type, raw in cases:
            with self.subTest(content_type=content_type):
                client, _ = make_client({START: FakeResponse(200, raw, {"Content-Type": content_type})})
                result = client.get(START)
                self.assertEqual(result.body, text)
                self.assertEqual(result.content_type, content_type)

    def test_utf8_is_the_default(self):
        text = "bl\u00e5b\u00e6r"
        for headers in ({}, {"Content-Type": "text/html"}, {"Content-Type": "text/html; charset="}):
            with self.subTest(headers=headers):
                client, _ = make_client({START: FakeResponse(200, text.encode(), headers)})
                self.assertEqual(client.get(START).body, text)

    def test_unknown_or_unusable_charset_falls_back_to_utf8(self):
        text = "bl\u00e5b\u00e6r"
        for charset in ("x-no-such-charset", "rot13", "base64", "zip"):
            with self.subTest(charset=charset):
                headers = {"Content-Type": f"text/html; charset={charset}"}
                client, _ = make_client({START: FakeResponse(200, text.encode(), headers)})
                self.assertEqual(client.get(START).body, text)

    def test_bad_bytes_are_replaced_not_raised(self):
        client, _ = make_client({START: FakeResponse(200, b"ok \xff\xfe slutt", {})})
        self.assertEqual(client.get(START).body, "ok \ufffd\ufffd slutt")


class StatusTests(unittest.TestCase):
    EXPECTED = {
        400: E.VALIDATION_ERROR,
        401: E.AUTHENTICATION_ERROR,
        402: E.VALIDATION_ERROR,
        403: E.AUTHENTICATION_ERROR,
        405: E.VALIDATION_ERROR,
        406: E.VALIDATION_ERROR,
        408: E.TEMPORARY_ERROR,
        409: E.VALIDATION_ERROR,
        418: E.VALIDATION_ERROR,
        429: E.RATE_LIMIT,
        451: E.VALIDATION_ERROR,
        499: E.VALIDATION_ERROR,
        500: E.EXTERNAL_SERVICE_ERROR,
        501: E.EXTERNAL_SERVICE_ERROR,
        502: E.EXTERNAL_SERVICE_ERROR,
        503: E.EXTERNAL_SERVICE_ERROR,
        504: E.EXTERNAL_SERVICE_ERROR,
        599: E.EXTERNAL_SERVICE_ERROR,
    }

    def test_every_status_maps_to_its_error_type(self):
        for form in FORMS:
            for status, error_type in self.EXPECTED.items():
                with self.subTest(form=form, status=status):
                    client, _ = make_client({START: status_outcome(form, START, status, b"body")})
                    with self.assertRaises(AutomationError) as ctx:
                        client.get(START)
                    self.assertEqual(ctx.exception.error_type, error_type)
                    self.assertIn(str(status), str(ctx.exception))

    def test_statuses_that_are_neither_success_nor_a_followed_redirect_are_errors(self):
        for status in (100, 300, 304, 305, 306, 600):
            with self.subTest(status=status):
                client, _ = make_client({START: FakeResponse(status, b"", {"Location": "/x"})})
                with self.assertRaises(AutomationError) as ctx:
                    client.get(START)
                self.assertEqual(ctx.exception.error_type, E.EXTERNAL_SERVICE_ERROR)

    def test_which_errors_can_be_retried(self):
        retryable = {E.TEMPORARY_ERROR, E.RATE_LIMIT, E.EXTERNAL_SERVICE_ERROR}
        for status, error_type in self.EXPECTED.items():
            with self.subTest(status=status):
                client, _ = make_client({START: FakeResponse(status)})
                with self.assertRaises(AutomationError) as ctx:
                    client.get(START)
                self.assertEqual(ctx.exception.retryable, error_type in retryable)


class NetworkErrorTests(unittest.TestCase):
    def failing(self, outcome, resolver=None):
        client, opener = make_client({START: outcome}, resolver=resolver)
        with self.assertRaises(AutomationError) as ctx:
            client.get(START)
        return ctx.exception

    def test_timeouts_are_temporary(self):
        for outcome in (
            TimeoutError("timed out"),
            socket.timeout("timed out"),
            urllib.error.URLError(socket.timeout("timed out")),
            urllib.error.URLError(TimeoutError("_ssl.c: The handshake operation timed out")),
        ):
            with self.subTest(outcome=repr(outcome)):
                error = self.failing(outcome)
                self.assertEqual(error.error_type, E.TEMPORARY_ERROR)
                self.assertIn("timeout", str(error))
                self.assertNotIn("tls", str(error).lower())

    def test_timeout_while_reading_the_body_is_temporary(self):
        error = self.failing(FakeResponse(200, b"x", {}, read_error=TimeoutError("timed out")))
        self.assertEqual(error.error_type, E.TEMPORARY_ERROR)

    def test_connection_problems_are_temporary(self):
        for outcome in (
            ConnectionResetError(104, "Connection reset by peer"),
            urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer")),
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
            urllib.error.URLError(OSError(113, "No route to host")),
            http.client.RemoteDisconnected("Remote end closed connection without response"),
            BrokenPipeError(32, "Broken pipe"),
        ):
            with self.subTest(outcome=repr(outcome)):
                error = self.failing(outcome)
                self.assertEqual(error.error_type, E.TEMPORARY_ERROR)
                self.assertNotIn("tls", str(error).lower())

    def test_connection_problems_while_reading_the_body_are_temporary(self):
        for read_error in (
            ConnectionResetError(104, "Connection reset by peer"),
            http.client.IncompleteRead(b"abc", 100),
        ):
            with self.subTest(read_error=type(read_error).__name__):
                error = self.failing(FakeResponse(200, b"x", {}, read_error=read_error))
                self.assertEqual(error.error_type, E.TEMPORARY_ERROR)

    def test_dns_failure_at_connect_time_is_temporary(self):
        outcome = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        error = self.failing(outcome)
        self.assertEqual(error.error_type, E.TEMPORARY_ERROR)
        self.assertNotIn("tls", str(error).lower())

    def test_dns_failure_in_the_check_is_temporary(self):
        resolver = FakeResolver({"www.example.no": socket.gaierror(-2, "Name or service not known")})
        error = self.failing(ok(), resolver=resolver)
        self.assertEqual(error.error_type, E.TEMPORARY_ERROR)

    def test_tls_and_certificate_problems_say_tls(self):
        for outcome in (
            urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed: self signed certificate")),
            urllib.error.URLError(ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")),
            ssl.SSLCertVerificationError(1, "certificate verify failed: Hostname mismatch"),
            ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
        ):
            with self.subTest(outcome=type(outcome).__name__):
                error = self.failing(outcome)
                self.assertEqual(error.error_type, E.EXTERNAL_SERVICE_ERROR)
                self.assertIn("tls", str(error))

    def test_tls_problem_while_reading_the_body(self):
        error = self.failing(FakeResponse(200, b"x", {}, read_error=ssl.SSLError("bad record mac")))
        self.assertEqual(error.error_type, E.EXTERNAL_SERVICE_ERROR)
        self.assertIn("tls", str(error))

    def test_only_tls_errors_mention_tls(self):
        outcomes = [
            FakeResponse(500),
            FakeResponse(302, b"", {}),
            http.client.BadStatusLine("garbage"),
            TimeoutError(),
            ConnectionResetError(),
        ]
        for outcome in outcomes:
            with self.subTest(outcome=repr(outcome)):
                self.assertNotIn("tls", str(self.failing(outcome)).lower())

    def test_malformed_response_is_an_external_service_error(self):
        for outcome in (http.client.BadStatusLine("garbage"), http.client.LineTooLong("header line")):
            with self.subTest(outcome=type(outcome).__name__):
                self.assertEqual(self.failing(outcome).error_type, E.EXTERNAL_SERVICE_ERROR)

    def test_unknown_reason_is_an_external_service_error(self):
        error = self.failing(urllib.error.URLError("unknown url type: x"))
        self.assertEqual(error.error_type, E.EXTERNAL_SERVICE_ERROR)

    def test_responses_are_closed_after_a_read_failure(self):
        response = FakeResponse(200, b"x", {}, read_error=ConnectionResetError())
        self.failing(response)
        self.assertTrue(response.closed)


class NoLeakTests(unittest.TestCase):
    """Errors must not carry response bodies, request headers or the query string of the URL."""

    URL = f"https://www.example.no/side?{SECRET_QUERY}"

    def failing(self, routes, **kwargs) -> AutomationError:
        client, _ = make_client(routes, user_agent=SECRET_AGENT, **kwargs)
        with self.assertRaises(AutomationError) as ctx:
            client.get(self.URL)
        return ctx.exception

    def assert_clean(self, error: AutomationError):
        text = f"{error} {error.message} {error!r}"
        for secret in (SECRET_BODY, SECRET_AGENT, SECRET_QUERY, "Accept-Encoding", "User-Agent"):
            self.assertNotIn(secret, text)

    def test_status_errors(self):
        canonical = f"https://www.example.no/side?{SECRET_QUERY}"
        for form in FORMS:
            for status in self.__class__.statuses():
                with self.subTest(form=form, status=status):
                    outcome = status_outcome(form, canonical, status, SECRET_BODY.encode(), {"X-Debug": SECRET_BODY})
                    self.assert_clean(self.failing({canonical: outcome}))

    @staticmethod
    def statuses():
        return (400, 401, 403, 408, 418, 429, 500, 502, 503, 304)

    def test_network_errors(self):
        canonical = f"https://www.example.no/side?{SECRET_QUERY}"
        outcomes = [
            TimeoutError(f"timed out {SECRET_BODY}"),
            ConnectionResetError(f"reset {SECRET_BODY} {SECRET_AGENT}"),
            urllib.error.URLError(socket.gaierror(-2, f"{SECRET_BODY} {SECRET_QUERY}")),
            ssl.SSLCertVerificationError(1, f"certificate verify failed {SECRET_BODY}"),
            http.client.BadStatusLine(SECRET_BODY),
            urllib.error.URLError(f"{SECRET_BODY} {SECRET_QUERY}"),
            FakeResponse(200, b"x", {}, read_error=ConnectionResetError(SECRET_BODY)),
        ]
        for outcome in outcomes:
            with self.subTest(outcome=repr(outcome)):
                self.assert_clean(self.failing({canonical: outcome}))

    def test_redirect_errors(self):
        canonical = f"https://www.example.no/side?{SECRET_QUERY}"
        loop = {canonical: redirect(canonical)}
        self.assert_clean(self.failing(loop))
        missing = {canonical: FakeResponse(302, SECRET_BODY.encode(), {})}
        self.assert_clean(self.failing(missing))
        blocked = {canonical: FakeResponse(302, SECRET_BODY.encode(), {"Location": f"http://10.0.0.1/?{SECRET_QUERY}"})}
        self.assert_clean(self.failing(blocked))
        endless = {canonical: redirect(f"/a?{SECRET_QUERY}")}
        endless.update({f"https://www.example.no/a?{SECRET_QUERY}": redirect(f"/b?{SECRET_QUERY}")})
        self.assert_clean(self.failing(endless, max_redirects=1))

    def test_validation_errors_before_the_request(self):
        for url in (
            f"http://user:{SECRET_BODY}@www.example.no/?{SECRET_QUERY}",
            f"ftp://www.example.no/?{SECRET_QUERY}",
            f"http://127.0.0.1/?{SECRET_QUERY}",
            f"http://www.example.no:99999/?{SECRET_QUERY}",
        ):
            with self.subTest(url=url):
                client, _ = make_client({}, user_agent=SECRET_AGENT)
                with self.assertRaises(AutomationError) as ctx:
                    client.get(url)
                self.assert_clean(ctx.exception)


class DeadlineTests(unittest.TestCase):
    def test_slow_body_hits_the_total_deadline(self):
        clock = FakeClock()
        response = FakeResponse(200, b"x" * 500_000, {}, on_read=lambda: clock.advance(11))
        client, _ = make_client({START: response}, clock=clock)
        with self.assertRaises(AutomationError) as ctx:
            client.get(START, timeout=10)
        self.assertEqual(ctx.exception.error_type, E.TEMPORARY_ERROR)
        self.assertIn("timeout", str(ctx.exception))
        self.assertLess(response.bytes_read, 500_000)
        self.assertTrue(response.closed)

    def test_slow_redirect_chain_hits_the_total_deadline(self):
        clock = FakeClock()
        urls = [START] + [f"https://www.example.no/h{n}" for n in range(1, 6)]
        routes = {url: redirect(nxt) for url, nxt in zip(urls, urls[1:])}
        routes[urls[-1]] = ok()
        client, opener = make_client(routes, clock=clock, on_open=lambda: clock.advance(11))
        with self.assertRaises(AutomationError) as ctx:
            client.get(START, timeout=10)
        self.assertEqual(ctx.exception.error_type, E.TEMPORARY_ERROR)
        self.assertEqual(len(opener.requests), 3)

    def test_fast_responses_are_not_affected(self):
        clock = FakeClock()
        client, _ = make_client({START: ok(b"y" * 1000)}, clock=clock, on_open=lambda: clock.advance(2))
        self.assertEqual(client.get(START, timeout=10).size_bytes, 1000)


if __name__ == "__main__":
    unittest.main()
