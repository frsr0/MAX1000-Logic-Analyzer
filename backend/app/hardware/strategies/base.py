"""CaptureStrategy ABC and CaptureDevice protocol.

Defines the seam between ExistingHostAdapter and its capture modes.
Each mode implements a single-attempt _do_capture(); retry/recovery is
owned by the base class via the template method pattern.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Callable, ClassVar, Optional, Protocol, Set

from ...capture.session import CaptureSettings
from ..base import CaptureResult, ProgressCb


class CaptureDevice(Protocol):
    """Narrow interface of OLSDeviceSPI methods used during capture.

    Strategies depend on this protocol, not the concrete driver class.
    """
    sample_clk: float

    def capture(
        self,
        rate_hz: float,
        nsamples: int,
        timeout: float,
        trigger: Optional[int | tuple[int, int]] = None,
        stop_evt: Optional[threading.Event] = None,
        progress_cb: Optional[Callable] = None,
        pre_trigger: int = 0,
    ) -> bytes: ...

    def set_analog_config(self, mode: int, adc_channel: int = 1) -> None: ...

    def set_readback_compression(self, mode: str) -> None: ...

    def reset(self) -> None: ...

    def flush(self) -> None: ...

    @property
    def raw_flags(self) -> int: ...

    @raw_flags.setter
    def raw_flags(self, value: int) -> None: ...

    @property
    def fast_mode_enabled(self) -> bool: ...

    @fast_mode_enabled.setter
    def fast_mode_enabled(self, value: bool) -> None: ...


class CaptureStrategy(ABC):
    """Base class for a single-attempt capture mode.

    Subclasses implement _do_capture() which performs one capture attempt.
    The public capture() method adds retry logic on top.
    """

    modes: ClassVar[Set[str]]  # capture mode strings this strategy handles

    def capture(
        self,
        dev: CaptureDevice,
        settings: CaptureSettings,
        trigger: Optional[int | tuple[int, int]] = None,
        progress: Optional[ProgressCb] = None,
        stop_evt: Optional[threading.Event] = None,
    ) -> CaptureResult:
        """Template method: one attempt, with retry + recovery built in."""
        self._pre_capture(dev, settings)
        for _attempt in range(2):
            try:
                result = self._do_capture(dev, settings, trigger, progress, stop_evt)
                if result.digital is not None or result.analog:
                    return result
            except Exception:
                pass
            self._recover(dev)
        # Final attempt — let the exception propagate
        return self._do_capture(dev, settings, trigger, progress, stop_evt)

    # ── hooks ─────────────────────────────────────────────────────────

    def _pre_capture(self, dev: CaptureDevice, settings: CaptureSettings) -> None:
        """Configure device before capture (called once per attempt series)."""
        pass

    def _recover(self, dev: CaptureDevice) -> None:
        """Recover device after a failed capture attempt."""
        try:
            dev.set_analog_config(0)
            dev.reset()
            dev.flush()
        except Exception:
            pass

    @abstractmethod
    def _do_capture(
        self,
        dev: CaptureDevice,
        settings: CaptureSettings,
        trigger: Optional[int | tuple[int, int]] = None,
        progress: Optional[ProgressCb] = None,
        stop_evt: Optional[threading.Event] = None,
    ) -> CaptureResult: ...
