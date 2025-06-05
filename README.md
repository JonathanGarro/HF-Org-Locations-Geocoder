# Organization Address Geocoder

A Python utility for geocoding organization addresses from CSV files. This tool takes a CSV file containing organization addresses and adds latitude and longitude coordinates using multiple geocoding services.

## Features

- Processes CSV files with organization address data
- Uses multiple geocoding services with fallback strategies:
  - Nominatim (OpenStreetMap)
  - ArcGIS
  - Google Maps API (optional)
- Handles address simplification for better geocoding results
- Provides detailed statistics on geocoding success rates
- Rate-limits API calls to respect service usage policies

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

3. (Optional) For improved geocoding accuracy, install additional packages:
   ```
   pip install googlemaps python-dotenv
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
3. For each address, it attempts geocoding using:
   - Free service (Nominatim or ArcGIS) with full address
   - Free service with simplified address (removing suite numbers, etc.)
   - Google Maps API with full address (if configured)
   - Google Maps API with simplified address (if configured)
4. Results are saved to a new CSV file with added columns:
   - Latitude
   - Longitude
   - Geocoding_Status (Success/Failed)
   - Geocoding_Method (which service and strategy worked)

## Example

Input CSV:
```
Organization Name,Primary Address Street,Primary Address City,Primary Address State/Province,Primary Address Zip/Postal Code
Some Org Name,1 Main St,Washington,DC,20431
```

Output CSV:
```
Organization Name,Primary Address Street,Primary Address City,Primary Address State/Province,Primary Address Zip/Postal Code,Latitude,Longitude,Geocoding_Status,Geocoding_Method
Some Org Name,1 Main St,Washington,DC,20431,38.8989426,-77.0442051,Success,Free Service (Full)
```

## Troubleshooting

- If geocoding fails, try increasing the delay between requests (`-d` option)
- If you encounter SSL errors, the script will attempt to work around them
- For better results, consider getting a Google Maps API key

## License

This project is licensed under the MIT License - see the LICENSE file for details.