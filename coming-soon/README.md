# Coming-soon page (sprouttoteco.com)

A single static page, `index.html`, served by Cloudflare Pages until the real
site launches. No build step, no external scripts or fonts.

**Adding the logo:** put the file in this folder (e.g. `logo.svg`) and replace
the text inside `<a class="brand">` with
`<img src="logo.svg" alt="Sprout Tote Co." height="40">`.

## Publish it (Cloudflare Pages, free)

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** →
   **Connect to Git** → pick `jaronervin/moving_bins`.
2. Production branch: the repo's default branch. Framework preset: **None**.
   Build command: *(leave empty)*. Build output directory: `coming-soon`.
3. **Save and Deploy**. You get a `*.pages.dev` address to check.
4. In the Pages project → **Custom domains** → add `sprouttoteco.com`, then
   `www.sprouttoteco.com`. Cloudflare creates the DNS records itself.

Every push to the production branch redeploys the page automatically.

## Send `www` and the other three domains to sprouttoteco.com

Each of these needs (a) a DNS record so traffic reaches Cloudflare, and (b) a
permanent redirect that keeps the path, so `sprouttote.com/q/ABC` lands on
`sprouttoteco.com/q/ABC`.

**(a) DNS — in each of sprouttotecompany.com, sprouttote.com, sprouttotes.com:**
DNS → Records → add two records, both **Proxied** (orange cloud):

| Type | Name | IPv6 address |
|---|---|---|
| AAAA | `@` | `100::` |
| AAAA | `www` | `100::` |

(`100::` is a placeholder address: the redirect answers before anything
would be fetched from it.)

**(b) Redirects — once, for all three:** Account home → **Bulk Redirects**
→ create a **list** with these three rows. Target for each is
`https://sprouttoteco.com`, status **301**. Under each row's options, tick
**Include subdomains**, **Subpath matching**, **Preserve path suffix** and
**Preserve query string**.

| Source URL |
|---|
| `sprouttotecompany.com` |
| `sprouttote.com` |
| `sprouttotes.com` |

Then create a **Bulk Redirect rule** that uses the list, and deploy it.

**`www.sprouttoteco.com` → `sprouttoteco.com`:** in the sprouttoteco.com
zone → Rules → **Redirect Rules** → create from the template
"Redirect from WWW to root", status 301, preserve query string.

## Check

```bash
curl -sI https://sprouttote.com/q/ABC | grep -i '^location'
```

should print `location: https://sprouttoteco.com/q/ABC`. Repeat for the
other domains and for `www.` versions.
