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

for source_file in codebase.source_files:
    print(source_file.path)
    for function in codebase.functions_in_file(source_file.path):
        print(function.id, function.source)

module = codebase.source_file("order.pkb")
module_functions = codebase.functions_in_file(module.path)