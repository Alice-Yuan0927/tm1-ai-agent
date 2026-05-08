from pydantic import BaseModel, Field


class QuestionRequest(BaseModel):
    question: str


class EmailRequest(BaseModel):
    to: str
    question: str
    chosen_cube: str
    chosen_view: str
    reasoning: str
    data_row_count: int
    data_preview: list[dict] = Field(default_factory=list)
    analysis: str
