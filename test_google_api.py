#!/usr/bin/env python3

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Test if API key is being read
api_key = os.getenv('GOOGLE_MAPS_API_KEY')

print("=== GOOGLE MAPS API TROUBLESHOOTING ===")
print(f"API key found: {'Yes' if api_key else 'No'}")

if api_key:
    print(f"API key length: {len(api_key)} characters")
    print(f"API key starts with: {api_key[:10]}...")
    print(f"API key ends with: ...{api_key[-10:]}")
else:
    print("❌ API key not found in environment")
    print("Check:")
    print("1. Is GOOGLE_MAPS_API_KEY in your .env file?")
    print("2. Is the .env file in the same directory as your script?")
    print("3. No spaces around the = sign?")
    exit()

# Test API with simple request
try:
    import googlemaps

    print("\n=== TESTING API CONNECTION ===")

    gmaps = googlemaps.Client(key=api_key)

    # Simple test geocode
    result = gmaps.geocode("New York, NY")

    if result:
        print("✅ API test successful!")
        print(f"Found {len(result)} result(s)")
        if result:
            location = result[0]['geometry']['location']
            print(f"Test coordinates: {location['lat']}, {location['lng']}")
    else:
        print("❌ API returned no results")

except Exception as e:
    print(f"❌ API test failed: {e}")
    print("\nPossible issues:")
    print("1. API key has IP restrictions")
    print("2. Geocoding API not enabled")
    print("3. Billing not set up")
    print("4. API key invalid")

print("\n=== ENVIRONMENT FILE CHECK ===")
try:
    with open('.env', 'r') as f:
        lines = f.readlines()
        print(f"Found .env file with {len(lines)} lines")
        for i, line in enumerate(lines, 1):
            if 'GOOGLE_MAPS_API_KEY' in line:
                print(f"Line {i}: {line.strip()[:50]}...")
except FileNotFoundError:
    print("❌ .env file not found in current directory")
    print(f"Current directory: {os.getcwd()}")