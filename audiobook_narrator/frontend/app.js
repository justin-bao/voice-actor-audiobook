const state = {
  projects: [],
  chapters: [],
  project: null,
  selectedChapterId: null,
  memory: null,
  chapterMemory: null,
  annotations: [],
  annotatedText: "",
  cast: null,
  elevenVoices: [],
  audioManifest: null,
  audioUrl: null,
  audioPlaybackRate: 1,
  pendingDeleteChapterId: null,
  pendingDeleteBookId: null,
  pendingClearAnnotationsId: null,
  loadingElevenVoices: false,
  autoSaveTimer: null,
  autoSaving: false,
  progressPollTimer: null,
  pendingBulkImportFiles: [],
  busyJobs: new Map(),
  pipelineCanceled: false,
  generatingChapters: new Set(),
};

let previewAudio = null;
let previewingVoiceId = null;

let chapterAudio = null;
let audioTimings = [];
let activePassageIndex = -1;
let userIsSeeking = false;
let passageChunkMap = {};

function playVoicePreview(voiceId) {
  const voice = state.elevenVoices.find((v) => v.voice_id === voiceId);
  if (!voice?.preview_url) return;
  if (previewingVoiceId === voiceId && previewAudio && !previewAudio.paused) {
    previewAudio.pause();
    previewAudio.currentTime = 0;
    previewingVoiceId = null;
    updatePreviewButtons();
    return;
  }
  if (previewAudio) {
    previewAudio.pause();
    previewAudio.currentTime = 0;
  }
  previewAudio = new Audio(voice.preview_url);
  previewingVoiceId = voiceId;
  previewAudio.addEventListener("ended", () => {
    previewingVoiceId = null;
    updatePreviewButtons();
  });
  previewAudio.play();
  updatePreviewButtons();
}

function updatePreviewButtons() {
  document.querySelectorAll(".voice-preview-btn").forEach((btn) => {
    const voiceId = btn.dataset.voiceId
      || btn.closest(".voice-select-wrap")?.querySelector("select")?.value;
    const isPlaying = !!voiceId && voiceId === previewingVoiceId;
    btn.textContent = isPlaying ? "⏸" : "▶";
    btn.classList.toggle("playing", isPlaying);
  });
}

function formatTime(seconds) {
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds || 0) / 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function buildAudioTimings(totalDuration) {
  const passages = (state.audioManifest || []).flatMap((chunk) => chunk.passages || []);
  const totalChars = passages.reduce((sum, p) => sum + (p.text_chars || 1), 0);
  let cumulative = 0;
  audioTimings = passages.map((p) => {
    const chars = p.text_chars || 1;
    const start = (cumulative / totalChars) * totalDuration;
    cumulative += chars;
    const end = (cumulative / totalChars) * totalDuration;
    return { start, end };
  });
}

function updateAudioSeek() {
  const audio = chapterAudio;
  if (!audio) return;
  const duration = audio.duration || 0;
  const current = audio.currentTime || 0;
  if (!userIsSeeking) {
    const seekEl = $("audio-seek");
    if (seekEl) seekEl.value = duration ? Math.round((current / duration) * 10000) : 0;
  }
  const timeEl = $("audio-time");
  if (timeEl) timeEl.textContent = `${formatTime(current)} / ${formatTime(duration)}`;
}

function clearPassageHighlight() {
  activePassageIndex = -1;
  document.querySelectorAll(".inline-annotation-item.audio-active").forEach((el) => {
    el.classList.remove("audio-active");
  });
}

