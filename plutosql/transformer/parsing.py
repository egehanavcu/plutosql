"""Mini-PL/SQL kaynak kodunu ayrıştırma API'si."""

from __future__ import annotations

from dataclasses import dataclass

from antlr4 import CommonTokenStream, InputStream, Token
from antlr4.error.ErrorListener import ErrorListener

from .generated.MiniPlsqlLexer import MiniPlsqlLexer
from .generated.MiniPlsqlParser import MiniPlsqlParser


@dataclass(frozen=True)
class SyntaxIssue:
    line: int
    column: int
    message: str

    def __str__(self) -> str:
        return f"{self.line}:{self.column}: {self.message}"


class PlsqlSyntaxError(Exception):
    """Kaynak, desteklenen Mini-PL/SQL alt kümesine uymuyor."""

    def __init__(self, issues: list[SyntaxIssue]):
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues))


class _CollectingErrorListener(ErrorListener):
    def __init__(self) -> None:
        self.issues: list[SyntaxIssue] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802 (ANTLR API)
        self.issues.append(SyntaxIssue(line, column, msg))


@dataclass
class ParseResult:
    source: str
    tokens: CommonTokenStream
    tree: MiniPlsqlParser.ScriptContext
    parser: MiniPlsqlParser

    def text(self, ctx) -> str:
        """Bir alt ağacın orijinal kaynak metni (gizli kanaldaki yorum/boşluklar dahil)."""
        return self.tokens.getText(ctx.start, ctx.stop)


def parse(source: str) -> ParseResult:
    """Kaynağı ayrıştırır; herhangi bir lexer/parser hatasında PlsqlSyntaxError fırlatır."""
    listener = _CollectingErrorListener()

    lexer = MiniPlsqlLexer(InputStream(source))
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)

    tokens = CommonTokenStream(lexer)
    parser = MiniPlsqlParser(tokens)
    parser.removeErrorListeners()
    parser.addErrorListener(listener)

    tree = parser.script()
    if listener.issues:
        raise PlsqlSyntaxError(listener.issues)
    return ParseResult(source=source, tokens=tokens, tree=tree, parser=parser)


def token_text_roundtrip(result: ParseResult) -> str:
    """Tüm tokenların (HIDDEN dahil) birleştirilmiş metni; kaynakla birebir aynı olmalıdır."""
    result.tokens.fill()
    return "".join(t.text for t in result.tokens.tokens if t.type != Token.EOF)
