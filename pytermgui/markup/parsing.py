from __future__ import annotations

from typing import Callable, Iterator, TypedDict
from warnings import filterwarnings, warn

from ..colors import str_to_color
from ..exceptions import ColorSyntaxError
from ..regex import RE_ANSI_NEW as RE_ANSI
from ..regex import RE_MACRO, RE_MARKUP, RE_POSITION
from .style_maps import CLEARERS, REVERSE_CLEARERS, REVERSE_STYLES, STYLES
from .tokens import (
    AliasToken,
    ClearToken,
    ColorToken,
    CursorToken,
    HLinkToken,
    MacroToken,
    PlainToken,
    StyleToken,
    Token,
)

# TODO: Improve first-run performance.

filterwarnings("always")


LINK_TEMPLATE = "\x1b]8;;{uri}\x1b\\{label}\x1b]8;;\x1b\\"

__all__ = [
    "ContextDict",
    "tokenize_markup",
    "tokenize_ansi",
    "optimize_tokens",
    "optimize_markup",
    "tokens_to_markup",
    "get_markup",
    "parse",
    "parse_tokens",
]


class ContextDict(TypedDict):
    aliases: dict[str, str]
    macros: dict[str, Callable[[str, ...], str]]

    @classmethod
    def create(cls) -> ContextDict:
        return {"aliases": {}, "macros": {}}


def tokenize_markup(text: str) -> Iterator[Token]:
    def _consume(tag: str) -> Token:
        if tag in STYLES:
            return StyleToken(tag)

        if tag.startswith("/"):
            return ClearToken(tag)

        if tag.startswith("!"):
            matchobj = RE_MACRO.match(tag)

            if matchobj is not None:
                name, args = matchobj.groups()

                if args is None:
                    return MacroToken(name, [])

                return MacroToken(name, args.split(":"))

        if tag.startswith("~"):
            return HLinkToken(tag[1:])

        if tag.startswith("(") and tag.endswith(")"):
            values = tag[1:-1].split(";")
            if len(values) != 2:
                raise ValueError(
                    f"Cursor tags must have exactly 2 values delimited by `;`, got {tag!r}."
                )

            return CursorToken(tag[1:-1], *map(int, values))

        token: Token
        try:
            token = ColorToken(tag, str_to_color(tag))

        except ColorSyntaxError:
            token = AliasToken(tag)

        finally:
            return token  # pylint: disable=lost-exception

    cursor = 0
    length = len(text)
    has_inverse = False
    for matchobj in RE_MARKUP.finditer(text):
        full, escapes, content = matchobj.groups()
        start, end = matchobj.span()

        if cursor < start:
            yield PlainToken(text[cursor:start])

        if not escapes == "":
            _, remaining = divmod(len(escapes), 2)

            yield PlainToken(full[max(1 - remaining, 1) :])
            cursor = end

            continue

        for tag in content.split():
            if tag == "inverse":
                has_inverse = True

            if tag == "/inverse":
                has_inverse = False

            consumed = _consume(tag)
            if has_inverse:
                if consumed.markup == "/fg":
                    consumed = ClearToken("/fg")

                elif consumed.markup == "/bg":
                    consumed = ClearToken("/bg")

            yield consumed

        cursor = end

    if cursor < length:
        yield PlainToken(text[cursor:length])


