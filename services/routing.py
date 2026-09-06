"""
Route optimization: OR-Tools solver over a road-network distance/duration
matrix (OSRM), with a straight-line fallback when OSRM can't be reached.

Problem shape: this is an *open* path-TSP, not a round trip —
    transporter depot  ->  visit every selected farmer exactly once (any order)  ->  buyer
The vehicle does not need to return to its depot, so we fix the start node
at the depot and the end node at the buyer, and let OR-Tools choose the best
order for everything in between.

Why OSRM: straight-line (geodesic) distance treats a route across a river or
around a hill the same as one down a straight highway, so the "optimal"
stop order it picks can be wrong once you're actually on real roads. OSRM's
public /table service returns real driving distances and durations for the
whole matrix in one call. If it's unreachable (offline demo, no network,
rate-limited) we fall back to geodesic distance with an assumed average
rural road speed, so the app degrades gracefully instead of failing.

For >1 vehicle in future (splitting one big order across two trucks), this
same module extends naturally: just pass more starts/ends and vehicle
capacities into the CapacityDimension.
"""
import logging

from geopy.distance import geodesic
from ortools.constraint_solver import routing_enums_pb2, pywrapcp

try:
    import requests
except ImportError:  # pragma: no cover - requests is a listed dependency
    requests = None

logger = logging.getLogger(__name__)

OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving/{coords}"
OSRM_TIMEOUT_SECONDS = 4
FALLBACK_AVG_SPEED_KMPH = 35  # rough mixed rural/highway speed, used only offline


def _geodesic_matrices(points):
    """Straight-line fallback: distance in meters + an estimated duration."""
    n = len(points)
    distance_m = [[0] * n for _ in range(n)]
    duration_s = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                meters = geodesic(points[i], points[j]).meters
                distance_m[i][j] = int(meters)
                duration_s[i][j] = int(meters / 1000 / FALLBACK_AVG_SPEED_KMPH * 3600)
    return distance_m, duration_s, "straight_line"


