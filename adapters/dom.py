"""A tiny, dependency-free HTML DOM with a practical CSS selector subset.

Why hand-rolled instead of BeautifulSoup/lxml?
---------------------------------------------
SJTU Course Exporter is meant to be installable by an ordinary student with
nothing but a Python interpreter.  ``html.parser`` ships with CPython, and the
subset of CSS we actually need (type / class / id / attribute selectors with
descendant and child combinators) is small enough to implement correctly in a
few hundred well-tested lines.

The engine is intentionally forgiving: SJTU saves its pages from a Vue SPA, and
the markup we parse is machine-generated and changes over time.  Selector
misses must degrade to ``None``/``[]`` rather than raising.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Callable, Iterable, Iterator, Sequence

__all__ = ["Node", "Text", "Element", "Document", "parse", "SelectorsError"]

# HTML5 void elements never have children or end tags.
_VOID_ELEMENTS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)

# Elements whose end tag may be omitted; keyed by the tag that implicitly
# closes them when it is encountered as a sibling.
_IMPLIED_END = {
    "p": frozenset(
        {"address", "article", "aside", "blockquote", "div", "dl", "fieldset",
         "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
         "main", "nav", "ol", "p", "pre", "section", "table", "ul"}
    ),
    "li": frozenset({"li"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
    "option": frozenset({"option", "optgroup"}),
    "optgroup": frozenset({"optgroup"}),
    "tr": frozenset({"tr", "tbody", "tfoot", "thead"}),
    "td": frozenset({"td", "th", "tr", "tbody", "tfoot", "thead"}),
    "th": frozenset({"td", "th", "tr", "tbody", "tfoot", "thead"}),
    "thead": frozenset({"tbody", "tfoot"}),
    "tbody": frozenset({"tbody", "tfoot"}),
}


class SelectorsError(ValueError):
    """Raised when a selector string cannot be parsed."""


# --------------------------------------------------------------------------
# Tree nodes
# --------------------------------------------------------------------------


class Node:
    """Base class for every node in the tree."""

    __slots__ = ("parent",)

    def __init__(self) -> None:
        self.parent: Element | None = None

    def iter_ancestors(self) -> Iterator["Element"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def iter_preorder(self) -> Iterator["Node"]:
        yield self


class Text(Node):
    """A run of character data (entity references already resolved)."""

    __slots__ = ("data",)

    def __init__(self, data: str) -> None:
        super().__init__()
        self.data = data

    @property
    def text(self) -> str:
        return self.data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Text({self.data[:40]!r})"


class Element(Node):
    """An element with a tag name, attributes and children."""

    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None) -> None:
        super().__init__()
        self.tag = tag.lower()
        self.attrs: dict[str, str] = dict(attrs or {})
        self.children: list[Node] = []

    # -- attributes --------------------------------------------------------

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attrs.get(name.lower(), default)

    def has_attr(self, name: str) -> bool:
        return name.lower() in self.attrs

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    @property
    def element_id(self) -> str | None:
        return self.attrs.get("id")

    # -- tree --------------------------------------------------------------

    def append(self, node: Node) -> None:
        node.parent = self
        self.children.append(node)

    @property
    def elements(self) -> list["Element"]:
        return [c for c in self.children if isinstance(c, Element)]

    def iter_preorder(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            yield from child.iter_preorder()

    def iter_elements(self) -> Iterator["Element"]:
        for node in self.iter_preorder():
            if isinstance(node, Element):
                yield node

    @property
    def text(self) -> str:
        """Concatenated text of this element and all descendants."""
        parts: list[str] = []
        for node in self.iter_preorder():
            if isinstance(node, Text):
                parts.append(node.data)
        return "".join(parts)

    @property
    def own_text(self) -> str:
        """Text of direct ``Text`` children only."""
        return "".join(c.data for c in self.children if isinstance(c, Text))

    # -- selection ---------------------------------------------------------

    def select(self, selector: str) -> list["Element"]:
        """All descendants matching *selector* (document order)."""
        compiled = parse_selector(selector)
        return [el for el in self.iter_elements() if el is not self and compiled(el)]

    def select_one(self, selector: str) -> "Element | None":
        for el in self.select(selector):
            return el
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.tag} {self.attrs!r} children={len(self.children)}>"


class Document(Element):
    """Root node.  ``Document.select`` searches the whole tree."""

    def __init__(self) -> None:
        super().__init__("#document")


# --------------------------------------------------------------------------
# Selector engine
# --------------------------------------------------------------------------

# A compound selector compiles to a list of predicates over one element.
Predicate = Callable[[Element], bool]
# A step is (combinator, compound) where the combinator relates the compound to
# the step before it: " " = descendant, ">" = direct child.
Step = tuple[str, list[Predicate]]


def _parse_compound(token: str) -> list[Predicate]:
    """Parse ``div.foo#bar[attr=value]`` into a list of predicates."""
    predicates: list[Predicate] = []
    i = 0
    n = len(token)
    while i < n:
        ch = token[i]
        if ch == "*":
            i += 1
        elif ch == ".":
            j = i + 1
            while j < n and (token[j].isalnum() or token[j] in "_-"):
                j += 1
            if j == i + 1:
                raise SelectorsError(f"empty class in {token!r}")
            cls = token[i + 1 : j]
            predicates.append(lambda el, c=cls: c in el.classes)
            i = j
        elif ch == "#":
            j = i + 1
            while j < n and (token[j].isalnum() or token[j] in "_-"):
                j += 1
            if j == i + 1:
                raise SelectorsError(f"empty id in {token!r}")
            ident = token[i + 1 : j]
            predicates.append(lambda el, v=ident: el.attrs.get("id") == v)
            i = j
        elif ch == "[":
            j = token.index("]", i)
            body = token[i + 1 : j].strip()
            predicates.append(_parse_attr(body))
            i = j + 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (token[j].isalnum() or token[j] in "_-"):
                j += 1
            if j == i:
                raise SelectorsError(f"unexpected {ch!r} in {token!r}")
            tag = token[i:j].lower()
            predicates.append(lambda el, t=tag: el.tag == t)
            i = j
        else:
            raise SelectorsError(f"unexpected {ch!r} in {token!r}")
    if not predicates:
        raise SelectorsError(f"empty compound selector in {token!r}")
    return predicates


