"""Human-readable setup sheet for a generated mold (shared by the web app's
ZIP bundle and the CLI)."""

from __future__ import annotations


def setup_sheet(record: dict) -> str:
    """Human-readable build notes for the generated mold."""
    p = record["params"]
    meta = record["metadata"]
    hw = meta.get("hardware", {})
    lines = [
        f"moldgen {record.get('version', '')} — mold setup sheet",
        "=" * 48,
        "",
        f"Mode:           {p['mode']}",
        f"Parting plane:  z = {meta['parting_z_mm']:.3f} mm (part frame)",
        f"Wall margin:    {p['wall_margin_mm']:g} mm   base: {p['base_thickness_mm']:g} mm",
    ]
    if p.get("shrink_comp_pct"):
        lines.append(f"Shrink comp.:   +{p['shrink_comp_pct']:g} % cavity scale")
    if p.get("cavity_offset_mm"):
        lines.append(f"Cavity offset:  {p['cavity_offset_mm']:g} mm")
    lines += ["", "Files:"]
    for name, h in meta["halves"].items():
        lines.append(
            f"  moldgen-{name}.stl  {h['volume'] / 1000:.0f} cm³, {h['triangles']:,} triangles"
        )
    forged = meta.get("forged")
    if forged:
        ch = forged.get("charge", {})
        lines += [
            "",
            "Compression (forged carbon):",
            f"  plunge {forged['plunge_mm']:g} mm, pinch gap "
            f"{forged['pocket_clearance_mm']:g} mm/side, wall draft {forged['draft_deg']:g}°",
        ]
        if ch:
            lines += [
                f"  part volume {ch['part_volume_cm3']:.1f} cm³ — forged part "
                f"≈ {ch['est_part_mass_g']:.0f} g at 1.5 g/cm³; weigh the charge "
                "to that plus a few % for flash",
                f"  loading volume {ch['loading_volume_cm3']:.1f} cm³ "
                f"({ch['loading_ratio']:.1f}x the part)",
            ]
        cores = forged.get("cores", {})
        if cores.get("to_male") or cores.get("to_female"):
            lines.append("  shell part: internal core(s) built onto the opposite half")
    if "pins" in hw:
        lines.append(
            f"  registration pins: 4x Ø{2 * hw['pins']['radius']:.1f} mm, "
            f"{hw['pins']['clearance']:g} mm clearance"
        )
    if "bolts" in hw:
        lines.append(
            f"  clamping bolts: {len(hw['bolts']['centers'])}x "
            f"M{hw['bolts']['bolt_diameter']:g} through both halves"
        )
    if "jack_screws" in hw:
        lines.append(
            f"  jack screws: {len(hw['jack_screws']['centers'])}x "
            f"M{hw['jack_screws']['bolt_diameter']:g} (tap-size holes in the male)"
        )
    if "ejector" in hw:
        lines.append("  ejector hole: plug with wax/tape before loading")
    pre = meta.get("prepreg")
    if pre:
        lines += [
            "",
            "Laminating tool:",
            f"  trim allowance {pre['trim_allowance_mm']:g} mm with a "
            f"{pre['trim_kick_deg']:g}° kick — trim to the witness line",
        ]
        lines += [f"  - {n}" for n in pre.get("notes", [])]
    if record.get("warnings"):
        lines += ["", "Warnings:"] + [f"  - {w}" for w in record["warnings"]]
    lines += [
        "",
        "General: seal printed tools, apply release agent (several coats of",
        "wax or a semi-permanent release) and do a test closure before the",
        "first real charge.",
        "",
    ]
    return "\n".join(lines)
