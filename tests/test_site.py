"""The website in site/: footers alike on every page, the homepage's hero, a 404 page that
works anywhere."""

import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
ISSUES = "https://github.com/ac-me-git/magic-db/issues"


def pages() -> list[Path]:
    return sorted(SITE.rglob("*.html"))


def links_line(page: Path) -> str:
    found = re.findall(r'<p class="links">.*?</p>', page.read_text(encoding="utf-8"), re.S)
    assert len(found) == 1, page
    return found[0]


def test_every_page_has_only_the_bug_link():
    assert len(pages()) == 15
    for page in pages():
        line = links_line(page)
        hrefs = re.findall(r'href="([^"]+)"', line)
        assert hrefs == [ISSUES], page
        italian = page.relative_to(SITE).parts[0] == "it"
        assert ("Segnala un bug" if italian else "Report a bug") in line, page
        assert "Contact me" not in line and "Contattami" not in line, page
        assert "buymeacoffee" not in line, page


HOMEPAGES = (SITE / "index.html", SITE / "it" / "index.html")


def test_the_homepages_show_the_full_name_logo_as_their_title():
    for page in HOMEPAGES:
        titles = re.findall(r"<h1>(.*?)</h1>", page.read_text(encoding="utf-8"), re.S)
        assert len(titles) == 1, page
        image = re.fullmatch(
            r'<img class="title" src="([^"]+)" alt="Another Magic Tracking Gizmo"[^>]*>',
            titles[0],
        )
        assert image, page
        assert (page.parent / image[1]).resolve() == SITE / "full-name.svg", page


def test_the_homepage_hero_cross_fades_every_screenshot():
    css = (SITE / "style.css").read_text(encoding="utf-8")
    slides = re.findall(r"\.hero-bg span:nth-child\((\d+)\)\{background-image:url\(([^)]+)\)", css)
    shots = sorted(path.name for path in (SITE / "img").glob("*.jpg"))
    assert [int(n) for n, _ in slides] == list(range(1, len(shots) + 1))
    assert sorted(Path(url).name for _, url in slides) == shots
    # the slides are spaced evenly over one turn of the animation
    turn = float(re.search(r"animation:hero-slide (\d+)s", css)[1])
    delays = [0.0] + [float(d) for d in re.findall(r"animation-delay:(\d+)s", css)]
    assert delays == [turn / len(shots) * i for i in range(len(shots))]
    assert "@media (prefers-reduced-motion:reduce){.hero-bg span{animation:none}" in css
    for page in HOMEPAGES:
        text = page.read_text(encoding="utf-8")
        layers = re.findall(r'<div class="hero-bg" aria-hidden="true">(.*?)</div>', text)
        assert layers == ["<span></span>" * len(shots)], page


def test_the_404_page_points_at_its_files_from_the_root():
    # GitHub Pages shows 404.html at whatever address was asked for, however deep
    text = (SITE / "404.html").read_text(encoding="utf-8")
    for target in re.findall(r'(?:href|src)="([^"]+)"', text):
        assert target.startswith(("/", "https://")), target
