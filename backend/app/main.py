import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from backend.app import netguard, sovereignty

# Strict offline mode (KRYPTO_OFFLINE=1) must be in place before anything can open a connection.
netguard.install_if_requested()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from backend.app.auth.bootstrap import prepare_accounts  # noqa: E402
from backend.app.core.config import REPO_ROOT, settings  # noqa: E402
from backend.app.db.mongo import db, ping  # noqa: E402
from backend.app.network import monitor  # noqa: E402
from backend.app.routes import auth, system, tasks, ws  # noqa: E402


def _warm_models() -> None:
    """Load the local models into memory at start-up, so the first question does not wait for that."""
    try:
        import ollama

        from backend.app.llm import KEEP_ALIVE
        from backend.app.router.router import demo_model, resolve_model_tag

        ollama.generate(model=demo_model() or resolve_model_tag("qwen3:14b"), prompt="", keep_alive=KEEP_ALIVE)
        ollama.embed(model="nomic-embed-text", input=["warm up"], keep_alive=KEEP_ALIVE)
    except Exception:
        pass  # Ollama not running yet: the first run reports that clearly


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Runs execute inside this process, so any task still queued/running in the
    # database belongs to a previous process that has died -- mark it failed
    # instead of leaving it "running" forever (it could never be deleted).
    await db.tasks.update_many(
        {"status": {"$in": ["queued", "running"]}},
        {"$set": {
            "status": "failed",
            "final_message": "Interrupted: the server restarted while this run was in progress.",
            "finished_at": datetime.now(timezone.utc),
        }},
    )
    await prepare_accounts()
    monitor.start()
    sovereignty.start()
    warm_up = asyncio.create_task(asyncio.to_thread(_warm_models))
    try:
        yield
    finally:
        warm_up.cancel()
        sovereignty.stop()
        await monitor.stop()


app = FastAPI(title="Krypto Backend API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(system.router)
app.include_router(ws.router)


@app.get("/health")
async def health_check():
    await ping()
    return {"status": "ok", "mongo": "connected"}


FRONTEND_DIR = REPO_ROOT / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui/")
