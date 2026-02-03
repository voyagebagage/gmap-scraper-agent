"""
Google Maps Scraper to Sheets - PURE SCRAPE VERSION
Uses Playwright to scrape Google Maps directly (No API) + Website Contact Scraping.

Strategy:
1. Search Google Maps via Playwright
2. Scroll through results
3. Click each result to extract details (Website, Phone, etc.)
4. Visit Websites to scrape contacts (Emails, Socials)
5. Export to Sheets & Postgres
"""

import argparse
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
from playwright.sync_api import sync_playwright

load_dotenv()

# ============ CONTACT SCRAPING (Copied from maps_to_sheets.py) ============

EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
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

# Additional patterns for chat widgets
WHATSAPP_WIDGET_PATTERNS = [
    re.compile(r'wa\.me/(\d+)', re.IGNORECASE),
    re.compile(r'whatsapp["\s:]+["\']?(\+?[\d\s-]{10,})', re.IGNORECASE),
    re.compile(r'data-wa-number[="\s]+["\']?(\+?[\d\s-]{10,})', re.IGNORECASE),
    re.compile(r'whatsappNumber["\s:]+["\']?(\+?[\d\s-]{10,})', re.IGNORECASE),
]

MESSENGER_WIDGET_PATTERNS = [
    re.compile(r'm\.me/([a-zA-Z0-9.]+)', re.IGNORECASE),
    re.compile(r'data-page-id[="\s]+["\']?(\d+)', re.IGNORECASE),
    re.compile(r'fb-messengermessageus[^>]*page_id[="\s]+["\']?(\d+)', re.IGNORECASE),
    re.compile(r'messenger_app_id["\s:]+["\']?(\d+)', re.IGNORECASE),
]

def extract_contacts_from_html(html: str) -> dict:
    """Extract contact information from HTML content."""
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
    
    # Extract social links
    for platform, pattern in SOCIAL_PATTERNS.items():
        matches = pattern.findall(html)
        if matches:
            handle = matches[0]
            if platform == 'instagram': contacts[platform] = f"https://instagram.com/{handle}"
            elif platform == 'facebook': contacts[platform] = f"https://facebook.com/{handle}"
            elif platform == 'twitter': contacts[platform] = f"https://x.com/{handle}"
            elif platform == 'whatsapp': contacts[platform] = f"https://wa.me/{handle}"
            elif platform == 'telegram': contacts[platform] = f"https://t.me/{handle}"
            elif platform == 'messenger': contacts[platform] = f"https://m.me/{handle}"
            elif platform == 'line': contacts[platform] = f"https://line.me/ti/p/{handle}"
    
    # Enhanced WhatsApp
    if not contacts['whatsapp']:
        for pattern in WHATSAPP_WIDGET_PATTERNS:
            matches = pattern.findall(html)
            if matches:
                phone = re.sub(r'[\s-]', '', matches[0])
                if phone.startswith('+'): phone = phone[1:]
                if len(phone) >= 9:
                    contacts['whatsapp'] = f"https://wa.me/{phone}"
                    break
    
    # Enhanced Messenger
    if not contacts['messenger']:
        for pattern in MESSENGER_WIDGET_PATTERNS:
            matches = pattern.findall(html)
            if matches:
                page_id = matches[0]
                contacts['messenger'] = f"https://m.me/{page_id}"
                break
    
    return contacts

def scrape_website(url: str, use_playwright: bool = True) -> dict:
    """Scrape a website for contact information."""
    if not url or not url.startswith('http'):
        url = 'https://' + (url or '')
    
    html = ""
    if use_playwright:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                try:
                    page.goto(url, timeout=15000, wait_until='domcontentloaded')
                    page.wait_for_timeout(2000)
                    html = page.content()
                except: pass
                finally: browser.close()
        except: pass
    
    if not html:
        try:
            import requests as req
            resp = req.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            html = resp.text
        except: pass
    
    if not html: return {}
    return extract_contacts_from_html(html)

