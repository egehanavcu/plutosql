// Mini-PL/SQL lexer.
// Yorumlar ve boşluklar HIDDEN kanala gider; TokenStreamRewriter kaynak metni birebir koruyabilsin diye
// hiçbir token atılmaz (skip kullanılmaz).
lexer grammar MiniPlsqlLexer;

options { caseInsensitive = true; }

// ---------------------------------------------------------------- anahtar kelimeler
AND        : 'and';
AS         : 'as';
BEGIN      : 'begin';
BETWEEN    : 'between';
BULK       : 'bulk';
BY         : 'by';
CASE       : 'case';
CLOSE      : 'close';
COLLECT    : 'collect';
COMMIT     : 'commit';
CONSTANT   : 'constant';
CONTINUE   : 'continue';
CREATE     : 'create';
CURSOR     : 'cursor';
DECLARE    : 'declare';
DEFAULT    : 'default';
DELETE     : 'delete';
ELSE       : 'else';
ELSIF      : 'elsif';
END        : 'end';
EXCEPTION  : 'exception';
EXCEPTIONS : 'exceptions';
EXECUTE    : 'execute';
EXIT       : 'exit';
FALSE      : 'false';
FETCH      : 'fetch';
FOR        : 'for';
FORALL     : 'forall';
FROM       : 'from';
FUNCTION   : 'function';
IF         : 'if';
IMMEDIATE  : 'immediate';
IN         : 'in';
INDEX      : 'index';
INSERT     : 'insert';
INTO       : 'into';
IS         : 'is';
LIKE       : 'like';
LIMIT      : 'limit';
LOOP       : 'loop';
MERGE      : 'merge';
NOCOPY     : 'nocopy';
NOT        : 'not';
NULL_      : 'null';
OF         : 'of';
OPEN       : 'open';
OR         : 'or';
OTHERS     : 'others';
OUT        : 'out';
PRAGMA     : 'pragma';
PROCEDURE  : 'procedure';
RAISE      : 'raise';
RECORD     : 'record';
REPLACE    : 'replace';
RETURN     : 'return';
REVERSE    : 'reverse';
ROLLBACK   : 'rollback';
SAVE       : 'save';
SELECT     : 'select';
TABLE      : 'table';
THEN       : 'then';
TRUE       : 'true';
TYPE       : 'type';
UPDATE     : 'update';
USING      : 'using';
VARRAY     : 'varray';
WHEN       : 'when';
WHILE      : 'while';
WITH       : 'with';

// ---------------------------------------------------------------- operatörler
ASSIGN  : ':=';
ARROW   : '=>';
DOTDOT  : '..';
CONCAT  : '||';
LLABEL  : '<<';
RLABEL  : '>>';
LE      : '<=';
GE      : '>=';
NE      : '<>' | '!=' | '^=' | '~=';
EQ      : '=';
LT      : '<';
GT      : '>';
PLUS    : '+';
MINUS   : '-';
STAR    : '*';
SLASH   : '/';
LPAREN  : '(';
RPAREN  : ')';
COMMA   : ',';
SEMI    : ';';
DOT     : '.';
PERCENT : '%';
COLON   : ':';
AT      : '@';

// ---------------------------------------------------------------- literaller ve tanımlayıcılar
NUMBER_LIT : DIGIT+ ('.' DIGIT+)? ('e' [+-]? DIGIT+)?
           | '.' DIGIT+ ('e' [+-]? DIGIT+)?
           ;
STRING_LIT : 'n'? '\'' ( ~'\'' | '\'\'' )* '\'' ;

ID        : [a-z] [a-z0-9_$#]* ;
QUOTED_ID : '"' ~'"'+ '"' ;

// ---------------------------------------------------------------- gizli kanal
LINE_COMMENT  : '--' ~[\r\n]*        -> channel(HIDDEN);
BLOCK_COMMENT : '/*' .*? '*/'        -> channel(HIDDEN);
WS            : [ \t\r\n\u000C]+     -> channel(HIDDEN);

// SQL adalarının içinde geçebilecek, PL/SQL iskeletinin tanımadığı karakterler.
// Lexer hata vermez; ayrıştırıcı bunları yalnızca opak SQL içinde kabul eder.
OTHER : . ;

fragment DIGIT : [0-9];
