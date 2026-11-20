"""
Address lookup and distance from base.

The service area is a straight-line radius around base, so all we need from a
map service is each address's coordinates (geocoding); the distance is then
plain geometry (rentals.pricing.straight_line_miles).

Two interchangeable backends, chosen by the MAPS_BACKEND setting:

* GoogleMaps: the Google Geocoding API. Needs GOOGLE_MAPS_API_KEY.
* FakeMaps: for local development and tests. No network. Places each address
  due north of base at a distance taken from a rough table of ZIP codes
  (unknown ZIPs are 10 miles). Never used in production.
"""

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured



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
    # Rough straight-line miles from 21214. Good enough to click around with.
    ZIP_MILES = {
        "21214": 0, "21206": 3, "21234": 4, "21218": 4, "21239": 3, "21212": 5,
        "21201": 6, "21230": 8, "21224": 6, "21093": 9, "21228": 12, "21043": 15,
        "21044": 18, "21045": 17, "21061": 14, "21401": 26, "21403": 28,
        "20001": 36, "21701": 46, "21702": 48, "17401": 42, "21801": 90,
    }

    def geocode(self, address, near=None):
        if not address.get("line1") or not address.get("zip_code"):
            raise AddressNotFound("Please enter a street address and ZIP code.")
        lat0, lng0 = near or (39.352, -76.563)
        miles = self.ZIP_MILES.get(address["zip_code"][:5], 10)
        return Place(formatted=one_line(address), lat=float(lat0) + miles / 69.05, lng=float(lng0),
                     place_id=f"fake:{address['zip_code']}")


class GoogleMaps:
    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
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

    def geocode(self, address, near=None):
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


def get_maps():
    backend = settings.MAPS_BACKEND
    if backend == "fake":
        return FakeMaps()
    if backend == "google":
        if not settings.GOOGLE_MAPS_API_KEY:
            raise ImproperlyConfigured("GOOGLE_MAPS_API_KEY is not set.")
        return GoogleMaps(settings.GOOGLE_MAPS_API_KEY)
    raise ImproperlyConfigured(f"Unknown MAPS_BACKEND {backend!r}")


def lookup(address, base_lat, base_lng):
    """Confirm ``address`` and measure straight-line miles from base.

    Returns (Place, miles). Cached for a day so re-submitting a form doesn't
    pay for the same lookup twice.
    """
    from .pricing import straight_line_miles

    raw = json.dumps([one_line(address).lower(), str(base_lat), str(base_lng), settings.MAPS_BACKEND])
    key = "maps:" + hashlib.sha256(raw.encode()).hexdigest()
    hit = cache.get(key)
    if hit:
        return Place(**hit[0]), hit[1]
    place = get_maps().geocode(address, near=(base_lat, base_lng))
    miles = straight_line_miles(base_lat, base_lng, place.lat, place.lng)
    cache.set(key, (place.__dict__, miles), 60 * 60 * 24)
    return place, miles
