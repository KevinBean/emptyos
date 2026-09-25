/* markitup — review list, pinned canvas, comment gutter.
 *
 * Loaded at end of body (same position the inline block would have occupied),
 * so load order against eos.js / eos-components.js is unchanged.
 *
 * Two conventions worth knowing before editing:
 *
 *  1. **No inline on* handlers anywhere.** Every interaction is delegated from
 *     a container and keyed off data- attributes. That sidesteps the
 *     inline-handler escaping trap (.claude/rules/shared-frontend.md) entirely
 *     rather than escaping around it — there is no attribute for a quote to
 *     break out of.
 *  2. **Pins are positioned in percent**, never pixels. The same markup then
 *     prints: exporting.py renders identical percent offsets into the PDF.
 *     A pixel offset measured against the on-screen image would be wrong at
 *     every other size.
 */
(function () {
  "use strict";

  var S = {
    reviews: null,       // null = not loaded yet (distinct from "none")
    rubrics: {},         // id -> {tags:[], label}
    review: null,        // open review document
    run: null,           // its pipeline state, when paused
    shot: null,          // selected shot id
    tags: {},            // tag id -> shown?
    hideResolved: false,
    selected: null,      // highlighted comment n
    hasProposal: false   // a rebuilt page exists for this review
  };

  var $ = function (id) { return document.getElementById(id); };

  // Every value interpolated into innerHTML below goes through esc(). That is
  // not boilerplate caution here: a comment's title and body are written by a
  // model from the text of a page we do not control, and a shot's url/title
  // come from that page too. This is the one surface where untrusted content
  // and markup meet, so nothing reaches innerHTML unescaped.
  var esc = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };

  // ── rubric helpers ────────────────────────────────────────────────

  function rubric() { return S.rubrics[(S.review && S.review.rubric) || "site"] || { tags: [] }; }

  function tagMeta(id) {
    var tags = rubric().tags || [];
    for (var i = 0; i < tags.length; i++) if (tags[i].id === id) return tags[i];
    return null;
  }

  function tagBadge(id) {
    var m = tagMeta(id);
    if (!m) return EOS_UI.statusBadge(id || "?", "");
    // statusBadge owns the class concatenation — never build it by hand.
    return EOS_UI.statusBadge(m.label, m.variant);
  }

  // ── list view ─────────────────────────────────────────────────────

  function renderList() {
    var el = $("mk-list");
    if (S.reviews === null) { el.innerHTML = '<p class="mk-muted">Loading…</p>'; return; }
    if (!S.reviews.length) {
      // emptyState returns an HTML *string* keyed on `message`, and its
      // onAction is inline-JS text — so no action is passed here; the header's
      // "+ New review" button is the affordance, and this page keeps its
      // no-inline-handler rule intact.
      el.innerHTML = EOS_UI.emptyState({
        icon: "🖍",
        message: "No reviews yet. Point it at a page and it will capture the views worth reviewing, then draft comments pinned to real elements."
      });
      return;
    }
    el.innerHTML = S.reviews.map(function (r) {
      var ready = r.status === "ready" || !r.status;
      var open = r.open ? '<span class="mk-open">' + r.open + " open</span>" : "";
      // A run that has not assembled is listed too — it is usually one waiting
      // at the approval gate. It must not look like a finished review, so its
      // footer states the run status and the stage instead of counts.
      var foot = ready
        ? '<span>' + r.shots + " view" + (r.shots === 1 ? "" : "s") + "</span>" +
          "<span>" + r.comments + " comment" + (r.comments === 1 ? "" : "s") + "</span>" + open
        : '<span class="mk-open">' +
          (r.status === "paused" ? "needs you" : esc(r.status)) +
          (r.stage ? " · " + esc(r.stage) : "") + "</span>";
      return '<article class="mk-card' + (ready ? "" : " mk-card-stopped") +
        '" data-open="' + escAttr(r.id) + '">' +
        '<h3>' + esc(r.title) + "</h3>" +
        '<p class="mk-muted">' + esc(r.source || "") + "</p>" +
        "<footer>" + foot +
        '<span class="mk-when">' + esc(r.updated || r.created || "") + "</span></footer>" +
        "</article>";
    }).join("");
  }

  // ── detail: filters ───────────────────────────────────────────────

  function renderFilters() {
    var tags = rubric().tags || [];
    var counts = {};
    (S.review.comments || []).forEach(function (c) {
      counts[c.tag] = (counts[c.tag] || 0) + 1;
    });
    $("mk-filters").innerHTML =
      tags.map(function (t) {
        var on = S.tags[t.id] !== false;
        return '<button class="mk-chip' + (on ? " on" : "") + '" data-tag="' + escAttr(t.id) + '" ' +
          'title="' + escAttr(t.desc) + '">' + esc(t.label) +
          ' <em>' + (counts[t.id] || 0) + "</em></button>";
      }).join("") +
      '<button class="mk-chip mk-chip-alt' + (S.hideResolved ? " on" : "") +
      '" data-toggle="resolved">Hide resolved</button>';
  }

  function visible(c) {
    if (S.tags[c.tag] === false) return false;
    if (S.hideResolved && c.status !== "open") return false;
    return true;
  }

  // ── detail: rail, canvas, gutter ──────────────────────────────────

  function renderRail() {
    var shots = S.review.shots || [];
    $("mk-rail").innerHTML = shots.map(function (s) {
      var n = (S.review.comments || []).filter(function (c) {
        return c.shot_id === s.id && visible(c);
      }).length;
      var vp = s.viewport && s.viewport.w ? s.viewport.w + "×" + s.viewport.h : "";
      return '<button class="mk-thumb' + (s.id === S.shot ? " on" : "") + '" data-shot="' + escAttr(s.id) + '">' +
        '<img src="/markitup/api/reviews/' + encodeURIComponent(S.review.id) +
        "/shots/" + encodeURIComponent(s.image) + '" alt="" loading="lazy">' +
        '<span class="mk-thumb-t">' + esc(s.title) + "</span>" +
        '<span class="mk-thumb-m">' + esc(vp) + " · " + n + "</span>" +
        "</button>";
    }).join("");
  }

  function shotById(id) {
    var shots = S.review.shots || [];
    for (var i = 0; i < shots.length; i++) if (shots[i].id === id) return shots[i];
    return null;
  }

  function renderCanvas() {
    var shot = shotById(S.shot);
    var el = $("mk-canvas");
    if (!shot) { el.innerHTML = '<p class="mk-muted">Select a view.</p>'; return; }
    var pins = (S.review.comments || []).filter(function (c) {
      return c.shot_id === shot.id && visible(c) &&
        typeof c.x === "number" && typeof c.y === "number";
    }).map(function (c) {
      var m = tagMeta(c.tag) || {};
      return '<button class="mk-pin v-' + escAttr(m.variant || "neutral") +
        (c.n === S.selected ? " on" : "") +
        (c.anchor === "dom" ? "" : " est") +
        '" style="left:' + (c.x * 100).toFixed(3) + "%;top:" + (c.y * 100).toFixed(3) + '%"' +
        ' data-pin="' + c.n + '" title="' + escAttr(c.title) +
        (c.anchor === "dom" ? "" : " (estimated position)") + '">' + c.n + "</button>";
    }).join("");
    el.innerHTML =
      '<div class="mk-figure">' +
      '<img id="mk-img" src="/markitup/api/reviews/' + encodeURIComponent(S.review.id) +
      "/shots/" + encodeURIComponent(shot.image) + '" alt="">' + pins + "</div>";
  }

  function renderGutter() {
    var shot = shotById(S.shot);
    var rows = (S.review.comments || []).filter(function (c) {
      return shot && c.shot_id === shot.id && visible(c);
    });
    var head = shot
      ? '<div class="mk-gutter-head"><strong>' + esc(shot.title) + "</strong>" +
        '<p class="mk-muted">' + esc(shot.url || "") + (shot.focus ? " · " + esc(shot.focus) : "") + "</p></div>"
      : "";
    if (!rows.length) {
      $("mk-gutter").innerHTML = head + '<p class="mk-muted mk-pad">No comments match the current filters.</p>';
      return;
    }
    $("mk-gutter").innerHTML = head + rows.map(function (c) {
      var m = tagMeta(c.tag) || {};
      // Exactly one provenance chip, never two. "estimated" belongs only to a
      // vision-placed pin; a comment with no pin at all is not an estimate of
      // anything, and showing both read as a contradiction on screen.
      var est = "";
      if (typeof c.x !== "number") {
        est = '<span class="mk-est" title="No pin — this comment is about the view as a whole">no pin</span>';
      } else if (c.anchor !== "dom") {
        est = '<span class="mk-est" title="Placed by estimate, not measured from the page">estimated</span>';
      }
      return '<article class="mk-note' + (c.n === S.selected ? " on" : "") +
        (c.status !== "open" ? " done" : "") + '" data-note="' + c.n + '">' +
        '<header><b class="mk-num v-' + escAttr(m.variant || "neutral") + '">' + c.n + "</b>" +
        tagBadge(c.tag) + est +
        '<span class="mk-note-sp"></span>' +
        '<button class="mk-mini" data-act="edit" data-n="' + c.n + '" title="Edit text and tag">✎</button>' +
        '<button class="mk-mini" data-act="accept" data-n="' + c.n + '" title="Accept">✓</button>' +
        '<button class="mk-mini" data-act="dismiss" data-n="' + c.n + '" title="Dismiss">✕</button>' +
        "</header>" +
        "<h4>" + esc(c.title) + "</h4>" +
        (c.body ? "<p>" + esc(c.body) + "</p>" : "") +
        (c.status !== "open" ? '<span class="mk-state">' + esc(c.status) + "</span>" : "") +
        "</article>";
    }).join("");
  }

  function renderAnchorHealth() {
    // A review whose comments could not be anchored is still readable prose,
    // but it is not what this app promises. Saying so — with the reason and the
    // lever — beats rendering a screenshot with no pins and letting the user
    // assume that is normal.
    var a = S.review.anchoring;
    var box = $("mk-anchor-health");
    if (!a || !a.comments || a.anchored > 0) { box.hidden = true; box.textContent = ""; return; }
    box.hidden = false;
    var why = a.unresolved_ids && a.unresolved_ids.length
      ? "The model named elements that are not on the page (" +
        esc(a.unresolved_ids.join(", ")) + ")."
      : "The model did not name any element, so every comment is about the view as a whole.";
    box.innerHTML =
      "<strong>No comment could be pinned.</strong> " + why +
      " The comments below are still usable; the pins are not. A stronger model" +
      " usually anchors most comments — switch it with the model pill above and" +
      " run the review again.";
  }

  function renderDetail() {
    $("mk-d-title").textContent = S.review.title || S.review.id;
    var r = S.rubrics[S.review.rubric];
    var n = (S.review.comments || []).length;
    $("mk-d-meta").textContent =
      (r ? r.label : S.review.rubric) + " · " +
      (S.review.shots || []).length + " views · " + n + " comments" +
      (S.review.source ? " · " + S.review.source : "");
    renderAnchorHealth();
    renderSummary();
    renderProposal();
    renderFilters();
    renderRail();
    renderCanvas();
    renderGutter();
  }

  // ── summary ───────────────────────────────────────────────────────
  // The review-level argument no pin can carry: themes, the order to tackle
  // them, how the review was made. Markdown, printed first in every export.

  function renderSummary() {
    var box = $("mk-summary");
    if (!S.review) { box.hidden = true; return; }
    box.hidden = false;
    $("mk-summary-form").hidden = true;
    var body = $("mk-summary-body");
    body.hidden = false;
    var text = S.review.summary || "";
    if (text) {
      EOS_UI.paintMarkdown(body, text);
      $("mk-summary-edit").textContent = "Edit";
    } else {
      body.innerHTML = '<span class="mk-muted">No general comments yet — the themes, the order ' +
        "to tackle them, and how the review was made.</span>";
      $("mk-summary-edit").textContent = "Add comments";
    }
  }

  function editSummary() {
    if (!S.review) return;
    $("mk-summary-text").value = S.review.summary || "";
    $("mk-summary-body").hidden = true;
    $("mk-summary-form").hidden = false;
    $("mk-summary-text").focus();
  }

  async function saveSummary() {
    if (!S.review) return;
    var btn = $("mk-summary-save");
    btn.disabled = true;
    var d = await EOS.apiSafe(
      "/markitup/api/reviews/" + encodeURIComponent(S.review.id),
      { method: "PATCH", body: JSON.stringify({ summary: $("mk-summary-text").value }) }
    );
    btn.disabled = false;
    if (!d || d.error) { EOS.toast((d && d.error) || "Save failed", "error"); return; }
    // The server's copy, not the textarea's: it strips and normalises.
    S.review.summary = d.summary;
    renderSummary();
    EOS.toast(d.summary ? "General comments saved" : "General comments cleared", "success");
  }

  $("mk-summary-edit").addEventListener("click", editSummary);
  $("mk-summary-save").addEventListener("click", saveSummary);
  $("mk-summary-cancel").addEventListener("click", renderSummary);

  // ── approval gate ─────────────────────────────────────────────────

  function renderApprove() {
    var box = $("mk-approve");
    var st = S.run;
    var paused = st && st.status === "paused" && st.stage === "discover";
    if (!paused) { box.hidden = true; box.innerHTML = ""; return; }
    var views = ((st.results || {}).discover || {}).views || [];
    box.hidden = false;
    box.innerHTML =
      "<h3>Approve the shot list</h3>" +
      '<p class="mk-muted">Nothing has been captured yet. Untick anything not worth reviewing — ' +
      "capture and comments are only paid for once you continue.</p>" +
      '<ul class="mk-views">' + views.map(function (v, i) {
        return '<li><label><input type="checkbox" checked data-view="' + i + '">' +
          "<span><strong>" + esc(v.title || v.url) + "</strong>" +
          '<em>' + esc(v.url) + (v.selector ? "  · " + esc(v.selector) : "") + "</em>" +
          (v.focus ? "<span>" + esc(v.focus) + "</span>" : "") + "</span></label></li>";
      }).join("") + "</ul>" +
      '<button class="eos-btn eos-btn-primary" id="mk-resume">Capture &amp; review these</button>';
  }

  // ── data ──────────────────────────────────────────────────────────

  async function loadList() {
    var d = await EOS.apiSafe("/markitup/api/reviews");
    S.reviews = d && d.reviews ? d.reviews : [];
    renderList();
  }

  async function openReview(id) {
    $("mk-list-view").hidden = true;
    $("mk-detail").hidden = false;

    // Fetch BOTH before deciding. A run waiting at the approval gate has no
    // review.json yet — assemble has not run — so fetching the review first and
    // bailing on its error made the approval gate unreachable from the UI
    // entirely: the toast said "shot list ready", the page said "no such
    // review", and the only way to approve was to hand-POST /resume.
    var got = await Promise.all([
      EOS.apiSafe("/markitup/api/reviews/" + encodeURIComponent(id)),
      EOS.apiSafe("/markitup/api/run/" + encodeURIComponent(id) + "/status")
    ]);
    var d = got[0], run = got[1];

    S.review = (d && d.review) ? d.review : null;
    S.run = (run && run.run) ? run.run : null;
    S.hasProposal = !!(d && d.has_proposal);
    S.tags = {};
    S.selected = null;
    S.shot = (S.review && S.review.shots && S.review.shots.length)
      ? S.review.shots[0].id : null;

    if (!S.review && !S.run) {
      resetDetail();
      $("mk-canvas").innerHTML = EOS_UI.errorState({
        message: "Could not open that review — " +
          ((d && d.error) || "the daemon did not answer.")
      });
      return;
    }

    renderApprove();
    if (S.review) {
      renderDetail();
    } else {
      // A run that exists but has not assembled: show what it is doing, and
      // (when it is paused at discover) the approval box above.
      renderRunOnly(id);
    }
  }

  function resetDetail() {
    S.review = null; S.run = null; S.shot = null; S.selected = null;
    S.hasProposal = false;
    $("mk-view-proposal").hidden = true; $("mk-rereview").hidden = true;
    $("mk-approve").hidden = true; $("mk-approve").innerHTML = "";
    $("mk-anchor-health").hidden = true;
    $("mk-summary").hidden = true;
    $("mk-filters").innerHTML = "";
    $("mk-rail").innerHTML = "";
    $("mk-gutter").innerHTML = "";
    $("mk-canvas").innerHTML = "";
    $("mk-d-title").textContent = "";
    $("mk-d-meta").textContent = "";
  }

  function renderRunOnly(id) {
    var st = S.run || {};
    var inputs = st.inputs || {};
    $("mk-d-title").textContent = inputs.title || inputs.url || id;
    var done = (st.completed || []).length;
    $("mk-d-meta").textContent =
      st.status + (st.stage ? " · " + st.stage : "") +
      " · " + done + "/5 stages" + (inputs.url ? " · " + inputs.url : "");
    $("mk-filters").innerHTML = "";
    $("mk-rail").innerHTML = "";
    $("mk-gutter").innerHTML = "";
    // A stopped run needs a way back, and it must say WHY it stopped —
    // .claude/rules/staged-pipeline.md's UI contract.
    $("mk-canvas").innerHTML = st.error
      ? EOS_UI.errorState({ message: "This run stopped at " +
          esc(st.stage || "an early stage") + " — " + esc(st.error) })
      : '<p class="mk-muted mk-pad">Nothing captured yet' +
        (st.status === "paused" ? " — approve the shot list above to continue."
                                : " (" + esc(st.status || "unknown") + ").") + "</p>";
  }

  function closeReview() {
    $("mk-detail").hidden = true;
    $("mk-list-view").hidden = false;
    resetDetail();
    loadList();
  }

  var route = EOS_UI.hashRoute({
    onShow: function (id) { openReview(id); },
    onHide: function () { closeReview(); }
  });

  // Returns whether the write landed. The pin drag below needs to know: on a
  // refusal it has to re-render to snap the pin back to the STORED position,
  // or the screen keeps showing a placement the server never accepted.
  async function patchComment(n, patch) {
    var d = await EOS.apiSafe(
      "/markitup/api/reviews/" + encodeURIComponent(S.review.id) + "/comments/" + n,
      { method: "PATCH", body: JSON.stringify(patch) }
    );
    if (!d || d.error) {
      EOS.toast((d && d.error) || "Could not save that", "error");
      return false;
    }
    (S.review.comments || []).forEach(function (c, i) {
      if (c.n === n) S.review.comments[i] = d.comment;
    });
    renderFilters(); renderRail(); renderCanvas(); renderGutter();
    return true;
  }

  function editComment(n) {
    var c = (S.review.comments || []).filter(function (x) { return x.n === n; })[0];
    if (!c) return;
    // Options must be plain strings (see newReview's note). The LABEL is the
    // safe string to pass both ways: normalize_tag accepts a display label as
    // readily as an id, and formHtml preselects by `o === val`, so sending the
    // label is what makes the current tag come up already chosen.
    var labels = (rubric().tags || []).map(function (t) { return t.label; });
    var cur = tagMeta(c.tag);
    EOS_UI.formModal({
      title: "Edit comment " + n,
      fields: [
        { key: "title", label: "Title", type: "text",
          value: c.title || "", required: true },
        { key: "body", label: "Body", type: "textarea", value: c.body || "" },
        { key: "tag", label: "Tag", type: "select", options: labels,
          value: cur ? cur.label : "" }
      ],
      onSubmit: function (v) {
        patchComment(n, { title: v.title, body: v.body, tag: v.tag });
      }
    });
  }

  function newReview() {
    // formHtml's field vocabulary is NOT settingsPanel's, and the differences
    // all fail silently:
    //   - `options` must be plain strings. An {value,label} object renders
    //     "[object Object]" and submits it (settingsPanel accepts objects,
    //     formHtml does not).
    //   - the boolean type is `checkbox`; "boolean" is not in formHtml's type
    //     list, so it falls through to a TEXT input — and any keystroke in a
    //     box labelled "skip the approval gate" would then arm the spend gate.
    //   - `hint` is not rendered at all, so guidance has to live in the label.
    // Verified against eos-components.js formHtml/formValues, not assumed.
    EOS_UI.formModal({
      title: "New review",
      fields: [
        { key: "url", label: "URL — a public page, or one of this daemon's own",
          type: "text", required: true },
        { key: "title", label: "Title (optional — defaults to the page title)",
          type: "text" },
        { key: "rubric", label: "Rubric — site: someone else's page · design: one of ours · requirements: a rendered spec (a URL gives it headings to pin to, a document gives it requirement rows)",
          type: "select", options: Object.keys(S.rubrics) },
        { key: "approve", type: "checkbox",
          label: "Skip the approval gate (captures and spends immediately)" }
      ],
      onSubmit: async function (v) {
        var d = await EOS.apiSafe("/markitup/api/run", {
          method: "POST", body: JSON.stringify(v)
        });
        if (!d || d.error) { EOS.toast((d && d.error) || "Could not start", "error"); return; }
        EOS.toast(d.status === "paused" ? "Shot list ready for approval" : "Review complete", "success");
        await loadList();
        if (d.run_id) route.set(d.run_id);
      }
    });
  }

  // ── events (delegated — no inline handlers anywhere) ──────────────

  $("mk-new").addEventListener("click", newReview);
  $("mk-proposal-file").addEventListener("change", async function (e) {
    var f = e.target.files && e.target.files[0];
    e.target.value = "";   // so re-picking the same file fires again
    await uploadProposal(f);
  });
  $("mk-back").addEventListener("click", function () { route.clear(); });

  $("mk-list").addEventListener("click", function (e) {
    var card = e.target.closest("[data-open]");
    if (card) route.set(card.getAttribute("data-open"));
  });

  $("mk-filters").addEventListener("click", function (e) {
    var chip = e.target.closest("[data-tag]");
    if (chip) {
      var t = chip.getAttribute("data-tag");
      S.tags[t] = S.tags[t] === false;
      renderFilters(); renderRail(); renderCanvas(); renderGutter();
      return;
    }
    if (e.target.closest('[data-toggle="resolved"]')) {
      S.hideResolved = !S.hideResolved;
      renderFilters(); renderRail(); renderCanvas(); renderGutter();
    }
  });

  $("mk-rail").addEventListener("click", function (e) {
    var b = e.target.closest("[data-shot]");
    if (!b) return;
    S.shot = b.getAttribute("data-shot");
    S.selected = null;
    renderRail(); renderCanvas(); renderGutter();
  });

  // ── pin selection + drag ──────────────────────────────────────────
  // A pointerup always emits a click too, so a finished drag would ALSO run
  // the select handler below and scroll the gutter out from under the user.
  // The flag is consumed by exactly one click, never left armed.
  var suppressClick = false;
  var drag = null;
  var DRAG_SLOP_PX = 4;   // under this, a press-and-release is a click

  $("mk-canvas").addEventListener("click", function (e) {
    if (suppressClick) { suppressClick = false; return; }
    var pin = e.target.closest("[data-pin]");
    if (!pin) return;
    S.selected = parseInt(pin.getAttribute("data-pin"), 10);
    renderCanvas(); renderGutter();
    var note = document.querySelector('.mk-note[data-note="' + S.selected + '"]');
    if (note) note.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });

  // Normalised against the FIGURE's rendered box — convention 2 at the top of
  // this file. The stored x/y are percentages of the captured region, and
  // exporting.py prints those same percentages, so a drop measured in pixels
  // off the on-screen image would land somewhere else in the PDF.
  function figurePoint(e, fig) {
    var r = fig.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))
    };
  }

  $("mk-canvas").addEventListener("pointerdown", function (e) {
    if (e.button !== 0) return;
    var pin = e.target.closest("[data-pin]");
    var fig = pin && pin.closest(".mk-figure");
    if (!fig) return;
    drag = { n: parseInt(pin.getAttribute("data-pin"), 10), pin: pin, fig: fig,
             moved: false, x0: e.clientX, y0: e.clientY, pt: null };
    // Capture on the pin, so a fast drag that outruns the 22px button still
    // reports moves instead of stranding the pin mid-flight.
    try { pin.setPointerCapture(e.pointerId); } catch (err) { /* non-fatal */ }
    e.preventDefault();   // suppress native drag-image + text selection
  });

  $("mk-canvas").addEventListener("pointermove", function (e) {
    if (!drag) return;
    if (!drag.moved && Math.abs(e.clientX - drag.x0) < DRAG_SLOP_PX
                    && Math.abs(e.clientY - drag.y0) < DRAG_SLOP_PX) return;
    var pt = figurePoint(e, drag.fig);
    if (!pt) return;
    drag.moved = true;
    drag.pt = pt;
    drag.pin.classList.add("dragging");
    drag.pin.style.left = (pt.x * 100).toFixed(3) + "%";
    drag.pin.style.top = (pt.y * 100).toFixed(3) + "%";
  });

  async function endDrag(e) {
    var d = drag;
    if (!d) return;
    drag = null;
    try { d.pin.releasePointerCapture(e.pointerId); } catch (err) { /* gone */ }
    d.pin.classList.remove("dragging");
    if (!d.moved || !d.pt) return;   // never moved — let the click select it
    suppressClick = true;
    // The server marks a hand-moved pin `anchor: human`, so the re-render
    // inside patchComment is what flips the chip from measured to estimated.
    var ok = await patchComment(d.n, { x: d.pt.x, y: d.pt.y });
    if (!ok) renderCanvas();   // refused — snap back to the stored position
  }

  $("mk-canvas").addEventListener("pointerup", endDrag);
  $("mk-canvas").addEventListener("pointercancel", endDrag);

  $("mk-gutter").addEventListener("click", function (e) {
    var act = e.target.closest("[data-act]");
    if (act) {
      var n = parseInt(act.getAttribute("data-n"), 10);
      var kind = act.getAttribute("data-act");
      // Branch edit out FIRST. The status ternary below is "accept or else
      // dismiss", so any third action falling into it would silently dismiss
      // the comment it was meant to open for editing.
      if (kind === "edit") { editComment(n); return; }
      var want = kind === "accept" ? "accepted" : "dismissed";
      var cur = (S.review.comments || []).filter(function (c) { return c.n === n; })[0];
      patchComment(n, { status: cur && cur.status === want ? "open" : want });
      return;
    }
    var note = e.target.closest("[data-note]");
    if (note) {
      S.selected = parseInt(note.getAttribute("data-note"), 10);
      renderCanvas(); renderGutter();
    }
  });

  $("mk-approve").addEventListener("click", async function (e) {
    if (!e.target.closest("#mk-resume") || !S.review || !S.run) return;
    var keep = [];
    var views = ((S.run.results || {}).discover || {}).views || [];
    document.querySelectorAll("#mk-approve [data-view]").forEach(function (cb) {
      if (cb.checked) keep.push(views[parseInt(cb.getAttribute("data-view"), 10)]);
    });
    if (!keep.length) { EOS.toast("Pick at least one view", "error"); return; }
    e.target.disabled = true;
    e.target.textContent = "Capturing…";
    var d = await EOS.apiSafe(
      "/markitup/api/run/" + encodeURIComponent(S.review.id) + "/resume",
      { method: "POST", body: JSON.stringify({ views: keep }) }
    );
    if (!d || d.error) { EOS.toast((d && d.error) || "Run failed", "error"); }
    openReview(S.review.id);
  });

  // A proposal is a rebuilt version of the reviewed page. Serving it from this
  // daemon is what lets it be re-reviewed at all — capture refuses file:// and
  // refuses any non-public host that is not us — so the loop is upload, then
  // re-review, then compare the two reviews.
  function renderProposal() {
    var id = S.review && S.review.id;
    var link = $("mk-view-proposal");
    var again = $("mk-rereview");
    var url = id ? "/markitup/api/reviews/" + encodeURIComponent(id) + "/proposal" : "";
    link.hidden = again.hidden = !(id && S.hasProposal);
    if (url) link.href = url;
  }

  async function uploadProposal(file) {
    if (!file || !S.review) return;
    var html = await file.text();
    var d = await EOS.apiSafe(
      "/markitup/api/reviews/" + encodeURIComponent(S.review.id) + "/proposal",
      { method: "POST", body: JSON.stringify({ html: html }) }
    );
    if (!d || d.error) { EOS.toast((d && d.error) || "Upload failed", "error"); return; }
    S.hasProposal = true;
    renderProposal();
    EOS.toast("Proposal saved (" + d.bytes + " bytes)", "success");
  }

  document.querySelector(".mk-detail-actions").addEventListener("click", async function (e) {
    var act = e.target.closest("[data-act]");
    if (act && S.review) {
      if (act.getAttribute("data-act") === "upload") {
        $("mk-proposal-file").click();
        return;
      }
      // Re-review: the URL is built server-side, because a browser reaching the
      // daemon through a tailnet name would send an origin that capture's own
      // host+port check rejects even though it is us.
      act.disabled = true;
      var r = await EOS.apiSafe(
        "/markitup/api/reviews/" + encodeURIComponent(S.review.id) + "/rereview",
        { method: "POST", body: "{}" }
      );
      act.disabled = false;
      if (!r || r.error) { EOS.toast((r && r.error) || "Could not start", "error"); return; }
      EOS.toast("Re-review complete", "success");
      await loadList();
      if (r.run_id) route.set(r.run_id);
      return;
    }
    var b = e.target.closest("[data-export]");
    if (!b || !S.review) return;
    b.disabled = true;
    var d = await EOS.apiSafe(
      "/markitup/api/reviews/" + encodeURIComponent(S.review.id) + "/export",
      { method: "POST", body: JSON.stringify({ format: b.getAttribute("data-export") }) }
    );
    b.disabled = false;
    if (!d || d.error) { EOS.toast((d && d.error) || "Export failed", "error"); return; }
    EOS.toast("Written to " + d.path, "success");
  });

  // ── boot ──────────────────────────────────────────────────────────

  var settings = EOS_UI.settingsPanel({
    id: "mk-settings-panel", title: "Mark It Up settings", app: "markitup"
  });
  $("mk-settings").addEventListener("click", function () { settings.open(); });

  EOS_UI.modelPill({ app: "markitup", mount: "#model-pill", domain: "text" });

  (async function boot() {
    var d = await EOS.apiSafe("/markitup/api/rubrics");
    ((d && d.rubrics) || []).forEach(function (r) { S.rubrics[r.id] = r; });
    await loadList();
    route.init();
  })();
})();
