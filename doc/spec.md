可以。按照你现在的目标，我建议**第一版不要做成一个“大而全的代码分析平台”**，而是先把下面这条链做扎实：

```text
Source / Function Chunks
        │
        ▼
      Parser
        │
        ▼
    Reference
        │
        ▼
     Resolver
        │
        ▼
Resolved Reference
        │
        ▼
 DependencyGraph
        │
        ├───────────────┐
        ▼               ▼
Entry Point          Change Impact
    Analysis             Analysis
        │               │
        └───────┬───────┘
                ▼
        Analysis Context
                │
                ▼
               LLM
```

下面这份可以直接作为第一版的 **Design Spec / Implementation Spec**。

---

# Code Dependency Analysis Framework

## 1. Overview

### 1.1 Goal

构建一个轻量、可扩展的代码依赖分析框架，用于从已经拆分好的 function/method chunks 中：

1. 解析函数之间的引用关系；
2. 将引用解析到唯一的代码 Symbol；
3. 构建直接依赖图；
4. 根据入口函数递归获取完整依赖链；
5. 当函数发生变化时，反向查找受影响的调用链；
6. 为后续 LLM 分析提供准确、可控的代码上下文。

第一版重点支持：

* Microsoft Access VBA
* Java
* 后续可扩展 PL/SQL、Python、C# 等

---

# 2. Design Principles

第一版遵循以下原则。

### 2.1 Simple First

优先：

> 简单、容易理解、容易测试、容易修改

不提前引入：

* Neo4j
* NetworkX
* AST framework abstraction
* Graph database
* distributed processing
* complicated event system

第一版使用 Python 标准数据结构即可实现。

---

### 2.2 Direct Dependency Only

Graph 只保存直接依赖：

```text
A → B
B → C
```

不保存：

```text
A → C
```

间接依赖通过 DFS/BFS 查询。

---

### 2.3 Parser 不负责 Resolve

Parser 只回答：

> 代码中出现了什么引用？

Resolver 回答：

> 这个引用实际指向哪个 Symbol？

因此：

```text
Parser
  ↓
Reference
  ↓
Resolver
  ↓
Resolved Reference
```

---

### 2.4 Graph 不负责 Resolve

DependencyGraph 只接受已经解析完成的：

```text
ResolvedReference
```

Graph 不知道 VBA、Java，也不负责名称解析。

---

### 2.5 Code Graph 和 LLM Analysis 分离

Dependency Graph：

> 客观描述代码结构。

LLM Analysis：

> 理解代码含义和业务逻辑。

不要把 LLM 分析结果混入 DependencyGraph。

---

# 3. Architecture

```text
                    ┌─────────────────┐
                    │ Function Chunks │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │     Parser      │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │    Reference    │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │    Resolver     │
                    └────────┬────────┘
                             │
                             ▼
                 ┌────────────────────────┐
                 │  Resolved Reference    │
                 └───────────┬────────────┘
                             │
                             ▼
                 ┌────────────────────────┐
                 │    DependencyGraph     │
                 └───────────┬────────────┘
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
       Entry Point Analysis        Change Impact
                │                         │
                └────────────┬────────────┘
                             ▼
                    Analysis Context
                             │
                             ▼
                            LLM
```

---

# 4. Core Domain Model

第一版只定义几个核心对象：

```text
Symbol
SymbolId

Reference
ReferenceKind
SourceLocation

ResolveResult
ResolveStatus

ResolvedReference

DependencyGraph
```

---

# 5. Symbol

`Symbol` 表示一个代码中可以被引用的实体。

```python
from dataclasses import dataclass
from enum import Enum


class SymbolKind(Enum):
    FUNCTION = "function"
    METHOD = "method"
    SUB = "sub"
    EVENT = "event"
    CLASS = "class"


@dataclass(frozen=True)
class SymbolId:
    value: str


@dataclass(frozen=True)
class Symbol:
    id: SymbolId
    name: str
    qualified_name: str
    kind: SymbolKind
    language: str
    module: str
    file: str
```

例如 VBA：

```python
Symbol(
    id=SymbolId("vba:frmModiRec:bSave_Click"),
    name="bSave_Click",
    qualified_name="frmModiRec.bSave_Click",
    kind=SymbolKind.EVENT,
    language="vba",
    module="frmModiRec",
    file="frmModiRec.bas",
)
```

Java：

```python
Symbol(
    id=SymbolId("java:CustomerController:create"),
    name="create",
    qualified_name="CustomerController.create",
    kind=SymbolKind.METHOD,
    language="java",
    module="CustomerController",
    file="CustomerController.java",
)
```

