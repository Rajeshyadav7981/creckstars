from pydantic import BaseModel


class UserSearchResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    full_name: str
    mobile: str
    profile: str | None = None

    model_config = {"from_attributes": True}
