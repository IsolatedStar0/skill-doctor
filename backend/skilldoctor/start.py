from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Start the hosted FastAPI app without relying on shell env expansion."""

    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(
        "backend.skilldoctor.api:app",
        host="0.0.0.0",
        port=port,
    )


if __name__ == "__main__":
    main()