---

# 6. SymbolId

必须区分：

```text
name
```

和：

```text
SymbolId
```

例如项目中可能存在：

```text
frmA.Validate
frmB.Validate
frmC.Validate
```

所以：

```text
Validate
```

不是唯一身份。

而：

```text
vba:frmA:Validate
```

才是唯一身份。

第一版可以直接使用字符串：

```python
SymbolId("vba:frmA:Validate")
```

以后如果需要再改成结构化 ID。

---

# 7. SymbolRepository

Resolver 需要查询所有 Symbol，因此需要一个非常简单的 Repository。

```python
from typing import Iterable, Protocol


class SymbolRepository(Protocol):

    def get(
        self,
        symbol_id: SymbolId,
    ) -> Symbol | None:
        ...

    def find_by_name(
        self,
        name: str,
    ) -> list[Symbol]:
        ...

    def add(
        self,
        symbol: Symbol,
    ) -> None:
        ...

    def remove(
        self,
        symbol_id: SymbolId,
    ) -> None:
        ...
```

第一版实现：

```python
class InMemorySymbolRepository:

    def __init__(self):
        self._symbols: dict[SymbolId, Symbol] = {}
        self._by_name: dict[str, list[SymbolId]] = {}

    def add(self, symbol: Symbol) -> None:
        self._symbols[symbol.id] = symbol

        self._by_name.setdefault(
            symbol.name,
            [],
        ).append(symbol.id)

    def get(self, symbol_id: SymbolId):
        return self._symbols.get(symbol_id)

    def find_by_name(self, name: str):
        ids = self._by_name.get(name, [])
        return [
            self._symbols[symbol_id]
            for symbol_id in ids
        ]

    def remove(self, symbol_id: SymbolId) -> None:
        symbol = self._symbols.pop(
            symbol_id,
            None,
        )

        if symbol is None:
            return

        ids = self._by_name.get(symbol.name, [])
        if symbol_id in ids:
            ids.remove(symbol_id)
```

这已经足够支撑第一版。

---

# 8. Reference

Reference 是 Parser 的输出。

```python
class ReferenceKind(Enum):
    CALL = "call"
    EVENT = "event"
    INHERIT = "inherit"
    IMPLEMENT = "implement"
    USE = "use"
```

第一版只需要：

```text
CALL
```

然后：

```python
@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int
    column: int | None = None
```

Reference：

```python
@dataclass(frozen=True)
class Reference:
    source: SymbolId
    target_name: str
    kind: ReferenceKind
    location: SourceLocation | None = None
```

例如：

```python
Reference(
    source=SymbolId(
        "vba:frmModiRec:bSave_Click"
    ),
    target_name="Validate",
    kind=ReferenceKind.CALL,
    location=SourceLocation(
        file="frmModiRec.bas",
        line=10,
    ),
)
```

注意：

```text
target_name = Validate
```

而不是：

```text
target = frmModiRec.Validate
```

因为 Resolver 还没有完成。

---

# 9. Parser

Parser 的唯一职责：

> 从一个 Symbol 的 source code 中提取 Reference。

接口：

```python
class Parser(Protocol):

    def parse(
        self,
        symbol: Symbol,
        source: str,
    ) -> list[Reference]:
        ...
```

---

# 10. VBA Parser

第一版不需要追求完整 VBA AST。

可以先使用：

* regex
* keyword filtering
* 已知 symbol table

例如：

```python
class VbaParser:

    def parse(
        self,
        symbol: Symbol,
        source: str,
    ) -> list[Reference]:

        references = []

        for line_no, line in enumerate(
            source.splitlines(),
            start=1,
        ):
            calls = self._extract_calls(line)

            for name in calls:
                references.append(
                    Reference(
                        source=symbol.id,
                        target_name=name,
                        kind=ReferenceKind.CALL,
                        location=SourceLocation(
                            file=symbol.file,
                            line=line_no,
                        ),
                    )
                )

        return references
```

第一版可以从你现在已有的：

```text
CodeScanner.extract_dependencies()
```

迁移过来。

不要为了新架构重新发明 Parser。

---

# 11. Resolver

Resolver 的职责：

> 将 Reference 解析为具体 Symbol。

接口：

```python
class Resolver(Protocol):

    def resolve(
        self,
        reference: Reference,
        repository: SymbolRepository,
    ) -> "ResolveResult":
        ...
```

