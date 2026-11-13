<script setup>
// Ansicht für Lehrende: Was weiß der Tutor, und eigenes Material hinzufügen.
// Keine Anmeldung: Im Betrieb käme die Rolle aus Moodle (LTI), siehe README.
import { onActivated, onMounted, ref } from "vue";
import { getInfo, getSources, removeSource, uploadSource } from "../api.js";

const info = ref(null);
const sources = ref([]);
const loadError = ref("");

const file = ref(null);
const fileInput = ref(null);
const title = ref("");
const license = ref("");
const uploading = ref(false);
const uploadMessage = ref("");
const uploadError = ref("");

const licenses = [
  "CC BY 4.0",
  "CC BY-SA 4.0",
  "CC0 1.0",
  "Eigenes Material, nur für diesen Kurs",
];

async function load() {
  loadError.value = "";
  try {
    [info.value, sources.value] = await Promise.all([getInfo(), getSources()]);
  } catch (error) {
    loadError.value = error.message;
  }
}

function pick(event) {
  file.value = event.target.files[0] ?? null;
  uploadMessage.value = uploadError.value = "";
  if (file.value && !title.value) {
    title.value = file.value.name.replace(/\.(md|txt)$/i, ""); // Vorschlag aus dem Dateinamen
  }
}

async function upload() {
  if (!file.value) return;
  uploading.value = true;
  uploadMessage.value = uploadError.value = "";
  try {
    const source = await uploadSource(file.value, title.value, license.value);
    uploadMessage.value = `„${source.title}“ ist aufgenommen (${source.chunks} Abschnitte).`;
    file.value = null;
    title.value = license.value = "";
    fileInput.value.value = "";
    await load();
  } catch (error) {
    uploadError.value = error.message;
  } finally {
    uploading.value = false;
  }
}

async function remove(source) {
  if (!window.confirm(`„${source.title}“ wirklich entfernen?`)) return;
  try {
    await removeSource(source.id);
    await load();
  } catch (error) {
    loadError.value = error.message;
  }
}

onMounted(load);
onActivated(load); // beim Zurückwechseln auf den Reiter aktualisieren
</script>

<template>
  <section class="knowledge">
    <p v-if="loadError" class="error">{{ loadError }}</p>

    <div v-if="info" class="card">
      <h2>Was der Tutor weiß</h2>
      <dl class="facts">
        <dt>Buch</dt>
        <dd>„{{ info.book_title }}“ von {{ info.book_author }}, {{ info.book_license }}</dd>
        <dt>Umfang</dt>
        <dd>{{ info.book_chapters }} Kapitel in {{ info.book_chunks }} Abschnitten</dd>
        <dt>Eigenes Material</dt>
        <dd>
          {{ info.uploads }} {{ info.uploads === 1 ? "Datei" : "Dateien" }} in
          {{ info.upload_chunks }} Abschnitten
        </dd>
        <dt>Sprachmodell</dt>
        <dd>
          {{ info.llm_model }}
          <span :class="info.llm_reachable ? 'ok' : 'error'">
            {{ info.llm_reachable ? "erreichbar" : "nicht erreichbar" }}
          </span>
        </dd>
        <dt>Suche</dt>
        <dd>{{ info.embedding_model }}</dd>
      </dl>
    </div>

    <div class="card">
      <h2>Eigenes Material hinzufügen</h2>
      <p>
        Skripte, Merkblätter oder Aufgabenerklärungen als <strong>Markdown (.md)</strong> oder
        <strong>Text (.txt)</strong>, höchstens 1 MB. Markdown wird an den Überschriften zerlegt.
        Der Tutor nutzt das Material ab sofort als Quelle und nennt es mit Titel und Lizenz.
      </p>
      <form class="upload" @submit.prevent="upload">
        <label>
          Datei
          <input ref="fileInput" type="file" accept=".md,.txt" required @change="pick" />
        </label>
        <label>
          Titel
          <input v-model="title" type="text" maxlength="200" required placeholder="z. B. Skript Woche 3" />
        </label>
        <label>
          Lizenz
          <input v-model="license" type="text" maxlength="200" required list="licenses" placeholder="z. B. CC BY 4.0" />
          <datalist id="licenses">
            <option v-for="option in licenses" :key="option" :value="option" />
          </datalist>
        </label>
        <button type="submit" class="primary" :disabled="uploading || !file">
          {{ uploading ? "Wird aufgenommen …" : "Hochladen" }}
        </button>
      </form>
      <p v-if="uploadMessage" class="ok">{{ uploadMessage }}</p>
      <p v-if="uploadError" class="error">{{ uploadError }}</p>
    </div>

    <div class="card">
      <h2>Hochgeladenes Material</h2>
      <p v-if="!sources.length" class="muted">Noch nichts hochgeladen.</p>
      <ul v-else class="source-list">
        <li v-for="source in sources" :key="source.id">
          <div>
            <strong>{{ source.title }}</strong>
            <span class="muted">
              · {{ source.license }} · {{ source.chunks }} Abschnitte ·
              {{ new Date(source.uploaded_at).toLocaleDateString("de-DE") }}
            </span>
          </div>
          <button class="danger" @click="remove(source)">Entfernen</button>
        </li>
      </ul>
    </div>
  </section>
</template>