function updateActivePassage(currentTime) {
  const index = audioTimings.findIndex((t) => currentTime >= t.start && currentTime < t.end);
  if (index === activePassageIndex) return;
  activePassageIndex = index;
  document.querySelectorAll(".inline-annotation-item").forEach((el, i) => {
    el.classList.toggle("audio-active", i === index);
  });
  if (index >= 0) {
    document.querySelector(`.inline-annotation-item[data-index="${index}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function toggleAudioPlay() {
  if (!chapterAudio) return;
  const btn = $("audio-play-btn");
  if (chapterAudio.paused) {
    chapterAudio.play();
    if (btn) btn.textContent = "⏸";
  } else {
    chapterAudio.pause();
    if (btn) btn.textContent = "▶";
  }
}

function initAudioPlayer() {
  if (chapterAudio) {
    chapterAudio.pause();
    chapterAudio = null;
  }
  audioTimings = [];
  activePassageIndex = -1;
  userIsSeeking = false;

  chapterAudio = new Audio(state.audioUrl);
  chapterAudio.preload = "metadata";
  chapterAudio.playbackRate = state.audioPlaybackRate;

  chapterAudio.addEventListener("loadedmetadata", () => {
    buildAudioTimings(chapterAudio.duration);
    updateAudioSeek();
  });
  chapterAudio.addEventListener("timeupdate", () => {
    updateAudioSeek();
    updateActivePassage(chapterAudio.currentTime);
  });
  chapterAudio.addEventListener("ended", () => {
    const btn = $("audio-play-btn");
    if (btn) btn.textContent = "▶";
    clearPassageHighlight();
  });

  const playBtn = $("audio-play-btn");
  if (playBtn) playBtn.addEventListener("click", toggleAudioPlay);

  const regenBtn = $("audio-regen-btn");
  if (regenBtn) regenBtn.addEventListener("click", () => runStep("synthesize"));

  const speedSelect = $("audio-speed");
  if (speedSelect) {
    speedSelect.value = String(state.audioPlaybackRate);
    speedSelect.addEventListener("change", (event) => {
      state.audioPlaybackRate = Number(event.target.value) || 1;
      if (chapterAudio) chapterAudio.playbackRate = state.audioPlaybackRate;
    });
  }

  const seekEl = $("audio-seek");
  if (seekEl) {
    seekEl.addEventListener("mousedown", () => { userIsSeeking = true; });
    seekEl.addEventListener("touchstart", () => { userIsSeeking = true; });
    seekEl.addEventListener("mouseup", () => { userIsSeeking = false; });
    seekEl.addEventListener("touchend", () => { userIsSeeking = false; });
    seekEl.addEventListener("input", (e) => {
      const duration = chapterAudio?.duration || 0;
      if (chapterAudio) chapterAudio.currentTime = (Number(e.target.value) / 10000) * duration;
    });
  }
}

function buildPassageChunkMap() {
  passageChunkMap = {};
  (state.audioManifest || []).forEach((chunk) => {
    (chunk.passages || []).forEach((p) => {
      passageChunkMap[p.index] = chunk.chunk_index;
    });
  });
}

async function regenerateChunk(chunkIndex) {
  if (!state.project || !state.selectedChapterId) return;
  setBusy(true, "Regenerating", `Re-synthesizing chunk ${chunkIndex}…`);
  setStatus(`Regenerating chunk ${chunkIndex}…`);
  try {
    await api(
      `/api/projects/${encodeURIComponent(state.project.project_id)}/audio/${encodeURIComponent(state.selectedChapterId)}/regenerate-chunk`,
      { method: "POST", body: JSON.stringify({ chunk_index: chunkIndex, backend: "elevenlabs" }) }
    );
    await loadProject(state.project.project_id, state.selectedChapterId);
    setStatus(`Chunk ${chunkIndex} regenerated`);
  } catch (error) {
    setStatus(`Regen failed: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

function renderAudioPlayer() {
  if (chapterAudio) {
    chapterAudio.pause();
    chapterAudio = null;
  }
  clearPassageHighlight();
  const section = $("audio-player-section");
  if (!section) return;
  if (!state.audioManifest || !state.audioUrl) {
    section.hidden = true;
    section.innerHTML = "";
    return;
  }
  buildPassageChunkMap();
  const totalPassages = (state.audioManifest || []).reduce((n, c) => n + (c.passages?.length || 0), 0);
  const chunkCount = state.audioManifest.length;
  section.hidden = false;
  section.innerHTML = `
    <div class="audio-player">
      <button id="audio-play-btn" class="audio-play-btn" title="Play / pause">▶</button>
      <div class="audio-progress-wrap">
        <input id="audio-seek" class="audio-seek" type="range" min="0" max="10000" value="0" step="1" />
        <div class="audio-player-meta">
          <span id="audio-time" class="audio-time">0:00 / 0:00</span>
          <span class="audio-info">${totalPassages} passages · ${chunkCount} chunk${chunkCount !== 1 ? "s" : ""}</span>
        </div>
      </div>
      <label class="audio-speed-wrap" title="Playback speed">
        <span>Speed</span>
        <select id="audio-speed" class="audio-speed">
          <option value="0.75">0.75x</option>
          <option value="1">1x</option>
          <option value="1.25">1.25x</option>
          <option value="1.5">1.5x</option>
          <option value="2">2x</option>
        </select>
      </label>
      <button id="audio-regen-btn" class="audio-regen-btn" title="Regenerate audio for entire chapter">↺</button>
    </div>
  `;
  initAudioPlayer();
}

const $ = (id) => document.getElementById(id);

const emotions = ["neutral", "tense", "fearful", "angry", "tender", "grief", "wonder", "comic", "solemn", "urgent"];
const deliveries = ["matter-of-fact", "dramatic", "intimate", "reflective", "clipped", "lyrical", "conversational", "suspenseful"];
const paces = ["slow", "medium", "quick"];

function extractInlineTags(text) {
  return (text.match(/\[[a-z ]+\]/gi) || []);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) throw new Error(payload.error || response.statusText);
  return payload;
}

function setStatus(message) {
  $("status").textContent = message;
}

function setBusy(isBusy, title = "Working", detail = "Waiting for the model...") {
  if (isBusy) {
    upsertBusyJob("global", { title, detail });
  } else {
    removeBusyJob("global");
  }
}

function upsertBusyJob(jobId, { title, detail, chapterId = null, cancellable = false, controller = null } = {}) {
  state.busyJobs.set(jobId, { title, detail, chapterId, cancellable, controller, progress: state.busyJobs.get(jobId)?.progress || null });
  renderBusyJobs();
}

function removeBusyJob(jobId) {
  state.busyJobs.delete(jobId);
  renderBusyJobs();
}

function renderBusyJobs() {
  const overlay = $("busy-overlay");
  const jobs = [...state.busyJobs.entries()];
  overlay.hidden = jobs.length === 0;
  overlay.innerHTML = jobs.map(([jobId, job]) => `
    <div class="busy-box" data-job-id="${escapeAttr(jobId)}">
      <div class="spinner" aria-hidden="true"></div>
      <div class="busy-copy">
        <strong>${escapeHtml(job.title || "Working")}</strong>
        <p>${escapeHtml(job.detail || "Waiting for the model...")}</p>
        <div class="busy-progress-wrap" ${job.progress?.total_chunks ? "" : "hidden"}>
          <progress value="${Number(job.progress?.completed_chunks || 0)}" max="${Number(job.progress?.total_chunks || 1)}"></progress>
          <span>${job.progress ? busyProgressLabel(job.progress) : ""}</span>
        </div>
      </div>
      ${job.cancellable ? `<button class="busy-cancel" type="button" data-job-id="${escapeAttr(jobId)}">Cancel</button>` : ""}
    </div>
  `).join("");
}

function busyProgressLabel(progress) {
  const completed = Number(progress.completed_chunks || 0);
  const total = Number(progress.total_chunks || 0);
  return progress.phase === "complete"
    ? `${total} of ${total} chunks complete`
    : `Chunk ${Number(progress.current_chunk || Math.min(completed + 1, total))} of ${total} · ${completed} complete`;
}

function setBusyProgress(progress) {
  const job = state.busyJobs.get("global");
  if (!job) return;
  if (!progress || !progress.total_chunks) {
    job.progress = null;
    renderBusyJobs();
    return;
  }
  job.progress = progress;
  renderBusyJobs();
}

function stopSynthesisProgressPolling() {
  if (!state.progressPollTimer) return;
  clearInterval(state.progressPollTimer);
  state.progressPollTimer = null;
}

function startSynthesisProgressPolling() {
  stopSynthesisProgressPolling();
  const poll = async () => {
    if (!state.project || !state.selectedChapterId) return;
    try {
      const progress = await api(
        `/api/projects/${encodeURIComponent(state.project.project_id)}/synthesis-progress?chapter=${encodeURIComponent(state.selectedChapterId)}`
      );
      setBusyProgress(progress);
      if (progress.total_chunks) {
        upsertBusyJob("global", {
          ...state.busyJobs.get("global"),
          detail: progress.phase === "complete"
            ? "Finishing narration output..."
            : `Generating chunk ${progress.current_chunk || 1} of ${progress.total_chunks}...`,
        });
      }
    } catch {
      // Progress polling is best-effort; the main Generate request remains authoritative.
    }
  };
  poll();
  state.progressPollTimer = setInterval(poll, 700);
}

function scheduleAutoSave() {
  if (!state.project || !state.selectedChapterId) return;
  clearTimeout(state.autoSaveTimer);
  state.autoSaveTimer = setTimeout(() => saveChapterPage({ silent: true }), 1200);
}

async function refreshProjects() {
  const payload = await api("/api/projects");
  state.projects = payload.projects || [];
  $("project-select").innerHTML = state.projects
    .map((p) => `<option value="${escapeHtml(p.project_id)}">${escapeHtml(p.title)}</option>`)
    .join("");
  if (!state.project && state.projects.length) {
    await loadProject(state.projects[0].project_id);
  } else if (!state.projects.length) {
    state.project = null;
    state.memory = null;
    state.cast = null;
    $("project-meta").textContent = "No book loaded";
    clearChapterUi();
  }
}

async function loadProject(projectId, chapterId = null) {
  if (!projectId) return;
  resetDeleteButton();
  resetBookDeleteButton();
  resetClearButton();
  const query = chapterId ? `?chapter=${encodeURIComponent(chapterId)}` : "";
  const payload = await api(`/api/projects/${encodeURIComponent(projectId)}${query}`);
  state.project = payload.config;
  state.selectedChapterId = payload.selected_chapter_id;
  state.memory = payload.memory;
  state.chapterMemory = payload.chapter_memory;
  state.annotations = payload.annotations || [];
  state.annotatedText = payload.annotated_text || "";
  state.chapters = payload.chapters || [];
  state.cast = payload.cast;
  renderProject(payload);
  ensureElevenLabsVoicesLoaded();
  setStatus(`${payload.config.title} loaded`);
}

function renderProject(payload) {
  $("project-meta").textContent = payload.config.title;
  $("project-select").value = payload.config.project_id;
  $("narration-mode").value = payload.config.narration_mode || "multi_voice";
  state.audioManifest = payload.audio_manifest || null;
  state.audioUrl = payload.audio_url || null;
  renderToc();
  const chapter = payload.chapters.find((c) => c.chapter_id === payload.selected_chapter_id);
  $("chapter-id").value = payload.selected_chapter_id || "ch01";
  $("chapter-title").value = chapter?.title || "";
  $("book-editor").value = payload.source_text || "";
  renderChapterHydration();
  renderMemory();
  renderCharacters();
  renderAnnotationsPanel();
  renderTranscript();
  renderAudioPlayer();
  renderCast();
}

function chapterStatusIcon(chapter, busyJob = null) {
  if (state.generatingChapters.has(chapter.chapter_id) || busyJob) {
    return '<span class="toc-status" title="Processing…"><span class="toc-spin"></span></span>';
  }
  if (chapter.pipeline_state === "error") {
    return `<span class="toc-status toc-badge-error" title="${escapeAttr(chapter.pipeline_message || "Analysis failed")}">!</span>`;
  }
  if (chapter.pipeline_state === "paused") {
    return '<span class="toc-status toc-badge-warn" title="Paused">Ⅱ</span>';
  }
  if (chapter.pipeline_state === "canceled") {
    return '<span class="toc-status toc-badge-warn" title="Canceled">—</span>';
  }
  if (chapter.has_audio) {
    return '<span class="toc-status toc-badge-audio" title="Audio generated">♪</span>';
  }
  if (chapter.has_annotations) {
    return '<span class="toc-status toc-badge-ok" title="Annotated">✓</span>';
  }
  if (chapter.has_memory) {
    return '<span class="toc-status toc-badge-mem" title="Analyzed">A</span>';
  }
  return '<span class="toc-status toc-badge-none" aria-hidden="true"></span>';
}

function renderToc() {
  $("toc-count").textContent = String(state.chapters.length);
  $("toc-list").innerHTML = state.chapters
    .map((chapter, index) => {
      const busyJob = state.busyJobs.get(chapter.chapter_id);
      const tocState = chapterTocState(chapter, busyJob);
      return `
      <li class="toc-item ${chapter.chapter_id === state.selectedChapterId ? "active" : ""} ${escapeAttr(tocState.className)}"
        draggable="true"
        data-chapter-id="${escapeAttr(chapter.chapter_id)}">
        <span class="toc-handle" aria-hidden="true">☰</span>
        <button class="toc-title" title="${escapeAttr(chapter.title || chapter.chapter_id)}">
          ${index + 1}. ${escapeHtml(chapter.title || chapter.chapter_id)}
        </button>
        ${busyJob?.cancellable ? `<button class="toc-cancel" title="Cancel analysis from this chapter onward" data-chapter-id="${escapeAttr(chapter.chapter_id)}">×</button>` : chapterStatusIcon(chapter, busyJob)}
        <div class="toc-menu-wrap">
          <button class="toc-menu-btn" title="Chapter actions">⋯</button>
          <div class="toc-menu-dropdown">
            <button class="toc-menu-item toc-menu-delete" title="Delete this chapter">Delete chapter</button>
          </div>
        </div>
      </li>
    `;
    })
    .join("") + `
      <li class="toc-add-row">
        <button id="new-chapter" class="toc-add" title="Add chapter">＋ Chapter</button>
      </li>
    `;
}

function chapterTocState(chapter, busyJob = null) {
  if (busyJob) {
    return { className: "is-running", title: busyJob.detail || busyJob.title, html: '<span class="mini-spinner"></span>' };
  }
  if (chapter.pipeline_state === "error") {
    return { className: "is-error", title: chapter.pipeline_message || "Analysis failed", html: "!" };
  }
  if (chapter.pipeline_state === "paused") {
    return { className: "is-paused", title: chapter.pipeline_message || "Paused", html: "Ⅱ" };
  }
  if (chapter.pipeline_state === "canceled") {
    return { className: "is-canceled", title: chapter.pipeline_message || "Canceled", html: "×" };
  }
  if (chapter.analyzed && chapter.annotated) {
    return { className: "is-complete", title: "Analyzed and annotated", html: "✓" };
  }
  return {
    className: "is-pending",
    title: `Analyzed${chapter.analyzed ? "" : " not"} · Annotated${chapter.annotated ? "" : " not"}`,
    html: `${chapter.analyzed ? "A" : "·"}${chapter.annotated ? "N" : "·"}`,
  };
}

function updateLocalChapterState(chapterId, patch) {
  const chapter = state.chapters.find((row) => row.chapter_id === chapterId);
  if (!chapter) return;
  Object.assign(chapter, patch);
}

function markSubsequentChapters(chapterId, patch) {
  let found = false;
  for (const chapter of state.chapters) {
    if (chapter.chapter_id === chapterId) {
      found = true;
      continue;
    }
    if (found && (!chapter.analyzed || !chapter.annotated)) {
      Object.assign(chapter, patch);
    }
  }
}

function renderChapterHydration() {
  const hasChapter = Boolean(state.selectedChapterId);
  document.querySelector(".workspace").classList.toggle("no-chapter", !hasChapter);
  document.querySelector(".editor-band").classList.toggle("no-chapter", !hasChapter);
  $("empty-editor").classList.toggle("active", !hasChapter);
}

function setInspectorOpen(isOpen) {
  document.querySelector(".workspace").classList.toggle("inspector-collapsed", !isOpen);
}

function renderMemory() {
  const memory = state.memory || {};
  $("plot-summary").value = memory.plot_summary || "";
  $("current-state").value = memory.current_state || "";
  $("themes").value = (memory.themes || []).join(", ");
  $("pronunciation-list").innerHTML = Object.entries(memory.pronunciation_notes || {})
    .map(([key, value]) => pronunciationRow(key, value))
    .join("");
  renderChapterMemory();
}

function renderChapterMemory() {
  const chapterMemory = state.chapterMemory || {};
  const chapter = state.chapters.find((row) => row.chapter_id === state.selectedChapterId);
  $("chapter-memory-section").hidden = !state.selectedChapterId;
  $("chapter-memory-label").textContent = state.selectedChapterId
    ? `${chapter?.title || state.selectedChapterId} · not merged backward unless you save/analyze`
    : "No chapter selected";
  $("chapter-plot-summary").value = chapterMemory.plot_summary || "";
  $("chapter-current-state").value = chapterMemory.current_state || "";
  $("chapter-themes").value = (chapterMemory.themes || []).join(", ");
  $("chapter-character-changes").innerHTML = Object.values(chapterMemory.character_changes || {})
    .map((change) => `
      <article class="item chapter-change-item" data-name="${escapeAttr(change.name)}">
        <div class="item-head">
          <input class="chapter-change-name item-title" value="${escapeAttr(change.name)}" />
        </div>
        <div class="chapter-change-grid">
          <textarea class="chapter-change-personality" placeholder="Personality during this chapter">${escapeHtml(change.personality_at_this_point || "")}</textarea>
          <textarea class="chapter-change-delta" placeholder="How the character changes in this chapter">${escapeHtml(change.changes || "")}</textarea>
        </div>
      </article>
    `)
    .join("");
}

function addChapterChange() {
  if (!state.selectedChapterId) return;
  state.chapterMemory ||= {
    chapter_id: state.selectedChapterId,
    title: $("chapter-title").value || state.selectedChapterId,
    character_changes: {},
  };
  state.chapterMemory.character_changes ||= {};
  const name = `Character ${Object.keys(state.chapterMemory.character_changes).length + 1}`;
  state.chapterMemory.character_changes[name] = {
    name,
    role_in_chapter: "",
    personality_at_this_point: "",
    changes: "",
    evidence: [],
  };
  renderChapterMemory();
}

function pronunciationRow(key = "", value = "") {
  return `
    <div class="kv-row">
      <input class="pron-key" value="${escapeAttr(key)}" placeholder="term" />
      <input class="pron-value" value="${escapeAttr(value)}" placeholder="pronunciation / note" />
      <button class="danger remove-row" title="Remove">×</button>
    </div>
  `;
}

function renderCharacters() {
  const characters = state.memory?.characters || {};
  $("characters-list").innerHTML = Object.values(characters)
    .map((character) => {
      const assignment = characterCastAssignment(character.name);
      const voice = assignment ? state.cast?.voices?.[assignment.voice_id] : null;
      return `
      <article class="item character-item" data-name="${escapeAttr(character.name)}">
        <div class="item-head">
          <input class="character-name item-title" value="${escapeAttr(character.name)}" />
          <button class="danger remove-character" title="Remove">×</button>
        </div>
        <div class="character-grid">
          <input class="character-aliases" value="${escapeAttr((character.aliases || []).join(", "))}" placeholder="aliases" />
          <input class="character-age" value="${escapeAttr(character.age || "")}" placeholder="age / life stage" />
          <input class="character-gender" value="${escapeAttr(character.gender || "")}" placeholder="gender / presentation" />
          ${providerVoiceControl(voice?.provider_voice || "", "character-provider-voice", character)}
          <textarea class="character-personality" placeholder="Base personality / overall profile">${escapeHtml(character.personality || "")}</textarea>
          <textarea class="character-voice-notes" placeholder="Voice casting notes">${escapeHtml(character.voice_notes || "")}</textarea>
        </div>
      </article>
    `;
    })
    .join("");
}

function renderAnnotationsPanel() {
  return;
}

function renderTranscript() {
  $("embedded-annotations-editor").value = state.annotatedText || buildEmbeddedAnnotationText(state.annotations);
  $("book-editor").hidden = state.annotations.length > 0;
  $("inline-annotations").hidden = state.annotations.length === 0;
  $("inline-annotations").innerHTML = state.annotations.length
    ? state.annotations.map((row, index) => inlineAnnotationHtml(row, index)).join("")
    : "";
}

function inlineAnnotationHtml(row, index) {
  const chunkIndex = state.audioManifest != null ? (passageChunkMap[index] ?? -1) : -1;
  const hasAudio = chunkIndex >= 0;
  const regenBtn = hasAudio
    ? `<button class="regen-chunk-btn" data-chunk="${chunkIndex}" title="Re-synthesize chunk ${chunkIndex}">↺</button>`
    : "";
  const ttsOpen = row.tts_text ? " open" : "";
  return `
    <article class="inline-annotation-item" data-index="${index}">
      <div class="annotation-header${hasAudio ? " has-audio" : ""}">
        <input class="ann-speaker" value="${escapeAttr(row.speaker || "Narrator")}" placeholder="speaker" />
        ${selectHtml("ann-emotion", emotions, row.emotion || "neutral")}
        ${selectHtml("ann-delivery", deliveries, row.delivery || "matter-of-fact")}
        ${selectHtml("ann-pace", paces, row.pace || "medium")}
        <input class="ann-intensity" type="number" min="1" max="5" value="${Number(row.intensity || 3)}" title="intensity 1–5" />
        ${regenBtn}
      </div>
      <div class="annotation-script">
        <textarea class="ann-text">${escapeHtml(row.text || "")}</textarea>
        <div class="script-pause">⏸ <input class="ann-pause" type="number" min="0" value="${Number(row.pause_after_ms || 350)}" /> ms</div>
      </div>
      <input class="ann-rationale" value="${escapeAttr(row.rationale || "")}" placeholder="rationale" />
      <details class="ann-tts-details"${ttsOpen}>
        <summary>TTS override</summary>
        <textarea class="ann-tts-text" placeholder="Text sent to TTS (leave blank to use passage text). Use for phonetic substitution: e.g. 汪淼(Wāng Miǎo) or full pinyin.">${escapeHtml(row.tts_text || "")}</textarea>
      </details>
    </article>
  `;
}

function buildEmbeddedAnnotationText(annotations) {
  if (!annotations.length) return "";
  const title = $("chapter-title").value || state.selectedChapterId || "Chapter";
  const lines = [`# ${title}`, ""];
  annotations.forEach((row) => {
    const meta = [
      `id=${row.passage_id || ""}`,
      `speaker=${row.speaker || "Narrator"}`,
      `emotion=${row.emotion || "neutral"}`,
      `delivery=${row.delivery || "matter-of-fact"}`,
      `pace=${row.pace || "medium"}`,
      `intensity=${Number(row.intensity || 3)}`,
      `pause_ms=${Number(row.pause_after_ms || 350)}`,
    ];
    lines.push(`[[${meta.join(" | ")}]]`);
    if (row.rationale) lines.push(`// ${row.rationale}`);
    lines.push(row.text || "");
    lines.push("");
  });
  return lines.join("\n").trimEnd() + "\n";
}

function renderCast() {
  const cast = state.cast || { assignments: {}, voices: {} };
  $("cast-list").innerHTML = Object.values(cast.assignments || {})
    .map((assignment) => {
      const voice = cast.voices?.[assignment.voice_id] || {};
      const charProfile = state.memory?.characters?.[assignment.character] || null;
      return `
        <article class="item cast-item" data-character="${escapeAttr(assignment.character)}">
          <div class="item-head">
            <input class="cast-character item-title" value="${escapeAttr(assignment.character)}" />
            <button class="danger remove-cast" title="Remove">×</button>
          </div>
          <div class="cast-grid">
            <input class="cast-voice-id" value="${escapeAttr(assignment.voice_id)}" placeholder="voice id" />
            ${providerVoiceControl(voice.provider_voice || "", "cast-provider-voice", charProfile)}
            <textarea class="cast-reason" placeholder="reason">${escapeHtml(assignment.reason || "")}</textarea>
          </div>
        </article>
      `;
    })
    .join("");
}

function characterCastAssignment(character) {
  return state.cast?.assignments?.[character] || null;
}

function scoreVoiceForCharacter(voice, character) {
  if (!character) return 0;
  let score = 0;
  const labels = voice.labels || {};
  const voiceGender = (labels.gender || "").toLowerCase();
  const charGender = (character.gender || "").toLowerCase();
  const normalizeGender = (g) =>
    /female|woman|girl|女/.test(g) ? "female" : /male|man|boy|男/.test(g) ? "male" : "";
  const vg = normalizeGender(voiceGender);
  const cg = normalizeGender(charGender);
  if (vg && cg && vg === cg) score += 10;

  const voiceAge = (labels.age || "").toLowerCase();
  const charAge = (character.age || "").toLowerCase();
  if (voiceAge && charAge) {
    const ageGroups = [
      [/child|teen|young|youth|adolescent|少年|少女|青少/, "young"],
      [/middle.aged|adult|grown|中年|成年/, "middle"],
      [/old|elder|senior|ancient|aged|老年|老/, "old"],
    ];
    const classify = (s) => {
      for (const [re, label] of ageGroups) if (re.test(s)) return label;
      return "";
    };
    if (classify(voiceAge) && classify(voiceAge) === classify(charAge)) score += 5;
  }

  const stopWords = new Set([
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "of", "to",
    "in", "for", "with", "on", "at", "by", "from", "has", "have", "be",
    "this", "that", "it", "its", "as", "but", "so",
  ]);
  const tokenize = (s) =>
    (s || "")
      .toLowerCase()
      .replace(/[^\w\s]/g, " ")
      .split(/\s+/)
      .filter((w) => w.length > 2 && !stopWords.has(w));

  const voiceTokens = new Set([
    ...tokenize(labels.gender),
    ...tokenize(labels.age),
    ...tokenize(labels.accent),
    ...tokenize(labels.description),
    ...tokenize(labels.use_case),
    ...tokenize(voice.name),
  ]);
  const charTokens = [
    ...tokenize(character.personality),
    ...tokenize(character.voice_notes),
    ...tokenize(character.gender),
    ...tokenize(character.age),
  ];
  for (const tok of charTokens) {
    if (voiceTokens.has(tok)) score += 1;
  }
  return score;
}

function providerVoiceControl(selectedVoiceId = "", className = "cast-provider-voice", character = null) {
  if (!state.elevenVoices.length) {
    return `<input class="${className}" value="${escapeAttr(selectedVoiceId)}" placeholder="provider voice / ElevenLabs voice id" />`;
  }
  const selectedExists = state.elevenVoices.some((voice) => voice.voice_id === selectedVoiceId);
  const sorted = character
    ? [...state.elevenVoices].sort(
        (a, b) => scoreVoiceForCharacter(b, character) - scoreVoiceForCharacter(a, character)
      )
    : state.elevenVoices;
  const options = sorted
    .map((voice) => {
      const labels = voice.labels || {};
      const detail = [labels.gender, labels.age, labels.accent].filter(Boolean).join(", ");
      const label = `${voice.name || voice.voice_id}${detail ? ` (${detail})` : ""}`;
      return `<option value="${escapeAttr(voice.voice_id)}" ${voice.voice_id === selectedVoiceId ? "selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
  const current = selectedVoiceId && !selectedExists
    ? `<option value="${escapeAttr(selectedVoiceId)}" selected>${escapeHtml(selectedVoiceId)}</option>`
    : "";
  const select = `<select class="${className}"><option value="">Select ElevenLabs voice...</option>${current}${options}</select>`;
  return `<div class="voice-select-wrap">${select}<button class="voice-preview-btn" title="Preview selected voice">▶</button></div>`;
}

function renderElevenVoices() {
  $("voice-library").innerHTML = state.elevenVoices
    .map((voice) => {
      const previewBtn = voice.preview_url
        ? `<button class="voice-preview-btn" data-voice-id="${escapeAttr(voice.voice_id)}" title="Preview voice">▶</button>`
        : "";
      return `<span class="voice-pill-group"><button class="voice-pill" data-voice-id="${escapeAttr(voice.voice_id)}" title="Copy voice ID">${escapeHtml(voice.name || voice.voice_id)}</button>${previewBtn}</span>`;
    })
    .join("");
}

function ensureElevenLabsVoicesLoaded() {
  if (state.elevenVoices.length || state.loadingElevenVoices) return;
  loadElevenLabsVoices({ quiet: true });
}

async function loadElevenLabsVoices({ quiet = false } = {}) {
  state.loadingElevenVoices = true;
  try {
    const payload = await api("/api/elevenlabs/voices");
    state.elevenVoices = payload.voices || [];
    renderElevenVoices();
    renderCharacters();
    renderCast();
    if (!quiet) setStatus(`${state.elevenVoices.length} ElevenLabs voices loaded`);
  } catch (error) {
    if (!quiet) setStatus(`ElevenLabs voices unavailable: ${error.message}`);
  } finally {
    state.loadingElevenVoices = false;
  }
}

function selectHtml(className, options, selected) {
  return `<select class="${className}">${options
    .map((option) => `<option value="${escapeAttr(option)}" ${option === selected ? "selected" : ""}>${escapeHtml(option)}</option>`)
    .join("")}</select>`;
}

async function saveChapterTranscript({ reload = true } = {}) {
  if (!state.project || !state.selectedChapterId) return;
  const chapterId = $("chapter-id").value.trim();
  const title = $("chapter-title").value.trim();
  if (state.annotations.length) {
    const annotations = collectAnnotations();
    await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/chapters`, {
      method: "POST",
      body: JSON.stringify({
        chapter_id: chapterId,
        title,
        text: annotations.map((row) => row.text || "").join("\n\n"),
      }),
    });
    await saveAnnotationsPayload(annotations);
    await saveAnnotatedTextPayload(buildEmbeddedAnnotationText(annotations));
    state.annotations = annotations;
    state.annotatedText = buildEmbeddedAnnotationText(annotations);
    state.selectedChapterId = chapterId;
    renderAnnotationsPanel();
    renderTranscript();
    if (reload) await loadProject(state.project.project_id, chapterId);
    return;
  }
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/chapters`, {
    method: "POST",
    body: JSON.stringify({
      chapter_id: chapterId,
      title,
      text: $("book-editor").value,
    }),
  });
  state.selectedChapterId = chapterId;
  if (reload) await loadProject(state.project.project_id, chapterId);
}

async function saveChapterPage({ silent = false } = {}) {
  if (!state.project || !state.selectedChapterId || state.autoSaving) return;
  state.autoSaving = true;
  try {
    await saveChapterTranscript({ reload: false });
    const memory = collectMemory();
    await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/memory`, {
      method: "POST",
      body: JSON.stringify(memory),
    });
    state.memory = memory;
    if (state.selectedChapterId) {
      const chapterMemory = collectChapterMemory();
      await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/chapter-memory/${encodeURIComponent(state.selectedChapterId)}`, {
        method: "POST",
        body: JSON.stringify(chapterMemory),
      });
      state.chapterMemory = chapterMemory;
    }
    await saveCharacterVoiceAssignments();
    if (!silent) {
      await loadProject(state.project.project_id, state.selectedChapterId);
      setStatus("Chapter page saved");
    }
  } catch (error) {
    setStatus(`Save failed: ${error.message}`);
  } finally {
    state.autoSaving = false;
  }
}

async function saveAnnotationsPayload(annotations) {
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/annotations/${encodeURIComponent(state.selectedChapterId)}`, {
    method: "POST",
    body: JSON.stringify({ annotations }),
  });
}

async function saveAnnotatedTextPayload(text) {
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/annotated-text/${encodeURIComponent(state.selectedChapterId)}`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

async function saveMemory() {
  if (!state.project) return;
  const memory = collectMemory();
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/memory`, {
    method: "POST",
    body: JSON.stringify(memory),
  });
  state.memory = memory;
  if (state.selectedChapterId) {
    const chapterMemory = collectChapterMemory();
    await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/chapter-memory/${encodeURIComponent(state.selectedChapterId)}`, {
      method: "POST",
      body: JSON.stringify(chapterMemory),
    });
    state.chapterMemory = chapterMemory;
  }
  renderCharacters();
  setStatus("Memory saved");
}

async function saveCharacterProfiles() {
  if (!state.project) return;
  const memory = collectMemory();
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/memory`, {
    method: "POST",
    body: JSON.stringify(memory),
  });
  state.memory = memory;
  await saveCharacterVoiceAssignments();
  renderCharacters();
  setStatus("Character profiles and voices saved");
}

