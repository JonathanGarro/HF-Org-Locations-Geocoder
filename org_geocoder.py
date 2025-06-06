#!/usr/bin/env python3

import pandas as pd
import time
import ssl
import certifi
import re
import os
from geopy.geocoders import Nominatim, ArcGIS
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import argparse
import sys
import urllib3
import requests
import json

# i get ssl warnings sometimes, disable if we need to bypass verification
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# this script will be used by folks unfamiliar with pip - use try/except on imports
try:
    import googlemaps

    GOOGLE_MAPS_AVAILABLE = True
except ImportError:
    GOOGLE_MAPS_AVAILABLE = False


try:
    from dotenv import load_dotenv

    load_dotenv()
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False


def create_full_address(row):
    address_parts = []

    # street address if available
    if pd.notna(row['Primary Address Street']) and str(row['Primary Address Street']).strip():
        # Clean up street address: remove newlines and extra spaces
        street = str(row['Primary Address Street']).replace('\n', ' ').replace('\r', ' ')
        street = ' '.join(street.split())  # Remove extra whitespace
        address_parts.append(street)

    # city
    if pd.notna(row['Primary Address City']) and str(row['Primary Address City']).strip():
        city = str(row['Primary Address City']).strip()
        address_parts.append(city)

    # state
    if pd.notna(row['Primary Address State/Province']) and str(row['Primary Address State/Province']).strip():
        state = str(row['Primary Address State/Province']).strip()
        address_parts.append(state)

    # zip code
    if pd.notna(row['Primary Address Zip/Postal Code']) and str(row['Primary Address Zip/Postal Code']).strip():
        zip_code = str(row['Primary Address Zip/Postal Code']).strip()
        address_parts.append(zip_code)

    return ', '.join(address_parts)


def create_geocoder(service='nominatim', ssl_verify=True):
    if service == 'nominatim':
        if ssl_verify:
            try:
                ctx = ssl.create_default_context(cafile=certifi.where())
                geolocator = Nominatim(
                    user_agent="organization_geocoder_v1.0",
                    ssl_context=ctx
                )
            except:
                geolocator = Nominatim(user_agent="organization_geocoder_v1.0")
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            geolocator = Nominatim(
                user_agent="organization_geocoder_v1.0",
                ssl_context=ctx
            )
    elif service == 'arcgis':
        geolocator = ArcGIS()
    else:
        raise ValueError(f"Unknown service: {service}")

    return geolocator


def simplify_address(address):
    simplified = address

    # some of these APIs seem to struggle with the following:
    patterns = [
        r',\s*Suite\s+[^,]+',
        r',\s*Ste\.?\s+[^,]+',
        r',\s*Floor\s+[^,]+',
        r',\s*\d+(?:st|nd|rd|th)\s+Floor',
        r',\s*Room\s+[^,]+',
        r',\s*#[^,]+',
        r',\s*Apt\.?\s+[^,]+',
        r',\s*Unit\s+[^,]+',
    ]

    for pattern in patterns:
        simplified = re.sub(pattern, '', simplified, flags=re.IGNORECASE)

    simplified = re.sub(r',\s*,', ',', simplified)
    simplified = re.sub(r'\s+', ' ', simplified).strip()

    return simplified


def initialize_google_maps():
    if not GOOGLE_MAPS_AVAILABLE:
        return None

    api_key = os.getenv('GOOGLE_MAPS_API_KEY')
    if not api_key:
        return None

    try:
        gmaps = googlemaps.Client(key=api_key)
        # Test the API key with a simple request
        test_result = gmaps.geocode("New York, NY")
        if test_result:
            print("✓ Google Maps API initialized successfully")
            return gmaps
        else:
            print("✗ Google Maps API key test failed")
            return None
    except Exception as e:
        print(f"✗ Failed to initialize Google Maps API: {e}")
        return None


