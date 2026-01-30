"""
Google Maps to Sheets - OPTIMIZED VERSION with Contact Scraping
Uses Text Search (New API) + Playwright for website scraping.

Cost: $32 per 1,000 requests (5,000 free/month)
Strategy: Use broad keywords to minimize API calls while maximizing coverage
"""

import argparse
import requests
import json
import os
import re
import gspread
import pandas as pd
import time
import sys
from urllib.parse import urljoin
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv
import psycopg2
from psycopg2 import extras

load_dotenv()

# ============ CONTACT SCRAPING ============

EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Phone number pattern (international format)
PHONE_PATTERN = re.compile(r'[\+]?[0-9]{1,3}[-.\s]?[0-9]{2,4}[-.\s]?[0-9]{3,4}[-.\s]?[0-9]{3,4}')

SOCIAL_PATTERNS = {
    'instagram': re.compile(r'(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_.]+)/?', re.IGNORECASE),
    'facebook': re.compile(r'(?:https?://)?(?:www\.)?facebook\.com/([a-zA-Z0-9.]+)/?', re.IGNORECASE),
    'twitter': re.compile(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]+)/?', re.IGNORECASE),
    'whatsapp': re.compile(r'(?:https?://)?(?:wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com)/([a-zA-Z0-9+]+)/?', re.IGNORECASE),
    'telegram': re.compile(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)/?', re.IGNORECASE),
    'messenger': re.compile(r'(?:https?://)?(?:m\.me|messenger\.com)/([a-zA-Z0-9.]+)/?', re.IGNORECASE),
    'line': re.compile(r'(?:https?://)?line\.me/(?:R/)?ti/p/([a-zA-Z0-9@~_-]+)/?', re.IGNORECASE),
}

# Additional patterns for chat widgets and embedded data
WHATSAPP_WIDGET_PATTERNS = [
    re.compile(r'wa\.me/(\d+)', re.IGNORECASE),
    re.compile(r'whatsapp["\s:]+["\']?(\+?[\d\s-]{10,})', re.IGNORECASE),
    re.compile(r'data-wa-number[="\s]+["\']?(\+?[\d\s-]{10,})', re.IGNORECASE),
    re.compile(r'whatsappNumber["\s:]+["\']?(\+?[\d\s-]{10,})', re.IGNORECASE),
]

MESSENGER_WIDGET_PATTERNS = [
    re.compile(r'm\.me/([a-zA-Z0-9.]+)', re.IGNORECASE),
    re.compile(r'data-page-id[="\s]+["\']?(\d+)', re.IGNORECASE),  # Facebook page ID
    re.compile(r'fb-messengermessageus[^>]*page_id[="\s]+["\']?(\d+)', re.IGNORECASE),
    re.compile(r'messenger_app_id["\s:]+["\']?(\d+)', re.IGNORECASE),
]

def extract_contacts_from_html(html: str) -> dict:
    """Extract contact information from HTML content, including chat widgets."""
    contacts = {
        'emails': [],
        'instagram': None,
        'facebook': None,
        'twitter': None,
        'whatsapp': None,
        'telegram': None,
        'messenger': None,
        'line': None,
    }
    
    # Extract emails
    emails = EMAIL_PATTERN.findall(html)
    filtered = [e for e in emails if not any(x in e.lower() for x in ['example.com', 'domain.com', 'wix', 'wordpress', 'sentry'])]
    contacts['emails'] = list(dict.fromkeys(filtered))[:3]
    
    # Extract social links from standard patterns
    for platform, pattern in SOCIAL_PATTERNS.items():
        matches = pattern.findall(html)
        if matches:
            handle = matches[0]
            if platform == 'instagram':
                contacts[platform] = f"https://instagram.com/{handle}"
            elif platform == 'facebook':
                contacts[platform] = f"https://facebook.com/{handle}"
            elif platform == 'twitter':
                contacts[platform] = f"https://x.com/{handle}"
            elif platform == 'whatsapp':
                contacts[platform] = f"https://wa.me/{handle}"
            elif platform == 'telegram':
                contacts[platform] = f"https://t.me/{handle}"
            elif platform == 'messenger':
                contacts[platform] = f"https://m.me/{handle}"
            elif platform == 'line':
                contacts[platform] = f"https://line.me/ti/p/{handle}"
    
    # Enhanced WhatsApp detection (chat widgets, data attributes)
    if not contacts['whatsapp']:
        for pattern in WHATSAPP_WIDGET_PATTERNS:
            matches = pattern.findall(html)
            if matches:
                # Clean up the phone number
                phone = re.sub(r'[\s-]', '', matches[0])
                if phone.startswith('+'):
                    phone = phone[1:]
                if len(phone) >= 9:  # Valid phone number length
                    contacts['whatsapp'] = f"https://wa.me/{phone}"
                    break
    
    # Enhanced Messenger detection (Facebook page ID, chat widgets)
    if not contacts['messenger']:
        for pattern in MESSENGER_WIDGET_PATTERNS:
            matches = pattern.findall(html)
            if matches:
                page_id = matches[0]
                # If it's a numeric page ID, use it directly
                if page_id.isdigit():
                    contacts['messenger'] = f"https://m.me/{page_id}"
                else:
                    contacts['messenger'] = f"https://m.me/{page_id}"
                break
    
    return contacts

