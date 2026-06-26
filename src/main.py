from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

from .web_server import create_web_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Web-based CCTV Tailgate camera and tailgating dashboard"
    )
    parser.add_argument("--config", default="config.yaml", help="YAML configuration path")
    parser.add_argument("--host", default=None, help="Override configured web host")
    parser.add_argument("--port", type=int, default=None, help="Override configured web port")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    app = create_web_app(config_path)
    config = app.state.gym_sentry_config
    api_config = config.get("api", {})
    host = args.host or str(api_config.get("host", "127.0.0.1"))
    port = args.port or int(api_config.get("port", 8080))
    print(f"CCTV Tailgate web dashboard: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