def extract_social_from_url(url: str) -> dict:
    """Extract social platform info from a URL that is itself a social link."""
    if not url: return {}
    # Reuse logic from maps_to_sheets if needed, or simple check
    for platform, pattern in SOCIAL_PATTERNS.items():
        matches = pattern.findall(url)
        if matches:
            handle = matches[0]
            # Reconstruct URL simply
            base = {
                'instagram': 'instagram.com', 'facebook': 'facebook.com', 'twitter': 'x.com',
                'whatsapp': 'wa.me', 'telegram': 't.me', 'messenger': 'm.me', 'line': 'line.me/ti/p'
            }
            if platform in base:
                return {platform: f"https://{base[platform]}/{handle}"}
    return {}

def scrape_places_websites(places: list, use_playwright: bool = True) -> list:
    """Scrape websites for all places that have them."""
    print("\n📧 Scraping websites for contact info...")
    platform_domains = ['facebook.com', 'instagram.com', 'twitter.com', 'x.com', 
                        'wa.me', 't.me', 'm.me', 'line.me',
                        'tripadvisor', 'booking.com', 'agoda.com']
    
    for i, place in enumerate(places):
        website = place.get('Website', '')
        is_platform_url = website and any(d in website.lower() for d in platform_domains)
        
        place['Emails'] = ''
        place['Instagram'] = ''
        place['Facebook'] = ''
        place['WhatsApp'] = ''
        place['Telegram'] = ''
        place['Messenger'] = ''
        place['LINE'] = ''
        
        if website and website != 'not have website':
            if is_platform_url:
                social_info = extract_social_from_url(website)
                for key, value in social_info.items():
                    place[key.capitalize() if key != 'line' else 'LINE'] = value
            else:
                print(f"  [{i+1}/{len(places)}] {place.get('Name', 'Unknown')[:30]}...")
                contacts = scrape_website(website, use_playwright)
                place['Emails'] = ', '.join(contacts.get('emails', []))
                place['Instagram'] = contacts.get('instagram', '')
                place['Facebook'] = contacts.get('facebook', '')
                place['WhatsApp'] = contacts.get('whatsapp', '')
                place['Telegram'] = contacts.get('telegram', '')
                place['Messenger'] = contacts.get('messenger', '')
                place['LINE'] = contacts.get('line', '')
                time.sleep(1)
    
    return places

# ============ GOOGLE MAPS DIRECT SCRAPING ============

