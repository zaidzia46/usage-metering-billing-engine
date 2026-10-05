from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.plan import Plan


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    subscription_status: Mapped[str] = mapped_column(String(30), default="active")
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    plan: Mapped[Plan] = relationship()