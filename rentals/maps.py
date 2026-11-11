"""
Address lookup and driving distances.

Two interchangeable backends, chosen by the MAPS_BACKEND setting:

* GoogleMaps — the real thing. Uses the Geocoding API to confirm an address
  and the Routes API for driving distance from base. Needs GOOGLE_MAPS_API_KEY.
* FakeMaps — for local development and tests. No network. Distances come from
  a rough table of ZIP codes (driving miles from 21214); unknown ZIPs are 10
  miles. Never used in production.

Both return the same things, so the rest of the code doesn't care which runs.
"""

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

METERS_PER_MILE = 1609.344


class AddressNotFound(Exception):
    pass


class MapsUnavailable(Exception):
    """The maps service failed (network, quota). Ask the renter to retry."""


@dataclass
class Place:
    formatted: str
    lat: float | None = None
    lng: float | None = None
    place_id: str = ""


def one_line(address):
    """{'line1':..., 'city':..., 'state':..., 'zip_code':...} → one string."""
    parts = [address.get("line1", ""), address.get("line2", ""), address.get("city", ""),
             f"{address.get('state', '')} {address.get('zip_code', '')}".strip()]
    return ", ".join(p for p in parts if p)


class FakeMaps:
    # Rough driving miles from 21214. Good enough to click around with.
    ZIP_MILES = {
        "21214": 0, "21206": 3, "21234": 4, "21218": 4, "21239": 3, "21212": 5,
        "21201": 7, "21230": 9, "21224": 7, "21093": 12, "21228": 14, "21043": 18,
        "21044": 25, "21045": 24, "21061": 20, "21401": 35, "21403": 37,
        "20001": 45, "21701": 52, "21702": 55, "17401": 55, "21801": 125,
    }

    def geocode(self, address):
        if not address.get("line1") or not address.get("zip_code"):
            raise AddressNotFound("Please enter a street address and ZIP code.")
        return Place(formatted=one_line(address), place_id=f"fake:{address['zip_code']}")

    def driving_miles(self, origin, place):
        zip_code = place.place_id.removeprefix("fake:")
        return float(self.ZIP_MILES.get(zip_code, 10))


class GoogleMaps:
    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
    # Results precise enough to deliver to.
    PRECISE_TYPES = {"street_address", "premise", "subpremise"}

    def __init__(self, api_key):
        self.api_key = api_key

    def _request(self, req):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.load(resp)
        except Exception as exc:  # network error, HTTP error, bad JSON
            raise MapsUnavailable("The map service didn't respond. Please try again.") from exc

    def geocode(self, address):
        query = urllib.parse.urlencode(
            {"address": one_line(address), "components": "country:US", "key": self.api_key}
        )
        data = self._request(urllib.request.Request(f"{self.GEOCODE_URL}?{query}"))
        if data.get("status") == "ZERO_RESULTS" or not data.get("results"):
            raise AddressNotFound("We couldn't find that address. Please check it.")
        if data.get("status") != "OK":
            raise MapsUnavailable(f"Map lookup failed ({data.get('status')}).")
        best = data["results"][0]
        if not self.PRECISE_TYPES & set(best.get("types", [])):
            raise AddressNotFound("Please enter a full street address, including house number.")
        loc = best["geometry"]["location"]
        return Place(formatted=best["formatted_address"], lat=loc["lat"], lng=loc["lng"],
                     place_id=best["place_id"])

    def driving_miles(self, origin, place):
        body = json.dumps({
            "origin": {"address": origin},
            "destination": {"placeId": place.place_id},
            "travelMode": "DRIVE",
        }).encode()
        req = urllib.request.Request(self.ROUTES_URL, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "routes.distanceMeters",
        })
        data = self._request(req)
        routes = data.get("routes") or []
        if not routes:
            raise AddressNotFound("We couldn't find a driving route to that address.")
        return routes[0]["distanceMeters"] / METERS_PER_MILE


def get_maps():
    backend = settings.MAPS_BACKEND
    if backend == "fake":
        return FakeMaps()
    if backend == "google":
        if not settings.GOOGLE_MAPS_API_KEY:
            raise ImproperlyConfigured("GOOGLE_MAPS_API_KEY is not set.")
        return GoogleMaps(settings.GOOGLE_MAPS_API_KEY)
    raise ImproperlyConfigured(f"Unknown MAPS_BACKEND {backend!r}")


def lookup(address, origin):
    """Confirm ``address`` and measure driving miles from ``origin``.

    Returns (Place, miles). Cached for a day so re-submitting a form doesn't
    pay for the same lookup twice.
    """
    raw = json.dumps([one_line(address).lower(), origin.lower(), settings.MAPS_BACKEND])
    key = "maps:" + hashlib.sha256(raw.encode()).hexdigest()
    hit = cache.get(key)
    if hit:
        return Place(**hit[0]), hit[1]
    maps = get_maps()
    place = maps.geocode(address)
    miles = maps.driving_miles(origin, place)
    cache.set(key, (place.__dict__, miles), 60 * 60 * 24)
    return place, miles
