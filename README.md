# asset-viewer

A local web viewer for a project's assets. Point it at a folder and it lists every model, texture, VFX flipbook and
sound under it, previews them in the browser, and reloads a file in place when it changes on disk. A review board lets
you choose between candidates that a script or an AI agent put up, and saves your picks and notes to a JSON file that
those tools read back. One Python file starts it: no install, no build step, no network.

- Models: FBX (binary and ASCII), glTF / GLB and OBJ, with the PBR maps found beside them by name, map variants, shared
  atlases, a clay look, wireframe, spin and frame.
- Textures and VFX: dark, light, checker or additive background, fit, 1:1 or 4x, a 3 x 3 tile check, and flipbook
  playback for sheets named `*_<cols>x<rows>`.
- Sounds: waveform, play from any point, loop, peak and RMS levels.
- Review board: candidates grouped by the choice they are for, one pick per group, a note per item, a kept part of a
  sound (a cut), every result in `picks.json`.
- Live: new files show up, re-baked files reload, and config and theme edits apply while the page is open.
- Themes are one CSS file of tokens. Plugins add asset kinds, views, model add-ons and API routes.
- Localhost only, with a per-run token on every write.

## Quick start

You need Python 3.9 or newer and a current desktop browser (Chrome, Edge, Firefox or Safari). It runs on Windows, macOS and
Linux. CI tests it on Ubuntu and Windows.

```
git clone https://github.com/NoxLoveYa/asset-viewer
cd asset-viewer
python serve.py --project examples/sample
```

The browser opens `http://127.0.0.1:8765/`. Press Ctrl+C to stop the server. On macOS and some Linux systems the command
is `python3`.

To view your own project, run `python serve.py --project path/to/your/project`. Without `--project` the viewer uses the
current folder. Without a config file it lists the whole project, apart from dot folders, `__*` folders,
`node_modules`, `bower_components` and `site-packages`.

| Option | Meaning |
|---|---|
| `--project DIR` | The project folder (default: the current folder). |
| `--config FILE` | The config file, relative to the project (default: `asset-viewer.json`, else `.asset-viewer/config.json`). |
| `--port N` | The port (default: the config's `port`, else 8765). |
| `--no-open` | Don't open the browser. |
| `--check` | Check the project and the config, print what was found, and exit (0 = ok, 1 = a check failed). |
| `--debug` | Print requests and tracebacks to stderr. |
| `--trust-project-plugins` | Also load the plugins in the config's `pluginDirs`. They are Python code from the project and run with your rights (see Plugins). |
| `--version` | Print the version. |

Without `--port`, the viewer skips a busy port and tries the next 19 ports. If the viewer for this project is already
running, it opens that page and exits. With `--port` (1-65535), a busy port is an error. A bad config or plugin at
start-up exits with code 2.

The viewer works offline: three.js and the fonts are in the repository (`vendor/`, `themes/aero/fonts/`), and the page
loads nothing from the network. The only network access is `roblox-rig-export`'s thumbnail route, when Roblox Studio asks
for a thumbnail.

## Config

The config is one JSON file in the project: `asset-viewer.json`, or `.asset-viewer/config.json`, or the file named by
`--config`. Every path in it is relative to the project and uses `/`. The viewer reads the file again when it changes. If
an edit is bad, the viewer keeps the last good config and shows the error in a red banner on the page. Roots, hidden
paths, materials, the theme, a plugin's own config and the other keys apply at once. Plugin code is imported once, at
start: turning a plugin on or off, or changing `pluginDirs`, needs a restart, and the page says so.

```json
{
  "title": "My project",
  "roots": [{"path": "assets", "label": ""}, "docs/icons"],
  "hide": ["assets/sfx/unused"],
  "model": {"front": "-z", "glassNames": ["^Glass"]},
  "materials": {
    "assets/trees/*.fbx": {
      "Bark": {"set": "assets/trees/atlas/bark"},
      "Leaves": {"set": "assets/trees/atlas/leaves", "alpha": "cutout", "doubleSided": true}
    }
  },
  "theme": "config/theme.css",
  "plugins": {"roblox-anim": {"rigsDir": "config/rigs"}}
}
```

[docs/config.md](docs/config.md) lists every key with its type, default and meaning. JSON schemas for editors are in
`schemas/`: `config.schema.json` for the config and `review.schema.json` for the review file.

## Asset kinds and naming rules

The viewer finds assets by extension. You do not register anything.

| Kind | Files | Rules |
|---|---|---|
| Models | `.fbx` `.glb` `.gltf` `.obj` | PBR maps in the same folder, by name (see below). |
| Textures | `.png` `.jpg` `.jpeg` `.webp` `.gif` `.svg` | |
| VFX | a texture under a folder named in `vfxFolders` (default `vfx`), at any depth | `name_<cols>x<rows>.png` (e.g. `spark_4x4.png`) also plays as a flipbook. |
| Sounds | `.ogg` `.mp3` `.wav` `.flac` | |
| Plugin kinds | what the plugin claims (e.g. `.json` animation clips for `roblox-anim`) | Each plugin's README. |

An item's id is its project path, such as `assets/models/crate.obj`. A plugin can make ids of its own: `roblox-anim`
uses `<path>#<Clip>`.

### Model maps

The map names are `Color`, `Normal`, `Metalness`, `Roughness` and `Emissive` (config `model.maps`). A map is a `.png`,
`.jpg`, `.jpeg` or `.webp` file in the same folder as the model:

| File | Meaning |
|---|---|
| `Stem.png` | The Base set's Color. |
| `Stem_<Map>.png` | The Base set's `<Map>`. |
| `Stem_<Variant>_<Map>.png` | A variant (`Worn`, `Red`...). A variant takes any map it lacks from the Base set. |

A map file belongs to one model only. If `gate.fbx` and `gate_left.fbx` are in the same folder, `gate_left_Color.png`
is the Color of `gate_left`, not the `left` variant of `gate`. If several stems fit, the longest wins. Each variant is
a look in the model view's Look selector, next to File (the file's own materials, shown when there are no maps) and
Clay. Map files also list under Textures.

### Shared atlases

Maps that many models share, kept outside the models' folders, are set in the config's `materials`:

- The key is a glob of model paths. `*` also matches `/`.
- Each entry maps a mesh name, or a material name, to a rule. `"*"` is the rule for every other mesh.
- `set` is the path stem of a map set: `assets/trees/atlas/bark` means `bark_Color.png`, `bark_Normal.png` and so on.
  Normal maps are OpenGL style (Y up), as three.js reads them.
- `"alpha": "cutout"` cuts the shape out of the Color's alpha (alpha test 0.5, depth written, no blending).
- `"doubleSided": true` draws both faces (for cards).

A model with a rule gets a Textured look, which is its default. The viewer does not load the textures that the model
file names itself. `--check` fails on a glob that matches no model, and on a set that has no `_Color` map.

### The list

Each category lists its items in folders. A folder's label is the item's path under its root, with the root's `label`
in front. Folders start closed, except the folder of the open item. Click a folder to open or close it, or use Expand all
and Collapse all. The search box matches names, paths and folder labels. The Picks button shows picked items only. A
search or the Picks filter opens every folder that has a match.

The page polls the server every 2 seconds. A new file shows up and a changed file reloads in place. Below a root,
files and folders whose names start with `.` are never listed.

### Links

- `http://127.0.0.1:8765/#<url-encoded id>` opens that item, for example `#assets%2Fmodels%2Fcrate.obj`. The link also
  works on a page that is already open.
- `#review` opens the review board. `#review/<slug>` opens one review, where the slug is the review's title in lower
  case with every run of other characters replaced by a dash: "Sample: choose a few things" is
  `#review/sample-choose-a-few-things`.

## Keys

| Where | Keys |
|---|---|
| Everywhere | Up / Down: item (in open folders only). `[` / `]`: previous / next folder, opened. `/`: search. `1`-`9`: category. `R`: review board (when there is something to review). Enter or Esc leaves a text box. |
| Models | `W` wireframe, `S` spin, `F` frame. Left drag orbits, right drag pans, the wheel zooms. Plugins add keys (e.g. `I`, `C`, `O` for `roblox-buildings`). |
| Textures, VFX | `T` tile. Space plays or pauses a flipbook. |
| Sounds | Space play / stop, `L` loop. Click the waveform to play from that point. |
| Review board | Up / Down: candidate (with Autoplay on, a sound plays). Left / Right: group (past the last group, into the next review). `[` / `]`: previous / next review. `/`: find a review. Space: play. `P`: pick. `N` or Enter: note. `X`: whole file (clears the cut). `R`: back to the catalog. The preview's own keys work too. |

The key line under the list shows the current keys, including plugin keys. The browser remembers the view options, the
last item (per category too), the open folders and the review board's state, in `localStorage` under the config's
`prefsPrefix`.

## Picks and reviews

### `picks.json`

The page writes your picks and notes to `picks.json`, next to the config file by default (config `data.picks`):

```json
{
  "assets/sounds/blip.wav": {"picked": true, "note": "shorter, please", "at": "2026-11-05T09:12:44+00:00", "cut": [0.12, 0.48]},
  "assets/models/crate.obj": {"picked": false, "note": "the lid floats", "at": "2026-11-05T09:13:02+00:00"}
}
```

- The keys are item ids.
- `at` is the time of the last change (UTC).
- `cut` is only on a sound trimmed on the review board: `[start, end]` in seconds of the file. No `cut` means the whole
  file.
- An entry with no pick, no note and no cut is removed.

You make picks on the review board only. Every other view has a note box. Writes are atomic (temp file and rename). If
the file is not valid JSON, the viewer never overwrites it: the page shows an error until you fix or delete the file.
Edits made outside the viewer show on the page at the next poll.

### `review.json`: asking a person to choose

When a script or an AI agent has made several candidates and needs a person to choose, it writes them to `review.json`
(config `data.review`, next to the config file by default):

```json
{"reviews": [
  {"title": "Sounds: alerts", "about": "What this review is for.",
   "groups": [
     {"id": "Alert.JobDone", "label": "Job done", "about": "Which alert plays when a job finishes?",
      "items": [
        {"id": "assets/sounds/beep.wav", "name": "Beep", "by": "sfx-script", "note": "Longer, lower."},
        {"id": "assets/sounds/blip.wav", "name": "Blip", "by": "sfx-script", "note": "Short and high."},
        {"id": "assets/sounds/old_alert.wav", "name": "Current", "ref": true}
      ]}
   ]}
]}
```

| Field | Required | Meaning |
|---|---|---|
| `reviews[].title` | yes | Shown as a heading. In the nav, the text before a colon groups reviews ("Sounds"). |
| `reviews[].about` | no | A line under the title. |
| `reviews[].groups[].id` | yes | The choice's key, shown on its card. |
| `groups[].label`, `groups[].about` | no | The choice's name and question. |
| `groups[].items[].id` | yes | A catalog item id. A missing item is shown as Missing. |
| `items[].name`, `items[].by`, `items[].note` | no | The candidate's name, its author, and what the author knows about it. |
| `items[].ref` | no | `true`: there for comparison only (an old version, a render). It shows a Reference badge and can have a note, but it can't be picked. A group of nothing but references is not counted. |

The workflow:

1. Make the candidates as real files under a root, so each is a catalog item. Put the model or the texture itself in the
   group, not a picture of it.
2. Write `review.json`. The page picks it up at the next poll and adds a Review row with `picked / groups`.
3. Send the person a link: `http://127.0.0.1:8765/#review/<slug>`.
4. The person picks one candidate per group (picking another unpicks the first), writes notes, and trims sounds by
   dragging on the waveform.
5. Read `picks.json`. For each group, the outcome is the item with `"picked": true`, its `note` and its `cut`. Notes
   on unpicked items are feedback too.
6. Remove the review from `review.json` once its picks are acted on, so the board shows only what is still open.

On the board, sounds play inline with their waveform, at matched levels by default (Match levels off plays the raw
file). Anything else previews in a pane beside the list, in the same view as the catalog. One review is drawn at a time;
All reviews draws them all in one scroll. Filter the groups with All, To pick and Picked.

### The HTTP API

Tools can also use the API. Reads need nothing special. A write needs the per-run token (see Security) and a JSON body.

| Route | What |
|---|---|
| `GET /api/hello` | `{app, version, project}`: is this an asset viewer, and for which project. |
| `GET /api/config` | The page config: title, categories, plugins, data file paths, config error and warnings. |
| `GET /api/catalog` | `{version, items, plugins, warnings}`: every item with its id, path, kind, name, dir, mtime, bytes, maps. |
| `GET /api/picks` | `{rev, picks}`. |
| `POST /api/picks` | `{id, picked?, note?, cut?}`: a missing field keeps its value, and `"cut": null` clears the cut. Or `{group: [ids], picked: id or null}`: picks one item of a group and unpicks the rest, in one write. The answer is `{rev, picks}`. |
| `GET /api/review` | `{rev, reviews, error}`. |

The server prints the token file's path at start-up (`Token file (for local tools): ...`). A tool can also find the
file itself: it is `<project>.token`, where `<project>` comes from `GET /api/hello`, in the first of these folders
that holds it:

| Platform | Folders, in order |
|---|---|
| any | `$ASSET_VIEWER_STATE_DIR` when it is set (then only that folder) |
| Windows | `%LOCALAPPDATA%\asset-viewer`, then `<temp>\asset-viewer` |
| macOS, Linux | `$XDG_RUNTIME_DIR/asset-viewer`, `$XDG_STATE_HOME/asset-viewer`, `~/.local/state/asset-viewer`, then `<temp>/asset-viewer` |

```python
import json, os, tempfile, urllib.request

BASE = "http://127.0.0.1:8765"


def token_file(project):
    if os.environ.get("ASSET_VIEWER_STATE_DIR"):
        dirs = [os.environ["ASSET_VIEWER_STATE_DIR"]]
    else:
        bases = [os.environ.get("LOCALAPPDATA")] if os.name == "nt" else [
            os.environ.get("XDG_RUNTIME_DIR"), os.environ.get("XDG_STATE_HOME"), os.path.expanduser("~/.local/state")]
        dirs = [os.path.join(b, "asset-viewer") for b in bases if b] + [os.path.join(tempfile.gettempdir(), "asset-viewer")]
    return next(p for p in (os.path.join(d, project + ".token") for d in dirs) if os.path.isfile(p))


project = json.load(urllib.request.urlopen(BASE + "/api/hello"))["project"]
with open(token_file(project), encoding="ascii") as f:
    token = f.read()
req = urllib.request.Request(
    BASE + "/api/picks", method="POST",
    data=json.dumps({"id": "assets/sounds/blip.wav", "note": "shorter, please"}).encode(),
    headers={"Content-Type": "application/json", "X-Viewer-Token": token})
print(json.load(urllib.request.urlopen(req)))
```

## Themes

The look comes from CSS custom properties (tokens) only. `themes/aero/theme.css` (dark, the default) always loads first
and defines every token. A theme is one CSS file that overrides the tokens you want to change. Set the config's `theme`
to `"paper"` (light, built in) or to the path of your own `.css` file in the project. A saved theme file repaints the
page, the 3D stage and the waveforms without a reload. See [docs/themes.md](docs/themes.md).

## Plugins

A plugin is a folder with a `plugin.json`, an optional `server.py` and optional client files. It can add asset kinds,
add data to items, add views, model add-ons and API routes, and run checks for `--check`. Turn a plugin on by naming it
under `plugins` in the config. The plugins in the tool's `plugins/` folder always load. A plugin in one of the
config's `pluginDirs` is project code: it loads only when you start the viewer with `--trust-project-plugins`, and
without the flag the viewer prints a warning that names what it skipped. Plugin code is imported once, at start (a plugin's own config can be edited while it runs). Three plugins ship
in `plugins/`:

| Plugin | What it does |
|---|---|
| [`roblox-anim`](plugins/roblox-anim/README.md) | Plays baked animation clip JSON on Roblox rigs (an Animations category). |
| [`roblox-buildings`](plugins/roblox-buildings/README.md) | Model add-ons for buildings and props with a JSON beside them: tiled tinted finishes, furniture props, storey cuts, opening doors and drawers. |
| [`roblox-rig-export`](plugins/roblox-rig-export/README.md) | Takes rigs exported from Roblox Studio into `roblox-anim`'s rigs folder. Needs `roblox-anim`. |

To write your own, see [docs/plugins.md](docs/plugins.md).

## Security

The viewer is a local tool. It serves only what it must, to this computer only. To report a vulnerability, see
[SECURITY.md](SECURITY.md).

- **Localhost only.** The server listens on `127.0.0.1` only. There is no option to listen elsewhere. A request whose
  `Host` header is not `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` is refused (421), which blocks DNS
  rebinding.
- **Same origin.** An API request from another web page (an `Origin` that is not the viewer, or a cross-site
  `Sec-Fetch-Site`) is refused (403). Every answer carries `Cross-Origin-Resource-Policy: same-origin`.
- **Per-run token.** Each run makes a random token. The page gets it inside its HTML. Every write (POST, PUT, DELETE)
  must send it as `X-Viewer-Token`, with a JSON `Content-Type` and a `Content-Length` under the route's limit. For
  local tools the server also writes the token to a file in a per-user state folder, never inside the project (see
  The HTTP API). The file can be read by your user only (on systems with Unix permissions), its path is printed at
  start-up, and it is deleted on exit. A plugin route can accept a request without the token only when the request has
  no `Origin` and no `Sec-Fetch-Site` header: no browser sends such a request (`roblox-rig-export` uses this for Roblox
  Studio).
- **What is served.** The tool's own `app/`, `vendor/`, `themes/` folders and the client files that a plugin's manifest
  lists (never `server.py`), the chosen theme's folder, and these project files at `/files/`:
  - only inside the `roots`, the two data files and the folders that plugins serve;
  - only the listed kinds' extensions (models `.fbx .glb .gltf .obj`, textures `.png .jpg .jpeg .webp .gif .svg`,
    sounds `.ogg .mp3 .wav .flac`) and the data types `.json .txt .mtl .bin`;
  - never a dot file or a path through a dot folder (`.git/`, `.env`), never a `hide` match, never through a symlink
    that leads outside the project, and never a folder listing. A served area that the config or a plugin declares
    may itself be in a dot folder (`.asset-viewer/rigs`); only that area is served, not the rest of the dot folder.
