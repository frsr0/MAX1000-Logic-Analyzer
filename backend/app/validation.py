"""Machine-readable session assertions for CI and regression runs."""
from __future__ import annotations

from typing import Iterable


def validate_events(events: Iterable[dict], spec: dict) -> dict:
    events = list(events)
    failures: list[str] = []
    for wanted in spec.get("expected_events", []):
        matches = [event for event in events if event.get("type") == wanted.get("type")
                   and all(event.get("fields", {}).get(k) == v
                           for k, v in wanted.get("fields", {}).items())]
        if not matches:
            failures.append(f"missing event {wanted}")
    if len(events) < int(spec.get("min_events", 0)):
        failures.append(f"expected at least {spec['min_events']} events, got {len(events)}")
    errors = sum(1 for event in events if event.get("severity") == "error")
    if errors > int(spec.get("max_errors", 0)):
        failures.append(f"expected at most {spec.get('max_errors', 0)} errors, got {errors}")
    for bound in spec.get("duration_bounds", []):
        for event in (e for e in events if e.get("type") == bound.get("type")):
            duration = float(event.get("end_time", 0)) - float(event.get("start_time", 0))
            if bound.get("min_s") is not None and duration < float(bound["min_s"]):
                failures.append(f"{bound['type']} duration below minimum: {duration}")
            if bound.get("max_s") is not None and duration > float(bound["max_s"]):
                failures.append(f"{bound['type']} duration above maximum: {duration}")
    return {"passed": not failures, "event_count": len(events),
            "error_count": errors, "failures": failures}


def junit_xml(result: dict, name: str = "session-validation") -> str:
    import html
    failures = result.get("failures", [])
    body = "" if result.get("passed") else "<failure message=\"validation failed\">" + \
        html.escape("; ".join(failures)) + "</failure>"
    return (f'<testsuite name="{html.escape(name)}" tests="1" '
            f'failures="{0 if result.get("passed") else 1}">'
            f'<testcase name="assertions">{body}</testcase></testsuite>\n')
