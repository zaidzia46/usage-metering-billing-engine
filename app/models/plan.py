from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    api_call_limit: Mapped[int] = mapped_column(BigInteger)
    token_limit: Mapped[int] = mapped_column(BigInteger)