function collectMemory() {
  const memory = structuredClone(state.memory || {});
  memory.title = memory.title || state.project?.title || "Untitled";
  memory.language = memory.language || state.project?.language || "zh";
  memory.plot_summary = $("plot-summary").value;
  memory.current_state = $("current-state").value;
  memory.themes = $("themes").value.split(",").map((x) => x.trim()).filter(Boolean);
  memory.pronunciation_notes = {};
  document.querySelectorAll(".kv-row").forEach((row) => {
    const key = row.querySelector(".pron-key").value.trim();
    const value = row.querySelector(".pron-value").value.trim();
    if (key) memory.pronunciation_notes[key] = value;
  });
  memory.characters = {};
  document.querySelectorAll(".character-item").forEach((item) => {
    const name = item.querySelector(".character-name").value.trim();
    if (!name) return;
    memory.characters[name] = {
      name,
      aliases: item.querySelector(".character-aliases").value.split(",").map((x) => x.trim()).filter(Boolean),
      age: item.querySelector(".character-age").value,
      gender: item.querySelector(".character-gender").value,
      personality: item.querySelector(".character-personality").value,
      role_in_plot: originalCharacter(name)?.role_in_plot || "",
      relationships: {},
      voice_notes: item.querySelector(".character-voice-notes").value,
      evidence: [],
    };
  });
  return memory;
}

