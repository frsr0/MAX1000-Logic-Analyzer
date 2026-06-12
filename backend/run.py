"""Convenience launcher: python run.py [--port 8000]"""
import argparse

import uvicorn

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port,
                reload=args.reload)
