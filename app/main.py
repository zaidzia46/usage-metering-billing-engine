from fastapi import FastAPI

from app.routes import generate, usage

app = FastAPI(title="Usage Metering & Billing Engine")

app.include_router(generate.router)
app.include_router(usage.router)


@app.get("/health")
def health():
    return {"status": "ok"}