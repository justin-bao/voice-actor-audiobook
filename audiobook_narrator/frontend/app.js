const state = {
  projects: [],
  project: null,
  selectedChapterId: null,
  memory: null,
  annotations: [],
  cast: null,
  elevenVoices: [],
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

async function refreshProjects() {
  const payload = await api("/api/projects");
  state.projects = payload.projects || [];
  $("project-select").innerHTML = state.projects
    .map((p) => `<option value="${escapeHtml(p.project_id)}">${escapeHtml(p.title)} · ${escapeHtml(p.project_id)}</option>`)
    .join("");
  if (!state.project && state.projects.length) {
    await loadProject(state.projects[0].project_id);
  }
}

async function loadProject(projectId, chapterId = null) {
  if (!projectId) return;
  const query = chapterId ? `?chapter=${encodeURIComponent(chapterId)}` : "";
  const payload = await api(`/api/projects/${encodeURIComponent(projectId)}${query}`);
  state.project = payload.config;
  state.selectedChapterId = payload.selected_chapter_id;
  state.memory = payload.memory;
  state.annotations = payload.annotations || [];
  state.cast = payload.cast;
  renderProject(payload);
  setStatus(`${payload.config.title} loaded`);
}

function renderProject(payload) {
  $("project-meta").textContent = `${payload.config.title} · ${payload.config.language}`;
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
  renderAnnotations();
  renderCast();
}

function renderMemory() {
  const memory = state.memory || {};
  $("plot-summary").value = memory.plot_summary || "";
  $("current-state").value = memory.current_state || "";
  $("themes").value = (memory.themes || []).join(", ");
  const notes = memory.pronunciation_notes || {};
  $("pronunciation-list").innerHTML = Object.entries(notes)
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

function renderAnnotations() {
  $("annotations-list").innerHTML = state.annotations
    .map((row, index) => `
      <article class="item annotation-item" data-index="${index}">
        <div class="item-head">
          <div class="item-title">${escapeHtml(row.passage_id || `passage-${index}`)}</div>
          <button class="danger remove-annotation" title="Remove">×</button>
        </div>
        <div class="annotation-grid">
          <input class="ann-speaker" value="${escapeAttr(row.speaker || "Narrator")}" placeholder="speaker" />
          ${selectHtml("ann-emotion", emotions, row.emotion || "neutral")}
          ${selectHtml("ann-delivery", deliveries, row.delivery || "matter-of-fact")}
          ${selectHtml("ann-pace", paces, row.pace || "medium")}
          <textarea class="ann-text">${escapeHtml(row.text || "")}</textarea>
          <input class="ann-rationale" value="${escapeAttr(row.rationale || "")}" placeholder="rationale" />
          <input class="ann-pause" type="number" min="0" value="${Number(row.pause_after_ms || 350)}" title="pause ms" />
          <input class="ann-intensity" type="number" min="1" max="5" value="${Number(row.intensity || 3)}" title="intensity" />
        </div>
      </article>
    `)
    .join("");
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
  const projectId = state.project?.project_id;
  if (!projectId) return;
  await api(`/api/projects/${encodeURIComponent(projectId)}/chapters`, {
    method: "POST",
    body: JSON.stringify({
      chapter_id: $("chapter-id").value.trim(),
      title: $("chapter-title").value.trim(),
      text: $("book-editor").value,
    }),
  });
  await loadProject(projectId, $("chapter-id").value.trim());
  setStatus("Chapter saved");
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

async function saveAnnotations() {
  if (!state.project || !state.selectedChapterId) return;
  const annotations = [];
  document.querySelectorAll(".annotation-item").forEach((item, index) => {
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
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/annotations/${encodeURIComponent(state.selectedChapterId)}`, {
    method: "POST",
    body: JSON.stringify({ annotations }),
  });
  state.annotations = annotations;
  setStatus("Annotations saved");
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
  await api(`/api/projects/${encodeURIComponent(state.project.project_id)}/run`, {
    method: "POST",
    body: JSON.stringify({
      step,
      chapter_id: state.selectedChapterId,
      backend: $("tts-backend").value,
    }),
  });
  await loadProject(state.project.project_id, state.selectedChapterId);
  setStatus(`${step} complete`);
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
    const projectId = $("project-id").value.trim();
    const title = $("project-title").value.trim();
    if (!projectId || !title) return;
    await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, title, language: "zh" }),
    });
    await refreshProjects();
    await loadProject(projectId);
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
    renderAnnotations();
  });
  $("save-chapter").addEventListener("click", saveChapter);
  $("save-memory").addEventListener("click", saveMemory);
  $("save-characters").addEventListener("click", saveMemory);
  $("save-annotations").addEventListener("click", saveAnnotations);
  $("save-cast").addEventListener("click", saveCast);
  $("add-cast").addEventListener("click", addCast);
  $("run-analyze").addEventListener("click", () => runStep("analyze"));
  $("run-annotate").addEventListener("click", () => runStep("annotate"));
  $("run-cast").addEventListener("click", () => runStep("cast"));
  $("synthesize").addEventListener("click", () => runStep("synthesize"));
  $("import-file").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (event) => importFile(event.target.files[0]));
  $("add-pronunciation").addEventListener("click", () => {
    $("pronunciation-list").insertAdjacentHTML("beforeend", pronunciationRow());
  });
  $("add-character").addEventListener("click", addCharacter);
  $("characters-list").addEventListener("click", (event) => {
    if (event.target.matches(".remove-character")) event.target.closest(".character-item").remove();
  });
  $("annotations-list").addEventListener("click", (event) => {
    if (event.target.matches(".remove-annotation")) event.target.closest(".annotation-item").remove();
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
