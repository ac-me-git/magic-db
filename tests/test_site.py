"""The website in site/: footers alike on every page, a 404 page that works anywhere."""

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


def test_the_404_page_points_at_its_files_from_the_root():
    # GitHub Pages shows 404.html at whatever address was asked for, however deep
    text = (SITE / "404.html").read_text(encoding="utf-8")
    for target in re.findall(r'(?:href|src)="([^"]+)"', text):
        assert target.startswith(("/", "https://")), target
