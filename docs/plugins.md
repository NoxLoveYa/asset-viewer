# Writing a plugin

A plugin is a folder with a manifest (`plugin.json`), an optional `server.py` and optional client files. The server
side can add asset kinds, add data to any item, serve or write project folders, mount API routes and add checks to
`--check`. The client side can add categories, views, model add-ons and key hints.

The core's own categories register through the same client calls, so a plugin's kind is a peer of the core kinds.

## Where plugins live

- The viewer looks in the tool's `plugins/` folder, then in each folder of the config's `pluginDirs`. The folder name
  is the plugin name: lower case letters, digits and dashes, starting with a letter.
- A plugin is on when the project's config names it under `plugins`. Its value there is the plugin's config.
- A plugin's `server.py` runs inside the viewer with your rights. The plugins in the tool's `plugins/` folder always
  load. A plugin in a `pluginDirs` folder is code that belongs to the project: it loads only when the viewer starts with
  `--trust-project-plugins`. Without the flag it is skipped, with a warning that names it, and so is any plugin that
  requires it. Plugins are never downloaded.
- Plugins load in config order, except that a plugin in `requires` loads before the plugin that needs it.
- A plugin that fails to load (bad manifest, wrong type in its config, an exception in `register`) stops the viewer at
  start-up with the plugin's name in the error.
