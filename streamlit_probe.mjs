// Do not confuse an HTTP 200 shell or an app's own login controls with a
// running dashboard or a Streamlit platform login redirect.
export function readiness({ url, titleVisible, exceptionVisible, platformLoginVisible }) {
  if (exceptionVisible) return "app-error";
  if (titleVisible) return "ready";
  const parsed = new URL(url);
  if (/\/-\/(login|auth)(\/|$)/i.test(parsed.pathname) || platformLoginVisible) {
    return "platform-login";
  }
  return "not-ready";
}
