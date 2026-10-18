# Generated from MiniPlsqlParser.g4 by ANTLR 4.13.2
from antlr4 import *
if "." in __name__:
    from .MiniPlsqlParser import MiniPlsqlParser
else:
    from MiniPlsqlParser import MiniPlsqlParser

# This class defines a complete generic visitor for a parse tree produced by MiniPlsqlParser.

class MiniPlsqlParserVisitor(ParseTreeVisitor):

    # Visit a parse tree produced by MiniPlsqlParser#script.
    def visitScript(self, ctx:MiniPlsqlParser.ScriptContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#unit.
    def visitUnit(self, ctx:MiniPlsqlParser.UnitContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#anonymousBlock.
    def visitAnonymousBlock(self, ctx:MiniPlsqlParser.AnonymousBlockContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#createProcedure.
    def visitCreateProcedure(self, ctx:MiniPlsqlParser.CreateProcedureContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#parameterList.
    def visitParameterList(self, ctx:MiniPlsqlParser.ParameterListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#parameter.
    def visitParameter(self, ctx:MiniPlsqlParser.ParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#body.
    def visitBody(self, ctx:MiniPlsqlParser.BodyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#exceptionSection.
    def visitExceptionSection(self, ctx:MiniPlsqlParser.ExceptionSectionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#exceptionHandler.
    def visitExceptionHandler(self, ctx:MiniPlsqlParser.ExceptionHandlerContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#exceptionName.
    def visitExceptionName(self, ctx:MiniPlsqlParser.ExceptionNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#declareSection.
    def visitDeclareSection(self, ctx:MiniPlsqlParser.DeclareSectionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#declaration.
    def visitDeclaration(self, ctx:MiniPlsqlParser.DeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#variableDeclaration.
    def visitVariableDeclaration(self, ctx:MiniPlsqlParser.VariableDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#cursorDeclaration.
    def visitCursorDeclaration(self, ctx:MiniPlsqlParser.CursorDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#cursorParameter.
    def visitCursorParameter(self, ctx:MiniPlsqlParser.CursorParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#typeDeclaration.
    def visitTypeDeclaration(self, ctx:MiniPlsqlParser.TypeDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#tableTypeDef.
    def visitTableTypeDef(self, ctx:MiniPlsqlParser.TableTypeDefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#varrayTypeDef.
    def visitVarrayTypeDef(self, ctx:MiniPlsqlParser.VarrayTypeDefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#recordTypeDef.
    def visitRecordTypeDef(self, ctx:MiniPlsqlParser.RecordTypeDefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#fieldDeclaration.
    def visitFieldDeclaration(self, ctx:MiniPlsqlParser.FieldDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#exceptionDeclaration.
    def visitExceptionDeclaration(self, ctx:MiniPlsqlParser.ExceptionDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#pragmaDeclaration.
    def visitPragmaDeclaration(self, ctx:MiniPlsqlParser.PragmaDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#procedureDeclaration.
    def visitProcedureDeclaration(self, ctx:MiniPlsqlParser.ProcedureDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#functionDeclaration.
    def visitFunctionDeclaration(self, ctx:MiniPlsqlParser.FunctionDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#anchoredType.
    def visitAnchoredType(self, ctx:MiniPlsqlParser.AnchoredTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#namedType.
    def visitNamedType(self, ctx:MiniPlsqlParser.NamedTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#statementList.
    def visitStatementList(self, ctx:MiniPlsqlParser.StatementListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#label.
    def visitLabel(self, ctx:MiniPlsqlParser.LabelContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#statement.
    def visitStatement(self, ctx:MiniPlsqlParser.StatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#block.
    def visitBlock(self, ctx:MiniPlsqlParser.BlockContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#assignmentStatement.
    def visitAssignmentStatement(self, ctx:MiniPlsqlParser.AssignmentStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#callStatement.
    def visitCallStatement(self, ctx:MiniPlsqlParser.CallStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#ifStatement.
    def visitIfStatement(self, ctx:MiniPlsqlParser.IfStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#caseStatement.
    def visitCaseStatement(self, ctx:MiniPlsqlParser.CaseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#basicLoopStatement.
    def visitBasicLoopStatement(self, ctx:MiniPlsqlParser.BasicLoopStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#whileLoopStatement.
    def visitWhileLoopStatement(self, ctx:MiniPlsqlParser.WhileLoopStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#numericForLoopStatement.
    def visitNumericForLoopStatement(self, ctx:MiniPlsqlParser.NumericForLoopStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#cursorForLoopStatement.
    def visitCursorForLoopStatement(self, ctx:MiniPlsqlParser.CursorForLoopStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#inlineCursorSource.
    def visitInlineCursorSource(self, ctx:MiniPlsqlParser.InlineCursorSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#namedCursorSource.
    def visitNamedCursorSource(self, ctx:MiniPlsqlParser.NamedCursorSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#forallStatement.
    def visitForallStatement(self, ctx:MiniPlsqlParser.ForallStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#forallBounds.
    def visitForallBounds(self, ctx:MiniPlsqlParser.ForallBoundsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#openStatement.
    def visitOpenStatement(self, ctx:MiniPlsqlParser.OpenStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#fetchStatement.
    def visitFetchStatement(self, ctx:MiniPlsqlParser.FetchStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#closeStatement.
    def visitCloseStatement(self, ctx:MiniPlsqlParser.CloseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#exitStatement.
    def visitExitStatement(self, ctx:MiniPlsqlParser.ExitStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#continueStatement.
    def visitContinueStatement(self, ctx:MiniPlsqlParser.ContinueStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#nullStatement.
    def visitNullStatement(self, ctx:MiniPlsqlParser.NullStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#raiseStatement.
    def visitRaiseStatement(self, ctx:MiniPlsqlParser.RaiseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#returnStatement.
    def visitReturnStatement(self, ctx:MiniPlsqlParser.ReturnStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#executeImmediateStatement.
    def visitExecuteImmediateStatement(self, ctx:MiniPlsqlParser.ExecuteImmediateStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#sqlStatement.
    def visitSqlStatement(self, ctx:MiniPlsqlParser.SqlStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#dmlStatement.
    def visitDmlStatement(self, ctx:MiniPlsqlParser.DmlStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#selectIntoStatement.
    def visitSelectIntoStatement(self, ctx:MiniPlsqlParser.SelectIntoStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#selectItems.
    def visitSelectItems(self, ctx:MiniPlsqlParser.SelectItemsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#insertStatement.
    def visitInsertStatement(self, ctx:MiniPlsqlParser.InsertStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#updateStatement.
    def visitUpdateStatement(self, ctx:MiniPlsqlParser.UpdateStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#deleteStatement.
    def visitDeleteStatement(self, ctx:MiniPlsqlParser.DeleteStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#mergeStatement.
    def visitMergeStatement(self, ctx:MiniPlsqlParser.MergeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#dmlTarget.
    def visitDmlTarget(self, ctx:MiniPlsqlParser.DmlTargetContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#intoTargets.
    def visitIntoTargets(self, ctx:MiniPlsqlParser.IntoTargetsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#selectQuery.
    def visitSelectQuery(self, ctx:MiniPlsqlParser.SelectQueryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#sqlFragment.
    def visitSqlFragment(self, ctx:MiniPlsqlParser.SqlFragmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#sqlFragmentInner.
    def visitSqlFragmentInner(self, ctx:MiniPlsqlParser.SqlFragmentInnerContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#sqlTail.
    def visitSqlTail(self, ctx:MiniPlsqlParser.SqlTailContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#unaryExpr.
    def visitUnaryExpr(self, ctx:MiniPlsqlParser.UnaryExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#notExpr.
    def visitNotExpr(self, ctx:MiniPlsqlParser.NotExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#primaryExpr.
    def visitPrimaryExpr(self, ctx:MiniPlsqlParser.PrimaryExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#addExpr.
    def visitAddExpr(self, ctx:MiniPlsqlParser.AddExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#isNullExpr.
    def visitIsNullExpr(self, ctx:MiniPlsqlParser.IsNullExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#inExpr.
    def visitInExpr(self, ctx:MiniPlsqlParser.InExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#mulExpr.
    def visitMulExpr(self, ctx:MiniPlsqlParser.MulExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#likeExpr.
    def visitLikeExpr(self, ctx:MiniPlsqlParser.LikeExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#orExpr.
    def visitOrExpr(self, ctx:MiniPlsqlParser.OrExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#compareExpr.
    def visitCompareExpr(self, ctx:MiniPlsqlParser.CompareExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#betweenExpr.
    def visitBetweenExpr(self, ctx:MiniPlsqlParser.BetweenExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#andExpr.
    def visitAndExpr(self, ctx:MiniPlsqlParser.AndExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#comparisonOp.
    def visitComparisonOp(self, ctx:MiniPlsqlParser.ComparisonOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#literalPrimary.
    def visitLiteralPrimary(self, ctx:MiniPlsqlParser.LiteralPrimaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#typedLiteralPrimary.
    def visitTypedLiteralPrimary(self, ctx:MiniPlsqlParser.TypedLiteralPrimaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#casePrimary.
    def visitCasePrimary(self, ctx:MiniPlsqlParser.CasePrimaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#refPrimary.
    def visitRefPrimary(self, ctx:MiniPlsqlParser.RefPrimaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#parenPrimary.
    def visitParenPrimary(self, ctx:MiniPlsqlParser.ParenPrimaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#caseExpression.
    def visitCaseExpression(self, ctx:MiniPlsqlParser.CaseExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#literal.
    def visitLiteral(self, ctx:MiniPlsqlParser.LiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#variableRef.
    def visitVariableRef(self, ctx:MiniPlsqlParser.VariableRefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#argumentList.
    def visitArgumentList(self, ctx:MiniPlsqlParser.ArgumentListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#argument.
    def visitArgument(self, ctx:MiniPlsqlParser.ArgumentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#qualifiedName.
    def visitQualifiedName(self, ctx:MiniPlsqlParser.QualifiedNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#identifier.
    def visitIdentifier(self, ctx:MiniPlsqlParser.IdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by MiniPlsqlParser#nonReservedKeyword.
    def visitNonReservedKeyword(self, ctx:MiniPlsqlParser.NonReservedKeywordContext):
        return self.visitChildren(ctx)



del MiniPlsqlParser