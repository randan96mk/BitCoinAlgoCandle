"""
Main application entry point — BTC Futures Candlestick strategy platform.
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import Config
from backend.database.models import init_db
from backend.api.routes import router as api_router
from backend.services.trading_engine import TradingEngine

engine_instance: TradingEngine = None

os.makedirs("logs", exist_ok=True)
os.makedirs("database", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/trading.log", mode="a"),
    ],
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine_instance
    init_db()
    engine_instance = TradingEngine()
    task = asyncio.create_task(engine_instance.start())
    logger.info("Application started")
    yield
    await engine_instance.stop()
    task.cancel()


app = FastAPI(title="BTC Futures Candlestick Alerts", lifespan=lifespan)

templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(templates_dir))
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/signals", response_class=HTMLResponse)
async def signals_page(request: Request):
    return templates.TemplateResponse("signals.html", {"request": request})


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    return templates.TemplateResponse("analytics.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


if __name__ == "__main__":
    import uvicorn
    cfg = Config()
    uvicorn.run(
        "backend.main:app",
        host=cfg.get("server.host", "0.0.0.0"),
        port=cfg.get("server.port", 8000),
        reload=False,
    )
