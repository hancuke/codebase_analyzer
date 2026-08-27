from ..domain.reference import Reference, ResolveResult, ResolveStatus
from ..repository.symbol import SymbolRepository


class SimpleResolver:
    def resolve(self, reference: Reference, repository: SymbolRepository) -> ResolveResult:
        candidates = repository.find_by_name(reference.target_name)
        if not candidates and "." in reference.target_name:
            candidates = repository.find_by_name(reference.target_name.rsplit(".", 1)[-1])
        if not candidates:
            return ResolveResult(
                status=ResolveStatus.UNRESOLVED,
                reason=f"Symbol not found: {reference.target_name}",
            )
        if len(candidates) > 1:
            return ResolveResult(
                status=ResolveStatus.AMBIGUOUS,
                candidates=tuple(symbol.id for symbol in candidates),
            )
        return ResolveResult(status=ResolveStatus.RESOLVED, target=candidates[0].id)
