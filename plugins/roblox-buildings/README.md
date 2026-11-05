# roblox-buildings

Two model add-ons for buildings and props made for Roblox, driven by a JSON file beside the model. The server tells the
page which models have one (`item.ext["roblox-buildings"]`), so a plain model never makes the page look for a file that
is not there.

```json
{"plugins": {"roblox-buildings": {"openingProps": "assets/props/{dir}/{prop}.fbx"}}}
```

`openingProps` is the path template of the door and window models of a building's openings. `{dir}` is `doors` for a
prop whose name starts with `Door`, else `windows`, and `{prop}` is the opening's `prop` name. Without it the openings
are left out (the furniture still shows). The page reads it each time it shows a building, so a change applies to the
open building without a reload.

## Building: a report beside an FBX

`<stem>.fbx` with `<stem>.json` holding `objects` (a map) **and** `files` (a map). Add-on `building`:

- **Building look** (the default for such a model): one mesh per finish, each with the tiling maps of its `objects` entry
  and Roblox's `SurfaceAppearance.Color` tint. Glass objects draw as glass; a mesh the report does not know draws as clay.
- **Props** (`I`): the report's door, window and furniture props, placed in the building's frame. A prop that is not
  made yet (`exists` false) is a box of its `size`, coloured by the `wish-box` token.
- **Storey cut** (`C`, or the Whole / Storey buttons): a clip plane 0.6 studs under the chosen storey's ceiling, to look
  in from above.
- The meta line reads the report: triangles, meshes, rooms, openings, props, size, warnings.

The FBX of a Blockbench export is turned 180 degrees about Y, so the model is turned back (front at -Z) and props are
placed at `(-x, y, -z)` of the report's positions.

The report shape (keys not listed are ignored):

```json
{
  "tris": 4323, "size": [82.0, 50.4, 54.0],
  "objects": {"plaster_white": {"finish": "plaster_white", "tint": "e2ded4", "glass": false,
              "maps": {"Color": "assets/buildings/_materials/plaster_Color.jpg", "Normal": "...", "Roughness": "...", "Metalness": "..."}}},
  "files": {"fbx": "assets/buildings/town/house_01.fbx"},
  "storeys": [{"index": 0, "name": "g", "y": 1.0, "height": 15.0, "clear": 13.5}],
  "rooms": [{"name": "kitchen"}],
  "openings": [{"prop": "Door_Wood", "pos": [x, y, z], "yaw": 90}],
  "props": [{"prop": "Bed", "pos": [x, y, z], "yaw": 270, "size": [w, h, d], "exists": true, "path": "assets/props/bedroom/Bed.fbx"}],
  "warnings": []
}
```

`objects` keys are the mesh names of the FBX; `maps.Color` is required, and `tint` is a hex colour without `#`. Map paths
and prop paths are project paths. A prop's textures come from the catalog (its `Stem_<Map>.png` maps beside it).

## Prop: a sidecar beside a model

`<stem>.json` with a non-empty `objects` map and no `files`. Add-on `prop-sidecar`: the model is turned back like
Studio's importer does (front at -Z), and the parts that move ease open and back with `Open` (`O`, 0.4 s):

```json
{"objects": {
  "Body":   {"tris": 88, "glass": false},
  "Door":   {"hinge": [-0.878, 1.08, -0.403], "axis": "y", "swing": 100, "grip": [0.824, 1.08, -0.522]},
  "Drawer": {"slide": [0.0, 0.0, -0.612]}}}
```

`slide` is the offset the part moves (studs). `hinge` is a point on the axis, and `axis` (`x`, `y` or `z`) and `swing`
(degrees) give the rotation. All vectors are in the prop's own axes, which the add-on flips with the FBX. A prop with no
moving part has no Open button but is still turned. The catalog item's `ext["roblox-buildings"].sidecar.movers` counts
the parts that move.

## Catalog data

`ext["roblox-buildings"] = {"report": {path, mtime}}` for a building, `{"sidecar": {path, mtime, movers}}` for a prop. A
JSON that is neither, a report beside a model that is not an FBX, and a model with no `<stem>.json` get nothing.

A JSON file over 8 MiB is not read; it gets a catalog warning. A parsed file is cached until it changes, and the cache
forgets files that a scan no longer meets.

## For other plugins

Prop models are loaded once and cloned per place. Clones share geometry and materials, and a least-recently-used cache
frees the sources (never a model's disposal), so one building's teardown cannot break the next one's props.