def scrape_website(url: str, use_playwright: bool = True) -> dict:
    """Scrape a website for contact information."""
    if not url or not url.startswith('http'):
        url = 'https://' + (url or '')
    
    html = ""
    
    # Try Playwright first
    if use_playwright:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                try:
                    page.goto(url, timeout=15000, wait_until='domcontentloaded')
                    page.wait_for_timeout(2000)
                    html = page.content()
                except:
                    pass
                finally:
                    browser.close()
        except:
            pass
    
    # Fallback to requests
    if not html:
        try:
            import requests as req
            resp = req.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            html = resp.text
        except:
            pass
    
    if not html:
        return {}
    
    return extract_contacts_from_html(html)

def extract_social_from_url(url: str) -> dict:
    """Extract social platform info from a URL that is itself a social link."""
    if not url:
        return {}
    
    url_lower = url.lower()
    
    # Check each social pattern
    for platform, pattern in SOCIAL_PATTERNS.items():
        matches = pattern.findall(url)
        if matches:
            handle = matches[0]
            if platform == 'instagram':
                return {'instagram': f"https://instagram.com/{handle}"}
            elif platform == 'facebook':
                return {'facebook': f"https://facebook.com/{handle}"}
            elif platform == 'twitter':
                return {'twitter': f"https://x.com/{handle}"}
            elif platform == 'whatsapp':
                return {'whatsapp': f"https://wa.me/{handle}"}
            elif platform == 'telegram':
                return {'telegram': f"https://t.me/{handle}"}
            elif platform == 'messenger':
                return {'messenger': f"https://m.me/{handle}"}
            elif platform == 'line':
                return {'line': f"https://line.me/ti/p/{handle}"}
    
    return {}

def scrape_places_websites(places: list, use_playwright: bool = True) -> list:
    """Scrape websites for all places that have them."""
    print("\n📧 Scraping websites for contact info...")
    
    # Domains that are social/booking platforms
    platform_domains = ['facebook.com', 'instagram.com', 'twitter.com', 'x.com', 
                        'wa.me', 't.me', 'm.me', 'line.me',
                        'tripadvisor', 'booking.com', 'agoda.com']
    
    for i, place in enumerate(places):
        website = place.get('Website', '')
        is_platform_url = website and any(d in website.lower() for d in platform_domains)
        
        # Initialize empty
        place['Emails'] = ''
        place['Instagram'] = ''
        place['Facebook'] = ''
        place['WhatsApp'] = ''
        place['Telegram'] = ''
        place['Messenger'] = ''
        place['LINE'] = ''
        
        if website and website != 'not have website':
            if is_platform_url:
                # Extract social handle directly from the URL
                social_info = extract_social_from_url(website)
                for key, value in social_info.items():
                    place[key.capitalize() if key != 'line' else 'LINE'] = value
            else:
                # Scrape the website for contacts
                print(f"  [{i+1}/{len(places)}] {place.get('Name', 'Unknown')[:30]}...")
                contacts = scrape_website(website, use_playwright)
                place['Emails'] = ', '.join(contacts.get('emails', []))
                place['Instagram'] = contacts.get('instagram', '')
                place['Facebook'] = contacts.get('facebook', '')
                place['WhatsApp'] = contacts.get('whatsapp', '')
                place['Telegram'] = contacts.get('telegram', '')
                place['Messenger'] = contacts.get('messenger', '')
                place['LINE'] = contacts.get('line', '')
                time.sleep(1)  # Rate limiting
    
    return places

# ============ END CONTACT SCRAPING ============