function collectChapterMemory() {
  const chapterMemory = structuredClone(state.chapterMemory || {});
  chapterMemory.chapter_id = state.selectedChapterId;
  chapterMemory.title = $("chapter-title").value || state.selectedChapterId || "";
  chapterMemory.plot_summary = $("chapter-plot-summary").value;
  chapterMemory.current_state = $("chapter-current-state").value;
  chapterMemory.themes = $("chapter-themes").value.split(",").map((x) => x.trim()).filter(Boolean);
  chapterMemory.character_changes = {};
  document.querySelectorAll(".chapter-change-item").forEach((item) => {
    const name = item.querySelector(".chapter-change-name").value.trim();
    if (!name) return;
    chapterMemory.character_changes[name] = {
      name,
      role_in_chapter: "",
      personality_at_this_point: item.querySelector(".chapter-change-personality").value,
      changes: item.querySelector(".chapter-change-delta").value,
      evidence: [],
    };
  });
  return chapterMemory;
}

function originalCharacter(name) {
  return state.memory?.characters?.[name] || null;
}

function collectAnnotations() {
  const annotations = [];
  document.querySelectorAll(".inline-annotation-item").forEach((item, index) => {
    const original = state.annotations[Number(item.dataset.index)] || {};
    annotations.push({
      ...original,
      passage_id: original.passage_id || `${state.selectedChapterId}-${String(index).padStart(4, "0")}`,
      chapter_id: state.selectedChapterId,
      index,
      text: item.querySelector(".ann-text").value,
      tts_text: item.querySelector(".ann-tts-text")?.value || "",
      speaker: item.querySelector(".ann-speaker").value || "Narrator",
      emotion: item.querySelector(".ann-emotion").value,
      delivery: item.querySelector(".ann-delivery").value,
      pace: item.querySelector(".ann-pace").value,
      intensity: Number(item.querySelector(".ann-intensity").value || 3),
      pause_after_ms: Number(item.querySelector(".ann-pause").value || 350),
      audio_tags: extractInlineTags(item.querySelector(".ann-text").value),
      rationale: item.querySelector(".ann-rationale").value,
      pronunciation_hints: original.pronunciation_hints || {},
    });
  });
  return annotations;
}

