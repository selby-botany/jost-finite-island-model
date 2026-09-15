"""Literature-derived completed-run visualization payloads for fim-gui."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from math import exp, isfinite, lgamma, log
from typing import Any, Final, TypedDict

from fim.model.params import Migration, SimulationParams
from fim.model.state import ModelState
from fim.statistics import equilibrium_g_st

_HISTOGRAM_BIN_COUNT: Final = 20
_MAX_BETA_COMPONENTS: Final = 64
_MAX_COMPOSITION_ALLELES: Final = 8
_MINIMUM_DECAY_CLASSES: Final = 2
_COMPOSITION_COLORS: Final = (
    "#0072b2",
    "#d55e00",
    "#009e73",
    "#cc79a7",
    "#e69f00",
    "#56b4e9",
    "#f0e442",
    "#000000",
    "#999999",
)


class LiteratureVisualPayload(TypedDict):
    """Client-ready payload for the run view's own supplemental panels."""

    alleleComposition: dict[str, Any]
    frequencySpectrum: dict[str, Any]
    isolationByDistance: dict[str, Any] | None


def literature_visual_payload(
    state: ModelState, params: SimulationParams
) -> LiteratureVisualPayload:
    """Return the run view's three supplemental visualization payloads for one state.

    Args:
        state: The completed or reanalyzed population state to display.
        params: The state run's validated simulation parameters.

    Returns:
        A JSON-ready object with a per-deme stacked allele-composition
        barplot, an empirical allele-frequency spectrum (with a Wright
        beta overlay when scalar assumptions are available), and an
        isolation-by-distance summary when migration edges define at
        least one deme distance class. The barplot was, for one
        interval this session, called "STRUCTURE-style allele
        composition" and then removed outright over a misreading of
        the request to drop that name — the actual ask was only that
        the *label* go, since "STRUCTURE-style" names an external tool
        this project has nothing to do with and describes nothing
        about what the chart shows; `allele_composition_payload` is
        the restored barplot under its own descriptive title instead.
        Its legend used to show each bar segment's raw internal allele
        id directly (`f"Allele {allele_id}"`, this project's own
        minted-and-retired numbering, not a compact, gap-free display
        order a reader could make sense of at a glance — "where did
        all the missing ones go?"); `allele_composition_payload` now
        remaps the shown alleles to a dense 1-based display order
        instead.
    """
    return {
        "alleleComposition": allele_composition_payload(state),
        "frequencySpectrum": frequency_spectrum_payload(state, params),
        "isolationByDistance": isolation_by_distance_payload(state, params),
    }


def allele_composition_payload(
    state: ModelState, max_alleles: int = _MAX_COMPOSITION_ALLELES
) -> dict[str, Any]:
    """Return a per-deme stacked allele-composition barplot payload.

    Args:
        state: The population state to summarize.
        max_alleles: Maximum globally common alleles shown explicitly.

    Returns:
        A JSON-ready mapping with ordered allele legend entries and one
        stacked-frequency segment list per deme. Frequencies are averaged
        over loci, so every deme bar sums to one. Legend labels are a
        dense 1-based display order ("Allele 1", "Allele 2", ...), not
        the underlying minted-and-retired internal allele id: those ids
        are sparse (mutation retires old ids and mints new ones, so a
        run's surviving alleles carry ids like 3, 47, 132, ...), and
        showing them raw in a legend of only the top `max_alleles` left
        a reader with no way to tell whether a low id was simply not
        common enough to make the cut or never existed at all.
    """
    allele_totals = _aggregate_frequencies_by_allele(state)
    top_alleles = tuple(
        allele_id
        for allele_id, _ in sorted(
            allele_totals.items(), key=lambda item: (-round(item[1], 15), item[0])
        )[:max_alleles]
    )
    allele_keys = [str(allele_id) for allele_id in top_alleles]
    legend = [
        {
            "key": key,
            "label": f"Allele {display_index}",
            "color": _COMPOSITION_COLORS[index % len(_COMPOSITION_COLORS)],
        }
        for index, (display_index, key) in enumerate(
            zip(range(1, len(allele_keys) + 1), allele_keys, strict=True)
        )
    ]
    legend.append(
        {
            "key": "other",
            "label": "Other alleles",
            "color": _COMPOSITION_COLORS[-1],
        }
    )

    demes = []
    for deme_index in range(state.deme_count):
        per_deme = _aggregate_deme_frequencies(state, deme_index)
        shown_total = 0.0
        segments = []
        for key, allele_id in zip(allele_keys, top_alleles, strict=True):
            value = per_deme.get(allele_id, 0.0)
            shown_total += value
            segments.append({"key": key, "value": value})
        other = max(0.0, 1.0 - shown_total)
        if other > 0.0:
            segments.append({"key": "other", "value": other})
        demes.append({"deme": deme_index + 1, "segments": segments})

    return {
        "title": "Allele composition by deme",
        "alleles": legend,
        "demes": demes,
        "note": "Allele frequencies are averaged across loci within each deme.",
    }


