from fastapi import FastAPI

from app.db.mongo import ping

app = FastAPI(title="Krypto Backend API")


@app.get("/health")
async def health_check():
    await ping()
    return {"status": "ok", "mongo": "connected"}
