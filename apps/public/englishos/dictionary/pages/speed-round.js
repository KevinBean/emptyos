/* Picture Dictionary — speed round: the pure half.
 *
 * Kept apart from pictures.js so node --test can load it without that file's
 * boot code (.claude/rules/testing.md). Everything here is a function of its
 * arguments; the round's recorder and rendering live in pictures.js.
 * Every global carries the `speedRound` prefix because the page and its
 * sibling scripts share one scope (.claude/rules/multi-module-apps.md).
 */
'use strict';

var SPEED_ROUND_SIZE = 10;

/* The words a learner reads in the results table. Status is never colour
 * alone (.claude/rules/list-card-density.md), so every verdict has one. */
var SPEED_ROUND_LABELS = {
  fast: 'Fast',
  right: 'Right (time not measured)',
  slow: 'Slow',
  missed: 'Missed',
  pending: 'Scoring…',
  error: 'Not scored',
};

/* Verdict → a shared badge status (EOS_UI.STATUS_VARIANTS). */
var SPEED_ROUND_BADGE = {
  fast: 'pass', right: 'pass', slow: 'draft', missed: 'fail',
  pending: 'running', error: 'archived',
};

function speedRoundLabel(verdict) {
  return SPEED_ROUND_LABELS[verdict] || SPEED_ROUND_LABELS.error;
}

/* Pick up to `n` cards from the list the learner is browsing, in random
 * order. `rand` is injectable so a test can pin the shuffle. */
function speedRoundPick(items, n, rand) {
  var pool = (items || []).filter(function (it) { return it && it.slug; });
  var r = rand || Math.random;
  for (var i = pool.length - 1; i > 0; i--) {
    var j = Math.floor(r() * (i + 1));
    var t = pool[i]; pool[i] = pool[j]; pool[j] = t;
  }
  return pool.slice(0, n || SPEED_ROUND_SIZE);
}

/* Counts for the results header. `remembered` is fast + right (right =
 * correct but speed unmeasurable), so it never claims a speed it did not
 * see. The average covers only answers that were right AND timed: a missed
 * word's "onset" is how long it took to say something wrong, which is not a
 * recall time. */
function speedRoundSummary(results) {
  var out = { total: 0, remembered: 0, slow: 0, missed: 0, pending: 0, avg_s: null };
  var sum = 0, timed = 0;
  (results || []).forEach(function (r) {
    out.total += 1;
    var v = r && r.verdict;
    if (v === 'fast' || v === 'right') out.remembered += 1;
    else if (v === 'slow') out.slow += 1;
    else if (v === 'missed' || v === 'error') out.missed += 1;
    else out.pending += 1;
    if ((v === 'fast' || v === 'slow') && typeof r.onset_s === 'number') {
      sum += r.onset_s; timed += 1;
    }
  });
  if (timed) out.avg_s = Math.round((sum / timed) * 10) / 10;
  return out;
}
