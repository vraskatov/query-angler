<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { ask } from "../api.js";
import ModeSwitch from "./ModeSwitch.vue";

const question = ref("");
const mode = ref("erklaeren");
const exchanges = ref([]); // { question, mode, answer, error }
const busy = ref(false);
const seconds = ref(0);
let timer = null;

// Im sokratischen Modus fragte das Modell bei vagen Anfragen nicht nach der Aufgabe, sondern
// verfolgte einen eigenen Lehrplan. Diese Hilfe hängt nicht vom Modell ab.
const placeholder = computed(() =>
  mode.value === "sokratisch"
    ? "Beschreib deine Aufgabe: Welche Tabellen hast du, was soll herauskommen?"
    : "Deine Frage zu SQL …",
);

const canSend = computed(() => question.value.trim().length >= 3 && !busy.value);

// So viele Wortwechsel nimmt das Backend höchstens an (HISTORY_TURNS in prompt.py).
const HISTORY_TURNS = 5;

const examples = [
  "Was unterscheidet WHERE von HAVING?",
  "Wie zähle ich, wie viele Abteilungen es pro Ort gibt?",
  "Wie verknüpfe ich eine Tabelle mit sich selbst?",
];

// Die letzten beantworteten Wortwechsel, älteste zuerst. Fehlgeschlagene zählen nicht.
function history() {
  return exchanges.value
    .filter((entry) => entry.answer)
    .slice(-HISTORY_TURNS)
    .map((entry) => ({ question: entry.question, answer: entry.answer.text }));
}

function send() {
  if (!canSend.value) return;
  const text = question.value.trim();
  question.value = "";
  submit(text, mode.value);
}

// "Spicken": Die letzte Nachricht noch einmal im Modus Erklären stellen, samt Verlauf.
// Die Lernenden entscheiden selbst, wann sie die Schreibweise sehen wollen. Der sokratische
// Modus bleibt streng; die Syntax kann man sich nicht erschließen, nur einmal sehen.
function peek(entry) {
  if (!busy.value) submit(entry.question, "erklaeren", true);
}

function canPeek(entry, index) {
  return (
    index === exchanges.value.length - 1 &&
    entry.mode === "sokratisch" &&
    entry.answer &&
    !entry.answer.text.startsWith("Dazu enthält meine Wissensbasis nichts")
  );
}

async function submit(text, modeValue, peeked = false) {
  const earlier = history(); // vor dem Hinzufügen der neuen Frage
  exchanges.value.push({ question: text, mode: modeValue, peeked, answer: null, error: null });
  const entry = exchanges.value.at(-1); // die reaktive Fassung, damit Änderungen sichtbar werden
  busy.value = true;
  seconds.value = 0;
  timer = setInterval(() => seconds.value++, 1000);
  try {
    entry.answer = await ask(entry.question, entry.mode, earlier);
  } catch (error) {
    entry.error = error.message;
  } finally {
    clearInterval(timer);
    busy.value = false;
    await nextTick();
    document.querySelector(".exchange:last-of-type")?.scrollIntoView({ behavior: "smooth" });
  }
}

function newConversation() {
  if (!busy.value) exchanges.value = [];
}

function onKeydown(event) {
  // Enter schickt ab, Umschalt+Enter macht einen Zeilenumbruch
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
}

// Das Modell antwortet in Markdown. DOMPurify entfernt alles, was im Browser
// ausgeführt werden könnte, damit eine Antwort keinen Code in die Seite einschleust.
function render(text) {
  return DOMPurify.sanitize(marked.parse(escapePlaceholders(text)));
}

