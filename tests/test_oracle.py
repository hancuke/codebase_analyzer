from codegraph import Codebase, OraclePlsqlFrontend, SourceFile


def test_oracle_package_body_resolves_members_and_qualified_calls() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "orders.pks",
                """
CREATE OR REPLACE PACKAGE orders AS
    PROCEDURE save_order(p_id NUMBER);
    PROCEDURE validate_order(p_id NUMBER);
END orders;
""".lstrip(),
            ),
            SourceFile(
                "orders.pkb",
                """
CREATE OR REPLACE PACKAGE BODY orders AS
    PROCEDURE save_order(p_id NUMBER) IS
    BEGIN
        IF p_id IS NOT NULL THEN
            validate_order(p_id);
        END IF;
        audit_pkg.write_log(p_id);
    END save_order;

    PROCEDURE validate_order(p_id NUMBER) IS
    BEGIN
        NULL;
    END validate_order;
END orders;
""".lstrip(),
            ),
            SourceFile(
                "audit.pkb",
                """
CREATE OR REPLACE PACKAGE BODY audit_pkg AS
    PROCEDURE write_log(p_id NUMBER) IS
    BEGIN
        NULL;
    END write_log;
END audit_pkg;
""".lstrip(),
            ),
        ],
        [OraclePlsqlFrontend()],
    )

    assert [function.id for function in codebase.functions] == [
        "plsql:audit_pkg:write_log",
        "plsql:orders:save_order",
        "plsql:orders:validate_order",
    ]
    calls = codebase.calls_from("plsql:orders:save_order")
    assert [(call.name, call.target_id) for call in calls] == [
        ("validate_order", "plsql:orders:validate_order"),
        ("audit_pkg.write_log", "plsql:audit_pkg:write_log"),
    ]
    assert not codebase.diagnostics


def test_oracle_package_reports_unterminated_member() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "broken.pkb",
                """
CREATE OR REPLACE PACKAGE BODY broken AS
    PROCEDURE run IS
    BEGIN
        NULL;
""".lstrip(),
            )
        ],
        [OraclePlsqlFrontend()],
    )

    assert [diagnostic.code for diagnostic in codebase.diagnostics] == [
        "unterminated_procedure"
    ]
