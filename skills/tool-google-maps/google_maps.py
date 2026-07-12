"""Google Maps API skill for Claude Code.

Usage:
    python google_maps.py distance "origin" "dest1" "dest2" ...
    python google_maps.py distance --mode driving "origin" "dest"
    python google_maps.py nearby "address" --type supermarket --radius 1000
    python google_maps.py geocode "address"
    python google_maps.py usage

Features:
    - Result caching (30-day TTL) to avoid duplicate API calls
    - Usage tracking with budget guard ($200/month free tier)
    - Multi-mode: walking, driving, transit, bicycling
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import googlemaps
except ImportError:
    print("ERROR: googlemaps not installed. Run: pip install googlemaps")
    sys.exit(1)

# --- Constants ---
SKILL_DIR = Path(__file__).parent
ENV_FILE = SKILL_DIR / ".env"
USAGE_FILE = SKILL_DIR / "usage.json"
CACHE_FILE = SKILL_DIR / "cache.json"

MONTHLY_BUDGET_USD = 200.0
WARN_THRESHOLD = 0.75  # 75% = $150
HARD_STOP_THRESHOLD = 0.90  # 90% = $180

CACHE_TTL_DAYS = 30

# API costs per 1,000 requests/elements
API_COSTS = {
    "distance_matrix": 5.0 / 1000,   # $5 per 1k elements
    "geocoding": 5.0 / 1000,          # $5 per 1k requests
    "places_nearby": 32.0 / 1000,     # $32 per 1k requests
    "directions": 5.0 / 1000,         # $5 per 1k requests
}


# --- API Key ---
def load_api_key():
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if key:
        return key
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("GOOGLE_MAPS_API_KEY="):
                return line.split("=", 1)[1].strip('"').strip("'")
    print("ERROR: No API key found. Set GOOGLE_MAPS_API_KEY or create .env file.")
    sys.exit(1)


# --- Usage Tracking ---
def load_usage():
    if USAGE_FILE.exists():
        return json.loads(USAGE_FILE.read_text())
    return {"calls": [], "monthly_totals": {}}


def save_usage(data):
    USAGE_FILE.write_text(json.dumps(data, indent=2))


def get_month_key():
    return datetime.now().strftime("%Y-%m")


def get_monthly_cost(usage_data):
    month = get_month_key()
    totals = usage_data.get("monthly_totals", {})
    return totals.get(month, {}).get("cost_usd", 0.0)


def check_budget(usage_data):
    """Check budget and return (ok, message)."""
    cost = get_monthly_cost(usage_data)
    pct = cost / MONTHLY_BUDGET_USD

    if pct >= HARD_STOP_THRESHOLD:
        return False, (
            f"BUDGET HARD STOP: ${cost:.2f} / ${MONTHLY_BUDGET_USD} "
            f"({pct:.0%}) used this month. Refusing API call."
        )
    if pct >= WARN_THRESHOLD:
        return True, (
            f"WARNING: ${cost:.2f} / ${MONTHLY_BUDGET_USD} "
            f"({pct:.0%}) used this month."
        )
    return True, None


def log_usage(usage_data, api_type, elements=1):
    cost_per = API_COSTS.get(api_type, 0.005)
    cost = elements * cost_per
    month = get_month_key()

    usage_data["calls"].append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "api": api_type,
        "elements": elements,
        "cost_usd": round(cost, 6),
    })

    if month not in usage_data.get("monthly_totals", {}):
        usage_data.setdefault("monthly_totals", {})[month] = {
            "elements": 0, "cost_usd": 0.0, "calls": 0
        }
    mt = usage_data["monthly_totals"][month]
    mt["elements"] += elements
    mt["cost_usd"] = round(mt["cost_usd"] + cost, 6)
    mt["calls"] += 1

    save_usage(usage_data)
    return cost


# --- Caching ---
def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(data):
    CACHE_FILE.write_text(json.dumps(data, indent=2))


def make_cache_key(*parts):
    return "|".join(str(p).strip().lower() for p in parts)


def get_cached(key):
    cache = load_cache()
    entry = cache.get(key)
    if not entry:
        return None
    cached_at = datetime.fromisoformat(entry["cached_at"])
    if datetime.now() - cached_at > timedelta(days=CACHE_TTL_DAYS):
        return None
    return entry["result"]


def set_cached(key, result):
    cache = load_cache()
    cache[key] = {
        "result": result,
        "cached_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_cache(cache)


# --- Commands ---
def cmd_distance(args):
    """Calculate distance between origin and destinations."""
    usage = load_usage()
    ok, msg = check_budget(usage)
    if msg:
        print(msg)
    if not ok:
        return

    mode = args.mode or "walking"

    if args.origin:
        origin = args.origin
        destinations = args.to if args.to else args.addresses
    else:
        if len(args.addresses) < 2:
            print("ERROR: Need at least origin and one destination.")
            print("Usage: google_maps.py distance \"origin\" \"dest1\" [\"dest2\" ...]")
            return
        origin = args.addresses[0]
        destinations = args.addresses[1:]

    if not destinations:
        print("ERROR: No destinations provided.")
        return

    # Check cache for each destination
    uncached_dests = []
    cached_results = {}
    for dest in destinations:
        key = make_cache_key(mode, origin, dest)
        cached = get_cached(key) if not args.no_cache else None
        if cached:
            cached_results[dest] = cached
        else:
            uncached_dests.append(dest)

    # Make API call for uncached destinations
    api_results = {}
    if uncached_dests:
        client = googlemaps.Client(key=load_api_key())
        result = client.distance_matrix(
            origins=[origin],
            destinations=uncached_dests,
            mode=mode,
            units="metric",
        )
        elements = len(uncached_dests)
        log_usage(usage, "distance_matrix", elements)

        for i, dest in enumerate(uncached_dests):
            elem = result["rows"][0]["elements"][i]
            if elem["status"] == "OK":
                info = {
                    "distance_m": elem["distance"]["value"],
                    "distance_km": round(elem["distance"]["value"] / 1000, 1),
                    "distance_text": elem["distance"]["text"],
                    "duration_s": elem["duration"]["value"],
                    "duration_min": round(elem["duration"]["value"] / 60),
                    "duration_text": elem["duration"]["text"],
                    "mode": mode,
                }
                api_results[dest] = info
                key = make_cache_key(mode, origin, dest)
                set_cached(key, info)
            else:
                api_results[dest] = {"error": elem["status"]}

    # Print results
    print(f"Origin: {origin}")
    print(f"Mode: {mode}")
    print()

    all_results = {}
    for dest in destinations:
        info = cached_results.get(dest) or api_results.get(dest, {})
        all_results[dest] = info
        if "error" in info:
            print(f"  -> {dest}")
            print(f"     ERROR: {info['error']}")
        else:
            cached_tag = " (cached)" if dest in cached_results else ""
            print(f"  -> {dest}{cached_tag}")
            print(f"     {info['distance_text']} | {info['duration_text']}")
        print()

    # JSON output for programmatic use
    print("---")
    print(json.dumps(all_results, indent=2))


def cmd_nearby(args):
    """Find nearby places."""
    usage = load_usage()
    ok, msg = check_budget(usage)
    if msg:
        print(msg)
    if not ok:
        return

    place_type = args.type or "point_of_interest"
    radius = args.radius or 1000

    # First geocode the address
    client = googlemaps.Client(key=load_api_key())
    geocode_result = client.geocode(args.address)
    log_usage(usage, "geocoding", 1)

    if not geocode_result:
        print(f"ERROR: Could not geocode '{args.address}'")
        return

    location = geocode_result[0]["geometry"]["location"]
    print(f"Searching near: {geocode_result[0]['formatted_address']}")
    print(f"Type: {place_type} | Radius: {radius}m")
    print()

    # Find nearby places
    places = client.places_nearby(
        location=location,
        radius=radius,
        type=place_type,
    )
    log_usage(usage, "places_nearby", 1)

    results = places.get("results", [])
    if not results:
        print("No results found.")
        return

    for i, place in enumerate(results[:10], 1):
        name = place.get("name", "Unknown")
        addr = place.get("vicinity", "N/A")
        rating = place.get("rating", "N/A")
        status = place.get("business_status", "")
        status_str = " (CLOSED)" if status == "CLOSED_PERMANENTLY" else ""
        print(f"  {i}. {name}{status_str}")
        print(f"     {addr}")
        if rating != "N/A":
            print(f"     Rating: {rating}/5")
        print()


def cmd_geocode(args):
    """Geocode an address."""
    usage = load_usage()
    ok, msg = check_budget(usage)
    if msg:
        print(msg)
    if not ok:
        return

    cache_key = make_cache_key("geocode", args.address)
    cached = get_cached(cache_key) if not args.no_cache else None

    if cached:
        print(f"Address: {cached['formatted_address']} (cached)")
        print(f"Lat: {cached['lat']}")
        print(f"Lng: {cached['lng']}")
        return

    client = googlemaps.Client(key=load_api_key())
    result = client.geocode(args.address)
    log_usage(usage, "geocoding", 1)

    if not result:
        print(f"ERROR: Could not geocode '{args.address}'")
        return

    loc = result[0]["geometry"]["location"]
    info = {
        "formatted_address": result[0]["formatted_address"],
        "lat": loc["lat"],
        "lng": loc["lng"],
    }
    set_cached(cache_key, info)

    print(f"Address: {info['formatted_address']}")
    print(f"Lat: {info['lat']}")
    print(f"Lng: {info['lng']}")


def cmd_usage(args):
    """Show usage statistics."""
    usage = load_usage()
    month = get_month_key()
    mt = usage.get("monthly_totals", {}).get(month, {})

    calls = mt.get("calls", 0)
    elements = mt.get("elements", 0)
    cost = mt.get("cost_usd", 0.0)
    pct = cost / MONTHLY_BUDGET_USD * 100

    print(f"Google Maps API Usage — {month}")
    print(f"{'='*40}")
    print(f"  API calls:      {calls}")
    print(f"  Total elements:  {elements}")
    print(f"  Estimated cost:  ${cost:.4f}")
    print(f"  Budget used:     {pct:.1f}%")
    print(f"  Budget remaining: ${MONTHLY_BUDGET_USD - cost:.2f}")
    print()

    if pct >= 90:
        print("  STATUS: HARD STOP — no more API calls allowed")
    elif pct >= 75:
        print("  STATUS: WARNING — approaching budget limit")
    else:
        print("  STATUS: OK")

    # Show cache stats
    cache = load_cache()
    valid = sum(
        1 for v in cache.values()
        if datetime.now() - datetime.fromisoformat(v["cached_at"]) <= timedelta(days=CACHE_TTL_DAYS)
    )
    print(f"\n  Cache: {valid} valid entries ({len(cache)} total)")

    # Show all months
    all_months = usage.get("monthly_totals", {})
    if len(all_months) > 1:
        print(f"\n  History:")
        for m in sorted(all_months.keys()):
            mc = all_months[m].get("cost_usd", 0)
            mn = all_months[m].get("calls", 0)
            print(f"    {m}: {mn} calls, ${mc:.4f}")


# --- Main ---
def main():
    parser = argparse.ArgumentParser(
        description="Google Maps API tool with caching and usage tracking"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # distance
    p_dist = subparsers.add_parser("distance", help="Calculate distance between places")
    p_dist.add_argument("addresses", nargs="*", help="Origin followed by destinations")
    p_dist.add_argument("--origin", help="Origin address (alternative to positional)")
    p_dist.add_argument("--to", nargs="+", help="Destination(s) (alternative to positional)")
    p_dist.add_argument("--mode", choices=["walking", "driving", "transit", "bicycling"],
                        default="walking", help="Travel mode (default: walking)")
    p_dist.add_argument("--no-cache", action="store_true", help="Bypass cache")

    # nearby
    p_near = subparsers.add_parser("nearby", help="Find nearby places")
    p_near.add_argument("address", help="Center address to search around")
    p_near.add_argument("--type", help="Place type (supermarket, pharmacy, gym, etc.)")
    p_near.add_argument("--radius", type=int, default=1000, help="Search radius in meters")

    # geocode
    p_geo = subparsers.add_parser("geocode", help="Geocode an address")
    p_geo.add_argument("address", help="Address to geocode")
    p_geo.add_argument("--no-cache", action="store_true", help="Bypass cache")

    # usage
    subparsers.add_parser("usage", help="Show API usage statistics")

    args = parser.parse_args()

    if args.command == "distance":
        cmd_distance(args)
    elif args.command == "nearby":
        cmd_nearby(args)
    elif args.command == "geocode":
        cmd_geocode(args)
    elif args.command == "usage":
        cmd_usage(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
