#!/usr/bin/env python3
"""Lightweight static server for the `web/` client.

Runs a simple HTTP server bound to all interfaces by default so the client
can be served over plain HTTP (so browsers allow ws:// connections).
"""
import argparse
import http.server
import socketserver
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve web/ directory over HTTP")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=20070, help="Port to listen on (default: 20070)")
    args = parser.parse_args(argv)

    web_dir = Path(__file__).resolve().parent
    handler_class = http.server.SimpleHTTPRequestHandler

    try:
        # Python 3.7+ supports the directory parameter
        handler = lambda *hargs, directory=str(web_dir): handler_class(*hargs, directory=directory)
    except TypeError:
        # Fallback: chdir to the web directory
        import os

        os.chdir(str(web_dir))
        handler = handler_class

    with socketserver.TCPServer((args.host, args.port), handler) as httpd:
        print(f"Serving {web_dir} on http://{args.host}:{args.port}/")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("Shutting down")
            httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
