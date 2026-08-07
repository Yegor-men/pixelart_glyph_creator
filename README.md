![Banner](media/banner.png)

# Pixelart Glyph Creator

A procedural binary glyph workshop. Design a template, blacklist unwanted local patterns, whitelist required motifs,
and render every valid glyph — now from a polished, entirely client-side web app.

The browser edition is the recommended interface. The original Python tools remain available and unchanged in purpose
for scripted or batch workflows.

## Use the browser app

The live app is designed for GitHub Pages. Once Pages is enabled for this repository, it is available at:

<https://yegor-men.github.io/pixelart-glyph-creator/>

Everything runs on the device. No configuration or glyph data is sent to a server.

### Run locally

Clone the repository, serve its root with any static file server, and open the printed URL:

```bash
git clone https://github.com/Yegor-men/pixelart-glyph-creator.git
cd pixelart-glyph-creator
python3 -m http.server 8000
```

Then visit <http://localhost:8000>. A server is needed because browsers do not allow a Web Worker to load reliably from
a `file://` page. There is no install, build, package manager, or backend.

### Publish with GitHub Pages

1. Open the repository's **Settings → Pages**.
2. Under **Build and deployment**, choose **Deploy from a branch**.
3. Select the `main` branch and the `/ (root)` folder, then save.

The root `index.html` and relative asset paths are ready for project-site hosting.

## Browser workflow

1. Pick a preset or resize the glyph template.
2. Click or drag over cells to cycle between flexible (`-1`), filled (`1`), and empty (`0`).
3. Add, duplicate, resize, edit, or remove blacklist and whitelist kernels.
4. Choose PNG scale, margin, colours, and an archive name.
5. Select **Render & download**. Enumeration happens in a background worker, with live progress and immediate cancel.

The downloaded ZIP contains one top-level folder with:

```text
pixelart-glyphs-4x4/
  glyphs/           # PNGs named by row-major bitstrings
  metadata.csv      # bitstring,width,height,filename
  nodes.csv         # Gephi-compatible glyph nodes
  edges.csv         # undirected one-bit-flip neighbours
  settings.json     # exact reproducible browser configuration
  README.txt        # archive format notes
```

The interface automatically remembers the current configuration in the browser. Settings can also be imported or
exported as JSON. Searches above 10 million theoretical assignments require confirmation; searches above 268 million
are disabled until more template cells are fixed. The number of flexible cells controls the exponential search space.

## How the rules work

The generator uses three concepts:

1. **Template** — fixed pixels shared by every output. `1` is filled, `0` is empty, and `-1` is flexible.
2. **Blacklist** — if any blacklisted kernel matches anywhere, the glyph is rejected. `-1` inside a kernel is a wildcard.
3. **Whitelist** — when whitelist kernels exist, at least one of them must match somewhere in the glyph.

The template reduces the binary search tree directly. Blacklists allow early branch pruning, while whitelists are
checked on complete candidates. The browser implementation follows the same rules as `generate_glyphs.py`.

## Python workflow

The original scripts are deliberately retained:

```bash
python3 -m pip install pillow tqdm
python3 generate_glyphs.py
python3 render_grid_from_export.py 4x4 --cols 10 --rows 10
```

Edit `TEMPLATE`, `BLACKLISTED_KERNELS`, `WHITELISTED_KERNELS`, and `EXPORT_DIR` near the top of
`generate_glyphs.py`, then run it to produce the same PNG/CSV/Gephi data shape. `render_grid_from_export.py` creates a
random sample mosaic from an export folder.

## Examples

With filled corners, the default filters produce 29,130 valid 5×5 glyphs and 294 valid 3×5 glyphs.

| 5×5 glyph grid | 3×5 glyph grid |
|---|---|
| ![5 by 5 glyph grid](media/5x5_glyph_10x10_grid.png) | ![3 by 5 glyph grid](media/3x5_glyph_10x10_grid.png) |

`nodes.csv` and `edges.csv` can be imported into Gephi with **File → Import spreadsheet**. Edges connect glyphs that
are one bit flip apart.

| 5×5 Gephi graph | 3×5 Gephi graph |
|---|---|
| ![5 by 5 Gephi graph](media/5x5_gephi.png) | ![3 by 5 Gephi graph](media/3x5_gephi.png) |

These sets work well for game UI icons, status effects, conlangs, ciphers, and any project that needs a coherent family
of small symbols.

![Hand-drawn glyphs](media/handdrawn.png)

## Browser dependency

The repository vendors [fflate](https://github.com/101arrowz/fflate) 0.8.2 for local, offline ZIP/PNG compression. Its
MIT license is included in `vendor/fflate.LICENSE.txt`.
