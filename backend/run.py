"""Convenience launcher: python run.py [--port 8000]"""
import argparse
import os

import uvicorn

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()
    # config.py reads these at import time (startup banner, QR page)
    os.environ["MSA_HOST"] = args.host
    os.environ["MSA_PORT"] = str(args.port)
    # Import the app after setting environment variables. Passing the object
    # instead of a string also keeps frozen/PyInstaller builds discoverable.
    from app.main import app

    uvicorn.run(app, host=args.host, port=args.port,
                reload=args.reload)