def scrape_google_maps(query: str, region: str, max_results: int = 20, headless: bool = True):
    """
    Scrape Google Maps results using Playwright.
    """
    search_term = f"{query} near {region}"
    print(f"🔍 Scraping Google Maps for: {search_term}")
    
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US"
        )
        page = context.new_page()
        
        try:
            # Go to Google Maps
            page.goto("https://www.google.com/maps?hl=en", timeout=60000)
            
            # Handle Consent Screen (if any)
            try:
                # Look for typical consent buttons
                consent_btn = page.locator('button[aria-label="Accept all"], button:has-text("Accept all")').first
                if consent_btn.is_visible(timeout=5000):
                    print("  Dismissing consent dialog...")
                    consent_btn.click()
                    page.wait_for_timeout(2000)
            except: pass

            # Search Box
            # Try multiple selectors
            search_input = page.locator("input#searchboxinput, input[name='q']").first
            search_input.wait_for(state="visible", timeout=30000)
            search_input.fill(search_term)
            page.keyboard.press("Enter")
            
            # Wait for results to load
            print("  Waiting for results...")
            # Wait for the feed or the "No results" message
            try:
                page.wait_for_selector('div[role="feed"], div[role="main"]', timeout=30000)
            except:
                print("  Timeout waiting for results feed.")
                # Snapshot for debug
                # page.screenshot(path=".tmp/debug_error.png")
            
            page.wait_for_timeout(3000)
            
            # Scroll feed to load items
            feed = page.locator('div[role="feed"]').first
            
            if not feed.is_visible():
                print("  Feed not found (maybe single result or empty).")
                # Handle single result case if needed, or retry
                return []
            
            # Initial scroll to load some items
            print("  Scrolling feed...")
            for _ in range(5):
                feed.evaluate("element => element.scrollBy(0, 1000)")
                page.wait_for_timeout(1000)
            
            # Find all result links
            # Strategy: Get all links in the feed, filter unique ones
            # A result usually has a link like /maps/place/...
            links = feed.locator('a[href*="/maps/place/"]').all()
            unique_links = []
            seen_hrefs = set()
            
            for link in links:
                href = link.get_attribute('href')
                if href and '/maps/place/' in href and href not in seen_hrefs:
                    seen_hrefs.add(href)
                    unique_links.append(link)
            
            print(f"  Found {len(unique_links)} potential results. Processing max {max_results}...")
            
            count = 0
            for i, link_locator in enumerate(unique_links):
                if count >= max_results:
                    break
                
                try:
                    # Get name from the link aria-label first (more reliable than H1 after click)
                    name = link_locator.get_attribute("aria-label")
                    if not name:
                         name = "Unknown"

                    # Scroll to element to ensure visibility
                    try:
                        link_locator.scroll_into_view_if_needed(timeout=2000)
                    except: pass
                    
                    # Force click if needed or just click
                    link_locator.click(timeout=5000)
                    
                    # Wait for details panel to load
                    # We can check if H1 matches the name we expect, or just wait for *any* H1 that isn't empty
                    # or wait for buttons that appear in detail view
                    try:
                        page.wait_for_selector('div[role="main"]', timeout=3000) # Detail view usually has role="main"
                        time.sleep(1.5) 
                        
                        # Try to get H1 as confirmation, or updated name
                        h1_text = page.locator("h1").first.inner_text()
                        if h1_text and len(h1_text) > 1 and "Results" not in h1_text:
                            name = h1_text
                    except:
                        pass
                    
                    # Address - Button with data-item-id="address" or aria-label containing "Address"
                    # Generally text inside buttons with specific icons
                    address = ""
                    try:
                        address_btn = page.locator('button[data-item-id="address"]').first
                        if address_btn.is_visible():
                            address = address_btn.get_attribute("aria-label").replace("Address: ", "")
                    except: pass
                    
                    # Phone
                    phone = ""
                    try:
                        phone_btn = page.locator('button[data-item-id^="phone"]').first
                        if phone_btn.is_visible():
                            phone = phone_btn.get_attribute("aria-label").replace("Phone: ", "")
                    except: pass
                    
                    # Website
                    website = ""
                    try:
                        website_btn = page.locator('a[data-item-id="authority"]').first
                        if website_btn.is_visible():
                            website = website_btn.get_attribute("href")
                    except: pass
                    
                    # Rating & Reviews
                    rating = 0.0
                    review_count = 0
                    try:
                        # Find the span with rating (e.g. "4.5 stars")
                        # Usually adjacent to h1 or in the first section
                        rating_span = page.locator('span[aria-label*="stars"]').first
                        if rating_span.is_visible():
                            rating_text = rating_span.get_attribute("aria-label")
                            match = re.search(r'(\d+(\.\d+)?) stars', rating_text)
                            if match:
                                rating = float(match.group(1))
                            
                            # Reviews usually next to it "(100)"
                            reviews_text = rating_span.locator("xpath=..").inner_text()
                            idx = reviews_text.find('(')
                            if idx != -1:
                                review_part = reviews_text[idx+1:].split(')')[0]
                                review_count = int(review_part.replace(',', '').replace('.', ''))
                    except: pass
                    
                    # Category
                    category = ""
                    try:
                        # Usually a button under the title
                        cat_btn = page.locator('button[jsaction*="category"]').first
                        if cat_btn.is_visible():
                            category = cat_btn.inner_text()
                    except: pass

                    print(f"    [{count+1}] {name} ({rating}★, {review_count} revs)")
                    
                    # Filter
                    results.append({
                        "Location": region,
                        "Name": name,
                        "Rating": rating,
                        "Review Count": review_count,
                        "Phone": phone,
                        "Address": address,
                        "Website": website if website else "not have website",
                        "Category": category,
                        "_has_website": bool(website),
                        "_sheet_category": "with websites" if website else "without websites"
                    })
                    count += 1
                    
                except Exception as e:
                    print(f"    Error processing item {i}: {e}")
                    continue
                
        except Exception as e:
            print(f"  Scraping Error: {e}")
        finally:
            browser.close()
            
    return results

