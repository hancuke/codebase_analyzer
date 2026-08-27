# CodeGraph 使用指南

`codegraph` 从函数或方法的源码中提取调用引用，解析为确定的 Symbol，并建立可双向查询的直接依赖图。

## 安装和导入

本项目使用 `src` 布局。未安装为 package 时，可从项目根目录运行示例：

```bash
PYTHONPATH=src python3 example.py
```

核心接口可直接从 `codegraph` 导入：

```python
from codegraph import (
    CodeProject,
    DependencyBuilder,
    EntryPoint,
    InMemorySymbolRepository,
    SimpleResolver,
    Symbol,
    SymbolId,
    SymbolKind,
    VbaParser,
)
```

## 核心概念

| 对象 | 用途 |
| --- | --- |
| `Symbol` | 可被引用的函数、方法、事件等代码实体。 |
| `SymbolId` | Symbol 的全局唯一标识；图中所有边均以它为端点。 |
| `VbaParser` / `JavaParser` | 从一段源码中提取尚未解析的 `Reference`。 |
| `SimpleResolver` | 按名称在 `SymbolRepository` 中解析 `Reference`。 |
| `DependencyBuilder` | 组合 Parser 与 Resolver，产出可加入图的 `ResolvedReference`。 |
| `DependencyGraph` | 维护直接依赖边，支持正向和反向递归查询。 |
| `CodeProject` | 面向应用层的门面，组合仓库、构建器、依赖图和入口点。 |

## 用例：建立 VBA 调用图

以下示例表示保存按钮事件调用 `Validate`，`Validate` 再调用 `isDuplicated`：

```python
from codegraph import (
    CodeProject,
    DependencyBuilder,
    SimpleResolver,
    Symbol,
    SymbolId,
    SymbolKind,
    VbaParser,
)


def vba_symbol(symbol_id: str, name: str, kind: SymbolKind) -> Symbol:
    return Symbol(
        id=SymbolId(symbol_id),
        name=name,
        qualified_name=f"frmModiRec.{name}",
        kind=kind,
        language="vba",
        module="frmModiRec",
        file="frmModiRec.bas",
    )


save_click = vba_symbol(
    "vba:frmModiRec:bSave_Click",
    "bSave_Click",
    SymbolKind.EVENT,
)
validate = vba_symbol(
    "vba:frmModiRec:Validate",
    "Validate",
    SymbolKind.FUNCTION,
)
duplicated = vba_symbol(
    "vba:frmModiRec:isDuplicated",
    "isDuplicated",
    SymbolKind.FUNCTION,
)

project = CodeProject(
    symbols=[save_click, validate, duplicated],
    sources={
        save_click.id: """
            Private Sub bSave_Click()
                If Validate() Then
                    Call isDuplicated
                End If
            End Sub
        """,
        validate.id: "If isDuplicated() Then MsgBox \"Duplicate\"",
        duplicated.id: "",
    },
)

# builder 需要与 project 使用同一个 SymbolRepository。
project.builder = DependencyBuilder(
    parser=VbaParser(),
    resolver=SimpleResolver(),
    repository=project.repository,
)
project.rebuild_all()
```

`rebuild_all()` 会逐一解析已注册的源码，并以新的解析结果替换每个 Symbol 原有的出边。未解析或解析不唯一的引用不会进入图。

## 查询直接和递归依赖

```python
assert project.dependencies_of(save_click.id) == {
    validate.id,
    duplicated.id,
}

assert project.dependents_of(duplicated.id) == {
    save_click.id,
    validate.id,
}

assert project.descendants_of(save_click.id) == {
    validate.id,
    duplicated.id,
}

assert project.ancestors_of(duplicated.id) == {
    save_click.id,
    validate.id,
}
```

`dependencies_of()` 与 `dependents_of()` 只返回直接相邻节点。`descendants_of()` 和 `ancestors_of()` 递归遍历，内部会处理循环依赖，且返回结果不包含查询起点自身。

## 用例：入口点分析

将 UI 事件、Controller 方法等登记为 `EntryPoint`：

```python
from codegraph import EntryPoint

project.add_entry_point(EntryPoint(
    symbol=save_click.id,
    kind="form_event",
))

context = project.analyze_entry_point(save_click.id)

print(context.entry_point)
# vba:frmModiRec:bSave_Click

for symbol in context.symbols:
    print(symbol.id, symbol.qualified_name)
```

`AnalysisContext` 包含：

* `entry_point`：分析的入口 SymbolId；
* `symbols`：入口及其所有递归依赖的已注册 Symbol；
* `paths`：从入口到叶子依赖的 `DependencyPath` 列表。

可将 `context.symbols` 中的文件和模块元数据用于加载源码，再将所需内容组装成 LLM 或报告的输入。依赖图本身不保存源码，也不保存 LLM 分析结果。

## 用例：增量更新与变更影响

当 `Validate` 的源码变更后，更新其源码并仅重建该节点：

```python
project.sources[validate.id] = """
    If isDuplicated() Then
        MsgBox "Duplicate"
    End If
"""
project.rebuild(validate.id)

affected = project.analyze_impact(validate.id)
assert affected == [
    EntryPoint(symbol=save_click.id, kind="form_event"),
]
```

`rebuild(symbol_id)` 调用 `DependencyGraph.replace_outgoing()`：它会清除该 Symbol 的旧调用边，再写入重新解析得到的新调用边，因此不会残留已删除的调用关系。

`analyze_impact(changed)` 从变更 Symbol 沿入边向上遍历，并返回已登记且受影响的入口点。

## 直接使用 DependencyGraph

若调用关系已由其他工具产生，可绕过 Parser 和 Resolver，直接写入已解析的边：

```python
from codegraph import DependencyGraph, ReferenceKind, ResolvedReference, SymbolId

graph = DependencyGraph()
a = SymbolId("vba:m:A")
b = SymbolId("vba:m:B")
c = SymbolId("vba:m:C")

graph.add(ResolvedReference(a, b, ReferenceKind.CALL))
graph.add(ResolvedReference(b, c, ReferenceKind.CALL))

assert graph.descendants_of(a) == {b, c}
assert graph.ancestors_of(c) == {a, b}
```

使用 `replace_outgoing()` 批量覆盖一个节点的直接依赖：

```python
graph.replace_outgoing(a, [
    ResolvedReference(a, c, ReferenceKind.CALL),
])

assert graph.dependencies_of(a) == {c}
```

传给 `replace_outgoing(source, references)` 的所有 `ResolvedReference.source` 必须等于 `source`，否则会抛出 `ValueError`。

## Java Parser

`JavaParser` 的使用方式与 `VbaParser` 相同，只替换 Parser：

```python
from codegraph import JavaParser

builder = DependencyBuilder(
    parser=JavaParser(),
    resolver=SimpleResolver(),
    repository=project.repository,
)
```

对于下列代码：

```java
public void create() {
    service.create();
}
```

Parser 会提取 `service.create`。当前 `SimpleResolver` 首先以完整名称查找；若未找到且引用包含 `.`，会以最后一段名称（这里为 `create`）回退查找。因此同名 Symbol 多于一个时结果为 `AMBIGUOUS`，该引用不会进入依赖图。需要更精确的 Java 类型、包和 import 解析时，应提供语言专用 Resolver。

## 限制

第一版的 Parser 是轻量级文本解析，不是完整 AST。`SimpleResolver` 只依据名称匹配，适合唯一命名或已预先规范化的 Function Chunks。对于重载、同名函数、对象成员类型、import、VBA 默认成员等复杂场景，应实现更具体的 Resolver，而无需修改语言无关的 `DependencyGraph` 和分析 API。
