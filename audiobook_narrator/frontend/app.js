const state = {
  projects: [],
  chapters: [],
  project: null,
  selectedChapterId: null,
  memory: null,
  annotations: [],
  annotatedText: "",
  cast: null,
  elevenVoices: [],
  pendingDeleteChapterId: null,
  pendingDeleteBookId: null,
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
  const query = chapterId ? `?chapter=${encodeURIComponent(chapterId)}` : "";
  const payload = await api(`/api/projects/${encodeURIComponent(projectId)}${query}`);
  state.project = payload.config;
  state.selectedChapterId = payload.selected_chapter_id;
  state.memory = payload.memory;
  state.annotations = payload.annotations || [];
  state.annotatedText = payload.annotated_text || "";
  state.chapters = payload.chapters || [];
  state.cast = payload.cast;
  renderProject(payload);
  setStatus(`${payload.config.title} loaded`);
}

function renderProject(payload) {
  $("project-meta").textContent = payload.config.title;
  $("project-select").value = payload.config.project_id;
  $("chapter-select").innerHTML = payload.chapters
    .map((c) => `<option value="${escapeHtml(c.chapter_id)}">${escapeHtml(c.title || c.chapter_id)}</option>`)
    .join("");
  if (payload.selected_chapter_id) $("chapter-select").value = payload.selected_chapter_id;
  const chapter = payload.chapters.find((c) => c.chapter_id === payload.selected_chapter_id);
  $("chapter-id").value = payload.selected_chapter_id || "ch01";
  $("chapter-title").value = chapter?.title || "";
  $("book-editor").value = payload.source_text || "";
  renderMemory();
  renderCharacters();
  renderAnnotationsPanel();
  renderTranscript();
  renderCast();
}

function renderMemory() {
  const memory = state.memory || {};
  $("plot-summary").value = memory.plot_summary || "";
  $("current-state").value = memory.current_state || "";
  $("themes").value = (memory.themes || []).join(", ");
  $("pronunciation-list").innerHTML = Object.entries(memory.pronunciation_notes || {})
    .map(([key, value]) => pronunciationRow(key, value))
    .join("");
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
    .map((character) => `
      <article class="item character-item" data-name="${escapeAttr(character.name)}">
        <div class="item-head">
          <input class="character-name item-title" value="${escapeAttr(character.name)}" />
          <button class="danger remove-character" title="Remove">×</button>
        </div>
        <div class="character-grid">
          <textarea class="character-personality" placeholder="Personality">${escapeHtml(character.personality || "")}</textarea>
          <textarea class="character-role" placeholder="Role in plot">${escapeHtml(character.role_in_plot || "")}</textarea>
          <input class="character-aliases" value="${escapeAttr((character.aliases || []).join(", "))}" placeholder="aliases" />
          <input class="character-voice-notes" value="${escapeAttr(character.voice_notes || "")}" placeholder="voice notes" />
        </div>
      </article>
    `)
    .join("");
}

