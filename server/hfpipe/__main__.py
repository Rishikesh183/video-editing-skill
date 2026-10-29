"""`uv run python -m hfpipe [--host H] [--port P]`"""

import argparse
import logging

import uvicorn

from . import config


def main() -> None:
    parser = argparse.ArgumentParser(prog="hfpipe")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 or your Tailscale IP to reach it from the phone")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost") and not config.API_TOKEN:
        raise SystemExit("Refusing to listen beyond localhost without HF_API_TOKEN set.")
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("hfpipe.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
