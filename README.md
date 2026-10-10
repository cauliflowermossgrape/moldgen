<p align="center">
  <img src="frontend/logo.svg" width="88" alt="moldgen logo" />
</p>

<h1 align="center">moldgen</h1>

<p align="center">
  Turn any 3D model into a ready-to-print (or CNC) mold for carbon fiber work.<br/>
  Runs locally in your browser — upload a part, drag the parting plane, download the mold halves.
  <img width="1889" height="897" alt="image" src="https://github.com/user-attachments/assets/4f718428-f024-4545-b020-e4fd2ca36f3c" />

</p>

---

- [What it does](#what-it-does)
- [Setup on Windows](#setup-on-windows)
- [Setup on Linux / macOS](#setup-on-linux--macos)
- [Using the app](#using-the-app)
- [Optional: STEP / IGES input](#optional-step--iges-input)
- [Troubleshooting](#troubleshooting)
- [For developers](#for-developers)

## What it does

Upload a part (STL / 3MF / OBJ), pick a mold style, and moldgen generates watertight, printable mold geometry — with the mold-making details already engineered in, following [Easy Composites' published guidance](https://www.easycomposites.co.uk/learning/forged-carbon-mould-design-and-optimisation). Every setting in the app has a hover tooltip explaining what it does and where the default number comes from.

Whichever way you spell it — carbon fiber or carbon fibre, mold or mould — this covers the three tool types home and small-shop composite work actually uses: forged carbon compression moulds, prepreg/vacuum-bagging laminating tools, and open layup tools.

| Mold style | What you get |
|---|---|
| **Compression / forged carbon** | Two-part telescoping mold — the male plug pistons into the female pocket so chopped fiber can't jam the seam. Registration pins, clamp bolt holes, pry slots, jack screws, resin overflow channels, optional ejector hole. |
| **Prepreg / laminating tool** | Single-sided female tool with a trim-allowance run-out (witness line to trim to), bagging flange, sealant-tape guide line, optional split-mould bush holes. |
| **Open layup** | Basic single-sided female tool with a flat bagging flange. |

Before you commit plastic, the built-in **demolding check** highlights faces that will lock (red), faces whose pull path is blocked by other geometry (purple — tilted bores, overhangs), and walls that will drag (amber). The parting height is auto-suggested for the mold style you pick, with an undercut-vs-height chart under the slider. Printer presets (resin / FDM / CNC) set all fit clearances in one click.

**Shell parts work.** Cups, cases, bowls and panels are handled properly: on a compression mold the inside of the shell is molded by a core built onto the male (or onto the female, for an upside-down shell); on a laminating tool the inside is left open as the bagged side. No loose cores trapped in the cavity.

Also built in: a section view to inspect the closed mold, a forged-carbon charge-weight estimate and loading-volume check, shrinkage compensation, exact cavity offsets, and a one-click ZIP with every half plus a printable setup sheet.

## Setup on Windows

You need [Python 3.11 or newer](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"** in the installer. Then in *PowerShell* or *Command Prompt*:

```bat
git clone https://github.com/cauliflowermossgrape/moldgen.git
cd moldgen

py -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m uvicorn moldgen.main:app
```

Open **http://localhost:8000** in your browser. That's it — next time, only the last command is needed.

## Setup on Linux / macOS

You need Python 3.11+ (`python3 --version` to check). Then:

```sh
git clone https://github.com/cauliflowermossgrape/moldgen.git
cd moldgen

python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m uvicorn moldgen.main:app
```

Open **http://localhost:8000**. (If you have `make`: `make dev` does all of the above, `make test` runs the test suite.)

## Using the app

1. **Pick your process** at the top — resin print, FDM print, or CNC. This sets all the fit clearances.
2. **Drop your model** into the viewport (STL, 3MF or OBJ, in millimeters). You'll see dimensions, a watertightness check, and a warning if the model looks like it was exported in meters.
3. **Set the parting plane** with the slider (it starts at the auto-suggested height) and click **Analyze undercuts** — red faces will lock in the mold, amber faces will drag. Adjust until it's clean.
4. **Pick a mold style** and tweak settings — hover anything to see what it means.
5. **Generate mold** — inspect the halves with the explode slider, ghost-part toggle and section view; read any warnings (they tell you what was shrunk, moved or skipped, and anything about your part that will fight demolding). Compression molds also show the charge weight to load.
6. **Download** the STL per half — or the ZIP with all halves and a setup sheet — and slice it like any other print.

Uploaded parts and generated molds are stored in the local `workdir/` folder — nothing leaves your machine.

## Optional: STEP / IGES input

To upload CAD files directly (STEP/IGES instead of meshes), install the `step` extra — it pulls OpenCascade, whose wheels are several hundred MB:

```sh
# Windows:   .venv\Scripts\pip install -e ".[step]"
# Linux/mac: .venv/bin/pip install -e ".[step]"
```

Surfaces are tessellated on upload with adjustable precision; IGES files are sewn into closed shells automatically. The app detects support at startup and enables the formats. On minimal Linux installs you may also need `sudo apt install libgl1`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` / `py` not found | Install Python 3.11+ and re-open the terminal; on Windows make sure "Add to PATH" was ticked. |
| Page loads but the 3D view is blank | The viewer loads three.js from a CDN — the first page load needs internet access. |
| Port 8000 already in use | Add `--port 8080` to the uvicorn command. |
| STEP upload says "not installed" | Install the `step` extra (see above); on Linux also `sudo apt install libgl1`. |
| "mesh is not watertight" error | Your model has holes/open edges. moldgen attempts repair automatically; if it still fails, fix the export in your CAD tool (export as a solid, not surfaces). |
| Mold seems too big/thick | *Wall margin* is horizontal (flange room for pins/bolts); *Base thickness* is the vertical shell — lower it for thin parts. |
| Part is tiny (a few mm) | It was probably exported in inches or meters — rescale by 25.4 or 1000 in your CAD tool. |
| "loading volume" warning | Loose chopped fibre bulks to 2–3× its pressed volume; raise *Plunge depth* so the charge fits before the plug engages. |

## For developers

```sh
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest        # ~85 tests, a few seconds
```

```
moldgen/
  core/            pure-geometry engine (no web imports)
    engine.py        generate_mold(mesh, params) -> MoldResult
    forged.py        telescoping compression construction
    prepreg.py       laminating-tool construction
    hardware.py      pins, bolts, pry slots, jack screws, overflow, scribes
    undercuts.py     demold (normals + occlusion + shell cores) & drape analysis,
                     parting-plane suggestion and profile
    rays.py          vectorized vertical ray casting (visibility, inside tests)
    offset.py        plane-exact cavity offset
    step_io.py       optional STEP/IGES tessellation (cadquery/OCP)
  main.py          FastAPI app: JSON API + static frontend
frontend/          vanilla JS + three.js viewport (no build step)
tests/             pytest — geometry verified by exact volume arithmetic
```

All booleans run through [manifold3d](https://github.com/elalish/manifold) (watertight-guaranteed); trimesh handles I/O and analysis. The frontend talks to a small JSON API (`POST /api/upload`, `/api/orient`, `/api/analyze`, `/api/generate`, `GET /api/download/{id}/{half}.stl` and `/api/download/{id}/mold.zip`) — script it directly if you prefer. Units are millimeters everywhere.

Known limitations: the parting surface is a flat plane (re-orient the part with *Lay on face* to choose it), the occlusion check samples one ray per triangle (dense meshes make this a non-issue), and fillets can't be added to your part automatically — the drape check tells you where they're needed.

## License

[MIT](LICENSE)
