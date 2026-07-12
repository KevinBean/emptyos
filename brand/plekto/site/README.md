# plekto.dev — landing page

Static single-page site for the Plekto distribution. Deploys to Cloudflare
Pages. Pure HTML + CSS + a single Pages Function for email capture; no
framework, no build step.

## Layout

```
site/
├── index.html              Hero + 3 features + footer
├── style.css               All styling (slate + amber palette, dark mode aware)
├── thanks/index.html       Success page after signup
├── functions/signup.js     Cloudflare Pages Function — POST /signup
├── favicon.svg             Brand mark for tab icon
├── icon.svg                Same, referenced in hero <img>
├── icon_32px.png           Legacy PNG favicon fallback
├── icon_256px.png          Apple touch icon
└── og-image.png            1200×630 social card
```

## Deployment — one-time setup

### 1. Create the GitHub repo

```bash
# On GitHub: create a new EMPTY repo at github.com/plekto-dev/site
# (no README, no .gitignore, no license — just the empty repo)
```

Then push this `site/` directory to it as the initial commit. From the
emptyos-private repo root:

```bash
# Quick one-shot — clone the empty target, copy site/, push
cd /tmp
git clone https://github.com/plekto-dev/site.git
cp -r D:/emptyos/brand/plekto/site/* site/
cd site
git add -A
git commit -m "Initial commit — Plekto landing page"
git push -u origin main
```

**Ongoing updates** — Plekto is registered as a `static-mirror` site in
the Publish app. Edit files in `brand/plekto/site/` here, then deploy
through Publish like any other site:

- **UI:** open `/publish/`, select Plekto, hit Deploy.
- **API:** `POST /publish/api/deploy {"site_id": "plekto"}`.
- **CLI:** `eos publish deploy --site plekto`.

The static-mirror mode (see `apps/publish/app.py` `_deploy_static_mirror`)
handles the publishing semantics:

- Clones `plekto-dev/site` into `data/apps/publish/sites/plekto/site/`
  (first deploy) or pulls `--ff-only` (subsequent deploys).
- Mirrors `brand/plekto/site/` files into the clone — copies new/changed,
  deletes files not in source, **preserves** anything listed in the
  site's `exclude_files` (configured: `wrangler.jsonc`, `README.md`).
  Cloudflare's auto-generated `wrangler.jsonc` survives every sync.
- Commits with `publish: <timestamp>` and pushes to `main` **without
  `--force`**. Force-pushing main would clobber third-party commits
  (Cloudflare autoconfig, etc.) — refused at the code level.
- If the local clone has diverged (someone pushed to plekto-dev/site
  directly), the deploy fails with a manual recovery hint instead of
  silently overwriting.

Tests pin the no-force invariant + the wrangler.jsonc-preservation
contract — see `tests/test_sys_publish_static_mirror.py`.

### 2. Connect Cloudflare Pages

- Cloudflare dashboard → Workers & Pages → Create application → Pages → Connect to Git
- Pick `plekto-dev/site` repo, branch `main`
- Build settings:
  - **Build command:** (leave blank)
  - **Build output directory:** `/`
  - **Root directory:** `/`
- Save and Deploy. First build runs in ~30 sec.

### 3. Set the custom domain

- In the new Pages project → Custom domains → Set up a custom domain → `plekto.dev`
- Cloudflare auto-creates the DNS records since `plekto.dev` is on the same account.

### 4. Create the KV namespace for signups

- Cloudflare dashboard → Workers & Pages → KV → Create namespace
- Name it `plekto-signups` (whatever you want — only the binding name matters)

### 5. Bind KV to the Pages project

- In the Pages project → Settings → Functions → KV namespace bindings → Add
- **Variable name:** `SIGNUPS` (this MUST match what `functions/signup.js` expects)
- **Namespace:** `plekto-signups` (the one you just created)
- Save. Next deploy will pick up the binding.

### 6. Test the signup flow

- Visit `https://plekto.dev/`
- Enter `test@example.com` in the form
- Should redirect to `/thanks/`
- Verify the entry appeared in KV: dashboard → KV → plekto-signups → search `email:test@example.com`

## Reading signups later

When v0.1 ships and you want to email everyone who signed up:

```bash
# Via wrangler CLI (one-time install: npm install -g wrangler)
wrangler kv:key list --namespace-id=<your-ns-id> --prefix=email: > signups.json

# Or via Cloudflare API
curl -H "Authorization: Bearer <your-api-token>" \
  "https://api.cloudflare.com/client/v4/accounts/<account-id>/storage/kv/namespaces/<ns-id>/keys?prefix=email:" \
  | jq -r '.result[].name' | sed 's/^email://'
```

## Failure modes the form handles

- Invalid email → redirect to `/?error=invalid` (inline message shown)
- KV binding missing → redirect to `/?error=server` (admin should see log)
- Duplicate email → silently OK (the function is idempotent; first signup wins)
- Bot / scraper → no captcha yet. If volume becomes a problem, add Cloudflare Turnstile (free, 1 line). Likely not needed until launch traffic spikes.

## Updating the site

Edit files here in `brand/plekto/site/`. Re-render previews:

```bash
python brand/plekto/_render_preview.py  # nothing — see scripts in repo
```

(The local Python preview server can be brought up with
`python -m http.server 8765 --directory brand/plekto/site` for live editing.)

When ready, copy to the `plekto-dev/site` clone and push.
