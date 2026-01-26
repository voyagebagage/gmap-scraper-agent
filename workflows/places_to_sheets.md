---
description: Search Google Maps and export results to Google Sheets in one command.
---

### Prerequisites
1.  **Configure `.env`**:
    *   `PLACES_API_KEY`: Your Google Maps Key.
    *   `GSHEET_ID`: Your Spreadsheet ID.
    *   `GSHEET_CREDS_PATH`: Path to your Service Account JSON.
2.  **Share the Sheet**:
    *   Open your Google Sheet.
    *   Share it with the **Email Address** found inside your Service Account JSON file (as an Editor).

### Execution

Run the consolidated tool with your search parameters:
```bash
python3 tools/maps_to_sheets.py --query "{{place_name}}" --region "{{region_name}}" --rating 4.4
```

### Input Parameters
- `place_name`: (Optional) The type of place (e.g., "bakery").
- `region_name`: The city or area (e.g., "Paris").
- `rating`: (Optional) Minimum rating filter (e.g., 4.4). Defaults to 4.0.
- `map_url`: (Optional) URL to center the search.