def _parse_attr(body: str) -> Predicate:
    """Parse the inside of ``[...]`` into a predicate."""
    m = re.fullmatch(
        r"""\s*([A-Za-z_][-\w:]*)\s*(?:([~^$*|]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\s\]]+))\s*)?\s*""",
        body,
    )
    if not m:
        raise SelectorsError(f"bad attribute selector [{body}]")
    name = m.group(1).lower()
    op = m.group(2)
    value = m.group(3) if m.group(3) is not None else m.group(4)
    if value is None:
        value = m.group(5)
    if op is None:
        return lambda el, n=name: n in el.attrs
    if value is None:
        value = ""
    if op == "=":
        return lambda el, n=name, v=value: el.attrs.get(n) == v
    if op == "^=":
        return lambda el, n=name, v=value: (el.attrs.get(n) or "").startswith(v)
    if op == "$=":
        return lambda el, n=name, v=value: (el.attrs.get(n) or "").endswith(v)
    if op == "*=":
        return lambda el, n=name, v=value: v in (el.attrs.get(n) or "")
    if op == "~=":
        return lambda el, n=name, v=value: v in (el.attrs.get(n) or "").split()
    if op == "|=":
        return lambda el, n=name, v=value: (el.attrs.get(n) or "") in (v, v + "-")
    raise SelectorsError(f"unsupported operator {op!r}")


