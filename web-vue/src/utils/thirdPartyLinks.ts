const SENSITIVE_QUERY_KEYS = new Set([
  'apikey',
  'api_key',
  'authorization',
  'access_token',
  'token',
])

export function buildThirdPartyHref(appUrl: string, baseUrl: string) {
  const url = appUrl.trim()
  if (!url) return ''

  try {
    const target = new URL(url)
    if (!['http:', 'https:'].includes(target.protocol)) return ''
    target.username = ''
    target.password = ''

    for (const key of Array.from(target.searchParams.keys())) {
      if (SENSITIVE_QUERY_KEYS.has(key.toLowerCase())) {
        target.searchParams.delete(key)
      }
    }
    target.searchParams.set('baseUrl', baseUrl.trim())
    return target.toString()
  } catch {
    return ''
  }
}
