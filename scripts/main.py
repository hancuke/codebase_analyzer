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

for source_file in codebase.source_files:
    print(source_file.path)
    for function in codebase.functions_in_file(source_file.path):
        print(function.id, function.source)

module = codebase.source_file("modOrder.bas")
module_functions = codebase.functions_in_file(module.path)