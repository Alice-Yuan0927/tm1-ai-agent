"""Shared parameter bundle for the MDX generation / repair / planning path."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MdxContext:
    """Everything needed to generate or repair MDX for one cube."""

    question: str
    cube_schema: dict
    history: list[dict] = field(default_factory=list)
    model_profile: dict | None = None
    grounded_members: list[dict] | None = None
    similar_queries: list[dict] | None = None

    @property
    def cube_name(self) -> str:
        return str(self.cube_schema.get("cube", ""))
