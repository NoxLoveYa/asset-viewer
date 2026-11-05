# roblox-rig-export

Takes a rig exported from Roblox Studio into the rigs folder that the `roblox-anim` plugin plays clips on. It needs
`roblox-anim` turned on, and writes the rigs to its `rigsDir` (default `<config folder>/rigs`).

```json
{"plugins": {"roblox-anim": {"rigsDir": "rigs"}, "roblox-rig-export": {}}}
```

## Export a rig

With the viewer running, run `export_rig.luau` in Studio, in Edit or Server (for example with the `execute_luau` tool
of a Studio MCP server). Put the source after the globals it reads:

```lua
NAME = "hero"                                   -- the rig's file name: <rigsDir>/hero.json
MODEL = game.StarterPlayer.StarterCharacter     -- the Model to export
ROOT = "HumanoidRootPart"                       -- the name of its root part
GRIP = nil                                      -- a held item: the character's grip Attachment it joins
MESHES = nil                                    -- a model whose same-named MeshParts lend their meshes
MESH = nil                                      -- a prop: the model file in the project that draws over its boxes
PORT = 8765                                     -- only when the viewer runs on another port
```

- A character: `NAME`, `MODEL` and `ROOT`; the rest `nil`.
- A held item, such as `NAME = "pistol"`: also `GRIP = game.StarterPlayer.StarterCharacter.RightHand.RightGripAttachment`.
  With `MESH = "assets/models/pistol.fbx"` the item's meshes stay out of the rig (a big one would pass Studio's 1 MB
  post limit), and the viewer draws that file's meshes and maps over the rig's boxes.
- A model whose own meshes Studio may not read: `MESHES` names a model with same-named MeshParts to borrow from.

The script returns the viewer's answer and a list of warnings (a mesh it could not read, an image it could not save).
It changes nothing in Studio.

## The rig file

`<rigsDir>/<NAME>.json` is `{root, parts, joints, clothing?, grip?, mesh?}`:

- `parts`: size, colour, rest CFrame from the root, optional `mesh` arrays, `texture` or `maps`, `decals`;
- `joints`: Part1 name to `{parent, c0, c1}`;
- CFrames are the 12 numbers of `CFrame:GetComponents()`.

Images are `<rigsDir>/<NAME>/<id>.png`.

## Routes

Mounted at `/api/plugins/roblox-rig-export/`:

| Route | Body | Writes |
|---|---|---|
| `POST rig/<name>` | the rig JSON, at most 8 MiB | `<rigsDir>/<name>.json` |
| `POST rig/<name>/<file>.png?w=&h=&part=&parts=` | one chunk of base64 RGBA rows, at most 1 MiB | `<rigsDir>/<name>/<file>.png` when the last part is in |
| `POST rig/<name>/<file>.png?thumb=<asset id>` | empty | Roblox's thumbnail of that asset |

`name` is `[A-Za-z_][A-Za-z0-9_]{0,40}` and `file` is `[A-Za-z0-9_]{1,60}`. A name that Windows reserves (`con`, `nul`,
`aux`, `com1`...) is refused with 400.

Limits, all checked before anything is written (a refusal is a 400 unless noted):

- A rig has `parts` and `joints` objects, at most 4096 parts, every part and joint an object, and a `root` (when given)
  that names a part. A rig with NaN or Infinity, which JSON can't carry, is refused.
- An image is at most 4096 x 4096 pixels and 64 chunks. `w`, `h` and `parts` must stay the same for every chunk of one
  image, and the chunks must add up to exactly `w * h * 4` bytes.
- A partial upload expires after 5 minutes without a chunk. At most 8 are pending and 192 MiB held at once (429 past
  that). Pending uploads live as long as the viewer runs: a config edit does not drop them.
- A thumbnail is fetched only through `https://thumbnails.roblox.com`, and its image only from `https://*.rbxcdn.com`
  (redirects are checked the same way). It must be `Completed` (not the placeholder, else 409), start with the PNG
  signature and be at most 16 MiB. This is the only network access of the viewer.
- Every write is atomic (temp file and rename) and stays inside the rigs folder.

## Security

Studio's HttpService sends no `Origin` header and can't read the viewer's per-run token, so these routes take its
requests without the token (`token="optional-if-no-origin"`). A browser always sends `Origin` or `Sec-Fetch-Site`, so a
web page still needs the token and is refused without it. The core's Host check, same-origin check and body caps apply as
usual.

## Tests

`python -m unittest discover -s tests` runs this plugin's tests with the rest of the repository's.
