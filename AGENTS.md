# AGENTS.md

This file applies to the entire repository. It is intended for Codex and other coding agents working on Pixelart Glyph
Creator.

## Project goal

Maintain a comfortable, professional, browser-first tool for designing and exhaustively exporting binary pixel-art
glyph families. The public application is hosted at:

<https://yegor-men.github.io/pixelart-glyph-creator/>

The product should remain approachable to people who do not program. Do not require users to edit source code, install
dependencies, create an account, or send their glyph data to a server.

## Runtime constraints

- This is a static GitHub Pages application: plain HTML, CSS, and JavaScript.
- There is no package manager, bundler, compilation step, framework, or backend.
- Keep asset URLs relative so the project works both at localhost and beneath the GitHub Pages project path.
- The app must run entirely in the browser. Do not introduce telemetry or upload configurations/glyphs.
- A local HTTP server is required because `generator.worker.js` cannot be loaded reliably from `file://`.
- `vendor/fflate.min.js` is a checked-in runtime dependency. Do not edit the minified file manually. If it is replaced
  or upgraded, update its license, version documentation, and archive/PNG regression checks.
- `.nojekyll` is intentional and should remain at the repository root.

## Architecture

| File | Responsibility |
|---|---|
| `index.html` | Semantic page structure, controls, status regions, and static copy. |
| `styles.css` | Responsive layout, component styling, interaction states, and accessibility affordances. |
| `app.js` | Application state, matrix editors, persistence, worker lifecycle, progress UI, previews, graph construction, and ZIP orchestration. |
| `generator.worker.js` | Exhaustive enumeration, kernel matching, blacklist pruning, whitelist validation, and per-glyph statistics. |
| `exporter.js` | Reusable CRC/PNG helpers and archive-name sanitisation. |
| `vendor/` | Vendored fflate compression runtime and its license. |
| `media/` | Project artwork and static README examples; not runtime-generated output. |

`generator.worker.js` and `exporter.js` deliberately expose CommonJS exports when loaded by Node. Preserve those exports:
they make dependency-free regression testing possible without changing the browser runtime.

## Data model and semantic invariants

Matrices are rectangular arrays containing only `-1`, `0`, and `1`.

- In the template, `-1` means flexible, `0` means forced empty, and `1` means forced filled.
- In a kernel, `-1` is a wildcard, while `0` and `1` must match exactly.
- A kernel placement is legal only when the complete kernel fits within the glyph.
- Any matching blacklist placement rejects the candidate.
- An empty whitelist imposes no requirement.
- A non-empty whitelist uses OR semantics: at least one placement of at least one whitelist kernel must match.
- Bitstrings are row-major and use `0`/`1` characters. They are stable identifiers and PNG filenames.
- Connected components use four-way orthogonal adjacency. Diagonal contact alone does not connect components.
- Graph neighbours have Hamming distance one. Write an undirected edge only once, when `source < target`.

Treat these as public behavior. If a requested change intentionally alters one, update the README, UI explanations,
export documentation, and regression expectations in the same change.

## Enumeration algorithm

For `F` flexible template cells, the theoretical search contains `2^F` assignments.

1. Flatten the template in row-major order.
2. Precompute each legal kernel anchor. Store only its non-wildcard checks plus the maximum covered row-major index.
3. Group blacklist anchors by that maximum index. At that point in the depth-first search, every cell the anchor covers
   is known, so the anchor can be tested safely.
4. Visit positions recursively. Fixed template values have one branch; flexible positions visit `0` and then `1`.
5. If a blacklist anchor matches, prune the subtree. Add `2^(remaining flexible positions)` to processed progress so
   progress still reaches the theoretical total.
6. At a leaf, require a whitelist match when the whitelist is non-empty. Record the bitstring, filled count, and
   orthogonal component count for survivors.

Do not move exhaustive enumeration onto the main thread. The worker keeps editing, progress, and cancellation
responsive. Cancellation during enumeration is implemented by terminating the worker; cancellation during packaging
is checked between batches.

## Output invariants

Every completed ZIP contains a single directory named from the sanitised archive name, with:

- `glyphs/<bitstring>.png`
- `metadata.csv`
- `nodes.csv`
- `edges.csv`
- `settings.json`
- `README.txt`

PNGs are one-bit indexed images. Palette index 0 is the paper colour and index 1 is the ink colour. Dimensions are:

```text
pixel width  = (glyph width  + 2 × margin) × scale
pixel height = (glyph height + 2 × margin) × scale
```

Keep CSV headers and relative filenames stable unless an explicit format-version change is being made. Imported
configuration JSON should continue to tolerate unknown fields, because `settings.json` also contains run metadata.

## Performance guardrails

- The current soft confirmation limit is 10,000,000 theoretical assignments.
- The current hard UI limit is 268,435,456 theoretical assignments.
- Progress messages are throttled in the worker; do not post a message for every candidate.
- PNG and graph packaging yields back to the browser in batches. Preserve that behavior for large exports.
- Avoid retaining canvases or decoded pixel buffers for every glyph. The compact PNG encoder and sampled preview are
  intentional memory controls.
- Remember that blacklist pruning improves observed speed but does not make huge unconstrained spaces predictably safe.

## UI and accessibility expectations

- Keep the primary workflow obvious: edit rules, render, see progress, receive ZIP.
- Preserve keyboard-operable controls, visible focus styles, semantic labels, live status/error regions, and reduced
  motion handling.
- Test both the two-column desktop layout and the single-column mobile layout.
- Do not expose internal terminology without nearby plain-language help.
- Keep destructive configuration actions explicit and cancellable where appropriate.
- Configuration is persisted under `glyph-foundry-configuration-v1`. If the shape changes incompatibly, add migration
  logic or deliberately version the key rather than breaking saved configurations silently.

## Verification

At minimum, syntax-check all JavaScript changed in a task:

```bash
node --check app.js
node --check exporter.js
node --check generator.worker.js
```

Important regression counts using the default four blacklist kernels are:

- Open 4×4 template: `5,208`
- Cornered 5×5 template: `29,130`
- Cornered 3×5 template: `294`

For generator changes, use the CommonJS export from `generator.worker.js` to confirm these counts and compare
bitstrings—not only totals—when reference data is available. For exporter changes, decode at least one PNG and test a
ZIP with `unzip -t` or an equivalent archive reader.

For UI or integration changes:

1. Start a local server, for example `python3 -m http.server 8000`.
2. Run the default 4×4 configuration in a real browser.
3. Confirm progress reaches 100%, cancellation remains usable, 5,208 glyphs are reported, the preview renders, and the
   ZIP can be downloaded and opened.
4. Check a narrow mobile viewport and a desktop viewport.

## Contribution hygiene

- Do not commit `.idea/`, downloaded ZIPs, or generated glyph folders.
- Do commit all runtime files in `vendor/` together with their licenses.
- Avoid adding a build system for a small change. If a build system or framework is genuinely required, document why
  static source files are no longer sufficient and preserve a one-command local workflow.
- Keep the README synchronized with visible behavior and archive contents.
- Preserve unrelated user changes in a dirty working tree.
- Run `git diff --check` before handing work back.
