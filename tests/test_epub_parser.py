from __future__ import annotations

import zipfile

from app import EPUB_PARSER_VERSION, _migrate_library_inplace, extract_epub_articles


def test_epub3_spine_order_sections_and_short_articles(tmp_path):
    epub = tmp_path / "opaque.epub"
    container = """<?xml version='1.0' encoding='utf-8'?>
    <container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
      <rootfiles><rootfile full-path='EPUB/content.opf' media-type='application/oebps-package+xml'/></rootfiles>
    </container>"""
    opf = """<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>
      <manifest>
        <item id='section' href='z-section.html' media-type='text/html'/>
        <item id='first' href='z-first.html' media-type='text/html'/>
        <item id='second' href='a-second.html' media-type='text/html'/>
      </manifest>
      <spine><itemref idref='section'/><itemref idref='first'/><itemref idref='second'/></spine>
    </package>"""
    section = """<html><body><h2 class='section_index_title'>Science</h2>
      <ul><li><a href='z-first.html'>First story</a></li></ul></body></html>"""
    first = """<html><body><p><span class='te_section_title'>Science</span></p>
      <h1 class='te_article_title'>First story</h1><h3 class='te_article_rubric'>A short report</h3>
      <p>This deliberately short report still contains enough useful words to be a real article.</p></body></html>"""
    second = """<html><body><p><span class='te_section_title'>Science</span></p>
      <h1 class='te_article_title'>Second story</h1>
      <p>This is another compact article whose filename sorts before the first article.</p></body></html>"""
    with zipfile.ZipFile(epub, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", container)
        book.writestr("EPUB/content.opf", opf)
        book.writestr("EPUB/z-section.html", section)
        book.writestr("EPUB/z-first.html", first)
        book.writestr("EPUB/a-second.html", second)

    articles = extract_epub_articles(epub, "source")

    assert [article["title"] for article in articles] == ["First story", "Second story"]
    assert [article["section"] for article in articles] == ["Science", "Science"]
    assert articles[0]["subtitle"] == "A short report"

    stale = {
        "id": "source",
        "filename": "opaque.epub",
        "stored_path": str(epub),
        "parser_version": 0,
        "cover_file": None,
        "articles": [{**articles[0], "section": "Articles", "favorite": True}],
    }
    library = {"sources": [stale]}
    assert _migrate_library_inplace(library) is True
    assert stale["parser_version"] == EPUB_PARSER_VERSION
    assert [article["section"] for article in stale["articles"]] == ["Science", "Science"]
    assert stale["articles"][0]["favorite"] is True
