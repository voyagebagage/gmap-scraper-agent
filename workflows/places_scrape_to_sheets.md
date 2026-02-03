---
description: Scrape Google Maps directly using Playwright (No API cost) and export to Sheets.
---

### Prerequisites
1.  **Configure `.env`**:
    *   `GSHEET_ID`: Your Spreadsheet ID.
    *   `GSHEET_CREDS_PATH`: Path to your Service Account JSON.
    *   `DB_HOST`, `DB_NAME`, etc.: Postgres config (Optional).
2.  **Dependencies**:
    *   `playwright` must be installed (`pip install playwright`).
    *   Browsers installed (`playwright install chromium`).

### Execution

Run the scraper tool:
```bash
python3 tools/maps_scraper_to_sheets.py --query "{{search_term}}" --region "{{region_name}}" --max-results 20
```

### Options
- `--query`: Search term (e.g., "Coffee Shop", "Gym", "Plumber").
- `--region`: City or Area (e.g., "Paris", "Soho, NY").
- `--max-results`: Limit number of results (Default: 20).
- `--no-scrape`: Skip visiting websites for contact info (Faster).
- `--headless`: Run browser in background (Default: True).
- `--append`: Append to existing sheet instead of overwriting.
- `--rating`: Minimum rating filter (e.g., 4.5).
- `--only-no-website`: ONLY export results that have no website.
- `--only-has-socials`: ONLY export results that have social media presence.

### Example
```bash
# Scrape bakeries in Soho that have NO website
python3 tools/maps_scraper_to_sheets.py --query "Bakery" --region "Soho" --only-no-website

# Scrape gyms in London that have social media presence
python3 tools/maps_scraper_to_sheets.py --query "Gym" --region "London" --only-has-socials
```

> [!WARNING]
> This tool scrapes Google Maps directly. Large scale scraping may result in CAPTCHAs or temporary blocks. Use with moderation.
