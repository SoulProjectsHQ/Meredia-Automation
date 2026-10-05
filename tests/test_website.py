import json
import time
import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from src.automation.errors import AutomationError, ErrorType
from src.integrations.http import HttpResponse
from src.research.website import (
    FETCH_MAX_BYTES,
    FETCH_TIMEOUT_SECONDS,
    MAX_HTML_CHARS,
    MAX_ITEMS,
    MAX_PARSE_EVENTS,
    OBSERVATION_CODES,
    PRIVATE_EMAIL_DOMAINS,
    Observation,
    WebsiteAnalysis,
    analyze_html,
    fetch_and_analyze,
)

HTTPS_URL = "https://eksempel.no/"
HTTP_URL = "http://eksempel.no/"
YEAR = 2026
TODAY = date(2026, 10, 6)

GOOD_PAGE = """<!DOCTYPE html>
<html lang="nb-NO"><head>
<meta charset="utf-8">
<title>Eksempel Regnskap AS</title>
<meta name="description" content="Regnskap og rådgivning for små bedrifter i Trondheim">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="WordPress 6.4.2">
</head><body>
<nav><a href="/">Hjem</a> <a href="/kontakt">Kontakt</a></nav>
<h1>Regnskap uten styr</h1>
<img src="kontor.jpg" alt="Kontoret vårt"><img src="strek.png" alt="">
<a class="btn" href="/kontakt">Kontakt oss</a>
<p>Skriv til <a href="mailto:post@eksempel.no">post@eksempel.no</a></p>
<footer>&copy; 2026 Eksempel Regnskap AS</footer>
</body></html>"""

BARE_PAGE = "<html><body>Hei</body></html>"


def analyze(html, **overrides):
    args = dict(
        url=HTTPS_URL,
        final_url=HTTPS_URL,
        status=200,
        elapsed_ms=400,
        size_bytes=len(html),
        current_year=YEAR,
    )
    args.update(overrides)
    return analyze_html(html, **args)


def page(body="", head=""):
    """A small valid page, so that only what a test adds can trigger observations."""
    return (
        '<html lang="nb"><head><title>Eksempel</title>'
        '<meta name="description" content="Beskrivelse">'
        '<meta name="viewport" content="width=device-width">'
        f"{head}</head><body><h1>Eksempel</h1>{body}</body></html>"
    )


def codes(analysis):
    return [o.code for o in analysis.observations]


def observation(analysis, code):
    for o in analysis.observations:
        if o.code == code:
            return o
    return None


def response(url=HTTPS_URL, body=GOOD_PAGE, status=200, elapsed_ms=400, size_bytes=None):
    size = len(body.encode("utf-8")) if size_bytes is None else size_bytes
    return HttpResponse(
        url=url, status=status, body=body, elapsed_ms=elapsed_ms, size_bytes=size, content_type="text/html"
    )


