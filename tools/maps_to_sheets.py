"""
Google Maps to Sheets - OPTIMIZED VERSION
Uses Text Search (New API) only - same cost as Nearby but better coverage.

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
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

load_dotenv()

def parse_args():
    parser = argparse.ArgumentParser(description="Search Google Maps and export to Google Sheets.")
    parser.add_argument("--query", help="Custom search query. If not set, uses optimized broad keywords.")
    parser.add_argument("--region", help="City or Area (e.g., 'Ko Pha-ngan')")
    parser.add_argument("--map_url", help="Google Maps URL (can be used instead of --region)")
    parser.add_argument("--radius", type=int, default=10, help="Search radius in km (default: 10, max: 50)")
    parser.add_argument("--rating", type=float, default=4.0, help="Minimum rating filter (default: 4.0)")
    parser.add_argument("--min_reviews", type=int, default=0, help="Minimum review count filter (default: 0)")
    parser.add_argument("--append", action="store_true", help="Append to existing sheet data")
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
    
    social_domains = [
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
        is_social = website_url and any(d in website_url.lower() for d in social_domains)
        has_standalone_site = bool(website_url) and not is_social
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
            "_sheet_category": "with websites" if has_standalone_site else "without websites"
        })
    
    return results

def update_sheets(data, sheet_id, creds_path, append_mode=False):
    """Upload data to Google Sheets."""
    if not data:
        print("No data to export.")
        return

    df = pd.DataFrame(data)
    df = df.fillna("")
    df = df.replace([float("inf"), float("-inf")], 0)

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id)
    
    for cat in ["with websites", "without websites"]:
        sub_df = df[df["_sheet_category"] == cat].copy()
        export_df = sub_df.drop(columns=["_sheet_category"])
        
        try:
            ws = sheet.worksheet(cat)
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet.add_worksheet(title=cat, rows="1000", cols="20")
        
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
            print(f"Appended {len(export_df)} results to '{cat}' tab.")
        else:
            ws.clear()
            ws.update(range_name="A1", values=[header] + data_rows, value_input_option="RAW")
            print(f"Updated '{cat}' tab with {len(export_df)} results.")

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
    
    # Save JSON
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Saved to {args.output}")
    
    # Upload to Sheets
    print("\nUploading to Google Sheets...")
    update_sheets(results, sheet_id, creds_path, append_mode=args.append)
    print("\nDone!")
    print(f"\n📊 API calls used: ~{len([q for q in ['restaurant', 'cafe', 'bar', 'hotel', 'spa', 'gym', 'shop', 'nightclub'] if not args.query]) or 1} Text Searches")

if __name__ == "__main__":
    main()
