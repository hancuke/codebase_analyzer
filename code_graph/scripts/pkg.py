from codegraph import Codebase, OraclePlsqlAnalyzer, SourceFile

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
    analyzers=[OraclePlsqlAnalyzer()],
)

for source_file in codebase.source_files:
    print(source_file.source_id)
    for function in codebase.functions_in_file(source_file.source_id):
        print(function.id, function.source)

module = codebase.source_file("order.pkb")
module_functions = codebase.functions_in_file(module.source_id)