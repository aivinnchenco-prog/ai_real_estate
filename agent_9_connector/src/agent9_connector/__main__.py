"""Agent 9 Connector entrypoint."""

from .main import ConnectorRuntime


def main() -> None:
    runtime = ConnectorRuntime.create()
    runtime.run_forever()


if __name__ == "__main__":
    main()
