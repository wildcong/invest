import test from "node:test";
import assert from "node:assert/strict";
import { readiness } from "../streamlit_probe.mjs";

const shell = { url: "https://example.streamlit.app", titleVisible: false,
  exceptionVisible: false, platformLoginVisible: false };
test("shell and healthy backend alone do not establish a rendered app", () => {
  assert.equal(readiness(shell), "not-ready");
});
test("actual dashboard is ready even if it contains its own login control", () => {
  assert.equal(readiness({ ...shell, titleVisible: true, platformLoginVisible: true }), "ready");
});
test("platform login route is blocked", () => {
  assert.equal(readiness({ ...shell, url: "https://share.streamlit.io/-/login" }), "platform-login");
});
test("an app exception must not count as ready", () => {
  assert.equal(readiness({ ...shell, titleVisible: true, exceptionVisible: true }), "app-error");
});
