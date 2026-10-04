/* Picture Dictionary — "Talk about it": the pure half.
 *
 * Loaded before pictures.js so node --test can reach these without the page's
 * boot code (.claude/rules/testing.md). Every global carries the `talkAbout`
 * prefix: the page and its sibling scripts share one scope
 * (.claude/rules/multi-module-apps.md).
 */
'use strict';

var TALK_ABOUT_MAX_MS = 60000;   // the prompt asks for 30–60 s; stop at 60
// Mirrors TALK_TARGET_SECONDS in picture_speak.py for the live clock; the
// result screen shows the server's own `target_seconds`.
var TALK_ABOUT_TARGET_S = 30;
var TALK_ABOUT_HINTS = 8;

/* Questions that keep a description going. Shown with every picture. */
var TALK_ABOUT_QUESTIONS = [
  'What is it, and what is it for?',
  'Where do you usually find one?',
  'What does it look like — colour, size, what it is made of?',
  'How do you use it? Walk through the steps.',
  'When did you last use one? What happened?',
];

/* "0:12 · keep going to 0:30", then "0:34 · stop whenever you like". */
function talkAboutTimerLabel(ms, targetS) {
  var s = Math.max(0, Math.floor((ms || 0) / 1000));
  var clock = Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
  var t = targetS || TALK_ABOUT_TARGET_S;
  var target = Math.floor(t / 60) + ':' + ('0' + (t % 60)).slice(-2);
  return s < t ? clock + ' · keep going to ' + target : clock + ' · stop whenever you like';
}

/* Up to `n` other words from the same pack to try using, never the object
 * itself (saying its name is checked separately). `rand` is injectable. */
function talkAboutHints(items, slug, n, rand) {
  var pool = (items || []).filter(function (it) { return it && it.slug && it.slug !== slug; });
  var r = rand || Math.random;
  for (var i = pool.length - 1; i > 0; i--) {
    var j = Math.floor(r() * (i + 1));
    var t = pool[i]; pool[i] = pool[j]; pool[j] = t;
  }
  return pool.slice(0, n || TALK_ABOUT_HINTS).map(function (it) { return it.name; });
}

/* The pack the scene words come from: the one being browsed when the object
 * is in it, else the object's own first pack. The server applies the same
 * rule; the hints are drawn from that pack so they are exactly the words that
 * get counted (a search or a group filter must not narrow them). */
function talkAboutScenePack(it, browsing) {
  var packs = (it && it.packs) || [];
  return (browsing && packs.indexOf(browsing) >= 0) ? browsing : ((it && it.pack) || '');
}

/* A "use these words in an Improv scene" button, or nothing when Improv is not
 * loaded. The dictionary ships in the open EnglishOS edition and Improv does
 * not, so an unconditional button would link a public install to an app it
 * cannot have. `hasImprov` is injectable; by default it asks EOS.hasApp. */
function improvSceneButton(label, hasImprov) {
  var has = (hasImprov !== undefined) ? hasImprov
    : (typeof EOS !== 'undefined' && !!EOS.hasApp && EOS.hasApp('improv'));
  if (!has) return '';
  return '<button class="eos-btn" onclick="improvSceneOpen()">' + label + '</button> ';
}
