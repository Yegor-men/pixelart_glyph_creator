"use strict";

/**
 * Browser worker for exhaustive glyph generation.
 *
 * The implementation mirrors generate_glyphs.py: blacklist kernels prune a
 * branch as soon as their final cell is known, while whitelist kernels are
 * checked at complete candidates. This file also exposes CommonJS exports so
 * the core can be regression-tested with Node without a browser.
 */

const PROGRESS_INTERVAL_MS = 45;

function matrixDimensions(matrix, label) {
  if (!Array.isArray(matrix) || matrix.length === 0 || !Array.isArray(matrix[0]) || matrix[0].length === 0) {
    throw new Error(`${label} must contain at least one row and one column.`);
  }
  const width = matrix[0].length;
  if (matrix.some((row) => !Array.isArray(row) || row.length !== width)) {
    throw new Error(`${label} rows must all have the same length.`);
  }
  if (matrix.some((row) => row.some((value) => ![-1, 0, 1].includes(value)))) {
    throw new Error(`${label} may only contain -1, 0, and 1.`);
  }
  return { width, height: matrix.length };
}

function precomputeKernelAnchors(width, height, kernel) {
  const { width: kernelWidth, height: kernelHeight } = matrixDimensions(kernel, "Kernel");
  const anchors = [];
  for (let anchorRow = 0; anchorRow <= height - kernelHeight; anchorRow += 1) {
    for (let anchorColumn = 0; anchorColumn <= width - kernelWidth; anchorColumn += 1) {
      const checks = [];
      let maxIndex = -1;
      for (let row = 0; row < kernelHeight; row += 1) {
        for (let column = 0; column < kernelWidth; column += 1) {
          const index = (anchorRow + row) * width + anchorColumn + column;
          maxIndex = Math.max(maxIndex, index);
          if (kernel[row][column] !== -1) {
            checks.push([index, kernel[row][column]]);
          }
        }
      }
      anchors.push({ checks, maxIndex });
    }
  }
  return anchors;
}

function anchorMatches(bits, anchor) {
  for (const [index, expected] of anchor.checks) {
    if (bits[index] !== expected) return false;
  }
  return true;
}

function countComponents(bits, width, height) {
  const visited = new Uint8Array(bits.length);
  const queue = new Int32Array(bits.length);
  let components = 0;

  for (let start = 0; start < bits.length; start += 1) {
    if (bits[start] !== 1 || visited[start]) continue;
    components += 1;
    let head = 0;
    let tail = 0;
    queue[tail++] = start;
    visited[start] = 1;

    while (head < tail) {
      const index = queue[head++];
      const row = Math.floor(index / width);
      const column = index % width;
      const neighbours = [];
      if (row > 0) neighbours.push(index - width);
      if (row + 1 < height) neighbours.push(index + width);
      if (column > 0) neighbours.push(index - 1);
      if (column + 1 < width) neighbours.push(index + 1);
      for (const neighbour of neighbours) {
        if (bits[neighbour] === 1 && !visited[neighbour]) {
          visited[neighbour] = 1;
          queue[tail++] = neighbour;
        }
      }
    }
  }

  return components;
}

