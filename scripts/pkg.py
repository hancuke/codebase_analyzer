from codegraph import Codebase, OraclePlsqlFrontend, SourceFile

codebase = Codebase.analyze(
    files=[
        SourceFile(
            "order.pkb",
            """
CREATE OR REPLACE PACKAGE BODY order_pkg AS
    PROCEDURE save_order(p_id NUMBER) IS
    BEGIN
        bbbbbb.validate_order(p_id);
    END save_order; 
    
END order_pkg;
""",
        ),
                SourceFile(
            "bbbbbb.pkb",
            """
CREATE OR REPLACE PACKAGE BODY bbbbbb AS
 
    
    PROCEDURE validate_order(p_id NUMBER) IS
    BEGIN
        NULL;
    END validate_order;
END  ;
""",
        ),
    ],
    frontends=[OraclePlsqlFrontend()],
)

for diagnostic in codebase.diagnostics:
    print(diagnostic.severity, diagnostic.code, diagnostic.message)

save = "plsql:order_pkg:save_order"
function = codebase.function(save)
direct_dependencies = codebase.callees(save)
print(direct_dependencies)