async function resetAnnotations() {
  if (!state.project || !state.selectedChapterId) return;
  const button = $("reset-annotations");
  if (state.pendingClearAnnotationsId !== state.selectedChapterId) {
    state.pendingClearAnnotationsId = state.selectedChapterId;
    button.textContent = "Confirm Clear";
    button.classList.add("armed");
    setStatus(`Click Confirm Clear to remove all annotations for ${state.selectedChapterId}`);
    return;
  }
  setStatus(`Clearing annotations for ${state.selectedChapterId}...`);
  try {
    await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/reset-annotations/${encodeURIComponent(state.selectedChapterId)}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.annotations = [];
    state.annotatedText = "";
    state.audioManifest = null;
    state.audioUrl = null;
    const chapter = state.chapters.find((row) => row.chapter_id === state.selectedChapterId);
    if (chapter) chapter.annotated = false;
    state.pendingClearAnnotationsId = null;
    button.textContent = "Clear";
    button.classList.remove("armed");
    renderAnnotationsPanel();
    renderTranscript();
    renderAudioPlayer();
    renderToc();
    setStatus("Chapter annotations and audio cleared");
  } catch (error) {
    setStatus(`Clear failed: ${error.message}`);
  }
}

async function deleteChapterById(chapterId) {
  if (!state.project || !chapterId) return;
  closeAllTocMenus();
  setStatus(`Deleting ${chapterId}...`);
  try {
    await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/delete-chapter/${encodeURIComponent(chapterId)}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadProject(state.project.project_id);
    setStatus(`Deleted ${chapterId}`);
  } catch (error) {
    setStatus(`Delete failed: ${error.message}`);
  }
}

async function deleteCurrentChapter() {
  await deleteChapterById(state.selectedChapterId);
}

function closeAllTocMenus() {
  document.querySelectorAll(".toc-menu-wrap.open").forEach((wrap) => wrap.classList.remove("open"));
}

