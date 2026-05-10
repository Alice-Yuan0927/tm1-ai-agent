from pydantic import BaseModel, Field


class QuestionRequest(BaseModel):
    question: str
    history: list[dict] = Field(default_factory=list)


class EmailRequest(BaseModel):
    to: str
    question: str
    chosen_cube: str
    reasoning: str
    data_row_count: int
    data_preview: list[dict] = Field(default_factory=list)
    analysis: str
    history: list[dict] = Field(default_factory=list)  # full conversation [{question, analysis}, ...]
