# Contributing

Thank you for helping. This file lists the rules a change must follow. Most of them are checked by the tests.

To report a security problem, don't open a public issue: see [SECURITY.md](SECURITY.md).

## Licence

asset-viewer is under the GNU AGPL, version 3 or later. By sending a contribution you agree that it is licensed under
`AGPL-3.0-or-later`. Code copied from elsewhere must have a compatible licence, and you must say where it comes from.

## Ground rules

- **Standard library only.** The server and every tool run on Python 3.9 or newer with no third-party package. Don't
  use syntax newer than 3.9 (no `match`, no `zip(strict=)`, no `X | Y` type hints evaluated at run time).
- **No build step.** The client is plain ES modules loaded by the browser as they are. No bundler, no npm, no
  transpiler. Third-party code goes in `vendor/` through `tools/vendor_three.py`, pinned and hash-checked.
- **Offline.** The page loads nothing from the network: no CDN, no web font service, no analytics.
- **Tokens only.** No colour literals in `app/` or `plugins/`, in CSS or JS. Colours live in `themes/`. A plugin's own
  token defaults go in `:where(:root) { ... }`. A colour that is data (a rig part's colour) carries a
  `// colour from data` comment on its line. See [docs/themes.md](docs/themes.md).
- **No HTML sinks.** No `innerHTML`, `outerHTML`, `insertAdjacentHTML` or `document.write`. Build elements with `el()`
  and set text with `textContent`. No inline scripts or styles in `app/index.html`, except the import map.
- **SPDX header.** Every `.py`, `.js`, `.mjs`, `.css` and `.luau` file starts with
  `SPDX-License-Identifier: AGPL-3.0-or-later` in a comment within its first three lines.
- **Security stays as it is.** Every file read, served or written goes through the project's path checks
  (`viewer/project.py`). Every route goes through the core's guards (`viewer/http.py`). Every served type is in
  `viewer/filetypes.py`. Don't add a way to listen on another address, to serve folder listings, to serve dot files, or
  to write outside declared areas. Don't load project code without `--trust-project-plugins`. A change to the security
  model needs a test that shows the attack it stops.
- **No personal data.** No absolute paths of your machine, no e-mail addresses, no names of private projects. A test
  looks for the user name, git e-mail and home folder of the machine it runs on, and for common shapes of both.
- **Small files.** No file over 256 KiB outside `vendor/` and the fonts. The sample in `examples/sample` is generated
  by `tools/make_sample.py`; change the script, run it, and commit both.
- **Docs.** A change in behaviour updates `README.md`, `SECURITY.md`, `docs/config.md`, `docs/plugins.md`,
  `docs/themes.md`, the JSON schemas in `schemas/` or the plugin's README in the same change.

## Where things are

| Path | What |
|---|---|
| `serve.py` | The command line. |
| `viewer/app.py` | The running viewer: project, config reload, plugins, token file. |
| `viewer/config.py` | Config discovery and validation. |
| `viewer/catalog.py`, `viewer/check.py` | The scan of the roots, and `--check`. |
| `viewer/http.py`, `viewer/static.py`, `viewer/launch.py` | Routes and guards, file serving, start-up. |
| `viewer/project.py`, `viewer/filetypes.py` | Path checks and serving rules, and the one table of file types. |
| `viewer/picks.py`, `viewer/plugins.py` | Picks and reviews, and the plugin loader and API. |
| `app/` | The page: `app.js`, `review.js` and `review/`, `views/`, and the public modules. |
| `themes/`, `schemas/`, `plugins/` | Built-in themes, JSON schemas, bundled plugins. |
| `tools/` | `smoke.py` with its page in `tools/smoke/`, `vendor_three.py`, `make_sample.py`. |
| `tests/` | The unit tests and their `helpers.py`. |

## Tests

Run these from the repository root before you send a change:

```
python -m unittest discover -s tests
python serve.py --project examples/sample --check
python tools/smoke.py --no-webgl
python tools/vendor_three.py --verify
```

- The unit tests also run every plugin's tests, the static checks of the client (tokens, HTML sinks, imports,
  offline), the schemas against what the viewer accepts, the vendored files against their manifests, and the hygiene
  checks (licence, SPDX headers, personal data, file sizes).
- `tools/smoke.py` needs Chrome, Edge or Chromium (`--browser PATH` or the `CHROME` variable if it can't find one). Also
  run it without `--no-webgl` when you change a 3D view. Its test page lives in `tools/smoke/` and is mounted by the
  runner only: never make the viewer serve it.
- A bug fix comes with a test that fails without the fix.
- CI runs the unit tests and the sample check on Ubuntu and Windows with Python 3.9 and 3.13, and the smoke test on
  Ubuntu.

## Plugins

- A plugin lives in `plugins/<name>/` with its `plugin.json`, and follows [docs/plugins.md](docs/plugins.md).
- It imports only the public client modules (`viewer/ui.js`, `viewer/theme.js`, `viewer/views/stage.js`,
  `viewer/views/model.js`, `three`). If it needs more of the core, propose a public API in the same change.
- A plugin with a `server.py` has tests in `plugins/<name>/tests/`, with file names unique across plugins
  and named after the plugin (`test_anim_server.py`, `test_rig_export.py`).
- Its `README.md` lists its config keys and defaults, the items and data it adds, its routes, its keys and its checks.
  Examples use neutral names, not those of a particular project.
- Its routes keep the token unless the client can't send one, and then use `token="optional-if-no-origin"` with the
  reason in the README.
- It keeps its own data in its declared areas (`api.serve`, `api.writes`) and never writes anywhere else.
- It caps what it reads and prunes its caches: it lives as long as the viewer.

## Style

- Match the code around your change: its naming, its comment density, its idioms.
- Short functions. A comment says why, not what.
- Text the user sees is plain and short.
