// Mini-PL/SQL parser (island grammar).
//
// Yordamsal iskelet (bloklar, bildirimler, kontrol akışı, imleç işlemleri, ifadeler) tam ayrıştırılır.
// SQL ifadeleri ise "ada" olarak ele alınır: baştaki anahtar kelime, hedef tablo ve INTO hedefleri
// yakalanır, geri kalan kısım `;` karakterine kadar opak token dizisi (sqlTail) olarak tutulur.
parser grammar MiniPlsqlParser;

options { tokenVocab = MiniPlsqlLexer; }

// ================================================================ derleme birimleri
script
    : unit (SLASH? unit)* SLASH? EOF
    ;

unit
    : anonymousBlock
    | createProcedure
    ;

anonymousBlock
    : (DECLARE declareSection?)? body SEMI
    ;

createProcedure
    : CREATE (OR REPLACE)? PROCEDURE qualifiedName parameterList? (IS | AS)
      declareSection? body SEMI
    ;

parameterList
    : LPAREN parameter (COMMA parameter)* RPAREN
    ;

parameter
    : identifier (IN OUT | IN | OUT)? NOCOPY? datatype ((ASSIGN | DEFAULT) expression)?
    ;

// BEGIN ... [EXCEPTION ...] END [ad]   (sondaki `;` çağıran kurala aittir)
body
    : BEGIN statementList exceptionSection? END identifier?
    ;

exceptionSection
    : EXCEPTION exceptionHandler+
    ;

exceptionHandler
    : WHEN exceptionName (OR exceptionName)* THEN statementList
    ;

exceptionName
    : OTHERS
    | qualifiedName
    ;

// ================================================================ bildirimler
declareSection
    : declaration+
    ;

declaration
    : typeDeclaration
    | cursorDeclaration
    | exceptionDeclaration
    | pragmaDeclaration
    | procedureDeclaration
    | functionDeclaration
    | variableDeclaration
    ;

variableDeclaration
    : identifier CONSTANT? datatype (NOT NULL_)? ((ASSIGN | DEFAULT) expression)? SEMI
    ;

cursorDeclaration
    : CURSOR identifier (LPAREN cursorParameter (COMMA cursorParameter)* RPAREN)?
      (RETURN datatype)? IS selectQuery SEMI
    ;

cursorParameter
    : identifier IN? datatype ((ASSIGN | DEFAULT) expression)?
    ;

typeDeclaration
    : TYPE identifier IS typeDefinition SEMI
    ;

typeDefinition
    : TABLE OF datatype (NOT NULL_)? (INDEX BY datatype)?                          # tableTypeDef
    | VARRAY LPAREN expression RPAREN OF datatype (NOT NULL_)?                   # varrayTypeDef
    | RECORD LPAREN fieldDeclaration (COMMA fieldDeclaration)* RPAREN            # recordTypeDef
    ;

fieldDeclaration
    : identifier datatype (NOT NULL_)? ((ASSIGN | DEFAULT) expression)?
    ;

exceptionDeclaration
    : identifier EXCEPTION SEMI
    ;

pragmaDeclaration
    : PRAGMA identifier (LPAREN argumentList? RPAREN)? SEMI
    ;

procedureDeclaration
    : PROCEDURE identifier parameterList? (IS | AS) declareSection? body SEMI
    ;

functionDeclaration
    : FUNCTION identifier parameterList? RETURN datatype (IS | AS) declareSection? body SEMI
    ;

// NUMBER(10,2), VARCHAR2(100 CHAR), t.col%TYPE, c%ROWTYPE, PLS_INTEGER, kullanıcı tipleri ...
datatype
    : qualifiedName PERCENT identifier                                            # anchoredType
    | qualifiedName (LPAREN NUMBER_LIT (COMMA NUMBER_LIT)? identifier? RPAREN)?  # namedType
    ;

// ================================================================ deyimler
statementList
    : (label? statement)+
    ;

label
    : LLABEL identifier RLABEL
    ;

statement
    : block
    | assignmentStatement
    | ifStatement
    | caseStatement
    | basicLoopStatement
    | whileLoopStatement
    | numericForLoopStatement
    | cursorForLoopStatement
    | forallStatement
    | openStatement
    | fetchStatement
    | closeStatement
    | exitStatement
    | continueStatement
    | nullStatement
    | raiseStatement
    | returnStatement
    | sqlStatement
    | executeImmediateStatement
    | callStatement
    ;

block
    : (DECLARE declareSection?)? body SEMI
    ;

assignmentStatement
    : variableRef ASSIGN expression SEMI
    ;

callStatement
    : variableRef SEMI
    ;

ifStatement
    : IF expression THEN statementList
      (ELSIF expression THEN statementList)*
      (ELSE statementList)?
      END IF SEMI
    ;

caseStatement
    : CASE expression? (WHEN expression THEN statementList)+ (ELSE statementList)? END CASE identifier? SEMI
    ;

basicLoopStatement
    : LOOP statementList END LOOP identifier? SEMI
    ;

whileLoopStatement
    : WHILE expression LOOP statementList END LOOP identifier? SEMI
    ;

numericForLoopStatement
    : FOR identifier IN REVERSE? expression DOTDOT expression LOOP statementList END LOOP identifier? SEMI
    ;

// FOR r IN c(args) LOOP ... | FOR r IN (SELECT ...) LOOP ...
cursorForLoopStatement
    : FOR identifier IN cursorSource LOOP statementList END LOOP identifier? SEMI
    ;

