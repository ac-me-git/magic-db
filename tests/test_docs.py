"""The documentation says what the code does (PLAN M9)."""

import importlib.util
import re
import tomllib
from pathlib import Path

import magicdb
from magicdb import search

ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readme_documents_every_setting():
    in_code = set(re.findall(r'"(MAGICDB_[A-Z_]+)"', read("src/magicdb/config.py")))
    assert len(in_code) == 12
    readme = read("README.md")
    table = readme.split("## Configuration")[1].split("\n## ")[0]
    assert {name for name in in_code if f"`{name}`" in table} == in_code
    # and the README does not document settings that do not exist
    assert set(re.findall(r"MAGICDB_[A-Z_]+", readme)) <= in_code
    for name in in_code:
        assert name in read("SPEC.md"), name


def test_documents_do_not_mention_unknown_settings():
    in_code = set(re.findall(r'"(MAGICDB_[A-Z_]+)"', read("src/magicdb/config.py")))
    for document in [
        "compose.yaml",
        "Dockerfile",
        *(f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md")),
    ]:
        assert set(re.findall(r"MAGICDB_[A-Z_]+", read(document))) <= in_code, document


def test_search_syntax_document_is_generated_from_the_code():
    script = load_script("make_syntax_doc")
    assert read("docs/search-syntax.md") == script.render(), (
        "docs/search-syntax.md is out of date: run python scripts/make_syntax_doc.py"
    )
    document = read("docs/search-syntax.md")
    for definition in search.FILTERS:
        for key in definition.keys:
            assert f"`{key}`" in document


def test_links_between_documents_exist():
    for document in [
        "README.md",
        "CONTRIBUTING.md",
        *(f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md")),
    ]:
        folder = (ROOT / document).parent
        for target in re.findall(r"\]\((?!https?://|#)([^)#]+)(?:#[^)]*)?\)", read(document)):
            assert (folder / target).exists(), f"{document} links to {target}"


def test_readme_covers_what_a_newcomer_needs():
    readme = read("README.md")
    for heading in [
        "## Quick start",
        "### Windows",
        "### Docker",
        "## Configuration",
        "## Your data, and backups",
        "## Reaching it from outside your home",
        "## Legal",
    ]:
        assert heading in readme, heading
    assert "Not approved/endorsed by Wizards" in readme and "Scryfall" in readme
    assert "Run anyway" in readme  # the unsigned executable's warning is explained


def test_version_is_the_same_everywhere():
    project = tomllib.loads(read("pyproject.toml"))["project"]
    assert project["version"] == magicdb.__version__
    assert f"## {magicdb.__version__}" in read("CHANGELOG.md")


def test_restore_instructions_mention_the_journal_files():
    guide = read("docs/backup-restore.md")
    assert "collection.db-wal" in guide and "collection.db-shm" in guide
    assert "Stop magic-db" in guide
