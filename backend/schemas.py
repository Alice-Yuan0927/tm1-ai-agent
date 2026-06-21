from pydantic import BaseModel, Field


class QuestionRequest(BaseModel):
    question: str
    history: list[dict] = Field(default_factory=list)
    selected_cubes: list[str] = Field(default_factory=list)
    mode: str = "analyst"  # "analyst" (default) | "developer"


class EmailRequest(BaseModel):
    to: str
    question: str
    chosen_cube: str
    reasoning: str
    data_row_count: int
    data_preview: list[dict] = Field(default_factory=list)
    analysis: str
    history: list[dict] = Field(default_factory=list)  # full conversation [{question, analysis}, ...]
    excel_sources: list[dict] = Field(default_factory=list)


class TM1ConfigRequest(BaseModel):
    llm_provider: str = "openai"
    llm_model: str = ""
    cube_select_temperature: float = 0.0
    mdx_temperature: float = 0.0
    attribute_intent_temperature: float = 0.0
    semantic_profile_temperature: float = 0.2
    analysis_temperature: float = 0.2
    suggestions_temperature: float = 0.4
    address: str
    port: int
    user: str
    password: str = ""
    namespace: str = ""
    ssl: bool = False
    verify: bool = False
    async_requests_mode: bool = False
