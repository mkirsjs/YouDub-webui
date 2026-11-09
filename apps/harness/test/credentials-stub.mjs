/** Test stand-in for the credential-key helper. BrowserAuth only needs this at runtime. */
export function credentialKey(scope, name) {
  return `${scope}/${name}`
}
