"""A day's dropoffs and pickups in a sensible driving order, with Google Maps links.

Order: start at base, always drive to the nearest remaining stop (straight-line).
Not optimal, but close for a dozen stops; owners can reorder in Google Maps.
Stops without coordinates go last, in booking order.
"""

import urllib.parse
from dataclasses import dataclass

from .models import Booking, BusinessSettings, Visit
from .pricing import straight_line_miles

LIVE = [Booking.Status.CONFIRMED, Booking.Status.OUT, Booking.Status.PICKED_UP, Booking.Status.CLOSED]
MAPS_WAYPOINTS = 9  # Google Maps links take up to 9 stops between start and end


@dataclass
class Stop:
    visit: Visit
    booking: Booking
    address: object  # Address or None (moving-in address not given yet)
    window: object

    @property
    def kind(self):
        return self.visit.kind

    @property
    def where(self):
        return str(self.address) if self.address else ""


def stops_for(day):
    visits = (Visit.objects.filter(booking__status__in=LIVE)
              .exclude(status=Visit.Status.CANCELLED)
              .select_related("booking__customer", "booking__dropoff_address", "booking__pickup_address",
                              "booking__package", "window", "requested_window"))
    stops = []
    for v in visits:
        window = v.window or v.requested_window
        if window is None or window.date != day:
            continue
        b = v.booking
        stops.append(Stop(v, b, b.dropoff_address if v.kind == "dropoff" else b.pickup_address, window))
    return order(stops)


def order(stops):
    s = BusinessSettings.load()
    here = (s.base_lat, s.base_lng)
    todo = [st for st in stops if st.address and st.address.lat is not None]
    rest = sorted((st for st in stops if not (st.address and st.address.lat is not None)),
                  key=lambda st: st.booking.pk)
    ordered = []
    while todo:
        nxt = min(todo, key=lambda st: straight_line_miles(here[0], here[1], st.address.lat, st.address.lng))
        ordered.append(nxt)
        todo.remove(nxt)
        here = (nxt.address.lat, nxt.address.lng)
    return ordered + rest


def maps_links(stops):
    """Google Maps directions links from base through the stops, in legs of
    up to 10 stops (each leg starts where the last one ended)."""
    s = BusinessSettings.load()
    places = [st.where for st in stops if st.where]
    origin = f"{s.base_lat},{s.base_lng}"
    links = []
    while places:
        leg, places = places[:MAPS_WAYPOINTS + 1], places[MAPS_WAYPOINTS + 1:]
        params = {"api": "1", "travelmode": "driving", "origin": origin, "destination": leg[-1]}
        if len(leg) > 1:
            params["waypoints"] = "|".join(leg[:-1])
        links.append("https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params))
        origin = leg[-1]
    return links
