from codegraph import Codebase, SourceFile, VbaFrontend

codebase = Codebase.analyze(
    files=[
        SourceFile(
            "frmOrder.frm",
            """
Private Sub bSave_Click()
    If ValidateOrder() Then
        Call SaveOrder
    End If
End Sub
""",
        ),
        SourceFile(
            "modOrder.bas",
            """
Public Function ValidateOrder() As Boolean
End Function

Public Sub SaveOrder()
End Sub
""",
        ),
    ],
    frontends=[VbaFrontend()],
)

for diagnostic in codebase.diagnostics:
    print(diagnostic.severity, diagnostic.code, diagnostic.message)

save = "vba:frmOrder:bSave_Click"
function = codebase.function(save)
direct_dependencies = codebase.callees(save)
print(direct_dependencies)