---

# 12. ResolveResult

不要简单：

```python
Symbol | None
```

因为现实世界存在：

```text
resolved
unresolved
ambiguous
external
```

定义：

```python
class ResolveStatus(Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    EXTERNAL = "external"
```

然后：

```python
@dataclass(frozen=True)
class ResolveResult:
    status: ResolveStatus
    target: SymbolId | None = None
    candidates: tuple[SymbolId, ...] = ()
    reason: str | None = None
```

---

# 13. 最简单的 Resolver

第一版可以非常简单：

```python
class SimpleResolver:

    def resolve(
        self,
        reference: Reference,
        repository: SymbolRepository,
    ) -> ResolveResult:

        candidates = repository.find_by_name(
            reference.target_name
        )

        if not candidates:
            return ResolveResult(
                status=ResolveStatus.UNRESOLVED,
                reason=(
                    f"Symbol not found: "
                    f"{reference.target_name}"
                ),
            )

        if len(candidates) > 1:
            return ResolveResult(
                status=ResolveStatus.AMBIGUOUS,
                candidates=tuple(
                    symbol.id
                    for symbol in candidates
                ),
            )

        return ResolveResult(
            status=ResolveStatus.RESOLVED,
            target=candidates[0].id,
        )
```

这就是第一版。

**不要一开始就设计复杂 Resolver。**

---

# 14. 后续 Resolver 的演进

第一版：

```text
target name
    ↓
repository.find_by_name()
```

后面可以逐步增加：

```text
1. Exact qualified name
2. Same module
3. Same class
4. Import
5. Package
6. Type information
7. Language-specific rule
8. Heuristic
```

最终：

```text
Resolver
   │
   ├── ExactMatch
   ├── SameModule
   ├── Import
   ├── TypeBased
   └── Fallback
```

但第一版不要实现这些。

---

# 15. ResolvedReference

Resolver 成功之后产生：

```python
@dataclass(frozen=True)
class ResolvedReference:
    source: SymbolId
    target: SymbolId
    kind: ReferenceKind
    location: SourceLocation | None = None
```

例如：

```text
Reference:

bSave_Click
    ↓
"Validate"
```

变成：

```text
ResolvedReference:

bSave_Click
    ↓
frmModiRec.Validate
```

---

# 16. DependencyBuilder

把：

```text
Parser
+
Resolver
```

组合起来。

```python
class DependencyBuilder:

    def __init__(
        self,
        parser: Parser,
        resolver: Resolver,
        repository: SymbolRepository,
    ):
        self.parser = parser
        self.resolver = resolver
        self.repository = repository

    def build(
        self,
        symbol: Symbol,
        source: str,
    ) -> list[ResolvedReference]:

        references = self.parser.parse(
            symbol,
            source,
        )

        result = []

        for reference in references:

            resolved = self.resolver.resolve(
                reference,
                self.repository,
            )

            if resolved.status != ResolveStatus.RESOLVED:
                continue

            result.append(
                ResolvedReference(
                    source=reference.source,
                    target=resolved.target,
                    kind=reference.kind,
                    location=reference.location,
                )
            )

        return result
```

这里有一个重要原则：

> **Unresolved Reference 不应该进入 DependencyGraph。**

否则 Graph 会产生错误关系。

---

# 17. DependencyGraph

Graph 只负责保存：

```text
Symbol → Symbol
```

第一版使用：

```python
class DependencyGraph:

    def __init__(self):
        self._outgoing: dict[
            SymbolId,
            set[SymbolId],
        ] = {}

        self._incoming: dict[
            SymbolId,
            set[SymbolId],
        ] = {}
```

为什么需要两个方向？

因为你有两个核心需求：

```text
Forward:
A → B → C
```

以及：

```text
Impact:
C ← B ← A
```

所以同时维护：

```text
outgoing
incoming
```

可以让两种查询都很快。

---

# 18. Graph Mutation API

```python
def add(
    self,
    reference: ResolvedReference,
) -> None:
    ...
```

实现：

```python
def add(
    self,
    reference: ResolvedReference,
) -> None:

    source = reference.source
    target = reference.target

    self._outgoing.setdefault(
        source,
        set(),
    ).add(target)

    self._incoming.setdefault(
        target,
        set(),
    ).add(source)
```

---

# 19. Remove

```python
def remove(
    self,
    reference: ResolvedReference,
) -> None:

    source = reference.source
    target = reference.target

    self._outgoing.get(
        source,
        set(),
    ).discard(target)

    self._incoming.get(
        target,
        set(),
    ).discard(source)
```

