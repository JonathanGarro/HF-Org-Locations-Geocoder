# Organization Address Geocoder

A Python utility for geocoding organization addresses from CSV files. This tool takes a CSV file containing organization addresses and adds latitude and longitude coordinates using multiple geocoding services.

## Features

- Processes CSV files with organization address data
- Uses multiple geocoding services with fallback strategies:
  - Nominatim (OpenStreetMap)
  - ArcGIS
  - Google Maps API (optional)
- Handles address simplification for better geocoding results
- Retrieves County Warning Area (CWA) regions from the National Weather Service API
- Provides detailed statistics on geocoding success rates and CWA regions
- Rate-limits API calls to respect service usage policies
- Skips geocoding for addresses that already have coordinates (incremental processing)

## Installation

1. Clone this repository:
   ```
   git clone <repository-url>
   cd Geocoding
   ```

2. Install required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Configuration

### Required CSV Format

The input CSV file must contain the following columns:
- Organization Name
- Primary Address Street
- Primary Address City
- Primary Address State/Province
- Primary Address Zip/Postal Code

### Google Maps API (Optional)

For better geocoding results, you can use the Google Maps API:

1. Get an API key from [Google Maps Platform](https://developers.google.com/maps/documentation/geocoding/get-api-key)
2. Create a `.env` file in the project directory with:
   ```
   GOOGLE_MAPS_API_KEY=your_api_key_here
   ```

## Usage

Run the script with a CSV file containing organization addresses:

```
python org_geocoder.py input_file.csv
```

### Command Line Options

```
python org_geocoder.py [-h] [-o OUTPUT] [-d DELAY] [-e ENCODING] input_file
```

- `input_file`: Path to input CSV file
- `-o, --output`: Path to output CSV file (optional, defaults to input_file_geocoded.csv)
- `-d, --delay`: Delay between API calls in seconds (default: 1.0)
- `-e, --encoding`: File encoding (e.g., utf-8, cp1252, iso-8859-1)

## How It Works

1. The script reads the input CSV file
2. It creates full address strings from the separate address components
3. For each address:
   - If the address already has geocoding data (Latitude, Longitude, etc.), it skips the geocoding process
   - Otherwise, it attempts geocoding using:
     - Free service (Nominatim or ArcGIS) with full address
     - Free service with simplified address (removing suite numbers, etc.)
     - Google Maps API with full address (if configured)
     - Google Maps API with simplified address (if configured)
4. For addresses with coordinates, it retrieves the County Warning Area (CWA) region from the National Weather Service API
5. Results are saved to a new CSV file with added columns:
   - Latitude
   - Longitude
   - Geocoding_Status (Success/Failed)
   - Geocoding_Method (which service and strategy worked)
   - CWA_Region (the National Weather Service forecast office responsible for that location)

## Example

Input CSV:
```
Organization Name,Primary Address Street,Primary Address City,Primary Address State/Province,Primary Address Zip/Postal Code
Some Org Name,1 Main St,Washington,DC,20431
```

Output CSV:
```
Organization Name,Primary Address Street,Primary Address City,Primary Address State/Province,Primary Address Zip/Postal Code,Latitude,Longitude,Geocoding_Status,Geocoding_Method,CWA_Region
Some Org Name,1 Main St,Washington,DC,20431,38.8989426,-77.0442051,Success,Free Service (Full),LWX
```

The CWA_Region "LWX" represents the National Weather Service forecast office in Sterling, VA, which covers the Washington DC area.

## Troubleshooting

- If geocoding fails, try increasing the delay between requests (`-d` option)
- If you encounter SSL errors, the script will attempt to work around them
- For better results, consider getting a Google Maps API key
- If CWA region lookup fails, it may be due to temporary National Weather Service API issues or the coordinates being outside the US (CWA regions only exist for US locations)

## License

This project is licensed under the MIT License - see the LICENSE file for details.
