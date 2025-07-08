#!/usr/bin/env python3

import pandas as pd
import time
import ssl
import certifi
import re
import os
import datetime
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
    if pd.notna(row['Organization: Primary Address Street']) and str(row['Organization: Primary Address Street']).strip():
        # Clean up street address: remove newlines and extra spaces
        street = str(row['Organization: Primary Address Street']).replace('\n', ' ').replace('\r', ' ')
        street = ' '.join(street.split())  # Remove extra whitespace
        address_parts.append(street)

    # city
    if pd.notna(row['Organization: Primary Address City']) and str(row['Organization: Primary Address City']).strip():
        city = str(row['Organization: Primary Address City']).strip()
        address_parts.append(city)

    # state
    if pd.notna(row['Organization: Primary Address State/Province']) and str(row['Organization: Primary Address State/Province']).strip():
        state = str(row['Organization: Primary Address State/Province']).strip()
        address_parts.append(state)

    # zip code
    if pd.notna(row['Organization: Primary Address Zip/Postal Code']) and str(row['Organization: Primary Address Zip/Postal Code']).strip():
        zip_code = str(row['Organization: Primary Address Zip/Postal Code']).strip()
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

        # try Google Maps with simplified address
        if simplified != address and simplified.strip():
            if verbose:
                print(f"    Strategy 4: Google Maps with simplified address")

            lat, lon = geocode_with_google(gmaps_client, simplified, verbose)
            if lat is not None and lon is not None:
                return lat, lon, "Google Maps (Simplified)"

    elif verbose:
        print(f"    Google Maps not available for fallback")

    return None, None, "Failed"


def get_multiple_zones(lat, lon, verbose=False):
    """
    Fetch multiple zone types from the weather.gov API
    Returns both forecast zones AND CWA office codes

    Args:
        lat (float): Latitude
        lon (float): Longitude
        verbose (bool): Whether to print verbose output

    Returns:
        dict: Dictionary with zone information
    """
    if lat is None or lon is None:
        return {}

    try:
        url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
        if verbose:
            print(f"    Fetching all zone info from: {url}")

        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            properties = data.get('properties', {})

            zones = {}

            # Forecast zone (specific - for weather alerts)
            if properties.get('forecastZone'):
                zones['forecast_zone'] = properties['forecastZone'].split('/')[-1]

            # CWA office (broader - for your shapefile)
            if properties.get('cwa'):
                zones['cwa_office'] = properties.get('cwa')

            # County zone
            if properties.get('county'):
                zones['county_zone'] = properties['county'].split('/')[-1]

            # Fire weather zone
            if properties.get('fireWeatherZone'):
                zones['fire_zone'] = properties['fireWeatherZone'].split('/')[-1]

            # Grid information (can be useful for detailed forecasts)
            if properties.get('gridId') and properties.get('gridX') and properties.get('gridY'):
                zones['grid_id'] = properties.get('gridId')
                zones['grid_x'] = properties.get('gridX')
                zones['grid_y'] = properties.get('gridY')

            if verbose:
                print(f"    ✓ Found zones: {zones}")

            return zones

        else:
            if verbose:
                print(f"    ✗ Failed to get zone info: HTTP {response.status_code}")
            return {}

    except Exception as e:
        if verbose:
            print(f"    ✗ Error fetching zone info: {e}")
        return {}


def get_cwa_region(lat, lon, verbose=False):
    """
    Fetch the CWA region from the weather.gov API
    This is a backward-compatible version that returns the most specific zone

    Args:
        lat (float): Latitude
        lon (float): Longitude
        verbose (bool): Whether to print verbose output

    Returns:
        str: Specific forecast zone code (e.g., DCZ001, CAZ006) or CWA office code as fallback
    """
    zones = get_multiple_zones(lat, lon, verbose)

    if zones:
        # Return forecast zone if available (most specific)
        if 'forecast_zone' in zones:
            return zones['forecast_zone']
        # Fallback to CWA office if forecast zone not available
        elif 'cwa_office' in zones:
            return zones['cwa_office']

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


