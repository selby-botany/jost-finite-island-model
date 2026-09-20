"""Literature-derived completed-run visualization payloads for fim-gui."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from math import exp, inf, isfinite, lgamma, log, log1p
from typing import Any, Final, TypedDict

from fim.model.params import Migration, SimulationParams
from fim.model.state import ModelState
from fim.statistics import equilibrium_g_st

_HISTOGRAM_BIN_COUNT: Final = 20
_MAX_BETA_COMPONENTS: Final = 64
_MAX_COMPOSITION_ALLELES: Final = 8
_MINIMUM_DECAY_CLASSES: Final = 2
# Lentz's algorithm needs a stand-in for an exactly-zero term; these
# are the conventional double-precision values for a beta continued
# fraction (Numerical Recipes' `betacf`).
_CONTINUED_FRACTION_TINY: Final = 1e-30
_CONTINUED_FRACTION_EPSILON: Final = 3e-16
_CONTINUED_FRACTION_MAX_ITERATIONS: Final = 300
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

        A thin, single-state wrapper over `pooled_literature_visual_
        payload` — every real caller with only one state to show
        (a live or reopened scalar run, one animation frame) goes
        through here; a completed batch's own several final states go
        through the pooled entry point directly instead. The same
        underlying computation either way, exactly like `fim.viz.
        scatter.scatter_panels`/`pooled_scatter_panels`'s own identical
        split: "how many states" is never a reason for one to be able
        to show something the other cannot.
    """
    return pooled_literature_visual_payload((state,), params)


