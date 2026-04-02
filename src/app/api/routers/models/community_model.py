from pydantic import BaseModel


class CreatePostRequest(BaseModel):
    text: str
    title: str | None = None
    tag: str | None = None
    image_url: str | None = None


class UpdatePostRequest(BaseModel):
    text: str | None = None
    title: str | None = None
    tag: str | None = None
    image_url: str | None = None


class CommentRequest(BaseModel):
    text: str
    parent_id: int | None = None


class CreatePollRequest(BaseModel):
    question: str
    options: list[str]


class VotePollRequest(BaseModel):
    option_id: int
