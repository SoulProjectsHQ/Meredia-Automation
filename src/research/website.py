"""Website analysis for research. Plain code, no LLM.

analyze_html is a pure function: HTML text in, WebsiteAnalysis out. It reads no clock and uses no
network. fetch_and_analyze is a thin wrapper that fetches the homepage through an HttpClient and
hands the body to analyze_html. Tests pass a fake HttpClient and never touch the network.

Only the HTML source is analysed. Nothing runs JavaScript, so a page that builds its content in
the browser looks emptier here than it does to a visitor. A browser-rendered source can be
plugged in later without changing this module: any HttpClient whose get() returns the rendered
HTML as the response body works. No browser dependency exists today.

What the analysis is and is not:

- Observations are factual statements about the HTML source, written in Norwegian. They never
  claim a security problem, a mistake by the company or a lost customer. improvement_signal marks
  the ones that may point to an area where Meredia can contribute. It is an input for a human
  and for scoring, not a verdict.
- Mobile experience is not measured. The viewport check only tells whether the tag exists.
- E-mail addresses are only those the company lists publicly on its own site, in mailto links
  or in visible text. They are recorded together with the observation source_url and are never
  treated as verified. Observation texts do not repeat the address, only counts and domains.
- When the input was cut (cap, parse budget or a body that hit the fetch limit) the page is
  incomplete, so absence cannot be established. The observations that say "not found" are then
  left out instead of guessed.
- Input is capped at MAX_HTML_CHARS and parsing at MAX_PARSE_EVENTS. All regular expressions use
  bounded quantifiers. html.parser itself is lenient but can be slow on hostile input, such as
  megabytes of unterminated tags, so the budgets keep the worst case at a few seconds.

Fields for content (title, h1_count and so on) are only filled when the page was analysed. For an
unreachable site or a 404 or 410 homepage they keep their neutral defaults and the observations
are the source of truth. elapsed_ms and size_bytes are None when no response was received.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from html.parser import HTMLParser
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from src.automation.errors import AutomationError, ErrorType
from src.crm.validation import is_valid_email_format, normalize_domain
from src.integrations.http import HttpClient, HttpResponse

# Limits. The fetch limits equal the HttpClient protocol defaults.
MAX_HTML_CHARS = 2_000_000
MAX_PARSE_EVENTS = 200_000
MAX_ITEMS = 10
MAX_LINK_TEXT = 80
FETCH_TIMEOUT_SECONDS = 10.0
FETCH_MAX_BYTES = 2_000_000

# Thresholds for observations.
SLOW_RESPONSE_MS = 3000
LARGE_PAGE_BYTES = 1_500_000
OLD_COPYRIGHT_YEARS = 3

# Free or private mail domains and Norwegian internet providers. A contact address on one of these
# (or on a subdomain of one) triggers private_email_domain. Lowercase only.
PRIVATE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "hotmail.no",
        "outlook.com",
        "outlook.no",
        "live.com",
        "live.no",
        "msn.com",
        "yahoo.com",
        "yahoo.no",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "gmx.com",
        "gmx.net",
        "mail.com",
        "proton.me",
        "protonmail.com",
        "online.no",
        "broadpark.no",
        "frisurf.no",
        "getmail.no",
        "start.no",
        "c2i.net",
        "lyse.net",
        "tele2.no",
        "altibox.no",
        "chello.no",
        "tiscali.no",
    }
)

OBSERVATION_CODES = frozenset(
    {
        "unreachable",
        "homepage_not_found",
        "no_https",
        "missing_title",
        "missing_meta_description",
        "missing_viewport",
        "missing_h1",
        "images_missing_alt",
        "no_cta_found",
        "no_contact_page_link",
        "private_email_domain",
        "old_copyright_year",
        "slow_response",
        "large_page",
        "platform_detected",
        "contact_email_found",
    }
)

# Lowercase phrases. Matched on whole words, so "ring" does not match "markedsforing" and
# "book" does not match "facebook".
_CTA_PHRASES = (
    "kontakt oss",
    "ta kontakt",
    "kom i kontakt",
    "snakk med oss",
    "book",
    "booking",
    "bestill",
    "ring",
    "få tilbud",
    "be om tilbud",
    "forespør tilbud",
    "send melding",
    "send oss en melding",
    "send forespørsel",
    "bli kunde",
    "kom i gang",
    "gratis konsultasjon",
    "avtal møte",
    "kjøp nå",
    "contact us",
    "get in touch",
    "get a quote",
    "request a quote",
)
_CTA_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(
        r"\s+".join(re.escape(word) for word in phrase.split())
        for phrase in sorted(_CTA_PHRASES, key=len, reverse=True)
    )
    + r")(?!\w)"
)

_CONTACT_RE = re.compile(r"(?<!\w)(?:kontakt|contact)")

# (needle, display name). A needle must start a word in the generator text.
_PLATFORMS = (
    ("wordpress", "WordPress"),
    ("wix", "Wix"),
    ("squarespace", "Squarespace"),
    ("webflow", "Webflow"),
    ("shopify", "Shopify"),
    ("framer", "Framer"),
    ("joomla", "Joomla"),
    ("drupal", "Drupal"),
    ("typo3", "TYPO3"),
    ("prestashop", "PrestaShop"),
    ("magento", "Magento"),
    ("weebly", "Weebly"),
    ("jimdo", "Jimdo"),
    ("umbraco", "Umbraco"),
    ("hubspot", "HubSpot"),
)
_PLATFORM_RE = tuple((re.compile(r"(?<![a-z0-9])" + re.escape(needle)), name) for needle, name in _PLATFORMS)

# Bounded quantifiers and a lookbehind keep the scan linear on any input.
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,5}"
)
_FILE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js"})
_MAX_EMAIL_CANDIDATES = 500

# The year has to sit right behind the marker, so "(c) Eksempel AS, 2000 Lillestrom" (a postal
# code) is not read as a year. A range like 2015-2024 yields both ends.
_COPYRIGHT_RE = re.compile(
    r"(?:©|\(c\)|copyright|opphavsrett)[\s:.,]{0,6}((?:19|20)\d{2})(?!\d)"
    r"(?:\s{0,2}[-\u2013\u2014/]\s{0,2}((?:19|20)\d{2})(?!\d))?",
    re.IGNORECASE,
)
_MIN_COPYRIGHT_YEAR = 1990
_MAX_COPYRIGHT_MATCHES = 1000

_IGNORED_TAGS = frozenset({"script", "style", "noscript"})
_MAX_TITLE_CHARS = 500
_MAX_CLICKABLE_CHARS = 300
_MAX_URL_CHARS = 300


@dataclass(frozen=True)
class Observation:
    """One factual statement about the page. text is Norwegian and never accuses."""

    code: str
    text: str
    source_url: str
    improvement_signal: bool


@dataclass(frozen=True)
class WebsiteAnalysis:
    url: str  # the URL that was requested
    reachable: bool
    final_url: str | None = None  # after redirects, None when nothing was received
    status: int | None = None
    https: bool = False  # True when final_url uses https
    title: str | None = None
    meta_description: str | None = None
    has_viewport: bool = False
    lang: str | None = None
    generator: str | None = None  # raw content of the first meta generator tag
    h1_count: int = 0
    images_total: int = 0
    images_missing_alt: int = 0  # img tags without an alt attribute. alt="" counts as present.
    ctas: list[str] = field(default_factory=list)
    contact_emails: list[str] = field(default_factory=list)  # lowercase, UNVERIFIED
    private_email_addresses: list[str] = field(default_factory=list)  # subset of contact_emails
    contact_page_links: list[str] = field(default_factory=list)  # absolute URLs on the same site
    copyright_year: int | None = None  # the newest copyright year found in the text
    elapsed_ms: int | None = None
    size_bytes: int | None = None
    observations: list[Observation] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Plain dict that json.dumps accepts."""
        return asdict(self)


