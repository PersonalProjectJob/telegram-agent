"""Compatible launcher for the source-bundled, loopback-only AgentFleet console."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
from agentfleet_console.cli import main
from agentfleet_console.server import ConsoleServer, PreviewHandler

__all__ = ['ConsoleServer', 'PreviewHandler', 'main']

if __name__ == '__main__':
    main()
