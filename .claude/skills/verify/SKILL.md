# Verifying moldgen

## Launch

The dev venv lives at `~/.venvs/moldgen` (WSL home, not in-repo — the repo is
on a slow /mnt/c Windows mount). `make` is not installed in this environment.

```sh
~/.venvs/moldgen/bin/python -m pip install -e ".[dev]"   # once / after dep changes
~/.venvs/moldgen/bin/python -m uvicorn moldgen.main:app --port 8765 &
curl -s http://127.0.0.1:8765/api/health                # {"status":"ok",...}
```

Use a non-default port (8765) — the user may have `make dev` running on 8000.

## Drive the API flow

1. Make a test part with undercuts (mushroom = stem + wider cap) using
   `trimesh.creation.cylinder` + `trimesh.boolean.union(engine="manifold")`,
   export STL to the scratchpad.
2. `curl -F "file=@mushroom.stl" /api/upload` → mesh_id, stats,
   `suggested_parting_z_mm` (should land in the cap band).
3. `POST /api/analyze {mesh_id, parting_z_mm}` — at a height through the stem
   the cap underside must flag (top half not demoldable); at the suggested
   height, zero undercuts.
4. `POST /api/generate` with params → halves with URLs; reload the downloaded
   STLs in trimesh and assert `is_watertight` and sane z-ranges (bottom half
   extends `pin_height` above the parting plane when pins are on).
5. Engine failures (bad parting height, non-watertight input) return
   HTTP 200 with `{"ok": false, "errors": [...]}` — not 4xx/5xx.

## Frontend

Static files served by the same server at `/`. `node --input-type=module
--check < moldgen/frontend/main.js` catches syntax errors. For a real check, drive
it headless: node Playwright lives at `/opt/node-tools/node_modules/playwright`
and Chromium at `/opt/pw-browsers/chromium-*/chrome-linux/chrome` (launch
with `--use-gl=swiftshader --enable-unsafe-swiftshader` for WebGL). The
jsdelivr CDN may be blocked by the network policy: `npm pack three@0.166.1`
into the scratchpad and `page.route('https://cdn.jsdelivr.net/npm/three@0.166.1/**')`
to serve those files instead. `page.setInputFiles('#part-upload', stl)`,
click `#analyze` / `#generate`, read `#status-text`, take screenshots.

## Gotchas

- Uploads write real dirs into `./workdir/` — harmless (gitignored), but
  clean up test uploads if you make many.
- `pkill -f uvicorn` can kill your own shell's process group (exit 144) —
  start the server with `run_in_background` instead and let it die with the
  session, or target the PID.
- STEP/IGES support (`pip install cadquery` in the venv) needs system libGL,
  which isn't apt-installed here and sudo needs a password. Workaround that
  works: `apt-get download libgl1 libglvnd0 libglx0` (no sudo needed),
  `dpkg-deb -x` them into `~/.local/opt/gl`, then run server/tests with
  `LD_LIBRARY_PATH=$HOME/.local/opt/gl/usr/lib/x86_64-linux-gnu`. Without it
  cadquery import fails and moldgen correctly degrades to mesh-only.

## Beyond the happy path

- CLI: `moldgen generate part.stl --out DIR` / `moldgen analyze part.stl`
  (exit 1 = locks / can't build). Options mirror MoldParams.
- After any geometry change, sweep shapes (sphere, torus, capsule, cone,
  cup, upside-down cup, L-part, plate) x modes x 3+ parting heights and
  assert every half has no duplicate vertex positions and is watertight
  after an STL round trip — see "Export integrity" in CLAUDE.md.
- Large meshes: a 1M-triangle torus (`trimesh.creation.torus(40, 12,
  major_sections=800, minor_sections=600)`) should upload/analyze in tens
  of seconds, not minutes.
