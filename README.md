# Gerber to Layer, STL, and PNG

This offline tool reads a practical subset of RS-274X Gerber and converts its geometry to `pwszImg`, binary STL, and PNG. The primary script in this project is `gerber_to_layer_v2.py`.

## Requirements and installation

Python 3.8 or newer, Pillow, and Shapely are required:

```powershell
python -m pip install Pillow Shapely
```

Shapely is required for `--invert` and STL generation. All processing and files remain local.

## Basic usage

```powershell
python gerber_to_layer_v2.py Soldermask_Top.gbr --verify
```

Outputs are always written to an `output` folder in the same directory as `gerber_to_layer_v2.py`. This location does not depend on the Gerber file's location or the current working directory. The folder is created automatically if needed. Output filenames use the input file's stem, for example: `Soldermask_Top.pwszImg`, `Soldermask_Top.stl`, and `Soldermask_Top.png`. During interactive execution, when `__file__` is unavailable, `output` is located relative to the current working directory.

## Geometric inversion

With `--invert`, the program parses `Keep-out.gbr` as the frame and the input Gerber as the area to subtract. If the frame is not a filled `G36/G37` region and consists only of `D01` strokes or closed lines/arcs, its points are converted to a closed polygon using a convex hull. For rectangular frames, this is equivalent to a filled mask covering the entire board. The operation is:

```text
output geometry = frame.difference(input geometry)
```

The resulting geometry is used for all three outputs: `pwszImg`, STL, and PNG. The output is the filled frame area minus the pads. The STL is built by triangulating that same Polygon and its holes, with closed walls; the PNG is saved at 300 DPI with dimensions of 3200×2240. This prevents a line-only frame from producing a thin output or a blank image. The default frame is `Keep-out.gbr` in the input file's directory. Use `--frame` to specify another frame:

```powershell
python gerber_to_layer_v2.py Soldermask_Top.gbr --invert --verify
python gerber_to_layer_v2.py Soldermask_Bot.gbr --invert --frame Keep-out.gbr --verify
```

If the default frame is missing, the program stops with a clear error. Using the input file itself as the frame in inversion mode is not allowed and is rejected. An empty result is an error, such as when the input covers the entire frame. For validation, the reported STL facet count must be greater than zero, and `--verify` must confirm the `pwszImg` record count and float32 coordinates.

## Inversion test example

From the directory containing the three Gerber files, run:

```powershell
python gerber_to_layer_v2.py Soldermask_Top.gbr --invert --frame Keep-out.gbr --verify
python gerber_to_layer_v2.py Soldermask_Bot.gbr --invert --frame Keep-out.gbr --verify
```

In this case, `Keep-out.gbr` is converted to a closed area and the layer pads are subtracted from it. The three outputs are written to `output` beside the script. The program reports the STL facet count and the number of `pwszImg` segments.

## CLI options

| Option | Description |
|---|---|
| `GERBER` | Path to the input Gerber file; required. |
| `--invert` | Compute the frame minus the input geometry before generating all outputs. |
| `--frame PATH` | Frame used with `--invert`; defaults to `Keep-out.gbr` beside the input file. |
| `--reference PATH` | Use the header/trailer from a reference `pwszImg` file. |
| `--thickness N` | STL thickness in mm; default: `0.1`. |
| `--line-width N` | Default stroke width; default: `0.1`. |
| `--verify` | Check the record count and float32 coordinates in `pwszImg`. |

For the complete help text:

```powershell
python gerber_to_layer_v2.py --help
```

## Limitations

This parser is not a complete interpreter for every aperture macro, complex polarity, or Gerber command. STL is based on the union of filled geometry and Shapely triangulation. Before production use, inspect the output in a viewer and check the mesh with a mesh-inspection tool. The PNG is for preview and should not be used as an independent dimensional reference.
