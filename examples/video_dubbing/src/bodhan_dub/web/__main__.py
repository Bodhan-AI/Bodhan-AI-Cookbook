"""python -m bodhan_dub.web [--port 8000]  : start the Dub Studio web UI."""
import argparse

import uvicorn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    from ..__main__ import load_dotenv
    load_dotenv()
    uvicorn.run("bodhan_dub.web.server:app", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
