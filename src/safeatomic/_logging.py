"""Internal logger for safeatomic.

The library obtains a single :class:`logging.Logger` instance and never
configures handlers, levels, formatters, or filters. Configuration belongs
to the application using safeatomic.

Use:

    from safeatomic._logging import logger

    logger.warning("...")

This module is NOT part of the public API. It is not re-exported via
:mod:`safeatomic`.

Cross-ref: design/api-v2-proposal.md (logging section).
"""

from __future__ import annotations

import logging

logger: logging.Logger = logging.getLogger("safeatomic")
"""The library-wide logger.

Name: ``safeatomic``. Consumers may attach handlers, set levels, or
silence the library by configuring this logger (or one of its parents)
through the standard :mod:`logging` mechanisms.
"""
