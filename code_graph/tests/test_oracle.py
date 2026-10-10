from dataclasses import FrozenInstanceError

import pytest

from codegraph import (
    Codebase,
    OraclePlsqlAnalyzer,
    PlsqlAnalysisMode,
    PlsqlWorkloadReason,
    SourceFile,
    assess_plsql_complexity,
)


def test_plsql_complexity_wide_dml_is_simple_and_format_independent() -> None:
    columns = ", ".join(f"column_{index}" for index in range(200))
    values = ", ".join(str(index) for index in range(200))
    source = (
        f"BEGIN INSERT INTO records ({columns}) VALUES ({values}); "
        "UPDATE records SET column_0 = 1; END;"
    )
    result = assess_plsql_complexity(source)

    wrapped = assess_plsql_complexity(source.replace(", ", ",\n"))
    assert result.metrics.logical_units == wrapped.metrics.logical_units == 2
    assert result.metrics.sql_statements == wrapped.metrics.sql_statements == 2
    assert result.mode is wrapped.mode is PlsqlAnalysisMode.SINGLE_PASS
    assert wrapped.metrics.physical_code_lines > result.metrics.physical_code_lines
    assert result.evidence[0].reason is PlsqlWorkloadReason.LOGICAL_LENGTH
    with pytest.raises(FrozenInstanceError):
        result.score = 100


def test_plsql_workload_actual_lines_gate_short_source_protection() -> None:
    short = assess_plsql_complexity("BEGIN\n" + "value := 1;\n" * 48 + "END;")
    long = assess_plsql_complexity("BEGIN\n" + "value := 1;\n" * 49 + "END;")
    assert short.metrics.physical_code_lines == 50
    assert long.metrics.physical_code_lines == 51
    assert short.evidence[-1].reason is PlsqlWorkloadReason.SHORT_SOURCE
    assert all(item.reason is not PlsqlWorkloadReason.SHORT_SOURCE for item in long.evidence)
    assert long.metrics.adjusted_code_lines == 51
    assert long.score == 52.02


def test_plsql_workload_wide_dml_does_not_hide_following_code_or_conditions() -> None:
    assignments = ",\n".join(f"c{i} = source.c{i}" for i in range(150))
    result = assess_plsql_complexity(
        f"BEGIN\nUPDATE target SET\n{assignments}\nWHERE id = 1\n"
        "AND enabled = 1;\n" + "process_row();\n" * 100 + "END;"
    )
    assert result.metrics.adjusted_code_lines >= 104
    assert result.mode is PlsqlAnalysisMode.MULTI_PASS


def test_plsql_complexity_counts_loops_decisions_and_nesting_once() -> None:
    result = assess_plsql_complexity("""
CREATE FUNCTION process_rows RETURN NUMBER IS
BEGIN
    FOR row IN (SELECT id FROM records) LOOP
        IF row.id > 0 THEN
            UPDATE records SET active = 1;
        ELSIF row.id < 0 THEN
            NULL;
        END IF;
    END LOOP;
    RETURN 1;
END;
""")
    assert result.metrics.decisions == 2
    assert result.metrics.loops == 1
    assert result.metrics.max_control_depth == 2
    assert result.metrics.nested_control_units == 1
    assert result.metrics.sql_statements == 2
    assert result.metrics.logical_units == 6
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS


def test_plsql_complexity_counts_sql_rules_without_procedural_branches() -> None:
    result = assess_plsql_complexity("""
BEGIN
    INSERT INTO summary
    SELECT CASE WHEN a.total > 0 THEN 1 WHEN a.total = 0 THEN 2 ELSE 3 END,
           SUM(a.total), ROW_NUMBER() OVER (ORDER BY a.id)
    FROM orders a JOIN customers b ON a.customer_id = b.id
    WHERE a.id IN (SELECT id FROM archived)
    UNION ALL SELECT 1, 0, 1 FROM dual;
END;
""")
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS
    assert result.metrics.sql_statements == 1
    assert result.metrics.decisions == 2
    assert result.metrics.set_operations == 1
    assert result.metrics.logical_units == 8


