from codegraph import Codebase, OraclePlsqlAnalyzer, SourceFile


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
        [OraclePlsqlAnalyzer()],
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
    assert [(entry.function_id, entry.kind) for entry in codebase.entry_points] == [
        ("plsql:orders:save_order", "package_public_member"),
        ("plsql:orders:validate_order", "package_public_member"),
    ]


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
        [OraclePlsqlAnalyzer()],
    )

    assert [diagnostic.code for diagnostic in codebase.diagnostics] == [
        "unterminated_procedure"
    ]


def test_oracle_marks_standalone_members_and_public_package_members_as_entries() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "inventory.pks",
                """
CREATE PACKAGE inventory AS
    PROCEDURE refresh;
    FUNCTION stock_count RETURN NUMBER;
END inventory;
""".lstrip(),
            ),
            SourceFile(
                "inventory.pkb",
                """
CREATE PACKAGE BODY inventory AS
    PROCEDURE refresh IS
    BEGIN
        recalculate;
    END refresh;

    FUNCTION stock_count RETURN NUMBER IS
    BEGIN
        RETURN 0;
    END stock_count;

    PROCEDURE recalculate IS
    BEGIN
        NULL;
    END recalculate;
END inventory;
""".lstrip(),
            ),
            SourceFile(
                "rebuild.sql",
                """
CREATE OR REPLACE PROCEDURE rebuild_inventory IS
BEGIN
    inventory.refresh;
END rebuild_inventory;
""".lstrip(),
            ),
            SourceFile(
                "count.sql",
                """
CREATE FUNCTION available_inventory RETURN NUMBER IS
BEGIN
    RETURN inventory.stock_count;
END available_inventory;
""".lstrip(),
            ),
        ],
        [OraclePlsqlAnalyzer()],
    )

    assert [(function.id, function.attributes["visibility"]) for function in codebase.functions] == [
        ("plsql:inventory:recalculate", "private"),
        ("plsql:inventory:refresh", "public"),
        ("plsql:inventory:stock_count", "public"),
        ("plsql:standalone:count:available_inventory", "public"),
        ("plsql:standalone:rebuild:rebuild_inventory", "public"),
    ]
    assert [(entry.function_id, entry.kind, entry.source) for entry in codebase.entry_points] == [
        ("plsql:inventory:refresh", "package_public_member", "package_spec"),
        ("plsql:inventory:stock_count", "package_public_member", "package_spec"),
        ("plsql:standalone:count:available_inventory", "standalone_function", "declaration"),
        ("plsql:standalone:rebuild:rebuild_inventory", "standalone_procedure", "declaration"),
    ]


def test_oracle_reports_public_package_members_without_implementations() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "orders.pks",
                """
CREATE PACKAGE orders AS
    PROCEDURE save_order;
END orders;
""".lstrip(),
            )
        ],
        [OraclePlsqlAnalyzer()],
    )

    assert codebase.functions == ()
    assert codebase.entry_points == ()
    assert [(diagnostic.code, diagnostic.source_id, diagnostic.line) for diagnostic in codebase.diagnostics] == [
        ("missing_package_member_implementation", "orders.pks", 2)
    ]


def test_oracle_standalone_members_allow_leading_comments_and_schema_ownership() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "refresh.sql",
                """
-- Nightly maintenance task.

CREATE OR REPLACE PROCEDURE reporting.refresh_orders IS
BEGIN
    NULL;
END refresh_orders;
""".lstrip(),
            ),
            SourceFile(
                "archive.sql",
                """
CREATE OR REPLACE PROCEDURE archive.refresh_orders IS
BEGIN
    NULL;
END refresh_orders;
""".lstrip(),
            ),
        ],
        [OraclePlsqlAnalyzer()],
    )

    assert [function.id for function in codebase.functions] == [
        "plsql:standalone:archive:refresh_orders",
        "plsql:standalone:reporting:refresh_orders",
    ]
    assert [function.source_range.start_line for function in codebase.functions] == [
        1,
        3,
    ]
    assert not codebase.diagnostics


def test_oracle_extracts_multiple_global_members_from_one_source() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "maintenance.sql",
                """
-- Global objects do not belong to a package.
CREATE OR REPLACE FUNCTION reporting.current_batch RETURN NUMBER IS
BEGIN
    RETURN 1;
END current_batch;

CREATE OR REPLACE PROCEDURE reporting.refresh_batch IS
BEGIN
    current_batch;
END refresh_batch;
""".lstrip(),
            )
        ],
        [OraclePlsqlAnalyzer()],
    )

    assert [function.id for function in codebase.functions] == [
        "plsql:standalone:reporting:current_batch",
        "plsql:standalone:reporting:refresh_batch",
    ]
    assert [
        (call.name, call.target_id)
        for call in codebase.calls_from("plsql:standalone:reporting:refresh_batch")
    ] == [("current_batch", "plsql:standalone:reporting:current_batch")]
    assert [
        (entry.function_id, entry.kind)
        for entry in codebase.entry_points
    ] == [
        (
            "plsql:standalone:reporting:current_batch",
            "standalone_function",
        ),
        (
            "plsql:standalone:reporting:refresh_batch",
            "standalone_procedure",
        ),
    ]
