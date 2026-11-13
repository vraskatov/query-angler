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

const canSend = computed(() => question.value.trim().length >= 3 && !busy.value);

const examples = [
  "Was unterscheidet WHERE von HAVING?",
  "Wie zähle ich, wie viele Abteilungen es pro Ort gibt?",
  "Wie verknüpfe ich eine Tabelle mit sich selbst?",
];

async function send() {
  if (!canSend.value) return;
  exchanges.value.push({ question: question.value.trim(), mode: mode.value, answer: null, error: null });
  const entry = exchanges.value.at(-1); // die reaktive Fassung, damit Änderungen sichtbar werden
  question.value = "";
  busy.value = true;
  seconds.value = 0;
  timer = setInterval(() => seconds.value++, 1000);
  try {
    entry.answer = await ask(entry.question, entry.mode);
  } catch (error) {
    entry.error = error.message;
  } finally {
    clearInterval(timer);
    busy.value = false;
    await nextTick();
    document.querySelector(".exchange:last-of-type")?.scrollIntoView({ behavior: "smooth" });
  }
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
  return DOMPurify.sanitize(marked.parse(text));
}

function link(source) {
  return source.permalink || source.url || null;
}

function notes(answer) {
  const list = [];
  if (answer.checks.some((c) => c.startsWith("Absage"))) {
    list.push("Das Buch und das hochgeladene Material enthalten dazu nichts.");
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
        du selbst auf die Lösung kommst.
      </p>
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
        {{ entry.question }}
      </p>

      <p v-if="!entry.answer && !entry.error" class="waiting">
        Der Tutor denkt nach … {{ seconds }} s
      </p>
      <p v-if="entry.error" class="error">{{ entry.error }}</p>

      <template v-if="entry.answer">
        <div class="answer" v-html="render(entry.answer.text)" />
        <p v-for="note in notes(entry.answer)" :key="note" class="note">{{ note }}</p>

        <details v-if="entry.answer.sources.length" class="sources" :open="entry.mode === 'sokratisch'">
          <summary>
            {{ entry.mode === "sokratisch" ? "Zum Nachlesen" : "Quellen" }}
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
          placeholder="Deine Frage zu SQL …"
          aria-label="Frage"
          @keydown="onKeydown"
        />
        <button type="submit" class="primary" :disabled="!canSend">Fragen</button>
      </div>
      <p class="hint">Jede Frage wird einzeln beantwortet, der Tutor kennt den Verlauf nicht.</p>
    </form>
  </section>
</template>
