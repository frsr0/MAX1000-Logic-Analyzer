"""Deterministic generator variant expansion and preview sweeps."""
from __future__ import annotations

from itertools import product
from typing import Any, Callable, Dict, List, Optional

from ..hardware.device_models import GeneratorConfig
from .bitbang import preview as bitbang_preview
from .controller import validate_generator_payload


def expand_variants(base: GeneratorConfig, axes: Dict[str, List[Any]], limit: int = 256) -> List[GeneratorConfig]:
    """Create a bounded Cartesian product of top-level or ``extra`` fields."""
    fields = [(key, values) for key, values in axes.items() if isinstance(values, list) and values]
    total = 1
    for _, values in fields: total *= len(values)
    if total > limit:
        raise ValueError(f"generator sweep has {total} variants; limit is {limit}")
    if not fields:
        return [base]
    variants = []
    for values in product(*(values for _, values in fields)):
        payload = base.model_dump()
        extra = dict(payload.get("extra") or {})
        for (key, _), value in zip(fields, values):
            if key.startswith("extra."):
                extra[key[6:]] = value
            else:
                payload[key] = value
        payload["extra"] = extra
        variants.append(GeneratorConfig(**payload))
    return variants


def preview_variant(cfg: GeneratorConfig) -> dict:
    """Validate one variant and return stable metrics instead of raising."""
    result = {"protocol": cfg.protocol, "config": cfg.model_dump(), "status": "ok"}
    try:
        validate_generator_payload(cfg)
        if cfg.protocol == "bitbang":
            p = bitbang_preview(cfg.extra, max(1, int(cfg.baud)))
            result.update({"symbol_count": p["count"], "duration_s": p["duration_s"]})
        else:
            result["payload_bytes"] = len(bytes.fromhex(cfg.data_hex)) if cfg.data_hex else 1
    except (TypeError, ValueError, ImportError) as exc:
        result["status"] = "error"
        result["error"] = str(exc)
    return result


def run_preview_sweep(base: GeneratorConfig, axes: Dict[str, List[Any]], limit: int = 256) -> dict:
    variants = expand_variants(base, axes, limit)
    rows = [preview_variant(cfg) for cfg in variants]
    return {"count": len(rows), "passed": sum(row["status"] == "ok" for row in rows),
            "failed": sum(row["status"] != "ok" for row in rows), "rows": rows}


def run_capture_sweep(
    base: GeneratorConfig,
    axes: Dict[str, List[Any]],
    limit: int,
    capture_rate: float,
    capture_samples: int,
    expected_hex: Optional[str],
    runner: Callable[[GeneratorConfig, float, int, Optional[str]], Any],
    stop_on_failure: bool = False,
) -> dict:
    """Run bounded generator variants through a capture-backed runner."""
    if capture_rate <= 0 or capture_samples <= 0:
        raise ValueError("capture_rate and capture_samples must be positive")
    variants = expand_variants(base, axes, limit)
    rows: List[dict] = []
    for cfg in variants:
        row: Dict[str, Any] = {"protocol": cfg.protocol,
                               "config": cfg.model_dump()}
        try:
            result = runner(cfg, float(capture_rate), int(capture_samples), expected_hex)
            payload = result.model_dump() if hasattr(result, "model_dump") else dict(result)
            row.update(payload)
            row["status"] = "passed" if bool(payload.get("passed")) else "failed"
        except Exception as exc:  # record a bad physical variant and continue
            row.update({"status": "error", "passed": False, "error": str(exc)})
        rows.append(row)
        if stop_on_failure and row["status"] != "passed":
            break
    return {"count": len(rows), "requested_count": len(variants),
            "passed": sum(row["status"] == "passed" for row in rows),
            "failed": sum(row["status"] != "passed" for row in rows),
            "rows": rows}