def _tokenize_selector(selector: str) -> list[tuple[str, str]]:
    """Split a selector into ``(combinator, compound)`` steps.

    Supports the descendant (space) and child (``>``) combinators, plus the
    comma-free simple case.  Commas are handled by :func:`parse_selector`.
    """
    steps: list[tuple[str, str]] = []
    buf: list[str] = []
    combinator = " "
    i = 0
    n = len(selector)
    depth = 0
    while i < n:
        ch = selector[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if depth == 0 and ch in " \t\n\r>":
            if buf:
                steps.append((combinator, "".join(buf)))
                buf = []
                combinator = " "
            if ch == ">":
                # look ahead: skip whitespace, the next compound is a child
                combinator = ">"
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        steps.append((combinator, "".join(buf)))
    if not steps:
        raise SelectorsError(f"empty selector {selector!r}")
    if steps[0][0] == ">":
        steps[0] = (" ", steps[0][1])
    return steps


def _compile_chain(selector: str) -> Predicate:
    steps: list[Step] = [
        (comb, _parse_compound(compound))
        for comb, compound in _tokenize_selector(selector)
    ]

    def matches(el: Element) -> bool:
        # Right-to-left matching, which is how browsers do it.
        return _match_from(el, steps, len(steps) - 1)

    return matches


def _match_from(el: Element, steps: Sequence[Step], index: int) -> bool:
    _, compound = steps[index]
    if not all(pred(el) for pred in compound):
        return False
    if index == 0:
        return True
    if steps[index][0] == ">":
        parent = el.parent
        return parent is not None and _match_from(parent, steps, index - 1)
    # Descendant combinator: walk up until some ancestor matches the prefix.
    ancestor = el.parent
    while ancestor is not None:
        if _match_from(ancestor, steps, index - 1):
            return True
        ancestor = ancestor.parent
    return False


def parse_selector(selector: str) -> Predicate:
    """Compile a selector string into a callable ``Element -> bool``."""
    groups = [g.strip() for g in selector.split(",")]
    groups = [g for g in groups if g]
    if not groups:
        raise SelectorsError(f"empty selector {selector!r}")
    compiled = [_compile_chain(g) for g in groups]
    if len(compiled) == 1:
        return compiled[0]

    def any_group(el: Element) -> bool:
        return any(fn(el) for fn in compiled)

    return any_group


# --------------------------------------------------------------------------
# HTML -> DOM
# --------------------------------------------------------------------------


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Document()
        self._stack: list[Element] = [self.root]

    # -- helpers -----------------------------------------------------------

    @property
    def _current(self) -> Element:
        return self._stack[-1]

    def _close_tag(self, tag: str) -> None:
        for idx in range(len(self._stack) - 1, 0, -1):
            if self._stack[idx].tag == tag:
                del self._stack[idx:]
                return
        # Stray end tag: ignore, never crash.

    def _apply_implied_end(self, tag: str) -> None:
        current = self._current
        closers = _IMPLIED_END.get(current.tag)
        if closers and tag in closers:
            self._stack.pop()

    # -- HTMLParser hooks --------------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self._apply_implied_end(tag)
        el = Element(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})
        self._current.append(el)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(el)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        self._apply_implied_end(tag)
        el = Element(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})
        self._current.append(el)

    def handle_endtag(self, tag):
        self._close_tag(tag.lower())

    def handle_data(self, data):
        if data:
            self._current.append(Text(data))

    def handle_comment(self, data):  # comments carry no data we need
        return

    def handle_decl(self, decl):
        return

    def unknown_decl(self, data):
        return


def parse(html: str) -> Document:
    """Parse an HTML document (or fragment) into a :class:`Document`."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def parse_file(path, encoding: str = "utf-8") -> Document:
    """Read *path* and parse it.

    Tries UTF-8 first, then falls back to GBK, because pages saved on Chinese
    Windows machines are sometimes written in the legacy codepage.
    """
    data = open(path, "rb").read()
    for enc in (encoding, "utf-8", "gb18030", "latin-1"):
        try:
            return parse(data.decode(enc))
        except UnicodeDecodeError:
            continue
    return parse(data.decode("utf-8", errors="replace"))


def find_first(root: Element, selectors: Iterable[str]) -> Element | None:
    """Return the first element matching any selector in *selectors*.

    Used by the adapter to express "primary selector, then fallbacks" without
    repeating the fallback logic at every call site.
    """
    for selector in selectors:
        try:
            found = root.select_one(selector)
        except SelectorsError:
            continue
        if found is not None:
            return found
    return None


def find_all(root: Element, selectors: Iterable[str]) -> list[Element]:
    """Return matches for the first selector in *selectors* that matches.

    Selectors are tried in priority order; the first one that yields results
    wins, so later entries are genuine fallbacks rather than additive matches.
    """
    for selector in selectors:
        try:
            found = root.select(selector)
        except SelectorsError:
            continue
        if found:
            return found
    return []