# ============ DATA EXPORT ============

def categorize_after_scraping(places):
    """Update sheet categories based on scraped contact info."""
    for place in places:
        has_website = place.get('_has_website', False)
        has_socials = any(place.get(s) for s in ['Instagram', 'Facebook', 'WhatsApp', 'Telegram', 'Messenger', 'LINE'])
        
        if has_website: place['_sheet_category'] = 'with websites'
        elif has_socials: place['_sheet_category'] = 'with socials'
        else: place['_sheet_category'] = 'without websites'
    return places

def update_sheets(data, sheet_id, creds_path, append_mode=False):
    """Upload data to Google Sheets."""
    if not data:
        print("No data to export.")
        return

    df = pd.DataFrame(data)
    df = df.fillna("")
    
    internal_cols = ['_sheet_category', '_has_website']
    export_cols = [c for c in df.columns if c not in internal_cols]

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id)
    
    categories = ["with websites", "with socials", "without websites"]
    
    for cat in categories:
        sub_df = df[df["_sheet_category"] == cat].copy()
        export_df = sub_df[export_cols]
        
        try: ws = sheet.worksheet(cat)
        except: ws = sheet.add_worksheet(title=cat, rows="1000", cols="25")
        
        header = export_df.columns.tolist()
        data_rows = export_df.values.tolist()
        
        if append_mode:
            existing = ws.get_all_values()
            if len(existing) == 0:
                ws.update(range_name="A1", values=[header] + data_rows, value_input_option="RAW")
            else:
                next_row = len(existing) + 1
                if data_rows: ws.update(range_name=f"A{next_row}", values=data_rows, value_input_option="RAW")
        else:
            ws.clear()
            ws.update(range_name="A1", values=[header] + data_rows, value_input_option="RAW")
        print(f"  📄 '{cat}': {len(export_df)} places")

def update_postgres(data):
    """Upload data to PostgreSQL database."""
    db_host = os.getenv("DB_HOST"); db_name = os.getenv("DB_NAME")
    db_user = os.getenv("DB_USER"); db_pass = os.getenv("DB_PASSWORD")
    if not all([db_host, db_name, db_user, db_pass]): return

    try:
        conn = psycopg2.connect(host=db_host, database=db_name, user=db_user, password=db_pass)
        cur = conn.cursor()
        
        cur.execute("CREATE TABLE IF NOT EXISTS places (id SERIAL PRIMARY KEY, location TEXT, name TEXT, rating FLOAT, review_count INTEGER, phone TEXT, address TEXT, website TEXT, category TEXT, has_website BOOLEAN, sheet_category TEXT, emails TEXT, instagram TEXT, facebook TEXT, whatsapp TEXT, telegram TEXT, messenger TEXT, line TEXT, contact_status INTEGER DEFAULT 0, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(name, address));")
        
        upsert_query = """
            INSERT INTO places (location, name, rating, review_count, phone, address, website, category, has_website, sheet_category, emails, instagram, facebook, whatsapp, telegram, messenger, line)
            VALUES %s ON CONFLICT (name, address) DO UPDATE SET
            location = EXCLUDED.location, rating = EXCLUDED.rating, review_count = EXCLUDED.review_count,
            phone = EXCLUDED.phone, website = EXCLUDED.website, category = EXCLUDED.category,
            has_website = EXCLUDED.has_website, sheet_category = EXCLUDED.sheet_category,
            emails = EXCLUDED.emails, instagram = EXCLUDED.instagram, facebook = EXCLUDED.facebook,
            whatsapp = EXCLUDED.whatsapp, telegram = EXCLUDED.telegram, messenger = EXCLUDED.messenger,
            line = EXCLUDED.line, updated_at = CURRENT_TIMESTAMP;
        """
        
        values = []
        for p in data:
            values.append((
                p.get("Location", ""), p.get("Name", ""), p.get("Rating", 0), p.get("Review Count", 0),
                p.get("Phone", ""), p.get("Address", ""), p.get("Website", ""), p.get("Category", ""),
                p.get("_has_website", False), p.get("_sheet_category", ""),
                p.get("Emails", ""), p.get("Instagram", ""), p.get("Facebook", ""),
                p.get("WhatsApp", ""), p.get("Telegram", ""), p.get("Messenger", ""), p.get("LINE", "")
            ))
            
        if values:
            extras.execute_values(cur, upsert_query, values)
            conn.commit()
            print(f"  🗄️ PostgreSQL: Updated {len(values)} records")
        
        cur.close(); conn.close()
    except Exception as e: print(f"❌ Postgres Error: {e}")

