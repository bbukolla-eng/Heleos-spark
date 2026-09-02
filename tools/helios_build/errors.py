"""Build Fabric exception hierarchy."""


class BuildFabricError(Exception):
    """Base exception for repository-only Build Fabric failures."""


class ContractError(BuildFabricError):
    """A JSON document or in-memory value violates a frozen contract."""


class StateRootError(BuildFabricError):
    """The external Build Fabric state root is unsafe or invalid."""


class CollisionError(BuildFabricError):
    """Two graph tasks claim an overlapping owned resource."""


class TransitionError(BuildFabricError):
    """A graph lifecycle transition is not legal."""
