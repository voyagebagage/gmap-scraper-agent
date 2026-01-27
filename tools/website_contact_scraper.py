#!/usr/bin/env python3
"""
Website Contact Scraper Tool
Scrapes websites for contact information: emails, social media links, messaging apps.
Uses Playwright for JavaScript-heavy sites.
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv

load_dotenv()

# Patterns for contact extraction
EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
SOCIAL_PATTERNS = {
    'instagram': re.compile(r'(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_.]+)/?', re.IGNORECASE),
    'facebook': re.compile(r'(?:https?://)?(?:www\.)?facebook\.com/([a-zA-Z0-9.]+)/?', re.IGNORECASE),
    'twitter': re.compile(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]+)/?', re.IGNORECASE),
    'whatsapp': re.compile(r'(?:https?://)?(?:wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com)/([a-zA-Z0-9+]+)/?', re.IGNORECASE),
    'telegram': re.compile(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)/?', re.IGNORECASE),
    'messenger': re.compile(r'(?:https?://)?(?:m\.me|messenger\.com)/([a-zA-Z0-9.]+)/?', re.IGNORECASE),
    'line': re.compile(r'(?:https?://)?line\.me/(?:R/)?ti/p/([a-zA-Z0-9@~_-]+)/?', re.IGNORECASE),
}

# Common contact page paths to check
CONTACT_PATHS = ['/contact', '/contact-us', '/about', '/about-us', '/kontakt', '/contacto']


def extract_contacts_from_html(html: str, base_url: str) -> dict:
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
        'contact_page': None,
    }
    
    # Extract emails (filter out common false positives)
    emails = EMAIL_PATTERN.findall(html)
    filtered_emails = []
    for email in emails:
        email_lower = email.lower()
        # Skip common non-contact emails
        if not any(skip in email_lower for skip in ['example.com', 'domain.com', 'email.com', 'wix', 'wordpress', 'sentry', 'cloudflare']):
            if email not in filtered_emails:
                filtered_emails.append(email)
    contacts['emails'] = filtered_emails[:3]  # Limit to 3 emails
    
    # Extract social media links
    for platform, pattern in SOCIAL_PATTERNS.items():
        matches = pattern.findall(html)
        if matches:
            # Get the first valid match (username/handle)
            handle = matches[0]
            # Reconstruct the URL
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
    
    # Check for contact page links
    contact_link_pattern = re.compile(r'href=["\']([^"\']*(?:contact|about|kontakt|contacto)[^"\']*)["\']', re.IGNORECASE)
    contact_matches = contact_link_pattern.findall(html)
    if contact_matches:
        for path in contact_matches:
            if path.startswith('http'):
                contacts['contact_page'] = path
            else:
                contacts['contact_page'] = urljoin(base_url, path)
            break
    
    return contacts


def scrape_with_playwright(url: str, timeout_ms: int = 15000) -> str:
    """Scrape a website using Playwright (handles JavaScript)."""
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = context.new_page()
            
            try:
                page.goto(url, timeout=timeout_ms, wait_until='domcontentloaded')
                # Wait a bit for dynamic content
                page.wait_for_timeout(2000)
                html = page.content()
            except Exception as e:
                print(f"  Warning: Could not load {url}: {e}", file=sys.stderr)
                html = ""
            finally:
                browser.close()
            
            return html
    except Exception as e:
        print(f"  Playwright error for {url}: {e}", file=sys.stderr)
        return ""


def scrape_with_requests(url: str, timeout: int = 10) -> str:
    """Fallback scraper using requests (faster but no JavaScript)."""
    try:
        import requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"  Requests error for {url}: {e}", file=sys.stderr)
        return ""


def scrape_website(url: str, use_playwright: bool = True) -> dict:
    """Scrape a website for contact information."""
    print(f"  Scraping: {url}")
    
    # Ensure URL has protocol
    if not url.startswith('http'):
        url = 'https://' + url
    
    # Try Playwright first (for JS sites), fallback to requests
    html = ""
    if use_playwright:
        html = scrape_with_playwright(url)
    
    if not html:
        html = scrape_with_requests(url)
    
    if not html:
        return {'error': 'Could not fetch website'}
    
    contacts = extract_contacts_from_html(html, url)
    
    # If we found a contact page and didn't find much, try scraping it too
    if contacts['contact_page'] and len(contacts['emails']) == 0:
        print(f"    Checking contact page: {contacts['contact_page']}")
        contact_html = scrape_with_requests(contacts['contact_page'], timeout=5)
        if contact_html:
            additional = extract_contacts_from_html(contact_html, url)
            # Merge results
            for key, value in additional.items():
                if value and not contacts.get(key):
                    contacts[key] = value
            if additional['emails']:
                contacts['emails'] = list(set(contacts['emails'] + additional['emails']))[:3]
    
    return contacts


def scrape_places(places: list, use_playwright: bool = True) -> list:
    """Scrape contact info for a list of places with websites."""
    results = []
    
    for i, place in enumerate(places):
        website = place.get('website')
        if not website:
            # No website to scrape
            results.append({**place, 'scraped_contacts': None})
            continue
        
        print(f"[{i+1}/{len(places)}] {place.get('name', 'Unknown')}")
        contacts = scrape_website(website, use_playwright=use_playwright)
        
        # Add scraped contacts to place data
        enriched_place = {**place, 'scraped_contacts': contacts}
        results.append(enriched_place)
        
        # Rate limiting - be nice to servers
        import time
        time.sleep(1)
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Scrape websites for contact information.')
    parser.add_argument('--input', required=True, help='Input JSON file with places data')
    parser.add_argument('--output', help='Output JSON file (default: input file with _enriched suffix)')
    parser.add_argument('--no-playwright', action='store_true', help='Disable Playwright (use requests only)')
    parser.add_argument('--url', help='Scrape a single URL (for testing)')
    
    args = parser.parse_args()
    
    # Single URL test mode
    if args.url:
        result = scrape_website(args.url, use_playwright=not args.no_playwright)
        print(json.dumps(result, indent=2))
        return
    
    # Load places from input file
    with open(args.input, 'r') as f:
        data = json.load(f)
    
    # Handle both list and dict with 'places' key
    if isinstance(data, dict) and 'places' in data:
        places = data['places']
    elif isinstance(data, list):
        places = data
    else:
        print("Error: Input file must contain a list of places or a dict with 'places' key", file=sys.stderr)
        sys.exit(1)
    
    # Filter to only places with websites
    places_with_websites = [p for p in places if p.get('website')]
    print(f"Found {len(places_with_websites)} places with websites to scrape")
    
    # Scrape websites
    enriched_places = scrape_places(places_with_websites, use_playwright=not args.no_playwright)
    
    # Merge back with places without websites
    all_enriched = []
    enriched_by_name = {p['name']: p for p in enriched_places}
    for place in places:
        if place['name'] in enriched_by_name:
            all_enriched.append(enriched_by_name[place['name']])
        else:
            all_enriched.append({**place, 'scraped_contacts': None})
    
    # Save output
    output_file = args.output or args.input.replace('.json', '_enriched.json')
    with open(output_file, 'w') as f:
        json.dump({'places': all_enriched}, f, indent=2)
    
    print(f"\n✅ Enriched data saved to: {output_file}")
    
    # Summary
    with_contacts = sum(1 for p in all_enriched if p.get('scraped_contacts') and (p['scraped_contacts'].get('emails') or any(p['scraped_contacts'].get(s) for s in SOCIAL_PATTERNS.keys())))
    print(f"📊 Found contacts for {with_contacts} places")


if __name__ == '__main__':
    main()