def parse_args():
    parser = argparse.ArgumentParser(description="Search Google Maps and export to Google Sheets.")
    parser.add_argument("--query", help="Custom search query. If not set, uses optimized broad keywords.")
    parser.add_argument("--region", help="City or Area (e.g., 'Ko Pha-ngan')")
    parser.add_argument("--map_url", help="Google Maps URL (can be used instead of --region)")
    parser.add_argument("--radius", type=int, default=10, help="Search radius in km (default: 10, max: 50)")
    parser.add_argument("--rating", type=float, default=4.0, help="Minimum rating filter (default: 4.0)")
    parser.add_argument("--min_reviews", type=int, default=0, help="Minimum review count filter (default: 0)")
    parser.add_argument("--append", action="store_true", help="Append to existing sheet data")
    parser.add_argument("--no-scrape", action="store_true", help="Skip website scraping (faster, but no contact info)")
    parser.add_argument("--output", default=".tmp/places_results.json", help="Path to JSON results")
    return parser.parse_args()

def extract_coords(url):
    """Extract coordinates from a Google Maps URL."""
    if not url: 
        return None
    match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    return {"latitude": float(match.group(1)), "longitude": float(match.group(2))} if match else None

def geocode_region(api_key, region):
    """Get coordinates for a region name using Geocoding API."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": region, "key": api_key}
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        if data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return {"latitude": loc["lat"], "longitude": loc["lng"]}
    return None

def search_text(api_key, query, location, radius_km):
    """
    Search using Text Search (New API).
    POST to https://places.googleapis.com/v1/places:searchText
    
    Handles pagination to get all results.
    """
    url = "https://places.googleapis.com/v1/places:searchText"
    
    # Field mask - request only what we need to minimize costs
    field_mask = ",".join([
        "places.id", "places.displayName", "places.formattedAddress", "places.types",
        "places.rating", "places.userRatingCount", "places.websiteUri",
        "places.internationalPhoneNumber", "places.googleMapsUri"
    ])
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask
    }
    
    radius_meters = min(radius_km * 1000, 50000)
    
    body = {
        "textQuery": query,
        "locationBias": {
            "circle": {
                "center": location,
                "radius": radius_meters
            }
        },
        "maxResultCount": 20
    }
    
    all_places = []
    page = 1
    
    while True:
        response = requests.post(url, headers=headers, json=body)
        
        if response.status_code != 200:
            print(f"      Error: {response.status_code}")
            break
            
        data = response.json()
        places = data.get("places", [])
        
        if not places:
            break
        
        all_places.extend(places)
        
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
            
        body["pageToken"] = next_page_token
        page += 1
        time.sleep(0.3)
    
    return all_places

def search_all(api_key, location, radius_km, region_name, custom_query=None):
    """
    Execute optimized search strategy.
    
    If custom_query provided: single search
    Otherwise: use broad keywords that cover maximum business types with minimum API calls
    """
    
    all_places = []
    seen_ids = set()
    
    if custom_query:
        # Single custom search
        queries = [f"{custom_query} in {region_name}"]
    else:
        # OPTIMIZED BROAD KEYWORDS
        # Strategy: Use inclusive terms that capture multiple categories
        # This minimizes API calls while maximizing coverage
        queries = [
            # Food & Drink (covers: restaurant, cafe, bar, bakery, etc.)
            f"food restaurant in {region_name}",
            f"cafe coffee in {region_name}",
            f"bar pub in {region_name}",
            
            # Accommodation (covers: hotel, resort, hostel, villa, etc.)
            f"hotel resort hostel lodging bungalows in {region_name}",
            f"point_of_interest, establishment in {region_name}",
            f"doctor clinic pharmacy in {region_name}",
            
            # Wellness (covers: spa, gym, yoga, wellness center)
            f"spa wellness in {region_name}",
            f"gym fitness in {region_name}",
            
            # Shopping & Services (broad terms)
            f"shopping store  in {region_name}",
            
            # Entertainment
            f"nightclub event venue in {region_name}",
        ]
    
    print(f"  Running {len(queries)} optimized searches...")
    
    for query in queries:
        results = search_text(api_key, query, location, radius_km)
        new_count = 0
        
        for place in results:
            place_id = place.get("id")
            if place_id not in seen_ids:
                seen_ids.add(place_id)
                all_places.append(place)
                new_count += 1
        
        print(f"    '{query}': {len(results)} found, +{new_count} new")
    
    print(f"  Total unique places: {len(all_places)}")
    return all_places

def process_places(places, region, min_rating, min_reviews):
    """Process and filter places."""
    results = []
    
    # Domains that are social/booking platforms, not standalone websites
    platform_domains = [
        "facebook.com", "instagram.com", "twitter.com", "x.com", 
        "foodpanda", "grab.com", "line.me", 
        "tripadvisor.com", "booking.com", "agoda.com"
    ]
    
    for place in places:
        rating = place.get("rating", 0)
        review_count = place.get("userRatingCount", 0)
        
        if rating < min_rating or review_count < min_reviews:
            continue
        
        display_name = place.get("displayName", {})
        name = display_name.get("text", "Unknown")
        
        types = place.get("types", [])
        category_str = ", ".join(types) if types else "unknown"
        
        website_url = place.get("websiteUri")
        is_platform = website_url and any(d in website_url.lower() for d in platform_domains)
        has_standalone_site = bool(website_url) and not is_platform
        website_display = website_url if website_url else "not have website"
        
        phone = place.get("internationalPhoneNumber", "")
        maps_url = place.get("googleMapsUri", "")
        
        results.append({
            "Location": region,
            "Name": name,
            "Rating": rating,
            "Review Count": review_count,
            "Phone": phone,
            "Address": maps_url,
            "Website": website_display,
            "Category": category_str,
            "_has_website": has_standalone_site,
            "_sheet_category": "with websites" if has_standalone_site else "without websites"  # Will be updated after scraping
        })
    
    return results

def categorize_after_scraping(places):
    """Update sheet categories based on scraped contact info."""
    for place in places:
        has_website = place.get('_has_website', False)
        has_socials = any(place.get(s) for s in ['Instagram', 'Facebook', 'WhatsApp', 'Telegram', 'Messenger', 'LINE'])
        
        if has_website:
            place['_sheet_category'] = 'with websites'
        elif has_socials:
            place['_sheet_category'] = 'with socials'
        else:
            place['_sheet_category'] = 'without websites'
    
    return places

def update_sheets(data, sheet_id, creds_path, append_mode=False):
    """Upload data to Google Sheets with 3 categories."""
    if not data:
        print("No data to export.")
        return

    df = pd.DataFrame(data)
    df = df.fillna("")
    df = df.replace([float("inf"), float("-inf")], 0)
    
    # Drop internal columns from export
    internal_cols = ['_sheet_category', '_has_website']
    export_cols = [c for c in df.columns if c not in internal_cols]

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id)
    
    # 3 sheet categories
    categories = ["with websites", "with socials", "without websites"]
    
    for cat in categories:
        sub_df = df[df["_sheet_category"] == cat].copy()
        export_df = sub_df[export_cols]
        
        try:
            ws = sheet.worksheet(cat)
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet.add_worksheet(title=cat, rows="1000", cols="25")
        
        header = export_df.columns.tolist()
        data_rows = export_df.values.tolist()
        
        if append_mode:
            existing = ws.get_all_values()
            if len(existing) == 0:
                ws.update(range_name="A1", values=[header] + data_rows, value_input_option="RAW")
            else:
                next_row = len(existing) + 1
                if data_rows:
                    ws.update(range_name=f"A{next_row}", values=data_rows, value_input_option="RAW")
            print(f"  📄 '{cat}': appended {len(export_df)} places")
        else:
            ws.clear()
            ws.update(range_name="A1", values=[header] + data_rows, value_input_option="RAW")
            print(f"  📄 '{cat}': {len(export_df)} places")

def update_postgres(data):
    """Upload data to PostgreSQL database."""
    db_host = os.getenv("DB_HOST")
    db_name = os.getenv("DB_NAME")
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")
    db_port = os.getenv("DB_PORT", "5432")

    if not all([db_host, db_name, db_user, db_pass]):
        print("⚠️ Skipping PostgreSQL: Missing configuration in .env")
        return

    try:
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_pass,
            port=db_port
        )
        cur = conn.cursor()

        # Create table if not exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS places (
                id SERIAL PRIMARY KEY,
                location TEXT,
                name TEXT,
                rating FLOAT,
                review_count INTEGER,
                phone TEXT,
                address TEXT,
                website TEXT,
                category TEXT,
                has_website BOOLEAN,
                sheet_category TEXT,
                emails TEXT,
                instagram TEXT,
                facebook TEXT,
                whatsapp TEXT,
                telegram TEXT,
                messenger TEXT,
                line TEXT,
                contact_status INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, address)
            );
        """)

        # Add indexes as requested
        cur.execute("CREATE INDEX IF NOT EXISTS idx_places_location ON places (location);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_places_website ON places (website);")

        # Upsert data
        upsert_query = """
            INSERT INTO places (
                location, name, rating, review_count, phone, address, website, category,
                has_website, sheet_category, emails, instagram, facebook, whatsapp, telegram, messenger, line
            ) VALUES %s
            ON CONFLICT (name, address) DO UPDATE SET
                location = EXCLUDED.location,
                rating = EXCLUDED.rating,
                review_count = EXCLUDED.review_count,
                phone = EXCLUDED.phone,
                website = EXCLUDED.website,
                category = EXCLUDED.category,
                has_website = EXCLUDED.has_website,
                sheet_category = EXCLUDED.sheet_category,
                emails = EXCLUDED.emails,
                instagram = EXCLUDED.instagram,
                facebook = EXCLUDED.facebook,
                whatsapp = EXCLUDED.whatsapp,
                telegram = EXCLUDED.telegram,
                messenger = EXCLUDED.messenger,
                line = EXCLUDED.line,
                updated_at = CURRENT_TIMESTAMP;
        """

        values = []
        for p in data:
            values.append((
                p.get("Location", ""),
                p.get("Name", ""),
                p.get("Rating", 0),
                p.get("Review Count", 0),
                p.get("Phone", ""),
                p.get("Address", ""),
                p.get("Website", ""),
                p.get("Category", ""),
                p.get("_has_website", False),
                p.get("_sheet_category", ""),
                p.get("Emails", ""),
                p.get("Instagram", ""),
                p.get("Facebook", ""),
                p.get("WhatsApp", ""),
                p.get("Telegram", ""),
                p.get("Messenger", ""),
                p.get("LINE", "")
            ))

        if values:
            extras.execute_values(cur, upsert_query, values)
            conn.commit()
            print(f"  🗄️ PostgreSQL: Updated {len(values)} records")

        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ PostgreSQL Error: {e}")

