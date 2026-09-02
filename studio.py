"""Launch the JUGAAD studio in a browser."""

import os

from studio.app import app


def main():
    import uvicorn

    port = int(os.getenv("PORT", "8787"))
    host = os.getenv("HOST", "0.0.0.0" if os.getenv("RENDER") else "127.0.0.1")
    print("\n  JUGAAD studio  →  http://{0}:{1}\n".format(host, port))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