# ============ MAIN ============

def main():
    parser = argparse.ArgumentParser(description="Scrape Google Maps Directly (No API) and export.")
    parser.add_argument("--query", required=True, help="Search query (e.g. 'Coffee Shop')")
    parser.add_argument("--region", required=True, help="Region (e.g. 'Paris')")
    parser.add_argument("--max-results", type=int, default=20, help="Max results to scrape")
    parser.add_argument("--rating", type=float, default=0.0, help="Min rating filter")
    parser.add_argument("--append", action="store_true", help="Append to existing")
    parser.add_argument("--no-scrape", action="store_true", help="Skip contact scraping")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless")
    parser.add_argument("--only-no-website", action="store_true", help="Filter for places WITHOUT a website")
    parser.add_argument("--only-has-socials", action="store_true", help="Filter for places WITH social media links")
    parser.add_argument("--output", default=".tmp/scraped_places.json", help="Output JSON")
    
    args = parser.parse_args()
    
    api_key = os.getenv("PLACES_API_KEY") # Not used for search, but maybe needed for env consistency
    sheet_id = os.getenv("GSHEET_ID")
    creds_path = os.getenv("GSHEET_CREDS_PATH")
    
    if not all([sheet_id, creds_path]):
        print("Error: Missing Sheet configuration in .env")
        return

    # 1. Scrape Google Maps
    print(f"\n🚀 Starting Maps Scraper: '{args.query}' in '{args.region}'")
    raw_results = scrape_google_maps(args.query, args.region, args.max_results, args.headless)
    
    # 2. Filter locally
    results = [p for p in raw_results if p.get('Rating', 0) >= args.rating]
    print(f"Filtered to {len(results)} results (Rating >= {args.rating})")
    
    # 3. Scrape Contacts (Websites)
    if not args.no_scrape:
        results = scrape_places_websites(results)
    
    # 4. Categorize
    results = categorize_after_scraping(results)
    
    # 5. Apply User Filters
    if args.only_no_website:
        print("🔍 Applying Filter: Only No Website")
        results = [p for p in results if p.get('_sheet_category') == 'without websites']
    
    if args.only_has_socials:
        print("🔍 Applying Filter: Only Has Socials")
        results = [p for p in results if p.get('_sheet_category') == 'with socials']
    
    print(f"Final result count after filtering: {len(results)}")

    # 6. Save
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    # 6. Export
    print("\n💾 Exporting...")
    update_sheets(results, sheet_id, creds_path, append_mode=args.append)
    update_postgres(results)
    
    print("\n✅ Done!")

if __name__ == "__main__":
    main()