async function deleteCurrentBook() {
  if (!state.project) return;
  const bookId = state.project.project_id;
  const button = $("delete-book");
  if (state.pendingDeleteBookId !== bookId) {
    state.pendingDeleteBookId = bookId;
    button.textContent = "!";
    button.classList.add("armed");
    setStatus(`Click Confirm Book Delete to remove ${state.project.title}`);
    return;
  }
  button.disabled = true;
  setStatus(`Deleting book ${state.project.title}...`);
  try {
    await api(`/api/projects/${encodeURIComponent(bookId)}/delete-project`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.project = null;
    clearChapterUi();
    resetBookDeleteButton();
    await refreshProjects();
    setStatus(`Deleted book ${bookId}`);
  } catch (error) {
    setStatus(`Delete book failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function saveCast() {
  if (!state.project) return;
  const cast = { assignments: {}, voices: structuredClone(state.cast?.voices || {}) };
  document.querySelectorAll(".cast-item").forEach((item) => {
    const character = item.querySelector(".cast-character").value.trim();
    const voiceId = item.querySelector(".cast-voice-id").value.trim();
    const providerVoice = item.querySelector(".cast-provider-voice").value.trim();
    if (!character || !voiceId) return;
    cast.assignments[character] = {
      character,
      voice_id: voiceId,
      reason: item.querySelector(".cast-reason").value,
    };
    cast.voices[voiceId] = {
      ...(cast.voices[voiceId] || {}),
      voice_id: voiceId,
      provider_voice: providerVoice || voiceId,
      language: cast.voices[voiceId]?.language || "zh",
      suitable_for: cast.voices[voiceId]?.suitable_for || [],
    };
  });
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/cast`, {
    method: "POST",
    body: JSON.stringify(cast),
  });
  state.cast = cast;
  setStatus("Voice cast saved");
}

async function saveCharacterVoiceAssignments() {
  if (!state.project) return;
  state.cast ||= { assignments: {}, voices: {} };
  document.querySelectorAll(".character-item").forEach((item, index) => {
    const character = item.querySelector(".character-name").value.trim();
    const providerVoice = item.querySelector(".character-provider-voice")?.value.trim();
    if (!character || !providerVoice) return;
    const existing = state.cast.assignments?.[character];
    const voiceId = existing?.voice_id || `character_${safeId(character || String(index + 1))}`;
    state.cast.assignments[character] = {
      character,
      voice_id: voiceId,
      reason: item.querySelector(".character-voice-notes").value || "Selected from the book character profile.",
    };
    state.cast.voices[voiceId] = {
      ...(state.cast.voices?.[voiceId] || {}),
      voice_id: voiceId,
      provider_voice: providerVoice,
      language: state.cast.voices?.[voiceId]?.language || "zh",
      age: item.querySelector(".character-age").value || state.cast.voices?.[voiceId]?.age || null,
      gender: item.querySelector(".character-gender").value || state.cast.voices?.[voiceId]?.gender || null,
      timbre: item.querySelector(".character-voice-notes").value || state.cast.voices?.[voiceId]?.timbre || "",
      suitable_for: [character],
    };
  });
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/cast`, {
    method: "POST",
    body: JSON.stringify(state.cast),
  });
}

async function runAnalyzeAnnotateBook() {
  if (!state.project) return;
  const pending = state.chapters.filter((chapter) => !chapter.analyzed || !chapter.annotated);
  if (!pending.length) {
    setStatus("All chapters are already analyzed and annotated");
    return;
  }
  state.pipelineCanceled = false;
  setStatus(`Analyze + Annotate started for ${pending.length} chapter${pending.length === 1 ? "" : "s"}`);
  try {
    for (const chapter of pending) {
      if (state.pipelineCanceled) break;
      if (!chapter.analyzed) {
        upsertBusyJob(chapter.chapter_id, {
          title: "Analyzing",
          detail: `Updating memory for ${chapter.title || chapter.chapter_id}...`,
          chapterId: chapter.chapter_id,
          cancellable: true,
          controller: new AbortController(),
        });
        updateLocalChapterState(chapter.chapter_id, { pipeline_state: "analyzing", pipeline_message: "Analyzing chapter context." });
        renderToc();
        await runChapterStep("analyze", chapter.chapter_id, { reload: false, controller: state.busyJobs.get(chapter.chapter_id)?.controller });
        removeBusyJob(chapter.chapter_id);
        updateLocalChapterState(chapter.chapter_id, { analyzed: true, pipeline_state: "analyzed", pipeline_message: "Analysis complete." });
      }
      if (state.pipelineCanceled) break;
      if (!chapter.annotated) {
        upsertBusyJob(chapter.chapter_id, {
          title: "Annotating",
          detail: `Directing ${chapter.title || chapter.chapter_id}...`,
          chapterId: chapter.chapter_id,
          cancellable: true,
          controller: new AbortController(),
        });
        updateLocalChapterState(chapter.chapter_id, { pipeline_state: "annotating", pipeline_message: "Annotating chapter." });
        renderToc();
        await runChapterStep("annotate", chapter.chapter_id, { reload: false, controller: state.busyJobs.get(chapter.chapter_id)?.controller });
        removeBusyJob(chapter.chapter_id);
        updateLocalChapterState(chapter.chapter_id, { annotated: true, pipeline_state: "complete", pipeline_message: "Analysis and annotation complete." });
      }
      renderToc();
    }
    await loadProject(state.project.project_id, state.selectedChapterId);
    setStatus(state.pipelineCanceled ? "Analyze + Annotate canceled" : "Analyze + Annotate complete");
  } catch (error) {
    const failed = [...state.busyJobs.values()].find((job) => job.chapterId)?.chapterId;
    if (failed) {
      updateLocalChapterState(failed, { pipeline_state: "error", pipeline_message: error.message });
      markSubsequentChapters(failed, { pipeline_state: "paused", pipeline_message: `Paused because ${failed} failed: ${error.message}` });
      renderToc();
    }
    setStatus(error.message);
  } finally {
    state.busyJobs.clear();
    renderBusyJobs();
  }
}

async function runChapterStep(step, chapterId = state.selectedChapterId, { reload = true, controller = null } = {}) {
  if (!state.project || !chapterId) return null;
  const busyCopy = {
    analyze: ["Analyzing", "Updating plot, character memory, and voices..."],
    annotate: ["Annotating", "Directing this chapter..."],
    synthesize: ["Generating", "Creating narration output..."],
  }[step] || ["Working", "Processing..."];
  const jobId = chapterId;
  controller ||= reload ? new AbortController() : null;
  if (reload) {
    upsertBusyJob(jobId, {
      title: busyCopy[0],
      detail: busyCopy[1],
      chapterId,
      cancellable: step === "analyze" || step === "annotate",
      controller,
    });
    setStatus(`${step} started`);
  }
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/run`, {
      method: "POST",
      body: JSON.stringify({ step, chapter_id: chapterId, backend: "elevenlabs" }),
      signal: controller?.signal,
    });
    if (reload) {
      await loadProject(state.project.project_id, chapterId);
      const llm = payload.llm ? ` with ${payload.llm.provider}${payload.llm.model ? ` (${payload.llm.model})` : ""}` : "";
      setStatus(`${step} complete${llm}`);
    }
    return payload;
  } catch (error) {
    if (reload) setStatus(error.name === "AbortError" ? `${step} canceled` : error.message);
    if (error.name !== "AbortError") throw error;
    return null;
  } finally {
    if (reload) removeBusyJob(jobId);
  }
}

async function runStep(step) {
  if (!state.project) return;
  const busyCopy = {
    synthesize: ["Generating", "Creating narration output..."],
  }[step] || ["Working", "Processing..."];
  setBusy(true, busyCopy[0], busyCopy[1]);
  if (step === "synthesize") startSynthesisProgressPolling();
  setStatus(`${step} started`);
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/run`, {
      method: "POST",
      body: JSON.stringify({
        step,
        chapter_id: state.selectedChapterId,
        backend: "elevenlabs",
      }),
    });
    await loadProject(state.project.project_id, state.selectedChapterId);
    const llm = payload.llm ? ` with ${payload.llm.provider}${payload.llm.model ? ` (${payload.llm.model})` : ""}` : "";
    setStatus(`${step} complete${llm}`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    if (step === "synthesize") stopSynthesisProgressPolling();
    setBusy(false);
  }
}

async function importFile(file) {
  if (!state.project || !file) return;
  setStatus(`Importing ${file.name}`);
  const data = await readAsDataUrl(file);
  const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/import`, {
    method: "POST",
    body: JSON.stringify({ filename: file.name, data }),
  });
  await loadProject(state.project.project_id, payload.manifest.chapter_id);
  setStatus(`${file.name} imported`);
}

async function importFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  if (files.length === 1) {
    await importFile(files[0]);
    return;
  }
  openBulkImportModal(files);
}

async function bulkImportFiles(fileList, { analyze = false } = {}) {
  if (!state.project || !fileList?.length) return;
  const files = Array.from(fileList);
  setBusy(true, "Importing", analyze ? `Importing ${files.length} chapters in order...` : `Adding ${files.length} chapter source files...`);
  setStatus(`Import started for ${files.length} chapters`);
  try {
    const uploads = [];
    for (const file of files) {
      uploads.push({ filename: file.name, data: await readAsDataUrl(file) });
    }
    const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/bulk-import`, {
      method: "POST",
      body: JSON.stringify({ files: uploads, analyze: false }),
    });
    await loadProject(state.project.project_id);
    if (analyze) {
      await runAnalyzeAnnotateBook();
    } else {
      setStatus(`Added ${payload.manifests?.length || 0} chapter source files`);
    }
  } catch (error) {
    setStatus(`Import failed: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

function openBulkImportModal(files) {
  state.pendingBulkImportFiles = Array.from(files).sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true })
  );
  renderBulkImportOrder();
  $("bulk-import-modal").showModal();
}