def frequency_spectrum_payload(
    state: ModelState, params: SimulationParams, bin_count: int = _HISTOGRAM_BIN_COUNT
) -> dict[str, Any]:
    """Return an empirical frequency spectrum and optional Wright beta overlay.

    Args:
        state: The population state to summarize.
        params: Parameters used to decide whether a scalar Wright beta
            approximation is available.
        bin_count: Number of equal-width bins over ``[0, 1]``.

    Returns:
        A JSON-ready mapping with bin counts, overlay points, and a note
        naming the overlay assumptions.
    """
    frequencies = [
        frequency
        for deme in state.frequencies
        for locus in deme
        for frequency in locus.values()
        if frequency > 0.0
    ]
    bins = _histogram(frequencies, bin_count)
    overlay = _wright_beta_overlay(state, params, bin_count, len(frequencies))
    return {
        "title": "Allele-frequency spectrum",
        "bins": bins,
        "betaOverlay": overlay,
        "overlayLabel": "Wright beta overlay" if overlay else None,
        "note": (
            "The overlay is variance-matched from scalar N, m, μ and "
            "the equilibrium G_ST approximation; it is a guide, not a "
            "fitted likelihood."
            if overlay
            else "Wright beta overlay requires scalar N, m, and μ."
        ),
    }


def isolation_by_distance_payload(
    state: ModelState, params: SimulationParams
) -> dict[str, Any] | None:
    """Return pairwise identity decay by migration-graph distance.

    Args:
        state: The population state to summarize.
        params: Parameters carrying either scalar or matrix migration.

    Returns:
        ``None`` when every deme pair has the same graph distance. Otherwise
        a JSON-ready object with mean identity by distance and a log-linear
        decay fit when at least two positive distance classes are available.
    """
    distances = _migration_graph_distances(params.m, state.deme_count)
    grouped: dict[int, list[float]] = {}
    for left in range(state.deme_count - 1):
        for right in range(left + 1, state.deme_count):
            distance = distances[left][right]
            if distance is None or distance <= 0:
                continue
            grouped.setdefault(distance, []).append(
                _pairwise_identity(state, left, right)
            )
    if len(grouped) < _MINIMUM_DECAY_CLASSES:
        return None
    points = [
        {
            "distance": distance,
            "meanIdentity": sum(values) / len(values),
            "pairCount": len(values),
        }
        for distance, values in sorted(grouped.items())
    ]
    return {
        "title": "Isolation-by-distance identity decay",
        "points": points,
        "fit": _log_linear_fit(points),
        "note": (
            "Distances are shortest paths over non-zero migration edges; "
            "the fitted curve is log-linear on mean pairwise identity."
        ),
    }


def _aggregate_deme_frequencies(state: ModelState, deme_index: int) -> dict[int, float]:
    """Return mean allele frequencies across loci for one deme."""
    totals: dict[int, float] = {}
    for locus_index in range(state.locus_count):
        frequency_map = state.frequency_map(deme_index, locus_index)
        for allele_id, frequency in frequency_map.items():
            totals[int(allele_id)] = totals.get(int(allele_id), 0.0) + frequency
    return {allele_id: total / state.locus_count for allele_id, total in totals.items()}


def _aggregate_frequencies_by_allele(state: ModelState) -> dict[int, float]:
    """Return globally averaged allele frequencies across demes and loci."""
    totals: dict[int, float] = {}
    divisor = state.deme_count
    for deme_index in range(state.deme_count):
        for allele_id, frequency in _aggregate_deme_frequencies(
            state, deme_index
        ).items():
            totals[allele_id] = totals.get(allele_id, 0.0) + frequency / divisor
    return totals


def _beta_density(x: float, alpha: float, beta: float) -> float:
    """Return the beta density at ``x`` from log-gamma arithmetic."""
    if x <= 0.0 or x >= 1.0:
        return 0.0
    log_beta = lgamma(alpha) + lgamma(beta) - lgamma(alpha + beta)
    return exp((alpha - 1.0) * log(x) + (beta - 1.0) * log(1.0 - x) - log_beta)


