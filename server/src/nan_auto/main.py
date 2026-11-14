"""Entry point for the NaN Auto gateway (uvicorn)."""


def main() -> None:
    """Run the gateway with uvicorn."""
    import uvicorn

    uvicorn.run("nan_auto.gateway.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
