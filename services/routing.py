"""
Route optimization using Google OR-Tools.

Problem shape: this is an *open* path-TSP, not a round trip —
    transporter depot  ->  visit every selected farmer exactly once (any order)  ->  buyer
The vehicle does not need to return to its depot, so we fix the start node
at the depot and the end node at the buyer, and let OR-Tools choose the best
order for everything in between.

For >1 vehicle in future (splitting one big order across two trucks), this
same module extends naturally: just pass more starts/ends and vehicle
capacities into the CapacityDimension.
"""
from geopy.distance import geodesic
from ortools.constraint_solver import routing_enums_pb2, pywrapcp


def _build_distance_matrix(points):
    n = len(points)
    matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                # OR-Tools wants integers; use meters for precision.
                matrix[i][j] = int(geodesic(points[i], points[j]).meters)
    return matrix


def optimize_route(depot_point, pickup_points, pickup_labels, dropoff_point):
    """
    depot_point:    (lat, lon) of the transporter's starting location
    pickup_points:  list[(lat, lon)] of farmer locations, in any input order
    pickup_labels:  list[str] parallel to pickup_points (farmer names), for output
    dropoff_point:  (lat, lon) of the buyer

    Returns dict:
      {
        "ordered_stops": [ {"label": ..., "lat":..., "lon":..., "leg_km": ...}, ... ],
        "total_distance_km": float,
      }
    The first stop is always the depot (distance 0), the last is the buyer.
    """
    points = [depot_point] + list(pickup_points) + [dropoff_point]
    labels = ["Transporter depot"] + list(pickup_labels) + ["Buyer (delivery)"]

    n = len(points)
    depot_index = 0
    dropoff_index = n - 1

    # Trivial cases: nothing to optimize.
    if n <= 2:
        total_km = geodesic(depot_point, dropoff_point).km if n == 2 else 0.0
        return {
            "ordered_stops": [
                {"label": labels[i], "lat": points[i][0], "lon": points[i][1],
                 "leg_km": 0.0 if i == 0 else round(total_km, 2)}
                for i in range(n)
            ],
            "total_distance_km": round(total_km, 2),
        }

    distance_matrix = _build_distance_matrix(points)

    manager = pywrapcp.RoutingIndexManager(n, 1, [depot_index], [dropoff_index])
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.FromSeconds(3)

    solution = routing.SolveWithParameters(search_parameters)
    if solution is None:
        raise RuntimeError("OR-Tools could not find a feasible route.")

    ordered_stops = []
    index = routing.Start(0)
    total_meters = 0
    prev_node = manager.IndexToNode(index)
    while True:
        node = manager.IndexToNode(index)
        if node == prev_node and node != depot_index:
            pass
        leg_km = 0.0
        if ordered_stops:  # not the first stop
            leg_meters = distance_matrix[prev_node][node]
            leg_km = round(leg_meters / 1000.0, 2)
            total_meters += leg_meters
        ordered_stops.append({
            "label": labels[node],
            "lat": points[node][0],
            "lon": points[node][1],
            "leg_km": leg_km,
        })
        if routing.IsEnd(index):
            break
        prev_node = node
        index = solution.Value(routing.NextVar(index))

    return {
        "ordered_stops": ordered_stops,
        "total_distance_km": round(total_meters / 1000.0, 2),
    }
