# Security

asset-viewer is a local tool: a web server for one user on one computer. This file says what it protects, what it
doesn't, and how to report a problem.

## Reporting a vulnerability

Please report privately, through GitHub's private vulnerability reporting:
[Report a vulnerability](https://github.com/NoxLoveYa/asset-viewer/security/advisories/new) on
`NoxLoveYa/asset-viewer`. If that is not available to you, open an
[issue](https://github.com/NoxLoveYa/asset-viewer/issues) that asks for a private channel, without the details.

Include the version (`python serve.py --version`), your OS and browser, the steps or a request that shows the problem,
and what an attacker gains. You will get an answer as soon as possible. A fix comes with a test, and the advisory
credits you unless you ask otherwise.

Only the latest release and the `main` branch get fixes.

## The model

The viewer assumes that the person who runs it, and every program running as that user, is trusted. It defends against:

- **web pages** in the same browser, which can send requests to `127.0.0.1` (cross-site requests, DNS rebinding);
- **project files** that are not code: a model, a texture, an SVG, a JSON file or a config from a project you opened;
- **project code** you did not choose to run: plugins in a project's `pluginDirs`.

It does not defend against other local users or malware running as you, and it is not meant to be reachable from
another computer.

## What protects you

- **Localhost only.** The server listens on `127.0.0.1`, and there is no option to change that. A request whose `Host`
  is not `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` gets 421, which stops DNS rebinding.
- **Same origin.** An API request with a foreign `Origin` or a cross-site `Sec-Fetch-Site` gets 403.
- **Per-run token.** Each run makes a random token. Every write (POST, PUT, DELETE) needs it in `X-Viewer-Token`, with a
  JSON `Content-Type` and a capped `Content-Length` (chunked bodies are refused). The page gets the token in its HTML.
  Local tools get it from a file in a per-user state folder (`%LOCALAPPDATA%\asset-viewer` on Windows;
  `$XDG_RUNTIME_DIR`, `$XDG_STATE_HOME` or `~/.local/state` under `asset-viewer` elsewhere; the temp folder last;
  `ASSET_VIEWER_STATE_DIR` overrides). The file is named `<project hash>.token`, made readable by your user only where
  the system has Unix permissions, never put in the project, printed at start-up and deleted on exit. A plugin route
  may skip the token only for requests that carry neither `Origin` nor `Sec-Fetch-Site`, which no browser sends.
- **What is served.** The tool's own files, the chosen theme's folder, and at `/files/` only project files that are:
  - inside the config's `roots`, the two data files, or a folder a plugin serves;
  - of a listed kind (`.fbx .glb .gltf .obj .png .jpg .jpeg .webp .gif .svg .ogg .mp3 .wav .flac`) or a data type
    (`.json .txt .mtl .bin`);
  - not a dot file and not under a dot folder (a served area that is itself in a dot folder is allowed, only that area);
  - not matched by `hide`, and not reached through a symlink that leads outside these rules or the project.
  There are no folder listings.
- **What is written.** Only the picks file and the areas a plugin declares, inside the project, through a temp file and
  a rename. The token file is the only write outside the project. A picks file that is not valid JSON is never
  overwritten.
- **Headers.** The page has a strict Content-Security-Policy: scripts from the viewer only, and no inline code but the
  import map, by hash. Everything that is not the tool's own file (project files, a project theme, API answers, errors)
  carries `Content-Security-Policy: sandbox; default-src 'none'`, so an opened SVG can't run script on the viewer's
  origin. Every answer has `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Resource-Policy: same-origin` and `X-Frame-Options: DENY`.
- **Plugin code.** The bundled plugins in the tool's `plugins/` folder always load. Plugins in a project's `pluginDirs`
  are Python code from the project and run with your rights, so they load only when you start the viewer with
  `--trust-project-plugins`. Plugin code is imported once, at start (never again from disk while the viewer runs), and is never downloaded.
- **Network.** The page loads nothing from the network. The server makes no outgoing request, except the
  `roblox-rig-export` thumbnail route, which only fetches from `https://thumbnails.roblox.com` and `https://*.rbxcdn.com`.

## In scope

- Reading a file that the rules above should refuse, or writing outside the declared areas.
- A web page that can read the API, write picks or reach a plugin route.
- Script running on the viewer's origin from a project file, a theme or a config.
- Project code that runs without `--trust-project-plugins`.
- A crash, hang or unbounded memory use caused by a request or a project file.

## Out of scope

- Anything that needs code running as your user already: such code can read the token file or the project directly.
- Code in a plugin you chose to trust, bundled or loaded with `--trust-project-plugins`.
- Exposing the viewer to a network by proxying or port forwarding.