function renderAnnotationsPanel() {
  $("annotations-list").innerHTML = state.annotations.length
    ? `<div class="empty-note">Annotations are editable directly in the transcript. Use Save Transcript to persist changes.</div>`
    : `<div class="empty-note">Run Annotate to add editable speaker, emotion, delivery, and pacing controls to the transcript.</div>`;
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
            <input class="cast-provider-voice" value="${escapeAttr(voice.provider_voice || "")}" placeholder="provider voice / ElevenLabs voice id" />
            <textarea class="cast-reason" placeholder="reason">${escapeHtml(assignment.reason || "")}</textarea>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderElevenVoices() {
  $("voice-library").innerHTML = state.elevenVoices
    .slice(0, 40)
    .map((voice) => `<button class="voice-pill" data-voice-id="${escapeAttr(voice.voice_id)}">${escapeHtml(voice.name || voice.voice_id)}</button>`)
    .join("");
}

function selectHtml(className, options, selected) {
  return `<select class="${className}">${options
    .map((option) => `<option value="${escapeAttr(option)}" ${option === selected ? "selected" : ""}>${escapeHtml(option)}</option>`)
    .join("")}</select>`;
}

async function saveChapter() {
  if (!state.project || !state.selectedChapterId) return;
  if (state.annotations.length) {
    const annotations = collectAnnotations();
    await saveAnnotationsPayload(annotations);
    await saveAnnotatedTextPayload(buildEmbeddedAnnotationText(annotations));
    state.annotations = annotations;
    state.annotatedText = buildEmbeddedAnnotationText(annotations);
    renderAnnotationsPanel();
    renderTranscript();
    setStatus("Transcript annotations saved");
    return;
  }
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/chapters`, {
    method: "POST",
    body: JSON.stringify({
      chapter_id: $("chapter-id").value.trim(),
      title: $("chapter-title").value.trim(),
      text: $("book-editor").value,
    }),
  });
  await loadProject(state.project.project_id, $("chapter-id").value.trim());
  setStatus("Transcript saved");
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
  renderCharacters();
  setStatus("Memory saved");
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
      personality: item.querySelector(".character-personality").value,
      role_in_plot: item.querySelector(".character-role").value,
      relationships: {},
      voice_notes: item.querySelector(".character-voice-notes").value,
      evidence: [],
    };
  });
  return memory;
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
      rationale: item.querySelector(".ann-rationale").value,
      pronunciation_hints: original.pronunciation_hints || {},
    });
  });
  return annotations;
}

async function resetAnnotations() {
  if (!state.project || !state.selectedChapterId) return;
  setStatus(`Resetting annotations for ${state.selectedChapterId}...`);
  try {
    await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/reset-annotations/${encodeURIComponent(state.selectedChapterId)}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.annotations = [];
    state.annotatedText = "";
    renderAnnotationsPanel();
    renderTranscript();
    setStatus("Chapter annotations reset");
  } catch (error) {
    setStatus(`Reset failed: ${error.message}`);
  }
}

async function deleteCurrentChapter() {
  if (!state.project || !state.selectedChapterId) return;
  const chapterId = state.selectedChapterId;
  const button = $("delete-chapter");
  if (state.pendingDeleteChapterId !== chapterId) {
    state.pendingDeleteChapterId = chapterId;
    button.textContent = "Confirm Delete";
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
    const remaining = payload.remaining_chapter_ids || [];
    if (remaining.length) {
      await loadProject(state.project.project_id, remaining[0]);
    } else {
      clearChapterUi();
    }
    setStatus(`Deleted ${chapterId}`);
  } catch (error) {
    setStatus(`Delete failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function deleteCurrentBook() {
  if (!state.project) return;
  const bookId = state.project.project_id;
  const button = $("delete-book");
  if (state.pendingDeleteBookId !== bookId) {
    state.pendingDeleteBookId = bookId;
    button.textContent = "Confirm Book Delete";
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
        backend: $("tts-backend").value,
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

async function bulkImportFiles(fileList) {
  if (!state.project || !fileList?.length) return;
  const files = Array.from(fileList).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  setBusy(true, "Bulk Importing", `Importing and analyzing ${files.length} chapters in order...`);
  setStatus(`Bulk import started for ${files.length} chapters`);
  try {
    const uploads = [];
    for (const file of files) {
      uploads.push({ filename: file.name, data: await readAsDataUrl(file) });
    }
    const payload = await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/bulk-import`, {
      method: "POST",
      body: JSON.stringify({ files: uploads, analyze: true }),
    });
    const last = payload.manifests?.at(-1)?.chapter_id || state.selectedChapterId;
    await loadProject(state.project.project_id, last);
    setStatus(`Imported ${payload.manifests?.length || 0} chapters and updated memory`);
  } catch (error) {
    setStatus(`Bulk import failed: ${error.message}`);
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

function wireEvents() {
  $("project-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = $("project-title").value.trim();
    if (!title) return;
    const project = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ title, language: "zh" }),
    });
    $("project-title").value = "";
    await refreshProjects();
    await loadProject(project.project_id);
  });

  $("project-select").addEventListener("change", (event) => loadProject(event.target.value));
  $("chapter-select").addEventListener("change", (event) => loadProject(state.project.project_id, event.target.value));
  $("new-chapter").addEventListener("click", () => {
    const next = `ch${String(($("chapter-select").options.length || 0) + 1).padStart(2, "0")}`;
    $("chapter-id").value = next;
    $("chapter-title").value = "";
    $("book-editor").value = "";
    state.selectedChapterId = next;
    state.annotations = [];
    state.annotatedText = "";
    resetDeleteButton();
    renderAnnotationsPanel();
    renderTranscript();
  });
  $("save-chapter").addEventListener("click", saveChapter);
  $("reset-annotations").addEventListener("click", resetAnnotations);
  $("delete-chapter").addEventListener("click", deleteCurrentChapter);
  $("delete-book").addEventListener("click", deleteCurrentBook);
  $("save-memory").addEventListener("click", saveMemory);
  $("save-characters").addEventListener("click", saveMemory);
  $("save-annotations").addEventListener("click", saveChapter);
  $("save-cast").addEventListener("click", saveCast);
  $("add-cast").addEventListener("click", addCast);
  $("run-analyze").addEventListener("click", () => runStep("analyze"));
  $("run-annotate").addEventListener("click", () => runStep("annotate"));
  $("run-cast").addEventListener("click", () => runStep("cast"));
  $("synthesize").addEventListener("click", () => runStep("synthesize"));
  $("import-file").addEventListener("click", () => $("file-input").click());
  $("bulk-import").addEventListener("click", () => $("bulk-file-input").click());
  $("file-input").addEventListener("change", (event) => importFile(event.target.files[0]));
  $("bulk-file-input").addEventListener("change", (event) => bulkImportFiles(event.target.files));
  $("add-pronunciation").addEventListener("click", () => {
    $("pronunciation-list").insertAdjacentHTML("beforeend", pronunciationRow());
  });
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
  $("load-elevenlabs").addEventListener("click", async () => {
    const payload = await api("/api/elevenlabs/voices");
    state.elevenVoices = payload.voices || [];
    renderElevenVoices();
    setStatus(`${state.elevenVoices.length} ElevenLabs voices loaded`);
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((node) => node.classList.remove("active"));
      tab.classList.add("active");
      $(`${tab.dataset.tab}-panel`).classList.add("active");
    });
  });
}

function clearChapterUi() {
  state.selectedChapterId = null;
  state.chapters = [];
  state.annotations = [];
  state.annotatedText = "";
  $("chapter-select").innerHTML = "";
  $("chapter-id").value = "ch01";
  $("chapter-title").value = "";
  $("book-editor").value = "";
  renderAnnotationsPanel();
  renderTranscript();
}

function resetDeleteButton() {
  state.pendingDeleteChapterId = null;
  const button = $("delete-chapter");
  if (!button) return;
  button.textContent = "Delete Chapter";
  button.classList.remove("armed");
}

function resetBookDeleteButton() {
  state.pendingDeleteBookId = null;
  const button = $("delete-book");
  if (!button) return;
  button.textContent = "Delete Book";
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

window.addEventListener("DOMContentLoaded", async () => {
  wireEvents();
  try {
    await refreshProjects();
  } catch (error) {
    setStatus(error.message);
  }
});
