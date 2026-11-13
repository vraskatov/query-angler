<script setup>
// Schalter mit zwei benannten Stellungen. Ein An-aus-Schalter wäre missverständlich,
// weil man nicht sieht, was "aus" bedeutet. Technisch eine Gruppe aus zwei Optionen.
const mode = defineModel({ type: String, required: true });

const options = [
  { value: "erklaeren", label: "Erklären", hint: "vollständige Antwort mit Beispiel" },
  { value: "sokratisch", label: "Sokratisch", hint: "nur ein Hinweis und eine Rückfrage" },
];
</script>

<template>
  <div class="mode-switch" role="radiogroup" aria-label="Modus">
    <span class="knob" :class="{ right: mode === 'sokratisch' }" aria-hidden="true" />
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      role="radio"
      :aria-checked="mode === option.value"
      :class="{ selected: mode === option.value }"
      :title="option.hint"
      @click="mode = option.value"
    >
      {{ option.label }}
    </button>
  </div>
</template>
