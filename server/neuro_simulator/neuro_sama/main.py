"""Main entry point for the Neuro Sama module."""

import asyncio
import json
import sys
import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI
from openai import AsyncOpenAI

from .config import Config
from .api import router
from .banner import display_banner


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    # Get working directory from environment
    import os
    working_dir = os.getenv("NEURO_WORKING_DIR")
    print(f"DEBUG: NEURO_WORKING_DIR = {working_dir}")

    # Read global config from file
    import yaml
    from pathlib import Path

    if working_dir:
        # The config.yaml should be in the parent directory of the module's working directory
        working_path = Path(working_dir)
        config_file = working_path.parent / "config.yaml"
        print(f"DEBUG: Working dir = {working_path}")
        print(f"DEBUG: Looking for config at: {config_file}")
        print(f"DEBUG: Config file exists: {config_file.exists()}")
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                global_config = yaml.safe_load(f)
        else:
            raise ValueError(f"Global configuration file not found: {config_file}. Please run Vedal Studio to initialize the working directory.")
    else:
        # 检查默认工作目录是否存在
        default_working_dir = Path.home() / ".config" / "neuro-simulator" / "neuro_sama"
        config_file = default_working_dir.parent / "config.yaml"
        if config_file.exists():
            # 使用默认工作目录
            working_dir = str(default_working_dir)
            working_path = Path(working_dir)
            with open(config_file, 'r', encoding='utf-8') as f:
                global_config = yaml.safe_load(f)
            print(f"Using default working directory: {working_dir}")
        else:
            raise ValueError(f"Global configuration file not found: {config_file}. Please run Vedal Studio to initialize the working directory.")

    app.state.config = Config(global_config, working_dir)
    app.state.openai_client = AsyncOpenAI(
        api_key=app.state.config.OPENAI_API_KEY,
        base_url=app.state.config.OPENAI_BASE_URL
    )

    # Display the banner
    display_banner()

    print(f"Neuro Sama module started on {app.state.config.HOST}:{app.state.config.PORT}")
    print(f"Using model: {app.state.config.OPENAI_MODEL}")

    yield

    # Shutdown
    print("Neuro Sama module shutting down...")


# Create FastAPI app
app = FastAPI(
    title="Neuro Sama Module",
    description="LLM service for Neuro Sama AI vtuber",
    version="0.1.0",
    lifespan=lifespan
)

# Include API routes
app.include_router(router)

@app.get("/")
async def root():
    """Root endpoint to check if the service is running."""
    return {"message": "Neuro Sama module is running", "status": "ok"}


if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--reload", action="store_true")

    args = parser.parse_args()

    uvicorn.run(
        "neuro_simulator.neuro_sama.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )