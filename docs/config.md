# Config reference

## Where the config lives

The config is one JSON object in a file inside the project. The viewer uses the first of these that exists:

1. the file named by `--config` (relative to the project; an absolute path must be inside the project, and the file
   must exist);
2. `asset-viewer.json` in the project folder;
3. `.asset-viewer/config.json`.

With no config file, every key has its default and the root is the whole project.

The **config folder** is the folder holding the config file (the project folder when there is none). The data files
default to it.

## Rules

- Every path is relative to the project and uses `/`. `./` at the start and `/` at the end are dropped. A path with
  `\`, `:`, `..`, a leading `/`, an empty segment or a reserved Windows name (`con`, `nul`, `com1`...) is an error.
  `hide` patterns are more lenient (see [hide](#hide)).
- A wrong value is an error that names its key path, such as `model.front: "+z" or "-z" expected`. An unknown key is a
  warning, printed at start-up and shown on the page.
- The server looks at the file again on the next request after a change (the page polls every 2 seconds). A bad edit
  keeps the last good config, and the page shows the error in a red banner until the file is fixed. A bad config at
  start-up exits with code 2.
- Every key applies at once, except `plugins` and `pluginDirs`: plugins load once, at start, so a change to either needs
  a restart of the viewer. Until then the page shows a warning. A change to the categories or plugins the page knows
  asks you to reload the page.
- The file may be at most 1 MiB. A UTF-8 byte order mark is fine.

## Schemas

`schemas/config.schema.json` (the config) and `schemas/review.schema.json` (the review file) are JSON schemas for
editors. Point the `"$schema"` key at one of them, by a path or a URL, to get completion and checks in your editor. The
viewer allows `"$schema"` and ignores it. The viewer checks more than a schema can (paths that leave the project, regex
syntax, each plugin's keys against its manifest), so `--check` is still the final word.

## Keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `title` | non-empty string | the project folder's name | The page title and the nav header. |
| `port` | integer 1-65535 | `8765` | The first port to try. `--port` wins over it. |
| `roots` | array of path or `{path, label}` | `["."]` (the whole project) | The folders to list (see [roots](#roots)). |
| `hide` | array of pattern | `[]` | Project paths that are neither listed nor served (see [hide](#hide)). |
| `categories` | array of kind | core kinds, then plugin kinds | The categories in the nav, in this order. Keys `1`-`9` follow the order. A kind that is not core and that no loaded plugin adds is dropped, with a warning on the page. A duplicate is an error. |
| `vfxFolders` | array of folder name | `["vfx"]` | A texture with a folder of one of these names in its path is a VFX item. The names have no `/` and match case-insensitively. |
| `model.maps` | array of word | `["Color", "Normal", "Metalness", "Roughness", "Emissive"]` | The map suffixes for `Stem_<Map>` files and shared sets. Each is letters, digits and `_`. The view uses `Color`, `Normal`, `Metalness`, `Roughness` and `Emissive`. |
| `model.front` | `"+z"` or `"-z"` | `"+z"` | The side a model's front faces, for the 3/4 framing. Roblox models face `-z`. |
| `model.glassNames` | array of regex | `[]` | Mesh names that draw as glass (the `--model-glass` token) in the map and Clay looks (see [glassNames](#glassnames)). |
| `materials` | object | `{}` | Shared atlases (see [materials](#materials)). |
| `theme` | string | `"aero"` | A built-in theme name (`aero`, `paper`), or the path of a `.css` file in the project. A value that ends in `.css` or contains `/` is a path. See [themes.md](themes.md). |
| `data.picks` | path | `<config folder>/picks.json` | Where the page saves picks and notes. |
| `data.review` | path | `<config folder>/review.json` | Where the review board reads its reviews. It must differ from `data.picks`. |
| `prefsPrefix` | string, 64 characters at most | `"av.<8 hex digits>."` | The `localStorage` prefix for the page's remembered state. The default comes from the project path, so two projects on the same port don't share state. Set it to keep state across a move. |
| `pluginDirs` | array of path | `[]` | Project folders that hold more plugins, searched after the tool's `plugins/`. They load only with `--trust-project-plugins` (see [plugins](#plugins)). |
| `plugins` | object | `{}` | The plugins to turn on (see [plugins](#plugins)). |

### roots

Each root is a project folder to list, searched at any depth. A root is a path string, or an object:

| Key | Type | Meaning |
|---|---|---|
| `path` | path | The folder. `"."` is the whole project. |
| `label` | string | Put in front of the folder labels of its items. The default is the last segment of `path`. `""` means no label. |

With `{"path": "assets", "label": ""}`, `assets/models/crate.obj` lists under `models`. With `"assets"` it lists under
`assets / models`. Below a root, the scan skips files and folders whose names start with `.`, folders whose names start
with `__`, `node_modules`, `bower_components` and `site-packages`, and it does not follow folder symlinks or Windows
junctions. A root may itself be in a dot folder. A missing root is a warning.

### hide

Each pattern is a glob of project paths, matched against the whole path, case-insensitively. `*` also matches `/`.

- A pattern means that path **or anything below it**. `assets/secret`, `assets/secret/`, `./assets/secret/*` and
  `assets\secret\*` all hide every file under `assets/secret`.
- `\` becomes `/`, and a leading `./` and a trailing `/` are dropped.
- `"*.tmp.png"` hides files with that ending anywhere.
- An absolute pattern (`/x`, `C:/x`), a pattern with `..` and an empty pattern can match nothing: each is a warning and
  is dropped.
- `--check` fails on a pattern that matches no file or folder under the roots (a typo, or a path that moved). On a
  project with more than 300,000 files under the roots, it warns and skips this check.

A hidden file is not listed, not served at `/files/`, and not served as a project theme file.

### glassNames

The page matches mesh names with a JavaScript `RegExp`, so a pattern may only use the syntax that Python and JavaScript
share: groups `( )`, `(?: )`, lookahead `(?= )` and `(?! )`, classes `[ ]`, alternation, anchors `^` and `$`, escapes
such as `\d \w \s`, and greedy and lazy quantifiers.

The viewer refuses, with an error that names the entry:

- named groups (`(?P<name>...)`, `(?<name>...)`);
- inline flags (`(?i)`);
- atomic groups (`(?>...)`), conditionals (`(?(1)...)`) and comments (`(?#...)`);
- lookbehind (`(?<=...)`, `(?<!...)`), which older browsers lack;
- possessive quantifiers (`a++`);
- `\A` and `\Z` (use `^` and `$`);
- `]` as the first character of a class (`[]a]`): write `\]`.

### materials

```json
"materials": {
  "assets/trees/*.fbx": {
    "Bark":   {"set": "assets/trees/atlas/bark"},
    "Leaves": {"set": "assets/trees/atlas/leaves", "alpha": "cutout", "doubleSided": true},
    "*":      {"set": "assets/trees/atlas/bark"}
  }
}
```

| Part | Type | Meaning |
|---|---|---|
| key | glob | Model paths this entry applies to (case-sensitive; `*` also matches `/`). |
| inner key | string | A mesh name in the model file, or a material name, or `"*"` for every other mesh. |
| `set` | path | Required. The path stem of a map set: `<set>_Color.png`, `<set>_Normal.png`... (also `.jpg`, `.jpeg`, `.webp`). |
| `alpha` | `"cutout"` | Cut the shape out of the Color map's alpha (alpha test 0.5, depth written, no blending). |
| `doubleSided` | boolean | Draw both faces. |

A matching model gets a Textured look, which is its default, and the textures its file names are not loaded. `--check`
fails on a key that matches no model, and on a `set` without a `_Color` map.

### plugins

```json
"plugins": {
  "roblox-anim": {"rigsDir": "config/rigs"},
  "roblox-rig-export": {},
  "roblox-buildings": {"enabled": false}
}
```

A key turns that plugin on. A plugin name is lower case letters, digits and dashes. The value is `null` or an object:
the plugin's own config, merged over the defaults in its `plugin.json` and checked against its `types`. `"enabled":
false` turns the plugin off but keeps its config. Each plugin's README lists its keys.

- The plugins in the tool's `plugins/` folder always load.
- A plugin in a `pluginDirs` folder is Python code from the project, run with your rights. It loads only when the viewer
  starts with `--trust-project-plugins`. Without the flag the viewer skips it, and every plugin that requires it, with
  a warning that names the folders and the plugins, and the rest of the viewer works.
- With the flag, a plugin name found in two places is an error.
- A plugin that is not found anywhere, that needs a plugin that is not turned on, or whose config has a wrong type is an
  error.
- Plugins load once, at start. A change to `plugins` (a plugin's config included) or `pluginDirs` takes effect after a
  restart.

## Files next to the config

| File | Written by | Meaning |
|---|---|---|
| `picks.json` | the viewer | Picks and notes (README, Picks and reviews). |
| `review.json` | your tools | What waits for a pick. The viewer only reads it. |

The per-run token file is not here: it lives in a per-user state folder (README, The HTTP API).
