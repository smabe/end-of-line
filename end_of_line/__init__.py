"""End of Line — plan orchestrator.

Thin coordinator over the /plan skill. Cron drives `eol tick`; tick reads
state, decides one action, takes it, exits. Workers are fresh Claude sessions.
"""

__version__ = "0.1.0"