function enumerateGlyphs(configuration, progressCallback = () => {}) {
  const template = configuration.template;
  const { width, height } = matrixDimensions(template, "Template");
  const cellCount = width * height;
  const templateBits = template.flat();
  const blacklistedKernels = configuration.blacklistedKernels || [];
  const whitelistedKernels = configuration.whitelistedKernels || [];

  blacklistedKernels.forEach((kernel, index) => matrixDimensions(kernel, `Blacklist kernel ${index + 1}`));
  whitelistedKernels.forEach((kernel, index) => matrixDimensions(kernel, `Whitelist kernel ${index + 1}`));

  const blacklistMap = Array.from({ length: cellCount }, () => []);
  for (const kernel of blacklistedKernels) {
    for (const anchor of precomputeKernelAnchors(width, height, kernel)) {
      blacklistMap[anchor.maxIndex].push(anchor);
    }
  }

  const whitelistAnchors = [];
  for (const kernel of whitelistedKernels) {
    whitelistAnchors.push(...precomputeKernelAnchors(width, height, kernel));
  }

  const freeSuffix = new Int32Array(cellCount + 1);
  for (let index = cellCount - 1; index >= 0; index -= 1) {
    freeSuffix[index] = freeSuffix[index + 1] + (templateBits[index] === -1 ? 1 : 0);
  }

  const flexibleCells = freeSuffix[0];
  const totalAssignments = 1n << BigInt(flexibleCells);
  const bits = new Uint8Array(cellCount);
  for (let index = 0; index < cellCount; index += 1) {
    if (templateBits[index] === 1) bits[index] = 1;
  }

  const glyphs = [];
  let processed = 0n;
  let prunedAssignments = 0n;
  let lastProgressAt = 0;
  const startedAt = performance.now();

  function reportProgress(force = false) {
    const now = performance.now();
    if (!force && now - lastProgressAt < PROGRESS_INTERVAL_MS) return;
    lastProgressAt = now;
    const basisPoints = totalAssignments === 0n ? 10000n : (processed * 10000n) / totalAssignments;
    progressCallback({
      processed: processed.toString(),
      total: totalAssignments.toString(),
      valid: glyphs.length,
      pruned: prunedAssignments.toString(),
      percent: Number(basisPoints) / 100,
    });
  }

  function isBlacklistedAt(index) {
    for (const anchor of blacklistMap[index]) {
      if (anchorMatches(bits, anchor)) return true;
    }
    return false;
  }

  function skipBranch(position) {
    const skipped = 1n << BigInt(freeSuffix[position + 1]);
    processed += skipped;
    prunedAssignments += skipped;
    reportProgress();
  }

  function visit(position) {
    if (position === cellCount) {
      processed += 1n;
      let whitelistMatches = whitelistedKernels.length === 0;
      if (!whitelistMatches) {
        for (const anchor of whitelistAnchors) {
          if (anchorMatches(bits, anchor)) {
            whitelistMatches = true;
            break;
          }
        }
      }

      if (whitelistMatches) {
        let bitstring = "";
        let filled = 0;
        for (const bit of bits) {
          bitstring += bit ? "1" : "0";
          filled += bit;
        }
        glyphs.push({
          bitstring,
          filled,
          components: countComponents(bits, width, height),
        });
      }
      reportProgress();
      return;
    }

    const forcedValue = templateBits[position];
    if (forcedValue !== -1) {
      bits[position] = forcedValue;
      if (isBlacklistedAt(position)) {
        skipBranch(position);
      } else {
        visit(position + 1);
      }
      return;
    }

    bits[position] = 0;
    if (isBlacklistedAt(position)) skipBranch(position);
    else visit(position + 1);

    bits[position] = 1;
    if (isBlacklistedAt(position)) skipBranch(position);
    else visit(position + 1);

    bits[position] = 0;
  }

  reportProgress(true);
  visit(0);
  reportProgress(true);

  return {
    glyphs,
    summary: {
      width,
      height,
      flexibleCells,
      totalAssignments: totalAssignments.toString(),
      prunedAssignments: prunedAssignments.toString(),
      validGlyphs: glyphs.length,
      elapsedMs: performance.now() - startedAt,
    },
  };
}

if (typeof self !== "undefined" && typeof self.postMessage === "function") {
  self.onmessage = (event) => {
    if (!event.data || event.data.type !== "generate") return;
    try {
      const result = enumerateGlyphs(event.data.configuration, (progress) => {
        self.postMessage({ type: "progress", ...progress });
      });
      self.postMessage({ type: "complete", ...result });
    } catch (error) {
      self.postMessage({
        type: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    anchorMatches,
    countComponents,
    enumerateGlyphs,
    matrixDimensions,
    precomputeKernelAnchors,
  };
}
