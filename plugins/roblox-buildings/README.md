# roblox-buildings

Two model add-ons for buildings and props made for Roblox, driven by a JSON file beside the model. Keys: `I` props,
`T` states (buildings), `C` storey cut (buildings), `O` open (props). The server tells the
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
- **States** (`T`, shown when the building has any): the props and openings as the report poses them, or all shut (off).
  A furniture prop's `state` poses its sidecar objects (below); an opening's `state` turns its lead leaf or removes the
  leaves. Off, every drawer, door and leaf is at its shut pose. Placement by `cf` (tipped props) does not change.
- **Dust**: the furniture's colour is multiplied by the report's `dust`. Doors and windows are not dusted.
- **Storey cut** (`C`, or the Whole / Storey buttons): a clip plane 0.6 studs under the chosen storey's ceiling, to look
  in from above.
- The meta line reads the report: `condition`, triangles, meshes, rooms, openings, props, how many props and openings
  are posed, size, warnings.

The FBX of a Blockbench export is turned 180 degrees about Y, so the model is turned back (front at -Z). Report
positions and rotations are in the Roblox frame; the add-on works in the FBX's frame, where `(x, y, z)` is `(-x, y, -z)`
and a rotation is conjugated by the turn (`pose.js`, no page code, so a script can run it).

### Placement and poses

- **Place**: a prop's `cf` (12 numbers `[x, y, z, r00 .. r22]`, row-major) is the CFrame of its mesh origin in the
  building's frame, and a tipped or leaning prop is placed by it. Without `cf` (or a bad one), `pos` and `yaw` (degrees
  about Y) place it. Openings use `pos` and `yaw`. A box for a prop still to make uses `pos` and `yaw`.
- **Furniture `state`** `{object: amount}` (on a prop): the object named is moved by `amount`: a sliding object by that
  fraction of its `slide`, a hinged one by that many degrees about its `hinge` and `axis` (positive: counter-clockwise
  seen from the axis's + end). Every `Glass_*` object with the same slide, or hinge and axis, moves with it. Needs the
  prop's sidecar; a prop without one stays shut.
- **Opening `state`** (on an opening): `"removed"` draws no leaf (every hinged object that is not glass, and the glass with
  its motion, is hidden; the frame and any sliding part stay). An object `{Leaf: degrees}` turns the *lead leaf*, the
  first hinged non-glass object by name, and its panes; an angle for another object is ignored.

### Report fields the add-on reads (all optional)

`props[].cf`, `props[].state`, `openings[].state`, `dust` (number under 1 darkens the furniture), `condition`
(`{"level": 4, "name": "Ruined"}`, or just a name or a level; shown in the meta line). Nothing else is needed, and a
report without them draws as before.

The report shape (keys not listed are ignored):

```json
{
  "tris": 4323, "size": [82.0, 50.4, 54.0],
  "objects": {"plaster_white": {"finish": "plaster_white", "tint": "e2ded4", "glass": false,
              "maps": {"Color": "assets/buildings/_materials/plaster_Color.jpg", "Normal": "...", "Roughness": "...", "Metalness": "..."}}},
  "files": {"fbx": "assets/buildings/town/house_01.fbx"},
  "storeys": [{"index": 0, "name": "g", "y": 1.0, "height": 15.0, "clear": 13.5}],
  "rooms": [{"name": "kitchen"}],
  "openings": [{"prop": "Door_Wood", "pos": [x, y, z], "yaw": 90, "state": {"Leaf": 18.8}}],
  "props": [{"prop": "Bed", "pos": [x, y, z], "yaw": 270, "cf": [x, y, z, 0, 0, -1, 0, 1, 0, 1, 0, 0],
             "state": {"Drawer_1": 0.4}, "size": [w, h, d], "exists": true, "path": "assets/props/bedroom/Bed.fbx"}],
  "condition": {"level": 4, "name": "Ruined"}, "dust": 0.76,
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
frees the sources (never a model's disposal), so one building's teardown cannot break the next one's props. A posed
mesh changes its own matrix only (the shared geometry is untouched); a dusted building makes its own copies of the
prop materials and frees them in `destroy`.

`pose.js` (listed in the manifest's `files`) holds the maths with no page code; `tests/test_pose.py` runs it under node
(skipped when node is missing).