def main():
    args = parse_args()
    
    api_key = os.getenv("PLACES_API_KEY")
    sheet_id = os.getenv("GSHEET_ID")
    creds_path = os.getenv("GSHEET_CREDS_PATH")

    if not all([api_key, sheet_id, creds_path]):
        print("Error: Missing configuration (check .env)")
        return
    
    if not args.region and not args.map_url:
        print("Error: Provide --region or --map_url")
        return
    
    # Get coordinates and region name
    location = extract_coords(args.map_url)
    region = args.region
    
    if not location:
        if region:
            print(f"Geocoding: {region}...")
            location = geocode_region(api_key, region)
        else:
            match = re.search(r'/place/([^/]+)/', args.map_url)
            if match:
                region = match.group(1).replace('+', ' ')
                location = geocode_region(api_key, region)
    
    if not location:
        print(f"Error: Could not determine coordinates")
        return
    
    if not region:
        region = f"Lat:{location['latitude']:.4f},Lng:{location['longitude']:.4f}"

    query_display = args.query if args.query else "optimized broad search"
    
    print(f"\n{'='*60}")
    print(f"OPTIMIZED SEARCH (Text Search only - same cost, better coverage)")
    print(f"Region: {region}")
    print(f"Radius: {args.radius}km | Rating >= {args.rating} | Reviews >= {args.min_reviews}")
    print(f"Mode: {'Append' if args.append else 'Replace'}")
    print(f"{'='*60}\n")
    
    # Run optimized search
    raw_places = search_all(api_key, location, args.radius, region, args.query)
    
    print(f"\nFiltering {len(raw_places)} raw results...")
    results = process_places(raw_places, region, args.rating, args.min_reviews)
    print(f"After filtering: {len(results)} places meet criteria.")
    
    # Scrape websites for contact info (only those with websites)
    results = scrape_places_websites(results, use_playwright=not args.no_scrape)
    
    # Re-categorize based on scraped contacts
    results = categorize_after_scraping(results)
    
    # Count categories
    with_websites = sum(1 for p in results if p.get('_sheet_category') == 'with websites')
    with_socials = sum(1 for p in results if p.get('_sheet_category') == 'with socials')
    without = sum(1 for p in results if p.get('_sheet_category') == 'without websites')
    print(f"\n📊 Categories: {with_websites} with websites | {with_socials} with socials | {without} without")
    
    # Save JSON
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Saved to {args.output}")
    
    # Upload to Sheets
    print("\nUploading to Google Sheets...")
    update_sheets(results, sheet_id, creds_path, append_mode=args.append)

    # Upload to PostgreSQL
    print("\nSyncing with PostgreSQL...")
    update_postgres(results)

    print("\n✅ Done!")
    print(f"API calls used: ~{len([q for q in ['restaurant', 'cafe', 'bar', 'hotel', 'spa', 'gym', 'shop', 'nightclub'] if not args.query]) or 1} Text Searches")

if __name__ == "__main__":
    main()
