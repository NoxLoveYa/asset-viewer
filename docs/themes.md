# Themes

A theme is **one CSS file that sets custom properties on `:root`**. The viewer's look (UI, 3D stage lights and grid,
waveforms, icons) comes only from these tokens, so a project can make the viewer match its own tools by overriding the
few that differ. There is no build step and no JSON-to-CSS conversion.

## How a theme loads

1. `themes/aero/theme.css` always loads first. It defines **every** token, so a missing override is never an error.
2. The project's theme loads after it and wins on any token it sets. Set it with the config key `theme`:
   - a built-in name: `"theme": "aero"` (the default) or `"theme": "paper"` (a light theme);
   - a path to a `.css` file inside the project: `"theme": "config/theme.css"`. The server serves that file's
     folder at `/theme/`, so `url()` values and `@font-face` rules relative to the file work. Only stylesheets, fonts
     and images are served from it, and never a dot file or a path the config's `hide` matches.
3. `app/base.css` (layout and components) and each plugin's stylesheet load last. They only read tokens.

The page reloads the theme by itself when the file changes (the config's `themeVersion` is its mtime): save the file and
the UI, the 3D stage and the waveforms repaint, without reloading the page.

## Make a theme that matches another project

1. Copy `themes/paper/theme.css` (light) or `themes/aero/theme.css` (dark) next to your project's own files.
2. Delete every token you don't want to change; a theme only has to hold what differs from aero.
3. Change the values. Colours can be any CSS colour syntax (`#hex`, `rgb(r g b / .5)`, `hsl()`, `oklch()`,
   `color-mix()`): the page reads them back through a canvas, so the 3D lights and the waveforms accept the same
   syntaxes as the CSS.
4. Point the config at it: `"theme": "path/to/theme.css"`.

A starting point that matches a game's UI kit, changing only the font, the accent and two icons:

```css
:root {
  --font: "My UI Font", system-ui, sans-serif;
  --accent: #e8a33d;
  --wave-head: #e8a33d;
  --wave-cut-handle: #e8a33d;
  --icon-model: url("/files/docs/ui/icons/backpack.svg");
  --icon-sound: url("/files/docs/ui/icons/radio.svg");
}
```

The check that the tokens really cover everything: `themes/paper/theme.css` restyles the whole viewer by overriding
tokens only, and `tests/test_static.py` fails if it leaves a colour token of aero unset.

## Rules

- **Tokens only.** No colour literals in `app/` or `plugins/` (CSS or JS). Colours live in `themes/`. Rig part colours
  and other data colours carry a `// colour from data` comment and are the only exception. A plugin may give its own
  tokens default values inside `:where(:root) { ... }`.
- **Opacity rides in the colour's alpha.** The stage grid's opacity is the alpha of `--stage-grid`
  (`rgb(255 255 255 / .07)`), the shadow's is the alpha of `--stage-shadow`, glass transparency the alpha of
  `--model-glass`.
- **Numbers** (light intensities) are plain numbers: `--stage-key-intensity: 2.2`.
- **Icons** are `url(...)` values drawn as masks in the current text colour (`mask-image` with `currentColor`), so they
  follow `--fg`, `--text-2`... Use **root-absolute** URLs (`/app/icons/x.svg`, `/files/<project path>`). A
  `/files/` icon must be inside one of the config's `roots`, like any project file the page loads. An SVG needs only a
  shape: its own colours are ignored.
- **Fonts** go in the theme file as `@font-face` rules with a `url()` relative to the file; the shipped Titillium Web
  and Roboto Mono are in `themes/aero/fonts/` (SIL OFL, licences in `OFL.txt`).
- A plugin declares its own tokens in `:where(:root) { ... }`. That selector has no specificity, so your `:root` value
  wins even though the plugin's stylesheet loads after yours.

## Token reference

Names are the custom property without `--`. JS reads them with `token("fg")`, `color("accent")` or
`num("stage-key-intensity", 2.2)` from `viewer/theme.js`; `color()` returns `{css, r, g, b, a}`.

| Group | Tokens |
|---|---|
| Surfaces | `page` (page edge), `world` (page centre), `glass` (inputs, cards), `glass-strong` (windows), `glass-line` (borders), `sheen` (the gradient laid over a window), `rhead-bg` (the review header) |
| Text and lines | `fg` (main text, selected borders, the filled button), `ink` (text on a `fg` fill), `text-2`, `text-3` (secondary, hints), `accent`, `danger`, `track` (slider track), `focus-fill` (selected row), `hover-fill`, `hover-line`, `seg-on` (the chosen segment), `done-line` (a picked group), `cand-line` (candidate badge), `scrollbar`, `text-shadow`, `focus-ring` (keyboard focus outline) |
| Shape, motion, type | `radius`, `radius-sm`, `quint` (easing), `font`, `mono`, `color-scheme` (`dark` or `light`, for native controls) |
| Images | `img-bg-dark`, `img-bg-light`, `img-checker-a`, `img-checker-b`, `img-bg-add` (the texture backgrounds) |
| 3D stage | `stage-sky`, `stage-ground`, `stage-hemi-intensity` (hemisphere light), `stage-key`, `stage-key-intensity`, `stage-rim`, `stage-rim-intensity`, `stage-env-intensity`, `stage-grid` (colour and alpha), `stage-shadow` (colour and alpha) |
| Models | `model-clay` (the Clay look), `model-glass` (glass meshes, alpha = opacity), `model-edge` (outline of box parts) |
| Waveforms | `wave-axis`, `wave-base` (sound view), `wave-kept` (review board), `wave-dim` (outside a cut), `wave-played`, `wave-head` (sound view playhead), `wave-playhead` (review playhead), `wave-cut-fill`, `wave-cut-handle` |
| Icons | `icon-model`, `icon-texture`, `icon-vfx`, `icon-sound`, `icon-review`, `icon-anim` (plugins add their own) |