def test_plsql_complexity_ignores_keywords_in_comments_and_literals() -> None:
    source = """
BEGIN
    -- IF LOOP JOIN
    /* CASE WHEN UNION */
    INSERT INTO "IF" ("LOOP") VALUES ('IF ''LOOP'' CASE');
    message := q'[it's IF LOOP CASE]';
    message := q'!it's JOIN UNION!';
    message := q'{END IF; LOOP}';
END;
"""
    result = assess_plsql_complexity(source)
    assert result.metrics.logical_units == 1.75
    assert result.metrics.decisions == result.metrics.loops == 0
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS


def test_plsql_complexity_explicit_cursor_and_exception_evidence() -> None:
    result = assess_plsql_complexity("""
DECLARE
    CURSOR items IS SELECT id FROM records;
    item_id NUMBER;
BEGIN
    OPEN items;
    LOOP
        FETCH items INTO item_id;
        EXIT WHEN items%NOTFOUND;
    END LOOP;
    CLOSE items;
EXCEPTION
    WHEN OTHERS THEN RAISE;
END;
""")
    assert result.metrics.cursor_declarations == 1
    assert result.metrics.fetched_cursors == 1
    assert result.metrics.exception_handlers == 1
    assert result.metrics.decisions == 1
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS


def test_plsql_workload_financial_rules_require_multiple_passes() -> None:
    result = assess_plsql_complexity("""
                     FUNCTION update_modi_status (pi_modi_seq_no IN VARCHAR2,
                                pi_new_modi_status IN VARCHAR2,
                                pi_new_ai_modi_status IN VARCHAR2,
                                pi_user_no IN VARCHAR2,
                                po_ret_msg OUT VARCHAR2) RETURN VARCHAR2 IS
      l_ai_modi_status       ai_modi_rec.ai_modi_status%TYPE := NULL;
      l_curr_prcs_datetime   DATE := pkg_global.curr_prcs_datetime;
      l_ret_msg              VARCHAR2(200);
   BEGIN
      IF pi_modi_seq_no IS NULL THEN
         RAISE pkg_global.invalid_parm;
      END IF;


      IF pi_user_no IS NULL THEN
         RAISE pkg_global.invalid_parm;
      END IF;

      l_ai_modi_status := pi_new_ai_modi_status;

      IF pi_new_modi_status <> modi_status_wdn THEN
         IF pkg_modi_intf.update_ai_modi_status(pi_modi_seq_no, pi_new_modi_status, l_ai_modi_status, l_ret_msg) <> pkg_global.success  THEN
            ROLLBACK;
            RETURN pkg_global.failure;
         END IF;
      END IF;

      UPDATE modi_rec
      SET modi_status = NVL(pi_new_modi_status, modi_status),
          ai_modi_status = NVL(l_ai_modi_status, ai_modi_status),
          status_upd_date = l_curr_prcs_datetime,
          status_upd_user = pi_user_no,
          ver = NVL(ver, 0) + 1,
          last_upd_date = l_curr_prcs_datetime,
          last_upd_user = pi_user_no
      WHERE modi_seq_no = pi_modi_seq_no;
 UPDATE modi_rec
      SET modi_status = NVL(pi_new_modi_status, modi_status),
          ai_modi_status = NVL(l_ai_modi_status, ai_modi_status),
          status_upd_date = l_curr_prcs_datetime,
          status_upd_user = pi_user_no,
          ver = NVL(ver, 0) + 1,
          last_upd_date = l_curr_prcs_datetime,
          last_upd_user = pi_user_no
      WHERE modi_seq_no = pi_modi_seq_no; UPDATE modi_rec
      SET modi_status = NVL(pi_new_modi_status, modi_status),
          ai_modi_status = NVL(l_ai_modi_status, ai_modi_status),
          status_upd_date = l_curr_prcs_datetime,
          status_upd_user = pi_user_no,
          ver = NVL(ver, 0) + 1,
          last_upd_date = l_curr_prcs_datetime,
          last_upd_user = pi_user_no
      WHERE modi_seq_no = pi_modi_seq_no;

      RETURN pkg_global.success;
   EXCEPTION
      WHEN pkg_global.invalid_parm THEN
         po_ret_msg := 'Invalid parameter';
         DBMS_OUTPUT.PUT_LINE(po_ret_msg);
         RETURN pkg_global.failure;
      WHEN OTHERS THEN
         ROLLBACK;
         l_ret_msg := 'Others error:' || SQLERRM;
         DBMS_OUTPUT.PUT_LINE(l_ret_msg);
         RETURN pkg_global.failure;
   END;

                                     
""")
    print(result.score)
    
    assert result.mode is PlsqlAnalysisMode.MULTI_PASS


