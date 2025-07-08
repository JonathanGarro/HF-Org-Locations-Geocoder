 #!/usr/bin/env python3

import pandas as pd
import requests
import json
from datetime import datetime
import time
import argparse
import sys


def fetch_all_active_alerts():
    """
    Fetch all active weather alerts from weather.gov API

    Returns:
        dict: JSON response with all active alerts
    """
    url = "https://api.weather.gov/alerts/active"

    print("Fetching all active weather alerts...")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        alert_count = len(data.get('features', []))
        print(f"✓ Successfully fetched {alert_count} active alerts")
        return data
    except requests.exceptions.RequestException as e:
        print(f"✗ Error fetching weather alerts: {e}")
        return None


def fetch_fema_disasters_by_states(states):
    """
    Fetch active FEMA disaster declarations for multiple states

    Args:
        states (set): Set of state codes to query

    Returns:
        dict: State code -> list of disaster info
    """
    print(f"Fetching FEMA disaster declarations for {len(states)} states...")

    state_disasters = {}

    for state in states:
        url = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
        params = {
            '$filter': f'state eq \'{state.upper()}\'',
            '$orderby': 'declarationDate desc',
            '$top': 50  # Get more recent declarations to filter from
        }

        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            disasters = []
            current_date = datetime.now()

            for declaration in data.get('DisasterDeclarationsSummaries', []):
                try:
                    decl_date = datetime.strptime(declaration['declarationDate'][:10], '%Y-%m-%d')
                    days_since = (current_date - decl_date).days

                    # More restrictive filtering for truly current disasters
                    is_very_recent = days_since <= 30  # Within 30 days
                    is_recent_and_active = days_since <= 90 and declaration.get('disasterCloseoutDate') is None

                    # Filter by declaration type - focus on emergency declarations
                    decl_type = declaration.get('declarationType', '').upper()
                    is_emergency_type = decl_type in ['DR', 'EM', 'FM']  # Major Disaster, Emergency, Fire Management

                    # Filter by incident type - exclude very old/administrative types
                    incident_type = declaration.get('incidentType', '').upper()
                    excluded_types = ['TERRORIST', 'OTHER', 'TOXIC SUBSTANCES', 'DAM/LEVEE BREAK']
                    is_relevant_incident = incident_type not in excluded_types

                    # Include if: (very recent) OR (recent + active + emergency type + relevant incident)
                    should_include = (
                            is_very_recent or
                            (is_recent_and_active and is_emergency_type and is_relevant_incident)
                    )

                    if should_include:
                        # Determine status more precisely
                        if declaration.get('disasterCloseoutDate') is None:
                            if days_since <= 30:
                                status = 'Active - Recent'
                            elif days_since <= 90:
                                status = 'Active - Ongoing'
                            else:
                                status = 'Active - Administrative'
                        else:
                            status = f'Closed ({days_since} days ago)'

                        disasters.append({
                            'disaster_number': declaration.get('disasterNumber'),
                            'declaration_type': declaration.get('declarationType'),
                            'declaration_title': declaration.get('declarationTitle'),
                            'incident_type': declaration.get('incidentType'),
                            'declaration_date': declaration.get('declarationDate'),
                            'state': declaration.get('state'),
                            'counties': declaration.get('designatedArea', ''),
                            'closeout_date': declaration.get('disasterCloseoutDate'),
                            'days_since_declaration': days_since,
                            'status': status,
                            'web_url': f"https://www.fema.gov/disaster/{declaration.get('disasterNumber')}",
                            'is_truly_active': declaration.get('disasterCloseoutDate') is None and days_since <= 90
                        })
                except (ValueError, TypeError):
                    continue  # Skip declarations with invalid dates

            state_disasters[state] = disasters
            active_count = len([d for d in disasters if d['is_truly_active']])
            print(f"  {state}: {len(disasters)} relevant disasters ({active_count} truly active)")
            time.sleep(0.5)  # Rate limiting

        except Exception as e:
            print(f"  ✗ Error fetching FEMA data for {state}: {e}")
            state_disasters[state] = []

    total_disasters = sum(len(disasters) for disasters in state_disasters.values())
    total_active = sum(len([d for d in disasters if d['is_truly_active']]) for disasters in state_disasters.values())
    print(f"✓ Found {total_disasters} relevant disasters ({total_active} truly active)")

    return state_disasters