# ---------------------------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------------------------


def _clean(text: str, limit: int) -> str:
    """Printable text with whitespace collapsed, cut to limit characters."""
    text = "".join(ch for ch in text[: limit * 4] if ch.isprintable() or ch.isspace())
    return " ".join(text.split())[:limit]


def _attr_map(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    """Attribute names in lowercase, first one wins, a missing value becomes an empty string."""
    result: dict[str, str] = {}
    for name, value in attrs:
        result.setdefault(name.lower(), value or "")
    return result


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[1]


def _accept_email(raw: str) -> str | None:
    """Lowercase, validated address or None. File names like logo@2x.png are refused."""
    email = raw.strip().lower()
    if len(email) > 254 or not is_valid_email_format(email):
        return None
    if email.rsplit(".", 1)[1] in _FILE_EXTENSIONS:
        return None
    return email


def _is_private_domain(domain: str) -> bool:
    labels = domain.lower().split(".")
    return any(".".join(labels[i:]) in PRIVATE_EMAIL_DOMAINS for i in range(len(labels) - 1))


def _find_copyright_year(text: str, current_year: int) -> int | None:
    """The newest plausible copyright year in the text. The newest wins so that an old credit
    line, for example from a map widget, does not make a maintained page look old."""
    best: int | None = None
    for count, match in enumerate(_COPYRIGHT_RE.finditer(text)):
        if count >= _MAX_COPYRIGHT_MATCHES:
            break
        for group in match.groups():
            if group is None:
                continue
            year = int(group)
            if _MIN_COPYRIGHT_YEAR <= year <= current_year + 1 and (best is None or year > best):
                best = year
    return best


def _scan_text_emails(text: str, found: list[str]) -> None:
    """Add addresses from visible text to found, at most MAX_ITEMS in total."""
    for count, match in enumerate(_EMAIL_RE.finditer(text)):
        if count >= _MAX_EMAIL_CANDIDATES or len(found) >= MAX_ITEMS:
            break
        email = _accept_email(match.group(0))
        if email is not None and email not in found:
            found.append(email)


def _detect_platform(generators: list[str]) -> str | None:
    """Display name for the platform a generator tag names. Unknown generators come back as
    their own cleaned text. None when there is no generator."""
    for generator in generators:
        lowered = generator.lower()
        for pattern, name in _PLATFORM_RE:
            if pattern.search(lowered):
                return name
    return generators[0][:60] if generators else None


def _comma(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


# ---------------------------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------------------------


class _StopParsing(Exception):
    """Raised from a handler when the parse budget is used up."""


@dataclass
class _Clickable:
    kind: str  # "a" or "button"
    href: str
    chunks: list[str] = field(default_factory=list)
    chars: int = 0


class _PageParser(HTMLParser):
    """Collects what analyze_html needs in one pass.

    Everything inside script, style and noscript is ignored, tags included. Tag and attribute
    names arrive lowercase from html.parser, and attribute values are compared in lowercase.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.base_domain = normalize_domain(base_url)
        try:
            self._base_page = urldefrag(base_url)[0].rstrip("/")
        except ValueError:
            self._base_page = base_url.rstrip("/")
        self.events = 0
        self.stopped_early = False
        self.title: str | None = None
        self.meta_description: str | None = None
        self.has_viewport = False
        self.lang: str | None = None
        self.generators: list[str] = []
        self.h1_count = 0
        self.images_total = 0
        self.images_missing_alt = 0
        self.ctas: list[str] = []
        self.emails: list[str] = []
        self.contact_links: list[str] = []
        self.text_chunks: list[str] = []
        self._text_chars = 0
        self._ignore_depth = 0
        self._svg_depth = 0
        self._title_chunks: list[str] = []
        self._title_chars = 0
        self._title_seen = False
        self._in_title = False
        self._clickable: _Clickable | None = None

    # Budget. Every parser event counts, comments and declarations included.

    def _tick(self) -> None:
        self.events += 1
        if self.events > MAX_PARSE_EVENTS:
            raise _StopParsing

    def handle_comment(self, data: str) -> None:
        self._tick()

    def handle_decl(self, decl: str) -> None:
        self._tick()

    def handle_pi(self, data: str) -> None:
        self._tick()

    def unknown_decl(self, data: str) -> None:
        self._tick()

    # Tags

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tick()
        self._end_title()  # a title holds no tags, so any tag means it was not closed
        if tag in _IGNORED_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return
        if tag == "svg":
            self._svg_depth += 1
            return
        a = _attr_map(attrs)
        if tag == "html":
            if self.lang is None:
                self.lang = _clean(a.get("lang") or a.get("xml:lang", ""), 20).lower() or None
        elif tag == "title":
            if not self._svg_depth and not self._title_seen:
                self._title_seen = True
                self._in_title = True
        elif tag == "meta":
            self._handle_meta(a)
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "img":
            self.images_total += 1
            if "alt" not in a:
                self.images_missing_alt += 1
        elif tag == "a":
            self._close_clickable()
            href = a.get("href", "")[:2000]
            self._add_mailto(href)
            self._clickable = _Clickable("a", href)
        elif tag == "button":
            self._close_clickable()
            self._clickable = _Clickable("button", "")
        elif tag == "input":
            if a.get("type", "").strip().lower() in ("submit", "button"):
                self._add_cta(_clean(a.get("value", ""), MAX_LINK_TEXT + 1))

    def handle_endtag(self, tag: str) -> None:
        self._tick()
        self._end_title()
        if tag in _IGNORED_TAGS:
            self._ignore_depth = max(0, self._ignore_depth - 1)
            return
        if self._ignore_depth:
            return
        if tag == "svg":
            self._svg_depth = max(0, self._svg_depth - 1)
        elif tag in ("a", "button"):
            self._close_clickable()

    def handle_data(self, data: str) -> None:
        self._tick()
        if self._ignore_depth:
            return
        if self._in_title:
            if self._title_chars < _MAX_TITLE_CHARS:
                self._title_chunks.append(data[:_MAX_TITLE_CHARS])
                self._title_chars += len(data)
            return
        if self._text_chars < MAX_HTML_CHARS:
            self.text_chunks.append(data)
            self._text_chars += len(data)
        clickable = self._clickable
        if clickable is not None and clickable.chars < _MAX_CLICKABLE_CHARS:
            clickable.chunks.append(data[:_MAX_CLICKABLE_CHARS])
            clickable.chars += len(data)

    def finish(self) -> None:
        """Close what is still open. Call once after parsing."""
        self._end_title()
        self._close_clickable()

    # Pieces

    def _end_title(self) -> None:
        if self._in_title:
            self._in_title = False
            self.title = _clean(" ".join(self._title_chunks), 200) or None

    def _handle_meta(self, a: dict[str, str]) -> None:
        name = a.get("name", "").strip().lower()
        if name == "description":
            if self.meta_description is None:
                self.meta_description = _clean(a.get("content", ""), 300) or None
        elif name == "viewport":
            self.has_viewport = True
        elif name == "generator":
            content = _clean(a.get("content", ""), 120)
            if content and len(self.generators) < 5:
                self.generators.append(content)

    def _add_mailto(self, href: str) -> None:
        stripped = href.strip()
        if stripped[:7].lower() != "mailto:" or len(self.emails) >= MAX_ITEMS:
            return
        target = unquote(stripped[7:].split("?", 1)[0][:500])
        for candidate in target.split(",")[:5]:
            email = _accept_email(candidate)
            if email is not None and email not in self.emails and len(self.emails) < MAX_ITEMS:
                self.emails.append(email)

    def _close_clickable(self) -> None:
        clickable, self._clickable = self._clickable, None
        if clickable is None:
            return
        text = _clean(" ".join(clickable.chunks), MAX_LINK_TEXT + 1)
        if len(text) > MAX_LINK_TEXT:
            text = ""  # a paragraph inside a link is not a label
        self._add_cta(text)
        if clickable.kind == "a":
            self._add_contact_link(clickable.href, text)

    def _add_cta(self, text: str) -> None:
        if not text or len(text) > MAX_LINK_TEXT or len(self.ctas) >= MAX_ITEMS:
            return
        if _CTA_RE.search(text.casefold()) is None:
            return
        if all(text.casefold() != known.casefold() for known in self.ctas):
            self.ctas.append(text)

    def _add_contact_link(self, href: str, text: str) -> None:
        """Record a link to a contact page or contact section on the same site."""
        target = href.strip()
        if not target or target == "#" or len(self.contact_links) >= MAX_ITEMS:
            return
        in_text = bool(text) and _CONTACT_RE.search(text.casefold()) is not None
        if not in_text and _CONTACT_RE.search(target.casefold()) is None:
            return  # cheap check first, resolving URLs is the expensive part
        try:
            absolute = urljoin(self.base_url, target)
            parts = urlsplit(absolute)
            host = parts.hostname
            page = urldefrag(absolute)[0].rstrip("/")
        except ValueError:
            return
        if parts.scheme not in ("http", "https") or not host or len(absolute) > _MAX_URL_CHARS:
            return
        if self.base_domain is not None and normalize_domain(host) != self.base_domain:
            return
        if not parts.fragment and page == self._base_page:
            return  # a link to the page itself
        in_href = _CONTACT_RE.search(f"{parts.path} {parts.fragment}".casefold()) is not None
        if (in_text or in_href) and absolute not in self.contact_links:
            self.contact_links.append(absolute)


# ---------------------------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------------------------


def _observations(
    parser: _PageParser,
    *,
    source: str,
    https: bool,
    elapsed_ms: int,
    size_bytes: int,
    copyright_year: int | None,
    current_year: int,
    private_addresses: list[str],
    complete: bool,
) -> list[Observation]:
    found: list[Observation] = []

    def add(code: str, text: str, improvement_signal: bool) -> None:
        found.append(Observation(code, text, source, improvement_signal))

    if not https:
        add("no_https", "Nettsiden ble hentet uten HTTPS (endelig adresse starter med http://).", True)
    if complete:
        if parser.title is None:
            add("missing_title", "Vi fant ingen sidetittel (title-tag) i HTML-kilden.", True)
        if parser.meta_description is None:
            add(
                "missing_meta_description",
                "Vi fant ingen beskrivelse (meta description) i HTML-kilden. "
                "Den kan vises som tekst i søkeresultater.",
                True,
            )
        if not parser.has_viewport:
            add(
                "missing_viewport",
                "Vi fant ingen viewport-tag i HTML-kilden. Mobilvisning er ikke målt, "
                "vi har bare sjekket om viewport-taggen finnes.",
                True,
            )
        if parser.h1_count == 0:
            add("missing_h1", "Vi fant ingen H1-overskrift i HTML-kilden.", True)
    if parser.images_missing_alt:
        add(
            "images_missing_alt",
            f"{parser.images_missing_alt} av {parser.images_total} bilder i HTML-kilden mangler alt-tekst.",
            True,
        )
    if complete:
        if not parser.ctas:
            add(
                "no_cta_found",
                "Vi fant ingen tydelig handlingsoppfordring (for eksempel «Kontakt oss» eller «Bestill») "
                "i lenker eller knapper i HTML-kilden.",
                True,
            )
        if not parser.contact_links:
            add("no_contact_page_link", "Vi fant ingen lenke til en kontaktside i HTML-kilden.", True)
    if private_addresses:
        domains = ", ".join(sorted({_email_domain(email) for email in private_addresses}))
        add(
            "private_email_domain",
            f"Minst én kontaktadresse på nettsiden bruker et gratis eller privat e-postdomene ({domains}). "
            "Dette kan være et område hvor Meredia kan bidra.",
            True,
        )
    if copyright_year is not None and current_year - copyright_year >= OLD_COPYRIGHT_YEARS:
        add(
            "old_copyright_year",
            f"Nettsiden oppgir opphavsrett for {copyright_year}. Det kan tyde på at siden ikke har vært "
            "gjennomgått på en stund, men det er ikke bekreftet.",
            True,
        )
    if elapsed_ms > SLOW_RESPONSE_MS:
        add(
            "slow_response",
            f"Hovedsiden brukte {_comma(elapsed_ms / 1000)} sekunder på å svare i én enkelt måling. "
            "Det kan skyldes midlertidig belastning.",
            True,
        )
    if size_bytes > LARGE_PAGE_BYTES:
        add(
            "large_page",
            f"HTML-filen for hovedsiden er {_comma(size_bytes / 1_000_000)} MB. "
            "Dette gjelder bare HTML-kilden, ikke bilder og skript.",
            True,
        )
    platform = _detect_platform(parser.generators)
    if platform is not None:
        add("platform_detected", f"Nettsiden oppgir {platform} som plattform i meta generator.", False)
    if parser.emails:
        count = len(parser.emails)
        add(
            "contact_email_found",
            "Vi fant én offentlig oppført e-postadresse på nettsiden. Den er ikke verifisert."
            if count == 1
            else f"Vi fant {count} offentlig oppførte e-postadresser på nettsiden. De er ikke verifisert.",
            False,
        )
    return found


def analyze_html(
    html: str,
    *,
    url: str,
    final_url: str | None,
    status: int | None,
    elapsed_ms: int,
    size_bytes: int,
    current_year: int,
) -> WebsiteAnalysis:
    """Analyse one HTML page. Pure: no clock, no network.

    Malformed markup never raises. Input longer than MAX_HTML_CHARS is cut. Every observation
    carries final_url as source_url (url when final_url is missing).
    """
    if not isinstance(html, str):
        raise AutomationError(ErrorType.VALIDATION_ERROR, "html must be a string")
    final = final_url or url
    too_long = len(html) > MAX_HTML_CHARS
    parser = _PageParser(final)
    try:
        parser.feed(html[:MAX_HTML_CHARS])
        parser.close()
    except _StopParsing:
        parser.stopped_early = True
    except Exception:
        # html.parser is lenient, but a bug in it must not stop a research run. Keep what was read.
        parser.stopped_early = True
    parser.finish()

    text = " ".join(parser.text_chunks)
    _scan_text_emails(text, parser.emails)
    private_addresses = [email for email in parser.emails if _is_private_domain(_email_domain(email))]
    copyright_year = _find_copyright_year(text, current_year)
    https = final.lower().startswith("https://")
    # A body that reached the size cap may have been cut by the fetch layer, so treat it as incomplete.
    complete = not (parser.stopped_early or too_long or size_bytes >= MAX_HTML_CHARS)

    return WebsiteAnalysis(
        url=url,
        reachable=True,
        final_url=final,
        status=status,
        https=https,
        title=parser.title,
        meta_description=parser.meta_description,
        has_viewport=parser.has_viewport,
        lang=parser.lang,
        generator=parser.generators[0] if parser.generators else None,
        h1_count=parser.h1_count,
        images_total=parser.images_total,
        images_missing_alt=parser.images_missing_alt,
        ctas=list(parser.ctas),
        contact_emails=list(parser.emails),
        private_email_addresses=private_addresses,
        contact_page_links=list(parser.contact_links),
        copyright_year=copyright_year,
        elapsed_ms=elapsed_ms,
        size_bytes=size_bytes,
        observations=_observations(
            parser,
            source=final,
            https=https,
            elapsed_ms=elapsed_ms,
            size_bytes=size_bytes,
            copyright_year=copyright_year,
            current_year=current_year,
            private_addresses=private_addresses,
            complete=complete,
        ),
    )


# ---------------------------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------------------------


def _homepage_not_found(response: HttpResponse, url: str) -> WebsiteAnalysis:
    """404 or 410 on the homepage. The error page is not analysed, it says nothing about the site."""
    final = response.url or url
    https = final.lower().startswith("https://")
    observations = [
        Observation(
            "homepage_not_found",
            f"Hovedsiden svarte med HTTP {response.status} (ikke funnet), så innholdet er ikke analysert.",
            final,
            True,
        )
    ]
    if not https:
        observations.append(
            Observation(
                "no_https", "Nettsiden ble hentet uten HTTPS (endelig adresse starter med http://).", final, True
            )
        )
    return WebsiteAnalysis(
        url=url,
        reachable=True,
        final_url=final,
        status=response.status,
        https=https,
        elapsed_ms=response.elapsed_ms,
        size_bytes=response.size_bytes,
        observations=observations,
    )


def _from_response(response: HttpResponse, url: str, current_year: int) -> WebsiteAnalysis:
    if response.status in (404, 410):
        return _homepage_not_found(response, url)
    return analyze_html(
        response.body,
        url=url,
        final_url=response.url or url,
        status=response.status,
        elapsed_ms=response.elapsed_ms,
        size_bytes=response.size_bytes,
        current_year=current_year,
    )


def _unreachable(url: str, https_error: AutomationError, http_error: AutomationError) -> WebsiteAnalysis:
    """Both attempts failed. Only the error types are named, never messages or response bodies."""
    text = (
        f"Nettsiden kunne ikke hentes, verken via HTTPS (feiltype: {https_error.error_type}) "
        f"eller HTTP (feiltype: {http_error.error_type}). Dette bekrefter ikke at nettsiden er nede."
    )
    # Not an improvement signal: the failure may be on our side or temporary.
    return WebsiteAnalysis(url=url, reachable=False, observations=[Observation("unreachable", text, url, False)])


def _note_https_failure(result: WebsiteAnalysis, https_error: AutomationError) -> WebsiteAnalysis:
    """Make the no_https text say why HTTPS was not used, when the HTTPS attempt failed first."""
    text = (
        f"Nettsiden ble ikke hentet via HTTPS (feiltype: {https_error.error_type}), men svarte via HTTP. "
        "Årsaken er ikke undersøkt."
    )
    observations = [replace(o, text=text) if o.code == "no_https" else o for o in result.observations]
    return replace(result, observations=observations)


def fetch_and_analyze(http: HttpClient, website: str | None, *, today: date | None = None) -> WebsiteAnalysis:
    """Fetch the homepage of website and analyse it.

    Tries https://domain/ first. If that raises AutomationError it tries http://domain/. When
    only http works, https is False and no_https is recorded. When both fail the result has
    reachable False and an unreachable observation. A 404 or 410 homepage is reachable with a
    homepage_not_found observation. An unusable website raises AutomationError(VALIDATION_ERROR)
    before any request. today only supplies the year for the copyright check.

    The result has https True when the final URL uses https, also when it was reached by redirect
    from the http attempt. Errors other than AutomationError are not caught.
    """
    domain = normalize_domain(website)
    if domain is None:
        raise AutomationError(ErrorType.VALIDATION_ERROR, "website is not a valid domain or URL")
    current_year = (today or date.today()).year
    https_url = f"https://{domain}/"
    http_url = f"http://{domain}/"

    try:
        response = http.get(https_url, timeout=FETCH_TIMEOUT_SECONDS, max_bytes=FETCH_MAX_BYTES)
    except AutomationError as exc:
        https_error = exc
    else:
        return _from_response(response, https_url, current_year)

    try:
        response = http.get(http_url, timeout=FETCH_TIMEOUT_SECONDS, max_bytes=FETCH_MAX_BYTES)
    except AutomationError as exc:
        return _unreachable(https_url, https_error, exc)
    return _note_https_failure(_from_response(response, http_url, current_year), https_error)
