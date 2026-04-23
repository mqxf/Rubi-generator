from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class Literal:
    raw: str


SnbtValue = dict[str, "SnbtValue"] | list["SnbtValue"] | str | bool | Literal


class ParseError(ValueError):
    pass


class Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.length = len(text)
        self.index = 0

    def parse(self) -> SnbtValue:
        value = self._parse_value()
        self._skip_ws()
        if self.index != self.length:
            raise ParseError(f"unexpected trailing content at offset {self.index}")
        return value

    def _peek(self) -> str:
        return self.text[self.index] if self.index < self.length else ""

    def _advance(self) -> str:
        char = self._peek()
        if not char:
            raise ParseError("unexpected end of input")
        self.index += 1
        return char

    def _skip_ws(self) -> None:
        while self.index < self.length and self.text[self.index].isspace():
            self.index += 1

    def _parse_value(self) -> SnbtValue:
        self._skip_ws()
        char = self._peek()
        if char == "{":
            return self._parse_compound()
        if char == "[":
            return self._parse_list()
        if char in {'"', "'"}:
            return self._parse_string()
        token = self._parse_token()
        if token == "true":
            return True
        if token == "false":
            return False
        return Literal(token)

    def _parse_compound(self) -> dict[str, SnbtValue]:
        result: dict[str, SnbtValue] = {}
        self._advance()
        while True:
            self._skip_ws()
            if self._peek() == "}":
                self._advance()
                return result
            key = self._parse_key()
            self._skip_ws()
            if self._advance() != ":":
                raise ParseError(f"expected ':' after key at offset {self.index}")
            result[key] = self._parse_value()
            self._skip_ws()
            if self._peek() == ",":
                self._advance()

    def _parse_list(self) -> list[SnbtValue]:
        result: list[SnbtValue] = []
        self._advance()
        while True:
            self._skip_ws()
            if self._peek() == "]":
                self._advance()
                return result
            result.append(self._parse_value())
            self._skip_ws()
            if self._peek() == ",":
                self._advance()

    def _parse_key(self) -> str:
        self._skip_ws()
        char = self._peek()
        if char in {'"', "'"}:
            return self._parse_string()
        return self._parse_token()

    def _parse_token(self) -> str:
        self._skip_ws()
        start = self.index
        while self.index < self.length:
            char = self.text[self.index]
            if char.isspace() or char in "{}[]:,":
                break
            self.index += 1
        if start == self.index:
            raise ParseError(f"expected token at offset {self.index}")
        return self.text[start:self.index]

    def _parse_string(self) -> str:
        quote = self._advance()
        parts: list[str] = []
        while True:
            char = self._advance()
            if char == quote:
                return "".join(parts)
            if char != "\\":
                parts.append(char)
                continue
            escaped = self._advance()
            if escaped == "n":
                parts.append("\n")
            elif escaped == "r":
                parts.append("\r")
            elif escaped == "t":
                parts.append("\t")
            else:
                parts.append(escaped)


def parse(text: str) -> SnbtValue:
    return Parser(text).parse()


def dump(value: SnbtValue, *, indent: int = 0) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        inner = []
        for key, item in value.items():
            rendered = dump(item, indent=indent + 1)
            inner.append(f"{_indent(indent + 1)}{_dump_key(key)}: {rendered}")
        return "{\n" + "\n".join(inner) + f"\n{_indent(indent)}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if any(isinstance(item, (dict, list)) for item in value):
            inner = [f"{_indent(indent + 1)}{dump(item, indent=indent + 1)}" for item in value]
            return "[\n" + "\n".join(inner) + f"\n{_indent(indent)}]"
        rendered = " ".join(dump(item, indent=indent + 1) for item in value)
        return f"[{rendered}]"
    if isinstance(value, str):
        return _dump_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return value.raw


def _indent(depth: int) -> str:
    return "\t" * depth


def _dump_key(key: str) -> str:
    if key and all(char.isalnum() or char in {"_", "-", "."} for char in key):
        return key
    return _dump_string(key)


def _dump_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'