---

# 20. 最重要的 API：replace_outgoing

这是为了支持你的增量分析。

```python
def replace_outgoing(
    self,
    source: SymbolId,
    references: list[ResolvedReference],
) -> None:
    ...
```

逻辑：

```text
删除 source 原来的所有 outgoing edges
                 ↓
添加新的 outgoing edges
```

例如：

```text
原来：

A → B
A → C
```

重新解析 A 后：

```text
A → B
A → D
```

执行：

```python
graph.replace_outgoing(
    A,
    [
        A → B,
        A → D,
    ],
)
```

结果：

```text
A → B
A → D
```

而不是留下：

```text
A → C
```

---

# 21. Graph Query API

核心查询：

```python
def dependencies_of(
    self,
    symbol: SymbolId,
) -> set[SymbolId]:
    ...
```

表示：

> 谁是这个 Symbol 的直接依赖？

例如：

```text
A → B
B → C
```

：

```python
dependencies_of(A)
```

返回：

```text
{B}
```

---

反方向：

```python
def dependents_of(
    self,
    symbol: SymbolId,
) -> set[SymbolId]:
    ...
```

表示：

> 谁直接依赖这个 Symbol？

例如：

```text
dependents_of(C)
```

返回：

```text
{B}
```

---

# 22. Recursive Query

提供：

```python
descendants_of()
```

和：

```python
ancestors_of()
```

例如：

```text
A → B → C
```

：

```python
descendants_of(A)
```

得到：

```text
B
C
```

而：

```python
ancestors_of(C)
```

得到：

```text
B
A
```

---

# 23. DFS 实现

第一版直接使用 DFS。

```python
def descendants_of(
    self,
    symbol: SymbolId,
) -> set[SymbolId]:

    visited: set[SymbolId] = set()
    stack = [symbol]

    while stack:

        current = stack.pop()

        for dependency in self.dependencies_of(
            current
        ):
            if dependency in visited:
                continue

            visited.add(dependency)
            stack.append(dependency)

    return visited
```

反向完全一样：

```python
def ancestors_of(
    self,
    symbol: SymbolId,
) -> set[SymbolId]:

    visited: set[SymbolId] = set()
    stack = [symbol]

    while stack:

        current = stack.pop()

        for dependent in self.dependents_of(
            current
        ):
            if dependent in visited:
                continue

            visited.add(dependent)
            stack.append(dependent)

    return visited
```

---

# 24. 为什么 visited 必须存在？

真实项目可能存在：

```text
A → B
B → C
C → A
```

也就是循环依赖。

没有：

```python
visited
```

DFS 会无限循环。

所以 Graph traversal 第一版就应该正确处理 cycle。

---

# 25. DependencyPath

Graph 不保存 Path。

但分析的时候可以生成 Path。

例如：

```text
A
 ↓
B
 ↓
C
```

可以得到：

```python
@dataclass
class DependencyPath:
    nodes: list[SymbolId]
```

例如：

```python
DependencyPath(
    nodes=[A, B, C]
)
```

这个对象属于：

```text
Query / Analysis
```

而不是 Graph 的存储模型。

---

# 26. Entry Point

入口是另外一个概念。

例如：

```text
Access:

frmModiRec.bSave_Click
```

或者：

```text
Java:

CustomerController.create
```

统一成：

```python
@dataclass(frozen=True)
class EntryPoint:
    symbol: SymbolId
    kind: str
```

例如：

```python
EntryPoint(
    symbol=SymbolId(
        "vba:frmModiRec:bSave_Click"
    ),
    kind="form_event",
)
```

Java：

```python
EntryPoint(
    symbol=SymbolId(
        "java:CustomerController:create"
    ),
    kind="controller",
)
```

---

# 27. Entry Point Analysis

第一版只需要：

```python
def analyze_entry_point(
    graph: DependencyGraph,
    entry_point: SymbolId,
) -> set[SymbolId]:

    return {
        entry_point,
        *graph.descendants_of(entry_point),
    }
```

例如：

```text
bSave_Click
    ↓
Validate
    ↓
isDuplicated
    ↓
PKG_MODI.get_fin_data
```

返回：

```text
bSave_Click
Validate
isDuplicated
PKG_MODI.get_fin_data
```

然后从 Repository 获取对应 source code。

---

# 28. Analysis Context

不要直接把 Graph 给 LLM。

建立一个简单的 Context：

