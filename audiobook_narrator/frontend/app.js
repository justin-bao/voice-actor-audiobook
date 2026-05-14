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
  pendingDeleteChapterId: null,
  pendingDeleteBookId: null,
  pendingClearAnnotationsId: null,
  loadingElevenVoices: false,
  autoSaveTimer: null,
  autoSaving: false,
};

const $ = (id) => document.getElementById(id);

const emotions = ["neutral", "tense", "fearful", "angry", "tender", "grief", "wonder", "comic", "solemn", "urgent"];
const deliveries = ["matter-of-fact", "dramatic", "intimate", "reflective", "clipped", "lyrical", "conversational", "suspenseful"];
const paces = ["slow", "medium", "quick"];

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
  $("busy-title").textContent = title;
  $("busy-detail").textContent = detail;
  $("busy-overlay").hidden = !isBusy;
  document.querySelectorAll("button, input, select, textarea").forEach((element) => {
    element.disabled = isBusy;
  });
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
  renderCast();
}

function renderToc() {
  $("toc-count").textContent = String(state.chapters.length);
  $("toc-list").innerHTML = state.chapters
    .map((chapter, index) => `
      <li class="toc-item ${chapter.chapter_id === state.selectedChapterId ? "active" : ""}"
        draggable="true"
        data-chapter-id="${escapeAttr(chapter.chapter_id)}">
        <span class="toc-handle" aria-hidden="true">☰</span>
        <button class="toc-title" title="${escapeAttr(chapter.title || chapter.chapter_id)}">
          ${index + 1}. ${escapeHtml(chapter.title || chapter.chapter_id)}
        </button>
        <button class="toc-delete danger" title="Delete chapter">×</button>
      </li>
    `)
    .join("") + `
      <li class="toc-add-row">
        <button id="new-chapter" class="toc-add" title="Add chapter">＋ Chapter</button>
      </li>
    `;
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
          ${providerVoiceControl(voice?.provider_voice || "", "character-provider-voice")}
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
  return `
    <article class="inline-annotation-item" data-index="${index}">
      <div class="annotation-meta-row">
        <input class="ann-speaker" value="${escapeAttr(row.speaker || "Narrator")}" placeholder="speaker" />
        ${selectHtml("ann-emotion", emotions, row.emotion || "neutral")}
        ${selectHtml("ann-delivery", deliveries, row.delivery || "matter-of-fact")}
        ${selectHtml("ann-pace", paces, row.pace || "medium")}
        <input class="ann-intensity" type="number" min="1" max="5" value="${Number(row.intensity || 3)}" title="intensity" />
        <input class="ann-pause" type="number" min="0" value="${Number(row.pause_after_ms || 350)}" title="pause ms" />
      </div>
      <input class="ann-tags" value="${escapeAttr((row.audio_tags || []).join(", "))}" placeholder="[tense], [whispers]" />
      <textarea class="ann-text">${escapeHtml(row.text || "")}</textarea>
      <input class="ann-rationale" value="${escapeAttr(row.rationale || "")}" placeholder="rationale" />
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
      `tags=${(row.audio_tags || []).join(",")}`,
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
      return `
        <article class="item cast-item" data-character="${escapeAttr(assignment.character)}">
          <div class="item-head">
            <input class="cast-character item-title" value="${escapeAttr(assignment.character)}" />
            <button class="danger remove-cast" title="Remove">×</button>
          </div>
          <div class="cast-grid">
            <input class="cast-voice-id" value="${escapeAttr(assignment.voice_id)}" placeholder="voice id" />
            ${providerVoiceControl(voice.provider_voice || "")}
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

function providerVoiceControl(selectedVoiceId = "", className = "cast-provider-voice") {
  if (!state.elevenVoices.length) {
    return `<input class="${className}" value="${escapeAttr(selectedVoiceId)}" placeholder="provider voice / ElevenLabs voice id" />`;
  }
  const selectedExists = state.elevenVoices.some((voice) => voice.voice_id === selectedVoiceId);
  const options = state.elevenVoices
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
  return `<select class="${className}"><option value="">Select ElevenLabs voice...</option>${current}${options}</select>`;
}

function renderElevenVoices() {
  $("voice-library").innerHTML = state.elevenVoices
    .slice(0, 40)
    .map((voice) => `<button class="voice-pill" data-voice-id="${escapeAttr(voice.voice_id)}">${escapeHtml(voice.name || voice.voice_id)}</button>`)
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
      speaker: item.querySelector(".ann-speaker").value || "Narrator",
      emotion: item.querySelector(".ann-emotion").value,
      delivery: item.querySelector(".ann-delivery").value,
      pace: item.querySelector(".ann-pace").value,
      intensity: Number(item.querySelector(".ann-intensity").value || 3),
      pause_after_ms: Number(item.querySelector(".ann-pause").value || 350),
      audio_tags: item.querySelector(".ann-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean),
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
    state.pendingClearAnnotationsId = null;
    button.textContent = "Clear";
    button.classList.remove("armed");
    renderAnnotationsPanel();
    renderTranscript();
    setStatus("Chapter annotations cleared");
  } catch (error) {
    setStatus(`Clear failed: ${error.message}`);
  }
}

async function deleteChapterById(chapterId, button) {
  if (!state.project || !chapterId || !button) return;
  if (state.pendingDeleteChapterId !== chapterId) {
    state.pendingDeleteChapterId = chapterId;
    button.textContent = "!";
    button.classList.add("armed");
    setStatus(`Click Confirm Delete to remove ${chapterId}`);
    return;
  }
  button.disabled = true;
  setStatus(`Deleting ${chapterId}...`);
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/delete-chapter/${encodeURIComponent(chapterId)}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    resetDeleteButton();
    await loadProject(state.project.project_id);
    setStatus(`Deleted ${chapterId}`);
  } catch (error) {
    setStatus(`Delete failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function deleteCurrentChapter() {
  const item = document.querySelector(`.toc-item[data-chapter-id="${CSS.escape(state.selectedChapterId || "")}"]`);
  await deleteChapterById(state.selectedChapterId, item?.querySelector(".toc-delete"));
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

async function runStep(step) {
  if (!state.project) return;
  const busyCopy = {
    analyze: ["Analyzing", "Updating plot, character memory, and story understanding..."],
    annotate: ["Annotating", "Waiting on narration annotations, speaker labels, and emotion tags..."],
    cast: ["Casting Voices", "Assigning voices to characters and speakers..."],
    synthesize: ["Generating", "Creating narration output..."],
  }[step] || ["Working", "Processing..."];
  setBusy(true, busyCopy[0], busyCopy[1]);
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
  await bulkImportFiles(files);
}

async function bulkImportFiles(fileList) {
  if (!state.project || !fileList?.length) return;
  const files = Array.from(fileList).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  setBusy(true, "Importing", `Importing and analyzing ${files.length} chapters in order...`);
  setStatus(`Import started for ${files.length} chapters`);
  try {
    const uploads = [];
    for (const file of files) {
      uploads.push({ filename: file.name, data: await readAsDataUrl(file) });
    }
    const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/bulk-import`, {
      method: "POST",
      body: JSON.stringify({ files: uploads, analyze: true }),
    });
    await loadProject(state.project.project_id);
    setStatus(`Imported ${payload.manifests?.length || 0} chapters and updated memory`);
  } catch (error) {
    setStatus(`Import failed: ${error.message}`);
  } finally {
    setBusy(false);
  }
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

function wireEvents() {
  $("open-book-modal").addEventListener("click", () => {
    $("project-title").value = "";
    $("book-modal").showModal();
    $("project-title").focus();
  });
  document.querySelectorAll(".close-modal").forEach((button) => {
    button.addEventListener("click", () => $("book-modal").close());
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
  $("rename-book").addEventListener("click", renameCurrentBook);
  $("toc-list").addEventListener("click", (event) => {
    if (event.target.closest("#new-chapter")) {
      createNewChapter();
      return;
    }
    const deleteButton = event.target.closest(".toc-delete");
    if (deleteButton) {
      const item = deleteButton.closest(".toc-item");
      deleteChapterById(item?.dataset.chapterId, deleteButton);
      return;
    }
    const item = event.target.closest(".toc-item");
    if (!item || !state.project) return;
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
  $("run-analyze").addEventListener("click", () => runStep("analyze"));
  $("run-annotate").addEventListener("click", () => runStep("annotate"));
  $("run-cast").addEventListener("click", () => runStep("cast"));
  $("synthesize").addEventListener("click", () => runStep("synthesize"));
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

function clearChapterUi() {
  state.selectedChapterId = null;
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
  document.querySelectorAll(".toc-delete").forEach((button) => {
    button.textContent = "×";
    button.classList.remove("armed");
  });
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
  try {
    await refreshProjects();
  } catch (error) {
    setStatus(error.message);
  }
});