- **What is written.** Only `picks.json`, the token file and the areas a plugin declares, always as a temp file and a
  rename. Nothing is written outside the project except the token file.
- **Headers.** The page has a strict Content-Security-Policy (scripts from the viewer only, no inline code except the
  import map, by hash). Everything that is not the tool's own file (project files, a project theme's files, API
  answers, errors) carries `Content-Security-Policy: sandbox; default-src 'none'`, so an opened SVG can't run script.
  Every answer has `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`.
- **Plugin code.** A plugin's `server.py` runs with your rights. The bundled plugins in the tool's `plugins/` folder
  always load. Plugins in a project's `pluginDirs` are code from the project, so they load only with
  `--trust-project-plugins`: opening a project you don't trust does not run its code. Plugins are never downloaded.

## Development

Standard library Python and plain ES modules: there is nothing to install or build. Run these from the repository root:

```
python -m unittest discover -s tests              # unit tests, plugin tests included
python serve.py --project examples/sample --check # the sample project's self-check
python tools/smoke.py --no-webgl                  # the page in headless Chrome or Edge, without WebGL
python tools/smoke.py                             # the same, 3D views included (needs a software or real GPU)
python tools/vendor_three.py --verify             # vendored three.js and fonts against their sha256 manifests
```

- `tools/smoke.py` serves a temporary copy of `examples/sample` and opens its own test page (`tools/smoke/smoke.html`,
  mounted by the runner; the viewer never serves it) in a headless Chrome, Edge or Chromium. The page mounts every
  view, drives the review board, and runs the real app in frames to check races, debounced saves, stored state and
  error handling. The runner injects some failures on purpose (a 503, a 403 after the token changes). The run fails on
  any failed step, console error, CSP violation or other 4xx/5xx answer. Use `--browser PATH` (or set `CHROME`) if it
  can't find a browser, `--verbose` to see each step, and `--budget MS` (default 120000) for the page's virtual time.
- `tools/vendor_three.py` without options fetches the pinned three.js (0.170.0) and fonts and checks them by hash;
  `--update` rewrites the manifests after a version bump. This tool is the only part that uses the network, and only
  when you run it.
- `tools/make_sample.py` regenerates `examples/sample`. The tests fail if the committed sample differs from its output.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the rules.

## Licence

Copyright (C) 2026 NoxLoveYa

asset-viewer is free software under the GNU Affero General Public License, version 3 or later
(`AGPL-3.0-or-later`); see [LICENSE](LICENSE). Bundled third-party files keep their own licences:

- three.js, in `vendor/three/`: MIT ([vendor/three/LICENSE](vendor/three/LICENSE)).
- fflate, vendored with three.js in `vendor/three/examples/jsm/libs/fflate.module.js`: MIT (notice in the file's
  header).
- Titillium Web and Roboto Mono, in `themes/aero/fonts/`: SIL Open Font License 1.1
  ([themes/aero/fonts/OFL.txt](themes/aero/fonts/OFL.txt)).
