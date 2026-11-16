# Wildlife Footage Source Priority (2026-11-14)

This order is for Naminal Hub's current, licensed, visually usable library,
not a claim about the size of the entire internet. The SQLite catalog and
individual item rights evidence govern production. No footage is eligible
merely because a site labels it free or public domain.

| Priority | Source | Current evidence | Operating rule |
| --- | --- | --- | --- |
| 1 | Wikimedia Commons | 20 approved real-world clips, ~448 seconds across 17 species. Some focused 4K sea-otter footage; other searches are sparse, soft or CC BY-SA (not supported). | Search exact species / behavior and filenames in small batches. Inspect license URL, creator, original frames and attribution per item; stop after 429 rather than retrying aggressively. |
| 2 | Pixabay | 8 approved clips, ~159 seconds; 4K guinea pigs and focused hamster footage. 17 cataloged, but many are visually weak, ambiguous or synthetic-looking. | Use one species/behavior query at a time, then filter titles and inspect contact sheets. Broad multi-word queries returned unrelated clips. Search results are cached locally for 24 hours, as the API requires. |
| 3 | NOAA Fisheries B-roll | 2 approved packages, ~479 raw seconds. Clear public-domain B-roll path, particularly valuable for marine species. | Use B-roll only, trim opening slates and unfocused sections. Read each package's slate for contributor and permit credits; do not extract narrative-program footage. For marine topics, search NOAA before Pixabay. |
| 4 (investigate) | NPS Gallery | No item imported yet. Asset pages expose original 1080p files and per-item copyright/constraints fields. | Test one rights-clear animal video page and its authorized download before calling this an automated source. "Public domain" on a page is not sufficient when third-party copyright is also listed. |
| 5 (metadata only) | Coverr | Key works; a targeted fox search returned one AI cartoon and no suitable real footage. Demo API quota: 50 requests/hour. Import is not implemented. | The search adapter now omits AI, premium and unclassified results. Do not present generated wildlife as documentary footage; verify stock vs sponsored terms before implementing downloads. |
| 6 (credential pending) | Pexels | Adapter exists; key issuance remains paused. Documentation now prefers the `/v1/videos/` API and describes 200 requests/hour and 20,000/month by default. | When credentials return, search for real vertical clips, credit contributors where possible and confirm each license. No production assets are currently sourced here. |
| 7 (manual only) | USFWS | NCTC B-roll pages use VBrick, which the existing extractor cannot fetch anonymously. | Only use an individual permitted download plus its item-specific evidence and requested credit; do not bypass access controls. |
| 8 (blocked) | Vecteezy Free | One previously approved 8-second clip in SQLite, plus two unapproved clips. Free production use is limited to projects with budgets up to $1,000 and requires attribution. | The rights gate now blocks free-tier assets until the intended production budget and item terms are explicitly cleared. API access is not proof of commercial eligibility. |
| 9 (manual only) | Mixkit | No official API integration or cataloged usable asset. | Do not scrape; evaluate an individual clip only under its current license. |
| 10 (niche) | Zenodo | Adapter live, keyless API. Species queries return mostly specimen/publication records; behavior-dataset searches surface real video (e.g. a CC-BY horned-beetle fight clip, imported unapproved as a plumbing proof). | Search by behavior/dataset topic, not common species names. Adapter refuses any item whose metadata license is not CC0, CC BY or public domain — NC, ND, SA and unspecified never reach the catalog. |
| 11 (niche) | Figshare | Adapter live, keyless API. Search returns articles without file filtering; most supplements are data tables, so many candidates fail import with "no video file". | Expect low hit rate; worth trying when a specific species/behavior study exists. Same per-item license gate as Zenodo. |
| 12 (noisy) | Internet Archive | Adapter live, keyless API. 464 movies matched "pika" but mostly fan uploads and mis-licensed reuploads. Item `licenseurl` is uploader-asserted — a PD mark can appear on clearly copyrighted material. | Only items declaring a cleared licenseurl import, but treat that metadata as a starting claim, not proof: operator must verify the item's actual provenance before approval. Useful mainly for old government/educational films with real rights histories. |
| 13 (manual only) | Vimeo CC / OpenBehavior | No adapter. Vimeo requires an API token and per-video download permission; OpenBehavior is a small curated list best handled item-by-item (most datasets mirror to Zenodo anyway). | Evaluate individual items only; do not scrape. |

Sources: [Commons video rights](https://commons.wikimedia.org/wiki/Commons:Video),
[Pixabay license](https://pixabay.com/service/license-summary/),
[Pixabay API limits/cache rule](https://pixabay.com/api/docs/),
[NOAA Fisheries gallery policy](https://videos.fisheries.noaa.gov/detail/videos),
[NPS Gallery](https://npgallery.nps.gov/),
[Coverr license](https://coverr.co/license),
[Coverr API quotas](https://api.coverr.co/docs/start/),
[Pexels API](https://www.pexels.com/api/),
[Vecteezy free-license limits](https://www.vecteezy.com/licensing-agreement).

## Visual Selection

- Read the approved assets already in SQLite before any external search. Search
  only the missing species, angle or behavior; batch a few distinct queries,
  cache permitted API metadata, and stop on 429.
- Inspect source time windows at several points. Prefer sharp eyes/face and
  recognizable behavior; exclude soft tails, slates, tiny distant subjects,
  wrong-species tags and apparently AI-generated video even if licensed.
- For new plans, target 15-35% of story time as sharp full-frame 9:16 cover
  crops. Check the animal stays visible and focused after crop; use `zoom=1`,
  `full_frame_focus_ok=true`, and optionally `thumbnail_candidate=true` and
  `thumbnail_at_s` for verified source windows. Otherwise use a pull-back near
  `zoom=0.8` with blurred fill and explain why a focused cover crop is unavailable.
  This is an editorial hypothesis, not an automated focus detector.
- Exported `thumbnail.jpg` is always a full-bleed 1080x1920 source frame,
  never a blurred letterbox or the brand end card. It prefers a verified
  full-frame shot and records its source in `package.json`. When none exists,
  it cover-crops a frame from the highest-resolution candidate in the plan.
  Resolution does not prove focus or subject placement: use `thumbnail_at_s`
  after inspecting the clip, and visually approve the JPEG before uploading.

## Evidence, Not Causation

The November 14 YouTube snapshot has approximately 1,200 views each on the
fully cropped monk seal, mixed-crop turtle and pull-back hamster; the
multi-angle sea otter has ~1,165, and the pull-back quokka ~783. The older
quokka shows average view duration above its video runtime (loops/replays);
sea-otter and squirrel completion is lower. Those examples differ in species,
hook, length, age and distribution. The Analytics API has not supplied
retention for the newest wave; impressions and CTR remain unavailable, and
TikTok/Instagram are not synced. Treat mixed framing plus a sharp full-bleed
cover as a **testable preference**, not a proven performance driver. Compare
similar subjects at matched age once enough posts have Analytics data.
