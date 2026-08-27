from codegraph import (
    DependencyBuilder,
    DependencyGraph,
    EntryPoint,
    InMemorySymbolRepository,
    ReferenceKind,
    ResolvedReference,
    SimpleResolver,
    Symbol,
    SymbolId,
    SymbolKind,
    VbaParser,
    JavaParser,
    CodeProject,
)


def symbol(value, name=None, language="vba"):
    name = name or value.rsplit(":", 1)[-1]
    return Symbol(SymbolId(value), name, name, SymbolKind.FUNCTION, language, "m", "m")


def edge(a, b):
    return ResolvedReference(SymbolId(a), SymbolId(b), ReferenceKind.CALL)


def test_vba_parser_and_builder():
    caller = symbol("vba:m:caller", "caller")
    target = symbol("vba:m:Validate", "Validate")
    repo = InMemorySymbolRepository([caller, target])
    refs = DependencyBuilder(VbaParser(), SimpleResolver(), repo).build(
        caller, "If Validate() Then\n    Call Validate\nEnd If"
    )
    assert [ref.target for ref in refs] == [target.id, target.id]


def test_java_parser_extracts_qualified_calls():
    current = symbol("java:C:create", "create", "java")
    refs = JavaParser().parse(current, "public void create() { service.create(); }")
    assert [ref.target_name for ref in refs] == ["create", "service.create"]


def test_graph_queries_cycles_and_replace():
    graph = DependencyGraph()
    graph.replace_outgoing(SymbolId("A"), [edge("A", "B")])
    graph.add(edge("B", "C"))
    graph.add(edge("C", "A"))
    assert graph.dependencies_of(SymbolId("A")) == {SymbolId("B")}
    assert graph.descendants_of(SymbolId("A")) == {SymbolId("B"), SymbolId("C")}
    assert graph.ancestors_of(SymbolId("C")) == {SymbolId("A"), SymbolId("B")}
    graph.replace_outgoing(SymbolId("A"), [edge("A", "D")])
    assert graph.dependencies_of(SymbolId("A")) == {SymbolId("D")}
    assert SymbolId("A") not in graph.dependents_of(SymbolId("B"))


def test_project_entry_point_and_impact():
    a, b, c = symbol("vba:m:A", "A"), symbol("vba:m:B", "B"), symbol("vba:m:C", "C")
    repo = InMemorySymbolRepository([a, b, c])
    builder = DependencyBuilder(VbaParser(), SimpleResolver(), repo)
    project = CodeProject(
        symbols=[a, b, c],
        sources={a.id: "Call B", b.id: "Call C", c.id: ""},
        builder=builder,
    )
    entry = EntryPoint(a.id, "event")
    project.add_entry_point(entry)
    project.rebuild_all()
    assert {s.id for s in project.analyze_entry_point(a.id).symbols} == {a.id, b.id, c.id}
    assert project.analyze_impact(c.id) == [entry]
