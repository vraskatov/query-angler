<script setup>
// Zwei Ansichten, umgeschaltet über Reiter. Kein Router: Für zwei Seiten wäre das
// mehr Aufwand als Nutzen. KeepAlive behält den Zustand, wenn man hin- und herwechselt.
import { ref } from "vue";
import AskView from "./components/AskView.vue";
import KnowledgeView from "./components/KnowledgeView.vue";
import ThemeToggle from "./components/ThemeToggle.vue";

const views = [
  { id: "ask", label: "Fragen", component: AskView },
  { id: "knowledge", label: "Wissensbasis", component: KnowledgeView },
];
const active = ref("ask");
</script>

<template>
  <header class="header">
    <div class="brand">
      <img src="/logo.png" alt="" class="logo" />
      <div>
        <h1>Query Angler</h1>
        <p class="tagline">Zieht Wissen an Land</p>
      </div>
    </div>
    <nav class="tabs" aria-label="Ansicht">
      <button
        v-for="view in views"
        :key="view.id"
        :class="{ active: active === view.id }"
        :aria-current="active === view.id ? 'page' : undefined"
        @click="active = view.id"
      >
        {{ view.label }}
      </button>
      <ThemeToggle />
    </nav>
  </header>

  <main class="main">
    <KeepAlive>
      <component :is="views.find((v) => v.id === active).component" />
    </KeepAlive>
  </main>

  <footer class="footer">
    Antworten auf Grundlage des Wikibooks
    <a href="https://de.wikibooks.org/wiki/Einf%C3%BChrung_in_SQL" target="_blank" rel="noopener"
      >„Einführung in SQL“</a
    >
    (CC BY-SA 4.0) und eigenem Material der Lehrenden. Lokal erzeugt, kann Fehler enthalten.
  </footer>
</template>