def pooled_literature_visual_payload(
    states: Sequence[ModelState], params: SimulationParams
) -> LiteratureVisualPayload:
    """Return the run view's three supplemental visualization payloads, pooled.

    Args:
        states: One or more completed or reanalyzed population states
            sharing the same deme/locus shape — a single scalar run's
            own final state, or every replicate's own final state from
            a completed batch, pooled exactly the way `fim.viz.scatter.
            pooled_scatter_panels` already pools the same replicates'
            own frequency points for the scatter panel.
        params: The run's own validated simulation parameters, shared
            across every state in `states`.

    Returns:
        The identical shape `literature_visual_payload` returns.
    """
    return {
        "alleleComposition": pooled_allele_composition_payload(states),
        "frequencySpectrum": pooled_frequency_spectrum_payload(states, params),
        "isolationByDistance": pooled_isolation_by_distance_payload(states, params),
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

        A thin, single-state wrapper over `pooled_allele_composition_
        payload` — see that function's own docstring.
    """
    return pooled_allele_composition_payload((state,), max_alleles)


def pooled_allele_composition_payload(
    states: Sequence[ModelState], max_alleles: int = _MAX_COMPOSITION_ALLELES
) -> dict[str, Any]:
    """Return a per-deme stacked allele-composition barplot, pooled across states.

    Args:
        states: One or more population states sharing the same deme
            count — a single scalar run's own final state, or every
            replicate's own final state from a completed batch.
        max_alleles: Maximum globally common alleles shown explicitly.

    Returns:
        The identical shape `allele_composition_payload` returns —
        frequencies averaged over loci *and* over every state in
        `states`, so a single-state call and a many-state call render
        through the same one code path, differing only in `note` (which
        names the replicate count once there is more than one state to
        pool).
    """
    allele_totals = _aggregate_frequencies_by_allele(states)
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
    for deme_index in range(states[0].deme_count):
        per_deme = _aggregate_deme_frequencies(states, deme_index)
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

    note = (
        "Allele frequencies are averaged across loci within each deme."
        if len(states) == 1
        else (
            "Allele frequencies are averaged across loci and "
            f"{len(states)} replicates within each deme."
        )
    )
    return {
        "title": "Allele composition by deme",
        "alleles": legend,
        "demes": demes,
        "note": note,
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

        A thin, single-state wrapper over `pooled_frequency_spectrum_
        payload` — see that function's own docstring.
    """
    return pooled_frequency_spectrum_payload((state,), params, bin_count)


def pooled_frequency_spectrum_payload(
    states: Sequence[ModelState],
    params: SimulationParams,
    bin_count: int = _HISTOGRAM_BIN_COUNT,
) -> dict[str, Any]:
    """Return an empirical frequency spectrum, pooled across states.

    Args:
        states: One or more population states sharing the same deme/
            locus shape — a single scalar run's own final state, or
            every replicate's own final state from a completed batch.
        params: Parameters used to decide whether a scalar Wright beta
            approximation is available.
        bin_count: Number of equal-width bins over ``[0, 1]``.

    Returns:
        The identical shape `frequency_spectrum_payload` returns — the
        histogram pools every (deme, locus, allele) frequency across
        every state in `states`, the same row-wise concatenation `fim.
        viz.scatter.pooled_frequency_points` already uses for the
        scatter panel; the Wright beta overlay (when available) is
        matched against that same pooled allele-frequency distribution,
        not just one arbitrarily-chosen state's own.
    """
    frequencies = [
        frequency
        for state in states
        for deme in state.frequencies
        for locus in deme
        for frequency in locus.values()
        if frequency > 0.0
    ]
    bins = _histogram(frequencies, bin_count)
    overlay = _wright_beta_overlay(states, params, bin_count, len(frequencies))
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

        A thin, single-state wrapper over `pooled_isolation_by_distance_
        payload` — see that function's own docstring.
    """
    return pooled_isolation_by_distance_payload((state,), params)


def pooled_isolation_by_distance_payload(
    states: Sequence[ModelState], params: SimulationParams
) -> dict[str, Any] | None:
    """Return pairwise identity decay by migration-graph distance, pooled.

    Args:
        states: One or more population states sharing the same deme
            count — a single scalar run's own final state, or every
            replicate's own final state from a completed batch.
        params: Parameters carrying either scalar or matrix migration,
            shared across every state in `states`.

    Returns:
        The identical shape `isolation_by_distance_payload` returns —
        `pairCount` is the total number of (deme pair, replicate)
        identity samples averaged into that distance class's own
        `meanIdentity`, not only the number of deme pairs: pooling adds
        more samples to the same distance class the same way a second
        locus already would within one state, no separate accounting
        needed for "how many states" versus "how many deme pairs."
    """
    deme_count = states[0].deme_count
    distances = _migration_graph_distances(params.m, deme_count)
    grouped: dict[int, list[float]] = {}
    for left in range(deme_count - 1):
        for right in range(left + 1, deme_count):
            distance = distances[left][right]
            if distance is None or distance <= 0:
                continue
            grouped.setdefault(distance, []).extend(
                _pairwise_identity(state, left, right) for state in states
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


def _aggregate_deme_frequencies(
    states: Sequence[ModelState], deme_index: int
) -> dict[int, float]:
    """Return mean allele frequencies across loci and states for one deme."""
    totals: dict[int, float] = {}
    sample_count = 0
    for state in states:
        for locus_index in range(state.locus_count):
            frequency_map = state.frequency_map(deme_index, locus_index)
            for allele_id, frequency in frequency_map.items():
                totals[int(allele_id)] = totals.get(int(allele_id), 0.0) + frequency
            sample_count += 1
    return {allele_id: total / sample_count for allele_id, total in totals.items()}


def _aggregate_frequencies_by_allele(states: Sequence[ModelState]) -> dict[int, float]:
    """Return globally averaged allele frequencies across demes, loci, and states."""
    totals: dict[int, float] = {}
    deme_count = states[0].deme_count
    for deme_index in range(deme_count):
        for allele_id, frequency in _aggregate_deme_frequencies(
            states, deme_index
        ).items():
            totals[allele_id] = totals.get(allele_id, 0.0) + frequency / deme_count
    return totals


def _beta_continued_fraction(x: float, alpha: float, beta: float) -> float:
    """Return the continued fraction used by `_regularized_incomplete_beta`.

    Args:
        x: Point in ``(0, 1)`` at which the fraction is evaluated.
        alpha: First beta shape parameter.
        beta: Second beta shape parameter.

    Returns:
        The value of the modified Lentz continued fraction; the caller
        multiplies it by the front factor to obtain ``I_x(alpha, beta)``.
    """
    qab = alpha + beta
    qap = alpha + 1.0
    qam = alpha - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _CONTINUED_FRACTION_TINY:
        d = _CONTINUED_FRACTION_TINY
    d = 1.0 / d
    result = d
    for iteration in range(1, _CONTINUED_FRACTION_MAX_ITERATIONS + 1):
        even = (
            iteration
            * (beta - iteration)
            * x
            / ((qam + 2 * iteration) * (alpha + 2 * iteration))
        )
        d = 1.0 + even * d
        if abs(d) < _CONTINUED_FRACTION_TINY:
            d = _CONTINUED_FRACTION_TINY
        c = 1.0 + even / c
        if abs(c) < _CONTINUED_FRACTION_TINY:
            c = _CONTINUED_FRACTION_TINY
        d = 1.0 / d
        result *= d * c
        odd = (
            -(alpha + iteration)
            * (qab + iteration)
            * x
            / ((alpha + 2 * iteration) * (qap + 2 * iteration))
        )
        d = 1.0 + odd * d
        if abs(d) < _CONTINUED_FRACTION_TINY:
            d = _CONTINUED_FRACTION_TINY
        c = 1.0 + odd / c
        if abs(c) < _CONTINUED_FRACTION_TINY:
            c = _CONTINUED_FRACTION_TINY
        d = 1.0 / d
        step = d * c
        result *= step
        if abs(step - 1.0) < _CONTINUED_FRACTION_EPSILON:
            break
    return result


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


def _regularized_incomplete_beta(x: float, alpha: float, beta: float) -> float:
    """Return the beta cumulative distribution ``I_x(alpha, beta)``.

    Args:
        x: Point at which the distribution is evaluated; values outside
            ``[0, 1]`` are clamped to the nearest endpoint.
        alpha: First beta shape parameter.
        beta: Second beta shape parameter.

    Returns:
        0 and the probability mass at or below ``x``.

    A beta *CDF*, not a beta density, because the overlay is an
    expected bin *count*: integrating each bin exactly is the only
    way its total can equal the sample count it is drawn against. The
    midpoint-density approximation this replaced silently lost most of
    the mass whenever a mixture component was sharply peaked (a rare
    allele, whose whole distribution sits inside the leftmost bin),
    which is what made the overlay read as a flat line along the axis.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        lgamma(alpha + beta)
        - lgamma(alpha)
        - lgamma(beta)
        + alpha * log(x)
        + beta * log1p(-x)
    )
    front = exp(log_front) if log_front > -inf else 0.0
    # The fraction converges quickly only on its own side of the
    # distribution's mode; past it, evaluate the mirrored problem.
    if x < (alpha + 1.0) / (alpha + beta + 2.0):
        return front * _beta_continued_fraction(x, alpha, beta) / alpha
    return 1.0 - front * _beta_continued_fraction(1.0 - x, beta, alpha) / beta


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
    states: Sequence[ModelState],
    params: SimulationParams,
    bin_count: int,
    sample_count: int,
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
        _aggregate_frequencies_by_allele(states).values(), reverse=True
    )[:_MAX_BETA_COMPONENTS]
    eligible = [mean for mean in allele_means if 0.0 < mean < 1.0]
    if not eligible:
        return None
    width = 1.0 / bin_count
    concentration = (1.0 - theta) / theta
    components = [
        (
            max(mean * concentration, 1e-9),
            max((1.0 - mean) * concentration, 1e-9),
        )
        for mean in eligible
    ]
    # Drift resamples `N` gene copies per deme, so a real frequency is
    # always a multiple of `1 / N` and "absent" is the exact atom 0 --
    # which `pooled_frequency_spectrum_payload` drops from its own
    # histogram (`if frequency > 0.0`). The continuous beta has no such
    # atom, so its mass below one gene copy describes samples the bars
    # never counted; integrating from there instead of from 0 compares
    # like with like. Without this the leftmost bin's own overlay point
    # absorbs every rare allele's near-zero mass and towers over the
    # histogram it is meant to be read against.
    smallest_observable = 1.0 / params.N
    masses = []
    for index in range(bin_count):
        low = max(index * width, smallest_observable)
        high = (index + 1) * width
        # Exact probability mass in this bin, summed over the mixture --
        # not a density sampled at the bin's own midpoint, which loses
        # nearly all of a sharply-peaked component's own mass (see
        # `_regularized_incomplete_beta`'s own docstring). That loss is
        # what flattened this overlay onto the axis.
        masses.append(
            sum(
                _regularized_incomplete_beta(high, alpha, beta)
                - _regularized_incomplete_beta(low, alpha, beta)
                for alpha, beta in components
            )
            if high > low
            else 0.0
        )
    total_mass = sum(masses)
    if total_mass <= 0.0:
        return None
    # Expected counts, not densities: the overlay sums to exactly the
    # number of frequencies the histogram itself binned, so the two are
    # on one shared vertical scale.
    return [
        {
            "x": index * width + width / 2.0,
            "expectedCount": (mass / total_mass) * sample_count,
        }
        for index, mass in enumerate(masses)
    ]