def test_plsql_complexity_cte_and_nested_sql_cases() -> None:
    result = assess_plsql_complexity("""
BEGIN
    WITH items AS (SELECT id FROM records)
    SELECT CASE WHEN id > 0 THEN
        CASE WHEN id > 10 THEN 2 ELSE 1 END
        ELSE 0 END
    INTO result FROM items;
END;
""")
    assert result.metrics.sql_statements == 1
    assert result.metrics.additional_query_blocks == 1
    assert result.metrics.nested_control_units == 1
    assert result.metrics.logical_units == 4


def test_plsql_complexity_while_and_repeated_fetch_are_not_double_counted() -> None:
    result = assess_plsql_complexity("""
BEGIN
    FETCH items INTO item_id;
    WHILE item_id > 0 LOOP
        FETCH items INTO item_id;
    END LOOP;
END;
""")
    assert result.metrics.loops == 1
    assert result.metrics.fetched_cursors == 1
    assert result.metrics.logical_units == 3


@pytest.mark.parametrize(
    "source",
    [
        "BEGIN EXECUTE IMMEDIATE 'SELECT 1 FROM dual'; END;",
        "BEGIN dbms_sql.parse(c, text, 1); END;",
        "BEGIN OPEN items FOR query_text; END;",
        "BEGIN IF ok THEN NULL; END;",
        "BEGIN NULL; END LOOP;",
        "",
    ],
)
def test_plsql_workload_has_no_uncertainty_override(source: str) -> None:
    result = assess_plsql_complexity(source)
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS
    assert result.evidence[-1].reason is PlsqlWorkloadReason.SHORT_SOURCE


@pytest.mark.parametrize("source", [
    "BEGIN x := 'unfinished",
    "BEGIN x := q'[unfinished",
    "BEGIN /* unfinished",
])
def test_plsql_workload_unterminated_tokens_do_not_return_partial_scores(source: str) -> None:
    with pytest.raises(ValueError, match="Unterminated.*line 1"):
        assess_plsql_complexity(source)


@pytest.mark.parametrize(("branches", "mode"), [
    (50, PlsqlAnalysisMode.SINGLE_PASS),
    (51, PlsqlAnalysisMode.SINGLE_PASS),
    (69, PlsqlAnalysisMode.SINGLE_PASS),
    (70, PlsqlAnalysisMode.MULTI_PASS),
])
def test_plsql_complexity_threshold(branches: int, mode: PlsqlAnalysisMode) -> None:
    source = "BEGIN " + "IF flag THEN NULL; END IF; " * branches + "END;"
    result = assess_plsql_complexity(source)
    assert result.metrics.logical_units == branches
    assert result.score == branches + 30.02
    assert result.mode is mode


@pytest.mark.parametrize("count", [200, 201])
def test_plsql_workload_discounts_simple_assignments(count: int) -> None:
    source = "BEGIN " + "value := row.field; " * count + "END;"
    result = assess_plsql_complexity(source)
    assert result.metrics.logical_units == count / 4
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS
    if count == 200:
        assert result.evidence[-1].reason is PlsqlWorkloadReason.SHORT_SOURCE
    else:
        assert all(item.reason is not PlsqlWorkloadReason.SHORT_SOURCE for item in result.evidence)


def test_plsql_workload_long_sequential_statements_need_multiple_passes() -> None:
    result = assess_plsql_complexity("BEGIN " + "process_row(); " * 100 + "END;")
    assert result.metrics.logical_units == 100
    assert result.metrics.physical_code_lines == 1
    assert result.metrics.loops == result.metrics.decisions == 0
    assert result.mode is PlsqlAnalysisMode.MULTI_PASS


