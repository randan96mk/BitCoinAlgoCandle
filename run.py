#!/usr/bin/env python3
"""Single-command entry point: python run.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    import uvicorn
    from backend.config import Config

    cfg = Config()
    uvicorn.run(
        "backend.main:app",
        host=cfg.get("server.host", "0.0.0.0"),
        port=cfg.get("server.port", 8000),
        reload=False,
        log_level="info",
    )