def tokenize_ansi(text: str) -> list[Token]:
    cursor = 0
    link = None

    for matchobj in RE_ANSI.finditer(text):
        start, end = matchobj.span()

        csi = matchobj.groups()[0:2]
        link_osc = matchobj.groups()[2:4]

        if link_osc != (None, None):
            cursor = end
            uri, label = link_osc

            yield HLinkToken(uri)
            yield PlainToken(label)

            continue

        full, content = csi

        if cursor < start:
            yield PlainToken(text[cursor:start])

        cursor = end

        code = ""

        # Position
        posmatch = RE_POSITION.match(full)

        if posmatch is not None:
            ypos, xpos = posmatch.groups()
            if not ypos and not xpos:
                raise ValueError(
                    f"Cannot parse cursor when no position is supplied. Match: {posmatch!r}"
                )

            yield CursorToken(content, ypos or None, xpos or None)
            continue

        parts = content.split(";")
        length = len(parts)

        state = None
        color_code = ""
        for i, part in enumerate(parts):
            if state is None:
                if part in REVERSE_STYLES:
                    yield StyleToken(REVERSE_STYLES[part])
                    continue

                if part in REVERSE_CLEARERS:
                    yield ClearToken(REVERSE_CLEARERS[part])
                    continue

                if part in ("38", "48"):
                    state = "COLOR"
                    color_code += part + ";"
                    continue

                # standard colors
                try:
                    yield ColorToken(part, str_to_color(part))
                    continue
                except ColorSyntaxError:
                    raise ValueError(f"Que eso {part!r}?")

            if state != "COLOR":
                continue

            color_code += part + ";"

            # Ignore incomplete RGB colors
            if (
                color_code.startswith(("38;2;", "48;2;"))
                and len(color_code.split(";")) != 6
            ):
                continue

            try:
                code = color_code

                if code.startswith(("38;2;", "48;2;", "38;5;", "48;5;")):
                    stripped = code[5:-1]

                    if code.startswith("4"):
                        stripped = "@" + stripped

                    code = stripped

                yield ColorToken(code, str_to_color(code))

            except ColorSyntaxError:
                continue

            state = None
            color_code = ""

    remaining = text[cursor:]
    if len(remaining) > 0:
        yield PlainToken(remaining)


def eval_alias(text: str, context: ContextDict) -> str:
    aliases = context["aliases"]

    evaluated = ""
    for tag in text.split():
        if tag not in aliases:
            evaluated += tag + " "
            continue

        evaluated += eval_alias(aliases[tag], context)

    return evaluated.rstrip(" ")


def parse_plain(token: PlainToken, _: ContextDict) -> str:
    return token.value


def parse_color(token: ColorToken, _: ContextDict) -> str:
    return token.color.sequence


def parse_style(token: StyleToken, _: ContextDict) -> str:
    index = STYLES[token.value]

    return f"\x1b[{index}m"


def parse_macro(
    token: MacroToken, context: ContextDict
) -> tuple[Callable[[str, ...], str], tuple[str, ...]]:
    func = context["macros"].get(token.value)

    if func is None:
        raise ValueError(f"Undefined macro {token.value!r}.")

    return func, token.arguments


def parse_alias(token: AliasToken, context: ContextDict) -> str:
    if token.value not in context["aliases"]:
        return token.value

    meaning = context["aliases"][token.value]

    return eval_alias(meaning, context).rstrip(" ")


def parse_clear(token: ClearToken, _: ContextDict) -> str:
    index = CLEARERS[token.value]

    return f"\x1b[{index}m"


def parse_cursor(token: CursorToken, _: ContextDict) -> str:
    ypos, xpos = map(lambda i: "" if i is None else i, token)

    return f"\x1b[{ypos};{xpos}H"


def optimize_tokens(tokens: Iterator[Token]) -> Iterator[Token]:
    previous = []
    current_tag_group = []

    def _diff_previous() -> Iterator[Token]:
        applied = previous.copy()

        for tkn in current_tag_group:
            targets = []

            if tkn.is_clear():
                targets = [tkn.targets(tag) for tag in applied]

            if tkn in previous and not tkn.is_clear():
                continue

            if tkn.is_clear() and not any(targets):
                continue

            applied.append(tkn)
            yield tkn

    def _remove_redundant_color(token: Token) -> None:
        for applied in current_tag_group.copy():
            if applied.is_clear() and applied.targets(token):
                current_tag_group.remove(applied)

            if not applied.is_color():
                continue

            old = applied.color

            if old.background == new.background:
                current_tag_group.remove(applied)

    for token in tokens:
        if token.is_plain():
            yield from _diff_previous()
            yield token

            previous = current_tag_group.copy()

            continue

        if token.is_color():
            new = token.color

            _remove_redundant_color(token)

            if not any(token.markup == applied.markup for applied in current_tag_group):
                current_tag_group.append(token)

            continue

        if token.is_style():
            if not any(token == tag for tag in current_tag_group):
                current_tag_group.append(token)

            continue

        if token.is_clear():
            applied = False
            for tag in current_tag_group.copy():
                if token.targets(tag) or token == tag:
                    current_tag_group.remove(tag)
                    applied = True

            if not applied:
                continue

        current_tag_group.append(token)

    yield from _diff_previous()


