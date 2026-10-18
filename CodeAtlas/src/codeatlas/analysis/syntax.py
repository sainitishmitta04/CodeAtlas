"""Language-neutral tree-sitter helpers shared by every extractor.

PNode is a plain-Python snapshot of a tree-sitter tree. Extractors read it through the
small accessors below; the module-level buffers hold the source of the file being parsed.
Access is serialized with _LOCK because API requests run in threads.
"""

import threading
from bisect import bisect_left

class PNode:
    """Plain-Python snapshot of a tree-sitter node, copied once with a TreeCursor.

    Line numbers are derived from byte offsets: reading Node.start_point / end_point corrupts
    memory (later segfaults) with py-tree-sitter 0.26 on CPython 3.13, while byte offsets,
    type, is_named and field names are safe.
    """

    __slots__ = ("type", "is_named", "start_byte", "end_byte", "start_row", "end_row",
                 "field", "children")

    def __init__(self, node, field: str | None):
        self.type: str = node.type
        self.is_named: bool = node.is_named
        self.start_byte: int = node.start_byte
        self.end_byte: int = node.end_byte
        self.start_row: int = bisect_left(_NEWLINES[0], self.start_byte)
        self.end_row: int = bisect_left(_NEWLINES[0], self.end_byte)
        self.field = field
        self.children: list[PNode] = []


def _materialize(tree) -> PNode:
    cursor = tree.walk()
    root = PNode(cursor.node, None)
    stack = [root]
    if not cursor.goto_first_child():
        return root
    while True:
        node = PNode(cursor.node, cursor.field_name)
        stack[-1].children.append(node)
        if cursor.goto_first_child():
            stack.append(node)
            continue
        while not cursor.goto_next_sibling():
            cursor.goto_parent()
            stack.pop()
            if not stack:
                return root


_SOURCE: list[bytes] = [b""]  # source of the file currently being extracted
_NEWLINES: list[list[int]] = [[]]  # byte offsets of b"\n" in that source
_LOCK = threading.Lock()  # the two buffers above are per-file state; API requests run in threads


def _children(node: PNode) -> list[PNode]:
    return node.children


def _named(node: PNode) -> list[PNode]:
    return [c for c in node.children if c.is_named]


def _fields(node: PNode | None, name: str) -> list[PNode]:
    return [] if node is None else [c for c in node.children if c.field == name]


def _field(node: PNode | None, name: str) -> PNode | None:
    found = _fields(node, name)
    return found[0] if found else None


def _text(node: PNode | None) -> str:
    if node is None:
        return ""
    return _SOURCE[0][node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _line(node: PNode) -> int:
    return node.start_row + 1


def _end_line(node: PNode) -> int:
    return node.end_row + 1