def test_plsql_workload_structure_changes_recommendation_for_same_length() -> None:
    flat = assess_plsql_complexity("BEGIN " + "process_row(); " * 80 + "END;")
    nested_source = "BEGIN " + "process_row(); " * 76 + """
        LOOP
            IF flag THEN
                LOOP
                    process_row();
                END LOOP;
            END IF;
        END LOOP;
    END;"""
    nested = assess_plsql_complexity(nested_source)
    assert flat.metrics.logical_units == nested.metrics.logical_units == 80
    assert flat.mode is PlsqlAnalysisMode.SINGLE_PASS
    assert nested.metrics.nested_control_units == 3
    # Nested corrections are bounded: modest nesting alone need not upgrade.
    assert nested.mode is PlsqlAnalysisMode.SINGLE_PASS
    dense = assess_plsql_complexity(
        "BEGIN " + "process_row(); " * 70
        + "IF flag THEN NULL; END IF; " * 10 + "END;"
    )
    assert dense.metrics.logical_units == 80
    assert dense.mode is PlsqlAnalysisMode.MULTI_PASS


@pytest.mark.parametrize(
    ("assignments", "line_breaks", "expected_score", "mode"),
    [
        (3, 11, 99.99, PlsqlAnalysisMode.SINGLE_PASS),
        (2, 24, 100.0, PlsqlAnalysisMode.MULTI_PASS),
    ],
)
def test_plsql_workload_exact_score_boundary(
    assignments: int, line_breaks: int, expected_score: float, mode: PlsqlAnalysisMode
) -> None:
    source = (
        "BEGIN " + "IF flag THEN NULL; END IF; " * 69
        + "value := 1; " * assignments + "END;"
    ).replace(";", ";\n", line_breaks)
    result = assess_plsql_complexity(source)
    assert result.score == expected_score
    assert result.mode is mode


def test_plsql_workload_mapping_expression_complexity_is_not_discounted() -> None:
    direct = ", ".join(f"c{i} = source.c{i}" for i in range(60))
    calculated = ", ".join(f"c{i} = source.c{i} + 1" for i in range(60))
    simple = assess_plsql_complexity(f"BEGIN UPDATE target SET {direct}; END;")
    complex_result = assess_plsql_complexity(
        f"BEGIN UPDATE target SET {calculated}; END;"
    )
    assert simple.metrics.logical_units == 1
    assert complex_result.metrics.logical_units == 61
    assert complex_result.metrics.complex_mappings == 60
    case_result = assess_plsql_complexity(
        "BEGIN UPDATE target SET flag = CASE WHEN value > 0 THEN 1 ELSE 0 END; END;"
    )
    assert case_result.metrics.logical_units == 2
    assert case_result.metrics.decisions == 1
    assert case_result.metrics.complex_mappings == 0


def test_plsql_workload_expression_size_and_declarations() -> None:
    result = assess_plsql_complexity(
        "CREATE FUNCTION f(p_id NUMBER) RETURN NUMBER IS "
        "a NUMBER; b NUMBER; BEGIN "
        "a := " + " + ".join(["p_id"] * 30) + "; RETURN a; END;"
    )
    assert result.metrics.logical_units == 4.5
    expression = " + ".join(["p_id"] * 30)
    returned = assess_plsql_complexity(f"BEGIN RETURN {expression}; END;")
    assert returned.metrics.logical_units == 3
    conditional = assess_plsql_complexity(
        f"BEGIN IF {expression} > 0 THEN NULL; END IF; END;"
    )
    assert conditional.metrics.logical_units == 4
    assert conditional.metrics.decisions == 1


def test_plsql_workload_sql_projection_expressions_and_grouping() -> None:
    calculated = ", ".join(f"a.c{i} + 1 AS result_{i}" for i in range(60))
    result = assess_plsql_complexity(
        f"BEGIN INSERT INTO target SELECT {calculated} FROM source a; END;"
    )
    assert result.metrics.logical_units == 61
    assert result.metrics.complex_mappings == 60
    grouped = assess_plsql_complexity("""
BEGIN
    SELECT a.id AS identifier, SUM(a.total) amount,
           SUM(a.total) OVER (PARTITION BY a.id)
    INTO id, amount, window_amount
    FROM source a
    GROUP BY a.id HAVING SUM(a.total) > 0;
END;
""")
    assert grouped.metrics.sql_statements == 1
    assert grouped.metrics.grouping_clauses == 2
    assert grouped.metrics.aggregates == 3
    assert grouped.metrics.windows == 1
    assert grouped.metrics.complex_mappings == 0
    assert grouped.metrics.logical_units == 7