- Plugins load once, at start, and `register` runs once. A change to `plugins` (a plugin's config included) or
  `pluginDirs` in the config takes effect after a restart; until then the page shows a warning. After you edit
  `server.py`, restart the viewer. After you edit a client file, reload the page.

## A minimal plugin

This plugin lists every `.txt` file under the roots as a Note and shows its text. Put it in the project, for example in
`viewer-plugins/txt-notes/`, and turn it on in the project's config:

```json
{"roots": ["notes"], "pluginDirs": ["viewer-plugins"], "plugins": {"txt-notes": {"maxLines": 200}}}
```

The plugin is in `pluginDirs`, so start the viewer with `python serve.py --project <project> --trust-project-plugins`.

`viewer-plugins/txt-notes/plugin.json`:

```json
{
  "name": "txt-notes",
  "apiVersion": 1,
  "server": "server.py",
  "client": "client.js",
  "files": ["view.js"],
  "defaults": {"maxLines": 500},
  "types": {"maxLines": "integer"}
}
```

`viewer-plugins/txt-notes/server.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""txt-notes: every .txt file under the roots is a "note" item."""
_lines = {}  # path -> ((mtime_ns, size), line count): a file is read again only when it changed


def count_lines(path, st):
    sig = (st.st_mtime_ns, st.st_size)
    hit = _lines.get(path)
    if not hit or hit[0] != sig:
        with open(path, encoding="utf-8", errors="replace") as f:
            hit = _lines[path] = (sig, sum(1 for _ in f))
    return hit[1]


def register(api):
    def claim(abs_path, rel, st, ctx):
        return [{"lines": count_lines(abs_path, st)}]  # the core fills id, path, name, dir, mtime, bytes

    def check(report):
        for item in report.items:
            if item["kind"] == "note" and item["lines"] > api.config["maxLines"]:
                report.warn("%s: %d lines" % (item["path"], item["lines"]))

    api.add_kind("note", [".txt"], claim)
    api.check(check)
```

`viewer-plugins/txt-notes/client.js`:

```js
// SPDX-License-Identifier: AGPL-3.0-or-later
export function register(viewer) {
  viewer.addCategory({
    kind: "note",
    label: "Notes",
    icon: "texture",
    view: () => import("/plugins/txt-notes/view.js"),
    rowMeta: (item) => item.lines + " lines",
  });
}
```

`viewer-plugins/txt-notes/view.js`:

```js
// SPDX-License-Identifier: AGPL-3.0-or-later
import { el, url } from "viewer/ui.js";

export async function mount(item, ctx) {
  const pre = el("pre");
  ctx.view2d.append(pre);

  async function update(next) {
    item = next;
    const r = await fetch(url(item.path, item.mtime));
    pre.textContent = r.ok ? await r.text() : "Can't read " + item.path;
    ctx.meta(item.lines + " lines");
  }

  await update(item);
  return { update, destroy() { pre.remove(); }, key() { return false; } };
}
```

`python serve.py --project <project> --check` now prints `ok: 1 note ...`, plus a warning for each note longer than
`maxLines`. The page has a Notes category.

## The manifest

| Key | Type | Meaning |
|---|---|---|
| `name` | string | Required. The folder name. |
| `apiVersion` | integer | Required. The plugin API version; this viewer supports `1`. A plugin with another version is refused. |
| `server` | file name | The Python module with `register(api)`. |
| `client` | file name | The ES module with `register(viewer)`. |
| `styles` | array of file names | Stylesheets the page adds before it runs `client`. |
| `files` | array of file names | Any other file the page may load (views, icons, data). |
| `requires` | array of plugin names | Plugins that must be on too. They load first. |
| `defaults` | object | The plugin's default config. The project's config is merged over it, key by key and at any depth. |
| `types` | object | The types of the config keys (see below). |

The viewer serves only `client`, `styles` and `files`, at `/plugins/<name>/<file>`, so every file the page loads
(a view module, an icon, a data file) must be listed in one of them. It serves only these extensions: `.js .mjs .css`,
fonts `.woff2 .woff .ttf .otf`, images `.png .jpg .jpeg .webp .gif .svg`, and `.json .txt .mtl .bin`. It never serves
`server.py`, an HTML file or any file that the manifest does not list. Every listed file must exist.

### Config types

`types` maps a config key to a type spec. A spec is either:

- a string of type names joined by `|`: `string`, `number`, `integer`, `boolean`, `array`, `object`, `null`, `any`. For
  example `"string|null"`.
- an object of key specs, where `"*"` is the spec of every other key. For example `{"*": "number"}` is an object of
  numbers.

A value of the wrong type is an error, named by its key path (`plugins.txt-notes.maxLines: integer expected`). A key
that the spec does not know is a warning. The project config's `enabled` key is removed before the merge.

## Server API

`server.py` defines `register(api)`. The viewer calls it once, at start. `api` belongs to this plugin only.

| Member | Meaning |
|---|---|
| `api.name` | The plugin name. |
| `api.config` | The merged config: the manifest's `defaults` with the project's config over it. |
| `api.dir` | The plugin's folder (an absolute path). |
| `api.project` | The project (see below). |
| `api.add_kind(kind, exts, claim)` | Claim file extensions as items of a new kind. |
| `api.enrich(kind, fn)` | Add data to every item of a kind, core kinds included. |
| `api.after_catalog(fn)` | `fn(items)` once per scan, after all items exist. Use it to resolve references across files. |
| `api.catalog_extra(fn)` | `fn()` returns JSON, sent as `catalog["plugins"][<name>]`. The page reads it with `ctx.catalogExtra(name)`. |
| `api.required_config(name)` | A copy of the merged config of a plugin listed in `requires` (it loads first). Any other name raises. `roblox-rig-export` uses it to find `roblox-anim`'s rigs folder. |
| `api.serve(rel_dir)` | Add a read-only project folder to `/files/`. The usual extension, `hide` and dot rules apply below it, but the folder itself may sit in a dot folder (`.asset-viewer/rigs`). `--check` fails when the folder can't be served. |
| `api.writes(rel)` | Declare a project folder or file that `api.project.write_atomic` may write. |
| `api.route(...)` | Mount an HTTP route (see Routes). |
| `api.check(fn)` | Add a check to `--check`. |
| `api.client_config(fn)` | `fn()` returns the JSON the page gets as `viewer.config`. The default is `api.config`. |
| `api.json_response`, `api.bytes_response`, `api.Response`, `api.HttpError` | For route handlers. |

### `api.project`

| Member | Meaning |
|---|---|
| `root` | The project folder (absolute, symlinks resolved). |
| `config_dir` | The config folder, as a project path (`""` for the project folder). Data defaults go here. |
| `rel(abs_path)` | The project path of an absolute path. Raises a `ValueError` subclass when the path is outside the project. |
| `confine(rel)` | The absolute path of a project path. Raises when the path is malformed or its real path leaves the project. |
| `read_json(rel)` | Read a project JSON file. |
| `write_atomic(rel, data)` | Write bytes through a temp file and a rename. Raises unless `rel` is inside an area declared with `api.writes`. |

Every path the viewer gives you or takes from you is a project path: relative, with `/`.

### Items

A core item is `{id, path, kind, name, dir, mtime, bytes}`, where `mtime` is in milliseconds, plus `maps` and
`materials` for models. `dir` is the folder label the list shows. A plugin's own kind may add flat fields. Data that a
plugin adds to an item of another kind goes under `item["ext"][<plugin name>]`, so names never clash.

### Claiming files: `api.add_kind(kind, exts, claim)`

- `kind` is a new name, lower case, not a core kind (`model`, `texture`, `vfx`, `sound`). Only one plugin may add a
  kind.
- `exts` are extensions like `".json"` that no core kind owns. Several plugins may claim the same extension.
- `claim(abs_path, rel, stat, ctx)` runs for each file with one of those extensions. It returns `None` for "not mine"
  (the next plugin that claims the extension gets a try), or a list of dicts. Each dict becomes an item: the core
  fills `id` (the path), `path`, `kind`, `name` (the file name without its extension), `dir`, `mtime` and `bytes`,
  and the dict's own keys override them.
- One file can give several items, each with a unique `id` (`roblox-anim` uses `<path>#<Clip>`). A duplicate id is
  dropped with a warning.
- An exception in `claim` becomes a catalog warning, and the next plugin gets the file. A broken file never breaks the
  catalog.
- The page can only fetch files that `/files/` serves (README, Security). Of those, only `.json`, `.txt`, `.mtl` and
  `.bin` have no core kind, so those are the extensions a plugin kind can usefully claim.

`ctx` describes the folder being scanned:

| Member | Meaning |
|---|---|
| `ctx.folder` | The folder's project path. |
| `ctx.dir` | The folder's display label. |
| `ctx.label(*parts)` | A label under the folder's: `ctx.label("walk")` is `<dir>/walk`. |
| `ctx.siblings`, `ctx.sibling_set` | The file names in the folder, as a list and as a frozenset. |
| `ctx.cache` | A dict shared by every callback during one scan. |

The catalog is scanned again at most once a second while a page is open. Keep `claim` and `enrich` cheap:

- Cache what you parse by `(st_mtime_ns, st_size)` in a module-level dict, as the example does.
- Prune that cache: the plugin lives as long as the viewer, so a cache of every file ever seen grows for ever. The
  bundled plugins note each path a scan asks about and, in `after_catalog`, forget the others.
- Cap what you read. The bundled plugins skip a JSON file over 8 MiB with a catalog warning instead of parsing it.

### Enriching items: `api.enrich(kind, fn)`

`fn(item, ctx)` runs for each item of `kind`. It may change the item. A dict it returns is stored as
`item["ext"][<plugin name>]`. Return `None` to add nothing. An exception becomes a catalog warning.

### Routes: `api.route(method, pattern, handler, max_body=65536, token=True, content_types=("application/json",))`

The route is mounted at `/api/plugins/<name>/<pattern>`.

- `method` is `GET`, `POST`, `PUT` or `DELETE`. A GET route also answers HEAD.
- In `pattern`, `<x>` matches one path segment and `<x:regex>` matches the regex. Both arrive in `req.params`.
- The core runs every guard before the handler: the Host check, the same-origin check, and for any method but GET the
  token, the content type (one of `content_types`; `()` accepts any) and a required `Content-Length` of at most
  `max_body` bytes (32 MiB at most).
- `token="optional-if-no-origin"` lets a request with no `Origin` and no `Sec-Fetch-Site` header skip the token. No
  browser sends such a request, so a web page still needs the token. Use it only for a client that can't read the
  token, such as Roblox Studio's HttpService.

The handler gets a request:

| Member | Meaning |
|---|---|
| `req.method` | The method. |
| `req.path` | The decoded path after the mount: `rig/hero` for a request to `/api/plugins/<name>/rig/hero`. |
| `req.full_path` | The whole decoded path, `/api/plugins/<name>/rig/hero`. |
| `req.params` | The pattern's parameters. |
| `req.query` | The query string, decoded; the last value of a repeated key wins. |
| `req.body` | The body as bytes, already read and capped. |
| `req.headers` | The request headers. |
| `req.json(expect=dict)` | The body parsed as JSON. Answers 400 when it is not JSON or not of type `expect`. |

It returns one of:

| Return | Answer |
|---|---|
| `api.json_response(obj, code=200)` | JSON. |
| `api.bytes_response(data, content_type="application/octet-stream", code=200)` | Bytes. |
| `api.Response(code, body, content_type, headers)` | Anything else. An `ETag` header lets a GET answer 304. |
| a dict or a list | JSON, 200. |
| bytes | `application/octet-stream`, 200. |
| `None` | 204. |

Raise `api.HttpError(code, message)` to answer `{"error": message}` with that code. Any other exception answers 500
`plugin <name> failed`, with the traceback on stderr under `--debug`.

### Checks: `api.check(fn)`

`fn(report)` runs during `--check`, after the core checks:

| Member | Meaning |
|---|---|
| `report.ok(msg)` | Print `ok: msg`. |
| `report.warn(msg)` | Print `warning: msg`. |
| `report.fail(msg)` | Print `FAIL: msg`. Any failure makes `--check` exit with 1. |
| `report.items`, `report.catalog` | A fresh scan: its items, and the whole catalog. |
| `report.config` | The validated config. |
| `report.project` | The project. |

An exception in `fn` is a failure.

## Client API

`client.js` exports `register(viewer)`. It may be async. The page runs it once, after the plugin's stylesheets are in.
If it throws, the page shows `Plugin <name> did not load` and works without the plugin.

| Member | Meaning |
|---|---|
| `viewer.name` | The plugin name. |
| `viewer.config` | The plugin's client config (`api.client_config`, else its merged config). |
| `viewer.addCategory({kind, label, icon, view, rowMeta})` | A nav category for a kind. `view` is a function that returns `import(...)` of a view module. `icon` names a theme token: `"anim"` draws `--icon-anim`. `rowMeta(item)` is the text at the right of a list row (default: the file size). |
| `viewer.addView(kind, loader)` | A view for a kind that already has a category. |
| `viewer.modelAddon(def)` | A model add-on (see below). |
| `viewer.keyHelp(text)` | A line in the key hints under the list. |
| `viewer.api(path, init)` | `fetch` of `/api/plugins/<name>/<path>`, with a timeout. For unsafe methods it adds the token and a JSON content type, and when the answer is 403 (the viewer was restarted and the page's token is stale) it reads the fresh token and sends the request once more. It returns the `fetch` Response. |

A category is shown only when the server also lists its kind, that is, when a server side adds it or it is a core kind.

### Modules a plugin may import

The page's import map gives these names:

| Import | What |
|---|---|
| `three`, `three/addons/...` | The vendored three.js (0.170.0). |
| `viewer/ui.js` | `el(tag, cls, text)`, `button`, `seg`, `stepper`, `sep`, `icon`, `activatable`, `url(path, mtime)` (the `/files/` URL of a project file), `fmtBytes`, `own(obj, key)` (an own property only, so a name like `constructor` finds nothing), and the stored settings (below). |
| `viewer/theme.js` | `token(name)`, `color(name)` (`{css, r, g, b, a}`), `num(name, fallback)`, `onChange(fn)` (after a theme reload; returns the unsubscribe). |
| `viewer/views/stage.js` | `getStage(container)`: the page's one three.js stage. `themeColor(name)`: a token as a three Color and its alpha. |
| `viewer/views/model.js` | `loadModel(path, mtime)`, `loadTexture(path, mtime, {srgb, repeat})`, `pbrMaterial(set, lookup, {alpha, doubleSided})`, `disposeObject(obj, skip)`, and the model view's `mount`. |

Everything else under `/app/` is internal and may change. Load your own files by their absolute URL,
`/plugins/<name>/<file>`, and list each of them in the manifest.

Stored settings live in `localStorage` under the project's `prefsPrefix`. `pref(key, fallback, valid)` returns the
stored value, or `fallback` when there is none, when the store is blocked, or when the value is not valid. Without
`valid`, a value is valid when it has the fallback's type (an array for an array; any value when the fallback is
`null`). `valid(v)` replaces that test: `oneOf(...values)`, `isString` and `isBoolean` are ready-made ones. For example
`pref("speed", 1, oneOf(0.5, 1, 2))`. `setPref(key, value)` stores a value.

### The view contract

A view module exports `mount(item, ctx)`. It may be async. It returns:

| Member | Meaning |
|---|---|
| `update(item)` | Show another item of the same kind, or a re-baked version of the same one. May be async. |
| `destroy()` | Free everything: listeners, timers, GPU resources. |
| `key(e)` | A `keydown` the page did not use. Return `true` when the view handled it. |

The page keeps one view at a time and calls `update` for the next item that uses the same loader. A mount that ends
after a newer request is destroyed, unseen. The review board's preview pane mounts the same views, with its own `ctx`.
The page handles Up, Down, `[`, `]`, `/`, `1`-`9` and `R` (when there are reviews) before a view sees a key. The review board also handles its
own keys first.

`ctx`:

| Member | Meaning |
|---|---|
| `ctx.view2d` | The element for a 2D view. Append to it. |
| `ctx.stage3d` | The container of the 3D stage: pass it to `getStage`. |
| `ctx.bar` | The view's toolbar under the view. Append controls to it. |
| `ctx.meta(text)` | Set the line under the item's name. |
| `ctx.item(path)` | The catalog item of a project path (its `mtime`, its maps), or `undefined`. |
| `ctx.catalogExtra(name)` | A plugin's `catalog_extra` data. |
| `ctx.url(path, mtime)` | The `/files/` URL of a project file, made unique by its mtime so a re-baked file loads fresh. |
| `ctx.theme` | The `viewer/theme.js` module. |
| `ctx.config` | The page config (`GET /api/config`). |
| `ctx.alive()` | `false` once the view's slot was dropped. |

A 3D view takes the stage with `const lease = getStage(ctx.stage3d).claim()`. Then `lease.show(obj, tick)` puts one
object on the stage. `tick(dt)` runs each frame; it returns `false` when nothing changed, so the stage draws only on
change. `lease.frame(box3, front)` frames a box, `lease.invalidate()` asks for a redraw, and `lease.release()` gives
the stage back. A newer claim releases the older lease, and the old lease's calls then do nothing. The stage also
exposes its `scene`, `camera`, `controls` (three's OrbitControls) and `renderer`.

### Model add-ons

A model add-on extends the core model view for some models, instead of replacing it:

```js
viewer.modelAddon({
  id: "my-addon",
  applies: (item) => !!item.ext?.["my-plugin"],   // which models it is for
  async prepare(api) { /* after the file loads, before it is shown */ },
  destroy() { /* when the next model loads or the view closes */ },
});
```

`applies` and `prepare` run for each model the view loads. An add-on with the same `id` replaces the earlier one. Loads
run one after another, so an add-on can keep its state in module variables between `prepare` and `destroy`.

`prepare(api)` gets:

| Member | Meaning |
|---|---|
| `item`, `obj`, `holder` | The catalog item, the loaded model's root object, and the group that holds it on the stage. |
| `stage`, `THREE` | The stage and three.js. |
| `loadModel`, `loadTexture`, `pbrMaterial`, `disposeObject`, `url`, `theme` | As exported by the public modules. |
| `materials.clay()`, `materials.glass()` | The view's themed clay and glass materials. |
| `config` | The page's `model` config (`front`, `glassNames`). The plugin's own config is `viewer.config`. |
| `lookup(path)` | The catalog item of a project path. |
| `lift` | How high the model's lowest point was before the view stood it on the ground. |
| `addLook(id, label, materialFor, {preferred})` | A look in the Look selector. `materialFor(mesh)` returns a Material, or `null` to keep the mesh's own. A `preferred` look is picked when a newly opened model can't keep the look the user had (or that look is Clay). |
| `addGroup(group)` | An object that looks, wireframe and disposal leave alone (props). The add-on frees it in `destroy`. |
| `addButton(label, key, onToggle, {on})` | A toggle in the add-on's part of the bar. `key` toggles it from the keyboard. |
| `addSeg(options, value, onChange, {key, label})` | A segmented control, `options` as `[[value, label], ...]`. `key` cycles it. |
| `setClipping(planes)` | Clipping planes for the model's meshes (`[]` for none). |
| `onTick(fn)` | `fn(dt)` each frame; it returns `false` when it changed nothing. |
| `onShown(fn)` | `fn()` once the model is on the stage and `lift` and `holder` are final. |
| `turnY(rad)` | Turn the model about Y. |
| `meta(parts)` | Replace the line under the item's name with these strings, or `null` for the default line. |

## Styles, colours and icons

- Colours come from theme tokens only (see [themes.md](themes.md)). A plugin can add tokens with default values inside
  `:where(:root) { ... }` in its stylesheet. That selector has no specificity, so a project theme's `:root` value
  wins. Outside that block, a bundled plugin may not contain colour literals; a static test checks this.
- In JS, read colours with `color()`, `num()` or `themeColor()`, and repaint in `onChange`.
- A category icon is the token `--icon-<icon>`: a `url()` of an SVG, drawn as a mask in the text colour. To ship one,
  list the SVG under `files` and declare `--icon-<icon>: url("/plugins/<name>/<file>.svg")` in `:where(:root)`.
- Build DOM with `el()` and `textContent`. `innerHTML`, `outerHTML`, `insertAdjacentHTML` and `document.write` are not
  allowed; a static test checks bundled plugins for them.

## Tests

A plugin in the tool's `plugins/` folder keeps its tests in `plugins/<name>/tests/test_*.py`.
`python -m unittest discover -s tests` runs them with the core tests: `tests/test_plugin_suites.py` finds them, since a
folder name with dashes can't be a Python package.

- Test file names must be unique across plugins, because they share one module namespace. Name them after the
  plugin: `test_rig_export.py`, `test_anim_limits.py`. A test checks this.
- Import the core's test helpers from the repository's `tests/` folder: `helpers.ProjectCase` makes a temporary project
  and tool folder, `make_app(config)` starts an `App` on it, `serve()` runs a real server on a free port and returns an
  HTTP client, and `catalog(app)` scans. Copy the plugin into `self.tool_dir/plugins/<name>` in `setUp`; the bundled
  plugins' tests show how.
- Every bundled plugin with a `server.py` must have tests.

The repository's test run does not find plugins in a project's `pluginDirs`. Run their tests yourself.

## Rules for a bundled plugin

A plugin sent to this repository also follows [CONTRIBUTING.md](../CONTRIBUTING.md): standard library Python, no build
step, an SPDX header in every source file, and a `README.md` with its config keys, item fields, keys and checks.
