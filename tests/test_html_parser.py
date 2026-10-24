"""Tests for the zero-dependency DOM and CSS selector engine."""

from __future__ import annotations

import pytest

from adapters import dom
from adapters.dom import SelectorsError, parse


class TestTreeBuilding:
    def test_simple_nesting_and_parent_links(self):
        doc = parse("<div><span>x</span></div>")
        div = doc.select_one("div")
        span = doc.select_one("span")
        assert div is not None and span is not None
        assert span.parent is div
        assert div.parent is doc

    def test_void_elements_do_not_swallow_siblings(self):
        doc = parse('<div><img src="a"><p>after</p></div>')
        assert doc.select_one("p") is not None
        assert doc.select_one("p").text == "after"
        assert doc.select_one("img").get("src") == "a"

    def test_unclosed_p_is_implicitly_closed(self):
        doc = parse("<div><p>one<p>two</div>")
        paragraphs = doc.select("p")
        assert len(paragraphs) == 2
        assert paragraphs[0].text == "one"
        assert paragraphs[1].text == "two"

    def test_li_siblings_are_not_nested(self):
        doc = parse("<ul><li>a<li>b</ul>")
        items = doc.select("li")
        assert len(items) == 2
        assert items[0].parent.tag == "ul"

    def test_script_and_style_content_is_not_parsed_as_markup(self):
        doc = parse("<script>var x = '<div>';</script><p>real</p>")
        assert doc.select("div") == []
        assert doc.select_one("p").text == "real"

    def test_entities_are_decoded(self):
        doc = parse("<p>a &amp; b &lt;tag&gt; &#39;q&#39;</p>")
        assert doc.select_one("p").text == "a & b <tag> 'q'"

    def test_stray_end_tag_does_not_crash(self):
        doc = parse("</div><p>ok</p>")
        assert doc.select_one("p").text == "ok"

    def test_comments_are_ignored(self):
        doc = parse("<div><!-- note --><p>x</p></div>")
        assert doc.select_one("p").text == "x"

    def test_text_property_concatenates_descendants(self):
        doc = parse("<div>a<span>b<i>c</i></span></div>")
        assert doc.select_one("div").text == "abc"

    def test_own_text_only_direct_children(self):
        doc = parse("<div>a<span>b</span></div>")
        assert doc.select_one("div").own_text == "a"

    def test_attributes_are_lowercased_and_valueless_attrs_kept(self):
        doc = parse('<INPUT TYPE="text" DISABLED>')
        el = doc.select_one("input")
        assert el.get("type") == "text"
        assert el.has_attr("disabled")
        assert el.get("disabled") == ""


class TestSelectors:
    def test_tag_class_id(self):
        doc = parse('<div id="a" class="x y"><span class="y">t</span></div>')
        assert doc.select_one("#a") is not None
        assert len(doc.select(".y")) == 2
        assert len(doc.select("span.y")) == 1
        assert doc.select("p") == []

    def test_descendant_and_child_combinators(self):
        html = '<div class="o"><p><span class="deep">x</span></p></div><span class="shallow">y</span>'
        doc = parse(html)
        assert len(doc.select("div .deep")) == 1
        assert len(doc.select("div > .deep")) == 0
        assert len(doc.select("div .shallow")) == 0
        assert len(doc.select(".o .deep")) == 1

    def test_selector_without_spaces_around_child(self):
        doc = parse('<div><p class="a">x</p></div>')
        assert len(doc.select("div>p.a")) == 1

    def test_attribute_operators(self):
        doc = parse('<a href="https://x.test/a.jpg" data-k="v1">x</a>')
        assert doc.select('[href^="https://"]')
        assert doc.select('[href$=".jpg"]')
        assert doc.select('[href*="x.test"]')
        assert doc.select('[data-k="v1"]')
        assert doc.select('[data-k="nope"]') == []

    def test_comma_groups(self):
        doc = parse("<p>a</p><span>b</span><i>c</i>")
        assert len(doc.select("p, span")) == 2

    def test_select_excludes_self(self):
        doc = parse("<div><div>inner</div></div>")
        outer = doc.select_one("div")
        assert len(outer.select("div")) == 1

    def test_quoted_attribute_values_with_spaces(self):
        doc = parse('<div title="hello world">x</div>')
        assert doc.select('[title="hello world"]')

    def test_invalid_selector_raises(self):
        doc = parse("<div></div>")
        with pytest.raises(SelectorsError):
            doc.select("div..bad")

    def test_find_first_skips_broken_selector_and_uses_fallback(self):
        doc = parse('<div class="real">x</div>')
        found = dom.find_first(doc, ["div..broken", ".real"])
        assert found is not None and found.text == "x"

    def test_find_all_returns_first_matching_selector_only(self):
        doc = parse('<p class="a">1</p><p class="b">2</p>')
        # ".missing" yields nothing, so ".a" wins and ".b" is not added.
        assert len(dom.find_all(doc, [".missing", ".a", ".b"])) == 1

    def test_deeply_nested_tree_is_handled(self):
        html = "<div>" * 200 + "deep" + "</div>" * 200
        doc = parse(html)
        assert doc.select_one("div div div").text == "deep"


class TestParseFile:
    def test_parse_file_reads_utf8(self, fixture_path):
        doc = dom.parse_file(str(fixture_path))
        assert doc.select_one("title").text == "课堂实录"

    def test_parse_file_detects_fixture_content(self, fixture_path):
        doc = dom.parse_file(str(fixture_path))
        assert doc.select_one(".caption-card") is not None