def test_plsql_workload_direct_projection_and_bind_mappings_remain_simple() -> None:
    projection = ", ".join(f"a.c{i} result_{i}" for i in range(200))
    result = assess_plsql_complexity(
        f"BEGIN INSERT INTO target SELECT {projection} FROM source a; END;"
    )
    assert result.metrics.logical_units == 1
    assert assess_plsql_complexity(
        "BEGIN INSERT INTO target (a, b, c) VALUES (:a, -1, +2); END;"
    ).metrics.logical_units == 1
    assert assess_plsql_complexity(
        "BEGIN SELECT a.* INTO item FROM source a; END;"
    ).metrics.logical_units == 1


def test_plsql_workload_case_assignment_has_statement_and_decision_units() -> None:
    result = assess_plsql_complexity(
        "BEGIN flag := CASE WHEN value > 0 THEN 1 ELSE 0 END; END;"
    )
    assert result.metrics.logical_units == 2
    assert result.metrics.decisions == 1


def test_plsql_workload_case_in_return_and_if_condition() -> None:
    result = assess_plsql_complexity("""
BEGIN
    IF CASE WHEN value > 0 THEN 1 ELSE 0 END = 1 THEN
        RETURN CASE WHEN enabled THEN 1 ELSE 0 END;
    END IF;
END;
""")
    assert result.metrics.decisions == 3
    assert result.metrics.logical_units == 4


@pytest.mark.parametrize(
    "wrapper",
    ["IF enabled THEN {body} END IF;", "CASE id WHEN 1 THEN {body} END CASE;",
     "BEGIN NULL; EXCEPTION WHEN OTHERS THEN {body} END;"],
)
def test_plsql_workload_merge_clauses_inside_controls_are_not_branches(wrapper: str) -> None:
    merge = (
        "MERGE INTO target t USING source s ON (t.id = s.id) "
        "WHEN MATCHED THEN UPDATE SET t.a = s.a "
        "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id);"
    )
    result = assess_plsql_complexity("BEGIN " + wrapper.format(body=merge) + " END;")
    assert result.metrics.sql_statements == 1
    assert result.metrics.decisions == (0 if "EXCEPTION" in wrapper else 1)
    assert result.metrics.exception_handlers == (1 if "EXCEPTION" in wrapper else 0)


def test_plsql_workload_cursor_header_query_is_not_a_nested_query() -> None:
    result = assess_plsql_complexity("""
BEGIN
    FOR item IN (SELECT id FROM source WHERE id IN (SELECT id FROM eligible)) LOOP
        process_row();
    END LOOP;
END;
""")
    assert result.metrics.additional_query_blocks == 1
    assert result.metrics.sql_statements == 1
    assert result.metrics.logical_units == 4


def test_plsql_workload_inline_header_declarations_do_not_count_as_signature() -> None:
    result = assess_plsql_complexity(
        "CREATE OR REPLACE PROCEDURE p(a NUMBER DEFAULT 1) AS "
        "value NUMBER; BEGIN value := -1; END p;"
    )
    assert result.metrics.logical_units == 0.5


@pytest.mark.parametrize("source", [
    "BEGIN x := (1 + 2; END;",
    "BEGIN x := 1); END;",
    "BEGIN process_row() END;",
])
def test_plsql_workload_structure_errors_do_not_force_multiple_passes(source: str) -> None:
    result = assess_plsql_complexity(source)
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS


def test_plsql_workload_nested_regions_and_procedural_case() -> None:
    result = assess_plsql_complexity("""
BEGIN
    CASE value
        WHEN 1 THEN
            IF enabled THEN process_row(); END IF;
        WHEN 2 THEN
            IF enabled THEN process_row(); END IF;
    END CASE;
END;
""")
    assert result.metrics.decisions == 4
    assert result.metrics.max_control_depth == 2
    assert result.metrics.nested_control_units == 2
    assert result.metrics.logical_units == 6
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS
    assert result == assess_plsql_complexity("""
BEGIN
    CASE value
        WHEN 1 THEN
            IF enabled THEN process_row(); END IF;
        WHEN 2 THEN
            IF enabled THEN process_row(); END IF;
    END CASE;
END;
""")
    with pytest.raises(FrozenInstanceError):
        result.metrics.logical_units = 0