def process_alerts_by_zones(alerts_data, target_zones):
    """
    Process alerts and group by zone codes

    Args:
        alerts_data (dict): API response with alerts
        target_zones (set): Set of CWA zones to match against

    Returns:
        dict: Zone code -> list of alert info
    """
    if not alerts_data or 'features' not in alerts_data:
        return {}

    zone_alerts = {}
    matched_alerts = 0

    print(f"Processing alerts for {len(target_zones)} unique CWA zones...")

    for feature in alerts_data['features']:
        props = feature.get('properties', {})

        # Get UGC codes (zone codes) from the alert
        geocode = props.get('geocode', {})
        if isinstance(geocode, dict):
            ugc_codes = geocode.get('UGC', [])
        else:
            ugc_codes = []

        # Also check area description for zone patterns
        area_desc = props.get('areaDesc', '')

        # Find matches with our target zones
        alert_zones = set()

        # Method 1: Direct UGC code matching
        for ugc in ugc_codes:
            if ugc in target_zones:
                alert_zones.add(ugc)

        # Method 2: Pattern matching in area description
        if not alert_zones:
            for zone in target_zones:
                if len(zone) == 3:  # CWA office codes like LWX, OKX
                    # Check if this CWA office issued the alert
                    sender_name = props.get('senderName', '').upper()
                    if zone.upper() in sender_name:
                        alert_zones.add(zone)
                elif zone.upper() in area_desc.upper():
                    alert_zones.add(zone)

        # Store alert info for each matching zone
        for zone in alert_zones:
            if zone not in zone_alerts:
                zone_alerts[zone] = []

            alert_info = {
                'alert_id': props.get('id', ''),
                'event': props.get('event', ''),
                'severity': props.get('severity', 'Unknown'),
                'certainty': props.get('certainty', ''),
                'urgency': props.get('urgency', ''),
                'headline': props.get('headline', ''),
                'description': props.get('description', ''),
                'instruction': props.get('instruction', ''),
                'area_desc': area_desc,
                'effective': props.get('effective', ''),
                'expires': props.get('expires', ''),
                'onset': props.get('onset', ''),
                'ends': props.get('ends', ''),
                'status': props.get('status', ''),
                'message_type': props.get('messageType', ''),
                'category': props.get('category', ''),
                'response': props.get('response', ''),
                'sender_name': props.get('senderName', ''),
                'web_url': props.get('web', ''),
                'ugc_codes': ', '.join(ugc_codes)
            }

            zone_alerts[zone].append(alert_info)
            matched_alerts += 1

    print(f"✓ Found {matched_alerts} relevant alerts across {len(zone_alerts)} zones")
    return zone_alerts