def _histogram(values: Sequence[float], bin_count: int) -> list[dict[str, float | int]]:
    """Return equal-width histogram bins over the unit interval."""
    counts = [0 for _ in range(bin_count)]
    for value in values:
        bounded = min(max(value, 0.0), 1.0)
        index = min(bin_count - 1, int(bounded * bin_count))
        counts[index] += 1
    width = 1.0 / bin_count
    return [
        {
            "low": index * width,
            "high": (index + 1) * width,
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def _log_linear_fit(points: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Return a log-linear least-squares fit for positive identity points."""
    samples = [
        (float(point["distance"]), log(float(point["meanIdentity"])))
        for point in points
        if float(point["meanIdentity"]) > 0.0
    ]
    if len(samples) < _MINIMUM_DECAY_CLASSES:
        return None
    mean_x = sum(x for x, _ in samples) / len(samples)
    mean_y = sum(y for _, y in samples) / len(samples)
    denominator = sum((x - mean_x) ** 2 for x, _ in samples)
    if denominator == 0.0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in samples) / denominator
    intercept = mean_y - slope * mean_x
    total_ss = sum((y - mean_y) ** 2 for _, y in samples)
    residual_ss = sum((y - (intercept + slope * x)) ** 2 for x, y in samples)
    r_squared = 1.0 if total_ss == 0.0 else 1.0 - residual_ss / total_ss
    return {
        "intercept": intercept,
        "slope": slope,
        "rSquared": r_squared,
        "points": [
            {"distance": x, "meanIdentity": exp(intercept + slope * x)}
            for x, _ in samples
        ],
    }


def _migration_graph_distances(
    migration: Migration, deme_count: int
) -> list[list[int | None]]:
    """Return unweighted shortest-path distances over non-zero migration edges."""
    if isinstance(migration, float):
        adjacency = [
            [neighbor for neighbor in range(deme_count) if neighbor != deme]
            for deme in range(deme_count)
        ]
    else:
        matrix = migration
        adjacency = [
            [
                neighbor
                for neighbor, rate in enumerate(row)
                if neighbor != deme and rate > 0.0
            ]
            for deme, row in enumerate(matrix)
        ]
    return [_shortest_paths(adjacency, start) for start in range(deme_count)]


def _pairwise_identity(state: ModelState, left: int, right: int) -> float:
    """Return mean allele-identity probability for one deme pair."""
    total = 0.0
    for locus_index in range(state.locus_count):
        left_map = state.frequency_map(left, locus_index)
        right_map = state.frequency_map(right, locus_index)
        alleles = set(left_map) | set(right_map)
        total += sum(
            left_map.get(allele, 0.0) * right_map.get(allele, 0.0) for allele in alleles
        )
    return total / state.locus_count


def _shortest_paths(adjacency: Sequence[Sequence[int]], start: int) -> list[int | None]:
    """Return breadth-first shortest paths from one start node."""
    distances: list[int | None] = [None] * len(adjacency)
    distances[start] = 0
    queue: deque[int] = deque([start])
    while queue:
        current = queue.popleft()
        current_distance = distances[current]
        for neighbor in adjacency[current]:
            if distances[neighbor] is not None:
                continue
            distances[neighbor] = (
                1 if current_distance is None else current_distance + 1
            )
            queue.append(neighbor)
    return distances


def _wright_beta_overlay(
    state: ModelState, params: SimulationParams, bin_count: int, sample_count: int
) -> list[dict[str, float]] | None:
    """Return a scalar Wright beta overlay, or ``None`` outside its scope."""
    if (
        not isinstance(params.N, int)
        or not isinstance(params.m, float)
        or not isinstance(params.mu, float)
        or sample_count == 0
    ):
        return None
    theta = equilibrium_g_st(params.N, params.m, params.mu, params.d)
    if theta <= 0.0 or theta >= 1.0 or not isfinite(theta):
        return None
    allele_means = sorted(
        _aggregate_frequencies_by_allele(state).values(), reverse=True
    )[:_MAX_BETA_COMPONENTS]
    eligible = [mean for mean in allele_means if 0.0 < mean < 1.0]
    if not eligible:
        return None
    width = 1.0 / bin_count
    overlay = []
    for index in range(bin_count):
        x = (index + 0.5) * width
        mixture = 0.0
        for mean in eligible:
            concentration = (1.0 - theta) / theta
            mixture += _beta_density(
                x,
                max(mean * concentration, 1e-9),
                max((1.0 - mean) * concentration, 1e-9),
            )
        overlay.append(
            {
                "x": x,
                "expectedCount": (mixture / len(eligible)) * sample_count * width,
            }
        )
    return overlay