// Platzhalter wie <tabelle1> hielte der Browser außerhalb von Code für HTML-Tags und
// DOMPurify würde sie entfernen. In Codeblöcken und `Code` maskiert marked selbst.
function escapePlaceholders(text) {
  return text
    .split("```")
    .map((part, i) =>
      i % 2 ? part : part.replace(/(`[^`]*`)|<([\wäöüÄÖÜß]{1,30})>/g, (m, code, name) => code ?? `&lt;${name}&gt;`),
    )
    .join("```");
}

// Bei einer Absage gehören die gefundenen Abschnitte nicht zur Antwort.
function showSources(answer) {
  return answer.sources.length > 0 && !answer.text.startsWith("Dazu enthält meine Wissensbasis nichts");
}

// Zitiert eine Erklärung keine Quelle (etwa bei einem Gruß), heißt die Liste anders,
// damit niemand denkt, die Antwort stütze sich darauf.
function sourcesLabel(entry) {
  if (entry.mode === "sokratisch") return "Zum Nachlesen";
  return entry.answer.sources.some((s) => s.cited) ? "Quellen" : "Gefundene Abschnitte, nicht zitiert";
}

function link(source) {
  return source.permalink || source.url || null;
}

function notes(answer) {
  const list = [];
  if (answer.checks.some((c) => c.startsWith("Absage"))) {
    list.push("Das Buch und das hochgeladene Material enthalten dazu nichts.");
  }
  if (answer.syntax_stage) {
    list.push("Der Tutor zeigt hier nur das Gerüst. Deine eigenen Tabellen und Spalten setzt du selbst ein.");
  }
  if (answer.attempts > 1) {
    list.push("Der erste Hinweis verriet zu viel, der Tutor hat ihn neu formuliert.");
  }
  return list;
}

onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <section class="ask">
    <div v-if="!exchanges.length" class="intro">
      <p>
        Stell eine Frage zu SQL. Im Modus <strong>Erklären</strong> bekommst du eine Antwort mit
        Beispiel, im Modus <strong>Sokratisch</strong> nur einen Hinweis und eine Rückfrage, damit
        du selbst auf die Lösung kommst. Beschreib dafür deine Aufgabe möglichst genau.
      </p>
      <p>Die erste Frage kann etwas länger dauern, weil das Sprachmodell erst geladen wird.</p>
      <p class="examples">
        Zum Beispiel:
        <button v-for="example in examples" :key="example" class="chip" @click="question = example">
          {{ example }}
        </button>
      </p>
    </div>

    <article v-for="(entry, index) in exchanges" :key="index" class="exchange">
      <p class="question">
        <span class="badge">{{ entry.mode === "sokratisch" ? "Sokratisch" : "Erklären" }}</span>
        <span v-if="entry.peeked" class="badge">Gespickt</span>
        {{ entry.question }}
      </p>

      <p v-if="!entry.answer && !entry.error" class="waiting">
        Der Tutor denkt nach … {{ seconds }} s
        <span v-if="seconds >= 5" class="note">
          Beim ersten Mal oder nach längerer Pause wird das Sprachmodell erst geladen, das kann
          etwas länger dauern.
        </span>
      </p>
      <p v-if="entry.error" class="error">{{ entry.error }}</p>

      <template v-if="entry.answer">
        <div class="answer" v-html="render(entry.answer.text)" />
        <p v-for="note in notes(entry.answer)" :key="note" class="note">{{ note }}</p>

        <details v-if="showSources(entry.answer)" class="sources" :open="entry.mode === 'sokratisch'">
          <summary>
            {{ sourcesLabel(entry) }}
            ({{ entry.answer.sources.length }})
          </summary>
          <ol>
            <li
              v-for="source in entry.answer.sources"
              :key="source.id"
              :class="{ cited: source.cited }"
              :value="source.number"
            >
              <a v-if="link(source)" :href="link(source)" target="_blank" rel="noopener">
                {{ source.chapter }} › {{ source.section }}
              </a>
              <span v-else>{{ source.chapter }} › {{ source.section }}</span>
              <span v-if="source.role === 'solution'" class="tag">Musterlösung</span>
              <span class="license">{{ source.license }}</span>
            </li>
          </ol>
        </details>

        <p class="meta">
          <button
            v-if="canPeek(entry, index)"
            type="button"
            class="link peek"
            :disabled="busy"
            @click="peek(entry)"
          >
            Spicken: im Modus Erklären zeigen
          </button>
          {{ entry.answer.model }} · {{ entry.answer.timings.llm?.toFixed(1) ?? "–" }} s
        </p>
      </template>
    </article>

    <form class="composer" @submit.prevent="send">
      <ModeSwitch v-model="mode" />
      <div class="input-row">
        <textarea
          v-model="question"
          rows="2"
          maxlength="1000"
          :placeholder="placeholder"
          aria-label="Frage"
          @keydown="onKeydown"
        />
        <button type="submit" class="primary" :disabled="!canSend">Fragen</button>
      </div>
      <p class="hint">
        Der Tutor berücksichtigt die letzten fünf Wortwechsel.
        <button
          v-if="exchanges.length"
          type="button"
          class="link"
          :disabled="busy"
          @click="newConversation"
        >
          Neues Gespräch
        </button>
      </p>
    </form>
  </section>
</template>