def geocode_csv(input_file, output_file=None, delay=1.0, encoding=None, enhanced_zones=True, previous_file=None):
    print(f"Reading CSV file: {input_file}")

    # Check if the geocoded organizations index file exists
    index_file_path = "outputs/geocoded_organizations_index.csv"
    already_geocoded_ids = set()

    if os.path.exists(index_file_path):
        try:
            print(f"Loading previously geocoded organizations from: {index_file_path}")
            index_df = pd.read_csv(index_file_path)
            # Filter for successfully geocoded organizations
            geocoded_orgs = index_df[index_df['Geocoded'] == True]
            already_geocoded_ids = set(geocoded_orgs['Organization_ID'].astype(str))
            print(f"Found {len(already_geocoded_ids)} previously geocoded organizations")
        except Exception as e:
            print(f"Warning: Error reading index file: {e}")

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
            'Organization: Primary Address Street',
            'Organization: Primary Address City',
            'Organization: Primary Address State/Province',
            'Organization: Primary Address Zip/Postal Code'
        ]

        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            print(f"Error: Missing required columns: {missing_columns}")
            return

        # Function to create a unique identifier for each organization address
        def create_org_id(row):
            org_name = str(row.get('Organization Name', '')).strip().lower()
            street = str(row.get('Organization: Primary Address Street', '')).strip().lower()
            city = str(row.get('Organization: Primary Address City', '')).strip().lower()
            state = str(row.get('Organization: Primary Address State/Province', '')).strip().lower()
            zip_code = str(row.get('Organization: Primary Address Zip/Postal Code', '')).strip().lower()
            return f"{org_name}|{street}|{city}|{state}|{zip_code}"

        # Load previous geocoded data if provided
        previous_data = None
        if previous_file:
            try:
                print(f"Loading previous geocoded data from: {previous_file}")
                # Try different encodings for the previous file too
                if encoding:
                    previous_data = pd.read_csv(previous_file, encoding=encoding)
                else:
                    for enc in ['utf-8', 'cp1252', 'iso-8859-1', 'latin1']:
                        try:
                            previous_data = pd.read_csv(previous_file, encoding=enc)
                            print(f"Successfully read previous file with encoding: {enc}")
                            break
                        except UnicodeDecodeError:
                            continue

                if previous_data is None:
                    print("Warning: Could not read previous file with any common encoding")
                else:
                    print(f"Loaded {len(previous_data)} rows from previous file")

                    # Check if previous data has the required geocoding columns
                    geocoding_columns = ['Latitude', 'Longitude', 'Geocoding_Status', 'Geocoding_Method']
                    missing_geocoding_columns = [col for col in geocoding_columns if col not in previous_data.columns]

                    if missing_geocoding_columns:
                        print(f"Warning: Previous file is missing geocoding columns: {missing_geocoding_columns}")
                        print("Will not use previous geocoding data")
                        previous_data = None

                    # If we have valid previous data, merge it with the current data
                    if previous_data is not None:

                        # Add org_id to both dataframes
                        df['org_id'] = df.apply(create_org_id, axis=1)
                        previous_data['org_id'] = previous_data.apply(create_org_id, axis=1)

                        # Create a dictionary of previously geocoded data
                        geocoded_dict = {}
                        geocoding_columns = ['Latitude', 'Longitude', 'Geocoding_Status', 'Geocoding_Method']

                        # Add CWA columns if they exist in the previous data
                        if 'CWA_Region' in previous_data.columns:
                            geocoding_columns.append('CWA_Region')
                        if enhanced_zones and 'CWA_Office' in previous_data.columns:
                            geocoding_columns.append('CWA_Office')
                        if 'County_Zone' in previous_data.columns:
                            geocoding_columns.append('County_Zone')
                        if 'Fire_Zone' in previous_data.columns:
                            geocoding_columns.append('Fire_Zone')
                        if 'Grid_ID' in previous_data.columns:
                            geocoding_columns.append('Grid_ID')
                            geocoding_columns.append('Grid_X')
                            geocoding_columns.append('Grid_Y')

                        # Only include rows with successful geocoding
                        successfully_geocoded = previous_data[
                            previous_data['Geocoding_Status'] == 'Success'
                        ]

                        for _, row in successfully_geocoded.iterrows():
                            org_id = row['org_id']
                            geocoded_dict[org_id] = {col: row[col] for col in geocoding_columns if col in row}

                        print(f"Found {len(geocoded_dict)} previously geocoded organizations")

                        # Apply the previously geocoded data to the current dataframe
                        previously_geocoded_count = 0
                        for idx, row in df.iterrows():
                            org_id = row['org_id']
                            if org_id in geocoded_dict:
                                for col, value in geocoded_dict[org_id].items():
                                    df.at[idx, col] = value
                                previously_geocoded_count += 1

                        print(f"Applied geocoding data to {previously_geocoded_count} organizations from previous file")

                        # Remove the temporary org_id column
                        df = df.drop('org_id', axis=1)
            except FileNotFoundError:
                print(f"Warning: Previous file '{previous_file}' not found")
                previous_data = None
            except Exception as e:
                print(f"Warning: Error reading previous file: {e}")
                previous_data = None

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

        # initialize columns that include both zone types
        if enhanced_zones:
            print("Enhanced zones mode: Will capture forecast zones AND CWA offices")
            columns_to_add = [
                'Latitude', 'Longitude', 'Geocoding_Status', 'Geocoding_Method',
                'CWA_Region',  # Specific forecast zone (DCZ001) - for weather alerts
                'CWA_Office',  # Broader office code (LWX) - for shapefile mapping
                'County_Zone', 'Fire_Zone',  # Additional zone types
                'Grid_ID', 'Grid_X', 'Grid_Y'  # Grid information for detailed forecasts
            ]
        else:
            columns_to_add = ['Latitude', 'Longitude', 'Geocoding_Status', 'Geocoding_Method', 'CWA_Region']

        for col in columns_to_add:
            if col not in df.columns:
                df[col] = None

        total_rows = len(df)
        successful_geocodes = 0
        existing_geocodes = 0

        print(f"Starting geocoding process with {delay}s delay between requests...")
        print("This may take a while for large datasets...")

        for idx, row in df.iterrows():
            # Check if this organization has already been geocoded in a previous run
            skip_geocoding = False
            org_id = None
            for col in df.columns:
                if 'organization' in col.lower() and 'id' in col.lower():
                    org_id = str(row[col]).strip()
                    if org_id in already_geocoded_ids:
                        print(f"Row {idx + 1}/{total_rows}: Organization ID {org_id} already geocoded in a previous run, skipping")
                        df.at[idx, 'Geocoding_Status'] = 'Previously Geocoded'
                        existing_geocodes += 1
                        skip_geocoding = True
                    break

            if skip_geocoding:
                continue

            address = row['Full_Address']

            if not address or address.strip() == '':
                print(f"Row {idx + 1}/{total_rows}: Empty address, skipping")
                df.at[idx, 'Geocoding_Status'] = 'Empty Address'
                continue

            # check if row already has valid geocoding data
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
                    if enhanced_zones:
                        df.at[idx, 'CWA_Office'] = 'N/A'
                    print(f"  ✗ All geocoding strategies failed")
                    continue  # Skip zone lookup if geocoding failed

            # get zone information from weather.gov API if we have coordinates
            if pd.notna(lat) and pd.notna(lon):
                # Check if we need to update CWA information
                needs_cwa_update = pd.isna(row['CWA_Region']) or row['CWA_Region'] in ['Not Found', 'N/A', '']
                needs_office_update = enhanced_zones and (
                            pd.isna(row.get('CWA_Office')) or row.get('CWA_Office') in ['Not Found', 'N/A', ''])

                if needs_cwa_update or needs_office_update:
                    verbose = idx < 10

                    if enhanced_zones:
                        # Get multiple zone types
                        zones = get_multiple_zones(lat, lon, verbose)

                        if zones:
                            # Use forecast zone as primary CWA_Region (most specific)
                            if 'forecast_zone' in zones and needs_cwa_update:
                                df.at[idx, 'CWA_Region'] = zones['forecast_zone']
                                print(f"  ✓ Found forecast zone: {zones['forecast_zone']}")
                            elif 'cwa_office' in zones and needs_cwa_update:
                                df.at[idx, 'CWA_Region'] = zones['cwa_office']
                                print(f"  ✓ Found CWA office: {zones['cwa_office']}")

                            # Always store CWA office separately for shapefile mapping
                            if 'cwa_office' in zones and needs_office_update:
                                df.at[idx, 'CWA_Office'] = zones['cwa_office']
                                print(f"  ✓ Found CWA office for shapefile: {zones['cwa_office']}")

                            # Store other zone types
                            if 'county_zone' in zones:
                                df.at[idx, 'County_Zone'] = zones['county_zone']
                            if 'fire_zone' in zones:
                                df.at[idx, 'Fire_Zone'] = zones['fire_zone']
                            if 'grid_id' in zones:
                                df.at[idx, 'Grid_ID'] = zones['grid_id']
                                df.at[idx, 'Grid_X'] = zones['grid_x']
                                df.at[idx, 'Grid_Y'] = zones['grid_y']
                        else:
                            if needs_cwa_update:
                                df.at[idx, 'CWA_Region'] = 'Not Found'
                            if needs_office_update:
                                df.at[idx, 'CWA_Office'] = 'Not Found'
                            print(f"  ✗ Could not determine any zones")
                    else:
                        # Simple mode - just get the best available zone
                        if needs_cwa_update:
                            cwa_region = get_cwa_region(lat, lon, verbose)
                            if cwa_region:
                                df.at[idx, 'CWA_Region'] = cwa_region
                                print(f"  ✓ Found zone: {cwa_region}")
                            else:
                                df.at[idx, 'CWA_Region'] = 'Not Found'
                                print(f"  ✗ Could not determine zone")

            # rate limit the free service (be a good citizen and don't overload it)
            if idx < total_rows - 1:
                time.sleep(delay)

        df = df.drop('Full_Address', axis=1)

        # Create outputs directory if it doesn't exist
        outputs_dir = "outputs"
        if not os.path.exists(outputs_dir):
            os.makedirs(outputs_dir)
            print(f"Created directory: {outputs_dir}")

        # Get current date for filename
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")

        if output_file is None:
            # Extract just the filename without path
            input_filename = os.path.basename(input_file)
            base_name = input_filename.rsplit('.', 1)[0]
            suffix = "_enhanced_geocoded" if enhanced_zones else "_geocoded"
            output_file = f"{outputs_dir}/{base_name}{suffix}_{current_date}.csv"
        else:
            # If output file is specified, still put it in outputs dir with date
            output_filename = os.path.basename(output_file)
            base_name = output_filename.rsplit('.', 1)[0]
            extension = output_filename.rsplit('.', 1)[1] if '.' in output_filename else 'csv'
            output_file = f"{outputs_dir}/{base_name}_{current_date}.{extension}"

        print(f"\nSaving results to: {output_file}")
        df.to_csv(output_file, index=False, encoding='utf-8')

        # Create or update the index file that tracks geocoded organizations
        index_file_path = f"{outputs_dir}/geocoded_organizations_index.csv"

        # Create a unique organization ID for each row
        # First check if 'Organization: ID' column exists
        org_id_column = None
        for col in df.columns:
            if 'organization' in col.lower() and 'id' in col.lower():
                org_id_column = col
                break

        # If no specific ID column found, use the create_org_id function
        if org_id_column is None:
            # Add org_id to dataframe
            df['org_id'] = df.apply(create_org_id, axis=1)
            org_id_column = 'org_id'

        # Create a dataframe for the index with org_id and geocoding status
        current_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        index_df = pd.DataFrame({
            'Organization_ID': df[org_id_column],
            'Geocoded': (df['Geocoding_Status'] == 'Success') | (df['Geocoding_Status'] == 'Previously Geocoded'),
            'Last_Updated': current_date_str
        })

        # If index file exists, update it with new information
        if os.path.exists(index_file_path):
            try:
                existing_index = pd.read_csv(index_file_path)

                # Merge existing index with new data, keeping the latest information
                merged_index = pd.concat([existing_index, index_df])
                # Drop duplicates, keeping the last occurrence (which is from the current run)
                merged_index = merged_index.drop_duplicates(subset=['Organization_ID'], keep='last')

                # Save the updated index
                merged_index.to_csv(index_file_path, index=False, encoding='utf-8')
                print(f"Updated index file: {index_file_path}")
            except Exception as e:
                print(f"Error updating index file: {e}")
                # If there's an error, just create a new index file
                index_df.to_csv(index_file_path, index=False, encoding='utf-8')
                print(f"Created new index file: {index_file_path}")
        else:
            # If index file doesn't exist, create it
            index_df.to_csv(index_file_path, index=False, encoding='utf-8')
            print(f"Created new index file: {index_file_path}")

        # If we created a temporary org_id column, remove it
        if org_id_column == 'org_id':
            df = df.drop('org_id', axis=1)

        print(f"\nGeocoding Summary:")
        print(f"Total addresses processed: {total_rows}")

        # Count how many were previously geocoded (from index file)
        previously_geocoded = len(df[df['Geocoding_Status'] == 'Previously Geocoded'])

        # If we loaded data from a previous file, show how many were reused
        if previous_file and 'previously_geocoded_count' in locals():
            print(f"Reused from previous file: {previously_geocoded_count}")
            print(f"Already had geocoding data in input file: {existing_geocodes - previously_geocoded_count - previously_geocoded}")
            print(f"Skipped (previously geocoded in index): {previously_geocoded}")
        else:
            print(f"Already had geocoding data: {existing_geocodes - previously_geocoded}")
            print(f"Skipped (previously geocoded in index): {previously_geocoded}")

        print(f"Newly geocoded in this run: {successful_geocodes}")
        print(f"Failed to geocode: {total_rows - successful_geocodes - existing_geocodes}")
        print(f"Overall success rate: {((successful_geocodes + existing_geocodes) / total_rows) * 100:.1f}%")

        if successful_geocodes > 0 or existing_geocodes > 0:
            # include all rows with valid geocoding
            method_counts = df[pd.notna(df['Geocoding_Method']) & (df['Geocoding_Method'] != 'Failed')][
                'Geocoding_Method'].value_counts()
            print(f"\nGeocoding method breakdown:")
            for method, count in method_counts.items():
                print(f"  {method}: {count} addresses")

            # Zone region summary
            if enhanced_zones:
                # Forecast zones
                forecast_zone_counts = df['CWA_Region'].value_counts()
                zones_found = len(
                    df[df['CWA_Region'].notna() & (df['CWA_Region'] != 'Not Found') & (df['CWA_Region'] != 'N/A')])
                print(f"\nForecast Zone Assignment Summary:")
                print(f"  Addresses with forecast zones: {zones_found}")
                print(f"  Addresses without zones: {total_rows - zones_found}")

                if zones_found > 0:
                    print(f"\nTop forecast zones:")
                    for zone, count in forecast_zone_counts.head(10).items():
                        if zone not in ['Not Found', 'N/A']:
                            print(f"  {zone}: {count} addresses")

                # CWA offices
                if 'CWA_Office' in df.columns:
                    office_counts = df['CWA_Office'].value_counts()
                    offices_found = len(
                        df[df['CWA_Office'].notna() & (df['CWA_Office'] != 'Not Found') & (df['CWA_Office'] != 'N/A')])
                    print(f"\nCWA Office Assignment Summary:")
                    print(f"  Addresses with CWA offices: {offices_found}")

                    if offices_found > 0:
                        print(f"\nTop CWA offices:")
                        for office, count in office_counts.head(10).items():
                            if office not in ['Not Found', 'N/A']:
                                print(f"  {office}: {count} addresses")
            else:
                zone_counts = df['CWA_Region'].value_counts()
                zones_found = len(
                    df[df['CWA_Region'].notna() & (df['CWA_Region'] != 'Not Found') & (df['CWA_Region'] != 'N/A')])
                print(f"\nZone Assignment Summary:")
                print(f"  Addresses with zones: {zones_found}")
                print(f"  Addresses without zones: {total_rows - zones_found}")

                if zones_found > 0:
                    print(f"\nTop zones/regions:")
                    for zone, count in zone_counts.head(10).items():
                        if zone not in ['Not Found', 'N/A']:
                            print(f"  {zone}: {count} addresses")

    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found")
    except pd.errors.EmptyDataError:
        print(f"Error: Input file '{input_file}' is empty")
    except Exception as e:
        print(f"Error processing file: {e}")


