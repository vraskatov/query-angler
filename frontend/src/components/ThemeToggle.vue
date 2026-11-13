<script setup>
// Hell oder dunkel. Standard ist die Einstellung des Betriebssystems; wer klickt,
// legt sich fest, und die Wahl wird im Browser gespeichert. Das Setzen vor dem ersten
// Zeichnen übernimmt ein kleines Skript in index.html, damit die Seite nicht aufblitzt.
import { onBeforeUnmount, onMounted, ref } from "vue";

const KEY = "queryangler-theme";
const system = window.matchMedia("(prefers-color-scheme: dark)");
const dark = ref(false);

function effective() {
  const chosen = document.documentElement.dataset.theme;
  return chosen ? chosen === "dark" : system.matches;
}

function toggle() {
  const next = dark.value ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // privates Fenster o. Ä.: Wahl gilt dann nur bis zum Neuladen
  }
  dark.value = !dark.value;
}

// Ändert sich die Systemeinstellung und hat niemand gewählt, folgt der Knopf ihr.
function onSystemChange() {
  dark.value = effective();
}

onMounted(() => {
  dark.value = effective();
  system.addEventListener("change", onSystemChange);
});
onBeforeUnmount(() => system.removeEventListener("change", onSystemChange));
</script>

<template>
  <button
    type="button"
    class="theme-toggle"
    :aria-label="dark ? 'Helles Farbschema' : 'Dunkles Farbschema'"
    :title="dark ? 'Helles Farbschema' : 'Dunkles Farbschema'"
    @click="toggle"
  >
    {{ dark ? "☀" : "☾" }}
  </button>
</template>
