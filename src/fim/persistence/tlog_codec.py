"""Record codec of the binary trajectory log.

The log (`fim.persistence.tlog`) is a sequence of blocks; a block holds
generation records; this module encodes and decodes one record. Every
function works on plain NumPy arrays and integers, so each one can be
compiled by Numba (`nogil`, cached) when Numba is installed and runs
unchanged, only more slowly, when it is not. Importing `fim` never needs
Numba.

A record:

```text
padded-4 varint  body_len   bytes after this field
padded-4 varint  rows       rows of the whole generation (not only a delta)
u8               kind       0 = full frame, 1 = delta (changed pairs only)
varint           gen_delta  generations since the previous record (0 for the
                            first record of a block, whose generation is in
                            the block header; usually 1; more when the run
                            is thinned)
body
```

A full body is one *pair body* per (deme, locus) pair, in pair order. A
delta body is `varint n_changed`, then `n_changed` times
`(varint pair - previous changed pair, pair body)`.

A pair body is `varint h`:

- `h == 1`: one allele at frequency exactly `1.0`; its `varint` id follows;
- `h >= 2`: `n = h >> 1` alleles and `esc = h & 1`; then for each allele
  its `varint` id and, when `esc == 0`, a `varint` count `c` meaning
  frequency `c / size`, or, when `esc == 1`, the eight bytes of the
  little-endian float64.

Count coding is lossless because the encoder checks it: it stores `c` only
when `float(c) / float(size)` equals the frequency bit for bit, and
otherwise (or when the deme size is unknown, `0`) stores every frequency of
that pair as the raw float64. No frequency is ever rounded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

try:  # Numba is an optional dependency of fim.
    import numba

    HAVE_NUMBA = True
except ImportError:  # pragma: no cover - exercised only without numba
    numba = None  # type: ignore[assignment]
    HAVE_NUMBA = False

PAD4 = 4
"""Bytes of a padded varint: a length written before it is known."""

KIND_FULL = 0
KIND_DELTA = 1

_COUNT_PAD = 3
"""Bytes of the padded `n_changed` varint of a delta record."""


def kernel(function: Callable[..., Any]) -> Callable[..., Any]:
    """Compile `function` with Numba (`nogil`, cached) when it is available.

    Args:
        function: A function written in the Numba-compatible subset.

    Returns:
        The compiled function, or `function` itself without Numba.
    """
    if HAVE_NUMBA:
        return numba.njit(cache=True, nogil=True)(function)
    return function


@kernel
def put_varint(out, pos, v):
    """Write `v` as a little-endian base-128 varint; return the new position."""
    while v >= 128:
        out[pos] = np.uint8((v & 127) | 128)
        v >>= 7
        pos += 1
    out[pos] = np.uint8(v)
    return pos + 1


@kernel
def put_padded4(out, pos, v):
    """Write `v` (below 2**28) as a four-byte padded varint; return the end."""
    out[pos] = np.uint8((v & 127) | 128)
    out[pos + 1] = np.uint8(((v >> 7) & 127) | 128)
    out[pos + 2] = np.uint8(((v >> 14) & 127) | 128)
    out[pos + 3] = np.uint8((v >> 21) & 127)
    return pos + 4


@kernel
def get_varint(buf, pos):
    """Read a varint at `pos`; return `(value, position after it)`."""
    v = np.int64(0)
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        v |= np.int64(b & 127) << shift
        if b < 128:
            break
        shift += 7
    return v, pos


@kernel
def put_pair(out, pos, n, ids, fr, base, size, tmp8):
    """Write one pair body from the entries `ids/fr[base:base + n]`.

    Args:
        out: The output byte buffer.
        pos: Where to write.
        n: Alleles in the pair (at least 1).
        ids: Allele ids of the frame.
        fr: Frequencies of the frame.
        base: Index of the pair's first entry.
        size: The deme's gene copies, or `0` when unknown.
        tmp8: One-element float64 scratch for the raw form.

    Returns:
        The position after the pair body.
    """
    if n == 1 and fr[base] == 1.0:
        pos = put_varint(out, pos, 1)
        return put_varint(out, pos, ids[base])
    esc = 1
    if size > 0:
        fsize = float(size)
        esc = 0
        for k in range(n):
            f = fr[base + k]
            c = np.int64(f * fsize + 0.5)
            if c < 1 or float(c) / fsize != f:
                esc = 1
                break
    pos = put_varint(out, pos, 2 * n + esc)
    for k in range(n):
        pos = put_varint(out, pos, ids[base + k])
        f = fr[base + k]
        if esc == 0:
            pos = put_varint(out, pos, np.int64(f * float(size) + 0.5))
        else:
            tmp8[0] = f
            raw = tmp8.view(np.uint8)
            for j in range(8):
                out[pos + j] = raw[j]
            pos += 8
    return pos


@kernel
def enc_full(out, pos, gen_delta, nal, ids, fr, demes, loci, sizes, tmp8):
    """Append one full-frame record built from a CSR frame.

    Args:
        out: The block buffer.
        pos: Where the record starts.
        gen_delta: Generations since the previous record (0 for a block's
            first).
        nal: `int32[pairs]` alleles per pair.
        ids: Allele ids, pair 0 first.
        fr: Frequencies, same order.
        demes: Number of demes.
        loci: Number of loci.
        sizes: `int64[demes]` gene copies (`0` unknown).
        tmp8: One-element float64 scratch.

    Returns:
        `(new position, rows)`.
    """
    start = pos
    pos += 2 * PAD4 + 1
    out[start + 2 * PAD4] = KIND_FULL
    pos = put_varint(out, pos, gen_delta)
    e = 0
    p = 0
    for d in range(demes):
        size = sizes[d]
        for _l in range(loci):
            n = nal[p]
            if n > 0:
                pos = put_pair(out, pos, n, ids, fr, e, size, tmp8)
                e += n
            else:
                pos = put_varint(out, pos, 0)
            p += 1
    put_padded4(out, start, pos - start - PAD4)
    put_padded4(out, start + PAD4, e)
    return pos, e


@kernel
def enc_delta(out, pos, gen_delta, nal, ids, fr, demes, loci, sizes, pn, pid, pf, tmp8):
    """Append a delta record against `pn/pid/pf` and update that state.

    Only pairs whose alleles or frequency bits differ from the previous
    frame are written. Every `nal[p]` must be at most `pid.shape[1]`
    (the caller grows the state first).

    Args:
        out: The block buffer.
        pos: Where the record starts.
        gen_delta: Generations since the previous record.
        nal: `int32[pairs]` alleles per pair.
        ids: Allele ids, pair 0 first.
        fr: Frequencies, same order.
        demes: Number of demes.
        loci: Number of loci.
        sizes: `int64[demes]` gene copies (`0` unknown).
        pn: `int32[pairs]` previous alleles per pair, updated.
        pid: `int64[pairs, width]` previous ids, updated.
        pf: `float64[pairs, width]` previous frequencies, updated.
        tmp8: One-element float64 scratch.

    Returns:
        `(new position, rows, pairs changed)`.
    """
    start = pos
    pos += 2 * PAD4 + 1
    out[start + 2 * PAD4] = KIND_DELTA
    pos = put_varint(out, pos, gen_delta)
    count_pos = pos
    pos += _COUNT_PAD
    e = 0
    p = 0
    last = -1
    changed = 0
    for d in range(demes):
        size = sizes[d]
        for _l in range(loci):
            n = nal[p]
            diff = n != pn[p]
            if not diff:
                for k in range(n):
                    if ids[e + k] != pid[p, k] or fr[e + k] != pf[p, k]:
                        diff = True
                        break
            if diff:
                pos = put_varint(out, pos, p - last)
                last = p
                if n > 0:
                    pos = put_pair(out, pos, n, ids, fr, e, size, tmp8)
                else:
                    pos = put_varint(out, pos, 0)
                pn[p] = n
                for k in range(n):
                    pid[p, k] = ids[e + k]
                    pf[p, k] = fr[e + k]
                changed += 1
            e += n
            p += 1
    out[count_pos] = np.uint8((changed & 127) | 128)
    out[count_pos + 1] = np.uint8(((changed >> 7) & 127) | 128)
    out[count_pos + 2] = np.uint8((changed >> 14) & 127)
    put_padded4(out, start, pos - start - PAD4)
    put_padded4(out, start + PAD4, e)
    return pos, e, changed


@kernel
def load_state_from_csr(nal, ids, fr, pn, pid, pf):
    """Set an encoder's previous-frame state from a CSR frame."""
    e = 0
    for p in range(nal.shape[0]):
        n = nal[p]
        pn[p] = n
        for k in range(n):
            pid[p, k] = ids[e + k]
            pf[p, k] = fr[e + k]
        e += n


