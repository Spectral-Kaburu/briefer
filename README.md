# briefing

Search the web, extract pages, and return clean, summarized results — from the
terminal or on a schedule.

```bash
briefing -r "What is the latest breaking news in kenya"
```

Extraction is **local-first**: a self-hosted Firecrawl does the work, and the cloud
API is only spent when a page genuinely blocks the local scrape (see `SPEC.md`
section 4).

Status: under construction — see `PLAN.md` and `PROGRESS.md`.
