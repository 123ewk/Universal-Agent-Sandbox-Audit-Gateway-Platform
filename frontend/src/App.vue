<script setup lang="ts">
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <router-link to="/" class="app-logo">ShadowOS</router-link>
      <nav class="app-nav">
        <router-link to="/history">历史</router-link>
      </nav>
      <span class="app-subtitle">Agent Runtime Observer</span>
    </header>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  background: #0d1117;
  border-bottom: 1px solid #21262d;
}

.app-logo {
  font-size: 16px;
  font-weight: 700;
  color: #58a6ff;
  text-decoration: none;
  letter-spacing: .5px;
}

.app-nav {
  display: flex;
  gap: 8px;
}

.app-nav a {
  font-size: 13px;
  color: var(--text-secondary);
  text-decoration: none;
  padding: 2px 8px;
  border-radius: 4px;
  transition: color .15s, background .15s;
}

.app-nav a:hover,
.app-nav a.router-link-active {
  color: var(--text-primary);
  background: rgba(88, 166, 255, .1);
}

.app-subtitle {
  font-size: 12px;
  color: #8b949e;
}

.app-main {
  flex: 1;
  display: flex;
  flex-direction: column;
}
</style>