@kernel
def get_pair(buf, pos, size, p, pn, pid, pc, pf):
    """Decode one pair body into the state arrays.

    Returns:
        The position after the body, or `-1` when the pair has more
        alleles than the state is wide (the caller widens and retries).
    """
    h, pos = get_varint(buf, pos)
    if h == 0:
        pn[p] = 0
        return pos
    if h == 1:
        pn[p] = 1
        pid[p, 0], pos = get_varint(buf, pos)
        pc[p, 0] = size
        pf[p, 0] = 1.0
        return pos
    n = h >> 1
    esc = h & 1
    if n > pid.shape[1]:
        return -1
    pn[p] = n
    fsize = float(size)
    for k in range(n):
        pid[p, k], pos = get_varint(buf, pos)
        if esc == 0:
            c, pos = get_varint(buf, pos)
            pc[p, k] = c
            pf[p, k] = float(c) / fsize
        else:
            v = np.uint64(0)
            for j in range(8):
                v |= np.uint64(buf[pos + j]) << np.uint64(8 * j)
            pos += 8
            pc[p, k] = -1
            pf[p, k] = np.array([v], dtype=np.uint64).view(np.float64)[0]
    return pos


@kernel
def apply_record(buf, pos, demes, loci, sizes, pn, pid, pc, pf):
    """Apply the record at `pos` to the decoded state.

    Returns:
        `(end position, rows, kind, gen_delta)`. An end position of `-1`
        means the state is too narrow: widen it and call again.
    """
    blen, p0 = get_varint(buf, pos)
    end = p0 + blen
    rows, p1 = get_varint(buf, p0)
    kind = buf[p1]
    gen_delta, cur = get_varint(buf, p1 + 1)
    if kind == KIND_FULL:
        p = 0
        for d in range(demes):
            size = sizes[d]
            for _l in range(loci):
                cur = get_pair(buf, cur, size, p, pn, pid, pc, pf)
                if cur < 0:
                    return np.int64(-1), rows, kind, gen_delta
                p += 1
    else:
        nch, cur = get_varint(buf, cur)
        p = -1
        for _ in range(nch):
            dp, cur = get_varint(buf, cur)
            p += dp
            size = sizes[p // loci]
            cur = get_pair(buf, cur, size, p, pn, pid, pc, pf)
            if cur < 0:
                return np.int64(-1), rows, kind, gen_delta
    return end, rows, kind, gen_delta


@kernel
def state_to_csr(pn, pid, pf, nal, out_ids, out_fr):
    """Flatten the decoded per-pair state into a CSR frame.

    Returns:
        The number of entries written.
    """
    e = 0
    for p in range(pn.shape[0]):
        n = pn[p]
        nal[p] = n
        for k in range(n):
            out_ids[e + k] = pid[p, k]
            out_fr[e + k] = pf[p, k]
        e += n
    return e


class DecodedState:
    """The per-pair state a decoder keeps, and what a delta applies to.

    Args:
        pairs: Number of (deme, locus) pairs.
        width: Initial number of alleles each pair can hold; it doubles on
            demand.
    """

    def __init__(self, pairs: int, width: int = 16) -> None:
        """Allocate the arrays."""
        self.pairs = pairs
        self.width = width
        self.pn = np.zeros(pairs, np.int32)
        self.pid = np.zeros((pairs, width), np.int64)
        self.pc = np.zeros((pairs, width), np.int64)
        self.pf = np.zeros((pairs, width), np.float64)

    def grow(self) -> None:
        """Double the width, keeping every value."""
        width = self.width * 2
        for name in ("pid", "pc", "pf"):
            old = getattr(self, name)
            new = np.zeros((self.pairs, width), old.dtype)
            new[:, : self.width] = old
            setattr(self, name, new)
        self.width = width

    def frame_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return the state as CSR arrays `(counts, allele_ids, frequencies)`."""
        counts = np.zeros(self.pairs, np.int32)
        total = int(self.pn.sum())
        ids = np.zeros(total, np.int64)
        frequencies = np.zeros(total, np.float64)
        state_to_csr(self.pn, self.pid, self.pf, counts, ids, frequencies)
        return counts, ids, frequencies