def tokens_to_markup(tokens: Iterator[Token]) -> str:
    tags = []
    markup = ""

    for token in tokens:
        if token.is_plain():
            if len(tags) > 0:
                markup += f"[{' '.join(tag.markup for tag in tags)}]"

            markup += token.value
            tags = []

        else:
            tags.append(token)

    if len(tags) > 0:
        markup += f"[{' '.join(tag.markup for tag in tags)}]"

    return markup


def get_markup(text: str) -> str:
    return tokens_to_markup(tokenize_ansi(text))


def optimize_markup(markup: str) -> str:
    return tokens_to_markup(optimize_tokens(tokenize_markup(markup)))


PARSERS = {
    PlainToken: parse_plain,
    ColorToken: parse_color,
    StyleToken: parse_style,
    MacroToken: parse_macro,
    AliasToken: parse_alias,
    ClearToken: parse_clear,
    CursorToken: parse_cursor,
}


def _apply_macros(text: str, macros: Iterator[MacroToken]) -> str:
    """Apply current macros to text"""

    for method, args in macros:
        if len(args) > 0:
            text = method(*args, text)
            continue

        text = method(text)

    return text


def _sub_aliases(tokens: Iterator[Token], context: ContextDict) -> list[Token]:
    token_list = list(tokens)

    def _is_substitute_candidate(token: Token) -> bool:
        if (
            token.is_alias() or token.is_clear() or token.is_macro()
        ) and token.value in context["aliases"]:
            return True

        return False

    line = ""
    tags = []

    output: list[Token] = []
    for token in token_list:
        if _is_substitute_candidate(token):
            output.extend(list(tokenize_markup(f"[{parse_alias(token, context)}]")))

            continue

        if token.is_macro() and token.value == "!link":
            warn(
                "Hyperlinks are no longer implemented as macros."
                + " Prefer using the `~{uri}` syntax.",
                DeprecationWarning,
                stacklevel=4,
            )

            output.append(HLinkToken(":".join(token.arguments)))
            continue

        output.append(token)

    return output


def parse_tokens(
    tokens: Iterator[Token],
    optimize: bool = False,
    context: ContextDict | None = None,
    append_reset: bool = True,
) -> str:
    tokens = list(_sub_aliases(tokens, context))

    if optimize:
        tokens = list(optimize_tokens(tokens))

    if append_reset:
        tokens.append(ClearToken("/"))

    output = ""
    segment = ""
    macros = []
    link = None

    for token in tokens:
        if token.is_plain():
            value = _apply_macros(
                token.value, (parse_macro(macro, context) for macro in macros)
            )

            output += segment + (
                value if link is None else LINK_TEMPLATE.format(uri=link, label=value)
            )

            segment = ""
            continue

        if token.is_hyperlink():
            link = token.value
            continue

        if token.is_macro():
            macros.append(token)
            continue

        if token.is_clear():
            if token.value in ("/", "/~"):
                link = None

            found = False
            for macro in macros.copy():
                if token.targets(macro):
                    macros.remove(macro)
                    found = True
                    break

            if found and token.value != "/":
                continue

            if token.value.startswith("/!"):
                raise ValueError(
                    f"Cannot use clearer {token.value!r} with nothing to target."
                )

        segment += PARSERS[type(token)](token, context)

    output += segment

    return output


def parse(
    text: str,
    optimize: bool = False,
    context: ContextDict | None = None,
    append_reset: bool = True,
) -> str:
    if context is None:
        context = ContextDict.create()

    if append_reset and not text.endswith("/]"):
        text += "[/]"

    tokens = tokenize_markup(text)

    return parse_tokens(
        tokens, optimize=optimize, context=context, append_reset=append_reset
    )