function renderBulkImportOrder() {
  $("bulk-import-list").innerHTML = state.pendingBulkImportFiles
    .map((file, index) => `
      <li class="bulk-import-item" data-index="${index}">
        <span class="bulk-import-rank">${index + 1}</span>
        <span class="bulk-import-name" title="${escapeAttr(file.name)}">${escapeHtml(file.name)}</span>
        <div class="bulk-import-actions">
          <button type="button" class="bulk-import-move" data-direction="up" title="Move earlier" ${index === 0 ? "disabled" : ""}>↑</button>
          <button type="button" class="bulk-import-move" data-direction="down" title="Move later" ${index === state.pendingBulkImportFiles.length - 1 ? "disabled" : ""}>↓</button>
        </div>
      </li>
    `)
    .join("");
}

function movePendingBulkImportFile(index, direction) {
  const nextIndex = direction === "up" ? index - 1 : index + 1;
  if (nextIndex < 0 || nextIndex >= state.pendingBulkImportFiles.length) return;
  const [file] = state.pendingBulkImportFiles.splice(index, 1);
  state.pendingBulkImportFiles.splice(nextIndex, 0, file);
  renderBulkImportOrder();
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function addCharacter() {
  if (!state.memory) return;
  state.memory.characters ||= {};
  const name = `Character ${Object.keys(state.memory.characters).length + 1}`;
  state.memory.characters[name] = {
    name,
    aliases: [],
    age: "",
    gender: "",
    personality: "",
    role_in_plot: "",
    relationships: {},
    voice_notes: "",
    evidence: [],
  };
  renderCharacters();
}

function addCast() {
  state.cast ||= { assignments: {}, voices: {} };
  const character = `Speaker ${Object.keys(state.cast.assignments).length + 1}`;
  const voiceId = `voice_${Object.keys(state.cast.voices).length + 1}`;
  state.cast.assignments[character] = { character, voice_id: voiceId, reason: "" };
  state.cast.voices[voiceId] = { voice_id: voiceId, provider_voice: "", language: "zh", suitable_for: [] };
  renderCast();
}

function createNewChapter() {
  if (!state.project) return;
  const next = `ch${String((state.chapters.length || 0) + 1).padStart(2, "0")}`;
  $("chapter-id").value = next;
  $("chapter-title").value = "";
  $("book-editor").value = "";
  state.selectedChapterId = next;
  state.annotations = [];
  state.annotatedText = "";
  state.chapterMemory = null;
  resetDeleteButton();
  renderToc();
  renderChapterHydration();
  renderAnnotationsPanel();
  renderTranscript();
}

async function renameCurrentBook() {
  if (!state.project) return;
  const title = prompt("Rename book", state.project.title);
  if (!title || title.trim() === state.project.title) return;
  const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/rename`, {
    method: "POST",
    body: JSON.stringify({ title: title.trim() }),
  });
  state.project = payload.config;
  await refreshProjects();
  await loadProject(state.project.project_id);
  setStatus(`Renamed book to ${state.project.title}`);
}

async function reorderChapters(chapterIds) {
  if (!state.project) return;
  const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/chapters-reorder`, {
    method: "POST",
    body: JSON.stringify({ chapter_ids: chapterIds }),
  });
  state.chapters = payload.chapters || state.chapters;
  renderToc();
  setStatus("Chapter order saved");
}

function openGenerateModal() {
  if (!state.project || !state.chapters.length) return;
  $("generate-chapter-list").innerHTML = state.chapters.map((chapter) => `
    <label class="generate-chapter-row">
      <input type="checkbox" class="generate-chapter-check" value="${escapeAttr(chapter.chapter_id)}" checked />
      <span class="generate-chapter-title">${escapeHtml(chapter.title || chapter.chapter_id)}</span>
      ${chapter.has_audio ? '<span class="generate-chapter-badge" title="Audio already exists">♪</span>' : ""}
    </label>
  `).join("");
  $("generate-modal").showModal();
}

