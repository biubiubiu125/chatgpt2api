function cleanString(value: unknown) {
  return String(value || '').trim()
}

function baseOrigin() {
  return typeof window !== 'undefined' && window.location?.origin
    ? window.location.origin
    : 'http://localhost'
}

export function isTrustedApiDownloadUrl(
  url: string,
  options: { pageOrigin?: string; apiBaseUrl?: string } = {},
) {
  const value = cleanString(url)
  if (!value || /^(data|blob):/i.test(value)) return false

  const pageOrigin = cleanString(options.pageOrigin) || baseOrigin()
  try {
    const target = new URL(value, pageOrigin)
    if (!target.pathname.startsWith('/files/')) return false
    const trustedOrigins = new Set([new URL(pageOrigin).origin])
    const apiBaseUrl = cleanString(options.apiBaseUrl)
    if (apiBaseUrl) trustedOrigins.add(new URL(apiBaseUrl, pageOrigin).origin)
    return trustedOrigins.has(target.origin)
  } catch {
    return false
  }
}

export function shouldAuthorizeDownloadUrl(url: string) {
  const value = cleanString(url)
  if (!value || /^(data|blob):/i.test(value)) return false

  try {
    return new URL(value, baseOrigin()).pathname.startsWith('/files/')
  } catch {
    return value.startsWith('/files/') || value.startsWith('files/')
  }
}

export function filenameFromDownloadUrl(url: string) {
  const fallback = 'download'
  const value = cleanString(url)
  if (!value) return fallback

  try {
    const pathname = new URL(value, baseOrigin()).pathname
    return decodeURIComponent(pathname.split('/').filter(Boolean).pop() || fallback)
  } catch {
    const pathname = value.split(/[?#]/, 1)[0] || ''
    return decodeURIComponent(pathname.split('/').filter(Boolean).pop() || fallback)
  }
}
