# roblox-anim

Plays baked animation JSON on Roblox rigs. Each clip of a `.json` file shaped like a clip set is an `anim` item, in an
Animations category of its own (its key comes after the core ones). The page poses a rig from `<rigsDir>/<name>.json`
the way Roblox does (`part1 = part0 * C0 * Transform * C1:Inverse()`), with its clothing, its decals and a held prop's
mesh.

It can also play a gun clip with layers: the clips played beneath it and a body aim. This is a third-person gun-aim
layer model, as one Roblox game uses it: the hips blade to the side when a gun is raised, the waist turns the barrel
onto the look, and the head turns onto a long gun's stock. Leave the layer keys out and every clip simply plays alone.

Turn it on in the project's config:

```json
{"plugins": {"roblox-anim": {
  "rigsDir": "config/rigs",
  "defaultRig": "hero",
  "bodyAim": {"blade": 30, "busyTwist": 35, "lockMax": 45},
  "files": {"assets/animations/pistol.json": {
    "prop": "pistol", "aimHead": [40, 35, -5], "bodyAim": {"blade": 45},
    "under": {"*": ["Idle", "PistolHold"], "PistolHold": ["Idle"], "PistolFireAim": ["Idle", "PistolHold", "PistolAim"]},
    "up": ["PistolAim", "PistolFireAim"], "busy": ["PistolReload"]}}}}}
```

## Config

Plugins load once, when the viewer starts: restart it after you change this config.

| Key | Default | Meaning |
|---|---|---|
| `rigsDir` | `<config folder>/rigs` | Project folder with the rigs (`<name>.json`, images in `<name>/`). Read-only, served at `/files/`, even inside a dot folder such as `.asset-viewer/rigs`. A missing folder is fine: clips still list, and the page says there is no rig. |
| `defaultRig` | none | The rig a clip plays on when its file names none (else the first character rig, by name). |
| `bodyAim` | `{}` | `blade`, `busyTwist` and `lockMax`, in degrees (see Layers). A file's own `bodyAim` overrides it key by key. |
| `files` | `{}` | Per animation file, by project path (see below). |

A `files` entry (every key optional):

| Key | Meaning |
|---|---|
| `rig`, `prop` | The character rig and the held-item rig that the file's clips play on. A rig with a `grip` is a prop. |
| `under` | `{clip: [clips under it]}`, with `"*"` for every other clip: the clips played beneath it. A name resolves to the first file that has a clip of that name. |
| `up` | Clips seen only with the gun raised (stance `up`). |
| `busy` | Clips worked during the aim, such as reloads (stance `busy`). |
| `aimHead` | `[yaw, roll, nod?]` in degrees: how the head turns onto a long gun's stock in stance `up`. |
| `bodyAim` | Overrides the plugin's `bodyAim` for this file. |

## Layers

With Layers on (`G`, the default), a clip plays with what its file's entry adds:

- `under`: the listed clips play beneath it, each from its start; a looped one loops on.
- Stance `up`: the hips blade `blade` degrees right of the look (the look is -Z, the camera's), then the waist turns so
  the barrel (the prop's root) points along the look, by at most `lockMax`. The neck keeps the head on the look, then
  `aimHead` turns it right onto the stock, rolls it and nods it down.
- Stance `busy`: with Aim held (`A`, shown on these clips) the clip is worked during the aim: bladed, and the chest
  twisted `busyTwist` further right.

The look is level and far. With Layers off, the clip plays alone.

## Animation files

`{"<Clip>": {"fps": 30, "looped": true, "priority": "Action", "frames": [{"<Joint>": [x, y, z, qx, qy, qz, qw]}]}}`:
a joint's value is its `Transform` (studs, quaternion), and `frames` is a non-empty list of frames.

- A file where every value has `fps` and `frames` is an animation file. Any other JSON is left to other plugins.
- A file of that shape with a bad clip (no frames, a non-list `frames`, an `fps` that is not a positive number) shows up
  as a catalog warning, and the rest of the catalog lists as usual.
- A JSON file over 8 MiB is not read; it gets a catalog warning.
- A parsed file is cached until it changes. The cache forgets files that a scan no longer meets.

## Catalog items

`{id: "<path>#<Clip>", kind: "anim", name, dir: "<folder>/<file without .json>", fps, frames (a count), looped, priority,
rig, prop, under?: [{path, name}], stance?, bodyAim?, aimHead?}`, plus the catalog's `plugins["roblox-anim"] = {rigsDir,
rigs: [{name, mtime}]}`. A rig's `mtime` also moves when one of its images is exported again, and the catalog version
follows, so a re-exported rig reloads in place.

## Rigs

`<rigsDir>/<name>.json`, as `roblox-rig-export` writes them:

- `parts`: size, colour, rest CFrame, `mesh` as flat arrays, `texture` or `maps`, `decals`;
- `clothing`: the Shirt and Pants templates;
- `joints`: Part1 name to parent, C0 and C1;
- `root`;
- for a prop, also `grip` (the hand part, its attachment as C0, the item's HandGrip as C1) and optionally `mesh`, the
  project path of a model whose meshes replace the prop's boxes.

CFrames are the 12 numbers of `CFrame:GetComponents()`. A rig that fails to load is skipped, with a warning in the
console, and never stops the page.

## Keys

Space play / pause, Left / Right one frame, Home start, `L` loop, `G` layers, `A` aim held (shown on `busy` clips). The
browser remembers the speed, the loop, the rig and prop per file, Layers and Aim held.

## `--check`

Every clip's rig and prop exist, every `under` clip exists, a stance has `bodyAim` numbers, every rig file is a rig
(`parts` and `joints`), and every `files` path is an animation file under the roots.