def main():
    """Command line interface."""
    parser = argparse.ArgumentParser(
        description='Geocode organization addresses in CSV file with enhanced weather zone support',
        epilog='''
Enhanced Zone Features:
- Captures BOTH forecast zones (DCZ001) AND CWA offices (LWX)
- Forecast zones: Perfect for matching with weather.gov alert data
- CWA offices: Perfect for mapping to CWA boundary shapefiles in Tableau
- Backward compatible with original functionality

Incremental Geocoding:
- Use --previous to specify a previously geocoded CSV file
- Only new organizations or those without geocoding data will be processed
- Saves time and API calls by reusing existing geocoding data

Google Maps API Setup (optional but recommended for better accuracy):
1. Install: pip install googlemaps python-dotenv
2. Get API key from: https://developers.google.com/maps/documentation/geocoding/get-api-key
3. Create .env file in same directory with: GOOGLE_MAPS_API_KEY=your_api_key_here
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('input_file', help='Path to input CSV file')
    parser.add_argument('-o', '--output', help='Path to output CSV file (optional)')
    parser.add_argument('-p', '--previous', help='Path to previous output CSV file to check for already geocoded organizations')
    parser.add_argument('-d', '--delay', type=float, default=1.0,
                        help='Delay between API calls in seconds (default: 1.0)')
    parser.add_argument('-e', '--encoding',
                        help='File encoding (e.g., utf-8, cp1252, iso-8859-1)')
    parser.add_argument('--simple-zones', action='store_true',
                        help='Use simple zone mode (CWA_Region only, no CWA_Office)')

    args = parser.parse_args()

    if args.delay < 0.5:
        print("Warning: Using delays less than 0.5 seconds may overwhelm the free service")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != 'y':
            sys.exit(0)

    # Enhanced zones by default, simple zones if requested
    enhanced_zones = not args.simple_zones

    geocode_csv(args.input_file, args.output, args.delay, args.encoding, enhanced_zones, args.previous)


if __name__ == "__main__":
    main()
