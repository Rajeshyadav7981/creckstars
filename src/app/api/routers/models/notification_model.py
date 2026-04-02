from pydantic import BaseModel


class PushTokenRequest(BaseModel):
    token: str
    device_type: str | None = None


class RemovePushTokenRequest(BaseModel):
    token: str