def _osrm_matrices(points):
    """
    Real road-network distance + duration from OSRM's table service, for
    every point pair at once. Returns None (caller falls back) on any
    network error, timeout, or malformed response — this must never raise,
    since it sits on the request path for placing an order.
    """
    if requests is None:
        return None
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = OSRM_TABLE_URL.format(coords=coords)
    try:
        resp = requests.get(
            url, params={"annotations": "distance,duration"}, timeout=OSRM_TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok":
            return None
        n = len(points)
        distances = data["distances"]
        durations = data["durations"]
        distance_m = [[int(distances[i][j] or 0) for j in range(n)] for i in range(n)]
        duration_s = [[int(durations[i][j] or 0) for j in range(n)] for i in range(n)]
        return distance_m, duration_s, "road_network"
    except Exception as exc:
        logger.warning("OSRM table request failed (%s); falling back to straight-line distance.", exc)
        return None


def _build_matrices(points):
    return _osrm_matrices(points) or _geodesic_matrices(points)


def pickup_route_distance_km(depot_point, pickup_points):
    """
    Shortest total distance to start at `depot_point` and visit every point
    in `pickup_points`, in whichever order is shortest, ending at whichever
    pickup happens to be last — the buyer/dropoff is NOT part of this at
    all. This exists purely so transporters can be compared against each
    other fairly: "how far does this truck actually have to drive to
    gather everything" rather than "how far is this truck from just the
    first farmer" (which used to be the whole comparison and could pick a
    truck that's close to one farmer but far from the rest).

    Once a transporter is actually chosen, the real delivery route
    (depot -> pickups -> buyer) is computed separately by optimize_route().

    This is "visit every stop, start fixed, end free" — solved with
    OR-Tools using the same heuristic search as optimize_route(), NOT by
    checking every possible visiting order. Checking every order is O(n!),
    which blows up fast (10 stops = 3.6 million orders to check); OR-Tools'
    heuristics find a route that's optimal or extremely close to it in a
    small, bounded amount of time regardless of how many stops there are.

    The trick to get a "free end" (no fixed final stop) out of a solver
    that normally wants a fixed end: add one extra virtual node with a
    distance of 0 to and from every real point, and tell OR-Tools to end
    there. Ending at the virtual node costs nothing extra, so the solver
    is effectively free to stop at whichever real stop is most convenient
    last — exactly what an open (non-round-trip) pickup route needs.
    """
    points = [depot_point] + list(pickup_points)
    n = len(points)
    if n <= 1:
        return 0.0
    if n == 2:
        distance_matrix, _, _ = _build_matrices(points)
        return round(distance_matrix[0][1] / 1000.0, 2)

    real_distance_matrix, _, _ = _build_matrices(points)
    free_end_index = n  # one virtual node appended after all real ones
    padded_matrix = [row + [0] for row in real_distance_matrix] + [[0] * (n + 1)]

    manager, routing, solution = _solve_ordering(padded_matrix, 0, free_end_index, n + 1)

    total_meters = 0
    index = routing.Start(0)
    prev_node = manager.IndexToNode(index)
    while not routing.IsEnd(index):
        index = solution.Value(routing.NextVar(index))
        node = manager.IndexToNode(index)
        total_meters += padded_matrix[prev_node][node]  # 0 for the final hop into the virtual node
        prev_node = node

    return round(total_meters / 1000.0, 2)


def _solve_ordering(distance_matrix, depot_index, dropoff_index, n):
    """
    Runs OR-Tools over a handful of first-solution strategies and keeps
    whichever produces the cheapest route, then polishes it with guided
    local search. Trying more than one starting heuristic costs a fraction
    of a second here but reliably avoids the occasional bad tour a single
    strategy can get stuck in, especially once there are 5+ pickup stops.
    """
    manager = pywrapcp.RoutingIndexManager(n, 1, [depot_index], [dropoff_index])
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Scale search effort with problem size instead of a fixed 3s for every
    # order — trivial 1-2 stop cases resolve instantly, larger multi-farmer
    # pickups get more room to improve on the first solution.
    time_budget_seconds = min(2 + n * 0.6, 12)

    strategies = [
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
        routing_enums_pb2.FirstSolutionStrategy.SAVINGS,
        routing_enums_pb2.FirstSolutionStrategy.CHRISTOFIDES,
    ]

    best_solution = None
    best_objective = None
    for strategy in strategies:
        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = strategy
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.FromSeconds(int(max(1, time_budget_seconds / len(strategies))))

        solution = routing.SolveWithParameters(search_parameters)
        if solution is None:
            continue
        objective = solution.ObjectiveValue()
        if best_objective is None or objective < best_objective:
            best_objective = objective
            best_solution = solution

    if best_solution is None:
        raise RuntimeError("OR-Tools could not find a feasible route.")

    return manager, routing, best_solution


def optimize_route(depot_point, pickup_points, pickup_labels, dropoff_point, dropoff_label="Buyer (delivery)"):
    """
    depot_point:    (lat, lon) of the transporter's starting location
    pickup_points:  list[(lat, lon)] of farmer locations, in any input order
    pickup_labels:  list[str] parallel to pickup_points (farmer names), for output
    dropoff_point:  (lat, lon) of the buyer
    dropoff_label:  display name for the final stop, e.g. "Buyer: Sunrise Mandi
                    Traders" — defaults to a generic label if the caller
                    doesn't have one (kept optional so existing callers
                    don't break).

    Returns dict:
      {
        "ordered_stops": [ {"label", "lat", "lon", "leg_km", "leg_min"}, ... ],
        "total_distance_km": float,
        "total_duration_min": float,
        "distance_source": "road_network" | "straight_line",
      }
    The first stop is always the depot (distance/duration 0), the last is
    the buyer. "distance_source" tells the UI whether these are real road
    distances (OSRM reachable) or a straight-line estimate (offline fallback).
    """
    points = [depot_point] + list(pickup_points) + [dropoff_point]
    labels = ["Transporter depot"] + list(pickup_labels) + [dropoff_label]

    n = len(points)
    depot_index = 0
    dropoff_index = n - 1

    distance_matrix, duration_matrix, distance_source = _build_matrices(points)

    # Trivial cases: nothing to order (0 or 1 pickup stops means only one
    # possible route), so skip the solver entirely.
    if n <= 2:
        total_m = distance_matrix[depot_index][dropoff_index] if n == 2 else 0
        total_s = duration_matrix[depot_index][dropoff_index] if n == 2 else 0
        return {
            "ordered_stops": [
                {
                    "label": labels[i], "lat": points[i][0], "lon": points[i][1],
                    "leg_km": 0.0 if i == 0 else round(total_m / 1000.0, 2),
                    "leg_min": 0.0 if i == 0 else round(total_s / 60.0, 1),
                }
                for i in range(n)
            ],
            "total_distance_km": round(total_m / 1000.0, 2),
            "total_duration_min": round(total_s / 60.0, 1),
            "distance_source": distance_source,
        }

    manager, routing, solution = _solve_ordering(distance_matrix, depot_index, dropoff_index, n)

    ordered_stops = []
    index = routing.Start(0)
    total_meters = 0
    total_seconds = 0
    prev_node = manager.IndexToNode(index)
    while True:
        node = manager.IndexToNode(index)
        leg_km = 0.0
        leg_min = 0.0
        if ordered_stops:  # not the first stop
            leg_meters = distance_matrix[prev_node][node]
            leg_seconds = duration_matrix[prev_node][node]
            leg_km = round(leg_meters / 1000.0, 2)
            leg_min = round(leg_seconds / 60.0, 1)
            total_meters += leg_meters
            total_seconds += leg_seconds
        ordered_stops.append({
            "label": labels[node],
            "lat": points[node][0],
            "lon": points[node][1],
            "leg_km": leg_km,
            "leg_min": leg_min,
        })
        if routing.IsEnd(index):
            break
        prev_node = node
        index = solution.Value(routing.NextVar(index))

    return {
        "ordered_stops": ordered_stops,
        "total_distance_km": round(total_meters / 1000.0, 2),
        "total_duration_min": round(total_seconds / 60.0, 1),
        "distance_source": distance_source,
    }
