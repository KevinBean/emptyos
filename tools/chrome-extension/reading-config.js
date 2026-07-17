// The reading layer's fallback settings — ONE copy, three consumers.
//
// The daemon owns the real settings; these are only what we fall back to when it
// cannot be reached. `mode: "off"` is the load-bearing one: a layer that would
// otherwise send page text somewhere must fail DORMANT, never open.
//
// Loaded by the service worker (importScripts), the content script (listed first
// in manifest content_scripts), and the side panel (a <script> tag). Three copies
// of this object is three chances for them to disagree about what "off" means.

globalThis.EOS_READING_DEFAULTS = {
  mode: "off",              // off | ask | flow
  display: "auto",          // auto | margin | corner | ink
  flow_provider: "",        // Flow's model — cloud OR local
  local_provider: "",       // Ask's model — local only
  excluded_hosts: [],
  // Hosts the reader has DELIBERATELY opened up despite being private by default
  // (see below). Empty is the safe state and the one we ship.
  allowed_private_hosts: [],
  // The reader's CEFR band — the bar a word must clear to be worth interrupting them.
  // The canonical value is the `dictionary.cefr_level` setting; this object is spread
  // OVER the daemon's reply in both background.js and sidepanel.js, so a key missing
  // here is dropped client-side even when the daemon sends it.
  cefr_level: "auto",
  native_language: "Chinese",
  target_language: "English",
  pronounce: true,
  rail: true,               // the scroll-following word list
  enrich_on_save: true,     // polish a saved note with a strong model
};

// Surfaces the reading layer is OFF on unless the reader names the host himself.
//
// Reading is not passive: Flow sends the visible text of the page to a model. On
// an article that is the point. On a mail, banking, health, or messaging page
// that text is the reader's private correspondence, and a grant that says "read
// the web for me" was never consent to read THAT. The existing "Pause this site"
// button is the wrong shape of protection for these — it is opt-out, so by the
// time it is clicked the page has already been read and sent.
//
// This list cannot be complete, and pretending otherwise would be the real
// danger: it covers the categories where a single mistake is unacceptable, and
// anything else the reader distrusts still goes in `excluded_hosts`. Being
// default-closed on the obvious cases is worth more than a list that claims to
// know every private site on the web.
globalThis.EOS_READING_PRIVATE_HOSTS = [
  // mail
  "mail.google.com", "inbox.google.com", "mail.yahoo.com", "outlook.com",
  "outlook.live.com", "outlook.office.com", "outlook.office365.com",
  "hotmail.com", "mail.proton.me", "protonmail.com", "tutanota.com",
  "fastmail.com", "zoho.com", "mail.aol.com", "roundcube.net", "hey.com",
  // messaging — a DM is mail by another name
  "web.whatsapp.com", "messenger.com", "web.telegram.org", "discord.com",
  "slack.com", "teams.microsoft.com", "chat.google.com", "signal.org",
  // money
  "paypal.com", "stripe.com", "wise.com", "revolut.com", "coinbase.com",
  "commbank.com.au", "nab.com.au", "anz.com.au", "westpac.com.au",
  "ing.com.au", "ubank.com.au", "americanexpress.com", "interactivebrokers.com",
  // identity, health, and the keys to everything else
  "1password.com", "bitwarden.com", "lastpass.com", "accounts.google.com",
  "login.microsoftonline.com", "my.gov.au", "ato.gov.au", "medicare.gov.au",
  "immi.homeaffairs.gov.au",
];

// Providers that answer by spawning a subprocess. Correct, often free — and slow:
// claude-cli needs ~33s on a full page, almost all of it process spawn. Reading is
// latency-bound, so the daemon already puts these LAST when the model is on Auto.
// Naming them here lets the reader see the cost before choosing one, rather than
// discovering it as a rail that sits there. Mirrors `_SLOW_TO_SPAWN` in
// apps/.../dictionary/reading.py.
globalThis.EOS_READING_SLOW_PROVIDERS = ["claude-cli", "codex", "gemini-cli"];

globalThis.EOS_READING_IS_PRIVATE_HOST = function (hostname) {
  const here = String(hostname || "").toLowerCase();
  if (!here) return false;
  return globalThis.EOS_READING_PRIVATE_HOSTS.some(
    entry => here === entry || here.endsWith("." + entry),
  );
};
