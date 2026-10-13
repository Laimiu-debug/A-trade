"""Training-only connected grid regions and explicit Cartesian coverage."""
from itertools import product
from statistics import mean, pstdev
from .plateau_validation import neighborhood_statistics, check_identifiability


def adjacent(left, right, axes):
    changed = [i for i, (a, b) in enumerate(zip(left["coordinates"], right["coordinates"])) if a != b]
    if len(changed) != 1:
        return False
    i = changed[0]
    return isinstance(axes[i]["values"][0], str) or abs(left["coordinates"][i] - right["coordinates"][i]) == 1


def connected_regions(candidates, training, settings):
    eligible = {row["candidateId"] for row in candidates if training[row["candidateId"]]["neighborhood"]["passed"]}
    lookup = {row["candidateId"]: row for row in candidates}
    remaining, regions = set(eligible), []
    while remaining:
        queue, members = [min(remaining)], []
        remaining.remove(queue[0])
        while queue:
            current = queue.pop(0)
            members.append(current)
            for other in sorted(remaining):
                if adjacent(lookup[current], lookup[other], settings["axes"]):
                    queue.append(other)
                    remaining.remove(other)
        members.sort()
        values = [training[key]["fullSystem"]["metrics"]["totalReturnPct"] for key in members]
        coordinates = [lookup[key]["coordinates"] for key in members]
        bounds = [(min(row[i] for row in coordinates), max(row[i] for row in coordinates)) for i in range(len(settings["axes"]))]
        box = list(product(*(range(low, high + 1) for low, high in bounds)))
        present = {tuple(lookup[key]["coordinates"]) for key in members}
        holes = [list(point) for point in box if point not in present]
        numeric_axes_vary = all(low < high for low, high in bounds)
        stats = neighborhood_statistics(values, settings)
        check_identifiability(stats, [training[key]["fullSystem"].get("behaviorFingerprint") for key in members], settings)
        stable = stats["passed"]
        regions.append({"regionId": "r" + str(len(regions) + 1).zfill(3), "candidateIds": members,
                        "meanNetReturnPct": mean(values), "stdNetReturnPct": pstdev(values),
                        "positiveFraction": sum(value > 0 for value in values) / len(values),
                        "medianNetReturnPct": stats["medianNetReturnPct"], "relativeStd": stats["relativeStd"],
                        "returnRetention": stats["returnRetention"], "failures": stats["failures"],
                        "distinctBehaviorCount": stats["distinctBehaviorCount"],
                        "bounds": bounds, "cartesianPointCount": len(box), "holes": holes,
                        "completeCartesian": not holes, "allAxesVary": numeric_axes_vary,
                        "passed": stable, "intervalEligible": stable and not holes and numeric_axes_vary,
                        "definition": "connected training-qualified grid component; holes never silently filled"})
    return regions


def verified_ranges(candidates, axes, identities):
    """Only return cross-product intervals when every grid point is covered."""
    selected = [row for row in candidates if row["candidateId"] in identities]
    if not selected or not axes:
        return [], "empty_region"
    values = [[value for value in axis["values"] if any(row["params"][axis["path"]] == value for row in selected)] for axis in axes]
    if any(len(items) < 2 for items in values):
        return [], "an_axis_has_no_stable_width"
    covered = {tuple(row["params"][axis["path"]] for axis in axes) for row in selected}
    if any(point not in covered for point in product(*values)):
        return [], "projected_intervals_contain_unverified_combinations"
    if any(isinstance(axis["values"][0], (int, float)) and
           axis["values"].index(items[-1]) - axis["values"].index(items[0]) + 1 != len(items)
           for axis, items in zip(axes, values)):
        return [], "numeric_region_contains_grid_gaps"
    ranges = [{"path": axis["path"], "values": items,
               **({"min": min(items), "max": max(items)} if isinstance(items[0], (int, float)) else {})}
              for axis, items in zip(axes, values)]
    return ranges, None
