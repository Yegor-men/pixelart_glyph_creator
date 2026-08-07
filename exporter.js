"use strict";

(function exposeExporter(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.GlyphExport = api;
}(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const PNG_SIGNATURE = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
  const CRC_TABLE = new Uint32Array(256);

  for (let number = 0; number < 256; number += 1) {
    let value = number;
    for (let bit = 0; bit < 8; bit += 1) {
      value = (value & 1) ? (0xedb88320 ^ (value >>> 1)) : (value >>> 1);
    }
    CRC_TABLE[number] = value >>> 0;
  }

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function writeUint32(target, offset, value) {
    target[offset] = (value >>> 24) & 0xff;
    target[offset + 1] = (value >>> 16) & 0xff;
    target[offset + 2] = (value >>> 8) & 0xff;
    target[offset + 3] = value & 0xff;
  }

  function concatBytes(parts) {
    const length = parts.reduce((total, part) => total + part.length, 0);
    const output = new Uint8Array(length);
    let offset = 0;
    for (const part of parts) {
      output.set(part, offset);
      offset += part.length;
    }
    return output;
  }

  function pngChunk(type, data) {
    const typeBytes = new TextEncoder().encode(type);
    const output = new Uint8Array(data.length + 12);
    writeUint32(output, 0, data.length);
    output.set(typeBytes, 4);
    output.set(data, 8);
    writeUint32(output, data.length + 8, crc32(concatBytes([typeBytes, data])));
    return output;
  }

  function hexToRgb(hex) {
    const match = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    if (!match) throw new Error(`Invalid colour: ${hex}`);
    return match.slice(1).map((part) => Number.parseInt(part, 16));
  }

  function fillPackedRange(buffer, rowOffset, start, length) {
    if (length <= 0) return;
    const end = start + length - 1;
    const firstByte = Math.floor(start / 8);
    const lastByte = Math.floor(end / 8);
    const startBit = start % 8;
    const endBit = end % 8;

    if (firstByte === lastByte) {
      const leftMask = 0xff >>> startBit;
      const rightMask = (0xff << (7 - endBit)) & 0xff;
      buffer[rowOffset + firstByte] |= leftMask & rightMask;
      return;
    }

    buffer[rowOffset + firstByte] |= 0xff >>> startBit;
    for (let byte = firstByte + 1; byte < lastByte; byte += 1) buffer[rowOffset + byte] = 0xff;
    buffer[rowOffset + lastByte] |= (0xff << (7 - endBit)) & 0xff;
  }

  /** Encode a glyph as a compact, standards-compliant 1-bit indexed PNG. */
  function encodeGlyphPng(options) {
    const {
      bitstring,
      width,
      height,
      scale,
      margin,
      paperColor,
      inkColor,
      zlibSync,
    } = options;
    if (typeof zlibSync !== "function") throw new Error("A zlib compressor is required to encode PNGs.");

    const pixelWidth = (width + margin * 2) * scale;
    const pixelHeight = (height + margin * 2) * scale;
    const rowBytes = Math.ceil(pixelWidth / 8);
    const scanlines = new Uint8Array((rowBytes + 1) * pixelHeight);
    const origin = margin * scale;

    for (let glyphRow = 0; glyphRow < height; glyphRow += 1) {
      const filledColumns = [];
      for (let glyphColumn = 0; glyphColumn < width; glyphColumn += 1) {
        if (bitstring[glyphRow * width + glyphColumn] === "1") filledColumns.push(glyphColumn);
      }
      if (filledColumns.length === 0) continue;

      for (let repeat = 0; repeat < scale; repeat += 1) {
        const pixelRow = origin + glyphRow * scale + repeat;
        const dataOffset = pixelRow * (rowBytes + 1) + 1;
        for (const glyphColumn of filledColumns) {
          fillPackedRange(scanlines, dataOffset, origin + glyphColumn * scale, scale);
        }
      }
    }

    const ihdr = new Uint8Array(13);
    writeUint32(ihdr, 0, pixelWidth);
    writeUint32(ihdr, 4, pixelHeight);
    ihdr[8] = 1; // one bit per palette index
    ihdr[9] = 3; // indexed colour
    const palette = new Uint8Array([...hexToRgb(paperColor), ...hexToRgb(inkColor)]);
    const compressed = zlibSync(scanlines, { level: 9 });

    return concatBytes([
      PNG_SIGNATURE,
      pngChunk("IHDR", ihdr),
      pngChunk("PLTE", palette),
      pngChunk("IDAT", compressed),
      pngChunk("IEND", new Uint8Array()),
    ]);
  }

  function sanitizeArchiveName(value) {
    const cleaned = String(value || "")
      .trim()
      .replace(/\.zip$/i, "")
      .replace(/[^a-z0-9._-]+/gi, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 64);
    return cleaned || "pixelart-glyphs";
  }

  return {
    concatBytes,
    crc32,
    encodeGlyphPng,
    fillPackedRange,
    hexToRgb,
    sanitizeArchiveName,
  };
}));
