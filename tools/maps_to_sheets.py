"""
Google Maps to Sheets - Using Places API (New) 2026

Searches for places using the new Places API and exports to Google Sheets.
Maximized for High Coverage:
- Exhaustive loop through 50+ business types using Nearby Search.
- Targeted Text Search fallback for hard-to-find names.
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
    parser.add_argument("--query", help="Text search query. If set, runs ONLY this query.")
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
    url = f"https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": region, "key": api_key}
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        if data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return {"latitude": loc["lat"], "longitude": loc["lng"]}
    return None

def search_nearby_new(api_key, location, radius_km, region_name, text_query=None):
    """
    Exhaustive Search Strategy:
    1. Loops through each business type individually (Necessary for >60 results total).
    2. Runs Text Search for common business terms to find things ignored by prominence.
    """
    
    field_mask = ",".join([
        "places.id", "places.displayName", "places.formattedAddress", "places.types",
        "places.rating", "places.userRatingCount", "places.websiteUri",
        "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.googleMapsUri"
    ])
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask
    }
    
    radius_meters = min(radius_km * 1000, 50000)
    all_places = []
    seen_ids = set()

    # --- MODE A: Dedicated Query (Override everything else if --query is used) ---
    if text_query:
        print(f"  Running custom query: {text_query}...")
        return search_text_new(api_key, f"{text_query} in {region_name}", location, radius_km)

    # --- PASS 1: Individual Category Nearby Search (Deep Crawl) ---
    # We MUST loop individually because a combined request caps at 60 results total.
    
    business_types = [
        "restaurant", "cafe", "coffee_shop", "bar", "bakery", "meal_takeaway", "meal_delivery", "fast_food_restaurant",
        "lodging", "hotel", "motel", "hostel", "resort_hotel",
        "spa", "gym", "yoga_studio", "wellness_center", "sauna",
        "store", "shopping_mall", "supermarket", "clothing_store", "convenience_store", "jewelry_store", "shoe_store", "furniture_store",
        "night_club", "event_venue", "movie_theater", "amusement_park",
        "beauty_salon", "hair_salon", "barber_shop", "laundry",
        "car_rental", "car_repair", "car_wash", "gas_station",
        "bank", "atm", "post_office", "travel_agency",
        "pharmacy", "hospital", "doctor", "dentist",
        "tourist_attraction", "art_gallery", "museum", "library",
        "school", "university", "real_estate_agency", "farm", "factory"
    ]
    
    print(f"  Pass 1: Running Individual Category Nearby Search ({len(business_types)} loops)...")
    
    for btype in business_types:
        body = {
            "includedTypes": [btype],
            "locationRestriction": {"circle": {"center": location, "radius": radius_meters}},
            "maxResultCount": 20
        }
        
        type_found_count = 0
        while True:
            response = requests.post("https://places.googleapis.com/v1/places:searchNearby", headers=headers, json=body)
            if response.status_code != 200: break
            data = response.json()
            places = data.get("places", [])
            if not places: break
            
            for p in places:
                pid = p.get("id")
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    all_places.append(p)
                    type_found_count += 1
            
            next_page = data.get("nextPageToken")
            if not next_page: break
            body["pageToken"] = next_page
            time.sleep(0.5)
            
        if type_found_count > 0:
            print(f"    {btype}: +{type_found_count} places")

    # --- PASS 2: Targeted Text Search (Find Missing Pearls like ATIRI) ---
    # Sometimes Google categorizes a 'cafe' as just an 'establishment' so Nearby fails.
    print(f"\n  Pass 2: Targeted Text Search for hidden gems...")
    
    deep_queries = ["restaurant", "cafe", "hotel", "spa", "sauna", "gym", "party", "eatery", "farm", "factory"]
    
    for dq in deep_queries:
        text_results = search_text_new(api_key, f"{dq} in {region_name}", location, radius_km)
        dq_found_count = 0
        for p in text_results:
            pid = p.get("id")
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_places.append(p)
                dq_found_count += 1
        if dq_found_count > 0:
            print(f"    '{dq} (text)': +{dq_found_count} places")

    print(f"\n  Total unique places found: {len(all_places)}")
    return all_places

def search_text_new(api_key, query, location, radius_km):
    """Search for places using Text Search. Paginated."""
    url = "https://places.googleapis.com/v1/places:searchText"
    field_mask = "places.id,places.displayName,places.formattedAddress,places.types,places.rating,places.userRatingCount,places.websiteUri,places.nationalPhoneNumber,places.internationalPhoneNumber,places.googleMapsUri"
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": api_key, "X-Goog-FieldMask": field_mask}
    body = {"textQuery": query, "locationBias": {"circle": {"center": location, "radius": min(radius_km * 1000, 50000)}}, "maxResultCount": 20}
    
    results = []
    while True:
        response = requests.post(url, headers=headers, json=body)
        if response.status_code != 200: break
        data = response.json()
        places = data.get("places", [])
        if not places: break
        results.extend(places)
        next_page = data.get("nextPageToken")
        if not next_page: break
        body["pageToken"] = next_page
        time.sleep(0.5)
    return results

def process_places(places, region, min_rating, min_reviews):
    """Process and categorize places for export."""
    results = []
    social_domains = ["facebook.com", "instagram.com", "twitter.com", "x.com", "line.me", "tripadvisor", "booking.com", "agoda.com"]
    for p in places:
        rating = p.get("rating", 0)
        review_count = p.get("userRatingCount", 0)
        if rating < min_rating or review_count < min_reviews: continue
        
        name = p.get("displayName", {}).get("text", "Unknown")
        types = p.get("types", [])
        category_str = ", ".join(types) if types else "unknown"
        website = p.get("websiteUri")
        is_social = website and any(d in website.lower() for d in social_domains)
        website_display = website if website else "not have website"
        phone = p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber") or ""
        
        results.append({
            "Location": region, "Name": name, "Rating": rating, "Review Count": review_count,
            "Phone": phone, "Address": p.get("googleMapsUri", ""), "Website": website_display, "Category": category_str,
            "_sheet_category": "with websites" if (website and not is_social) else "without websites"
        })
    return results

def update_sheets(data, sheet_id, creds_path, append_mode=False):
    """Upload results to Google Sheets."""
    if not data: return
    df = pd.DataFrame(data).fillna("").replace([float("inf"), float("-inf")], 0)
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = gspread.authorize(creds).open_by_key(sheet_id)
    
    for cat in ["with websites", "without websites"]:
        sub_df = df[df["_sheet_category"] == cat].drop(columns=["_sheet_category"])
        try: ws = sheet.worksheet(cat)
        except gspread.exceptions.WorksheetNotFound: ws = sheet.add_worksheet(title=cat, rows="5000", cols="20")
        
        header, rows = sub_df.columns.tolist(), sub_df.values.tolist()
        if append_mode:
            existing_count = len(ws.get_all_values())
            if existing_count == 0: ws.update(range_name="A1", values=[header] + rows, value_input_option="RAW")
            elif rows: ws.update(range_name=f"A{existing_count+1}", values=rows, value_input_option="RAW")
            print(f"  Appended {len(sub_df)} rows to '{cat}'.")
        else:
            ws.clear()
            ws.update(range_name="A1", values=[header] + rows, value_input_option="RAW")
            print(f"  Updated '{cat}' with {len(sub_df)} rows.")

def main():
    args = parse_args()
    api_key, sheet_id, creds_path = os.getenv("PLACES_API_KEY"), os.getenv("GSHEET_ID"), os.getenv("GSHEET_CREDS_PATH")
    if not all([api_key, sheet_id, creds_path]): return print("Error: Check .env")

    # Resolve location
    loc, region = extract_coords(args.map_url), args.region
    if not loc:
        if region: loc = geocode_region(api_key, region)
        else:
            match = re.search(r'/place/([^/]+)/', args.map_url)
            if match: region = match.group(1).replace('+', ' '); loc = geocode_region(api_key, region)
    if not loc: return print("Error: Location not found.")
    if not region: region = "Custom Search"

    print(f"\n============================================================")
    print(f"Searching: {args.query if args.query else 'Exhaustive High-Coverage Scan'}")
    print(f"Loc: {region} | Radius: {args.radius}km | Min Rating: {args.rating}")
    print(f"============================================================\n")
    
    raw = search_nearby_new(api_key, loc, args.radius, region, args.query)
    results = process_places(raw, region, args.rating, args.min_reviews)
    
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f: json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\nExporting {len(results)} places to Google Sheets...")
    update_sheets(results, sheet_id, creds_path, append_mode=args.append)
    print("\nMission Complete! 🚀")

if __name__ == "__main__":
    main()