def geocode_with_google(gmaps_client, address, verbose=False):
    if not gmaps_client:
        return None, None

    try:
        # see flags for verbose settings
        if verbose:
            print(f"    Trying Google Maps API: '{address}'")

        results = gmaps_client.geocode(address)

        if results and len(results) > 0:
            location = results[0]['geometry']['location']
            if verbose:
                print(f"    ✓ Google Maps API succeeded")
            return location['lat'], location['lng']
        else:
            if verbose:
                print(f"    ✗ Google Maps API: No results")
            return None, None

    except Exception as e:
        if verbose:
            print(f"    ✗ Google Maps API error: {e}")
        return None, None


def geocode_address_comprehensive(geolocator, address, gmaps_client=None, verbose=False):
    # try full address with free service first
    if verbose:
        print(f"    Strategy 1: Free service with full address")

    try:
        location = geolocator.geocode(address, timeout=15)
        if location:
            if verbose:
                print(f"    ✓ Free service (full) succeeded")
            return location.latitude, location.longitude, "Free Service (Full)"
    except Exception as e:
        if verbose:
            print(f"    ✗ Free service (full) failed: {e}")

    # if that doesn't work try simplified address with free service
    simplified = simplify_address(address)
    if simplified != address and simplified.strip():
        if verbose:
            print(f"    Strategy 2: Free service with simplified address: '{simplified}'")

        try:
            location = geolocator.geocode(simplified, timeout=15)
            if location:
                if verbose:
                    print(f"    ✓ Free service (simplified) succeeded")
                return location.latitude, location.longitude, "Free Service (Simplified)"
        except Exception as e:
            if verbose:
                print(f"    ✗ Free service (simplified) failed: {e}")

    # fall back on google maps
    if gmaps_client:
        if verbose:
            print(f"    Strategy 3: Google Maps with full address")

        lat, lon = geocode_with_google(gmaps_client, address, verbose)
        if lat is not None and lon is not None:
            return lat, lon, "Google Maps (Full)"

        # Strategy 4: Try Google Maps with simplified address
        if simplified != address and simplified.strip():
            if verbose:
                print(f"    Strategy 4: Google Maps with simplified address")

            lat, lon = geocode_with_google(gmaps_client, simplified, verbose)
            if lat is not None and lon is not None:
                return lat, lon, "Google Maps (Simplified)"

    elif verbose:
        print(f"    Google Maps not available for fallback")

    return None, None, "Failed"


def get_cwa_region(lat, lon, verbose=False):
    """
    Fetch the CWA (County Warning Area) region from the weather.gov API

    Args:
        lat (float): Latitude
        lon (float): Longitude
        verbose (bool): Whether to print verbose output

    Returns:
        str: CWA region code or None if not found
    """
    if lat is None or lon is None:
        return None

    try:
        url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
        if verbose:
            print(f"    Fetching CWA region from: {url}")

        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            cwa = data.get('properties', {}).get('cwa')
            if verbose:
                print(f"    ✓ Found CWA region: {cwa}")
            return cwa
        else:
            if verbose:
                print(f"    ✗ Failed to get CWA region: HTTP {response.status_code}")
            return None

    except Exception as e:
        if verbose:
            print(f"    ✗ Error fetching CWA region: {e}")
        return None


def initialize_geocoder():
    print("Initializing geocoder")

    configs = [
        ('nominatim', True, 'Nominatim with SSL verification'),
        ('nominatim', False, 'Nominatim without SSL verification'),
        ('arcgis', True, 'ArcGIS geocoder'),
    ]

    for service, ssl_verify, description in configs:
        try:
            print(f"Trying: {description}")
            geolocator = create_geocoder(service=service, ssl_verify=ssl_verify)

            # test geocoder with a simple address (i'm using new york as it should work 100 percent of the time
            test_location = geolocator.geocode("New York, NY", timeout=10)
            if test_location:
                print(f"✓ Successfully initialized: {description}")
                return geolocator, service
            else:
                print(f"✗ Geocoder initialized but test failed: {description}")

        except Exception as e:
            print(f"✗ Failed to initialize {description}: {e}")
            continue

    print("Error: Could not initialize any geocoder")
    return None, None


