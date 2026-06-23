from pydantic import BaseModel, Field, model_validator


class GuestsFields(BaseModel):
    """Mixin: structural guests fields with validation.

    Used by Create-schemas (CreateBookingRequest, WalkinBookingCreate).
    View-schemas declare the same fields without the validator —
    DB is source of truth on the way out.
    """

    adults: int = Field(default=1, ge=1, le=8)
    children: int = Field(default=0, ge=0, le=6)
    infants: int = Field(default=0, ge=0, le=4)
    child_ages: list[int] | None = None

    @model_validator(mode="after")
    def _check_child_ages(self) -> "GuestsFields":
        if self.child_ages is None:
            return self
        if len(self.child_ages) != self.children:
            raise ValueError(
                f"child_ages length {len(self.child_ages)} != children {self.children}"
            )
        for age in self.child_ages:
            if not (0 <= age <= 17):
                raise ValueError(f"child age {age} out of range 0..17")
        return self
