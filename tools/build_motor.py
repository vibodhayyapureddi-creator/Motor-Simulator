"""Generate web/assets/motor.glb - the detailed motor model.

Plan section 17 ("generate it, don't hunt for it"): a reproducible script
builds the asset, so there is no licensing or availability risk and the
model can be tweaked and re-exported at any time. Blender isn't installed
on this machine, so the geometry is built with trimesh instead of a
Blender headless script - same idea, pure Python.

Contract with the viewer (web/src/scene/motor3d.js MotorRig.useGlb):
  * axis along +X, origin at the housing centre, Y up
  * ONE node named "shaft" holds everything that spins (the loader finds
    it by /shaft|rotor/i and rotates that node; loads couple at x=1.32
    in its local frame, so the node transform must be identity)
  * meshes named housing/copper/shaft get the temperature / current /
    stall glow hooks attached
  * dimensions match the procedural fallback so camera, loads and sector
    ring line up

Run:  uv run --no-project --python 3.14 --with trimesh --with numpy \
          python tools/build_motor.py
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual import TextureVisuals

OUT = Path(__file__).resolve().parents[1] / "web" / "assets" / "motor.glb"

# ------------------------------------------------------------------ helpers

ROT_Z_TO_X = trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0])


def cyl_x(radius, length, x=0.0, y=0.0, z=0.0, sections=64):
    """Cylinder along +X centred at (x, y, z)."""
    m = trimesh.creation.cylinder(radius=radius, height=length, sections=sections)
    m.apply_transform(ROT_Z_TO_X)
    m.apply_translation([x, y, z])
    return m


def box(ex, ey, ez, x=0.0, y=0.0, z=0.0):
    m = trimesh.creation.box(extents=[ex, ey, ez])
    m.apply_translation([x, y, z])
    return m


def torus_x(major, minor, x=0.0, sections=64, minor_sections=24):
    m = trimesh.creation.torus(major_radius=major, minor_radius=minor,
                               major_sections=sections,
                               minor_sections=minor_sections)
    # torus lies in XY plane (axis Z); rotate so its axis is X
    m.apply_transform(ROT_Z_TO_X)
    m.apply_translation([x, 0, 0])
    return m


def merge(parts):
    return trimesh.util.concatenate(parts)


def pbr(name, rgb, metallic, roughness):
    return PBRMaterial(name=name,
                       baseColorFactor=[rgb[0], rgb[1], rgb[2], 1.0],
                       metallicFactor=metallic, roughnessFactor=roughness)


STEEL = pbr("steel", (0.604, 0.647, 0.694), 0.85, 0.38)
DARK = pbr("dark_steel", (0.255, 0.290, 0.329), 0.80, 0.50)
COPPER = pbr("copper", (0.722, 0.451, 0.200), 0.90, 0.35)
BRIGHT = pbr("bright_steel", (0.788, 0.824, 0.863), 0.95, 0.25)
RUBBER = pbr("rubber", (0.102, 0.114, 0.129), 0.00, 0.95)
BRASS = pbr("brass", (0.760, 0.620, 0.300), 0.90, 0.30)

# ------------------------------------------------------------------- meshes

def build_housing():
    parts = [cyl_x(0.44, 1.16)]
    # 18 cooling fins with a slight taper illusion (two stacked slabs)
    for i in range(18):
        a = (i / 18) * math.tau
        for r_off, thick in ((0.475, 0.022), (0.505, 0.014)):
            fin = box(1.10, 0.062, thick)
            fin.apply_translation([0, r_off, 0])
            fin.apply_transform(trimesh.transformations.rotation_matrix(a, [1, 0, 0]))
            parts.append(fin)
    # nameplate recess strip
    plate = box(0.34, 0.015, 0.22, 0.10, 0.45, 0.0)
    parts.append(plate)
    return merge(parts)


def build_bells():
    parts = []
    for x in (-0.645, 0.645):
        parts.append(cyl_x(0.465, 0.10, x))
        parts.append(cyl_x(0.445, 0.03, x + (0.065 if x > 0 else -0.065)))  # chamfer step
        # 8 hex bolt heads per bell
        for i in range(8):
            a = (i / 8) * math.tau + 0.2
            bolt = cyl_x(0.022, 0.03, x + (0.065 if x > 0 else -0.065),
                         math.sin(a) * 0.40, math.cos(a) * 0.40, sections=6)
            parts.append(bolt)
    # bearing boss + cap on the drive end
    parts.append(cyl_x(0.13, 0.10, 0.75))
    parts.append(cyl_x(0.085, 0.022, 0.805))
    # rear grille: radial slots on the back bell
    for i in range(6):
        a = (i / 6) * math.tau
        slot = box(0.02, 0.05, 0.30)
        slot.apply_translation([-0.712, 0, 0])
        slot.apply_transform(trimesh.transformations.rotation_matrix(a, [1, 0, 0]))
        parts.append(slot)
    # mounting feet with lightening steps
    for x in (-0.38, 0.38):
        parts.append(box(0.34, 0.12, 0.70, x, -0.56, 0))
        parts.append(box(0.40, 0.03, 0.76, x, -0.635, 0))
    # terminal box with a lid lip
    parts.append(box(0.34, 0.18, 0.30, -0.18, 0.53, 0))
    parts.append(box(0.36, 0.02, 0.32, -0.18, 0.63, 0))
    return merge(parts)


def build_copper():
    return merge([torus_x(0.33, 0.05, -0.585), torus_x(0.33, 0.05, 0.585)])


def build_cables():
    parts = []
    for dz in (-0.06, 0.06):
        c = trimesh.creation.cylinder(radius=0.022, height=0.5, sections=16)
        c.apply_translation([0, 0, 0])          # along Z -> rotate to Y
        c.apply_transform(trimesh.transformations.rotation_matrix(
            math.pi / 2, [1, 0, 0]))
        c.apply_translation([-0.18, 0.86, dz])
        parts.append(c)
    return merge(parts)


def build_terminals():
    parts = []
    for dz in (-0.06, 0.06):
        parts.append(cyl_x(0.016, 0.05, -0.18 + 0.0, 0.655, dz, sections=6))
    return merge(parts)


def build_shaft():
    """Everything that spins - exported as ONE mesh under the 'shaft' node."""
    parts = [
        cyl_x(0.048, 0.75, 1.06, sections=48),     # drive shaft
        cyl_x(0.030, 0.10, -0.70, sections=32),    # rear stub (fan end)
        cyl_x(0.100, 0.09, 0.92, sections=48),     # coupling hub
        cyl_x(0.070, 0.02, 0.885, sections=48),    # hub chamfer ring
        box(0.22, 0.020, 0.030, 1.28, 0.052, 0),   # keyway key
    ]
    return merge(parts)


def main() -> int:
    scene = trimesh.Scene()
    for name, mesh, material in [
        ("housing", build_housing(), STEEL),
        ("bells", build_bells(), DARK),
        ("copper_windings", build_copper(), COPPER),
        ("cables", build_cables(), RUBBER),
        ("terminals", build_terminals(), BRASS),
        ("shaft", build_shaft(), BRIGHT),
    ]:
        mesh.unmerge_vertices()   # flat shading: crisp machined look
        mesh.vertex_normals       # touch so the exporter writes NORMAL
        mesh.visual = TextureVisuals(material=material)
        scene.add_geometry(mesh, node_name=name, geom_name=name)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # without normals the PBR materials render black in three.js
    scene.export(OUT, include_normals=True)
    tris = sum(len(g.faces) for g in scene.geometry.values())
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KiB, {tris} triangles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
