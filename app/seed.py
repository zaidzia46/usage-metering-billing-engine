from sqlalchemy import select

from app.db import SessionLocal
from app.models import Plan, Tenant

PLANS = [
    {"name": "free", "api_call_limit": 1_000, "token_limit": 100_000},
    {"name": "pro", "api_call_limit": 50_000, "token_limit": 5_000_000},
]

DEMO_TENANTS = ["Acme Corp", "Globex Inc"]


def seed() -> None:
    with SessionLocal() as db:
        for data in PLANS:
            exists = db.scalar(select(Plan).where(Plan.name == data["name"]))
            if not exists:
                db.add(Plan(**data))
        db.commit()

        free_plan = db.scalar(select(Plan).where(Plan.name == "free"))
        for name in DEMO_TENANTS:
            exists = db.scalar(select(Tenant).where(Tenant.name == name))
            if not exists:
                db.add(Tenant(name=name, plan_id=free_plan.id))
        db.commit()

    print("Seed complete.")


if __name__ == "__main__":
    seed()