# Coming-soon page (sprouttoteco.com)

A single static page, `public/index.html`, served by a Cloudflare Worker until
the real site launches. No build step, no external scripts or fonts.

## Publish it (Cloudflare Workers, free)

Files: `wrangler.jsonc` (settings) and `public/` (what visitors get). Only
`public/` is published.

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Import a
   repository** → pick `jaronervin/moving_bins` → **Next**.
2. On *Create and deploy*:
   - **Project name:** `sprouttoteco` (must match `name` in `wrangler.jsonc`)
   - **Build command:** leave empty
   - **Deploy command:** `npx wrangler deploy` (the default)
   - **Advanced settings → Path:** `coming-soon`
3. **Deploy.** The config attaches `sprouttoteco.com` and
   `www.sprouttoteco.com` and creates their DNS records, so don't add those
   domains by hand.

Every push to the production branch redeploys automatically.

**Logo:** put the file in `public/` and replace the text inside
`<a class="brand">` in `public/index.html`.

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

**`www.sprouttoteco.com` → `sprouttoteco.com`:** after the first deploy, in
the sprouttoteco.com zone → Rules → **Redirect Rules** → create from the
template "Redirect from WWW to root", status 301, preserve query string.

## Check

```bash
curl -sI https://sprouttote.com/q/ABC | grep -i '^location'
```

should print `location: https://sprouttoteco.com/q/ABC`. Repeat for the
other domains and for `www.` versions.
