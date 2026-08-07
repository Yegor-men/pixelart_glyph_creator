"use strict";

(() => {
  const STORAGE_KEY = "glyph-foundry-configuration-v1";
  const SOFT_ASSIGNMENT_LIMIT = 10_000_000n;
  const HARD_ASSIGNMENT_LIMIT = 268_435_456n;
  const MAX_PREVIEW_GLYPHS = 72;
  const textEncoder = new TextEncoder();

  const PRESETS = {
    "open-4x4": Array.from({ length: 4 }, () => Array(4).fill(-1)),
    "corners-5x5": [
      [1, -1, -1, -1, 1],
      [-1, -1, -1, -1, -1],
      [-1, -1, -1, -1, -1],
      [-1, -1, -1, -1, -1],
      [1, -1, -1, -1, 1],
    ],
    "corners-3x5": [
      [1, -1, 1],
      [-1, -1, -1],
      [-1, -1, -1],
      [-1, -1, -1],
      [1, -1, 1],
    ],
  };

  const DEFAULT_STATE = {
    preset: "open-4x4",
    template: PRESETS["open-4x4"],
    blacklistedKernels: [
      [[1, 0], [0, 1]],
      [[0, 1], [1, 0]],
      [[1, 1], [1, 1]],
      [[0, 0], [0, 0]],
    ],
    whitelistedKernels: [],
    output: {
      archiveName: "pixelart-glyphs-4x4",
      scale: 20,
      margin: 1,
      inkColor: "#000000",
      paperColor: "#ffffff",
    },
  };

  const ids = [
    "addBlacklistButton", "addWhitelistButton", "archiveName", "assignmentEstimate",
    "blacklistList", "cancelButton", "clearTemplateButton", "downloadAgainButton",
    "emptyPreview", "errorMessage", "estimateCard", "estimateDetail", "exportConfigButton",
    "imageMargin", "importConfigButton", "importConfigInput", "inkColor", "inkColorLabel",
    "paperColor", "paperColorLabel", "pixelScale", "presetSelect", "previewGrid", "previewMeta",
    "progressBar", "progressBlock", "progressCount", "progressPercent", "progressPhase",
    "progressTrack", "renderButton", "resetButton", "runSubtitle", "runTitle", "statElapsed",
    "statRejected", "statValid", "statusDot", "templateGrid", "templateHeight", "templateWidth",
    "toast", "validCount", "whitelistList",
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));

  let state = loadState();
  let activeRun = null;
  let latestDownload = null;
  let toastTimer = 0;
  let saveTimer = 0;
  let dragPaint = null;

  initialise();

  function initialise() {
    syncAllControls();
    renderTemplate();
    renderKernelLists();
    updateEstimate();
    bindEvents();
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function isMatrix(value) {
    return Array.isArray(value)
      && value.length >= 1
      && value.length <= 10
      && value.every((row) => Array.isArray(row) && row.length === value[0].length && row.length >= 1 && row.length <= 10)
      && value.every((row) => row.every((cell) => [-1, 0, 1].includes(cell)));
  }

  function normaliseState(candidate) {
    const fallback = clone(DEFAULT_STATE);
    if (!candidate || !isMatrix(candidate.template)) return fallback;
    fallback.template = candidate.template.map((row) => row.slice());
    fallback.preset = Object.hasOwn(PRESETS, candidate.preset) ? candidate.preset : "custom";
    if (Array.isArray(candidate.blacklistedKernels) && candidate.blacklistedKernels.every(isMatrix)) {
      fallback.blacklistedKernels = clone(candidate.blacklistedKernels);
    }
    if (Array.isArray(candidate.whitelistedKernels) && candidate.whitelistedKernels.every(isMatrix)) {
      fallback.whitelistedKernels = clone(candidate.whitelistedKernels);
    }
    if (candidate.output && typeof candidate.output === "object") {
      fallback.output.archiveName = GlyphExport.sanitizeArchiveName(candidate.output.archiveName || fallback.output.archiveName);
      fallback.output.scale = clampInteger(candidate.output.scale, 1, 64, 20);
      fallback.output.margin = clampInteger(candidate.output.margin, 0, 12, 1);
      if (/^#[0-9a-f]{6}$/i.test(candidate.output.inkColor)) fallback.output.inkColor = candidate.output.inkColor.toLowerCase();
      if (/^#[0-9a-f]{6}$/i.test(candidate.output.paperColor)) fallback.output.paperColor = candidate.output.paperColor.toLowerCase();
    }
    return fallback;
  }

  function loadState() {
    try {
      return normaliseState(JSON.parse(localStorage.getItem(STORAGE_KEY)));
    } catch (_) {
      return clone(DEFAULT_STATE);
    }
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (_) { /* private browsing may deny storage */ }
    }, 180);
  }

  function bindEvents() {
    elements.presetSelect.addEventListener("change", () => applyPreset(elements.presetSelect.value));
    elements.templateWidth.addEventListener("change", resizeTemplateFromInputs);
    elements.templateHeight.addEventListener("change", resizeTemplateFromInputs);
    elements.clearTemplateButton.addEventListener("click", () => {
      state.template = state.template.map((row) => row.map(() => -1));
      state.preset = "custom";
      renderTemplate();
      configurationChanged();
    });

    elements.addBlacklistButton.addEventListener("click", () => addKernel("blacklistedKernels"));
    elements.addWhitelistButton.addEventListener("click", () => addKernel("whitelistedKernels"));
    elements.renderButton.addEventListener("click", startRun);
    elements.cancelButton.addEventListener("click", cancelRun);
    elements.downloadAgainButton.addEventListener("click", downloadLatestArchive);

    elements.archiveName.addEventListener("input", () => {
      state.output.archiveName = elements.archiveName.value;
      configurationChanged(false);
    });
    elements.archiveName.addEventListener("change", () => {
      state.output.archiveName = GlyphExport.sanitizeArchiveName(elements.archiveName.value);
      elements.archiveName.value = state.output.archiveName;
      configurationChanged(false);
    });
    bindNumericOutput(elements.pixelScale, "scale", 1, 64);
    bindNumericOutput(elements.imageMargin, "margin", 0, 12);
    bindColorOutput(elements.inkColor, elements.inkColorLabel, "inkColor");
    bindColorOutput(elements.paperColor, elements.paperColorLabel, "paperColor");

    elements.exportConfigButton.addEventListener("click", exportConfiguration);
    elements.importConfigButton.addEventListener("click", () => elements.importConfigInput.click());
    elements.importConfigInput.addEventListener("change", importConfiguration);
    elements.resetButton.addEventListener("click", resetConfiguration);

    window.addEventListener("pointerup", () => { dragPaint = null; });
    window.addEventListener("pointercancel", () => { dragPaint = null; });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !activeRun && !elements.renderButton.disabled) {
        event.preventDefault();
        startRun();
      }
    });
  }

  function bindNumericOutput(input, key, min, max) {
    input.addEventListener("change", () => {
      state.output[key] = clampInteger(input.value, min, max, state.output[key]);
      input.value = state.output[key];
      configurationChanged(false);
    });
  }

  function bindColorOutput(input, label, key) {
    input.addEventListener("input", () => {
      state.output[key] = input.value.toLowerCase();
      label.textContent = input.value.toUpperCase();
      configurationChanged(false);
    });
  }

  function clampInteger(value, min, max, fallback) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
  }

  function syncAllControls() {
    elements.presetSelect.value = state.preset;
    elements.templateWidth.value = state.template[0].length;
    elements.templateHeight.value = state.template.length;
    elements.archiveName.value = state.output.archiveName;
    elements.pixelScale.value = state.output.scale;
    elements.imageMargin.value = state.output.margin;
    elements.inkColor.value = state.output.inkColor;
    elements.paperColor.value = state.output.paperColor;
    elements.inkColorLabel.textContent = state.output.inkColor.toUpperCase();
    elements.paperColorLabel.textContent = state.output.paperColor.toUpperCase();
  }

  function applyPreset(name) {
    if (!PRESETS[name]) return;
    state.template = clone(PRESETS[name]);
    state.preset = name;
    const width = state.template[0].length;
    const height = state.template.length;
    state.output.archiveName = `pixelart-glyphs-${width}x${height}`;
    syncAllControls();
    renderTemplate();
    configurationChanged();
  }

  function resizeTemplateFromInputs() {
    const oldWidth = state.template[0].length;
    const oldHeight = state.template.length;
    const width = clampInteger(elements.templateWidth.value, 1, 10, oldWidth);
    const height = clampInteger(elements.templateHeight.value, 1, 10, oldHeight);
    state.template = resizeMatrix(state.template, width, height, -1);
    state.preset = "custom";
    if (/^pixelart-glyphs-\d+x\d+$/i.test(state.output.archiveName)) {
      state.output.archiveName = `pixelart-glyphs-${width}x${height}`;
    }
    syncAllControls();
    renderTemplate();
    configurationChanged();
  }

  function resizeMatrix(matrix, width, height, fill) {
    return Array.from({ length: height }, (_, row) => (
      Array.from({ length: width }, (_, column) => matrix[row]?.[column] ?? fill)
    ));
  }

  function renderTemplate() {
    renderMatrix(elements.templateGrid, state.template, (row, column, value) => {
      state.template[row][column] = value;
      state.preset = "custom";
      elements.presetSelect.value = "custom";
      configurationChanged();
    }, "Template");
  }

  function renderMatrix(container, matrix, onCellChange, label) {
    container.replaceChildren();
    const width = matrix[0].length;
    const height = matrix.length;
    const maxDimension = Math.max(width, height);
    const isTemplate = container === elements.templateGrid;
    const cellSize = isTemplate ? Math.max(22, Math.min(58, Math.floor(290 / maxDimension))) : Math.max(15, Math.min(31, Math.floor(100 / maxDimension)));
    container.style.setProperty("--cols", width);
    container.style.setProperty("--cell-size", `${cellSize}px`);

    matrix.forEach((rowValues, row) => rowValues.forEach((value, column) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `matrix-cell ${stateClass(value)}`;
      button.dataset.row = row;
      button.dataset.column = column;
      updateCellLabel(button, label, row, column, value);

      const applyValue = (nextValue) => {
        if (matrix[row][column] === nextValue) return;
        matrix[row][column] = nextValue;
        button.className = `matrix-cell ${stateClass(nextValue)}`;
        updateCellLabel(button, label, row, column, nextValue);
        onCellChange(row, column, nextValue);
      };

      button.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        const nextValue = nextCellState(matrix[row][column]);
        dragPaint = { matrix, value: nextValue, visited: new Set([`${row}:${column}`]) };
        applyValue(nextValue);
      });
      button.addEventListener("pointerenter", () => {
        if (!dragPaint || dragPaint.matrix !== matrix) return;
        const key = `${row}:${column}`;
        if (dragPaint.visited.has(key)) return;
        dragPaint.visited.add(key);
        applyValue(dragPaint.value);
      });
      button.addEventListener("click", (event) => {
        if (event.detail === 0) applyValue(nextCellState(matrix[row][column]));
      });
      container.append(button);
    }));
  }

  function nextCellState(value) {
    if (value === -1) return 1;
    if (value === 1) return 0;
    return -1;
  }

  function stateClass(value) {
    return value === 1 ? "state-on" : value === 0 ? "state-off" : "state-any";
  }

  function updateCellLabel(button, label, row, column, value) {
    const stateLabel = value === 1 ? "filled" : value === 0 ? "empty" : "flexible";
    button.setAttribute("aria-label", `${label}, row ${row + 1}, column ${column + 1}: ${stateLabel}`);
    button.title = `${stateLabel[0].toUpperCase()}${stateLabel.slice(1)} — click to change`;
  }

  function renderKernelLists() {
    renderKernelList("blacklistedKernels", elements.blacklistList, "Blacklist");
    renderKernelList("whitelistedKernels", elements.whitelistList, "Whitelist");
  }

  function renderKernelList(collectionKey, container, label) {
    const kernels = state[collectionKey];
    container.replaceChildren();
    if (kernels.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-kernels";
      empty.textContent = collectionKey === "whitelistedKernels"
        ? "No required motifs — every glyph that passes the blacklist is eligible."
        : "No forbidden motifs — glyphs will not be pruned by pattern.";
      container.append(empty);
      return;
    }

    kernels.forEach((kernel, index) => {
      const card = document.createElement("article");
      card.className = "kernel-card";
      card.innerHTML = `
        <div class="kernel-card-heading">
          <strong>${label} ${String(index + 1).padStart(2, "0")}</strong>
          <div class="kernel-actions">
            <button class="kernel-icon-button duplicate" type="button" aria-label="Duplicate ${label.toLowerCase()} kernel ${index + 1}" title="Duplicate kernel">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 8V3h13v13h-5v5H3V8h5Zm2 0h6v6h3V5h-9v3Zm4 2H5v9h9v-9Z"/></svg>
            </button>
            <button class="kernel-icon-button remove" type="button" aria-label="Remove ${label.toLowerCase()} kernel ${index + 1}" title="Remove kernel">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h10l-.7 12H7.7L7 9Zm3 2 .3 8h1L11 11h-1Zm3 0-.3 8h1l.3-8h-1Z"/></svg>
            </button>
          </div>
        </div>`;

      const matrixWrap = document.createElement("div");
      matrixWrap.className = "matrix-wrap";
      const matrixGrid = document.createElement("div");
      matrixGrid.className = "matrix-grid";
      matrixGrid.setAttribute("role", "grid");
      matrixGrid.setAttribute("aria-label", `${label} kernel ${index + 1}`);
      matrixWrap.append(matrixGrid);
      renderMatrix(matrixGrid, kernel, (row, column, value) => {
        state[collectionKey][index][row][column] = value;
        configurationChanged();
      }, `${label} kernel ${index + 1}`);

      const dimensions = document.createElement("div");
      dimensions.className = "kernel-dimensions";
      dimensions.innerHTML = `
        <label>W <input type="number" min="1" max="10" value="${kernel[0].length}" aria-label="Kernel width"></label>
        <label>H <input type="number" min="1" max="10" value="${kernel.length}" aria-label="Kernel height"></label>`;
      const [widthInput, heightInput] = dimensions.querySelectorAll("input");
      const resize = () => {
        const width = clampInteger(widthInput.value, 1, 10, kernel[0].length);
        const height = clampInteger(heightInput.value, 1, 10, kernel.length);
        state[collectionKey][index] = resizeMatrix(kernel, width, height, -1);
        renderKernelLists();
        configurationChanged();
      };
      widthInput.addEventListener("change", resize);
      heightInput.addEventListener("change", resize);
      card.querySelector(".duplicate").addEventListener("click", () => {
        state[collectionKey].splice(index + 1, 0, clone(kernel));
        renderKernelLists();
        configurationChanged();
      });
      card.querySelector(".remove").addEventListener("click", () => {
        state[collectionKey].splice(index, 1);
        renderKernelLists();
        configurationChanged();
      });
      card.append(matrixWrap, dimensions);
      container.append(card);
    });
  }

  function addKernel(collectionKey) {
    const kernel = collectionKey === "blacklistedKernels" ? [[1, 0], [0, 1]] : [[0, 1, 0], [1, 1, 1], [0, 1, 0]];
    state[collectionKey].push(kernel);
    renderKernelLists();
    configurationChanged();
    const list = collectionKey === "blacklistedKernels" ? elements.blacklistList : elements.whitelistList;
    list.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function configurationChanged(updateSearch = true) {
    scheduleSave();
    if (updateSearch) updateEstimate();
    if (latestDownload && !activeRun) elements.runSubtitle.textContent = "Settings changed — the available ZIP is from the previous run.";
  }

  function updateEstimate() {
    const flexible = state.template.flat().filter((value) => value === -1).length;
    const assignments = 1n << BigInt(flexible);
    elements.assignmentEstimate.textContent = formatBigInteger(assignments);
    elements.assignmentEstimate.title = assignments.toString();
    elements.estimateDetail.textContent = `${flexible} flexible cell${flexible === 1 ? "" : "s"}`;
    elements.estimateCard.classList.toggle("is-heavy", assignments > SOFT_ASSIGNMENT_LIMIT);
    const tooLarge = assignments > HARD_ASSIGNMENT_LIMIT;
    elements.renderButton.disabled = Boolean(activeRun) || tooLarge;
    if (tooLarge) {
      elements.assignmentEstimate.textContent = "Too large";
      elements.estimateDetail.textContent = `${formatBigInteger(assignments)} patterns · lock more cells`;
      elements.renderButton.title = "Lock more template cells to reduce the search space.";
    } else {
      elements.renderButton.removeAttribute("title");
    }
  }

  function formatBigInteger(value) {
    return value.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function formatCountString(value) {
    try { return formatBigInteger(BigInt(value)); } catch (_) { return String(value); }
  }

  function buildConfiguration() {
    return {
      template: clone(state.template),
      blacklistedKernels: clone(state.blacklistedKernels),
      whitelistedKernels: clone(state.whitelistedKernels),
      output: {
        archiveName: GlyphExport.sanitizeArchiveName(state.output.archiveName),
        scale: clampInteger(state.output.scale, 1, 64, 20),
        margin: clampInteger(state.output.margin, 0, 12, 1),
        inkColor: state.output.inkColor,
        paperColor: state.output.paperColor,
      },
    };
  }

  async function startRun() {
    if (activeRun) return;
    clearError();
    if (location.protocol === "file:") {
      showError("The generator worker needs a local web server. From this folder, run “python3 -m http.server 8000”, then open http://localhost:8000.");
      return;
    }
    const configuration = buildConfiguration();
    state.output.archiveName = configuration.output.archiveName;
    elements.archiveName.value = configuration.output.archiveName;
    scheduleSave();
    const flexible = configuration.template.flat().filter((value) => value === -1).length;
    const assignments = 1n << BigInt(flexible);
    if (assignments > SOFT_ASSIGNMENT_LIMIT && !window.confirm(`This run will examine ${formatBigInteger(assignments)} patterns. It may take a while. Continue?`)) return;

    const context = { cancelled: false, startedAt: performance.now(), worker: null, zip: null };
    activeRun = context;
    setRunningUi(true);
    setRunStatus("Enumerating patterns", "The rules are being applied in a background worker.", "running");
    setProgress("Enumerating patterns", 0, "0", assignments.toString(), 0);
    elements.statValid.textContent = "0";
    elements.statRejected.textContent = "—";
    elements.statElapsed.textContent = "0.0s";
    elements.previewGrid.replaceChildren();
    elements.emptyPreview.hidden = false;
    elements.previewMeta.textContent = "Valid glyphs will appear after enumeration.";

    try {
      const result = await runGeneratorWorker(configuration, context);
      if (context.cancelled) return;
      renderPreview(result.glyphs, result.summary.width, result.summary.height, configuration.output);
      elements.statValid.textContent = formatBigInteger(BigInt(result.glyphs.length));
      elements.statRejected.textContent = formatBigInteger(BigInt(result.summary.totalAssignments) - BigInt(result.glyphs.length));
      setRunStatus("Packaging archive", `${formatBigInteger(BigInt(result.glyphs.length))} glyphs passed every rule.`, "running");
      const blob = await buildArchive(result.glyphs, result.summary, configuration, context);
      if (context.cancelled) return;

      replaceLatestDownload(blob, `${configuration.output.archiveName}.zip`);
      const elapsed = performance.now() - context.startedAt;
      elements.statElapsed.textContent = formatDuration(elapsed);
      setProgress("Archive ready", 100, result.glyphs.length.toString(), result.glyphs.length.toString(), result.glyphs.length);
      setRunStatus(`${formatBigInteger(BigInt(result.glyphs.length))} glyphs ready`, `${latestDownload.name} · ${formatBytes(blob.size)}`, "ready");
      elements.downloadAgainButton.hidden = false;
      downloadLatestArchive();
      showToast(`Downloaded ${latestDownload.name}`);
    } catch (error) {
      if (!context.cancelled) showError(error instanceof Error ? error.message : String(error));
    } finally {
      if (activeRun === context) activeRun = null;
      setRunningUi(false);
      updateEstimate();
    }
  }

  function runGeneratorWorker(configuration, context) {
    return new Promise((resolve, reject) => {
      let worker;
      try {
        worker = new Worker("generator.worker.js");
      } catch (error) {
        reject(new Error(`Could not start the generator worker: ${error.message}`));
        return;
      }
      context.worker = worker;
      worker.onmessage = (event) => {
        const message = event.data;
        if (message.type === "progress") {
          setProgress("Enumerating patterns", message.percent, message.processed, message.total, message.valid);
          elements.statElapsed.textContent = formatDuration(performance.now() - context.startedAt);
        } else if (message.type === "complete") {
          worker.terminate();
          context.worker = null;
          resolve(message);
        } else if (message.type === "error") {
          worker.terminate();
          context.worker = null;
          reject(new Error(message.message));
        }
      };
      worker.onerror = (event) => {
        worker.terminate();
        context.worker = null;
        reject(new Error(event.message || "The generation worker stopped unexpectedly."));
      };
      worker.postMessage({ type: "generate", configuration });
    });
  }

  async function buildArchive(glyphs, summary, configuration, context) {
    if (typeof fflate === "undefined") throw new Error("The archive library did not load.");
    const chunks = [];
    let resolveArchive;
    let rejectArchive;
    const archivePromise = new Promise((resolve, reject) => {
      resolveArchive = resolve;
      rejectArchive = reject;
    });
    const zip = new fflate.Zip((error, data, final) => {
      if (error) {
        rejectArchive(error);
        return;
      }
      chunks.push(data.slice());
      if (final) resolveArchive(new Blob(chunks, { type: "application/zip" }));
    });
    context.zip = zip;
    const root = configuration.output.archiveName;

    const addText = (name, text, level = 6) => {
      const file = new fflate.ZipDeflate(`${root}/${name}`, { level });
      zip.add(file);
      file.push(textEncoder.encode(text), true);
    };
    const addBytes = (name, bytes) => {
      const file = new fflate.ZipPassThrough(`${root}/${name}`);
      zip.add(file);
      file.push(bytes, true);
    };

    const metadata = ["bitstring,width,height,filename\r\n"];
    const nodes = ["Id,Label,filled,components\r\n"];
    for (const glyph of glyphs) {
      metadata.push(`${glyph.bitstring},${summary.width},${summary.height},glyphs/${glyph.bitstring}.png\r\n`);
      nodes.push(`${glyph.bitstring},${glyph.bitstring},${glyph.filled},${glyph.components}\r\n`);
    }
    addText("metadata.csv", metadata.join(""));
    addText("nodes.csv", nodes.join(""));
    addText("settings.json", `${JSON.stringify({
      formatVersion: 1,
      generatedAt: new Date().toISOString(),
      template: configuration.template,
      blacklistedKernels: configuration.blacklistedKernels,
      whitelistedKernels: configuration.whitelistedKernels,
      output: configuration.output,
      summary,
    }, null, 2)}\n`);
    addText("README.txt", [
      "Glyph Foundry browser export",
      "============================",
      "",
      "glyphs/       Individual PNG glyphs named by their row-major bitstrings.",
      "metadata.csv  bitstring, width, height, and relative PNG filename.",
      "nodes.csv     Gephi-compatible node table with fill and component counts.",
      "edges.csv     Undirected one-bit-flip neighbours with weight 1.",
      "settings.json The exact rules used for this export.",
      "",
    ].join("\n"));

    const totalWork = Math.max(1, glyphs.length * 2);
    for (let index = 0; index < glyphs.length; index += 1) {
      if (context.cancelled) throw new Error("Run cancelled.");
      const glyph = glyphs[index];
      const png = GlyphExport.encodeGlyphPng({
        bitstring: glyph.bitstring,
        width: summary.width,
        height: summary.height,
        scale: configuration.output.scale,
        margin: configuration.output.margin,
        paperColor: configuration.output.paperColor,
        inkColor: configuration.output.inkColor,
        zlibSync: fflate.zlibSync,
      });
      addBytes(`glyphs/${glyph.bitstring}.png`, png);
      if (index % 32 === 0 || index + 1 === glyphs.length) {
        setProgress("Rendering PNG files", ((index + 1) / totalWork) * 100, (index + 1).toString(), glyphs.length.toString(), glyphs.length);
        await yieldToBrowser();
      }
    }

    const edgeFile = new fflate.ZipDeflate(`${root}/edges.csv`, { level: 6 });
    zip.add(edgeFile);
    edgeFile.push(textEncoder.encode("Source,Target,Weight\r\n"), false);
    const bitset = new Set(glyphs.map((glyph) => glyph.bitstring));
    let edgeBuffer = [];
    for (let glyphIndex = 0; glyphIndex < glyphs.length; glyphIndex += 1) {
      if (context.cancelled) throw new Error("Run cancelled.");
      const bitstring = glyphs[glyphIndex].bitstring;
      const bits = bitstring.split("");
      for (let bitIndex = 0; bitIndex < bits.length; bitIndex += 1) {
        bits[bitIndex] = bits[bitIndex] === "1" ? "0" : "1";
        const neighbour = bits.join("");
        bits[bitIndex] = bits[bitIndex] === "1" ? "0" : "1";
        if (bitstring < neighbour && bitset.has(neighbour)) edgeBuffer.push(`${bitstring},${neighbour},1\r\n`);
      }
      if (edgeBuffer.length >= 1024) {
        edgeFile.push(textEncoder.encode(edgeBuffer.join("")), false);
        edgeBuffer = [];
      }
      if (glyphIndex % 64 === 0 || glyphIndex + 1 === glyphs.length) {
        const completed = glyphs.length + glyphIndex + 1;
        setProgress("Building neighbour graph", (completed / totalWork) * 100, (glyphIndex + 1).toString(), glyphs.length.toString(), glyphs.length);
        await yieldToBrowser();
      }
    }
    if (edgeBuffer.length) edgeFile.push(textEncoder.encode(edgeBuffer.join("")), false);
    edgeFile.push(new Uint8Array(), true);
    zip.end();
    const blob = await archivePromise;
    context.zip = null;
    return blob;
  }

  function yieldToBrowser() {
    return new Promise((resolve) => window.requestAnimationFrame(resolve));
  }

  function renderPreview(glyphs, width, height, output) {
    elements.previewGrid.replaceChildren();
    elements.emptyPreview.hidden = glyphs.length > 0;
    if (glyphs.length === 0) {
      elements.previewMeta.textContent = "No patterns passed the current rules.";
      return;
    }
    const count = Math.min(MAX_PREVIEW_GLYPHS, glyphs.length);
    const samples = Array.from({ length: count }, (_, index) => {
      const sourceIndex = count === 1 ? 0 : Math.round((index * (glyphs.length - 1)) / (count - 1));
      return glyphs[sourceIndex];
    });
    for (const glyph of samples) {
      const item = document.createElement("div");
      item.className = "preview-item";
      item.title = `${glyph.bitstring} · ${glyph.filled} filled · ${glyph.components} component${glyph.components === 1 ? "" : "s"}`;
      const canvas = document.createElement("canvas");
      canvas.width = 96;
      canvas.height = 96;
      const context = canvas.getContext("2d");
      context.fillStyle = output.paperColor;
      context.fillRect(0, 0, canvas.width, canvas.height);
      const cellSize = Math.max(1, Math.floor(72 / Math.max(width, height)));
      const glyphWidth = width * cellSize;
      const glyphHeight = height * cellSize;
      const originX = Math.floor((canvas.width - glyphWidth) / 2);
      const originY = Math.floor((canvas.height - glyphHeight) / 2);
      context.fillStyle = output.inkColor;
      for (let row = 0; row < height; row += 1) {
        for (let column = 0; column < width; column += 1) {
          if (glyph.bitstring[row * width + column] === "1") {
            context.fillRect(originX + column * cellSize, originY + row * cellSize, cellSize, cellSize);
          }
        }
      }
      const code = document.createElement("code");
      code.textContent = glyph.bitstring;
      item.append(canvas, code);
      elements.previewGrid.append(item);
    }
    elements.previewMeta.textContent = `Showing ${count} evenly spaced samples of ${formatBigInteger(BigInt(glyphs.length))}.`;
  }

  function setProgress(phase, percent, completed, total, valid) {
    const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
    elements.progressBlock.hidden = false;
    elements.progressPhase.textContent = phase;
    elements.progressPercent.textContent = `${safePercent < 10 && safePercent > 0 ? safePercent.toFixed(1) : Math.round(safePercent)}%`;
    elements.progressBar.style.width = `${safePercent}%`;
    elements.progressTrack.setAttribute("aria-valuenow", String(Math.round(safePercent)));
    elements.progressCount.textContent = `${formatCountString(completed)} / ${formatCountString(total)}`;
    elements.validCount.textContent = `${formatBigInteger(BigInt(valid))} valid`;
  }

  function setRunStatus(title, subtitle, status) {
    elements.runTitle.textContent = title;
    elements.runSubtitle.textContent = subtitle;
    elements.statusDot.classList.toggle("is-running", status === "running");
    elements.statusDot.classList.toggle("is-error", status === "error");
  }

  function setRunningUi(running) {
    elements.cancelButton.hidden = !running;
    elements.renderButton.disabled = running;
    elements.renderButton.querySelector("span").textContent = running ? "Rendering…" : "Render & download";
    elements.downloadAgainButton.hidden = running || !latestDownload;
  }

  function cancelRun() {
    if (!activeRun) return;
    activeRun.cancelled = true;
    activeRun.worker?.terminate();
    activeRun.worker = null;
    activeRun.zip?.terminate?.();
    activeRun.zip = null;
    activeRun = null;
    setRunningUi(false);
    updateEstimate();
    setRunStatus("Run cancelled", "Your configuration is unchanged and ready to run again.", "ready");
    showToast("Generation cancelled");
  }

  function showError(message) {
    elements.errorMessage.textContent = message;
    elements.errorMessage.hidden = false;
    setRunStatus("Could not complete the run", "Review the message below, then try again.", "error");
  }

  function clearError() {
    elements.errorMessage.hidden = true;
    elements.errorMessage.textContent = "";
  }

  function replaceLatestDownload(blob, name) {
    if (latestDownload) URL.revokeObjectURL(latestDownload.url);
    latestDownload = { blob, name, url: URL.createObjectURL(blob) };
  }

  function downloadLatestArchive() {
    if (!latestDownload) return;
    const anchor = document.createElement("a");
    anchor.href = latestDownload.url;
    anchor.download = latestDownload.name;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }

  function exportConfiguration() {
    const configuration = buildConfiguration();
    const blob = new Blob([`${JSON.stringify(configuration, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${configuration.output.archiveName}-settings.json`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("Configuration exported");
  }

  async function importConfiguration(event) {
    const [file] = event.target.files;
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      state = normaliseState(parsed);
      syncAllControls();
      renderTemplate();
      renderKernelLists();
      configurationChanged();
      showToast("Configuration imported");
    } catch (error) {
      showError(`Could not import that configuration: ${error.message}`);
    }
  }

  function resetConfiguration() {
    if (!window.confirm("Reset the template, filters, and output options to their defaults?")) return;
    state = clone(DEFAULT_STATE);
    syncAllControls();
    renderTemplate();
    renderKernelLists();
    configurationChanged();
    showToast("Configuration reset");
  }

  function showToast(message) {
    clearTimeout(toastTimer);
    elements.toast.textContent = message;
    elements.toast.classList.add("is-visible");
    toastTimer = window.setTimeout(() => elements.toast.classList.remove("is-visible"), 2600);
  }

  function formatDuration(milliseconds) {
    if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
    if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)}s`;
    const minutes = Math.floor(milliseconds / 60_000);
    return `${minutes}m ${Math.round((milliseconds % 60_000) / 1000)}s`;
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  }
})();