```python
@dataclass
class AnalysisContext:
    entry_point: SymbolId
    symbols: list[Symbol]
    paths: list[DependencyPath]
```

以后可以加入：

```text
source code
metadata
relationship
analysis history
```

但第一版先简单。

---

# 29. Change Impact

这是第二个核心用例。

如果：

```text
A → B → C
```

C 修改：

```python
graph.ancestors_of(C)
```

得到：

```text
B
A
```

然后筛选 EntryPoint：

```text
A = EntryPoint
B = Function
```

所以：

```text
Affected EntryPoint:

A
```

然后重新生成：

```text
A → B → C
```

对应的 AnalysisContext。

---

# 30. 完整增量流程

假设：

```text
PKG_MODI.update_modi_status
```

发生变化。

流程：

```text
ChangeSet
    ↓
Changed Symbol
    ↓
Parser
    ↓
Reference
    ↓
Resolver
    ↓
Resolved Reference
    ↓
Graph.replace_outgoing()
    ↓
Graph.ancestors_of(changed_symbol)
    ↓
Affected EntryPoints
    ↓
Rebuild AnalysisContext
    ↓
LLM
```

这就是你最终想实现的核心机制。

---

# 31. 推荐的 Project Facade

上面这些底层对象不应该直接暴露给普通调用者。

对外提供一个：

```python
CodeProject
```

例如：

```python
class CodeProject:

    def dependencies_of(
        self,
        symbol: SymbolId,
    ) -> set[SymbolId]:
        ...

    def dependents_of(
        self,
        symbol: SymbolId,
    ) -> set[SymbolId]:
        ...

    def descendants_of(
        self,
        symbol: SymbolId,
    ) -> set[SymbolId]:
        ...

    def ancestors_of(
        self,
        symbol: SymbolId,
    ) -> set[SymbolId]:
        ...

    def analyze_entry_point(
        self,
        entry_point: SymbolId,
    ) -> AnalysisContext:
        ...

    def analyze_impact(
        self,
        changed: SymbolId,
    ) -> list[EntryPoint]:
        ...
```

这样外部模块只需要理解：

```text
CodeProject
```

而不需要知道：

```text
Parser
Resolver
Graph
Repository
```

---

# 32. 第一版目录结构

建议保持非常简单：

```text
src/
└── codegraph/
    │
    ├── domain/
    │   ├── symbol.py
    │   ├── reference.py
    │   └── entrypoint.py
    │
    ├── parser/
    │   ├── base.py
    │   ├── vba.py
    │   └── java.py
    │
    ├── resolver/
    │   ├── base.py
    │   └── simple.py
    │
    ├── graph/
    │   └── dependency.py
    │
    ├── repository/
    │   └── symbol.py
    │
    ├── analysis/
    │   ├── dependency.py
    │   └── impact.py
    │
    └── project.py
```

第一版**不要**：

```text
factory/
strategy/
manager/
service/
handler/
provider/
```

到真正有复杂性的时候再抽象。

---

# 33. 最小可工作的实现

实际上核心代码可以非常少。

```python
parser = VbaParser()

repository = InMemorySymbolRepository()

resolver = SimpleResolver()

builder = DependencyBuilder(
    parser=parser,
    resolver=resolver,
    repository=repository,
)

graph = DependencyGraph()
```

处理一个 Function：

```python
resolved = builder.build(
    symbol,
    source,
)

graph.replace_outgoing(
    symbol.id,
    resolved,
)
```

查询：

```python
graph.dependencies_of(symbol.id)
```

递归：

```python
graph.descendants_of(symbol.id)
```

反向：

```python
graph.ancestors_of(symbol.id)
```

这就是第一版核心。

---

# 34. 一个完整 VBA 示例

源码：

```vb
Private Sub bSave_Click()

    If Validate() Then
        Call update_modi_status()
    End If

End Sub
```

Parser：

```text
Reference(
    source=bSave_Click,
    target_name="Validate"
)

Reference(
    source=bSave_Click,
    target_name="update_modi_status"
)
```

Resolver：

```text
Validate
    ↓
frmModiRec.Validate

update_modi_status
    ↓
PKG_MODI.update_modi_status
```

Resolved Reference：

```text
bSave_Click
    → frmModiRec.Validate

bSave_Click
    → PKG_MODI.update_modi_status
```

Graph：

```text
frmModiRec.bSave_Click
       ├── frmModiRec.Validate
       │
       └── PKG_MODI.update_modi_status
```

如果：

```text
frmModiRec.Validate
       ↓
isDuplicated
```

