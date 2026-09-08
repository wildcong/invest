import assert from "node:assert/strict";
import test from "node:test";
import { appScopes, probeApp, readiness, waitForApp } from "../streamlit_probe.mjs";

const normal = {
  url: "https://pension.example.com/",
  titleVisible: false,
  exceptionVisible: false,
  platformLoginVisible: false,
};

test("HTTP shell and healthy backend cannot substitute for rendered app", () => {
  assert.equal(readiness({ ...normal, healthOk: true }), "not-ready");
});
test("rendered dashboard or own password heading is ready", () => {
  assert.equal(readiness({ ...normal, titleVisible: true }), "ready");
});
test("exception defeats even a rendered heading", () => {
  assert.equal(readiness({ ...normal, titleVisible: true, exceptionVisible: true }), "app-error");
});
test("platform authentication cannot count as own app login", () => {
  for (const url of ["https://share.streamlit.io/-/auth", "https://pension.streamlit.app/-/login?x=1"]) {
    assert.equal(readiness({ ...normal, url, titleVisible: true }), "platform-login");
  }
  assert.equal(readiness({ ...normal, titleVisible: true, platformLoginVisible: true }), "platform-login");
});

function scope({ url = "https://example.streamlit.app/~/+/", title = false,
  exception = false, login = false, wake = false, frameTitle = "streamlitApp",
  frameVisible = true, detached = false } = {}) {
  const state = { title, exception, login, wake, clicks: 0 };
  const locator = (key) => ({
    first() { return this; },
    async isVisible() { return Boolean(state[key]); },
    async innerText() { return state.title ? "Actual app heading" : ""; },
    async click() { state.clicks++; state.wake = false; state.title = true; },
  });
  return {
    state, url: () => url,
    getByRole: (role, options) => locator(role === "heading" ? "title" :
      String(options.name).includes("google") ? "login" : "wake"),
    locator: (selector) => locator(selector === "body" ? "body" : "exception"),
    async frameElement() {
      if (detached) throw new Error("detached");
      return { getAttribute: async () => frameTitle, isVisible: async () => frameVisible, dispose: async () => {} };
    },
  };
}

function page(main, children = []) {
  return { mainFrame: () => main, frames: () => [main, ...children], url: main.url,
    waitForTimeout: async () => {} };
}

test("same-origin visible Cloud iframe is probed even when outer body is empty", async () => {
  const app = scope({ title: true });
  const browserPage = page(scope(), [app]);
  const result = await probeApp(browserPage, "app title");
  assert.equal(result.state, "ready");
  assert.equal(result.probes.length, 2);
  assert.equal(result.probes[0].bodyPreview, "");
  assert.equal(result.probes[1].bodyPreview, "Actual app heading");
});

test("status, hidden, cross-origin and detached frames never establish readiness", async () => {
  const browserPage = page(scope(), [
    scope({ title: true, frameTitle: "Streamlit Cloud Status" }),
    scope({ title: true, frameVisible: false }),
    scope({ title: true, url: "https://unrelated.example/" }),
    scope({ title: true, detached: true }),
  ]);
  assert.equal((await appScopes(browserPage)).length, 1);
  assert.equal((await probeApp(browserPage, "app title")).state, "not-ready");
});

test("app iframe exceptions defeat its visible heading", async () => {
  assert.equal((await probeApp(page(scope(), [scope({ title: true, exception: true })]), "app")).state, "app-error");
});

test("outer platform authentication cannot be bypassed by an inner app heading", async () => {
  assert.equal((await probeApp(page(scope({ login: true }), [scope({ title: true })]), "app")).state, "platform-login");
});

test("wake button inside app frame is used and actual rendering must follow", async () => {
  const app = scope({ wake: true });
  await waitForApp(page(scope(), [app]), "app", { timeoutMs: 1000, pollIntervalMs: 0 });
  assert.equal(app.state.clicks, 1);
  assert.equal(app.state.title, true);
});

test("frame list is rediscovered after attachment", async () => {
  const children = [];
  const browserPage = page(scope(), children);
  assert.equal((await probeApp(browserPage, "app")).state, "not-ready");
  children.push(scope({ title: true }));
  assert.equal((await probeApp(browserPage, "app")).state, "ready");
});
