from fastapi import FastAPI

from app.routes import checkout, generate, usage

app = FastAPI(title="Usage Metering & Billing Engine")

app.include_router(generate.router)
app.include_router(usage.router)
app.include_router(checkout.router)


@app.get("/health")
def health():
    return {"status": "ok"}