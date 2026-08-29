import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { nanocatZhCN, setNanocatLocale } from 'nanocat-ui'
import router from './router'
import { getAuthToken, setUnauthorizedHandler } from './api/client'
import { useAuthStore } from './stores/auth'
import { applyThemeMode, getStoredThemeMode } from './lib/theme'
import App from './App.vue'
import './style.css'
import './styles/features.css'

setNanocatLocale(nanocatZhCN)
applyThemeMode(getStoredThemeMode())

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)

setUnauthorizedHandler(async (reason) => {
  const authStore = useAuthStore()
  if (reason === 'forbidden' && getAuthToken()) {
    const stillLoggedIn = await authStore.refreshAuth()
    if (stillLoggedIn) {
      const requiresAdmin = router.currentRoute.value.matched.some((record) => record.meta.adminOnly)
      if (requiresAdmin && !authStore.isAdmin) {
        void router.replace({ name: 'studio' }).catch(() => {})
      }
      return
    }
  }
  authStore.clearIdentity()
  void router.replace({ name: 'login' }).catch(() => {})
})

app.use(router)

app.mount('#app')
