from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "ShopSphere Pro"
    DEBUG: bool = True

    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "shopsphere_db"
    # if this is set it wins over the DB_* values (tests use sqlite through this)
    DATABASE_URL: str = ""

    SECRET_KEY: str = "dev-only-secret-key-change-this-in-env-file"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    SANDBOX_WEBHOOK_SECRET: str = "local-sandbox-secret"
    CURRENCY: str = "inr"

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    MAIL_FROM: str = "no-reply@shopsphere.local"

    UPLOAD_DIR: str = "uploads"
    EXPORT_DIR: str = "exports"
    MAX_UPLOAD_MB: int = 5

    FREE_SHIPPING_ABOVE: float = 999
    COD_MAX_AMOUNT: float = 10000

    ENABLE_SCHEDULER: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def db_url(self):
        if self.DATABASE_URL:
            return self.DATABASE_URL
        user = self.DB_USER
        # quote_plus so passwords with @ or # don't break the url
        password = quote_plus(self.DB_PASSWORD)
        host = self.DB_HOST
        port = self.DB_PORT
        name = self.DB_NAME
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"


settings = Settings()