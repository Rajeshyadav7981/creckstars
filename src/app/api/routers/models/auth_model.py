from pydantic import BaseModel


class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    mobile: str
    email: str | None = None
    password: str
    profile: str | None = None
    username: str | None = None  # Instagram-style @handle


class LoginRequest(BaseModel):
    mobile: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str | None = None
    first_name: str
    last_name: str
    full_name: str
    mobile: str
    email: str | None = None
    profile: str | None = None
    followers_count: int = 0
    following_count: int = 0

    model_config = {"from_attributes": True}


class UpdateProfileRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    profile: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