async function startGenerate() {
  const chapterIds = Array.from(document.querySelectorAll(".generate-chapter-check:checked"))
    .map((cb) => cb.value);
  if (!chapterIds.length) return;
  $("generate-modal").close();
  chapterIds.forEach((id) => state.generatingChapters.add(id));
  renderToc();
  setStatus(`Generating ${chapterIds.length} chapter${chapterIds.length > 1 ? "s" : ""}…`);
  const promises = chapterIds.map(async (chapterId) => {
    try {
      await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/run`, {
        method: "POST",
        body: JSON.stringify({ step: "synthesize", chapter_id: chapterId, backend: "elevenlabs" }),
      });
      const chapter = state.chapters.find((c) => c.chapter_id === chapterId);
      if (chapter) chapter.has_audio = true;
    } catch (err) {
      setStatus(`Generate failed for ${chapterId}: ${err.message}`);
    } finally {
      state.generatingChapters.delete(chapterId);
      renderToc();
    }
  });
  await Promise.allSettled(promises);
  if (state.selectedChapterId && chapterIds.includes(state.selectedChapterId)) {
    await loadProject(state.project.project_id, state.selectedChapterId);
  }
  setStatus(`Generation complete`);
}

function wireSidebarResize() {
  const appShell = document.querySelector(".app-shell");
  let resizing = false;
  $("sidebar-resize-handle").addEventListener("mousedown", (e) => {
    e.preventDefault();
    resizing = true;
    document.body.classList.add("sidebar-resizing");
  });
  document.addEventListener("mousemove", (e) => {
    if (!resizing) return;
    const width = Math.max(200, Math.min(560, e.clientX));
    appShell.style.gridTemplateColumns = `${width}px minmax(0, 1fr)`;
  });
  document.addEventListener("mouseup", () => {
    if (!resizing) return;
    resizing = false;
    document.body.classList.remove("sidebar-resizing");
  });
}

function wireEvents() {
  $("open-book-modal").addEventListener("click", () => {
    $("project-title").value = "";
    $("book-modal").showModal();
    $("project-title").focus();
  });
  document.querySelectorAll(".close-modal").forEach((button) => {
    button.addEventListener("click", () => $("book-modal").close());
  });
  $("close-bulk-import-modal").addEventListener("click", () => {
    state.pendingBulkImportFiles = [];
    $("bulk-import-modal").close();
  });
  $("cancel-bulk-import").addEventListener("click", () => {
    state.pendingBulkImportFiles = [];
    $("bulk-import-modal").close();
  });
  $("bulk-import-list").addEventListener("click", (event) => {
    const button = event.target.closest(".bulk-import-move");
    if (!button) return;
    const item = button.closest(".bulk-import-item");
    movePendingBulkImportFile(Number(item.dataset.index), button.dataset.direction);
  });
  $("confirm-bulk-import").addEventListener("click", async () => {
    const files = [...state.pendingBulkImportFiles];
    state.pendingBulkImportFiles = [];
    $("bulk-import-modal").close();
    await bulkImportFiles(files, { analyze: true });
  });
  $("import-source-only").addEventListener("click", async () => {
    const files = [...state.pendingBulkImportFiles];
    state.pendingBulkImportFiles = [];
    $("bulk-import-modal").close();
    await bulkImportFiles(files, { analyze: false });
  });

  $("project-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = $("project-title").value.trim();
    if (!title) return;
    const project = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ title, language: "zh" }),
    });
    $("project-title").value = "";
    $("book-modal").close();
    await refreshProjects();
    await loadProject(project.project_id);
  });

  $("project-select").addEventListener("change", (event) => loadProject(event.target.value));
  $("busy-overlay").addEventListener("click", (event) => {
    const button = event.target.closest(".busy-cancel");
    if (!button) return;
    const job = state.busyJobs.get(button.dataset.jobId);
    job?.controller?.abort();
    if (job?.chapterId) cancelPipelineFromChapter(job.chapterId);
    removeBusyJob(button.dataset.jobId);
  });
  $("rename-book").addEventListener("click", renameCurrentBook);
  $("toc-list").addEventListener("click", (event) => {
    if (event.target.closest("#new-chapter")) {
      createNewChapter();
      return;
    }
    const cancelButton = event.target.closest(".toc-cancel");
    if (cancelButton) {
      cancelPipelineFromChapter(cancelButton.dataset.chapterId);
      return;
    }
    const menuBtn = event.target.closest(".toc-menu-btn");
    if (menuBtn) {
      const wrap = menuBtn.closest(".toc-menu-wrap");
      const isOpen = wrap.classList.contains("open");
      closeAllTocMenus();
      if (!isOpen) wrap.classList.add("open");
      return;
    }
    const deleteItem = event.target.closest(".toc-menu-delete");
    if (deleteItem) {
      const tocItem = deleteItem.closest(".toc-item");
      deleteChapterById(tocItem?.dataset.chapterId);
      return;
    }
    const item = event.target.closest(".toc-item");
    if (!item || !state.project) return;
    if (event.target.closest(".toc-menu-wrap")) return;
    loadProject(state.project.project_id, item.dataset.chapterId);
  });
  $("toc-list").addEventListener("dragstart", (event) => {
    const item = event.target.closest(".toc-item");
    if (!item) return;
    event.dataTransfer.setData("text/plain", item.dataset.chapterId);
    event.dataTransfer.effectAllowed = "move";
  });
  $("toc-list").addEventListener("dragover", (event) => {
    const item = event.target.closest(".toc-item");
    if (!item) return;
    event.preventDefault();
    document.querySelectorAll(".toc-item.drag-over").forEach((node) => node.classList.remove("drag-over"));
    item.classList.add("drag-over");
  });
  $("toc-list").addEventListener("dragleave", (event) => {
    event.target.closest(".toc-item")?.classList.remove("drag-over");
  });
  $("toc-list").addEventListener("drop", async (event) => {
    const item = event.target.closest(".toc-item");
    if (!item) return;
    event.preventDefault();
    document.querySelectorAll(".toc-item.drag-over").forEach((node) => node.classList.remove("drag-over"));
    const draggedId = event.dataTransfer.getData("text/plain");
    const targetId = item.dataset.chapterId;
    if (!draggedId || draggedId === targetId) return;
    const ids = state.chapters.map((chapter) => chapter.chapter_id).filter((id) => id !== draggedId);
    ids.splice(ids.indexOf(targetId), 0, draggedId);
    await reorderChapters(ids);
  });
  $("save-chapter").addEventListener("click", () => saveChapterPage());
  $("reset-annotations").addEventListener("click", resetAnnotations);
  $("delete-book").addEventListener("click", deleteCurrentBook);
  $("save-memory").addEventListener("click", saveMemory);
  $("save-characters").addEventListener("click", saveCharacterProfiles);
  $("save-cast").addEventListener("click", saveCast);
  $("add-cast").addEventListener("click", addCast);
  $("run-pipeline").addEventListener("click", runAnalyzeAnnotateBook);
  $("narration-mode").addEventListener("change", async () => {
    if (!state.project) return;
    const mode = $("narration-mode").value;
    try {
      await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/config`, {
        method: "POST",
        body: JSON.stringify({ narration_mode: mode }),
      });
      state.project = { ...state.project, narration_mode: mode };
      setStatus(`Narration mode: ${mode === "single_narrator" ? "Single narrator" : "Multi-voice cast"}`);
    } catch (err) {
      setStatus(err.message);
    }
  });
  $("synthesize").addEventListener("click", () => runStep("synthesize"));
  $("analyze-chapter").addEventListener("click", () => runChapterStep("analyze"));
  $("annotate-chapter").addEventListener("click", () => runChapterStep("annotate"));
  $("open-generate-modal").addEventListener("click", openGenerateModal);
  $("start-generate").addEventListener("click", startGenerate);
  $("generate-select-all").addEventListener("click", () => {
    document.querySelectorAll(".generate-chapter-check").forEach((cb) => { cb.checked = true; });
  });
  $("generate-select-none").addEventListener("click", () => {
    document.querySelectorAll(".generate-chapter-check").forEach((cb) => { cb.checked = false; });
  });
  document.querySelectorAll(".close-generate").forEach((btn) => {
    btn.addEventListener("click", () => $("generate-modal").close());
  });
  $("toggle-inspector").addEventListener("click", () => setInspectorOpen(true));
  $("close-inspector").addEventListener("click", () => setInspectorOpen(false));
  $("import-file").addEventListener("click", () => $("file-input").click());
  $("sidebar-import-file").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (event) => importFiles(event.target.files));
  $("add-pronunciation").addEventListener("click", () => {
    $("pronunciation-list").insertAdjacentHTML("beforeend", pronunciationRow());
  });
  $("add-chapter-change").addEventListener("click", addChapterChange);
  $("add-character").addEventListener("click", addCharacter);
  $("characters-list").addEventListener("click", (event) => {
    if (event.target.matches(".remove-character")) event.target.closest(".character-item").remove();
  });
  $("pronunciation-list").addEventListener("click", (event) => {
    if (event.target.matches(".remove-row")) event.target.closest(".kv-row").remove();
  });
  $("cast-list").addEventListener("click", (event) => {
    if (event.target.matches(".remove-cast")) event.target.closest(".cast-item").remove();
  });
  $("voice-library").addEventListener("click", (event) => {
    if (!event.target.matches(".voice-pill")) return;
    navigator.clipboard?.writeText(event.target.dataset.voiceId);
    setStatus(`Copied ${event.target.dataset.voiceId}`);
  });
  document.addEventListener("click", (event) => {
    const btn = event.target.closest(".voice-preview-btn");
    if (!btn) return;
    event.stopPropagation();
    const voiceId = btn.dataset.voiceId
      || btn.closest(".voice-select-wrap")?.querySelector("select")?.value;
    if (voiceId) playVoicePreview(voiceId);
  });
  document.addEventListener("click", (event) => {
    const btn = event.target.closest(".regen-chunk-btn");
    if (!btn) return;
    event.stopPropagation();
    regenerateChunk(Number(btn.dataset.chunk));
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".toc-menu-wrap")) closeAllTocMenus();
  });
  $("inline-annotations").addEventListener("click", (event) => {
    const tag = event.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") return;
    const item = event.target.closest(".inline-annotation-item");
    if (!item || !chapterAudio || !audioTimings.length) return;
    const index = Number(item.dataset.index);
    const timing = audioTimings[index];
    if (!timing) return;
    chapterAudio.currentTime = timing.start;
    if (chapterAudio.paused) {
      chapterAudio.play();
      const btn = $("audio-play-btn");
      if (btn) btn.textContent = "⏸";
    }
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((node) => node.classList.remove("active"));
      tab.classList.add("active");
      $(`${tab.dataset.tab}-panel`).classList.add("active");
    });
  });
  [
    "book-editor",
    "chapter-id",
    "chapter-title",
    "inline-annotations",
    "memory-panel",
    "characters-panel",
    "voices-panel",
  ].forEach((id) => {
    $(id)?.addEventListener("input", scheduleAutoSave);
    $(id)?.addEventListener("change", scheduleAutoSave);
  });
}

async function cancelPipelineFromChapter(chapterId) {
  state.pipelineCanceled = true;
  const job = state.busyJobs.get(chapterId);
  job?.controller?.abort();
  if (state.project) {
    try {
      await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/cancel-pipeline/${encodeURIComponent(chapterId)}`, {
        method: "POST",
        body: JSON.stringify({}),
      });
    } catch (error) {
      setStatus(`Cancel request failed: ${error.message}`);
    }
  }
  updateLocalChapterState(chapterId, { pipeline_state: "canceled", pipeline_message: "Canceled by user." });
  markSubsequentChapters(chapterId, { pipeline_state: "canceled", pipeline_message: `Canceled because ${chapterId} was canceled.` });
  state.busyJobs.clear();
  renderBusyJobs();
  renderToc();
  setStatus(`Canceled analysis from ${chapterId} onward`);
}

function clearChapterUi() {
  state.selectedChapterId = null;
  state.audioManifest = null;
  state.audioUrl = null;
  if (chapterAudio) {
    chapterAudio.pause();
    chapterAudio = null;
  }
  audioTimings = [];
  activePassageIndex = -1;
  state.chapters = [];
  state.annotations = [];
  state.annotatedText = "";
  $("toc-list").innerHTML = "";
  $("toc-count").textContent = "0";
  $("chapter-id").value = "ch01";
  $("chapter-title").value = "";
  $("book-editor").value = "";
  renderAnnotationsPanel();
  renderChapterHydration();
  renderMemory();
  renderTranscript();
}

function resetDeleteButton() {
  state.pendingDeleteChapterId = null;
  closeAllTocMenus();
}

function resetClearButton() {
  state.pendingClearAnnotationsId = null;
  const button = $("reset-annotations");
  if (!button) return;
  button.textContent = "Clear";
  button.classList.remove("armed");
}

function resetBookDeleteButton() {
  state.pendingDeleteBookId = null;
  const button = $("delete-book");
  if (!button) return;
  button.textContent = "🗑";
  button.classList.remove("armed");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

function safeId(value) {
  return String(value || "voice").trim().toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "_").replace(/^_+|_+$/g, "") || "voice";
}

window.addEventListener("DOMContentLoaded", async () => {
  wireEvents();
  wireSidebarResize();
  try {
    await refreshProjects();
  } catch (error) {
    setStatus(error.message);
  }
});
