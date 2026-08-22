export function safeExternalHref(value: unknown) {
  const raw = String(value ?? '').trim()
  if (!raw) return ''
  try {
    const parsed = new URL(raw)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return ''
    parsed.username = ''
    parsed.password = ''
    return parsed.toString()
  } catch {
    return ''
  }
}

export function safeExternalHost(value: unknown) {
  const href = safeExternalHref(value)
  if (!href) return ''
  try {
    return new URL(href).host.replace(/^www\./, '')
  } catch {
    return ''
  }
}