def geocode_csv(input_file, output_file=None, delay=1.0, encoding=None):
    print(f"Reading CSV file: {input_file}")

    try:
        # try to read the CSV file with different encodings
        # salesforce seems to inconsistently format csv report exports
        if encoding:
            print(f"Using specified encoding: {encoding}")
            df = pd.read_csv(input_file, encoding=encoding)
        else:
            encodings_to_try = ['utf-8', 'cp1252', 'iso-8859-1', 'latin1']
            df = None

            for enc in encodings_to_try:
                try:
                    print(f"Trying encoding: {enc}")
                    df = pd.read_csv(input_file, encoding=enc)
                    print(f"Successfully read file with encoding: {enc}")
                    break
                except UnicodeDecodeError:
                    continue

            if df is None:
                print("Error: Could not read file with any common encoding")
                return

        print(f"Loaded {len(df)} rows")

        # we should be using the same structure each time but verify required columns exist
        # remember to always use the simple 'Organization' type report
        required_columns = [
            'Organization Name',
            'Primary Address Street',
            'Primary Address City',
            'Primary Address State/Province',
            'Primary Address Zip/Postal Code'
        ]

        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            print(f"Error: Missing required columns: {missing_columns}")
            return

        geolocator, service_used = initialize_geocoder()
        if geolocator is None:
            print("Could not initialize any geocoding service. Please check your internet connection.")
            return

        print(f"Using {service_used} geocoding service")

        # google maps client as fallback
        gmaps_client = initialize_google_maps()
        if gmaps_client:
            print("Google Maps API available as fallback")
        else:
            print("Google Maps API not available")
            if GOOGLE_MAPS_AVAILABLE:
                print("To use Google Maps fallback: add GOOGLE_MAPS_API_KEY to your .env file")
            else:
                print("Install with: pip install googlemaps python-dotenv")

        print("Creating full address strings...")
        df['Full_Address'] = df.apply(create_full_address, axis=1)

        # Only initialize columns that don't already exist
        for col in ['Latitude', 'Longitude', 'Geocoding_Status', 'Geocoding_Method', 'CWA_Region']:
            if col not in df.columns:
                df[col] = None

        total_rows = len(df)
        successful_geocodes = 0
        existing_geocodes = 0

        print(f"Starting geocoding process with {delay}s delay between requests...")
        print("This may take a while for large datasets...")

        for idx, row in df.iterrows():
            address = row['Full_Address']

            if not address or address.strip() == '':
                print(f"Row {idx + 1}/{total_rows}: Empty address, skipping")
                df.at[idx, 'Geocoding_Status'] = 'Empty Address'
                continue

            # Check if row already has valid geocoding data
            has_geocoding_data = (
                pd.notna(row['Latitude']) and 
                pd.notna(row['Longitude']) and 
                pd.notna(row['Geocoding_Status']) and 
                pd.notna(row['Geocoding_Method'])
            )

            if has_geocoding_data:
                print(f"Row {idx + 1}/{total_rows}: Already has geocoding data, skipping geocoding")
                lat, lon = row['Latitude'], row['Longitude']
                existing_geocodes += 1
                print(f"  ✓ Using existing coordinates: {lat:.6f}, {lon:.6f} ({row['Geocoding_Method']})")
            else:
                print(f"Row {idx + 1}/{total_rows}: Geocoding '{address}'")
                verbose = idx < 10
                lat, lon, method = geocode_address_comprehensive(geolocator, address, gmaps_client, verbose)

                if lat is not None and lon is not None:
                    df.at[idx, 'Latitude'] = lat
                    df.at[idx, 'Longitude'] = lon
                    df.at[idx, 'Geocoding_Status'] = 'Success'
                    df.at[idx, 'Geocoding_Method'] = method
                    successful_geocodes += 1
                    print(f"  ✓ Found coordinates: {lat:.6f}, {lon:.6f} ({method})")
                else:
                    df.at[idx, 'Geocoding_Status'] = 'Failed'
                    df.at[idx, 'Geocoding_Method'] = 'Failed'
                    df.at[idx, 'CWA_Region'] = 'N/A'
                    print(f"  ✗ All geocoding strategies failed")
                    continue  # Skip CWA lookup if geocoding failed

            # Get CWA region from weather.gov API if we have coordinates
            if pd.notna(lat) and pd.notna(lon) and pd.isna(row['CWA_Region']):
                verbose = idx < 10
                cwa_region = get_cwa_region(lat, lon, verbose)
                if cwa_region:
                    df.at[idx, 'CWA_Region'] = cwa_region
                    print(f"  ✓ Found CWA region: {cwa_region}")
                else:
                    df.at[idx, 'CWA_Region'] = 'Not Found'
                    print(f"  ✗ Could not determine CWA region")

            # rate limit the free service (be a good citizen and don't overload it)
            if idx < total_rows - 1:
                time.sleep(delay)

        df = df.drop('Full_Address', axis=1)

        if output_file is None:
            base_name = input_file.rsplit('.', 1)[0]
            output_file = f"{base_name}_geocoded.csv"

        print(f"\nSaving results to: {output_file}")
        df.to_csv(output_file, index=False, encoding='utf-8')

        print(f"\nGeocoding Summary:")
        print(f"Total addresses processed: {total_rows}")
        print(f"Already had geocoding data: {existing_geocodes}")
        print(f"Newly geocoded in this run: {successful_geocodes}")
        print(f"Failed to geocode: {total_rows - successful_geocodes - existing_geocodes}")
        print(f"Overall success rate: {((successful_geocodes + existing_geocodes) / total_rows) * 100:.1f}%")

        if successful_geocodes > 0 or existing_geocodes > 0:
            # Include all rows with valid geocoding data in the method breakdown
            method_counts = df[pd.notna(df['Geocoding_Method']) & (df['Geocoding_Method'] != 'Failed')]['Geocoding_Method'].value_counts()
            print(f"\nGeocoding method breakdown:")
            for method, count in method_counts.items():
                print(f"  {method}: {count} addresses")

            # Add CWA region summary
            cwa_counts = df['CWA_Region'].value_counts()
            cwa_found = len(df[df['CWA_Region'].notna() & (df['CWA_Region'] != 'Not Found') & (df['CWA_Region'] != 'N/A')])
            print(f"\nCWA Region Summary:")
            print(f"  Addresses with CWA region: {cwa_found}")
            print(f"  Addresses without CWA region: {total_rows - cwa_found}")

            if cwa_found > 0:
                print(f"\nTop CWA regions:")
                for cwa, count in cwa_counts.head(5).items():
                    if cwa not in ['Not Found', 'N/A']:
                        print(f"  {cwa}: {count} addresses")

    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found")
    except pd.errors.EmptyDataError:
        print(f"Error: Input file '{input_file}' is empty")
    except Exception as e:
        print(f"Error processing file: {e}")


def main():
    """Command line interface."""
    parser = argparse.ArgumentParser(
        description='Geocode organization addresses in CSV file',
        epilog='''
Google Maps API Setup (optional but Jonathan recommends for better accuracy):
1. Install: pip install googlemaps python-dotenv
2. Get API key from: https://developers.google.com/maps/documentation/geocoding/get-api-key
3. Create .env file in same directory with: GOOGLE_MAPS_API_KEY=your_api_key_here
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('input_file', help='Path to input CSV file')
    parser.add_argument('-o', '--output', help='Path to output CSV file (optional)')
    parser.add_argument('-d', '--delay', type=float, default=1.0,
                        help='Delay between API calls in seconds (default: 1.0)')
    parser.add_argument('-e', '--encoding',
                        help='File encoding (e.g., utf-8, cp1252, iso-8859-1)')

    args = parser.parse_args()

    if args.delay < 0.5:
        print("Warning: Using delays less than 0.5 seconds may overwhelm the free service")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != 'y':
            sys.exit(0)

    geocode_csv(args.input_file, args.output, args.delay, args.encoding)


if __name__ == "__main__":
    main()
