from pydantic import BaseModel, Field


class CreatePostRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    title: str | None = Field(None, max_length=300)
    tag: str | None = Field(None, max_length=50)
    image_url: str | None = Field(None, max_length=500)


class UpdatePostRequest(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=5000)
    title: str | None = Field(None, max_length=300)
    tag: str | None = Field(None, max_length=50)
    image_url: str | None = Field(None, max_length=500)


class CommentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    parent_id: int | None = None


class CreatePollRequest(BaseModel):
    question: str = Field(..., min_length=5, max_length=500)
    options: list[str] = Field(..., min_length=2, max_length=10)


class VotePollRequest(BaseModel):
    option_id: int = Field(..., gt=0)
