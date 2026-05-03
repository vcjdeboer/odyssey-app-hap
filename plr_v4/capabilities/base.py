"""Capability base utilities — setup guard decorator.

The three-layer model merges the backend ABC into the capability.
Drivers inherit from the capability and implement underscore methods.
The Device manages lifecycle and sets _setup_finished on each driver.

The @need_setup decorator guards public capability methods so they
raise a clear error if called before setup().
"""

from __future__ import annotations

import functools
from typing import Any, Callable


def need_setup(method: Callable) -> Callable:
    """Guard decorator: raises RuntimeError if the driver is not set up."""
    @functools.wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not getattr(self, "_setup_finished", False):
            raise RuntimeError(
                f"{type(self).__name__} is not set up. "
                f"Call setup() on the parent Device first."
            )
        return await method(self, *args, **kwargs)
    return wrapper