那么：

```text
descendants_of(bSave_Click)
```

得到：

```text
Validate
isDuplicated
PKG_MODI.update_modi_status
```

---

# 35. Java 同样工作

源码：

```java
public void create() {
    service.create();
}
```

Parser：

```text
Reference(
    source=CustomerController.create,
    target_name="service.create",
    kind=CALL
)
```

Resolver：

```text
service.create
       ↓
CustomerService.create
```

Graph：

```text
CustomerController.create
        ↓
CustomerService.create
```

核心代码完全不用修改。

只需要：

```text
JavaParser
JavaResolver
```

---

# 36. 第一版暂时明确不做什么

这是非常重要的。

第一版**不做**：

### 不做 Graph Database

不用 Neo4j。

### 不做 AST Universal Framework

不同语言先各自 Parser。

### 不做复杂 Type System

Resolver 先使用简单规则。

### 不做 LLM Resolver

依赖关系优先保证 deterministic。

### 不做 Dependency Cache

DFS/BFS 先解决性能问题。

### 不保存 Transitive Dependency

只保存：

```text
A → B
B → C
```

### 不把 Analysis Result 放进 Graph

Graph 只保存代码依赖。

---

# 37. 第一版的测试重点

核心测试应该非常容易写。

## Parser

```text
source
  ↓
references
```

测试：

```text
Call Validate()
→ Validate

Call update_modi_status()
→ update_modi_status
```

---

## Resolver

```text
Reference
  ↓
ResolveResult
```

测试：

```text
唯一匹配
→ RESOLVED

不存在
→ UNRESOLVED

多个匹配
→ AMBIGUOUS
```

---

## Graph

测试：

```text
A → B
B → C
```

应该：

```text
dependencies_of(A)
→ B

descendants_of(A)
→ B,C

dependents_of(C)
→ B

ancestors_of(C)
→ B,A
```

---

## Cycle

测试：

```text
A → B
B → C
C → A
```

确保：

```python
graph.descendants_of(A)
```

不会死循环。

---

# 38. 第一版完成后的能力

完成这套核心以后，你实际上已经拥有一个非常有价值的基础设施：

```text
                    CodeGraph
                        │
        ┌───────────────┼────────────────┐
        │               │                │
        ▼               ▼                ▼
   Dependency       Entry Point       Change
     Query            Query            Impact
        │               │                │
        ▼               ▼                ▼
   dependencies     descendants      ancestors
   dependents
```

然后 LLM 只是其中一个 consumer：

```text
                    CodeGraph
                        │
                        ▼
                AnalysisContext
                        │
             ┌──────────┼──────────┐
             ▼          ▼          ▼
            LLM       Reporter    RAG
```

这点非常重要。

**不要把这个框架设计成“LLM Dependency Analyzer”。**

它本质上应该是：

> **Code Dependency Graph Library**

LLM 只是使用这个 Library 的一个上层模块。

---

# 39. 最终推荐的依赖方向

整个 package 最终应该保持：

```text
domain
  ↑
parser
  ↑
resolver
  ↑
graph
  ↑
analysis
  ↑
project
  ↑
cli / llm / reporter
```

更准确地说，依赖应该是：

```text
                 domain
                /      \
               /        \
          parser       graph
             │           │
             ▼           ▼
          resolver    analysis
               \         /
                \       /
                 project
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
         CLI       LLM     Reporter
```

其中：

> **domain 是最稳定的部分；语言差异集中在 Parser / Resolver；Graph 保持完全语言无关。**

---

## 第一版的核心边界

最后可以把整个设计压缩成下面 5 个问题：

```text
Parser
  └─ “源码里引用了谁？”

Reference
  └─ “这个引用是什么？”

Resolver
  └─ “这个名字实际指向哪个 Symbol？”

ResolvedReference
  └─ “已经确认 A → B。”

DependencyGraph
  └─ “保存并查询所有 A → B。”
```

然后只围绕两个业务操作：

```python
graph.descendants_of(entry_point)
```

> **从入口向下，获取 LLM 需要分析的整个依赖范围。**

以及：

```python
graph.ancestors_of(changed_function)
```

> **从变化函数向上，找到需要重新分析的调用链。**

这已经足够支撑你当前的 **Access VBA + Java + Function Chunk + LLM 增量文档分析** 场景。后续真正出现复杂性时，再逐步增强 Resolver、Path、Scope、External Dependency、Persistence 和缓存，而不需要推翻这个核心模型。
