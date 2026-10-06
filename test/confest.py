from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import settings

# Point the app at a separate test database BEFORE anything imports app.db.
_dev_url = make_url(settings.database_url)
_test_url = _dev_url.set(database="billing_test")
settings.database_url = _test_url.render_as_string(hide_password=False)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Plan, Tenant  # noqa: E402
from app.seed import PLANS  # noqa: E402


def _create_test_database() -> None:
    admin_url = _dev_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.scalar(
            text("select 1 from pg_database where datname = 'billing_test'")
        )
        if not exists:
            conn.execute(text("CREATE DATABASE billing_test"))
    admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database():
    """Create the test database and build the schema with our real migrations."""
    _create_test_database()
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(_database):
    """Before every test: empty all tables and re-insert the two plans."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE usage_events, subscriptions, processed_webhook_events, "
                "tenants, plans RESTART IDENTITY CASCADE"
            )
        )
    with SessionLocal() as db:
        db.add_all(Plan(**p) for p in PLANS)
        db.commit()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def make_tenant(db):
    """Factory: create a tenant and return its id."""

    def _make(name="Test Co", plan="free", status="active", customer_id=None) -> int:
        plan_row = db.query(Plan).filter_by(name=plan).one()
        tenant = Tenant(
            name=name,
            plan_id=plan_row.id,
            subscription_status=status,
            stripe_customer_id=customer_id,
        )
        db.add(tenant)
        db.commit()
        return tenant.id

    return _make