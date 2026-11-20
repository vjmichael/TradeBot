"""Write-only PAPER development credentials; never accepts wallet or live keys."""
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class CredentialSetup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    grok: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    telegram_desk: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    telegram_recovery: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    kraken_read_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    kraken_read_secret: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)

    def vault_values(self):
        """Unwrap only at vault write boundary; never serialize this return value."""
        return {name: value.get_secret_value() for name in type(self).model_fields
                if (value := getattr(self, name)) is not None}
