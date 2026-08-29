import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi } from '@/api/auth'
import { getAuthToken } from '@/api/client'
import type { AuthStatusResponse } from '@/types/api'

type AuthRole = 'admin' | 'user' | ''

function normalizeRole(value: unknown): AuthRole {
  const role = String(value || '').trim().toLowerCase()
  return role === 'admin' || role === 'user' ? role : ''
}

function isCredentialFailure(error: unknown): boolean {
  const status = Number((error as { status?: unknown } | null)?.status)
  return status === 401 || status === 403
}

export const useAuthStore = defineStore('auth', () => {
  const isLoggedIn = ref(false)
  const isLoading = ref(false)
  const authUnavailable = ref(false)
  const role = ref<AuthRole>('')
  const subjectId = ref('')
  const name = ref('')
  const lastCheckedAt = ref(0)
  const AUTH_CACHE_MS = 60000
  let checkPromise: Promise<boolean> | null = null

  const isAdmin = computed(() => role.value === 'admin')
  const isUser = computed(() => role.value === 'user')

  function applyStatus(status: AuthStatusResponse | undefined | null) {
    authUnavailable.value = false
    isLoggedIn.value = Boolean(status?.authenticated)
    role.value = isLoggedIn.value ? normalizeRole(status?.role) : ''
    subjectId.value = isLoggedIn.value ? String(status?.subject_id || '') : ''
    name.value = isLoggedIn.value ? String(status?.name || '') : ''
  }

  function clearIdentity() {
    authUnavailable.value = false
    isLoggedIn.value = false
    role.value = ''
    subjectId.value = ''
    name.value = ''
  }

  // 登录
  async function login(password: string) {
    isLoading.value = true
    try {
      await authApi.login({ password })
      const status = await authApi.checkAuth()
      applyStatus(status)
      lastCheckedAt.value = Date.now()
      return isLoggedIn.value
    } catch (error) {
      clearIdentity()
      throw error
    } finally {
      isLoading.value = false
    }
  }

  // 登出
  async function logout() {
    try {
      await authApi.logout()
    } finally {
      clearIdentity()
      lastCheckedAt.value = 0
    }
  }

  async function refreshAuth() {
    if (!getAuthToken()) {
      clearIdentity()
      lastCheckedAt.value = 0
      checkPromise = null
      return false
    }
    if (checkPromise) {
      return checkPromise
    }
    try {
      checkPromise = (async () => {
        const status = await authApi.checkAuth()
        applyStatus(status)
        lastCheckedAt.value = Date.now()
        return isLoggedIn.value
      })()
      return await checkPromise
    } catch (error) {
      if (isCredentialFailure(error)) {
        clearIdentity()
        lastCheckedAt.value = 0
        return false
      }
      authUnavailable.value = true
      return Boolean(getAuthToken()) || isLoggedIn.value
    } finally {
      checkPromise = null
    }
  }

  // 检查登录状态
  async function checkAuth() {
    if (!getAuthToken()) {
      clearIdentity()
      lastCheckedAt.value = 0
      checkPromise = null
      return false
    }
    const now = Date.now()
    if (now - lastCheckedAt.value < AUTH_CACHE_MS) {
      return isLoggedIn.value
    }
    return refreshAuth()
  }

  return {
    isLoggedIn,
    isLoading,
    authUnavailable,
    role,
    subjectId,
    name,
    isAdmin,
    isUser,
    login,
    logout,
    checkAuth,
    refreshAuth,
    clearIdentity,
  }
})
