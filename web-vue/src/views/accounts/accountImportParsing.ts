export type ParsedAccountImport = Record<string, unknown> & {
  access_token: string
}

function cleanString(value: unknown): string {
  return String(value || '').trim()
}

export function uniqueTokens(tokens: string[]) {
  return Array.from(new Set(tokens.map((token) => token.trim()).filter(Boolean)))
}

export function parseTokenLines(text: string) {
  return uniqueTokens(
    text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#')),
  )
}

function accessTokenFromAccount(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return ''
  const source = value as Record<string, unknown>
  return cleanString(source.access_token || source.accessToken || source.cookie)
}

function accountFromSource(value: unknown): ParsedAccountImport | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const source = value as Record<string, unknown>
  const accessToken = accessTokenFromAccount(source)
  if (!accessToken) return null
  const account: ParsedAccountImport = {
    ...source,
    access_token: accessToken,
  }
  delete account.accessToken
  return account
}

export function normalizeAccountImports(values: unknown[]): ParsedAccountImport[] {
  const deduped = new Map<string, ParsedAccountImport>()
  for (const value of values) {
    const account = accountFromSource(value)
    if (!account) continue
    deduped.set(account.access_token, account)
  }
  return Array.from(deduped.values())
}

export function accountTokens(accounts: unknown[]): string[] {
  return uniqueTokens(accounts.map(accessTokenFromAccount))
}

export function parseSessionJsonAccounts(rawText: string): ParsedAccountImport[] {
  const text = rawText.trim()
  if (!text) throw new Error('请先粘贴 Session JSON')
  const parsed = JSON.parse(text)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Session JSON 格式不正确')
  }
  const account = accountFromSource(parsed)
  if (!account) throw new Error('Session JSON 中没有找到 accessToken')
  return [account]
}

export function parseCPAJsonAccounts(rawText: string, label: string): ParsedAccountImport[] {
  const text = rawText.trim()
  if (!text) throw new Error(`${label} 是空文件`)
  const parsed = JSON.parse(text)
  const candidates: unknown[] = []

  if (Array.isArray(parsed)) {
    candidates.push(...parsed)
  } else if (parsed && typeof parsed === 'object') {
    if (accountFromSource(parsed)) {
      candidates.push(parsed)
    } else {
      const source = parsed as Record<string, unknown>
      for (const key of ['accounts', 'items', 'data', 'results']) {
        const rows = source[key]
        if (Array.isArray(rows)) candidates.push(...rows)
      }
    }
  }

  const accounts = normalizeAccountImports(candidates)
  if (!accounts.length) throw new Error(`${label} 中没有找到 access_token`)
  return accounts
}
