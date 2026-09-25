/* Pure-logic pins for the shared EOS_UI bundle.
 *
 * Every case here is a CONTRACT some rule file or call site already depends on,
 * chosen because breaking it is silent in a browser — the page renders, and the
 * damage shows up as a control that quietly does nothing or a badge with no
 * colour. Run with `node --test tests/js/` (or via tests/test_unit_js_suite.py).
 *
 * Pure functions only — see the header of shim.mjs for why.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const EOS_UI = loadStatic("emptyos/web/static/eos-components.js").EOS_UI;

test("the bundle loads far enough to expose its helpers", () => {
  // Guards the whole file: if the shim ever fails to satisfy a load-time
  // global, `EOS_UI` comes back tiny and every other test below would pass
  // vacuously against undefined helpers.
  assert.ok(Object.keys(EOS_UI).length > 100, "EOS_UI looks truncated");
  for (const name of ["esc", "escAttr", "jsArg", "statusVariant", "warningBanner", "store"]) {
    assert.equal(typeof EOS_UI[name], "function", `${name} missing`);
  }
});

test("jsArg quotes for an inline handler where bare JSON.stringify breaks it", () => {
  // .claude/rules/shared-frontend.md: a real double quote closes the attribute,
  // leaving a truncated handler that renders fine and does nothing on click.
  // Four dead handlers shipped this way before jsArg existed.
  const raw = 'it "quoted" & <b>';
  assert.ok(JSON.stringify(raw).includes('"'), "precondition: stringify emits real quotes");
  const arg = EOS_UI.jsArg(raw);
  assert.ok(!arg.includes('"'), `jsArg must not emit a raw double quote: ${arg}`);
  // Must still survive the HTML parser's entity decode back to the same string.
  const decoded = arg.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  assert.equal(JSON.parse(decoded), raw);
});

test("esc escapes markup but leaves quotes to escAttr", () => {
  // The two are NOT interchangeable: entityCard escapes attributes for you and
  // wants a bare JSON.stringify, while a hand-written attribute needs escAttr.
  // Copying a handler between the two sinks is the documented footgun.
  // Expected value measured in Chrome, not assumed — see shim.mjs.
  assert.equal(EOS_UI.esc("<a>&\"'"), "&lt;a&gt;&amp;\"'");
  assert.ok(EOS_UI.escAttr('x"y').includes("&quot;"), "escAttr must neutralise a double quote");
});

test("model rows name the current one in words and can refuse a dead one", () => {
  // Shared by modelPill and portal's chat picker. "current" must be a word,
  // not only the accent border (list-card-density: never colour alone).
  const cur = EOS_UI.modelRowHtml({ name: "openai-mini", model: "gpt-x", available: true }, { current: true });
  assert.ok(cur.includes("eos-model-row-active") && cur.includes(">current<") && cur.includes('aria-current="true"'));
  const off = EOS_UI.modelRowHtml({ name: "openai", available: false }, { disableUnavailable: true });
  assert.ok(off.includes(" disabled") && off.includes("unreachable"));
  // modelPill keeps an unreachable row clickable (it pins an override).
  assert.ok(!EOS_UI.modelRowHtml({ name: "openai", available: false }).includes(" disabled"));
  const hostile = EOS_UI.modelRowHtml({ name: 'x"><img>', model: "<b>", available: true });
  assert.ok(!hostile.includes('x"><img>') && !hostile.includes("<b>"));
});

test("the model pill carries its cost tint whoever renders it", () => {
  // The hand-rolled copy in portal dropped eos-model-pill-<klass>, so a paid
  // model looked free (model-pill.md: the cost class drives the colour).
  const paid = EOS_UI.modelPillHtml({ name: "openai-mini" }, { warn: "unreachable" });
  assert.ok(paid.includes("eos-model-pill-paid") && paid.includes("eos-model-pill-warn"));
  assert.ok(EOS_UI.modelPillHtml({ name: "" }, { empty: "no model" }).includes(">no model<"));
});

test("declared locality corrects the name-based cost guess", () => {
  // anthropic is a per-token key (it read as uncosted); a rented box named
  // "ollama-…" is cloud (it read 🔒 local); an unmapped local provider is local.
  assert.equal(EOS_UI._modelCostOf({ name: "anthropic_sdk" }), "paid");
  assert.equal(EOS_UI._modelCostOf({ name: "ollama-rented", is_cloud: true }), "unknown");
  assert.equal(EOS_UI._modelCostOf({ name: "ollama" }), "local");
  assert.equal(EOS_UI._modelCostOf({ name: "llamacpp", is_cloud: false }), "local");
  assert.equal(EOS_UI._modelCostOf({ name: "claude-cli", is_cloud: true }), "free");  // the name wins otherwise
  assert.ok(EOS_UI.modelRowHtml({ name: "ollama-rented", is_cloud: true, available: true }).includes("·"));
});

test("statusVariant answers neutral for anything it does not know", () => {
  // A bogus status must never come back as 'status-<bogus>' — that is a class
  // with no CSS behind it, i.e. an invisible, colourless chip.
  assert.equal(EOS_UI.statusVariant("nonsense-status"), "neutral");
  assert.equal(EOS_UI.statusVariant(""), "neutral");
  assert.equal(EOS_UI.statusVariant(null), "neutral");
  assert.equal(EOS_UI.statusVariant("active"), "status-active", "a known status keeps its prefix");
});

test("statusBadge owns the class prefix so callers cannot double-prefix it", () => {
  // Ten states shipped unstyled in conversation-ingest by hand-building
  // 'eos-badge-status-' + statusVariant(...) — which yields the doubled
  // 'status-status-' below, a class no stylesheet defines.
  assert.equal(EOS_UI.statusBadgeClass("active"), "eos-badge eos-badge-status-active");
  assert.equal(EOS_UI.statusBadgeClass("nope"), "eos-badge eos-badge-neutral");
  const html = EOS_UI.statusBadge("Active", "active");
  assert.ok(!html.includes("status-status-"), `double-prefixed class in: ${html}`);
  assert.ok(html.includes(">Active<"), "label must survive");
});

test("warningBanner escapes its message", () => {
  // This is why the studio ability banner cannot pass <b> markup through.
  // The positive assertion is the load-bearing half: checking only that the
  // raw markup is ABSENT passes just as well when the message vanished
  // entirely, which is exactly how this test first passed against a shim that
  // could not escape at all.
  const html = EOS_UI.warningBanner({ message: "<b>bold</b>", tone: "info" });
  assert.ok(html.includes("&lt;b&gt;bold&lt;/b&gt;"), `message not escaped into: ${html}`);
  assert.ok(!html.includes("<b>"), `raw markup survived: ${html}`);
  assert.ok(html.includes("eos-warning-banner--info"), "tone must reach the class");
});

test("errorState is an alert, escaped, and distinct from an empty state", () => {
  // A failure that replaces a panel is the one change a reader not watching
  // that region cannot otherwise learn of. Until 2026-09-12 the wrapper had no
  // role at all: visually distinct, audibly identical to "nothing here yet".
  const html = EOS_UI.errorState({ message: "<b>down</b>" });
  // Assert the attributes of the WRAPPER tag, in any order: the role has to
  // sit on the element that replaces the panel, not on the message inside it,
  // and a reordering of attributes is not a defect.
  const wrapper = (html.match(/^<div\b[^>]*>/) || [""])[0];
  assert.ok(wrapper.includes('class="eos-error-state"'), `wrapper lost its class: ${wrapper}`);
  assert.ok(wrapper.includes('role="alert"'), `no alert role on the wrapper: ${wrapper}`);
  assert.ok(html.includes("&lt;b&gt;down&lt;/b&gt;"), `message not escaped into: ${html}`);
  assert.ok(!html.includes("eos-empty-state"), "an error must not wear the empty-state vocabulary");
  assert.ok(EOS_UI.errorState().includes("Something went wrong."), "a bare call still says something");
});

test("abilityMeets treats an unknown ability as standard, never as a pass", () => {
  // .claude/rules/model-ability.md: unknown must not over-restrict, but it also
  // must not silently satisfy a strong bar.
  assert.equal(EOS_UI.abilityMeets("standard", "strong"), false);
  assert.equal(EOS_UI.abilityMeets("strong", "strong"), true);
  assert.equal(EOS_UI.abilityMeets("weak", "standard"), false);
  assert.equal(EOS_UI.abilityMeets("mystery-model", "strong"), false, "unknown must not pass a strong bar");
  assert.equal(EOS_UI.abilityMeets("weak", ""), true, "no bar means no gate");
});

test("store re-renders on set and coalesces a set issued from inside render", () => {
  // The documented contract behind the state->render convention. A set() from
  // inside render() must fold into ONE follow-up pass, not recurse.
  const seen = [];
  let reentered = false;
  const S = EOS_UI.store({ n: 0 }, (state) => {
    seen.push(state.n);
    if (!reentered && state.n === 1) {
      reentered = true;
      S.set({ n: 2 });
    }
  });
  S.set({ n: 1 });
  assert.deepEqual(seen, [1, 2], `expected one coalesced follow-up, got ${JSON.stringify(seen)}`);
  assert.equal(S.get("n"), 2);
});

test("store.get() with no key hands back the live object, which skips render", () => {
  // Load-bearing: the studio icon brief mirrors DOM->state through this exact
  // escape hatch to avoid rebuilding a textarea under the caret per keystroke.
  let renders = 0;
  const S = EOS_UI.store({ draft: "" }, () => { renders += 1; });
  S.get().draft = "typed";
  assert.equal(renders, 0, "mutating the live object must not render");
  assert.equal(S.get("draft"), "typed", "but the value must be there for the next render");
});

test("store fails loudly on a set/render loop instead of freezing the tab", () => {
  const S = EOS_UI.store({ n: 0 }, (state) => { S.set({ n: state.n + 1 }); });
  assert.throws(() => S.set({ n: 1 }), /set\/render loop/);
});

test("sandboxFrame never emits allow-same-origin, whatever it is handed", () => {
  // The whole safety story of an artifact panel rests on this one attribute:
  // with allow-same-origin, model-written script runs on the daemon's origin,
  // where it can read the session cookie and call every /api/ route as the
  // user. The sandbox string is a constant, not a caller's argument.
  const frames = [
    EOS_UI.sandboxFrame({ src: "/viz/api/html/a" }),
    EOS_UI.sandboxFrame({ src: "/x", sandbox: "allow-scripts allow-same-origin" }),
    EOS_UI.sandboxFrame({ sandbox: "allow-same-origin" }),
    EOS_UI.sandboxFrame({}),
  ];
  for (const html of frames) {
    assert.equal(html.includes("allow-same-origin"), false, html);
    assert.match(html, /sandbox="allow-scripts"/);
  }
});

test("sandboxFrame escapes the src, so a hostile path cannot add attributes", () => {
  const html = EOS_UI.sandboxFrame({ src: '/x" onload="steal()', title: '<img src=x>' });
  assert.equal(html.includes('onload="steal()"'), false);
  assert.match(html, /src="\/x&quot; onload=&quot;steal\(\)"/);
  assert.equal(html.includes("<img"), false);
});

test("sandboxFrame refuses a non-numeric height rather than writing it into style", () => {
  const html = EOS_UI.sandboxFrame({ src: "/x", height: "100px;position:fixed;top:0" });
  assert.equal(html.includes("position:fixed"), false);
  assert.match(html, /height:100px;/);
});

test("the embed builders go through sandboxFrame, not their own iframe markup", () => {
  // The helper is the safe part; the CALL SITES are what a refactor breaks,
  // and neither of these had any test before sandboxFrame existed. Hand-roll
  // an iframe in either and these go red — which is the mutation that matters.
  const ref = EOS_UI._vizEmbedHtml("e1", { mode: "reference", source_viz_id: "v9", shape: "chart" }, {});
  assert.match(ref, /sandbox="allow-scripts"/);
  assert.equal(ref.includes("allow-same-origin"), false);
  assert.match(ref, /src="\/viz\/api\/html\/v9"/);

  const snap = EOS_UI._vizEmbedHtml("e1", { mode: "snapshot", shape: "chart" }, {});
  assert.match(snap, /src="\/viz\/api\/embed\/e1"/);
  assert.match(snap, /sandbox="allow-scripts"/);

  // A heavy embed renders a poster instead; loading it must land on the same
  // frame builder rather than a second hand-rolled one.
  const poster = EOS_UI._vizEmbedHtml("e1", { mode: "snapshot", heavy: true, shape: "chart" }, {});
  assert.equal(poster.includes("<iframe"), false, "a heavy embed is a poster until clicked");
  assert.match(poster, /eos-embed-poster/);
});

test("refLinks turns a KB wikilink into a link instead of printing brackets", () => {
  // The defect this replaced: methodPicker and conformancePanel both ran
  // `references.map(esc)`, so every engineering calculator showed the reader
  // a literal `[[iec-60865-rigid-busbar-forces]]`. Thirteen manifests agreed
  // on the wikilink convention, so the component was the thing that was wrong.
  const html = EOS_UI.refLinks(["[[iec-60865-rigid-busbar-forces]]"]);
  assert.ok(!html.includes("[["), `brackets still rendered: ${html}`);
  assert.ok(html.includes('href="/kb/#iec-60865-rigid-busbar-forces"'), html);
  assert.ok(html.includes(">iec-60865-rigid-busbar-forces</a>"), html);
});

test("refLinks honours the alias and #section forms of the wikilink grammar", () => {
  // Same grammar as the markdown renderer's wikilink rule, deliberately — a
  // second grammar here would drift from it.
  const alias = EOS_UI.refLinks(["[[ieee-605-2023|IEEE 605]]"]);
  assert.ok(alias.includes('href="/kb/#ieee-605-2023"'), alias);
  assert.ok(alias.includes(">IEEE 605</a>"), alias);

  // A #section targets the same note; the label drops the section, as the
  // markdown rule does.
  const sec = EOS_UI.refLinks(["[[ieee-605-2023#D.5]]"]);
  assert.ok(sec.includes('href="/kb/#ieee-605-2023"'), sec);
  assert.ok(sec.includes(">ieee-605-2023</a>"), sec);
});

test("refLinks passes a non-wikilink through as escaped text, not a link", () => {
  // Every reference in the repo today is a wikilink, but a prose citation is a
  // legitimate future value and a strip that assumed brackets would mangle it.
  const html = EOS_UI.refLinks(["IEEE Std 738-2012"]);
  assert.equal(html, "IEEE Std 738-2012");
  assert.ok(!html.includes("<a"), html);
});

test("refLinks escapes a hostile reference rather than emitting its markup", () => {
  const html = EOS_UI.refLinks(['<img src=x onerror=alert(1)>']);
  assert.ok(!html.includes("<img"), html);
  // And inside a wikilink, where the label reaches the link text.
  const inner = EOS_UI.refLinks(['[[slug|<b>bold</b>]]']);
  assert.ok(!inner.includes("<b>"), inner);
});

test("refLinks percent-encodes the href so a slug cannot close the attribute", () => {
  // The sink this pins is `encodeURIComponent(slug)`. Replacing it with bare
  // `slug` left every other refLinks test GREEN — the href assertions elsewhere
  // use a slug with no special characters, which plain concatenation satisfies
  // just as well. So this is the only case that can tell the correct
  // implementation from the broken one.
  //
  // Unencoded, `sl"ug onmouseover=alert(1) x=` closes the href and leaves a
  // live handler on a link that renders perfectly and logs nothing.
  const html = EOS_UI.refLinks(['[[sl"ug onmouseover=alert(1) x=]]']);

  // Count quotes; do not parse. The whole security property is "no attribute
  // VALUE contains a raw double quote" — nothing can break out of an attribute
  // it cannot close. A two-attribute tag therefore has exactly four `"`, and
  // `&quot;` is an entity rather than a quote, so the correct output lands on
  // four while dropping encodeURIComponent (raw `"` into href), swapping
  // escAttr for esc (raw `"` into title), or removing the title all move it.
  //
  // Three earlier drafts of this assertion scanned the markup for `onmouseover`
  // or for attribute names instead. Every one of them failed on CORRECT output,
  // because the payload legitimately survives twice — percent-encoded in the
  // href and entity-escaped in the title — and ` x="` inside the title's value
  // even looks like an attribute to a regex. Structure is not greppable.
  const tag = html.slice(0, html.indexOf(">") + 1);
  assert.equal((tag.match(/"/g) || []).length, 4,
    `an attribute value carries a raw quote: ${tag}`);

  const href = html.match(/href="([^"]*)"/)[1];
  assert.ok(href.includes("%22"), `quote not percent-encoded: ${href}`);
  assert.ok(html.includes("&quot;"), `title not entity-escaped: ${tag}`);
});

test("refLinks escapes the title attribute, not merely the link text", () => {
  // Pins `escAttr(slug)` on the title. Swapping it for `esc()` also left the
  // suite green, because `esc` deliberately leaves quotes alone — that is the
  // documented division of labour between the two helpers, and it is precisely
  // why the wrong one here is invisible.
  const html = EOS_UI.refLinks(['[[a"b]]']);
  assert.ok(html.includes('title="a&quot;b"') || html.includes("a&quot;b"),
    `title not attribute-escaped: ${html}`);
  assert.ok(!html.includes('title="a"b"'), `raw quote in title: ${html}`);
});

test("refLinks always carries a title, so the full slug survives an alias", () => {
  // Dropping the title attribute entirely left the suite green. The title is
  // the only place the real slug appears when a display alias replaces it.
  const html = EOS_UI.refLinks(['[[ieee-605-2023|IEEE 605]]']);
  assert.ok(html.includes('title="ieee-605-2023"'), `no title: ${html}`);
});

test("refLinks joins with the separator both call sites already used", () => {
  const html = EOS_UI.refLinks(["[[a]]", "[[b]]"]);
  assert.ok(html.includes(" · "), html);
  assert.equal(html.split("</a>").length - 1, 2, html);
});

test("refLinks refuses to emit an anchor with nothing in it", () => {
  // `[[   ]]` matched the grammar and produced `<a href="/kb/#" title=""></a>`
  // — invisible to a mouse, still reachable by Tab, and pointing nowhere.
  // `[[slug| ]]` did the same via an empty alias.
  for (const degenerate of ["[[   ]]", "[[slug| ]]"]) {
    const html = EOS_UI.refLinks([degenerate]);
    assert.ok(!html.includes("<a"), `emitted an empty anchor: ${html}`);
  }
  // The already-passthrough shapes stay passthrough.
  for (const s of ["[[]]", "[[#sec]]", "[[slug|]]"]) {
    assert.ok(!EOS_UI.refLinks([s]).includes("<a"), s);
  }
});

test("refLinks answers empty for nothing to show, so callers need no guard", () => {
  for (const empty of [null, undefined, []]) {
    assert.equal(EOS_UI.refLinks(empty), "");
  }
});

test("confirm keeps the newlines of a message that shows what is being confirmed", () => {
  // The commonest reason to put a newline in a confirm message is that the
  // message CONTAINS the thing to inspect — a command line, a path list.
  // Collapsing them runs the artefact together with the warning prose, which
  // defeats the one card whose job is to be read literally.
  let html = "";
  const orig = EOS_UI.modal;
  EOS_UI.modal = (o) => { html = o.body; return null; };
  try {
    // The body is built before confirm wires its buttons; the shim has no real
    // getElementById, so that wiring throws once we already have what we need.
    try {
      EOS_UI.confirm({ title: "Run this?", message: "Careful.\n\nnpx -y srv\n\nin /srv" });
    } catch (e) { /* DOM wiring — not what this asserts */ }
  } finally {
    EOS_UI.modal = orig;
  }
  assert.ok(html, "confirm did not build a body");
  assert.match(html, /white-space:pre-wrap/);
  assert.match(html, /Careful\.\n\nnpx -y srv/);
});