class FakeHttp:
    """HttpClient stand-in. routes maps a URL to an HttpResponse, or to an exception to raise."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.kwargs = []

    def get(self, url, *, timeout=10.0, max_bytes=2_000_000):
        self.calls.append(url)
        self.kwargs.append({"timeout": timeout, "max_bytes": max_bytes})
        result = self.routes[url]
        if isinstance(result, Exception):
            raise result
        return result


class GoodPageTests(unittest.TestCase):
    def test_complete_page_has_no_improvement_signals(self):
        a = analyze(GOOD_PAGE)
        self.assertTrue(a.reachable)
        self.assertTrue(a.https)
        self.assertEqual(a.status, 200)
        self.assertEqual(a.title, "Eksempel Regnskap AS")
        self.assertEqual(a.meta_description, "Regnskap og rådgivning for små bedrifter i Trondheim")
        self.assertTrue(a.has_viewport)
        self.assertEqual(a.lang, "nb-no")
        self.assertEqual(a.generator, "WordPress 6.4.2")
        self.assertEqual(a.h1_count, 1)
        self.assertEqual((a.images_total, a.images_missing_alt), (2, 0))
        self.assertEqual(a.ctas, ["Kontakt oss"])
        self.assertEqual(a.contact_emails, ["post@eksempel.no"])
        self.assertEqual(a.private_email_addresses, [])
        self.assertEqual(a.contact_page_links, ["https://eksempel.no/kontakt"])
        self.assertEqual(a.copyright_year, 2026)
        self.assertEqual(sorted(codes(a)), ["contact_email_found", "platform_detected"])
        self.assertFalse(any(o.improvement_signal for o in a.observations))

    def test_title_is_unescaped_and_whitespace_collapsed(self):
        a = analyze("<title>\n  Eksempel &amp; Sønn   AS </title>")
        self.assertEqual(a.title, "Eksempel & Sønn AS")

    def test_title_inside_svg_is_not_the_page_title(self):
        a = analyze("<html><body><svg><title>ikon</title></svg></body></html>")
        self.assertIsNone(a.title)
        self.assertIn("missing_title", codes(a))

    def test_empty_title_and_description_count_as_missing(self):
        a = analyze('<title>  </title><meta name="description" content="  ">')
        self.assertIsNone(a.title)
        self.assertIsNone(a.meta_description)
        self.assertIn("missing_title", codes(a))
        self.assertIn("missing_meta_description", codes(a))

    def test_h1_and_images(self):
        a = analyze(
            '<H1>En</H1><h1>To</h1><img src=a.png><IMG SRC=b.png ALT="Logo"><img src=c.png alt=""><img src=d.png alt>'
        )
        self.assertEqual(a.h1_count, 2)
        self.assertEqual((a.images_total, a.images_missing_alt), (4, 1))
        o = observation(a, "images_missing_alt")
        self.assertIn("1 av 4", o.text)
        self.assertTrue(o.improvement_signal)

    def test_attribute_and_tag_names_are_case_insensitive(self):
        a = analyze(
            '<HTML LANG="NB"><HEAD><META NAME="Viewport" CONTENT="width=device-width">'
            '<META NAME="DESCRIPTION" CONTENT="Beskrivelse"><TITLE>T</TITLE></HEAD>'
            '<BODY><H1>x</H1><A HREF="MAILTO:Post@Eksempel.NO">Kontakt oss</A></BODY></HTML>'
        )
        self.assertTrue(a.has_viewport)
        self.assertEqual(a.meta_description, "Beskrivelse")
        self.assertEqual(a.lang, "nb")
        self.assertEqual(a.contact_emails, ["post@eksempel.no"])
        self.assertEqual(a.ctas, ["Kontakt oss"])


class BarePageTests(unittest.TestCase):
    def test_bare_page_reports_everything_missing(self):
        a = analyze(BARE_PAGE)
        self.assertEqual(
            codes(a),
            [
                "missing_title",
                "missing_meta_description",
                "missing_viewport",
                "missing_h1",
                "no_cta_found",
                "no_contact_page_link",
            ],
        )
        self.assertTrue(all(o.improvement_signal for o in a.observations))
        self.assertIsNone(a.title)
        self.assertIsNone(a.meta_description)
        self.assertFalse(a.has_viewport)
        self.assertIsNone(a.lang)
        self.assertIsNone(a.generator)
        self.assertEqual((a.h1_count, a.images_total, a.images_missing_alt), (0, 0, 0))
        self.assertEqual((a.ctas, a.contact_emails, a.contact_page_links), ([], [], []))
        self.assertIsNone(a.copyright_year)

    def test_empty_string_does_not_raise(self):
        a = analyze("")
        self.assertTrue(a.reachable)
        self.assertIn("missing_title", codes(a))

    def test_viewport_text_says_mobile_was_not_measured(self):
        text = observation(analyze(BARE_PAGE), "missing_viewport").text
        self.assertIn("Mobilvisning er ikke målt", text)
        self.assertIn("viewport-taggen", text)

    def test_every_observation_carries_the_final_url(self):
        html = BARE_PAGE + "<img src=a.png>"
        a = analyze(html, url="http://eksempel.no/", final_url="http://www.eksempel.no/", elapsed_ms=5000)
        self.assertIn("no_https", codes(a))
        self.assertIn("slow_response", codes(a))
        self.assertTrue(a.observations)
        self.assertTrue(all(o.source_url == "http://www.eksempel.no/" for o in a.observations))
        self.assertEqual(a.url, "http://eksempel.no/")
        self.assertEqual(a.final_url, "http://www.eksempel.no/")

    def test_non_string_html_is_a_validation_error(self):
        for bad in (None, b"<html></html>", 5):
            with self.subTest(bad=bad), self.assertRaises(AutomationError) as ctx:
                analyze_html(
                    bad, url=HTTPS_URL, final_url=HTTPS_URL, status=200, elapsed_ms=1, size_bytes=1, current_year=YEAR
                )
            self.assertEqual(ctx.exception.error_type, ErrorType.VALIDATION_ERROR)


class MalformedHtmlTests(unittest.TestCase):
    CASES = [
        "<",
        ">",
        "<<<>>>",
        "<html><body><div><p>unclosed <a href=\"",
        "<a href='x",
        "<title>Hei",
        "<!--",
        "<!-- aldri avsluttet",
        "<![CDATA[ x",
        "<?php echo 1; ?>",
        "</div></div></p>",
        "<img alt",
        "<meta name=viewport content=",
        "&#xZZ; &bogus; &#99999999; &",
        "\x00<p>\x00</p>",
        "<scr<script>ipt>",
        "<h1><h1><h1>",
        "<svg><title>x",
        "<noscript><a href=/kontakt>",
        "<button><a><button>x",
        "<a><a><a>tekst",
        "<p " + 'a="b" ' * 1000,
        "<a href=\"" + "x" * 100_000,
    ]

    def test_malformed_markup_never_raises(self):
        for html in self.CASES:
            with self.subTest(html=html[:40]):
                a = analyze(html)
                self.assertIsInstance(a, WebsiteAnalysis)
                json.dumps(a.to_dict())

    def test_unclosed_anchor_is_still_read(self):
        a = analyze("<a href=kontakt>Kontakt oss")
        self.assertEqual(a.ctas, ["Kontakt oss"])
        self.assertEqual(a.contact_page_links, ["https://eksempel.no/kontakt"])


class IgnoredContentTests(unittest.TestCase):
    def test_script_style_and_noscript_are_ignored(self):
        html = (
            "<html><head><title>T</title>"
            '<script>var m = "ghost@eksempel.no"; document.write(\'<h1>x</h1><img src=a.png>'
            '<a href="mailto:ghost2@eksempel.no">Kontakt oss</a> © 2001\');</script>'
            '<style>.a::after { content: "Bestill ring@eksempel.no © 2001"; }</style>'
            "</head><body>"
            '<noscript><img src="pixel.png"><h1>Aktiver JavaScript</h1>'
            '<a href="/kontakt">Kontakt oss</a> post@eksempel.no © 2001</noscript>'
            "<p>Hei</p></body></html>"
        )
        a = analyze(html)
        self.assertEqual(a.contact_emails, [])
        self.assertEqual(a.h1_count, 0)
        self.assertEqual(a.images_total, 0)
        self.assertEqual(a.ctas, [])
        self.assertEqual(a.contact_page_links, [])
        self.assertIsNone(a.copyright_year)
        self.assertIn("missing_h1", codes(a))
        self.assertIn("no_cta_found", codes(a))

    def test_self_closing_script_does_not_hide_the_page(self):
        a = analyze('<script src="app.js"/><h1>Synlig</h1><a href="/x">Kontakt oss</a>')
        self.assertEqual(a.h1_count, 1)
        self.assertEqual(a.ctas, ["Kontakt oss"])

    def test_text_in_title_is_not_visible_text(self):
        a = analyze("<title>post@eksempel.no © 2001</title><p>Hei</p>")
        self.assertEqual(a.contact_emails, [])
        self.assertIsNone(a.copyright_year)


class EmailTests(unittest.TestCase):
    def test_mailto_and_visible_text(self):
        html = page(
            '<a href="mailto:post@eksempel.no">Skriv til oss</a>'
            '<a href="mailto:Ola.Nordmann@Gmail.com?subject=Hei%20p%C3%A5&body=x">Ola</a>'
            '<a href="mailto:a@eksempel.no,b@eksempel.no">To</a>'
            '<a href="mailto:POST@eksempel.no">Igjen</a>'
            '<a href="mailto:ikke-en-adresse">Ugyldig</a>'
            "<p>Eller send e-post til Kontakt@Eksempel.no, takk. Bildet logo@2x.png og foo@bar er ikke adresser.</p>"
            '<script>var e = "skjult@eksempel.no";</script>'
        )
        a = analyze(html)
        self.assertEqual(
            a.contact_emails,
            ["post@eksempel.no", "ola.nordmann@gmail.com", "a@eksempel.no", "b@eksempel.no", "kontakt@eksempel.no"],
        )
        self.assertEqual(a.private_email_addresses, ["ola.nordmann@gmail.com"])
        found = observation(a, "contact_email_found")
        self.assertIn("5 offentlig oppførte e-postadresser", found.text)
        self.assertIn("ikke verifisert", found.text)
        self.assertFalse(found.improvement_signal)
        self.assertEqual(found.source_url, HTTPS_URL)

    def test_single_address_text(self):
        a = analyze(page("<p>post@eksempel.no</p>"))
        self.assertIn("én offentlig oppført e-postadresse", observation(a, "contact_email_found").text)

    def test_private_domains(self):
        for domain in ("gmail.com", "hotmail.com", "outlook.com", "online.no", "Yahoo.no", "mail.online.no"):
            with self.subTest(domain=domain):
                a = analyze(page(f'<a href="mailto:firma@{domain}">Skriv</a>'))
                self.assertEqual(a.private_email_addresses, [f"firma@{domain.lower()}"])
                o = observation(a, "private_email_domain")
                self.assertIsNotNone(o)
                self.assertTrue(o.improvement_signal)
                self.assertIn(domain.lower(), o.text)
                self.assertNotIn("firma@", o.text)  # the address itself is not repeated

    def test_own_domain_is_not_private(self):
        for address in ("post@eksempel.no", "ola@gmail.eksempel.no", "ola@notgmail.com", "ola@gmail.com.eksempel.no"):
            with self.subTest(address=address):
                a = analyze(page(f"<p>{address}</p>"))
                self.assertEqual(a.contact_emails, [address])
                self.assertEqual(a.private_email_addresses, [])
                self.assertNotIn("private_email_domain", codes(a))

    def test_private_observation_lists_each_domain_once(self):
        a = analyze(page("<p>a@gmail.com b@gmail.com c@hotmail.com</p>"))
        text = observation(a, "private_email_domain").text
        self.assertEqual(text.count("gmail.com"), 1)
        self.assertIn("gmail.com, hotmail.com", text)

    def test_at_most_ten_addresses(self):
        links = "".join(f'<a href="mailto:person{i}@eksempel.no">x</a>' for i in range(15))
        a = analyze(page(links))
        self.assertEqual(len(a.contact_emails), MAX_ITEMS)
        self.assertEqual(a.contact_emails[0], "person0@eksempel.no")
        text = "<p>" + " ".join(f"tekst{i}@eksempel.no" for i in range(15)) + "</p>"
        self.assertEqual(len(analyze(page(text)).contact_emails), MAX_ITEMS)

    def test_private_domain_list_is_lowercase_and_has_the_usual_ones(self):
        self.assertTrue({"gmail.com", "hotmail.com", "outlook.com", "online.no"} <= PRIVATE_EMAIL_DOMAINS)
        self.assertTrue(all(d == d.lower() for d in PRIVATE_EMAIL_DOMAINS))


class CopyrightTests(unittest.TestCase):
    def test_years_found(self):
        cases = [
            ("© 2019 Eksempel AS", 2019),
            ("&copy; 2019 Eksempel AS", 2019),
            ("Copyright © 2018 - 2020 Eksempel", 2020),
            ("Copyright 2019 Eksempel", 2019),
            ("(c) 2017 Eksempel", 2017),
            ("©2016", 2016),
            ("© 2015-2024 Eksempel AS", 2024),
            ("© 2015 \u2013 2022 Eksempel AS", 2022),
            ("© 2019 Eksempel AS. Kartdata © 2012 Kartverket", 2019),
            ("Kartdata © 2012 Kartverket. © 2025 Eksempel AS", 2025),
            ("Opphavsrett 2021", 2021),
        ]
        for text, year in cases:
            with self.subTest(text=text):
                self.assertEqual(analyze(page(f"<footer>{text}</footer>")).copyright_year, year)

    def test_no_year_found(self):
        cases = [
            "© Eksempel AS, 2000 Lillestrøm",  # a postal code, not a year
            "© 2035 Eksempel AS",  # in the future
            "© 1985 Eksempel AS",  # implausibly old
            "Etablert 2019, alle rettigheter forbeholdt",  # no copyright marker
            "copyrighted 2019",
            "©",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(analyze(page(f"<footer>{text}</footer>")).copyright_year)

    def test_old_year_boundary_is_three_years(self):
        for year, old in ((2019, True), (2023, True), (2024, False), (2026, False), (2027, False)):
            with self.subTest(year=year):
                a = analyze(page(f"<footer>© {year} Eksempel AS</footer>"))
                self.assertEqual("old_copyright_year" in codes(a), old)

    def test_old_year_observation(self):
        a = analyze(page("<footer>© 2019 Eksempel AS</footer>"))
        o = observation(a, "old_copyright_year")
        self.assertIn("2019", o.text)
        self.assertIn("ikke bekreftet", o.text)
        self.assertTrue(o.improvement_signal)
        self.assertEqual(o.source_url, HTTPS_URL)

    def test_current_year_is_a_parameter(self):
        html = page("<footer>© 2019 Eksempel AS</footer>")
        self.assertIn("old_copyright_year", codes(analyze(html, current_year=2026)))
        self.assertNotIn("old_copyright_year", codes(analyze(html, current_year=2021)))


class CtaTests(unittest.TestCase):
    def test_links_buttons_and_inputs(self):
        body = (
            '<a href="/k">Kontakt oss</a>'
            "<button>Bestill time</button>"
            "<a>Ring oss 22 33 44 55</a>"
            '<input type="submit" value="Send melding">'
            "<a>Få tilbud</a>"
            "<a>Book et møte</a>"
            "<a>Bli kunde</a>"
            "<a>Ta kontakt</a>"
        )
        self.assertEqual(
            analyze(page(body)).ctas,
            [
                "Kontakt oss",
                "Bestill time",
                "Ring oss 22 33 44 55",
                "Send melding",
                "Få tilbud",
                "Book et møte",
                "Bli kunde",
                "Ta kontakt",
            ],
        )

    def test_things_that_are_not_ctas(self):
        body = (
            "<a>Facebook</a><a>Markedsføring</a><a>Kontakt</a><a>Programmering og drift</a>"
            "<button>Meny</button><a>Bookmarks</a>"
            "<p>Kontakt oss i dag</p>"
            '<input type="text" value="Kontakt oss">'
            "<a>Dette er en lang lenketekst som egentlig er et helt avsnitt og som nevner at du kan kontakt oss når som helst</a>"
        )
        a = analyze(page(body))
        self.assertEqual(a.ctas, [])
        self.assertIn("no_cta_found", codes(a))

    def test_script_content_is_not_a_cta(self):
        a = analyze(page("<script>document.write('<a>Kontakt oss</a>')</script>"))
        self.assertEqual(a.ctas, [])

    def test_nested_markup_and_whitespace(self):
        a = analyze(page('<a href="/x"><span>Kontakt</span> <b>oss</b></a><button>\n  Bestill\n   nå </button>'))
        self.assertEqual(a.ctas, ["Kontakt oss", "Bestill nå"])

    def test_duplicates_ignoring_case_and_cap(self):
        a = analyze(page("<a>Kontakt oss</a><a>KONTAKT OSS</a><a>kontakt  oss</a>"))
        self.assertEqual(a.ctas, ["Kontakt oss"])
        many = "".join(f"<a>Book møte {i}</a>" for i in range(15))
        capped = analyze(page(many)).ctas
        self.assertEqual(len(capped), MAX_ITEMS)
        self.assertEqual(capped[0], "Book møte 0")

    def test_found_cta_means_no_observation(self):
        a = analyze(page("<a>Kontakt oss</a>"))
        self.assertNotIn("no_cta_found", codes(a))


class ContactLinkTests(unittest.TestCase):
    def test_links_to_contact_pages(self):
        body = (
            '<a href="/kontakt">Kontakt</a>'
            '<a href="/om/oss">Kontakt oss</a>'
            '<a href="https://www.eksempel.no/contact-us">Contact</a>'
            '<a href="#kontakt">Ta kontakt</a>'
            '<a href="/kontakt"><img src="x.png" alt=""></a>'
            '<a href="https://facebook.com/eksempel/kontakt">Kontakt</a>'
            '<a href="mailto:post@eksempel.no">Kontakt oss</a>'
            '<a href="tel:+4722334455">Kontakt</a>'
            '<a href="javascript:void(0)">Kontakt</a>'
            '<a href="#">Kontakt</a>'
            '<a href="/">Kontakt</a>'
            '<a href="/produkter">Produkter</a>'
        )
        a = analyze(page(body))
        self.assertEqual(
            a.contact_page_links,
            [
                "https://eksempel.no/kontakt",
                "https://eksempel.no/om/oss",
                "https://www.eksempel.no/contact-us",
                "https://eksempel.no/#kontakt",
            ],
        )
        self.assertNotIn("no_contact_page_link", codes(a))

    def test_relative_links_resolve_against_the_final_url(self):
        a = analyze(page('<a href="/kontakt">Kontakt</a>'), final_url="https://www.eksempel.no/")
        self.assertEqual(a.contact_page_links, ["https://www.eksempel.no/kontakt"])

    def test_missing_contact_link_is_reported(self):
        a = analyze(page('<a href="/produkter">Produkter</a><a href="mailto:post@eksempel.no">Skriv</a>'))
        self.assertEqual(a.contact_page_links, [])
        o = observation(a, "no_contact_page_link")
        self.assertTrue(o.improvement_signal)

    def test_at_most_ten_links(self):
        links = "".join(f'<a href="/kontakt-{i}">Kontakt</a>' for i in range(15))
        self.assertEqual(len(analyze(page(links)).contact_page_links), MAX_ITEMS)


class GeneratorTests(unittest.TestCase):
    def test_known_platforms(self):
        cases = [
            ('<meta name="generator" content="WordPress 6.4.2">', "WordPress"),
            ('<META NAME="GENERATOR" CONTENT="Wix.com Website Builder">', "Wix"),
            ('<meta content="Webflow" name="generator">', "Webflow"),
            ('<meta name="generator" content="Squarespace">', "Squarespace"),
            ('<meta name="generator" content="Framer 1a2b3c">', "Framer"),
            ('<meta name="generator" content="Shopify">', "Shopify"),
        ]
        for tag, platform in cases:
            with self.subTest(platform=platform):
                a = analyze(page(head=tag))
                o = observation(a, "platform_detected")
                self.assertIsNotNone(o)
                self.assertIn(platform, o.text)
                self.assertFalse(o.improvement_signal)
                self.assertEqual(o.source_url, HTTPS_URL)

    def test_raw_generator_is_kept(self):
        a = analyze(page(head='<meta name="generator" content="WordPress 6.4.2">'))
        self.assertEqual(a.generator, "WordPress 6.4.2")

    def test_unknown_generator_is_named_as_written(self):
        a = analyze(page(head='<meta name="generator" content="Hugo 0.120.4">'))
        self.assertEqual(a.generator, "Hugo 0.120.4")
        self.assertIn("Hugo 0.120.4", observation(a, "platform_detected").text)

    def test_no_or_empty_generator(self):
        for head in ("", '<meta name="generator" content="  ">', '<meta name="generator">'):
            with self.subTest(head=head):
                a = analyze(page(head=head))
                self.assertIsNone(a.generator)
                self.assertNotIn("platform_detected", codes(a))

    def test_first_generator_is_kept_and_platform_comes_from_any(self):
        head = '<meta name="generator" content="Elementor 3.1"><meta name="generator" content="WordPress 6.4">'
        a = analyze(page(head=head))
        self.assertEqual(a.generator, "Elementor 3.1")
        self.assertIn("WordPress", observation(a, "platform_detected").text)

    def test_word_start_is_required(self):
        a = analyze(page(head='<meta name="generator" content="Swixel 1.0">'))
        self.assertIn("Swixel 1.0", observation(a, "platform_detected").text)
        self.assertNotIn("Wix", observation(a, "platform_detected").text)


class SpeedAndSizeTests(unittest.TestCase):
    def test_thresholds(self):
        html = page()
        cases = [
            ("slow_response", dict(elapsed_ms=3000), False),
            ("slow_response", dict(elapsed_ms=3001), True),
            ("large_page", dict(size_bytes=1_500_000), False),
            ("large_page", dict(size_bytes=1_500_001), True),
        ]
        for code, overrides, expected in cases:
            with self.subTest(code=code, **overrides):
                o = observation(analyze(html, **overrides), code)
                self.assertEqual(o is not None, expected)
                if o is not None:
                    self.assertTrue(o.improvement_signal)

    def test_texts_use_norwegian_decimals(self):
        a = analyze(page(), elapsed_ms=3500, size_bytes=1_600_000)
        self.assertIn("3,5 sekunder", observation(a, "slow_response").text)
        self.assertIn("1,6 MB", observation(a, "large_page").text)

    def test_no_https(self):
        a = analyze(page(), url=HTTP_URL, final_url=HTTP_URL)
        self.assertFalse(a.https)
        o = observation(a, "no_https")
        self.assertTrue(o.improvement_signal)
        self.assertEqual(o.source_url, HTTP_URL)
        self.assertNotIn("no_https", codes(analyze(page())))


class IncompleteInputTests(unittest.TestCase):
    ABSENCE_CODES = {
        "missing_title",
        "missing_meta_description",
        "missing_viewport",
        "missing_h1",
        "no_cta_found",
        "no_contact_page_link",
    }

    def test_body_at_the_fetch_limit_does_not_claim_absence(self):
        a = analyze(BARE_PAGE + "<img src=a.png>", size_bytes=2_000_000)
        self.assertEqual(codes(a), ["images_missing_alt", "large_page"])

    def test_input_over_the_cap_does_not_claim_absence(self):
        html = "<p>tekst</p>" * (MAX_HTML_CHARS // 12 + 10)
        self.assertGreater(len(html), MAX_HTML_CHARS)
        a = analyze(html, size_bytes=1000)
        self.assertFalse(self.ABSENCE_CODES & set(codes(a)))

    def test_parse_budget_does_not_claim_absence(self):
        a = analyze("<div>" * (MAX_PARSE_EVENTS + 10), size_bytes=1000)
        self.assertFalse(self.ABSENCE_CODES & set(codes(a)))

    def test_complete_page_does_claim_absence(self):
        self.assertTrue(self.ABSENCE_CODES <= set(codes(analyze(BARE_PAGE))))

    def test_what_was_found_before_the_cap_is_kept(self):
        html = '<a href="mailto:post@eksempel.no">Kontakt oss</a>' + "<div>" * (MAX_PARSE_EVENTS + 10)
        a = analyze(html)
        self.assertEqual(a.contact_emails, ["post@eksempel.no"])
        self.assertEqual(a.ctas, ["Kontakt oss"])


class LargeInputTests(unittest.TestCase):
    """Sanity check that hostile input stays fast. The bound is generous on purpose, the worst
    case measured while writing this was about 2 seconds."""

    LIMIT_SECONDS = 10.0

    def test_large_inputs_stay_fast(self):
        n = MAX_HTML_CHARS
        cases = {
            "normal markup": '<p>Tekst <a href="/x">lenke</a> &amp; <b>fet</b></p>\n' * (n // 56),
            "plain text": "lorem ipsum dolor " * (n // 18),
            "only text over the cap": "x" * (n + 500_000),
            "lone angle brackets": "<" * n,
            "unterminated tags": "<a " * (n // 3),
            "many attributes": "<a " + "b " * (n // 2),
            "at signs": "a@" * (n // 2),
            "one long token": "a" * n,
            "copyright markers": "<p>© </p>" * (n // 9),
        }
        for name, html in cases.items():
            with self.subTest(case=name):
                started = time.perf_counter()
                a = analyze(html, size_bytes=1000)
                elapsed = time.perf_counter() - started
                self.assertIsInstance(a, WebsiteAnalysis)
                self.assertLess(elapsed, self.LIMIT_SECONDS)


class ToDictTests(unittest.TestCase):
    def test_json_serializable(self):
        for name, a in (
            ("good", analyze(GOOD_PAGE)),
            ("bare", analyze(BARE_PAGE)),
            ("http", analyze(page(), url=HTTP_URL, final_url=HTTP_URL)),
        ):
            with self.subTest(page=name):
                data = a.to_dict()
                self.assertEqual(json.loads(json.dumps(data)), data)
                self.assertEqual(data["url"], a.url)
                self.assertEqual(len(data["observations"]), len(a.observations))
                for o in data["observations"]:
                    self.assertEqual(set(o), {"code", "text", "source_url", "improvement_signal"})

    def test_unreachable_result_is_json_serializable(self):
        http = FakeHttp(
            {
                HTTPS_URL: AutomationError(ErrorType.TEMPORARY_ERROR, "x"),
                HTTP_URL: AutomationError(ErrorType.TEMPORARY_ERROR, "x"),
            }
        )
        data = fetch_and_analyze(http, "eksempel.no", today=TODAY).to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)
        self.assertIsNone(data["final_url"])

    def test_results_are_frozen(self):
        a = analyze(BARE_PAGE)
        with self.assertRaises(FrozenInstanceError):
            a.reachable = False
        with self.assertRaises(FrozenInstanceError):
            a.observations[0].text = "x"

    def test_observation_texts_are_clean_and_codes_are_known(self):
        html = (
            "<html><body><img src=a.png><p>firma@gmail.com © 2019</p></body></html>"
            '<meta name="generator" content="WordPress 6.4">'
        )
        a = analyze(html, url=HTTP_URL, final_url=HTTP_URL, elapsed_ms=3500, size_bytes=1_600_000)
        self.assertEqual(set(codes(a)) - OBSERVATION_CODES, set())
        self.assertGreaterEqual(len(a.observations), 12)
        for o in a.observations:
            with self.subTest(code=o.code):
                self.assertIsInstance(o, Observation)
                self.assertTrue(o.text)
                self.assertTrue(all(ord(ch) < 0x2000 for ch in o.text), "no dashes beyond hyphen, no emojis")
                self.assertNotIn("sikkerhet", o.text.lower())


class FetchTests(unittest.TestCase):
    def test_https_ok(self):
        http = FakeHttp({HTTPS_URL: response()})
        a = fetch_and_analyze(http, "eksempel.no", today=TODAY)
        self.assertEqual(http.calls, [HTTPS_URL])
        self.assertEqual(http.kwargs[0], {"timeout": FETCH_TIMEOUT_SECONDS, "max_bytes": FETCH_MAX_BYTES})
        self.assertTrue(a.reachable)
        self.assertTrue(a.https)
        self.assertEqual(a.url, HTTPS_URL)
        self.assertEqual(a.final_url, HTTPS_URL)
        self.assertEqual(a.status, 200)
        self.assertEqual(a.title, "Eksempel Regnskap AS")
        self.assertEqual((a.elapsed_ms, a.size_bytes), (400, len(GOOD_PAGE.encode("utf-8"))))
        self.assertNotIn("no_https", codes(a))

    def test_website_is_normalized_before_the_request(self):
        for website in ("eksempel.no", "https://www.Eksempel.no/om-oss?x=1", "  HTTP://eksempel.no:8080  "):
            with self.subTest(website=website):
                http = FakeHttp({HTTPS_URL: response()})
                fetch_and_analyze(http, website, today=TODAY)
                self.assertEqual(http.calls, [HTTPS_URL])

    def test_final_url_after_redirect_is_the_source(self):
        http = FakeHttp({HTTPS_URL: response(url="https://www.eksempel.no/no/", body=BARE_PAGE)})
        a = fetch_and_analyze(http, "eksempel.no", today=TODAY)
        self.assertEqual(a.url, HTTPS_URL)
        self.assertEqual(a.final_url, "https://www.eksempel.no/no/")
        self.assertTrue(all(o.source_url == "https://www.eksempel.no/no/" for o in a.observations))

    def test_https_fails_then_http_works(self):
        http = FakeHttp(
            {
                HTTPS_URL: AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, "tls or certificate error (SSLError)"),
                HTTP_URL: response(url=HTTP_URL),
            }
        )
        a = fetch_and_analyze(http, "eksempel.no", today=TODAY)
        self.assertEqual(http.calls, [HTTPS_URL, HTTP_URL])
        self.assertTrue(a.reachable)
        self.assertFalse(a.https)
        self.assertEqual(a.url, HTTP_URL)
        self.assertEqual(a.final_url, HTTP_URL)
        self.assertEqual(a.title, "Eksempel Regnskap AS")
        o = observation(a, "no_https")
        self.assertTrue(o.improvement_signal)
        self.assertEqual(o.source_url, HTTP_URL)
        self.assertIn("external_service_error", o.text)
        self.assertNotIn("SSLError", o.text)  # error types only, never messages
        self.assertEqual(codes(a).count("no_https"), 1)

    def test_http_that_redirects_to_https_counts_as_https(self):
        http = FakeHttp(
            {
                HTTPS_URL: AutomationError(ErrorType.TEMPORARY_ERROR, "timeout"),
                HTTP_URL: response(url="https://www.eksempel.no/"),
            }
        )
        a = fetch_and_analyze(http, "eksempel.no", today=TODAY)
        self.assertTrue(a.https)
        self.assertNotIn("no_https", codes(a))

    def test_both_fail(self):
        secret = "<html>SECRET-BODY token=abc123</html>"
        http = FakeHttp(
            {
                HTTPS_URL: AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, secret),
                HTTP_URL: AutomationError(ErrorType.TEMPORARY_ERROR, secret),
            }
        )
        a = fetch_and_analyze(http, "eksempel.no", today=TODAY)
        self.assertEqual(http.calls, [HTTPS_URL, HTTP_URL])
        self.assertFalse(a.reachable)
        self.assertFalse(a.https)
        self.assertEqual(a.url, HTTPS_URL)
        self.assertIsNone(a.final_url)
        self.assertIsNone(a.status)
        self.assertIsNone(a.elapsed_ms)
        self.assertIsNone(a.size_bytes)
        self.assertEqual(codes(a), ["unreachable"])
        o = a.observations[0]
        self.assertIn("external_service_error", o.text)
        self.assertIn("temporary_error", o.text)
        self.assertNotIn("SECRET-BODY", o.text)
        self.assertNotIn("abc123", o.text)
        self.assertEqual(o.source_url, HTTPS_URL)
        self.assertFalse(o.improvement_signal)  # may be a problem on our side, not the company's

    def test_homepage_not_found(self):
        body = "<html><head><title>404</title></head><body>Siden finnes ikke</body></html>"
        for status in (404, 410):
            with self.subTest(status=status):
                http = FakeHttp({HTTPS_URL: response(status=status, body=body)})
                a = fetch_and_analyze(http, "eksempel.no", today=TODAY)
                self.assertEqual(http.calls, [HTTPS_URL])
                self.assertTrue(a.reachable)
                self.assertEqual(a.status, status)
                self.assertTrue(a.https)
                self.assertIsNone(a.title)  # the error page is not analysed
                self.assertEqual(codes(a), ["homepage_not_found"])
                o = a.observations[0]
                self.assertIn(f"HTTP {status}", o.text)
                self.assertTrue(o.improvement_signal)
                self.assertEqual(o.source_url, HTTPS_URL)

    def test_homepage_not_found_over_http_also_reports_no_https(self):
        http = FakeHttp(
            {
                HTTPS_URL: AutomationError(ErrorType.TEMPORARY_ERROR, "x"),
                HTTP_URL: response(url=HTTP_URL, status=404, body=""),
            }
        )
        a = fetch_and_analyze(http, "eksempel.no", today=TODAY)
        self.assertTrue(a.reachable)
        self.assertEqual(codes(a), ["homepage_not_found", "no_https"])

    def test_invalid_website_raises_before_any_request(self):
        for website in (None, "", "   ", "not a domain", "localhost", "http://", "bare-ord"):
            with self.subTest(website=website):
                http = FakeHttp({})
                with self.assertRaises(AutomationError) as ctx:
                    fetch_and_analyze(http, website, today=TODAY)
                self.assertEqual(ctx.exception.error_type, ErrorType.VALIDATION_ERROR)
                self.assertEqual(http.calls, [])

    def test_other_exceptions_are_not_swallowed(self):
        http = FakeHttp({HTTPS_URL: RuntimeError("bug in the client")})
        with self.assertRaises(RuntimeError):
            fetch_and_analyze(http, "eksempel.no", today=TODAY)
        self.assertEqual(http.calls, [HTTPS_URL])

    def test_timing_and_size_come_from_the_response(self):
        body = page('<a href="/kontakt">Kontakt oss</a>')
        http = FakeHttp({HTTPS_URL: response(body=body, elapsed_ms=3500, size_bytes=1_600_000)})
        a = fetch_and_analyze(http, "eksempel.no", today=TODAY)
        self.assertEqual({"slow_response", "large_page"}, set(codes(a)))

    def test_today_sets_the_year(self):
        body = page("<footer>© 2019 Eksempel AS</footer>")
        old = fetch_and_analyze(FakeHttp({HTTPS_URL: response(body=body)}), "eksempel.no", today=date(2026, 1, 1))
        recent = fetch_and_analyze(FakeHttp({HTTPS_URL: response(body=body)}), "eksempel.no", today=date(2021, 12, 31))
        self.assertIn("old_copyright_year", codes(old))
        self.assertNotIn("old_copyright_year", codes(recent))

    def test_today_defaults_to_the_current_date(self):
        # Only checks that the default path works. Nothing here depends on what the date is.
        a = fetch_and_analyze(FakeHttp({HTTPS_URL: response(body=page())}), "eksempel.no")
        self.assertTrue(a.reachable)
        self.assertEqual(a.title, "Eksempel")


if __name__ == "__main__":
    unittest.main()
