"""Fail-open, context-local observations of the executing turn (not the measure port)."""
from contextvars import ContextVar
from functools import wraps

_state = ContextVar("turn_diagnostics", default=None)


def observe(event, value=None):
    try:
        data = _state.get()
        if data is None:
            return
        if event in ("L0", "L1"):
            data["memory"][event] = value
        elif event == "candidates":
            data.setdefault("candidates", []).append(value)
        elif event == "proposal":
            data["proposals"] += 1
            if data["proposals"] > 1:
                data["reproposals"].append({"source": value, "used": False})
        elif event == "selected" and data["reproposals"]:
            last = data["reproposals"][-1]
            if value is None or last["source"] == value:
                last["used"] = True
    except Exception:
        pass


def collect(run):
    @wraps(run)
    def measured(*args, **kwargs):
        token = None
        try:
            data = {"memory": {"L0": "excluded", "L1": "excluded"},
                    "proposals": 0, "reproposals": []}
            token = _state.set(data)
        except Exception:
            data = None
        try:
            result = run(*args, **kwargs)
            try:
                result.planning = data
            except Exception:
                pass
            return result
        finally:
            try:
                if token is not None:
                    _state.reset(token)
            except Exception:
                pass
    return measured
