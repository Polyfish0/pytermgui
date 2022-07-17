"""Wrappers around the TIM parsing engine, implementing caching and context management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generator, Iterator

from ..colors import ColorSyntaxError, str_to_color
from ..terminal import get_terminal
from .aliases import apply_default_aliases
from .macros import apply_default_macros
from .parsing import (
    PARSERS,
    ContextDict,
    eval_alias,
    parse_tokens,
    tokenize_ansi,
    tokenize_markup,
    tokens_to_markup,
)
from .style_maps import CLEARERS
from .tokens import Token

__all__ = [
    "MarkupLanguage",
    "StyledText",
    "tim",
]

Tokenizer = Callable[[str], Iterator[Token]]


class MarkupLanguage:
    """A relatively simple object that binds context to TIM parsing functions.

    Most of the job this class has is to pass along a `ContextDict` to various
    "lower level" functions, in order to maintain a sort of state. It also exposes
    ways to modify this state, namely the `alias` and `define` methods.
    """

    def __init__(
        self, *, default_aliases: bool = True, default_macros: bool = True
    ) -> None:
        self._cache: dict[str, tuple[list[Token], str]] = {}

        self.context = ContextDict.create()
        self._aliases = self.context["aliases"]
        self._macros = self.context["macros"]

        if default_aliases:
            apply_default_aliases(self)

        if default_macros:
            apply_default_macros(self)

    @property
    def aliases(self) -> dict[str, str]:
        """Returns a copy of the aliases defined in context."""

        return self._aliases.copy()

    @property
    def macros(self) -> dict[str, Callable[[str, ...]]]:
        """Returns a copy of the macros defined in context."""

        return self._macros.copy()

    def define(self, name: str, method: Callable[[str, ...], str]) -> None:
        """Defines a markup macro.

        Macros are essentially function bindings callable within markup. They can be
        very useful to represent changing data and simplify TIM code.

        Args:
            name: The name that will be used within TIM to call the macro. Must start with
                a bang (`!`).
            method: The function bound to the name given above. This function will take
                any number of strings as arguments, and return a terminal-ready (i.e. parsed)
                string.
        """

        if not name.startswith("!"):
            raise ValueError("TIM macro names must be prefixed by `!`.")

        self._macros[name] = method

    def alias(self, name: str, value: str, *, generate_unsetter: bool = True) -> None:
        """Creates an alias from one custom name to a set of styles.

        These can be used to store and reference a set of tags using only one name.

        Aliases may reference other aliases, but only do this consciously, as it can become
        a hard to follow trail of sorrow very quickly!

        Args:
            name: The name this alias will be referenced by.
            value: The markup value that the alias will represent.
            generate_unsetter: Disable generating clearer aliases.

                For example:
                    ```
                    my-tag = 141 bold italic
                    ```

                will generate:
                    ```
                    /my-tag = /fg /bold /italic
                    ```
        """

        value = eval_alias(value, self.context)

        def _generate_unsetter() -> str:
            unsetter = ""
            for tag in value.split():
                if "(" in tag and ")" in tag:
                    tag = tag[: tag.find("(")]

                if tag in self._aliases or tag in self._macros:
                    unsetter += f" /{tag}"
                    continue

                try:
                    color = str_to_color(tag)
                    unsetter += f" /{'bg' if color.background else 'fg'}"

                except ColorSyntaxError:
                    unsetter += f" /{tag}"

            return unsetter.lstrip(" ")

        self._aliases[name] = value

        if generate_unsetter:
            self._aliases[f"/{name}"] = _generate_unsetter()

    def alias_multiple(
        self, *, generate_unsetter: bool = True, **items: dict[str, str]
    ) -> None:
        """Runs `MarkupLanguage.alias` repeatedly for all arguments.

        The same `generate_unsetter` value will be used for all calls.

        You can use this in two forms:

        - Traditional keyword arguments:

            ```python
            lang.alias_multiple(my-tag1="bold", my-tag2="italic")
            ```

        - Keyword argument unpacking:

            ```python
            my_aliases = {"my-tag1": "bold", "my-tag2": "italic"}
            lang.alias_multiple(**my_aliases)
            ```
        """

        for name, value in items.items():
            self.alias(name, value, generate_unsetter=generate_unsetter)

    def parse(
        self,
        text: str,
        optimize: bool = False,
        append_reset: bool = True,
    ) -> str:
        """Parses some markup text.

        This is a thin wrapper around `pytermgui.markup.parsing.parse`. The main additions
        of this wrapper are a caching system, as well as state management.

        Ignoring caching, all calls to this function would be equivalent to:

            def parse(self, *args, **kwargs) -> str:
                kwargs["context"] = self.context

                return parse(*args, **kwargs)
        """

        key = (text, optimize, append_reset)

        if key in self._cache:
            tokens, output, has_macro = self._cache[key]

            if has_macro:
                output = parse_tokens(
                    tokens,
                    optimize=optimize,
                    append_reset=append_reset,
                    context=self.context,
                )

                self._cache[key] = (tokens, output, has_macro)

            return output

        tokens = list(tokenize_markup(text))

        output = parse_tokens(
            tokens,
            optimize=optimize,
            append_reset=append_reset,
            context=self.context,
        )

        has_macro = any(token.is_macro() for token in tokens)

        self._cache[key] = (tokens, output, has_macro)

        return output

    # TODO: This should be deprecated.
    @staticmethod
    def get_markup(text: str) -> str:
        """DEPRECATED: Convert ANSI text into markup.

        This function does not use context, and thus is out of place here.
        """

        return tokens_to_markup(tokenize_ansi(text))

    def group_styles(
        self, text: str, tokenizer: Tokenizer = tokenize_ansi
    ) -> Generator[StyledText, None, None]:
        """Generate StyledText-s from some text, using our context.

        See `StyledText.group_styles` for arguments.
        """

        yield from StyledText.group_styles(
            text, tokenizer=tokenizer, context=self.context
        )

    def prettify_markup(self, markup: str) -> str:
        """Syntax-highlight markup."""

        output = ""

        for span in StyledText.group_styles(
            markup, tokenizer=tokenize_markup, context=self.context
        ):
            tags = " ".join(token.prettified_markup for token in span.tokens[:-1])
            markup = " ".join(tkn.markup for tkn in span.tokens[:-1])

            if len(tags) > 0 and len(markup) > 0:
                output += f"[{tags}][{markup}]"

            output += f"{span.plain}[/]"

        return self.parse(output, optimize=True)

    def print(self, *args, **kwargs) -> None:
        """Parse all arguments and pass them through to print, along with kwargs."""

        parsed = []
        for arg in args:
            parsed.append(self.parse(str(arg)))

        get_terminal().print(*parsed, **kwargs)


tim = MarkupLanguage()


@dataclass(frozen=True)
class StyledText:
    """An ANSI style-infused string.

    This is a sort of helper to handle ANSI texts in a more semantic manner. It
    keeps track of a sequence and a plain part.

    Calling `len()` will return the length of the printable, non-ANSI part, and
    indexing will return the characters at the given slice, but also include the
    sequences that are applied to them.

    To generate StyledText-s, it is recommended to use the `StyledText.yield_from_ansi`
    classmethod.
    """

    __slots__ = ("plain", "sequences", "tokens", "link")

    sequences: str
    plain: str
    tokens: list[Token]
    link: str | None

    # TODO: These attributes could be added in the future, though doing so would cement
    #       StyledText-s only ever being created by `group_styles`.
    #
    #       Maybe we could add a `styled_text.as_bold()`, `as_color()` type API? We would
    #       still need to somehow default the attributes somehow, which could then be done
    #       with a helper function?
    #
    # foreground: Color | None
    # background: Color | None
    # bold: bool
    # dim: bool
    # italic: bool
    # underline: bool
    # strikethrough: bool
    # inverse: bool

    @staticmethod
    def group_styles(
        text: str,
        tokenizer: Tokenizer = tokenize_ansi,
        context: ContextDict | None = None,
    ) -> Generator[StyledText, None, None]:
        """Yields StyledTexts from an ANSI coded string.

        A new StyledText will be created each time a non-plain token follows a
        plain token, thus all texts will represent a single (ANSI)PLAIN group
        of characters.
        """

        context = context if context is not None else ContextDict.create()

        parsers = PARSERS
        link = None

        def _parse(token: Token) -> str:
            nonlocal link

            if token.is_macro():
                return token.markup

            if token.is_hyperlink():
                link = token
                return ""

            if link is not None and token.is_clear() and token.targets(link):
                link = None

            if token.is_clear() and token.value not in CLEARERS:
                return token.markup

            return parsers[type(token)](token, context)

        tokens = []
        token: Token

        for token in tokenizer(text):
            if token.is_plain():
                yield StyledText(
                    "".join(_parse(tkn) for tkn in tokens),
                    token.value,
                    tokens + [token],
                    link.value if link is not None else None,
                )

                tokens = [tkn for tkn in tokens if not tkn.is_cursor()]
                continue

            if token.is_clear():
                tokens = [tkn for tkn in tokens if not token.targets(tkn)]

                if len(tokens) > 0 and tokens[-1] == token:
                    continue

            if len(tokens) > 0 and all(tkn.is_clear() for tkn in tokens):
                tokens = []

            tokens.append(token)

        # if len(tokens) > 0:
        #     token = PlainToken("")

        #     yield StyledText(
        #         "".join(_parse(tkn) for tkn in tokens),
        #         token.value,
        #         tokens + [token],
        #         link.value if link is not None else None,
        #     )

    @classmethod
    def first_of(cls, text: str) -> StyledText | None:
        """Returns the first element of cls.yield_from_ansi(text)."""

        for item in cls.group_styles(text):
            return item

        return None

    def __len__(self) -> int:
        return len(self.plain)

    def __str__(self) -> str:
        return self.sequences + self.plain

    def __getitem__(self, sli: int | slice) -> str:
        return self.sequences + self.plain[sli]
