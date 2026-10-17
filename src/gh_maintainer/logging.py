"""Readable sync logs on stderr, separate from command results on stdout."""

import logging

from rich.console import Console
from rich.logging import RichHandler


def configure_logging() -> None:
    handler = RichHandler(
        console=Console(stderr=True),
        rich_tracebacks=True,
        markup=False,
        show_path=False,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("gh_maintainer")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
