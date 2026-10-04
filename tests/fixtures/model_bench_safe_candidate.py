"""Static safe candidate used to exercise the behavioural subprocess wire."""

CAPTURE = ("buy ", "call ")


def classify_route(text: str) -> str | None:
    low = text.strip().lower()
    if low.startswith(CAPTURE):
        return "capture"
    if low.endswith("?"):
        return "ask"
    return None
