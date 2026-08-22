"""Tiny HTML tree for deterministic sanitized-fixture parsing without new dependencies."""

from __future__ import annotations

from html.parser import HTMLParser


class Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node] = []
        self.text_parts: list[str] = []

    def text(self) -> str:
        return " ".join(
            part for part in [*self.text_parts, *(child.text() for child in self.children)] if part
        ).strip()

    def descendants(self) -> list[Node]:
        result: list[Node] = []
        for child in self.children:
            result.append(child)
            result.extend(child.descendants())
        return result

    def first_test_id(self, test_id: str) -> Node | None:
        for node in [self, *self.descendants()]:
            if node.attrs.get("data-testid") == test_id:
                return node
        return None

    def all_test_id(self, test_id: str) -> list[Node]:
        return [
            node for node in [self, *self.descendants()] if node.attrs.get("data-testid") == test_id
        ]

    def first_class(self, class_name: str) -> Node | None:
        for node in [self, *self.descendants()]:
            if class_name in node.attrs.get("class", "").split():
                return node
        return None

    def all_class(self, class_name: str) -> list[Node]:
        return [
            node
            for node in [self, *self.descendants()]
            if class_name in node.attrs.get("class", "").split()
        ]


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if cleaned:
            self.stack[-1].text_parts.append(cleaned)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    return builder.root