cursorSource
    : LPAREN selectQuery RPAREN          # inlineCursorSource
    | identifier (LPAREN argumentList? RPAREN)?    # namedCursorSource
    ;

forallStatement
    : FORALL identifier IN forallBounds (SAVE EXCEPTIONS)? dmlStatement
    ;

forallBounds
    : expression DOTDOT expression
    | identifier OF variableRef               // INDICES OF / VALUES OF
    ;

openStatement
    : OPEN identifier (LPAREN argumentList? RPAREN)? (FOR selectQuery)? SEMI
    ;

fetchStatement
    : FETCH identifier (BULK COLLECT)? INTO intoTargets (LIMIT expression)? SEMI
    ;

closeStatement
    : CLOSE identifier SEMI
    ;

exitStatement
    : EXIT identifier? (WHEN expression)? SEMI
    ;

continueStatement
    : CONTINUE identifier? (WHEN expression)? SEMI
    ;

nullStatement
    : NULL_ SEMI
    ;

raiseStatement
    : RAISE qualifiedName? SEMI
    ;

returnStatement
    : RETURN expression? SEMI
    ;

executeImmediateStatement
    : EXECUTE IMMEDIATE sqlTail SEMI
    ;

// ================================================================ SQL adaları
sqlStatement
    : selectIntoStatement
    | dmlStatement
    | COMMIT sqlTail SEMI
    | ROLLBACK sqlTail SEMI
    ;

dmlStatement
    : insertStatement
    | updateStatement
    | deleteStatement
    | mergeStatement
    ;

// SELECT <liste> [BULK COLLECT] INTO <hedefler> FROM ... ;
selectIntoStatement
    : SELECT selectItems (BULK COLLECT)? INTO intoTargets FROM sqlTail SEMI
    ;

selectItems
    : (~(INTO | BULK | SEMI))+
    ;

insertStatement
    : INSERT INTO dmlTarget sqlTail SEMI
    ;

updateStatement
    : UPDATE dmlTarget sqlTail SEMI
    ;

deleteStatement
    : DELETE FROM? dmlTarget sqlTail SEMI
    ;

mergeStatement
    : MERGE INTO dmlTarget sqlTail SEMI
    ;

// Hedef tablo (opsiyonel alias sqlTail içinde kalır).
dmlTarget
    : qualifiedName
    ;

intoTargets
    : variableRef (COMMA variableRef)*
    ;

// İmleç / FOR döngüsü sorgusu: SELECT ... veya WITH ... SELECT ...
selectQuery
    : (SELECT | WITH) sqlFragment
    ;

// Parantezleri dengeli opak SQL (inline imleç sorgusu için; `)` ile kapanır).
sqlFragment
    : (LPAREN sqlFragmentInner RPAREN | ~(LPAREN | RPAREN | SEMI))*
    ;

sqlFragmentInner
    : (LPAREN sqlFragmentInner RPAREN | ~(LPAREN | RPAREN | SEMI))*
    ;

// `;` görülene kadar her şey.
sqlTail
    : (~SEMI)*
    ;

// ================================================================ ifadeler
expression
    : (PLUS | MINUS) expression                                               # unaryExpr
    | expression (STAR | SLASH) expression                                    # mulExpr
    | expression (PLUS | MINUS | CONCAT) expression                           # addExpr
    | expression comparisonOp expression                                      # compareExpr
    | expression IS NOT? NULL_                                                # isNullExpr
    | expression NOT? IN LPAREN argumentList RPAREN                           # inExpr
    | expression NOT? BETWEEN expression AND expression                       # betweenExpr
    | expression NOT? LIKE expression                                         # likeExpr
    | NOT expression                                                          # notExpr
    | expression AND expression                                               # andExpr
    | expression OR expression                                                # orExpr
    | primary                                                                 # primaryExpr
    ;

comparisonOp
    : EQ | NE | LT | LE | GT | GE
    ;

primary
    : literal                                                   # literalPrimary
    | identifier STRING_LIT                                     # typedLiteralPrimary   // DATE '2024-01-01'
    | caseExpression                                            # casePrimary
    | variableRef (PERCENT identifier (LPAREN argumentList RPAREN)?)?  # refPrimary     // c%NOTFOUND, SQL%BULK_ROWCOUNT(i)
    | LPAREN expression RPAREN                                  # parenPrimary
    ;

caseExpression
    : CASE expression? (WHEN expression THEN expression)+ (ELSE expression)? END
    ;

literal
    : NUMBER_LIT
    | STRING_LIT
    | NULL_
    | TRUE
    | FALSE
    ;

// a, a.b, a(i), a(i).b, pkg.proc(x => 1), l_tab.COUNT, l_tab.EXISTS(k) ...
variableRef
    : identifier (DOT identifier | LPAREN argumentList? RPAREN)*
    ;

argumentList
    : argument (COMMA argument)*
    ;

argument
    : (identifier ARROW)? expression
    ;

qualifiedName
    : identifier (DOT identifier)* (AT identifier)?
    ;

// PL/SQL'de ayrılmış (reserved) olmayan anahtar kelimeler tanımlayıcı olarak da kullanılabilir
// (ör. kolon adı `type`, koleksiyon metodu `l_tab.DELETE`).
identifier
    : ID
    | QUOTED_ID
    | nonReservedKeyword
    ;

nonReservedKeyword
    : BULK | COLLECT | CONTINUE | DELETE | EXCEPTIONS | EXECUTE | IMMEDIATE | INDEX
    | LIMIT | MERGE | NOCOPY | OUT | PRAGMA | RECORD | REPLACE | REVERSE | SAVE
    | TYPE | USING | VARRAY
    ;
