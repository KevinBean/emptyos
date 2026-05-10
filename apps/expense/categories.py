"""Category keyword detection + AA-split parser for expense entries."""

from __future__ import annotations

import re

CATEGORY_KEYWORDS = {
    "Dining": [
        "lunch", "dinner", "breakfast", "cafe", "coffee", "restaurant", "food",
        "eat", "meal", "takeaway", "mcdonald", "kfc", "subway", "pizza", "sushi",
        "thai", "chinese", "indian", "noodle", "burger", "pad", "curry", "salad",
        "bakery", "snack", "brunch", "starbucks", "hungry jack", "domino",
        "bubble tea", "boba", "ramen",
    ],
    "Groceries": [
        "coles", "woolworths", "aldi", "costco", "iga", "grocery", "fruit",
        "vegetables", "meat", "milk", "bread", "eggs", "supermarket",
    ],
    "Transport": [
        "uber", "taxi", "bus", "train", "tram", "fuel", "petrol", "parking",
        "toll", "rego", "car wash", "mechanic", "didi", "lyft",
    ],
    "Shopping": [
        "amazon", "ebay", "kmart", "target", "jb", "officeworks", "bunnings",
        "ikea", "uniqlo", "online", "clothing", "shoes",
    ],
    "Bills": [
        "electricity", "gas", "water", "internet", "phone", "rent", "insurance",
        "netflix", "spotify", "subscription", "youtube", "premium",
    ],
    "Health": [
        "pharmacy", "chemist", "doctor", "dentist", "gym", "supplement",
        "vitamin", "protein", "physio", "massage", "medical",
    ],
    "Entertainment": [
        "movie", "cinema", "concert", "ticket", "game", "bar", "pub", "drinks",
        "beer", "wine", "alcohol", "club",
    ],
}


def detect_category(text: str) -> str:
    """Auto-detect expense category from description keywords."""
    t = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return cat
    return "Other"


def parse_aa_split(text: str) -> tuple[float, str] | None:
    """Parse AA split: '50 lunch AA 2' → (25.0, 'lunch (AA÷2)')."""
    m = re.match(r"^(\d+\.?\d*)\s+(.+?)\s+AA\s*(\d+)\s*$", text, re.IGNORECASE)
    if m:
        amount = float(m.group(1)) / int(m.group(3))
        desc = f"{m.group(2)} (AA÷{m.group(3)})"
        return amount, desc
    return None
