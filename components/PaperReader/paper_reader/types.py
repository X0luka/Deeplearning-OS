from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Selection(StrictModel):
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    text: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="after")
    def valid_rectangle(self):
        x0, top, x1, bottom = self.bbox
        if not (0 <= x0 < x1 and 0 <= top < bottom):
            raise ValueError("invalid_selection_rectangle")
        return self


class Question(StrictModel):
    document: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["selection", "fulltext", "formula"]
    question: str = Field(min_length=1, max_length=4000)
    selection: Selection | None = None
    supplied_formula: str | None = Field(default=None, max_length=2000)
    previous: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def require_selection(self):
        if self.mode == "selection" and self.selection is None:
            raise ValueError("selection_required")
        return self


class Citation(StrictModel):
    evidence_id: str
    quote: str | None = Field(default=None, min_length=8, max_length=2500)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def one_reference_format(self):
        if self.quote is not None:
            if self.start_line is not None or self.end_line is not None:
                raise ValueError("ambiguous_citation_format")
        elif (
            self.start_line is None
            or self.end_line is None
            or self.end_line < self.start_line
            or self.end_line - self.start_line > 11
        ):
            raise ValueError("invalid_citation_line_range")
        return self


class Claim(StrictModel):
    kind: Literal["paper", "background", "uncertain"]
    text: str = Field(min_length=1, max_length=3000)
    citations: list[Citation] = Field(default_factory=list, max_length=12)


class Answer(StrictModel):
    status: Literal["answered", "partial", "insufficient"]
    claims: list[Claim] = Field(min_length=1, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def cited_paper_claims(self):
        for claim in self.claims:
            if claim.kind == "paper" and not claim.citations:
                raise ValueError("paper_claim_requires_citation")
        if self.status in {"partial", "insufficient"} and not self.limitations:
            raise ValueError("limitations_required")
        if self.status == "answered" and not any(c.kind == "paper" for c in self.claims):
            raise ValueError("answered_requires_paper_evidence")
        return self
