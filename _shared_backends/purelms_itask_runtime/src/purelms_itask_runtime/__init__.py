"""
Shared runtime helper for PureLMS InteractiveTask backend containers.

Hides the local-dir-vs-GCS-URI I/O split and the progress/complete
worker-callback contract behind three calls, so every backend's
``main.py`` is identical regardless of how the LMS launched it. See
:mod:`purelms_itask_runtime.runtime` for the contract details.
"""

from __future__ import annotations

from purelms_itask_runtime.runtime import DEFAULT_PROGRESS_MILESTONES
from purelms_itask_runtime.runtime import CallbackAuthenticationError
from purelms_itask_runtime.runtime import CompleteCallbackError
from purelms_itask_runtime.runtime import ProgressReporter
from purelms_itask_runtime.runtime import RuntimeConfigError
from purelms_itask_runtime.runtime import RuntimeLocation
from purelms_itask_runtime.runtime import make_progress_reporter
from purelms_itask_runtime.runtime import read_input_envelope
from purelms_itask_runtime.runtime import write_output_envelope

__all__ = [
    "DEFAULT_PROGRESS_MILESTONES",
    "CallbackAuthenticationError",
    "CompleteCallbackError",
    "ProgressReporter",
    "RuntimeConfigError",
    "RuntimeLocation",
    "make_progress_reporter",
    "read_input_envelope",
    "write_output_envelope",
]
