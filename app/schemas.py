from pydantic import BaseModel, ConfigDict, Field


class TelegramIdentity(BaseModel):
    id: int
    first_name: str = "کاربر"
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class OrderCreate(BaseModel):
    plan_id: str = Field(min_length=2, max_length=32)


class RejectOrderRequest(BaseModel):
    reason: str = Field(default="رسید پرداخت قابل تأیید نیست", max_length=255)


class TrialConfigureRequest(BaseModel):
    volume_mb: int = Field(ge=1, le=102400)
    duration_hours: int = Field(ge=1, le=8760)
    max_claims: int = Field(ge=1, le=100)
    window_days: int = Field(ge=0, le=3650)
    panel_id: str | None = Field(default=None, max_length=40)


class HealthResponse(BaseModel):
    status: str
    database: str
    bot_configured: bool
    bot_username: str
    demo_mode: bool
    panel_mode: str
    backup_configured: bool


class APIMessage(BaseModel):
    ok: bool = True
    message: str


class ModelSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
