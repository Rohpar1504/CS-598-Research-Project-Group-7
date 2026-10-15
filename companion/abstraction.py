"""Conventions for what adapters return.

An adapter translates the program's outputs and state into the JSON of the model's types.
When some part cannot be expressed in those types (an unexpected value, a response of the
wrong shape, an exit code that contradicts the response, state that cannot be read), the
adapter puts `inexpressible(...)` in its place. The model never produces that marker, so the
step can only count as a mismatch, and transitions containing it are not given to the
model's property evaluator (whose JSON parser would reject them).
"""

INEXPRESSIBLE = "$inexpressible"


def inexpressible(what: str, raw=None) -> dict:
    """A placeholder for something the model's types cannot express: `what` says what was
    expected, `raw` is what the program gave."""
    return {INEXPRESSIBLE: {"what": what, "raw": raw}}


def expressible(value) -> bool:
    """True if `value` contains no `inexpressible` marker anywhere."""
    if isinstance(value, dict):
        return INEXPRESSIBLE not in value and all(expressible(v) for v in value.values())
    if isinstance(value, list):
        return all(expressible(v) for v in value)
    return True
