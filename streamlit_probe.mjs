// A backend health response is diagnostic only. Readiness requires the app's
// rendered heading, including its own password screen, without an exception.
export function readiness({ url, titleVisible, exceptionVisible, platformLoginVisible }) {
  if (exceptionVisible) return "app-error";
  const parsed = new URL(url);
  if (/\/-\/(login|auth)(\/|$)/i.test(parsed.pathname) || platformLoginVisible) {
    return "platform-login";
  }
  return titleVisible ? "ready" : "not-ready";
}

const loginButton = /continue with google|sign in with google/i;
const wakeButton = /get this app back up|yes, get this app back up|wake|reboot/i;
const visible = async (locator) => locator.first().isVisible().catch(() => false);
const preview = (text) => text.replace(/\s+/g, " ").slice(0, 160);

// Community Cloud wraps the app in a visible, same-origin streamlitApp iframe.
// Its separate status/support frames must never determine app readiness.
export async function appScopes(page) {
  const main = page.mainFrame();
  const scopes = [main];
  const origin = new URL(page.url()).origin;
  for (const frame of page.frames()) {
    if (frame === main) continue;
    try {
      if (new URL(frame.url()).origin !== origin) continue;
      const element = await frame.frameElement();
      try {
        if (await element.getAttribute("title") === "streamlitApp" && await element.isVisible()) {
          scopes.push(frame);
        }
      } finally {
        await element.dispose();
      }
    } catch {
      // A frame can detach or still be about:blank during boot; rescan next poll.
    }
  }
  return scopes;
}

export async function probeApp(page, title) {
  const probes = [];
  let wake = null;
  for (const scope of await appScopes(page)) {
    try {
      const [titleVisible, exceptionVisible, platformLoginVisible, body] = await Promise.all([
        visible(scope.getByRole("heading", { name: title, exact: true })),
        visible(scope.locator('[data-testid="stException"]')),
        visible(scope.getByRole("button", { name: loginButton })),
        scope.locator("body").innerText({ timeout: 1500 }).catch(() => ""),
      ]);
      probes.push({
        url: scope.url(),
        state: readiness({ url: scope.url(), titleVisible, exceptionVisible, platformLoginVisible }),
        bodyPreview: preview(body),
      });
      const button = scope.getByRole("button", { name: wakeButton }).first();
      if (!wake && await button.isVisible().catch(() => false)) wake = button;
    } catch {
      // A disappearing frame is not evidence of readiness.
    }
  }
  const state = probes.some((probe) => probe.state === "platform-login") ? "platform-login"
    : probes.some((probe) => probe.state === "app-error") ? "app-error"
      : probes.some((probe) => probe.state === "ready") ? "ready" : "not-ready";
  return { state, probes, wake };
}

export async function waitForApp(page, title, { timeoutMs = 210000, pollIntervalMs = 3000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let previousReady = false;
  let lastProbe;
  while (Date.now() < deadline) {
    lastProbe = await probeApp(page, title);
    console.log(JSON.stringify({ state: lastProbe.state, frames: lastProbe.probes }));
    if (lastProbe.state === "platform-login") throw new Error("Streamlit platform login blocks this browser.");
    if (lastProbe.state === "app-error") throw new Error("The rendered Streamlit app raised an exception.");
    if (lastProbe.state === "ready" && previousReady) return;
    previousReady = lastProbe.state === "ready";
    if (lastProbe.wake) {
      await lastProbe.wake.click({ timeout: 5000 }).catch(() => {});
    }
    await page.waitForTimeout(pollIntervalMs);
  }
  throw new Error(`No rendered app confirmed before timeout. Frames=${JSON.stringify(lastProbe?.probes ?? [])}`);
}
