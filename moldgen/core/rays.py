"""Vectorized vertical ray casting against triangle meshes.

Every visibility question in a vertical-pull mold is a ray along ±Z: does
the mold half slide off this face without passing through the part, is
this point inside a solid, which part surface lies under the ejector pin.
Vertical rays reduce to 2-D point-in-triangle tests on the XY projection,
so instead of a general BVH (trimesh's needs rtree/embree, manifold3d's
ray_cast is one ray per Python call) this bins triangles into a uniform XY
grid and tests every ray against the triangles of its cell in bulk numpy.
"""

from __future__ import annotations

import numpy as np

# rays x candidate-triangle pairs evaluated per numpy batch (memory bound)
_PAIR_BATCH = 4_000_000
# barycentric slack so rays through shared edges/vertices never slip
# between neighbouring triangles
_BARY_TOL = 1e-9
CELLS_PER_TRIANGLE = 1.0


class VerticalRayCaster:
    """Cast many rays along +Z or −Z against a fixed set of triangles.

    Triangles whose XY projection is degenerate (vertical walls) are
    ignored: a vertical ray can only graze them, never pass through.
    """

    def __init__(self, triangles: np.ndarray):
        tri = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        e0 = b[:, :2] - a[:, :2]
        e1 = c[:, :2] - a[:, :2]
        denom = e0[:, 0] * e1[:, 1] - e1[:, 0] * e0[:, 1]
        scale = np.maximum(np.abs(e0).max(axis=1), np.abs(e1).max(axis=1))
        keep = np.abs(denom) > 1e-12 * np.maximum(scale * scale, 1e-12)
        # flat per-component arrays: gathers on 1-D arrays are much cheaper
        # than on (n, 2) rows in the hot loop
        inv = 1.0 / denom[keep]
        self._ax, self._ay, self._az = (np.ascontiguousarray(a[keep, i]) for i in range(3))
        self._e0x = e0[keep, 0] * inv
        self._e0y = e0[keep, 1] * inv
        self._e1x = e1[keep, 0] * inv
        self._e1y = e1[keep, 1] * inv
        self._dz0 = b[keep, 2] - a[keep, 2]
        self._dz1 = c[keep, 2] - a[keep, 2]
        self.n = int(keep.sum())
        if self.n == 0:
            return

        xy = tri[keep][:, :, :2]
        lo = xy.min(axis=1)
        hi = xy.max(axis=1)
        self._origin = lo.min(axis=0)
        span = np.maximum(hi.max(axis=0) - self._origin, 1e-9)
        # ~1 cell per triangle: a balance between build cost (entries per
        # triangle) and candidate-list length where the projection piles up
        # thin slivers (silhouettes of curved parts)
        cell = float(np.sqrt(span[0] * span[1] / (CELLS_PER_TRIANGLE * self.n)))
        cell = max(cell, float(span.max()) / 2048.0, 1e-9)
        self._cell = cell
        self._nx = int(span[0] // cell) + 1
        self._ny = int(span[1] // cell) + 1

        i0 = ((lo[:, 0] - self._origin[0]) // cell).astype(np.int64)
        j0 = ((lo[:, 1] - self._origin[1]) // cell).astype(np.int64)
        i1 = np.minimum(((hi[:, 0] - self._origin[0]) // cell).astype(np.int64), self._nx - 1)
        j1 = np.minimum(((hi[:, 1] - self._origin[1]) // cell).astype(np.int64), self._ny - 1)
        wi = i1 - i0 + 1
        wj = j1 - j0 + 1
        counts = wi * wj
        tri_idx = np.repeat(np.arange(self.n), counts)
        local = np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts)
        ii = i0[tri_idx] + local % wi[tri_idx]
        jj = j0[tri_idx] + local // wi[tri_idx]
        cell_id = ii * self._ny + jj
        # order within a cell is irrelevant, so the unstable sort is fine
        order = np.argsort(cell_id)
        self._entries = tri_idx[order]
        per_cell = np.bincount(cell_id, minlength=self._nx * self._ny)
        self._cell_start = np.concatenate(([0], np.cumsum(per_cell)))

    @classmethod
    def from_mesh(cls, mesh) -> "VerticalRayCaster":
        return cls(mesh.triangles)

    def first_hit(
        self, origins: np.ndarray, direction: float, min_dist: float = 0.0
    ) -> np.ndarray:
        """Distance to the first triangle hit along (0, 0, direction) from
        each origin, ignoring hits closer than `min_dist`; inf on a miss."""
        return self._cast(origins, direction, min_dist, count=False)

    def crossings(self, origins: np.ndarray, direction: float) -> np.ndarray:
        """Number of triangles crossed along the ray (parity → inside test
        for closed meshes)."""
        return self._cast(origins, direction, 0.0, count=True)

    def contains(self, points: np.ndarray) -> np.ndarray:
        """Inside test for a closed mesh by +Z ray-crossing parity.

        Rays through a shared edge may count both neighbours; casting in
        both directions and requiring both to agree keeps that from flipping
        the answer in practice.
        """
        up = self.crossings(points, 1.0) % 2 == 1
        down = self.crossings(points, -1.0) % 2 == 1
        return up & down

    def _cast(self, origins, direction, min_dist, count):
        origins = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
        n_rays = len(origins)
        out = np.zeros(n_rays, dtype=np.int64) if count else np.full(n_rays, np.inf)
        if self.n == 0 or n_rays == 0:
            return out
        sign = 1.0 if direction > 0 else -1.0
        ox, oy, oz = (np.ascontiguousarray(origins[:, i]) for i in range(3))

        ci = np.floor((origins[:, 0] - self._origin[0]) / self._cell).astype(np.int64)
        cj = np.floor((origins[:, 1] - self._origin[1]) / self._cell).astype(np.int64)
        inside = (ci >= 0) & (ci < self._nx) & (cj >= 0) & (cj < self._ny)
        ray_ids = np.flatnonzero(inside)
        if len(ray_ids) == 0:
            return out
        cells = ci[ray_ids] * self._ny + cj[ray_ids]
        starts = self._cell_start[cells]
        n_cand = self._cell_start[cells + 1] - starts

        # batch rays so the expanded (ray, triangle) pair list stays bounded
        cum = np.cumsum(n_cand)
        lo = 0
        while lo < len(ray_ids):
            done = int(cum[lo - 1]) if lo else 0
            hi = max(int(np.searchsorted(cum, done + _PAIR_BATCH, side="right")), lo + 1)
            k = n_cand[lo:hi]
            total = int(k.sum())
            if total == 0:
                lo = hi
                continue
            r_local = np.repeat(np.arange(lo, hi), k)
            offs = np.arange(total) - np.repeat(np.cumsum(k) - k, k)
            t_idx = self._entries[starts[r_local] + offs]
            rays = ray_ids[r_local]

            vx = ox[rays] - self._ax[t_idx]
            vy = oy[rays] - self._ay[t_idx]
            u = vx * self._e1y[t_idx] - self._e1x[t_idx] * vy
            v = self._e0x[t_idx] * vy - vx * self._e0y[t_idx]
            hit = (u >= -_BARY_TOL) & (v >= -_BARY_TOL) & (u + v <= 1.0 + _BARY_TOL)
            rays, t_idx, u, v = rays[hit], t_idx[hit], u[hit], v[hit]
            z = self._az[t_idx] + u * self._dz0[t_idx] + v * self._dz1[t_idx]
            dist = (z - oz[rays]) * sign
            ok = dist > min_dist
            if count:
                np.add.at(out, rays[ok], 1)
            else:
                np.minimum.at(out, rays[ok], dist[ok])
            lo = hi
        return out