def enhance_organizations_with_alerts(input_file, output_file=None):
    """
    Main function to enhance organization data with weather alerts and FEMA disasters

    Args:
        input_file (str): Path to geocoded organizations CSV
        output_file (str): Path for output CSV (optional)
    """

    # Load organization data
    print(f"Reading organization data from: {input_file}")
    try:
        df = pd.read_csv(input_file)
        print(f"✓ Loaded {len(df)} organizations")
    except Exception as e:
        print(f"✗ Error loading file: {e}")
        return

    # Check required columns
    required_cols = ['CWA_Region', 'Primary Address State/Province']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        print(f"✗ Missing required columns: {missing_cols}")
        print(f"Available columns: {list(df.columns)}")
        return

    # Get unique CWA zones
    cwa_zones = df['CWA_Region'].dropna()
    cwa_zones = cwa_zones[cwa_zones.notna()]
    cwa_zones = cwa_zones[~cwa_zones.isin(['Not Found', 'N/A', ''])]
    unique_zones = set(cwa_zones.unique())

    print(f"✓ Found {len(unique_zones)} unique CWA zones in organization data")
    print(f"Sample zones: {list(unique_zones)[:10]}")

    # Get unique states for FEMA data
    states = df['Primary Address State/Province'].dropna()
    states = states[~states.isin(['', 'N/A'])]
    unique_states = set(states.unique())

    print(f"✓ Found {len(unique_states)} unique states for FEMA data")

    # Fetch weather alerts
    alerts_data = fetch_all_active_alerts()
    if not alerts_data:
        print("✗ Could not fetch weather alerts")
        return

    # Fetch FEMA disaster data
    fema_data = fetch_fema_disasters_by_states(unique_states)

    # Process alerts by zones
    zone_alerts = process_alerts_by_zones(alerts_data, unique_zones)

    # Initialize new columns for alert and FEMA information
    alert_columns = [
        # Weather alerts
        'has_active_alerts', 'alert_count', 'max_severity', 'alert_events',
        'alert_headlines', 'alert_descriptions', 'alert_instructions',
        'earliest_effective', 'latest_expires', 'alert_urgency_max',
        'alert_certainty_max', 'alert_web_urls', 'alert_ids',

        # FEMA disasters
        'fema_disaster_count', 'fema_active_disasters', 'fema_recent_disasters',
        'fema_disaster_types', 'fema_disaster_titles', 'fema_disaster_counties',
        'fema_disaster_urls', 'fema_latest_declaration_date', 'fema_disaster_numbers',
        'fema_disaster_status',  # New field for disaster status details

        # Combined risk assessment
        'combined_risk_level', 'risk_factors', 'last_alert_check'
    ]

    for col in alert_columns:
        df[col] = None

    # Severity and urgency ranking for max calculations
    severity_rank = {'Unknown': 0, 'Minor': 1, 'Moderate': 2, 'Severe': 3, 'Extreme': 4}
    urgency_rank = {'Unknown': 0, 'Future': 1, 'Expected': 2, 'Immediate': 3}
    certainty_rank = {'Unknown': 0, 'Unlikely': 1, 'Possible': 2, 'Likely': 3, 'Observed': 4}

    # Enhance each organization with alert and FEMA data
    print("Enhancing organizations with weather alerts and FEMA disaster information...")

    organizations_with_alerts = 0
    organizations_with_fema = 0

    for idx, row in df.iterrows():
        cwa_region = row['CWA_Region']
        state = row['Primary Address State/Province']

        # Set timestamp
        df.at[idx, 'last_alert_check'] = datetime.now().isoformat()

        # === WEATHER ALERTS PROCESSING ===
        if pd.isna(cwa_region) or cwa_region in ['Not Found', 'N/A', '']:
            df.at[idx, 'has_active_alerts'] = False
            df.at[idx, 'alert_count'] = 0
            df.at[idx, 'max_severity'] = 'None'
        else:
            alerts = zone_alerts.get(cwa_region, [])

            if alerts:
                organizations_with_alerts += 1
                df.at[idx, 'has_active_alerts'] = True
                df.at[idx, 'alert_count'] = len(alerts)

                # Collect alert details
                events = [alert['event'] for alert in alerts if alert['event']]
                headlines = [alert['headline'] for alert in alerts if alert['headline']][:3]
                descriptions = [alert['description'][:200] + '...' if len(alert['description']) > 200
                                else alert['description'] for alert in alerts if alert['description']][:2]
                instructions = [alert['instruction'][:200] + '...' if len(alert['instruction']) > 200
                                else alert['instruction'] for alert in alerts if alert['instruction']][:2]
                web_urls = [alert['web_url'] for alert in alerts if alert['web_url']][:3]
                alert_ids = [alert['alert_id'] for alert in alerts if alert['alert_id']][:5]
                effective_times = [alert['effective'] for alert in alerts if alert['effective']]
                expires_times = [alert['expires'] for alert in alerts if alert['expires']]

                # Find maximum severity, urgency, certainty
                max_severity = 'Unknown'
                max_severity_rank = 0
                max_urgency = 'Unknown'
                max_urgency_rank = 0
                max_certainty = 'Unknown'
                max_certainty_rank = 0

                for alert in alerts:
                    severity = alert['severity']
                    if severity_rank.get(severity, 0) > max_severity_rank:
                        max_severity_rank = severity_rank[severity]
                        max_severity = severity

                    urgency = alert['urgency']
                    if urgency_rank.get(urgency, 0) > max_urgency_rank:
                        max_urgency_rank = urgency_rank[urgency]
                        max_urgency = urgency

                    certainty = alert['certainty']
                    if certainty_rank.get(certainty, 0) > max_certainty_rank:
                        max_certainty_rank = certainty_rank[certainty]
                        max_certainty = certainty

                # Store aggregated alert information
                df.at[idx, 'max_severity'] = max_severity
                df.at[idx, 'alert_events'] = ' | '.join(list(dict.fromkeys(events)))
                df.at[idx, 'alert_headlines'] = ' | '.join(headlines)
                df.at[idx, 'alert_descriptions'] = ' | '.join(descriptions)
                df.at[idx, 'alert_instructions'] = ' | '.join(instructions)
                df.at[idx, 'alert_urgency_max'] = max_urgency
                df.at[idx, 'alert_certainty_max'] = max_certainty
                df.at[idx, 'alert_web_urls'] = ' | '.join(web_urls)
                df.at[idx, 'alert_ids'] = ' | '.join(alert_ids)

                if effective_times:
                    df.at[idx, 'earliest_effective'] = min(effective_times)
                if expires_times:
                    df.at[idx, 'latest_expires'] = max(expires_times)

            else:
                df.at[idx, 'has_active_alerts'] = False
                df.at[idx, 'alert_count'] = 0
                df.at[idx, 'max_severity'] = 'None'

        # === FEMA DISASTERS PROCESSING ===
        if pd.isna(state) or state in ['', 'N/A']:
            df.at[idx, 'fema_disaster_count'] = 0
            df.at[idx, 'fema_active_disasters'] = 0
            df.at[idx, 'fema_recent_disasters'] = 0
            df.at[idx, 'fema_disaster_status'] = 'No State Info'
        else:
            disasters = fema_data.get(state, [])

            if disasters:
                organizations_with_fema += 1

                # More precise categorization
                truly_active_disasters = [d for d in disasters if d.get('is_truly_active', False)]
                recent_closed_disasters = [d for d in disasters if not d.get('is_truly_active', False)]

                df.at[idx, 'fema_disaster_count'] = len(disasters)
                df.at[idx, 'fema_active_disasters'] = len(truly_active_disasters)
                df.at[idx, 'fema_recent_disasters'] = len(recent_closed_disasters)

                # Prioritize truly active disasters for details
                priority_disasters = truly_active_disasters if truly_active_disasters else disasters

                disaster_types = list(set([d['incident_type'] for d in priority_disasters if d['incident_type']]))
                disaster_titles = [d['declaration_title'] for d in priority_disasters if d['declaration_title']][:3]
                disaster_counties = list(set([d['counties'] for d in priority_disasters if d['counties']]))[:3]
                disaster_urls = [d['web_url'] for d in priority_disasters if d['web_url']][:3]
                disaster_numbers = [str(d['disaster_number']) for d in priority_disasters if d['disaster_number']][:5]
                disaster_statuses = list(set([d['status'] for d in priority_disasters if d['status']]))

                declaration_dates = [d['declaration_date'] for d in priority_disasters if d['declaration_date']]
                if declaration_dates:
                    df.at[idx, 'fema_latest_declaration_date'] = max(declaration_dates)

                df.at[idx, 'fema_disaster_types'] = ' | '.join(disaster_types)
                df.at[idx, 'fema_disaster_titles'] = ' | '.join(disaster_titles)
                df.at[idx, 'fema_disaster_counties'] = ' | '.join(disaster_counties)
                df.at[idx, 'fema_disaster_urls'] = ' | '.join(disaster_urls)
                df.at[idx, 'fema_disaster_numbers'] = ' | '.join(disaster_numbers)
                df.at[idx, 'fema_disaster_status'] = ' | '.join(disaster_statuses)

            else:
                df.at[idx, 'fema_disaster_count'] = 0
                df.at[idx, 'fema_active_disasters'] = 0
                df.at[idx, 'fema_recent_disasters'] = 0
                df.at[idx, 'fema_disaster_status'] = 'None'

        # === COMBINED RISK ASSESSMENT ===
        risk_factors = []
        risk_level = 'Low'

        has_alerts = df.at[idx, 'has_active_alerts']
        if has_alerts:
            max_sev = df.at[idx, 'max_severity']
            if max_sev == 'Extreme':
                risk_factors.append('Extreme Weather Alert')
                risk_level = 'Critical'
            elif max_sev == 'Severe':
                risk_factors.append('Severe Weather Alert')
                risk_level = 'High' if risk_level != 'Critical' else risk_level
            elif max_sev in ['Moderate', 'Minor']:
                risk_factors.append('Weather Advisory')
                risk_level = 'Moderate' if risk_level == 'Low' else risk_level

        # Use truly active FEMA disasters for risk assessment
        truly_active_fema = df.at[idx, 'fema_active_disasters'] or 0
        if truly_active_fema > 0:
            risk_factors.append(f'Active FEMA Disaster ({truly_active_fema})')
            risk_level = 'High' if risk_level not in ['Critical'] else risk_level

        recent_fema = df.at[idx, 'fema_recent_disasters'] or 0
        if recent_fema > 0 and truly_active_fema == 0:  # Only count recent if no truly active
            risk_factors.append(f'Recent FEMA Disaster ({recent_fema})')
            risk_level = 'Moderate' if risk_level == 'Low' else risk_level

        df.at[idx, 'combined_risk_level'] = risk_level
        df.at[idx, 'risk_factors'] = ' | '.join(risk_factors) if risk_factors else 'None'

    # Save enhanced data
    if output_file is None:
        base_name = input_file.rsplit('.', 1)[0]
        output_file = f"{base_name}_with_weather_alerts_and_fema.csv"

    print(f"\n✓ Saving enhanced data to: {output_file}")
    df.to_csv(output_file, index=False, encoding='utf-8')

    # Summary
    print(f"\n=== COMPREHENSIVE ALERTS & DISASTERS SUMMARY ===")
    print(f"Total organizations processed: {len(df)}")
    print(f"Organizations with weather alerts: {organizations_with_alerts}")
    print(f"Organizations in states with FEMA disasters: {organizations_with_fema}")

    # Count truly active FEMA disasters
    truly_active_count = (df['fema_active_disasters'] > 0).sum()
    print(f"Organizations with truly active FEMA disasters: {truly_active_count}")

    if organizations_with_alerts > 0:
        from collections import Counter

        all_events = []
        for events_str in df[df['has_active_alerts'] == True]['alert_events']:
            if pd.notna(events_str):
                all_events.extend([e.strip() for e in str(events_str).split('|')])

        if all_events:
            event_counts = Counter(all_events)
            print(f"\n=== TOP WEATHER ALERT TYPES ===")
            for event, count in event_counts.most_common(10):
                print(f"  {event}: {count}")

    if organizations_with_fema > 0:
        from collections import Counter

        all_fema_types = []
        for types_str in df[df['fema_disaster_count'] > 0]['fema_disaster_types']:
            if pd.notna(types_str):
                all_fema_types.extend([t.strip() for t in str(types_str).split('|')])

        if all_fema_types:
            fema_counts = Counter(all_fema_types)
            print(f"\n=== TOP FEMA DISASTER TYPES ===")
            for disaster_type, count in fema_counts.most_common(10):
                print(f"  {disaster_type}: {count}")

    if 'combined_risk_level' in df.columns:
        risk_counts = df['combined_risk_level'].value_counts()
        print(f"\n=== COMBINED RISK LEVEL BREAKDOWN ===")
        for risk_level, count in risk_counts.items():
            print(f"  {risk_level}: {count}")


def main():
    """Command line interface"""
    parser = argparse.ArgumentParser(
        description='Enhance geocoded organization data with weather alerts and FEMA disasters'
    )

    parser.add_argument('input_file', help='Path to geocoded organizations CSV file')
    parser.add_argument('-o', '--output', help='Path to output CSV file (optional)')

    args = parser.parse_args()

    enhance_organizations_with_alerts(args.input_file, args.output)


if __name__ == "__main__":
    main()