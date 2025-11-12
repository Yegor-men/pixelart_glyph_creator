![Banner](media/banner.png)
[![License](https://img.shields.io/badge/License-Apache%202.0-purple.svg)](https://www.apache.org/licenses/LICENSE-2.0)

# Pixelart Glyph Creator

A simple, procedural binary glyph creator, allowing to create arbitrary size glyphs with arbitrary size blacklist and
whitelist kernels.

To use, first run `generate_glyphs.py` to create the export folder `foo/` with all the glyphs as .png images and the
`metadata.csv` and Gephi compatible `nodes.csv` and `edges.csv` (edge between glyphs that are 1 bit flip away). Then run
`render_grid_from_export.py` to render a grid of glyphs from the export
folder.

Internally, the code uses 3 main things:

1. Template
2. Blacklist
3. Whitelist

The template dictates which pixels are fixed, how they must look like on the output end. The blacklist contains kernels
that mustn't be present anywhere in the glyph. The whitelist contains kernels that must be contained at least once in
the glyph. The template directly reduces the size of the binary tree, the blacklist allows for early pruning if it's
detected, and the whitelist is only checked at the very end.

`generate_glyphs.py` has two default templates: 5x5 and 3x5, both have corners filled (easier to spot), other templates
are simple to make. The list of lists should look like the image (kernels as well), 1 for filled in, 0 for unfilled and
-1 for the cells that the code can work with. Keep in mind that the size of the template, the amount of flexible bits
there are, directly affects the running time. The whitelist is empty by default, and the blacklist is set up to
eliminate thick gaps and lines, and to eliminate diagonal touching. Here you can see the results of the two default
templates.

| 5x5 glyph grid                                       | 3x5 glyph grid                                       |
|------------------------------------------------------|------------------------------------------------------|
| ![5 by 5 glyph grid](media/5x5_glyph_10x10_grid.png) | ![3 by 5 glyph grid](media/3x5_glyph_10x10_grid.png) |

The produced images are dumped into a folder, the grid is useful to just quickly scan for aesthetics, or to pick out the
good ones since the default amount is likely to be much larger than needed. The 5x5 with filled corners and the default
blacklist and no whitelist produces a total of 29,130 glyphs, and while some glyphs may look similar to others, it's
more than enough. For example, experts in Chinese may know up to 10,000 characters, far more than ever needed in daily
life or even specialized fields of work, and even then you could effectively fit it 3 times into these glyphs.

The smaller, 3x5 glyphs, even with the restrictions of needing the corners filled, still produce a likely overkill
amount of 294 glyphs. Meaning you could fit in all the standard ASCII characters into these glyphs. Even the largest
character language in the world, Tamil with 247 characters, can be entirely fit into these glyphs.

`generate_glyphs.py` also produces Gephi compatible `nodes.csv` and `edges.csv`. Nodes are the glyphs, and edges are
only present for the nodes that are one bit flip away. It's pretty cool to look at, how the blacklist and kernel prune
the original graph, if there's any notable groups or unique nodes. Do `File > Import spreadsheet...` and import
`nodes.csv` first and then `edges.csv`. Recommended to color nodes by their degree, just for visualization. Here's the
graphs produced for the 5x5 and 3x5 glyphs from before.

| 5x5 Gephi graph                            | 3x5 Gephi graph                            |
|--------------------------------------------|--------------------------------------------|
| ![5 by 5 Gephi graph](media/5x5_gephi.png) | ![3 by 5 Gephi graph](media/3x5_gephi.png) |

The default blacklist and available templates are thus great for games, primarily for UI icons or status effect symbols,
but are also very good for conlangs or ciphers. Like discussed before, the default glyphs are likely more than
sufficient for any usecase. But you can always add more blacklists to be more specific, or add kernels to the whitelist
so that glyphs fit a certain aesthetic (say a 3x3 "circle") somewhere within them. Or change the template to force all
glyphs to share some common feature, like in the case here, filled corners.

Have fun!

![outro_image](media/handdrawn.png)