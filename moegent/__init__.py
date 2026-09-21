"""Compatibility namespace for pre-rename launch commands and installed hooks.

New code uses litellm_memory. Resolve old module paths from the same source so
existing ``python -m moegent.connector`` hooks continue to run after upgrading.
"""

from litellm_memory import __path__ as __path__
