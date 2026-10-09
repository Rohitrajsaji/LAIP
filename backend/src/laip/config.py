from pathlib import Path
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from laip.private_ai import PrivateAIConfig


class ModelContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    model_id: str = Field(min_length=1, max_length=128)
    model_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    policy_version: str = Field(min_length=1, max_length=128)
    tokenizer_id: str = Field(min_length=1, max_length=128)
    model_window: int = Field(ge=1, le=1000000)
    dimension: int | None = Field(default=None, ge=1, le=2000)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LAIP_", extra="forbid", hide_input_in_errors=True)

    database_url: SecretStr = Field(default=SecretStr(""), repr=False)
    database_url_file: Path | None = None
    service_token: SecretStr = Field(default=SecretStr(""), repr=False)
    service_token_file: Path | None = None
    artifact_root: Path = Path("/var/lib/laip/artifacts")
    ai_enabled: bool = False
    embeddings_enabled: bool = False
    ai_endpoint: str | None = None
    embedding_endpoint: str | None = None
    ai_operator_approved: bool = False
    ai_approved_endpoints: list[str] = Field(default_factory=list, repr=False)
    llm_model_contract: ModelContract | None = None
    embedding_model_contract: ModelContract | None = None
    private_ai_token: SecretStr = Field(default=SecretStr(""), repr=False)
    private_ai_token_file: Path | None = Field(default=None, repr=False)
    worker_poll_seconds: float = Field(default=5, ge=1, le=60)
    worker_heartbeat_max_age: float = Field(default=30, ge=10, le=120)
    allowed_hosts: list[str] = ["api", "localhost", "127.0.0.1", "[::1]"]
    read_namespace: str | None = Field(default=None, min_length=1, max_length=256)
    mcp_namespace: str | None = Field(default=None, min_length=1, max_length=256)
    analyst_namespace: str = Field(default="local:workspace", min_length=1, max_length=256)
    allowed_read_origins: list[str] = ["http://127.0.0.1:3030", "http://localhost:3030"]

    @model_validator(mode="after")
    def validate_local_scaffold(self) -> Self:
        if self.database_url_file is not None:
            if self.database_url.get_secret_value():
                raise ValueError("Configure one database URL source")
            try:
                self.database_url = SecretStr(self.database_url_file.read_text().strip())
            except OSError:
                raise ValueError("Database URL file is unavailable") from None
        if self.private_ai_token_file is not None:
            if self.private_ai_token.get_secret_value():
                raise ValueError("Configure one private AI token source")
            try:
                self.private_ai_token = SecretStr(self.private_ai_token_file.read_text().strip())
            except OSError:
                raise ValueError("Private AI token file is unavailable") from None
        self.private_ai_config()
        if not self.database_url.get_secret_value().startswith(("postgresql://", "postgres://")):
            raise ValueError("PostgreSQL is required")
        if self.service_token_file is not None:
            if self.service_token.get_secret_value():
                raise ValueError("Configure one service token source")
            try:
                token = self.service_token_file.read_text().strip()
            except OSError:
                raise ValueError("Service token file is unavailable") from None
            self.service_token = SecretStr(token)
        if len(self.service_token.get_secret_value()) < 32:
            raise ValueError("Service token must have at least 32 characters")
        if not self.artifact_root.is_absolute():
            raise ValueError("Artifact root must be absolute")
        if not self.allowed_hosts or any("*" in host for host in self.allowed_hosts):
            raise ValueError("Explicit allowed hosts are required")
        return self

    def private_ai_config(self) -> "PrivateAIConfig":
        from laip.private_ai import PrivateAIConfig, PrivateAIProfile, validate_profile

        def profile(
            enabled: bool,
            endpoint: str | None,
            model: ModelContract | None,
            embedding: bool = False,
        ) -> PrivateAIProfile:
            if not enabled:
                if endpoint or model:
                    raise ValueError("Disabled AI must not configure an endpoint or model")
                return PrivateAIProfile()
            if not self.ai_operator_approved or model is None or endpoint is None:
                raise ValueError("Approved private AI endpoint and model contract required")
            if embedding and model.dimension is None:
                raise ValueError("Embedding model dimension required")
            configured = PrivateAIProfile(
                enabled=True,
                approved=True,
                endpoint=endpoint,
                approved_endpoints=tuple(self.ai_approved_endpoints),
                bearer_token=self.private_ai_token,
                **model.model_dump(),
            )
            validate_profile(configured)
            return configured

        return PrivateAIConfig(
            llm=profile(self.ai_enabled, self.ai_endpoint, self.llm_model_contract),
            embedding=profile(
                self.embeddings_enabled,
                self.embedding_endpoint,
                self.embedding_model_contract,
                True,
            ),
        )