def test_plsql_workload_comments_do_not_increase_code_lines() -> None:
    plain = assess_plsql_complexity("BEGIN\nvalue := 1;\nEND;")
    commented = assess_plsql_complexity(
        "-- IF LOOP\nBEGIN -- CASE\n/* JOIN\nSELECT */\nvalue := 1;\nEND;"
    )
    assert plain.metrics == commented.metrics
    assert plain.score == commented.score


@pytest.mark.parametrize("query", [
    "SELECT id FROM records",
    "(SELECT id FROM records)",
    "WITH items AS (SELECT id FROM records) SELECT id FROM items",
])
def test_plsql_workload_static_open_cursor_is_not_dynamic_sql(query: str) -> None:
    result = assess_plsql_complexity(
        f"BEGIN OPEN items FOR {query}; END;"
    )
    assert result.metrics.sql_statements == 1
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS


def test_plsql_workload_short_deep_source_and_long_literal_have_bounded_corrections() -> None:
    result = assess_plsql_complexity(
        "BEGIN " + "LOOP " * 10 + "NULL; " + "END LOOP; " * 10 + "END;"
    )
    assert result.metrics.logical_units == 10
    assert result.metrics.max_control_depth == 10
    assert result.score == 40.02
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS
    literal = "\n".join(["IF LOOP JOIN"] * 1000)
    wide = assess_plsql_complexity(
        f"BEGIN INSERT INTO target (text) VALUES (q'[{literal}]'); END;"
    )
    assert wide.metrics.logical_units == 1
    assert wide.metrics.physical_code_lines == 1000
    assert wide.metrics.adjusted_code_lines == 2
    assert wide.score == 12
    assert wide.mode is PlsqlAnalysisMode.SINGLE_PASS


def test_plsql_workload_local_subprogram_does_not_force_multiple_passes() -> None:
    result = assess_plsql_complexity("""
CREATE PROCEDURE outer_proc IS
    PROCEDURE inner_proc IS BEGIN NULL; END;
BEGIN inner_proc(); END;
""")
    assert result.mode is PlsqlAnalysisMode.SINGLE_PASS


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


def test_oracle_package_member_ends_after_nested_blocks() -> None:
    source = """
CREATE OR REPLACE PACKAGE BODY pkg_vald AS
  PROCEDURE ins_vald_log IS
  BEGIN
    BEGIN
      NULL;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        NULL;
    END;
    IF 1 = 1 THEN
      NULL;
    END IF;
  END;

  PROCEDURE validate_rule IS
    FUNCTION local_count RETURN NUMBER IS
    BEGIN
      RETURN 1;
    END;
  BEGIN
    FOR item IN 1..local_count LOOP
      CASE item
        WHEN 1 THEN NULL;
        ELSE NULL;
      END CASE;
    END LOOP;
  END;
END;
""".lstrip()

    functions, diagnostics = OraclePlsqlAnalyzer().extract_functions(
        SourceFile("pkg_vald.pkb", source)
    )

    assert [
        (function.name, function.source_range.start_line, function.source_range.end_line)
        for function in functions
    ] == [
        ("ins_vald_log", 2, 13),
        ("validate_rule", 15, 27),
    ]
    assert "PROCEDURE validate_rule" not in functions[0].source
    assert "FUNCTION local_count" in functions[1].source
    assert not diagnostics


def test_oracle_ignores_declarations_in_comments_and_strings() -> None:
    source = """
-- CREATE PACKAGE BODY fake AS
CREATE PACKAGE BODY "Real$Package" AS
  text_value VARCHAR2(100) := 'PROCEDURE fake IS BEGIN NULL; END;';

  PROCEDURE "do$work" IS
  BEGIN
    NULL;
  END;
END;
""".lstrip()

    functions, diagnostics = OraclePlsqlAnalyzer().extract_functions(
        SourceFile("real.pkb", source)
    )

    assert [(function.module, function.name) for function in functions] == [
        ("Real$Package", "do$work")
    ]
    assert (
        functions[0].source_range.start_line,
        functions[0].source_range.end_line,
    ) == (5, 8)
    assert not diagnostics
