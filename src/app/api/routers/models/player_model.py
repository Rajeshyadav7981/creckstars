from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import date


class CreatePlayerRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str | None = None
    # Mobile: required for non-guest adds. Validated below.
    mobile: str | None = None
    # Guest flag: when true, this is a permanent unlinkable stub (kid/walk-in/
    # no phone). Mobile is not required in that case; never auto-links.
    is_guest: bool = False
    date_of_birth: date | None = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    batting_style: str | None = None
    bowling_style: str | None = None
    role: str | None = None
    profile_image: str | None = None
    user_id: int | None = None

    @field_validator("mobile")
    @classmethod
    def _mobile_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if len(v) != 10 or not v.isdigit():
            raise ValueError("Mobile must be a 10-digit number")
        return v

    @model_validator(mode="after")
    def _mobile_or_guest(self):
        # Skip the rule when admin is explicitly linking to a user_id (legacy
        # path; the user's own mobile is authoritative). Also skip for guests.
        if self.user_id is not None or self.is_guest:
            return self
        if not self.mobile:
            raise ValueError(
                "Mobile number is required. Mark this player as a guest if they have no phone."
            )
        return self


class UpdatePlayerRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    mobile: str | None = None
    is_guest: bool | None = None
    date_of_birth: date | None = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    batting_style: str | None = None
    bowling_style: str | None = None
    role: str | None = None
    profile_image: str | None = None

    @field_validator("mobile")
    @classmethod
    def _mobile_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if len(v) != 10 or not v.isdigit():
            raise ValueError("Mobile must be a 10-digit number")
        return v
